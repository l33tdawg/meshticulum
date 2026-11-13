import copy,hashlib,json,struct
from dataclasses import replace
from types import SimpleNamespace
import pytest
import radio_file
from meshticulum.bench.continuation import *
from meshticulum.bench.admission import RadioAdmission
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.serial_worker import SerialWorker,journal_binding
from meshticulum.bench.serial_protocol import Decoder
from meshticulum.bench.burst_protocol import STATUS
from tests.test_continuation_record import fixture


def approved():
    v,p,i,f=fixture();terms=copy.deepcopy(v);terms.pop('approval_sha256')
    raw=canonical(dict(schema='meshticulum.same-boot-continuation-approval.v1',approved=True,
        terms_sha256=hashlib.sha256(canonical(terms)).hexdigest(),issued_epoch_us=1,expires_epoch_us=181000000))
    pin=hashlib.sha256(raw).hexdigest();v['approval_sha256']=pin;p['boot_continuation']=v
    record=ContinuationRecord(canonical(v));approval=ContinuationApproval(raw,pin,record)
    base=RadioAdmission('MY',((921375000,500000,5,5,0,32,18),),60000,60000,1024,300000000)
    return p,ContinuationAdmission(base,approval),i,f,raw,pin

class Channel:
    def __init__(self,identity,status,fault=None):
        self.identity=identity;self.status=status;self.time=100.;self.inbound=b'';self.writes=[];self.fault=fault;self.after=lambda frame:None
    def monotonic(self):return self.time
    def write(self,raw,end):
        assert self.time<end
        frame=Decoder().feed(raw)[0];self.writes.append(frame)
        kind=frame.type
        if kind is Message.DISARM:reply=Frame(Message.DISARM_REPLY,self.identity.boot,frame.request,b'\0')
        elif kind is Message.STATUS:
            body=bytearray(self.status.body)
            if self.fault=='counter':body[30:34]=struct.pack('!I',1013)
            if self.fault=='profile':body[0]^=1
            reply=Frame(Message.STATUS_REPLY,self.identity.boot,frame.request,bytes(body))
        elif kind is Message.BURST_STATUS:
            body=STATUS.pack(0,bytes(32),1 if self.fault=='dirty' else 0,0,0,0,False,False,False,1000,0,1012,36218560)
            reply=Frame(Message.BURST_STATUS_REPLY,self.identity.boot,frame.request,body)
        elif kind is Message.ARM:
            if self.fault=='partial':raise OSError('partial ARM write')
            reply=Frame(Message.ARM_REPLY,self.identity.boot,frame.request,frame.body)
        else:raise AssertionError(kind)
        self.inbound+=reply.encode();self.after(frame)
    def read(self,timeout):
        self.time+=.001;data=self.inbound;self.inbound=b'';return data

@pytest.mark.parametrize('fault',[None,'counter','profile','dirty','owner','expiry','partial','record_mutation'])
def test_actual_arm_queries_and_cleanup(tmp_path,fault):
    p,a,i,f,_,_=approved();c=Channel(i,f,fault);w=SerialWorker(c);w.identity=i;profile=RadioProfile(**p['profile']);tmp_path.chmod(0o700)
    j=AttemptJournal(tmp_path/'journal',binding=journal_binding(b'x'*32,i,profile),max_attempts=358,max_airtime_us=18000000,create=True,request_clock=lambda:100000000)
    live=[True];record=a.approval.record
    def after(frame):
        if frame.type is Message.STATUS:
            if fault=='owner':live[0]=False
            if fault=='expiry':c.time=200.
            if fault=='record_mutation':object.__setattr__(record,'raw',record.raw+b' ')
    c.after=after
    args=dict(journal=j,profile=profile,context_digest=b'x'*32,authorize=lambda:True,authority_deadline=180.,continuation=record,continuation_owner=ContinuationOwner(lambda:live[0],lambda:live[0]))
    try:
        if fault is None:
            w.arm(**args);assert [f.type for f in c.writes]==[Message.DISARM,Message.STATUS,Message.BURST_STATUS,Message.ARM]
        else:
            with pytest.raises((ValueError,OSError,HardwareError)):w.arm(**args)
            assert w.fenced
            assert not any(f.type is Message.ARM for f in c.writes) or fault=='partial'
        assert j.snapshot()['attempts']==0 and j.snapshot()['reserved_estimated_airtime_us']==0
        if fault!='expiry':w.disarm();assert c.writes[-1].type is Message.DISARM
    finally:j.close()

@pytest.mark.parametrize('mutation',['pin','terms','approved','window','record'])
def test_approval_refusal(mutation):
    p,a,_,_,raw,pin=approved();v=json.loads(raw);record=a.approval.record
    if mutation=='pin':pin='00'*32
    elif mutation=='record':
        vrecord=record.value();vrecord['history_sha256']='55'*32;record=ContinuationRecord(canonical(vrecord))
    else:
        if mutation=='terms':v['terms_sha256']='00'*32
        if mutation=='approved':v['approved']=False
        if mutation=='window':v['expires_epoch_us']=0
        raw=canonical(v);pin=hashlib.sha256(raw).hexdigest();vrecord=record.value();vrecord['approval_sha256']=pin;record=ContinuationRecord(canonical(vrecord))
    with pytest.raises(ValueError):ContinuationApproval(raw,pin,record)

def test_application_pinned_input_before_admission(tmp_path):
    p,a,_,_,raw,pin=approved();tmp_path.chmod(0o700);f=tmp_path/'approval';f.write_bytes(raw);f.chmod(0o600)
    args=SimpleNamespace(boot_continuation=True,boot_continuation_approval=f,boot_continuation_approval_sha256=pin)
    value=radio_file.continuation_admission(args,p,a.base);value.check('MY',True,RadioProfile(**p['profile']))
    args.boot_continuation=False
    with pytest.raises(ValueError):radio_file.continuation_admission(args,p,a.base)

@pytest.mark.parametrize('boundary',['request','clock'])
@pytest.mark.parametrize('mutation',['channel','identity','owner'])
def test_post_query_callback_cannot_rebind_before_arm(tmp_path,boundary,mutation):
    p,a,i,f,_,_=approved();c=Channel(i,f);w=SerialWorker(c);w.identity=i;profile=RadioProfile(**p['profile']);tmp_path.chmod(0o700)
    j=AttemptJournal(tmp_path/'journal',binding=journal_binding(b'x'*32,i,profile),max_attempts=358,max_airtime_us=18000000,create=True,request_clock=lambda:100000000)
    owner=[True];ready=[False];armed=[False]
    def mutate():
        if mutation=='channel':w.channel=Channel(i,f)
        elif mutation=='identity':w.identity=replace(i,version='mtb1-0000000000')
        else:owner[0]=False
    original_request=j.command_request;original_clock=c.monotonic
    def request(**kwargs):
        value=original_request(**kwargs)
        if ready[0] and not armed[0]:
            armed[0]=True
            if boundary=='request':mutate()
        return value
    def clock():
        value=original_clock()
        if boundary=='clock' and armed[0]:armed[0]=False;mutate()
        return value
    c.monotonic=clock;j.command_request=request
    c.after=lambda frame:ready.__setitem__(0,frame.type is Message.BURST_STATUS or ready[0])
    try:
        with pytest.raises((ValueError,HardwareError)):
            w.arm(journal=j,profile=profile,context_digest=b'x'*32,authorize=lambda:True,authority_deadline=180.,continuation=a.approval.record,continuation_owner=ContinuationOwner(lambda:owner[0],lambda:owner[0]))
        assert not any(row.type is Message.ARM for row in c.writes)
    finally:j.close()


def test_actual_authority_pair_binds_record_and_preserves_legacy_refusal(tmp_path):
    from meshticulum.bench.authority import RunAuthority
    from tests.test_meshticulum_bench_runtime import provision,Clock,BOOT
    from meshticulum.bench.burst_protocol import policy_for_train32
    p,template,_,_,_,_=approved();clock=Clock();authorities=[]
    try:
        for unit in ('unit-a','unit-b'):
            path=tmp_path/unit;plan=provision(path,unit,b'payload',clock,RadioProfile(**p['profile']))
            plan['operation_limits']=p['operation_limits'];plan['data_burst']=policy_for_train32()
            value=template.approval.record.value();value['context_sha256']=context_digest(plan)
            terms=copy.deepcopy(value);terms.pop('approval_sha256')
            raw=canonical(dict(schema='meshticulum.same-boot-continuation-approval.v1',approved=True,terms_sha256=hashlib.sha256(canonical(terms)).hexdigest(),issued_epoch_us=plan['issued_epoch_us'],expires_epoch_us=plan['expires_epoch_us']))
            pin=hashlib.sha256(raw).hexdigest();value['approval_sha256']=pin;record=ContinuationRecord(canonical(value));admission=ContinuationAdmission(template.base,ContinuationApproval(raw,pin,record))
            plan['boot_continuation']=value;(path/'grant.json').write_bytes(canonical(plan))
            with pytest.raises(ValueError):RunAuthority(path,expected_unit=unit,host_boot_id=BOOT,clock=clock,owner_probe=lambda:True,radio_admission=template.base)
            authority=RunAuthority(path,expected_unit=unit,host_boot_id=BOOT,clock=clock,owner_probe=lambda:True,radio_admission=admission);authorities.append(authority)
            assert authority.route_record['boot_continuation']==value
        assert authorities[0].grant.digest==authorities[1].grant.digest
        authorities[0].plan['boot_continuation']['approval_sha256']='00'*32
        assert not authorities[0].application_live()
    finally:
        for authority in authorities:authority.close()

def test_final_owner_cost_narrows_lease_without_rebasing(tmp_path):
    p,a,i,f,_,_=approved();c=Channel(i,f);w=SerialWorker(c);w.identity=i;profile=RadioProfile(**p['profile']);tmp_path.chmod(0o700)
    j=AttemptJournal(tmp_path/'journal',binding=journal_binding(b'x'*32,i,profile),max_attempts=358,max_airtime_us=18000000,create=True,request_clock=lambda:100000000)
    def owner():c.time+=.004;return True
    try:
        w.arm(journal=j,profile=profile,context_digest=b'x'*32,authorize=lambda:True,authority_deadline=104.,continuation=a.approval.record,continuation_owner=ContinuationOwner(owner,lambda:True))
        assert 1000 <= w.arm_lease_ms < 1900
        assert w.arm_lease_end_upper_bound < 104. and w.deadline == 104.
    finally:w.disarm();j.close()


def test_actual_train32_continuation_opens_and_charges_first_burst(tmp_path,monkeypatch):
    from tests import test_burst_worker as model
    from meshticulum.bench.burst_worker import BurstSerialWorker
    from meshticulum.bench.burst_protocol import policy_for_train32
    p,a,i,f,_,_=approved();profile=RadioProfile(**p['profile']);monkeypatch.setattr(model,'VERSION',i.version)
    value=a.approval.record.value();value['baseline']['mcu_boot']=model.BOOT.hex();record=ContinuationRecord(canonical(value))
    class BurstChannel(model.Channel):
        def write(self,raw,deadline):
            frame=Decoder().feed(raw)[0]
            if frame.type is Message.STATUS:
                self.writes.append(frame);self.offer(Frame(Message.STATUS_REPLY,model.BOOT,frame.request,f.body));return
            return super().write(raw,deadline)
    c=BurstChannel(train_depth=8,train_frames=32,profile=profile);c.attempts=1012;c.airtime=36218560
    w=BurstSerialWorker(c,policy=policy_for_train32());identity=w.discover(expected_board_id=1,expected_version=i.version)
    tmp_path.chmod(0o700);j=AttemptJournal(tmp_path/'journal',binding=journal_binding(b'x'*32,identity,profile),max_attempts=358,max_airtime_us=18000000,create=True,request_clock=lambda:100000000)
    try:
        w.arm(journal=j,profile=profile,context_digest=b'x'*32,authorize=lambda:True,authority_deadline=180.,continuation=record,continuation_owner=ContinuationOwner(lambda:True,lambda:True))
        arm=next(row for row in c.writes if row.type is Message.ARM)
        assert w._arm_request==c.arm_request==arm.request and w._open.arm_request==arm.request
        queued=w.queue_data(b'first DATA',logical_id=b'd'*32)
        assert j.snapshot()['attempts']==1 and c.attempts==1013
        from meshticulum.bench.tx_burst import Stage
        c.event(queued.request,Stage.RF_DONE);w.poll_physical();assert w.assert_drained()
    finally:w.disarm();j.close()

@pytest.mark.parametrize('fault',[None,'owner','partial','missing_optin'])
def test_actual_public_run_cleanup_and_preopen_gate(tmp_path,monkeypatch,fault):
    from meshticulum.bench.authority import OperationLimits
    p,a,i,f,_,_=approved();p=dict(p,unit='unit-a');raw=canonical(p);calls=[];profile=RadioProfile(**p['profile'])
    authority=SimpleNamespace(plan=p,plan_bytes=raw,operation_limits=OperationLimits.from_plan(p),profile=profile,data_burst_policy=None,sends=True,
        grant=SimpleNamespace(digest=b'x'*32),local_peer=object(),application_live=lambda:True,
        deadlines=SimpleNamespace(application_deadline_monotonic=180.),close=lambda:calls.append('authority.close'))
    class Transport:
        def __init__(self,*args,**kwargs):calls.append('transport.open')
        def close(self):calls.append('transport.close')
    class Owner:
        def __init__(self,transport,*,by_id,usb_serial,**kwargs):self.transport=transport;self.by_id=by_id;self.usb_serial=usb_serial;self.fds=()
        def current(self):return fault!='owner'
        def close(self):calls.append('owner.close')
    class Worker:
        def __init__(self,channel,**kwargs):self.channel=channel
        def discover(self,**kwargs):return i
        def arm(self,**kwargs):
            calls.append('worker.arm');assert type(kwargs['continuation']) is ContinuationRecord
            assert kwargs['continuation_owner'].current() is True
            if fault=='partial':raise OSError('partial arm')
        def disarm(self):calls.append('worker.disarm')
    class Session:
        endpoint=SimpleNamespace(receipt_confirmation_enabled=False)
        def step(self):return True
        def close(self):calls.append('session.close')
    monkeypatch.setattr(radio_file,'ExclusiveSerial',Transport);monkeypatch.setattr(radio_file,'HeldHardware',Owner);monkeypatch.setattr(radio_file,'SerialWorker',Worker)
    monkeypatch.setattr(radio_file,'RunAuthority',lambda *args,**kwargs:authority)
    monkeypatch.setattr(radio_file,'host_boot',lambda:'model');monkeypatch.setattr(radio_file,'clock',lambda:(1000000,100000000))
    monkeypatch.setattr(radio_file,'SessionStore',lambda *args,**kwargs:SimpleNamespace(close=lambda:calls.append('store.close'),retire=lambda:calls.append('store.retire')))
    monkeypatch.setattr(radio_file,'SessionCipher',lambda *args:object())
    args=SimpleNamespace(boot_continuation=fault!='missing_optin',interface='usb',device='/never-open',usb_serial=i and a.approval.record.value()['baseline']['usb_serial'],grant_directory=tmp_path,unit='unit-a',chunk=0)
    window=SimpleNamespace(current=lambda:True,deadline_us=180000000,pin_mcu=lambda identity:None,check=lambda:None)
    if fault is None:assert radio_file.run_object(args,raw,a,i.version,None,window,lambda *args:Session())['sender_peer_receipt'] is True
    elif fault=='missing_optin':
        with pytest.raises(ValueError):radio_file.run_object(args,raw,a,i.version,None,window,lambda *args:Session())
        assert not calls;return
    else:
        with pytest.raises(BaseExceptionGroup):radio_file.run_object(args,raw,a,i.version,None,window,lambda *args:Session())
    assert 'transport.close' in calls and 'owner.close' in calls and 'authority.close' in calls and 'worker.disarm' in calls
    if fault=='owner':assert 'worker.arm' not in calls
