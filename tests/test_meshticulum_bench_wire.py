# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Native bench frame authentication and real SQLite restart/replay controls.

No radio, device service or private SAGE store is opened. These are source and
local persistence results, not hardware or transport-performance measurements.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from meshticulum.contracts.identity import PeerID, SessionKey
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import (
    BenchGrant, FrameKind, HEADER, MAX_BODY, SessionCipher, WireError)

A = PeerID('bench-A', 'a' * 64)
B = PeerID('bench-B', 'b' * 64)
BOOT = 'f7634f52-5e03-44eb-a877-2ab652a208b2'
ALLOW = lambda: True


class Clock:
    def __init__(self):
        self.epoch = 100_000_000
        self.mono = 10_000_000

    def __call__(self):
        return self.epoch, self.mono


def grant(**changes):
    base = BenchGrant(SessionKey(A, B, b's' * 16, b'g' * 16), 'bench_transfer',
                      b'r' * 32, 100_000_000, 160_000_000, 128, 10_000, b'K' * 32)
    return replace(base, **changes)


@pytest.fixture
def setup(tmp_path):
    tmp_path.chmod(0o700)
    clock = Clock()
    opened = []

    def endpoint(name, peer, spec=None, *, create=True, boot=BOOT):
        spec = spec or grant()
        store = SessionStore(tmp_path / name, spec, peer, boot_id=boot, clock=clock, create=create)
        opened.append(store)
        return SessionCipher(spec, peer, store)

    yield tmp_path, clock, endpoint
    for store in opened:
        store.close()


def test_bidirectional_frames_have_distinct_keys_and_independent_counters(setup):
    _, _, endpoint = setup
    a, b = endpoint('a.db', A), endpoint('b.db', B)
    forward = a.seal(b'hello', FrameKind.DATA, authorize=ALLOW)
    reverse = b.seal(b'hello', FrameKind.DATA, authorize=ALLOW)
    assert forward != reverse
    assert b.open(forward, authorize=ALLOW).payload == b'hello'
    assert a.open(reverse, authorize=ALLOW).payload == b'hello'
    assert HEADER.unpack(forward[:HEADER.size])[-2] == 1
    assert HEADER.unpack(reverse[:HEADER.size])[-2] == 1
    with pytest.raises(WireError, match='wrong_direction'):
        a.open(forward, authorize=ALLOW)


def test_every_mutated_byte_is_refused_without_consuming_valid_replay_slot(setup):
    _, _, endpoint = setup
    a, b = endpoint('a.db', A), endpoint('b.db', B)
    packet = a.seal(b'payload with binary\x00\xff', FrameKind.DATA, authorize=ALLOW)
    for offset in range(len(packet)):
        corrupt = bytearray(packet)
        corrupt[offset] ^= 0x80
        with pytest.raises(WireError):
            b.open(bytes(corrupt), authorize=ALLOW)
    assert b.open(packet, authorize=ALLOW).payload == b'payload with binary\x00\xff'
    with pytest.raises(WireError, match='replayed'):
        b.open(packet, authorize=ALLOW)


@pytest.mark.parametrize('change', [
    {'secret': b'X' * 32}, {'route_binding_digest': b'Z' * 32},
    {'max_plaintext': 127}, {'max_frames': 9999}, {'expires_epoch_us': 159_000_000},
    {'session': SessionKey(A, B, b's' * 16, b'x' * 16)},
    {'session': SessionKey(PeerID('another-chain', 'a' * 64), B, b's' * 16, b'g' * 16)},
])
def test_full_identity_policy_and_incarnation_are_authenticated(setup, change):
    _, _, endpoint = setup
    a = endpoint('a.db', A)
    b = endpoint('b.db', B, grant(**change))
    packet = a.seal(b'hello', FrameKind.DATA, authorize=ALLOW)
    with pytest.raises(WireError, match='authentication_failed'):
        b.open(packet, authorize=ALLOW)


def test_nonce_reservation_survives_abandoned_send_and_process_reopen(setup):
    _, _, endpoint = setup
    a = endpoint('a.db', A)
    assert a.store.reserve_transmit(authorize=ALLOW) == 1  # power loss before encryption/write
    a.store.close()
    reopened = endpoint('a.db', A, create=False)
    packet = reopened.seal(b'next attempt', FrameKind.DATA, authorize=ALLOW)
    assert HEADER.unpack(packet[:HEADER.size])[-2] == 2


def test_committed_nonce_survives_abrupt_child_process_exit(setup):
    path, _, endpoint = setup
    a = endpoint('a.db', A)
    a.store.close()
    code = '''
import os,sys,runpy
from pathlib import Path
h=runpy.run_path(sys.argv[1])
s=h['SessionStore'](Path(sys.argv[2]),h['grant'](),h['A'],boot_id=h['BOOT'],clock=h['Clock']())
assert s.reserve_transmit(authorize=lambda: True)==1
os._exit(17)
'''
    child = subprocess.run([sys.executable, '-B', '-c', code, str(Path(__file__).resolve()),
                            str(path / 'a.db')], capture_output=True, text=True, timeout=10)
    assert child.returncode == 17, child.stderr
    a = endpoint('a.db', A, create=False)
    assert HEADER.unpack(a.seal(b'after crash', FrameKind.DATA, authorize=ALLOW)[:HEADER.size])[-2] == 2


def test_malformed_persisted_counter_is_refused(setup):
    path, _, endpoint = setup
    a = endpoint('a.db', A)
    with sqlite3.connect(path / 'a.db') as db:
        db.execute('UPDATE state SET tx=0.5 WHERE only_row=1')
    with pytest.raises(WireError, match='invalid_persisted_state'):
        a.seal(b'no', FrameKind.DATA, authorize=ALLOW)


def test_epoch_storage_bound_is_validated_before_a_session_can_exist():
    with pytest.raises(WireError, match='invalid_expiry'):
        grant(expires_epoch_us=2**63)


def test_replay_reordering_and_reopen_are_durable(setup):
    _, _, endpoint = setup
    a, b = endpoint('a.db', A), endpoint('b.db', B)
    packets = [a.seal(bytes([i]), FrameKind.DATA, authorize=ALLOW) for i in range(3)]
    assert b.open(packets[2], authorize=ALLOW).counter == 3
    assert b.open(packets[0], authorize=ALLOW).counter == 1
    b.store.close()
    b = endpoint('b.db', B, create=False)
    with pytest.raises(WireError, match='replayed'):
        b.open(packets[2], authorize=ALLOW)
    assert b.open(packets[1], authorize=ALLOW).counter == 2


def test_counter_reservations_are_atomic_across_independent_connections(setup):
    _, _, endpoint = setup
    clients = [endpoint('a.db', A), endpoint('a.db', A, create=False)]
    def emit(i):
        packet = clients[i % 2].seal(b'bounded', FrameKind.DATA, authorize=ALLOW)
        return HEADER.unpack(packet[:HEADER.size])[-2]
    with ThreadPoolExecutor(max_workers=4) as pool:
        counters = list(pool.map(emit, range(40)))
    assert sorted(counters) == list(range(1, 41))


def test_unauthorized_send_does_not_consume_counter_and_receive_can_be_retried(setup):
    _, _, endpoint = setup
    a, b = endpoint('a.db', A), endpoint('b.db', B)
    with pytest.raises(WireError, match='authority_refused'):
        a.seal(b'stop', FrameKind.DATA, authorize=lambda: False)
    packet = a.seal(b'allowed', FrameKind.DATA, authorize=ALLOW)
    assert HEADER.unpack(packet[:HEADER.size])[-2] == 1
    with pytest.raises(WireError, match='authority_refused'):
        b.open(packet, authorize=lambda: False)
    assert b.open(packet, authorize=ALLOW).payload == b'allowed'


def test_revocation_between_decrypt_and_admission_prevents_delivery(setup):
    _, _, endpoint = setup
    a, b = endpoint('a.db', A), endpoint('b.db', B)
    packet = a.seal(b'allowed', FrameKind.DATA, authorize=ALLOW)
    checks = iter([True, False])
    with pytest.raises(WireError, match='authority_refused'):
        b.open(packet, authorize=lambda: next(checks))
    assert b.open(packet, authorize=ALLOW).payload == b'allowed'


def test_retired_session_and_reboot_cannot_be_reopened(setup):
    _, _, endpoint = setup
    a = endpoint('a.db', A)
    with pytest.raises(WireError, match='boot_changed'):
        endpoint('a.db', A, create=False, boot=str(uuid.uuid4()))
    a.store.retire()
    with pytest.raises(WireError, match='session_retired'):
        a.seal(b'no', FrameKind.DATA, authorize=ALLOW)
    with pytest.raises(WireError, match='session_retired'):
        endpoint('a.db', A, create=False)


@pytest.mark.parametrize('field', ['epoch', 'mono'])
def test_expiry_is_enforced_by_either_original_clock_bound(setup, field):
    _, clock, endpoint = setup
    a = endpoint('a.db', A)
    setattr(clock, field, getattr(clock, field) + 60_000_000)
    with pytest.raises(WireError, match='session_expired'):
        a.seal(b'too late', FrameKind.DATA, authorize=ALLOW)


def test_clock_rollback_cannot_extend_session(setup):
    _, clock, endpoint = setup
    a = endpoint('a.db', A)
    clock.epoch += 10
    clock.mono += 10
    a.seal(b'one', FrameKind.DATA, authorize=ALLOW)
    clock.epoch -= 1
    with pytest.raises(WireError, match='clock_moved_backwards'):
        a.seal(b'two', FrameKind.DATA, authorize=ALLOW)


def test_frame_budget_is_durable(setup):
    _, _, endpoint = setup
    g = grant(max_frames=1)
    a = endpoint('a.db', A, g)
    a.seal(b'one', FrameKind.DATA, authorize=ALLOW)
    a.store.close()
    a = endpoint('a.db', A, g, create=False)
    with pytest.raises(WireError, match='session_frame_limit'):
        a.seal(b'two', FrameKind.DATA, authorize=ALLOW)


def test_window_is_bounded_and_reordered_edge_is_accepted_once(setup):
    _, _, endpoint = setup
    a, b = endpoint('a.db', A), endpoint('b.db', B)
    packets = [a.seal(b'x', FrameKind.DATA, authorize=ALLOW) for _ in range(257)]
    assert b.open(packets[256], authorize=ALLOW).counter == 257
    with pytest.raises(WireError, match='too_old'):
        b.open(packets[0], authorize=ALLOW)
    assert b.open(packets[1], authorize=ALLOW).counter == 2
    with pytest.raises(WireError, match='replayed'):
        b.open(packets[1], authorize=ALLOW)


def test_missing_or_existing_state_never_implicitly_resets(setup):
    _, _, endpoint = setup
    with pytest.raises(FileNotFoundError):
        endpoint('missing.db', A, create=False)
    a = endpoint('a.db', A)
    a.seal(b'one', FrameKind.DATA, authorize=ALLOW)
    with pytest.raises(FileExistsError):
        endpoint('a.db', A, create=True)
    with pytest.raises(WireError, match='state_binding_mismatch'):
        endpoint('a.db', A, grant(max_frames=9999), create=False)
    assert HEADER.unpack(a.seal(b'two', FrameKind.DATA, authorize=ALLOW)[:HEADER.size])[-2] == 2


def test_binary_budget_secrets_and_persistence_scope(setup):
    path, _, endpoint = setup
    g = grant(max_plaintext=MAX_BODY)
    a, b = endpoint('a.db', A, g), endpoint('b.db', B, g)
    body = b'x' * MAX_BODY
    packet = a.seal(body, FrameKind.DATA, authorize=ALLOW)
    assert len(packet) == 255
    assert b.open(packet, authorize=ALLOW).payload == body
    with pytest.raises(WireError, match='payload_size'):
        a.seal(body + b'x', FrameKind.DATA, authorize=ALLOW)
    assert repr(g.secret) not in repr(g)
    assert g.secret not in (path / 'a.db').read_bytes()
    assert body not in (path / 'b.db').read_bytes()


def test_state_permissions_and_symlink_are_refused(setup):
    path, _, endpoint = setup
    endpoint('a.db', A)
    (path / 'alias.db').symlink_to(path / 'a.db')
    with pytest.raises(WireError, match='unsafe_state_file'):
        endpoint('alias.db', A, create=False)
    (path / 'a.db').chmod(0o644)
    with pytest.raises(WireError, match='unsafe_state_file'):
        endpoint('a.db', A, create=False)
