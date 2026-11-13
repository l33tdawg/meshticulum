import copy
import hashlib
import struct
from dataclasses import asdict, replace
import pytest
from meshticulum.bench.continuation import ContinuationRecord, canonical, context_digest, SCHEMA
from meshticulum.bench.admission import RadioAdmission
from meshticulum.bench.serial_protocol import RadioProfile, Frame, FirmwareIdentity, Message
from meshticulum.bench.burst_protocol import policy_for_train32


def fixture():
    old=RadioProfile(921375000,500000,5,5,0,32,18,60000,1024,300000000)
    plan=dict(schema='meshticulum.bench-run.v1',initiator={'chain_id':'test','agent_id':'a'},responder={'chain_id':'test','agent_id':'c'},session_id='01'*16,generation='02'*16,
        profile=asdict(replace(old,max_boot_attempts=1370)),object={'bytes':65536,'sha256':'03'*32},sender_unit='unit-a',country='MY',antenna_confirmed=True,
        max_frames=512,operation_limits={'sender':{'attempts':358,'estimated_airtime_us':18000000},'receiver':{'attempts':192,'estimated_airtime_us':9000000}},
        data_burst=policy_for_train32(),issued_epoch_us=1000000,expires_epoch_us=181000000)
    value=dict(schema=SCHEMA,original_ceiling=1024,absolute_ceiling=1370,cumulative_airtime_us=300000000,
        baseline=dict(mcu_boot='000008f6a51b5bf80000000000000048',firmware='mtb1-86f7307b7c',usb_serial='F8:5B:1B:A5:F6:08',boot_attempts=1012,boot_reserved_estimated_airtime_us=36218560,dropped_receive=0),
        operation_limits=copy.deepcopy(plan['operation_limits']),history_sha256='11'*32,approval_sha256='22'*32,
        context_sha256=context_digest(plan),prior_profile_sha256=hashlib.sha256(old.encode(board_id=1)).hexdigest())
    identity=FirmwareIdentity(bytes.fromhex(value['baseline']['mcu_boot']),1,value['baseline']['firmware'])
    frame=Frame(Message.STATUS_REPLY,identity.boot,7,old.encode(board_id=1)+struct.pack('!IQI',1012,36218560,0)+b'\0'*3)
    return value,plan,identity,frame


def test_inert_exact_record_and_native_binding():
    value,plan,identity,frame=fixture();record=ContinuationRecord(canonical(value));original=copy.deepcopy(plan)
    assert record.check_plan(plan) is None
    assert record.check_status(frame,request=7,identity=identity,held_usb_serial=value['baseline']['usb_serial']) is None
    assert plan==original
    returned=record.value();returned['absolute_ceiling']=100000
    assert record.value()['absolute_ceiling']==1370

@pytest.mark.parametrize('key,value',[('original_ceiling',1025),('original_ceiling',True),('absolute_ceiling',1371),('absolute_ceiling',100000),('cumulative_airtime_us',300000001),('approval_sha256',None),('history_sha256','x'*64),('extra',1)])
def test_record_mutations(key,value):
    record,*_=fixture();record[key]=value
    with pytest.raises(ValueError):ContinuationRecord(canonical(record))

@pytest.mark.parametrize('key,value',[('boot_attempts',True),('boot_attempts',1025),('dropped_receive',1),('mcu_boot','0'*32),('usb_serial','unknown'),('boot_reserved_estimated_airtime_us',300000000)])
def test_baseline_mutations(key,value):
    record,*_=fixture();record['baseline'][key]=value
    with pytest.raises(ValueError):ContinuationRecord(canonical(record))

@pytest.mark.parametrize('key,value',[('session_id','ff'*16),('expires_epoch_us',181000001),('sender_unit','unit-c'),('max_frames',513)])
def test_plan_drift(key,value):
    record,plan,*_=fixture();checked=ContinuationRecord(canonical(record));plan[key]=value
    with pytest.raises(ValueError):checked.check_plan(plan)

@pytest.mark.parametrize('mutation',['request','boot','firmware','serial','count','airtime','drop','armed','fenced','profile','short'])
def test_native_refusal(mutation):
    value,_,identity,frame=fixture();record=ContinuationRecord(canonical(value));serial=value['baseline']['usb_serial'];request=7
    if mutation=='request':request=8
    if mutation=='boot':identity=replace(identity,boot=b'1'*16)
    if mutation=='firmware':identity=replace(identity,version='mtb1-0000000000')
    if mutation=='serial':serial='00:00:00:00:00:00'
    body=bytearray(frame.body)
    if mutation=='count':body[30:34]=struct.pack('!I',1013)
    if mutation=='airtime':body[34:42]=struct.pack('!Q',36218561)
    if mutation=='drop':body[42:46]=struct.pack('!I',1)
    if mutation=='armed':body[46]=1
    if mutation=='fenced':body[48]=1
    if mutation=='profile':body[0]^=1
    if mutation=='short':body.pop()
    with pytest.raises(ValueError):record.check_status(replace(frame,body=bytes(body)),request=request,identity=identity,held_usb_serial=serial)

def test_legacy_admission_still_rejects_higher_ceiling():
    value,plan,*_=fixture()
    with pytest.raises(ValueError,match='invalid_radio_admission_limits'):
        RadioAdmission('MY',((921375000,500000,5,5,0,32,18),),60000,60000,1370,300000000)
    with pytest.raises(ValueError):ContinuationRecord(canonical(value)+b' ')

@pytest.mark.parametrize('field',['file_capability','turn_end_capability'])
def test_behavior_capability_is_bound(field):
    value,plan,*_=fixture();plan[field]={'schema':'test-capability','enabled':True}
    value['context_sha256']=context_digest(plan);record=ContinuationRecord(canonical(value));record.check_plan(plan)
    plan[field]['enabled']=False
    with pytest.raises(ValueError):record.check_plan(plan)

def test_unknown_behavior_field_and_boolean_limit_refused():
    value,plan,*_=fixture();record=ContinuationRecord(canonical(value))
    plan['new_behavior_capability']={'enabled':True}
    with pytest.raises(ValueError,match='unrecognized'):record.check_plan(plan)
    value,plan,*_=fixture();plan['operation_limits']['sender']['attempts']=True
    with pytest.raises(ValueError):record.check_plan(plan)
    value['operation_limits']=plan['operation_limits'];value['context_sha256']=context_digest(plan)
    with pytest.raises(ValueError):ContinuationRecord(canonical(value))

def test_embedded_record_must_equal_checked_bytes():
    value,plan,*_=fixture();record=ContinuationRecord(canonical(value))
    plan['boot_continuation']=copy.deepcopy(value);record.check_plan(plan)
    plan['boot_continuation']['approval_sha256']='44'*32
    with pytest.raises(ValueError,match='record_changed'):record.check_plan(plan)

@pytest.mark.parametrize('schema',[None,'meshticulum.service-run.v1','unknown'])
def test_exact_shared_protocol_schema_required(schema):
    value,plan,*_=fixture();record=ContinuationRecord(canonical(value))
    if schema is None:plan.pop('schema')
    else:plan['schema']=schema
    with pytest.raises(ValueError,match='invalid_continuation_plan'):record.check_plan(plan)
