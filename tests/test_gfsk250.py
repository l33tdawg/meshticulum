"""Explicit GFSK250 compatibility, real SQLite/AEAD and C++ source controls.

Channels/radio hardware are modeled. No test establishes physical speed/range.
"""
from contextlib import ExitStack, closing
from dataclasses import replace
import hashlib
import json
import math
import struct
import subprocess

import pytest

from meshticulum.bench.admission import GfskAdmission, Gfsk250Admission
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.gfsk_profile import GfskProfile, GfskProfileError, RECORD
from meshticulum.bench.gfsk250_profile import Gfsk250Profile, CAPABILITY, estimated_airtime_us, sx1262_plan
from meshticulum.bench.gfsk250_sx1262 import Gfsk250SX1262
from meshticulum.bench.hat_worker import GfskHATWorker, Gfsk250HATWorker, journal_binding as hat_binding
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.serial_protocol import Decoder, Frame, Message, Outcome, SerialError, Gfsk250RawReceive, Gfsk250Status, GfskStatus
from meshticulum.bench.serial_worker import GfskSerialWorker, Gfsk250SerialWorker, journal_binding
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import SessionCipher, WireError
from meshticulum.scheduler.budget import BudgetPolicy, Ledger
from tests.test_gfsk_profile import native as profile_native
from tests.test_gfsk_workers import GfskChip
from tests.test_meshticulum_bench_worker import Channel, BOOT, VERSION
from tests.test_meshticulum_bench_runtime import Clock, ModelWorker, BOOT as HOST_BOOT, provision

P = Gfsk250Profile(921375000, 0, 60000, 10, 10000000)
A = Gfsk250Admission('MY', (921375000,), (0,), 1000, 180000, 256, 60000000)


def test_exact_version2_host_firmware_airtime_and_radiolib_sequence(profile_native):
    raw = P.encode(board_id=1)
    assert len(raw) == 47 and raw[:3] == b'GF\2' and Gfsk250Profile.decode(raw, board_id=1) == P
    values = subprocess.run([str(profile_native), '250'], input=raw, check=True, capture_output=True).stdout.splitlines()
    assert list(map(int, values)) == [estimated_airtime_us(P, n) for n in range(1, 256)]
    assert estimated_airtime_us(P, 255) == 8640 and estimated_airtime_us(P, 70) == 2720
    subprocess.run([str(profile_native), '250', 'program'], input=raw, check=True, capture_output=True)
    plan = sx1262_plan(P)
    assert plan.modulation == bytes.fromhex('0010000909010000')
    assert plan.packet == bytes.fromhex('002007400001ff0201')
    assert Gfsk250Profile.from_dict(P.to_dict(), board_id=1) == P
    with pytest.raises(GfskProfileError): GfskProfile.decode(raw, board_id=1)
    assert subprocess.run([str(profile_native)], input=raw, capture_output=True).returncode == 2


@pytest.mark.parametrize('field,value,index', [
    ('bit_rate_bps', 125000, 3), ('deviation_hz', 31250, 4), ('rx_bandwidth_hz', 312000, 5),
    ('frequency_hz', 902233499, 2), ('frequency_hz', 927766501, 2),
    ('chip_power_dbm', 11, 6), ('lease_ms', 999, 10), ('max_boot_attempts', 0, 11),
    ('max_boot_estimated_airtime_us', 0, 12), ('preamble_bits', 16, 7), ('shaping_code', 0, 9)])
def test_unreviewed250_values_fail_both_parsers(profile_native, field, value, index):
    with pytest.raises(GfskProfileError): replace(P, **{field: value}).encode(board_id=1)
    parts = list(RECORD.unpack(P.encode(board_id=1))); parts[index] = value
    assert subprocess.run([str(profile_native), '250'], input=RECORD.pack(*parts), capture_output=True).returncode == 2


def test_no_version_or_json_downgrade_or_implicit_conversion(profile_native):
    old = GfskProfile(921375000, 0, 60000, 10, 10000000)
    for raw in (old.encode(board_id=1), P.encode(board_id=1)[:2] + b'\1' + P.encode(board_id=1)[3:]):
        with pytest.raises(GfskProfileError): Gfsk250Profile.decode(raw, board_id=1)
        assert subprocess.run([str(profile_native), '250'], input=raw, capture_output=True).returncode == 2
    with pytest.raises(GfskProfileError): Gfsk250Profile.from_dict(old.to_dict(), board_id=1)
    with pytest.raises(GfskProfileError): GfskProfile.from_dict(P.to_dict(), board_id=1)
    with pytest.raises(GfskProfileError): estimated_airtime_us(old, 255)
    with pytest.raises(GfskProfileError): replace(P, lease_ms=True).encode(board_id=1)
    for hz in (902233500, 927766500): replace(P, frequency_hz=hz).encode(board_id=1)


class Channel250(Channel):
    def write(self, data, deadline):
        frame, = Decoder().feed(data)
        if frame.type is Message.GFSK250_CAPABILITIES:
            self.writes.append(frame)
            kind = Message.REFUSED if self.fault == 'old_firmware' else Message.GFSK250_CAPABILITIES_REPLY
            body = b'\1' if self.fault == 'old_firmware' else CAPABILITY
            if self.fault == 'wrong_capability': body = body[:-1] + bytes([body[-1] ^ 1])
            boot = bytes(16) if self.fault == 'capability_boot' else BOOT
            if self.fault == 'late_capability': self.time += 3.0
            self.offer(Frame(kind, boot, frame.request, body)); return
        if frame.type is Message.GFSK250_ARM:
            self.writes.append(frame)
            self.actual_profile = Gfsk250Profile.decode(frame.body, board_id=1)
            kind = Message.GFSK_ARM_REPLY if self.fault == 'downgrade_arm' else Message.GFSK250_ARM_REPLY
            self.offer(Frame(kind, BOOT, frame.request, frame.body)); return
        if frame.type is not Message.TRANSMIT: return super().write(data, deadline)
        self.writes.append(frame)
        if self.fault == 'partial': raise OSError('modeled partial write')
        estimate = estimated_airtime_us(self.actual_profile, len(frame.body)) + (1 if self.fault == 'estimate' else 0)
        for outcome in (Outcome.STARTED, Outcome.RF_DONE):
            body = struct.pack('!B32sQQIh', outcome, hashlib.sha256(frame.body).digest(), estimate,
                               0 if outcome is Outcome.STARTED else estimate,
                               0 if outcome is Outcome.STARTED else 1, 0)
            self.offer(Frame(Message.TX_RESULT, BOOT, frame.request, body))


@pytest.mark.parametrize('fault', ['old_firmware', 'wrong_capability', 'capability_boot', 'late_capability'])
def test_capability_refusal_never_arms_or_transmits(fault):
    channel = Channel250(fault); worker = Gfsk250SerialWorker(channel)
    with pytest.raises((HardwareError, SerialError)): worker.discover(expected_board_id=1, expected_version=VERSION)
    assert [f.type for f in channel.writes] == [Message.HELLO, Message.GFSK250_CAPABILITIES]
    with pytest.raises(HardwareError): worker.receive()


def test_failed_capability_guard_cannot_be_filled_by_assignment_or_arm(tmp_path):
    tmp_path.chmod(0o700);channel=Channel250('old_firmware');worker=Gfsk250SerialWorker(channel)
    with pytest.raises(HardwareError,match='capability_absent'):
        worker.discover(expected_board_id=1,expected_version=VERSION)
    for name in ('_Gfsk250SerialWorker__capability_check','_Gfsk250SerialWorker__discover250','_live'):
        with pytest.raises(HardwareError,match='guard_changed'): setattr(worker,name,lambda *a:None)
        with pytest.raises(HardwareError,match='guard_changed'): delattr(worker,name)
    identity=worker.identity;before=len(channel.writes)
    with closing(AttemptJournal(tmp_path/'unsupported.db',binding=journal_binding(b'u'*32,identity,P),
        max_attempts=10,max_airtime_us=10000000,create=True,request_clock=lambda:100000000)) as journal:
        with pytest.raises(HardwareError,match='capability_absent'):
            worker.arm(journal=journal,profile=P,context_digest=b'u'*32,authorize=lambda:True,authority_deadline=200)
        assert len(channel.writes)==before and journal.snapshot()['attempts']==0


def test_failed_capability_cannot_bypass_live_method_through_dict_shadow(tmp_path):
    tmp_path.chmod(0o700);channel=Channel250('old_firmware');worker=Gfsk250SerialWorker(channel)
    with pytest.raises(HardwareError):worker.discover(expected_board_id=1,expected_version=VERSION)
    worker.__dict__['_live']=lambda *a:None
    identity=worker.identity;before=len(channel.writes)
    with closing(AttemptJournal(tmp_path/'shadow.db',binding=journal_binding(b's'*32,identity,P),
        max_attempts=10,max_airtime_us=10000000,create=True,request_clock=lambda:100000000)) as journal:
        with pytest.raises(HardwareError,match='guard_changed'):
            worker.arm(journal=journal,profile=P,context_digest=b's'*32,authorize=lambda:True,authority_deadline=200)
        assert len(channel.writes)==before and journal.snapshot()['attempts']==0


@pytest.mark.parametrize('fault', [None, 'partial', 'estimate'])
def test_serial250_paid_reservation_exact_estimate_and_unknown_containment(tmp_path, fault):
    tmp_path.chmod(0o700); channel = Channel250(fault); worker = Gfsk250SerialWorker(channel)
    identity = worker.discover(expected_board_id=1, expected_version=VERSION)
    with closing(AttemptJournal(tmp_path/'attempts.db', binding=journal_binding(b'x'*32, identity, P),
             max_attempts=10, max_airtime_us=10000000, create=True, request_clock=lambda:100000000)) as journal:
        worker.arm(journal=journal, profile=P, context_digest=b'x'*32, authorize=lambda:True, authority_deadline=200)
        assert channel.writes[-1].type is Message.GFSK250_ARM
        result = worker.transmit(bytes(range(255)))
        assert result.outcome == ('RF_DONE' if fault is None else 'UNKNOWN')
        assert worker.fenced is (fault is not None)
        assert journal.snapshot()['attempts'] == 1
        assert journal.snapshot()['reserved_estimated_airtime_us'] == math.ceil(8640*1.1)+1000


def test_capability_and_modem_identity_are_not_mutable_after_discovery():
    channel = Channel250(); worker = Gfsk250SerialWorker(channel)
    worker.discover(expected_board_id=1, expected_version=VERSION)
    with pytest.raises(HardwareError): worker._Gfsk250SerialWorker__capability_check = lambda:None
    worker.identity = replace(worker.identity, version='other')
    with pytest.raises(HardwareError, match='capability_changed'): worker._live()


def test_capability_cannot_move_to_another_transport():
    worker=Gfsk250SerialWorker(Channel250());worker.discover(expected_board_id=1,expected_version=VERSION)
    worker.channel=Channel250()
    with pytest.raises(HardwareError,match='capability_changed'):worker._live()


def test_serial250_arm_downgrade_and_expiry_preserve_profile_and_paid_state(tmp_path):
    tmp_path.chmod(0o700); channel = Channel250('downgrade_arm'); worker = Gfsk250SerialWorker(channel)
    identity = worker.discover(expected_board_id=1, expected_version=VERSION)
    with closing(AttemptJournal(tmp_path/'arm.db', binding=journal_binding(b'y'*32, identity, P),
             max_attempts=10, max_airtime_us=10000000, create=True, request_clock=lambda:100000000)) as journal:
        with pytest.raises(HardwareError, match='arm_not_confirmed'):
            worker.arm(journal=journal, profile=P, context_digest=b'y'*32, authorize=lambda:True, authority_deadline=200)
        assert worker.fenced and journal.snapshot()['attempts'] == 0
        assert P.lease_ms == 60000


def test_serial250_clips_exact_v8_setup_to_original_absolute_end(tmp_path):
    class PreparedLate(Channel250):
        def write(self, data, deadline):
            frame, = Decoder().feed(data)
            if frame.type is Message.DISARM: self.time += .695360
            super().write(data, deadline)
    tmp_path.chmod(0o700); channel=PreparedLate(); channel.time=302085.817965
    worker=Gfsk250SerialWorker(channel); identity=worker.discover(expected_board_id=1,expected_version=VERSION)
    profile=replace(P,lease_ms=73045); original=profile.encode(board_id=1); end=302159.866307
    with closing(AttemptJournal(tmp_path/'late.db',binding=journal_binding(b'l'*32,identity,profile),
        max_attempts=10,max_airtime_us=10000000,create=True,request_clock=lambda:100000000)) as journal:
        worker.arm(journal=journal,profile=profile,context_digest=b'l'*32,authorize=lambda:True,authority_deadline=end)
        assert channel.writes[-1].type is Message.GFSK250_ARM
        assert worker.arm_lease_ms==71252 < profile.lease_ms
        assert worker.arm_lease_end_upper_bound < end and worker.deadline==end
        assert profile.encode(board_id=1)==original and journal.binding==journal_binding(b'l'*32,identity,profile)
        assert journal.snapshot()['attempts']==0 and journal.snapshot()['reserved_estimated_airtime_us']==0
        channel.time=end
        with pytest.raises(HardwareError): worker.transmit(b'expired')
        assert journal.snapshot()['attempts']==0


@pytest.mark.parametrize('refuse_on',[1,2,3])
def test_serial250_revocation_during_setup_never_reaches_arm_or_rf(tmp_path,refuse_on):
    tmp_path.chmod(0o700);channel=Channel250();worker=Gfsk250SerialWorker(channel)
    identity=worker.discover(expected_board_id=1,expected_version=VERSION);calls=[0]
    def authorize():
        calls[0]+=1
        return calls[0] < refuse_on
    with closing(AttemptJournal(tmp_path/'revoked.db',binding=journal_binding(b'r'*32,identity,P),
        max_attempts=10,max_airtime_us=10000000,create=True,request_clock=lambda:100000000)) as journal:
        with pytest.raises(HardwareError):
            worker.arm(journal=journal,profile=P,context_digest=b'r'*32,authorize=authorize,authority_deadline=200)
        assert not any(f.type in (Message.GFSK250_ARM,Message.TRANSMIT) for f in channel.writes)
        assert journal.snapshot()['attempts']==0


@pytest.mark.parametrize('worker_type,profile_type', [(GfskSerialWorker, Gfsk250Profile), (Gfsk250SerialWorker, GfskProfile)])
def test_serial_modes_never_accept_the_other_profile(worker_type, profile_type):
    worker = worker_type(Channel250()); worker.discover(expected_board_id=1,expected_version=VERSION)
    with pytest.raises(HardwareError, match='invalid_arm_parameters'):
        worker.arm(journal=None, profile=profile_type(921375000,0,60000,10,10000000),
                   context_digest=bytes(32), authorize=lambda:True, authority_deadline=200)


def test_hat250_spi_vectors_revocation_and_original_expiry_do_not_refund(tmp_path):
    tmp_path.chmod(0o700); chip = GfskChip(); admitted = [True]
    with closing(AttemptJournal(tmp_path/'hat.db', binding=hat_binding(b'z'*32, chip.boot_id, P),
             max_attempts=10, max_airtime_us=10000000, create=True, request_clock=lambda:100)) as journal:
        worker = Gfsk250HATWorker(chip,journal=journal,profile=P,context_digest=b'z'*32,
                 authorize=lambda:admitted[0],authority_deadline=160,antenna_confirmed=True,
                 monotonic=chip.monotonic,time_basis='model-monotonic')
        assert b'\x8b'+sx1262_plan(P).modulation in chip.commands
        assert worker.transmit(bytes(255)).outcome == 'RF_DONE'; before = journal.snapshot()
        assert before['reserved_estimated_airtime_us'] == math.ceil(8640*1.1)+1000
        admitted[0] = False
        with pytest.raises(HardwareError): worker.transmit(b'revoked')
        admitted[0] = True; worker.deadline = 200; chip.time = 160
        with pytest.raises(HardwareError): worker.transmit(b'expired')
        assert journal.snapshot() == before
        worker.disarm()
    with pytest.raises(HardwareError): Gfsk250SX1262(GfskChip()).configure(GfskProfile(921375000,0,60000,10,10000000))


def test_status_and_raw_receive_cannot_cross_modes():
    raw = Frame(Message.GFSK250_RECEIVE, BOOT, 0, struct.pack('!h',-925)+b'encrypted')
    assert Gfsk250RawReceive.from_frame(raw, boot=BOOT).packet == b'encrypted'
    body = P.encode(board_id=1)+struct.pack('!IQI3B',1,8640,3,1,0,0)
    frame = Frame(Message.GFSK250_STATUS_REPLY, BOOT, 7, body)
    assert Gfsk250Status.from_reply(frame,boot=BOOT,request=7,board_id=1).profile == P
    with pytest.raises(SerialError): GfskStatus.from_reply(frame,boot=BOOT,request=7,board_id=1)
    for worker_type in (GfskSerialWorker,):
        worker=worker_type(Channel250());worker.identity=type('Identity',(),{'boot':BOOT})()
        with pytest.raises(HardwareError,match='wrong_receive_modem'): worker._ingest(raw.encode())


def plan250(path, unit, payload, clock, marker='gfsk250'):
    plan = provision(path,unit,payload,clock)
    plan['profile'] = replace(P,lease_ms=90000,max_boot_attempts=256,max_boot_estimated_airtime_us=60000000).to_dict()
    if marker is not None: plan['radio_modem'] = marker
    (path/'grant.json').write_text(json.dumps(plan)); (path/'grant.json').chmod(0o600)


@pytest.mark.parametrize('fault',['old_admission','missing_marker','old_marker','old_json'])
def test_generic250_requires_exact_marker_profile_and_admission(tmp_path, fault):
    clock=Clock();path=tmp_path/'a';plan250(path,'unit-a',b'public',clock,
        None if fault=='missing_marker' else 'gfsk125' if fault=='old_marker' else 'gfsk250')
    if fault=='old_json':
        plan=json.loads((path/'grant.json').read_bytes());plan['profile']=GfskProfile(921375000,0,90000,256,60000000).to_dict()
        (path/'grant.json').write_text(json.dumps(plan))
    admission=GfskAdmission('MY',(921375000,),(0,),1000,180000,256,60000000) if fault=='old_admission' else A
    with pytest.raises((WireError,GfskProfileError,TypeError)):
        RunAuthority(path,radio_admission=admission,expected_unit='unit-a',host_boot_id=HOST_BOOT,clock=clock,owner_probe=lambda:True)


def test_generic250_aead_frozen_flow_real_sqlite_and_mutation_revocation(tmp_path):
    clock=Clock();payload=bytes(range(256))*4;flows=[];workers=[];journals=[];owners=[True]
    with ExitStack() as stack:
        for unit in ('unit-a','unit-b'):
            path=tmp_path/unit;plan250(path,unit,payload,clock)
            journal=AttemptJournal(path/'attempts.db',binding=bytes(32),max_attempts=256,max_airtime_us=60000000,
                                   create=True,request_clock=lambda:clock()[0]);stack.callback(journal.close)
            worker=ModelWorker(journal,clock);workers.append(worker);journals.append(journal)
            authority=RunAuthority(path,radio_admission=A,expected_unit=unit,host_boot_id=HOST_BOOT,clock=clock,
                                   owner_probe=lambda:owners[0]);stack.callback(authority.close)
            store=SessionStore(path/'wire.db',authority.grant,authority.local_peer,boot_id=HOST_BOOT,clock=clock,create=True);stack.callback(store.close)
            cipher=SessionCipher(authority.grant,authority.local_peer,store)
            ledger=Ledger(BudgetPolicy(180000000,60000000,120000000,1000000,quantization_us=1),authority.ledger_epoch,clock()[1])
            endpoint=BenchFlow(authority=authority,worker=worker,cipher=cipher,journal=journal,flow_key=authority.flow_key,
                contract=authority.contract,profile=authority.carrier_profile,ledger=ledger,scheduler_epoch=authority.scheduler_epoch,
                broker_epoch=authority.broker_epoch,sends=unit=='unit-a',clock=clock);stack.callback(endpoint.close);flows.append(endpoint)
            assert authority.route_record['radio_modem']=='gfsk250' and type(authority.profile) is Gfsk250Profile
        workers[0].peer=workers[1];workers[1].peer=workers[0];flows[0].send(payload)
        for _ in range(1500):
            flows[0].step();flows[1].step();clock.advance(50000)
            if flows[0].flow.complete and flows[1].flow.complete: break
        assert flows[0].flow.complete and flows[1].flow.complete and bytes(flows[1].received)==payload
        assert all(j.snapshot()['attempts']>0 for j in journals)
        owners[0]=False;assert not flows[0].authority.application_live();owners[0]=True
        flows[0].authority.plan['radio_modem']='gfsk125';assert not flows[0].authority.live()
