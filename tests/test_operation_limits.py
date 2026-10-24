"""Bound incremental host debt with real grants/journals/workers and modeled USB.

No hardware, native reset or physical RF/performance claim is made here.
"""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
import json
import math
import struct
import sys
from types import SimpleNamespace

import pytest

from meshticulum.bench.attempts import AttemptError, AttemptJournal
from meshticulum.bench.authority import OperationLimits, RunAuthority
from meshticulum.bench.burst_protocol import POLICY
from meshticulum.bench.burst_worker import BurstSerialWorker
from meshticulum.bench.serial_protocol import Decoder, Message
from meshticulum.bench.serial_worker import estimated_airtime_us
from meshticulum.bench.state import SessionStore
from meshticulum.bench.tx_burst import Stage
from meshticulum.bench.wire import SessionCipher, WireError
from tests.radio_profiles import SOURCE_ADMISSION, profile_for
from tests.test_burst_runtime import RadioChannel
from tests.test_burst_worker import VERSION
from tests.test_meshticulum_bench_runtime import BOOT, Clock, ModelWorker, provision
from tests.test_radio_file_example import example


PROFILE = replace(profile_for('sf7-500'), max_boot_attempts=480,
                  max_boot_estimated_airtime_us=20000000)
ADMISSION = replace(SOURCE_ADMISSION, max_boot_attempts=480,
                    max_boot_estimated_airtime_us=20000000)
PACKET = bytes(range(255))
COST = math.ceil(estimated_airtime_us(PROFILE, len(PACKET)) * 1.1) + 1000
LIMITS = dict(sender=dict(attempts=2, estimated_airtime_us=2 * COST),
              receiver=dict(attempts=3, estimated_airtime_us=3 * COST))


def grant(path, clock, *, unit='unit-a', limits=LIMITS, burst=False, session=None):
    plan = provision(path, unit, b'one', clock, PROFILE)
    if limits is not None:
        plan['operation_limits'] = deepcopy(limits)
    if burst:
        plan['data_burst'] = dict(POLICY)
    if session is not None:
        plan['session_id'] = f'{session:032x}'
    raw = json.dumps(plan).encode()
    (path / 'grant.json').write_bytes(raw)
    return plan, raw


def authority(path, clock, unit):
    return RunAuthority(path, expected_unit=unit, host_boot_id=BOOT,
        clock=clock, owner_probe=lambda: True, radio_admission=ADMISSION)


def test_shared_role_map_binds_both_contexts_and_selects_original_local_role(tmp_path):
    clock = Clock()
    digests = []
    with ExitStack() as stack:
        for index in range(2):
            limits = deepcopy(LIMITS)
            limits['receiver']['attempts'] += index
            pair = []
            for unit in ('unit-a', 'unit-b'):
                path = tmp_path / f'{index}-{unit}'
                grant(path, clock, unit=unit, limits=limits)
                current = authority(path, clock, unit)
                stack.callback(current.close)
                expected = limits['sender' if unit == 'unit-a' else 'receiver']
                assert current.operation_limits == OperationLimits(**expected)
                assert current.route_record['operation_limits'] == limits
                current.sends = not current.sends
                assert current.operation_limits == OperationLimits(**expected)
                pair.append(current.grant.digest)
            assert pair[0] == pair[1]
            digests.append(pair[0])
        assert digests[0] != digests[1]  # Opposite-role-only change binds both peers.


def test_plan_limit_mutation_invalidates_original_admission(tmp_path):
    clock = Clock()
    path = tmp_path / 'grant'
    grant(path, clock)
    current = authority(path, clock, 'unit-a')
    try:
        current.plan['operation_limits']['receiver']['attempts'] += 1
        assert not current.live()
        with pytest.raises(WireError, match='bench_admitted_plan_changed'):
            current.operation_limits
    finally:
        current.close()


def test_canonical_flow_refuses_journal_above_grant_role_cap_before_data(tmp_path):
    clock = Clock()
    path = tmp_path / 'grant'
    grant(path, clock)
    with ExitStack() as stack:
        current = authority(path, clock, 'unit-a')
        stack.callback(current.close)
        journal = AttemptJournal(path / 'attempts.db', binding=current.grant.digest,
            max_attempts=3, max_airtime_us=3 * COST, create=True)
        stack.callback(journal.close)
        store = SessionStore(path / 'wire.db', current.grant, current.local_peer,
                             boot_id=BOOT, clock=clock, create=True)
        stack.callback(store.close)
        worker = ModelWorker(journal, clock)
        with pytest.raises(WireError, match='^bench_journal_limits_exceed_operation_grant$'):
            example.ChunkTransfer(current, worker, journal,
                SessionCipher(current.grant, current.local_peer, store),
                current.plan['object'], None, clock=clock)
        assert not worker.sent and journal.snapshot()['attempts'] == 0


@pytest.mark.parametrize('limits', [
    None, dict(sender=dict(attempts=480, estimated_airtime_us=20000000),
               receiver=dict(attempts=480, estimated_airtime_us=20000000)),
])
def test_absent_or_equal_limits_keep_absolute_profile_defaults(tmp_path, limits):
    clock = Clock()
    path = tmp_path / 'grant'
    grant(path, clock, limits=limits)
    current = authority(path, clock, 'unit-a')
    try:
        assert current.operation_limits == OperationLimits(480, 20000000)
        assert ('operation_limits' in current.route_record) is (limits is not None)
    finally:
        current.close()


@pytest.mark.parametrize('bad', [
    None, {}, {'sender': LIMITS['sender']},
    dict(sender=dict(attempts=True, estimated_airtime_us=COST), receiver=LIMITS['receiver']),
    dict(sender=dict(attempts=0, estimated_airtime_us=COST), receiver=LIMITS['receiver']),
    dict(sender=dict(attempts=1, estimated_airtime_us=-1), receiver=LIMITS['receiver']),
    dict(sender=dict(attempts=481, estimated_airtime_us=COST), receiver=LIMITS['receiver']),
    dict(sender=LIMITS['sender'], receiver=dict(attempts=1, estimated_airtime_us=20000001)),
    dict(sender=dict(attempts=1, estimated_airtime_us=COST, extra=1), receiver=LIMITS['receiver']),
])
def test_invalid_grant_limits_refuse_before_single_use_claim(tmp_path, bad):
    clock = Clock()
    path = tmp_path / 'grant'
    plan, _ = grant(path, clock)
    plan['operation_limits'] = bad
    (path / 'grant.json').write_text(json.dumps(plan))
    with pytest.raises(WireError, match='operation_limits'):
        authority(path, clock, 'unit-a')
    assert not (path / 'started').exists()


@pytest.mark.parametrize('values', [(0, None), (-1, None), (True, None), ('1', None),
    (None, 0), (None, True), (3, None), (None, 2 * COST + 1)])
def test_cli_cannot_raise_grant_limits_or_open_transport(tmp_path, monkeypatch, values):
    clock = Clock()
    path = tmp_path / 'grant'
    _, raw = grant(path, clock)
    args = SimpleNamespace(operation_attempt_limit=values[0], operation_airtime_limit_us=values[1])
    monkeypatch.setattr(example, 'ExclusiveSerial', lambda *a, **k: pytest.fail('opened transport'))
    with pytest.raises(WireError, match='operation_limits'):
        example.run_object(args, raw, ADMISSION, VERSION, None, None, None)
    assert not (path / 'started').exists() and not (path / 'attempts.db').exists()


@pytest.mark.parametrize('kind', ['file', 'bundle'])
@pytest.mark.parametrize('flags', [[], ['--operation-attempt-limit', '1',
                                      '--operation-airtime-limit-us', '110895']])
def test_both_transfer_clis_parse_and_propagate_optional_reductions(monkeypatch, capsys, kind, flags):
    if kind == 'file':
        module, operation = example, 'transfer_chunk'
        specific = ['--manifest', 'manifest.json', '--manifest-sha256', '0' * 64,
                    '--chunks', 'chunks', '--chunk', '0']
    else:
        from tests.test_radio_bundle_example import bundle
        module, operation = bundle, 'transfer'
        specific = ['--pack', 'pack', '--manifest-sha256', '0' * 64,
                    '--trust-root', 'trust.json', '--trust-root-sha256', '0' * 64,
                    '--part', '0', '--state', 'state']
    common = ['--grant-directory', 'grant', '--grant-sha256', '0' * 64,
              '--file-issued-epoch-us', '1000000000', '--file-expires-epoch-us', '1180000000',
              '--admission', 'admission.json', '--admission-sha256', '0' * 64,
              '--firmware-source', 'source.json', '--firmware-source-sha256', '0' * 64,
              '--unit', 'unit-a', '--interface', 'hat']
    seen = []
    def transfer(args):
        seen.append((args.operation_attempt_limit, args.operation_airtime_limit_us))
        return dict(parsed=True)
    monkeypatch.setattr(module, operation, transfer)
    monkeypatch.setattr(sys, 'argv', ['source-example', 'transfer', *specific, *common, *flags])
    module.main()
    assert seen == ([(1, 110895)] if flags else [(None, None)])
    assert json.loads(capsys.readouterr().out) == dict(parsed=True)


class CountedChannel(RadioChannel):
    """Keep boot totals across ARM and separate fresh host journals."""
    def write(self, raw, deadline):
        frame, = Decoder().feed(raw)
        super().write(raw, deadline)
        if frame.type is Message.TRANSMIT:
            self.attempts += 1
            self.airtime += estimated_airtime_us(PROFILE, len(frame.body))

    def close(self):
        pass


@pytest.mark.parametrize('burst', [False, True])
@pytest.mark.parametrize('boundary', ['attempts', 'airtime'])
def test_run_object_caps_real_journal_before_second_write_without_reset_or_refund(
        tmp_path, monkeypatch, burst, boundary):
    clock = Clock()
    channel = CountedChannel(clock)
    channel.peer = channel
    channel.attempts, channel.airtime = 70, 7000000
    monkeypatch.setattr(example, 'ExclusiveSerial', lambda *a, **k: channel)
    monkeypatch.setattr(example, 'HeldHardware', lambda *a, **k: SimpleNamespace(
        current=lambda: True, close=lambda: None))
    monkeypatch.setattr(example, 'host_boot', lambda: BOOT)
    monkeypatch.setattr(example, 'clock', clock)
    window = SimpleNamespace(current=lambda: True, pin_mcu=lambda identity: None,
        deadline_us=clock()[1] + 180000000, check=lambda: None)
    identities = []
    for index in range(2):
        path = tmp_path / f'run-{index}'
        _, raw = grant(path, clock, burst=burst, session=index + 1)
        args = SimpleNamespace(grant_directory=path, unit='unit-a', interface='usb',
            device='/source-model', usb_serial='source-model', chunk=0,
            operation_attempt_limit=1 if boundary == 'attempts' else None,
            operation_airtime_limit_us=COST if boundary == 'airtime' else None)
        def factory(current, worker, journal, cipher):
            identities.append(worker.identity.boot)
            assert current.operation_limits == OperationLimits(**LIMITS['sender'])
            assert journal.max_attempts == (1 if boundary == 'attempts' else 2)
            assert journal.max_airtime_us == (COST if boundary == 'airtime' else 2 * COST)
            assert worker.profile == PROFILE
            transfer = example.ChunkTransfer(current, worker, journal, cipher,
                current.plan['object'], None, clock=clock)
            assert transfer.endpoint.ledger.policy.own_tx_limit_us == 20000000

            def step():
                if burst:
                    first = worker.queue_data(PACKET, logical_id=b'1' * 32)
                    channel.event(first.request, Stage.RF_DONE)
                    worker.drain_physical()
                    second = lambda: worker.queue_data(PACKET, logical_id=b'2' * 32)
                else:
                    first = worker.transmit(PACKET)
                    assert first.outcome == 'RF_DONE'
                    second = lambda: worker.transmit(PACKET)
                before = journal.snapshot()
                writes = sum(f.type in (Message.TRANSMIT, Message.BURST_DATA) for f in channel.writes)
                with pytest.raises(AttemptError, match='^attempt_budget_exhausted$'):
                    second()
                assert journal.snapshot() == before
                assert before['attempts'] == 1 and before['reserved_estimated_airtime_us'] == COST
                assert not before['fenced']
                assert sum(f.type in (Message.TRANSMIT, Message.BURST_DATA) for f in channel.writes) == writes
                return True

            return SimpleNamespace(step=step, close=transfer.close, endpoint=transfer.endpoint)

        result = example.run_object(args, raw, ADMISSION, VERSION, None, window, factory)
        assert result['operation_limits'] == dict(
            attempts=1 if boundary == 'attempts' else 2,
            estimated_airtime_us=COST if boundary == 'airtime' else 2 * COST)
        assert result['attempt_journal']['reserved_estimated_airtime_us'] == COST
        assert channel.attempts == 71 + index
        assert channel.airtime == 7000000 + (index + 1) * estimated_airtime_us(PROFILE, len(PACKET))
        arm = [f for f in channel.writes if f.type is Message.ARM][-1]
        assert struct.unpack('!IQ', arm.body[-12:]) == (480, 20000000)
    assert len(identities) == 2 and identities[0] == identities[1]
