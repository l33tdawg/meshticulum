"""Combined continuation approval + authenticated marker, source/native model only."""
import copy
import hashlib
from types import SimpleNamespace

import pytest
from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.burst_protocol import policy_for_train32
from meshticulum.bench.burst_worker import BurstSerialWorker
from meshticulum.bench.continuation import (ContinuationRecord, ContinuationApproval,
    ContinuationAdmission, ContinuationOwner, canonical, context_digest)
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.serial_protocol import Decoder, Frame, Message, RadioProfile
from meshticulum.bench.serial_worker import journal_binding
from meshticulum.bench.turn_end import capability
from meshticulum.bench.tx_burst import Stage
from meshticulum.bench.wire import SessionCipher, FrameKind
from meshticulum.bench.state import SessionStore
from tests.test_continuation_execution import approved
from tests.test_meshticulum_bench_runtime import provision, Clock, BOOT
from tests import test_burst_worker as native


@pytest.fixture
def combined(tmp_path, monkeypatch):
    p, template, identity, status, _, _ = approved()
    profile = RadioProfile(**p['profile'])
    monkeypatch.setattr(native, 'VERSION', identity.version)
    clock = Clock()
    authorities, stores, ciphers, records = [], [], [], []
    for unit in ('unit-a', 'unit-b'):
        path = tmp_path / unit
        plan = provision(path, unit, b'x' * 386, clock, profile)
        plan.update(operation_limits=p['operation_limits'], data_burst=policy_for_train32(),
                    turn_end_capability=capability())
        value = template.approval.record.value()
        value['baseline']['mcu_boot'] = native.BOOT.hex()
        value['context_sha256'] = context_digest(plan)
        terms = copy.deepcopy(value); terms.pop('approval_sha256')
        raw = canonical(dict(schema='meshticulum.same-boot-continuation-approval.v1',
            approved=True, terms_sha256=hashlib.sha256(canonical(terms)).hexdigest(),
            issued_epoch_us=plan['issued_epoch_us'], expires_epoch_us=plan['expires_epoch_us']))
        pin = hashlib.sha256(raw).hexdigest(); value['approval_sha256'] = pin
        record = ContinuationRecord(canonical(value))
        admission = ContinuationAdmission(template.base, ContinuationApproval(raw, pin, record))
        plan['boot_continuation'] = value
        (path / 'grant.json').write_bytes(canonical(plan))
        authority = RunAuthority(path, expected_unit=unit, host_boot_id=BOOT,
            clock=clock, owner_probe=lambda: True, radio_admission=admission)
        store = SessionStore(path/'wire.db', authority.grant, authority.local_peer,
                             boot_id=BOOT, clock=clock, create=True)
        authorities.append(authority); stores.append(store); records.append(record)
        ciphers.append(SessionCipher(authority.grant, authority.local_peer, store, turn_end=True))

    class Channel(native.Channel):
        after = lambda self, frame: None
        def write(self, raw, deadline):
            frame, = Decoder().feed(raw)
            if frame.type is Message.STATUS:
                self.writes.append(frame)
                self.offer(Frame(Message.STATUS_REPLY, native.BOOT, frame.request, status.body))
            else:
                super().write(raw, deadline)
            self.after(frame)

    channel = Channel(train_depth=8, train_frames=32, profile=profile)
    channel.attempts, channel.airtime = 1012, 36218560
    worker = BurstSerialWorker(channel, policy=policy_for_train32())
    discovered = worker.discover(expected_board_id=1, expected_version=identity.version)
    journal = AttemptJournal(tmp_path/'unit-a'/'attempts.db',
        binding=journal_binding(authorities[0].grant.digest, discovered, profile),
        max_attempts=358, max_airtime_us=18000000, create=True,
        request_clock=lambda: clock()[0])
    owner = [True]
    args = dict(journal=journal, profile=profile, context_digest=authorities[0].grant.digest,
        authorize=authorities[0].application_live,
        authority_deadline=authorities[0].deadlines.application_deadline_monotonic,
        continuation=records[0],
        continuation_owner=ContinuationOwner(lambda: owner[0], lambda: owner[0]))
    case = SimpleNamespace(worker=worker, channel=channel, journal=journal,
        authorities=authorities, ciphers=ciphers, owner=owner, args=args, records=records)
    try:
        yield case
    finally:
        worker.disarm()
        journal.close()
        for store, authority in zip(stores, authorities):
            store.close(); authority.close()


def normal_then_reserve(case):
    w = case.worker
    w.arm(**case.args)
    packet = case.ciphers[0].seal(b'first DATA', FrameKind.DATA,
                                authorize=case.authorities[0].live)
    first = w.queue_data(packet, logical_id=b'a'*32, owner_frame_id=1)
    seal = w.reserve_turn_end(logical_id=b'b'*32, owner_frame_id=2, plaintext=b'last DATA')
    return first, seal


def test_combined_approval_route_arm_open_marker_and_retirement(combined):
    c = combined; w = c.worker
    a, b = c.authorities
    assert a.grant.digest == b.grant.digest
    assert a.route_record['turn_end_capability'] == capability()
    assert a.route_record['boot_continuation'] == b.route_record['boot_continuation']
    first, seal = normal_then_reserve(c)
    original = w.train_identity
    arm = next(f for f in c.channel.writes if f.type is Message.ARM)
    assert arm.request == w._arm_request == w._open.arm_request == c.channel.arm_request
    packet = c.ciphers[0].seal(seal.plaintext, FrameKind.DATA_TURN_END, authorize=a.live)
    w.bind_turn_end_packet(seal, packet)
    last = w.queue_turn_end(seal, packet)
    decoded = c.ciphers[1].open(packet, authorize=b.live)
    assert decoded.kind is FrameKind.DATA_TURN_END and decoded.payload == seal.plaintext
    assert w.train_identity == (*original[:3], original[3]+1)
    assert c.journal.snapshot()['attempts'] == 2 and c.channel.attempts == 1014
    assert not w.has_capacity
    for result in (first, last): c.channel.event(result.request, Stage.RF_DONE)
    w.drain_physical(); assert w._turn_end_seal is seal
    w.retire_drained_train(); assert w._turn_end_seal is None and w.has_capacity
    assert c.journal.snapshot()['attempts'] == 2  # retirement never refunds


def test_combined_late_owner_mutation_blocks_arm(combined):
    c = combined
    c.channel.after = lambda frame: c.owner.__setitem__(0, False) if frame.type is Message.BURST_STATUS else None
    with pytest.raises((ValueError, HardwareError)): c.worker.arm(**c.args)
    assert not any(f.type in (Message.ARM, Message.BURST_OPEN, Message.BURST_DATA) for f in c.channel.writes)
    assert c.journal.snapshot()['attempts'] == 0


def test_combined_marker_callback_reentry_latches_after_continuation(combined):
    c = combined; w = c.worker
    first, seal = normal_then_reserve(c)
    def authorize():
        with pytest.raises(HardwareError): w.queue_data(b'reentry', logical_id=b'z'*32)
        return c.authorities[0].live()
    packet = c.ciphers[0].seal(seal.plaintext, FrameKind.DATA_TURN_END, authorize=authorize)
    with pytest.raises(HardwareError): w.bind_turn_end_packet(seal, packet)
    assert w._turn_end_failed and w._turn_end_seal is seal and not w.has_capacity
    assert c.journal.snapshot()['attempts'] == 1 and c.channel.attempts == 1013
    c.channel.event(first.request, Stage.RF_DONE)


def test_combined_context_mutation_cannot_keep_old_approval(combined):
    c = combined; a = c.authorities[0]
    changed = copy.deepcopy(a.plan); changed['turn_end_capability']['host_pending_limit'] = 4
    with pytest.raises(ValueError): c.records[0].check_plan(changed)
    a.plan['turn_end_capability']['host_pending_limit'] = 4
    assert not a.live()
    with pytest.raises((ValueError, HardwareError)): c.worker.arm(**c.args)
    assert not any(f.type is Message.ARM for f in c.channel.writes)

@pytest.mark.parametrize('boundary', ['unexpected_guard', 'post_clock'])
def test_combined_send_keeps_sealed_data_guard(combined, boundary):
    c = combined; w = c.worker
    first, seal = normal_then_reserve(c)
    before = len(c.channel.writes)
    callback_calls = []
    frame = Frame(Message.BURST_DATA, w.identity.boot, 999, b'never written')
    original_clock = c.channel.monotonic
    if boundary == 'post_clock':
        def mutate():
            value = original_clock(); w._turn_generation += 1; return value
        c.channel.monotonic = mutate
    try:
        with pytest.raises(HardwareError):
            w._send(frame, 180., **({'before_write': lambda: callback_calls.append(True)}
                                   if boundary == 'unexpected_guard' else {}))
        assert len(c.channel.writes) == before and callback_calls == []
        assert w._turn_end_seal is seal and c.journal.snapshot()['attempts'] == 1
    finally:
        c.channel.monotonic = original_clock
        c.channel.event(first.request, Stage.RF_DONE)
