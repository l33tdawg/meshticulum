"""Public example composition with encrypted, journaled model radio frames.

No serial/SPI port is opened. Model RF_DONE and ownership are source fixtures,
not native authority, driver evidence or a physical performance result.
"""
from collections import deque
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from meshticulum.bench.admission import Gfsk250Admission
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.gfsk250_profile import Gfsk250Profile
from meshticulum.bench.objects import VerifiedChunks
from meshticulum.bench.serial_protocol import RawReceive
from meshticulum.bench.serial_worker import AttemptResult
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import SessionCipher, WireError

spec = importlib.util.spec_from_file_location('radio_file_example',
    Path(__file__).resolve().parents[1] / 'examples/radio_file.py')
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)

BOOT = '21f9c178-6090-40d9-9359-ecf1c7b950b1'
PROFILE = Gfsk250Profile(921375000, 0, 180000, 256, 60000000)
ADMISSION = Gfsk250Admission('MY', (921375000,), (0,), 1000, 180000, 256, 60000000)
PEERS = [dict(chain_id='public-example-source-model', agent_id=x * 64) for x in ('a', 'b')]


class Clock:
    def __init__(self):
        self.epoch, self.mono = 1000000000, 100000000

    def __call__(self):
        return self.epoch, self.mono

    def advance(self, us):
        self.epoch += us
        self.mono += us


class ModelWorker:
    def __init__(self, journal, clock, cumulative):
        self.journal, self.clock, self.cumulative = journal, clock, cumulative
        self.incoming, self.sent = deque(), []
        self.fenced, self.owned, self.peer = False, True, None
        self.corrupt_once = False

    def transmit(self, packet):
        assert self.cumulative[0] < 256
        request = self.journal.reserve(packet, 12000)
        self.cumulative[0] += 1
        self.journal.settle(request, packet, 'RF_DONE', 'source_radio_model')
        self.sent.append(packet)
        if self.corrupt_once:
            self.corrupt_once = False
            packet = packet[:-1] + bytes([packet[-1] ^ 1])
        self.peer.incoming.append(packet)
        return AttemptResult(request, 'RF_DONE', 12000, None)

    def receive(self, timeout=0):
        if self.incoming:
            return self.clock()[1] / 1000000, RawReceive(-70., 3., self.incoming.popleft())


def pair(path, descriptor, clock, issued, expires, receiver_counter):
    opened, transfers, workers = [], [], []
    for i, unit in enumerate(('sender', 'receiver')):
        root = path / unit
        root.mkdir(mode=0o700, parents=True)
        plan = dict(schema='meshticulum.bench-run.v1', unit=unit, host_boot_id=BOOT,
            local_peer=PEERS[i], initiator=PEERS[0], responder=PEERS[1],
            session_id=f'{descriptor["chunk_index"] + 1:032x}', generation='22' * 16,
            issued_epoch_us=issued, expires_epoch_us=expires,
            profile=PROFILE.to_dict(), radio_modem='gfsk250', sender_unit='sender',
            country='MY', antenna_confirmed=True, max_frames=512, object=descriptor)
        for name, data in (('grant.json', json.dumps(plan).encode()),
                           ('secret.bin', b'K' * 32), ('active', b'active\n')):
            fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as output:
                output.write(data)
        journal = AttemptJournal(root / 'attempts.db', binding=bytes(32),
            max_attempts=256, max_airtime_us=60000000, create=True)
        # HAT sends under its distinct per-run journal; MCU ACK counter persists.
        worker = ModelWorker(journal, clock, [0] if i == 0 else receiver_counter)
        authority = RunAuthority(root, expected_unit=unit, host_boot_id=BOOT, clock=clock,
            owner_probe=lambda w=worker: w.owned, radio_admission=ADMISSION)
        store = SessionStore(root / 'wire.db', authority.grant, authority.local_peer,
                             boot_id=BOOT, clock=clock, create=True)
        transfer = example.ChunkTransfer(authority, worker, journal,
            SessionCipher(authority.grant, authority.local_peer, store), descriptor,
            VerifiedChunks(path.parent / 'chunks'), clock=clock)
        transfers.append(transfer)
        workers.append(worker)
        opened.append((transfer, store, authority, journal))
    workers[0].peer, workers[1].peer = workers[1], workers[0]
    return opened, transfers, workers


def close_pair(opened):
    for transfer, store, authority, journal in opened:
        transfer.close()
        store.retire()
        store.close()
        authority.close()
        journal.close()


def finish(transfers, clock):
    sender, receiver = transfers
    sender_done, receiver_done = False, False
    for _ in range(10000):
        if not sender_done:
            sender_done = sender.step()
        if not receiver_done:
            receiver_done = receiver.step()
        if sender_done and receiver_done:
            return
        clock.advance(1000000 if sender_done and receiver.completed_at is not None else 20000)
    raise AssertionError('source model did not finish inside its finite step bound')


def test_public_100k_chunk_composition_retains_mcu_ack_cap_and_original_window(tmp_path):
    data = bytes(range(256)) * 390 + bytes(range(160))
    assert len(data) == 100000
    file = example.manifest(data)
    assert len(file['chunks']) == 7
    clock = Clock()
    issued, expires = clock()[0], clock()[0] + 240000000
    receiver_counter = [0]
    reserved = []
    for index, descriptor in enumerate(file['chunks']):
        opened, transfers, workers = pair(tmp_path / str(index), descriptor, clock,
                                         issued, expires, receiver_counter)
        try:
            transfers[0].endpoint.send(data[index * 16384:(index + 1) * 16384])
            if index == 0:
                workers[0].corrupt_once = True
            finish(transfers, clock)
            assert transfers[1].clock()[1] - transfers[1].completed_at >= example.ACK_GRACE_US
            assert all(data[:100] not in packet for worker in workers for packet in worker.sent)
            assert transfers[0].endpoint.snapshot()['acked_frames'] > 0
            reserved.extend(worker.journal.snapshot()['reserved_estimated_airtime_us'] for worker in workers)
        finally:
            close_pair(opened)
    assert 0 < receiver_counter[0] < 256
    assert clock()[0] < expires
    assert all(cost > 0 for cost in reserved)
    assert VerifiedChunks(tmp_path / 'chunks').assemble(file['chunks']) == data


@pytest.mark.parametrize('fault', ['owner', 'clock', 'revocation', 'deadline'])
def test_real_authority_refusal_stops_example_without_refunding(tmp_path, fault):
    clock = Clock()
    descriptor = example.manifest(b'bounded source payload')['chunks'][0]
    opened, transfers, workers = pair(tmp_path / 'run', descriptor, clock,
                                     clock()[0], clock()[0] + 240000000, [0])
    try:
        transfers[0].endpoint.send(b'bounded source payload')
        transfers[1].step()
        clock.advance(100000)
        transfers[0].step()
        before = workers[0].journal.snapshot()
        assert before['attempts'] > 0
        if fault == 'owner':
            workers[0].owned = False
        elif fault == 'clock':
            clock.mono -= 1
        elif fault == 'revocation':
            (tmp_path / 'run/sender/active').write_bytes(b'retired\n')
        else:
            clock.advance(240000001)
        with pytest.raises((WireError, ValueError)):
            transfers[0].step()
        assert workers[0].journal.snapshot() == before
    finally:
        close_pair(opened)


def test_receiver_grace_cannot_renew_expired_authority(tmp_path):
    clock = Clock()
    descriptor = example.manifest(b'one')['chunks'][0]
    opened, transfers, workers = pair(tmp_path / 'run', descriptor, clock,
                                     clock()[0], clock()[0] + 240000000, [0])
    try:
        transfers[0].endpoint.send(b'one')
        for _ in range(200):
            transfers[0].step()
            transfers[1].step()
            if transfers[1].completed_at is not None:
                break
            clock.advance(20000)
        first = transfers[1].completed_at
        assert first is not None
        clock.advance(240000001)
        with pytest.raises(WireError):
            transfers[1].step()
        assert transfers[1].completed_at == first
    finally:
        close_pair(opened)


def test_manifest_pin_and_whole_object_verification(tmp_path):
    data = b'x' * 100000
    file = example.manifest(data)
    path = tmp_path / 'manifest.json'
    raw = json.dumps(file).encode()
    path.write_bytes(raw)
    assert example.load_manifest(path, example.sha(raw)) == file
    with pytest.raises(WireError, match='hash_mismatch'):
        example.load_manifest(path, '0' * 64)
    cache = VerifiedChunks(tmp_path / 'chunks')
    for index, descriptor in enumerate(file['chunks']):
        cache.commit(descriptor, data[index * 16384:(index + 1) * 16384])
    (tmp_path / 'chunks' / file['sha256'] / '6.bin').write_bytes(b'corrupt')
    with pytest.raises(WireError, match='stored_chunk_corrupt'):
        cache.assemble(file['chunks'])


def test_output_is_create_only_and_syncs_verified_bytes(tmp_path):
    output = tmp_path / 'file.bin'
    example.publish(output, b'verified')
    assert output.read_bytes() == b'verified'
    with pytest.raises(FileExistsError):
        example.publish(output, b'replacement')
    assert output.read_bytes() == b'verified'


def test_file_window_cannot_rotate_between_chunk_commands(tmp_path):
    file = example.manifest(b'x' * 100000)
    cache = VerifiedChunks(tmp_path / 'cache')
    clock = Clock()
    kw = dict(time_source=clock, boot_reader=lambda: BOOT)
    issued, expires = clock()[0], clock()[0] + 240000000
    example.bind_file_window(cache, file, issued, expires, **kw)
    example.bind_file_window(cache, file, issued, expires, **kw)
    clock.advance(1000000)
    with pytest.raises(WireError, match='original_file_window_changed'):
        example.bind_file_window(cache, file, issued + 1000000, expires + 1000000, **kw)
    assert len(list(cache.path.iterdir())) == 1
    with pytest.raises(WireError, match='file_window_bound'):
        example.bind_file_window(cache, file, issued, expires + 1, **kw)


def source_metadata(tmp_path):
    app = {name: str(i) * 64 for i, name in enumerate(
        ('main.cpp', 'protocol.hpp', 'gfsk_profile.hpp', 'gfsk250_profile.hpp'))}
    value = dict(schema='meshticulum.raw-firmware-prepared.v1', application=app,
        identity='mtb1-' + example.sha(json.dumps(app, sort_keys=True).encode())[:10])
    path = tmp_path / 'SOURCE.json'
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    return path, example.sha(raw), value


def test_expected_firmware_uses_exact_four_file_source_and_supplied_pin(tmp_path):
    path, pin, source = source_metadata(tmp_path)
    assert example.firmware_identity(path, pin) == source['identity']
    with pytest.raises(WireError, match='hash_mismatch'):
        example.firmware_identity(path, '0' * 64)
    source['application'].pop('gfsk250_profile.hpp')
    raw = json.dumps(source).encode()
    path.write_bytes(raw)
    with pytest.raises(WireError, match='firmware_source_mismatch'):
        example.firmware_identity(path, example.sha(raw))


def test_postboot_inspection_sends_only_readonly_commands_and_closes(tmp_path, monkeypatch):
    from meshticulum.bench.gfsk250_profile import CAPABILITY
    from meshticulum.bench.serial_protocol import Decoder, Frame, Message
    import struct
    path, pin, source = source_metadata(tmp_path)
    commands, closed = [], []

    class Channel:
        monotonic = staticmethod(lambda: 10.)

        def __init__(self, *args, **kwargs):
            self.pending = b''

        def write(self, data, deadline):
            frame, = Decoder().feed(data)
            commands.append(frame.type)
            boot = bytes.fromhex('0000545662f61b440000000000000012')
            if frame.type is Message.HELLO:
                kind = Message.HELLO_REPLY
                body = struct.pack('!BBH', 1, 1, 255) + source['identity'].encode() + b'\0'
            elif frame.type is Message.GFSK250_CAPABILITIES:
                kind, body = Message.GFSK250_CAPABILITIES_REPLY, CAPABILITY
            else:
                assert frame.type is Message.STATUS
                kind, body = Message.STATUS_REPLY, bytes(49)
            self.pending = Frame(kind, boot, frame.request, body).encode()

        def read(self, timeout):
            result, self.pending = self.pending, b''
            return result

        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append(True)

    monkeypatch.setattr(example, 'ExclusiveSerial', Channel)
    result = example.inspect_usb(SimpleNamespace(firmware_source=path,
        firmware_source_sha256=pin, device='/source-only-model', usb_serial='source-model'))
    assert commands == [Message.HELLO, Message.GFSK250_CAPABILITIES, Message.STATUS]
    assert closed == [True]
    assert result['firmware'] == source['identity']
    assert result['armed'] is result['transmitting'] is result['fenced'] is False


def test_unknown_attempt_remains_charged_and_fences_example(tmp_path):
    clock = Clock()
    descriptor = example.manifest(b'one')['chunks'][0]
    opened, transfers, workers = pair(tmp_path / 'run', descriptor, clock,
                                     clock()[0], clock()[0] + 240000000, [0])
    try:
        journal = workers[0].journal
        request = journal.reserve(b'uncertain ciphertext', 12000)
        journal.settle(request, b'uncertain ciphertext', 'UNKNOWN', 'source_fault')
        before = journal.snapshot()
        with pytest.raises(WireError, match='worker_run_fenced'):
            transfers[0].step()
        assert journal.snapshot() == before
        assert before['fenced'] and before['reserved_estimated_airtime_us'] == 12000
    finally:
        close_pair(opened)
    assert (tmp_path / 'run/sender/attempts.db').exists()


def test_partial_arm_failure_contains_then_retires_and_closes(tmp_path, monkeypatch):
    from meshticulum.bench.serial_protocol import FirmwareIdentity
    import sqlite3
    clock = Clock()
    file = example.manifest(b'one')
    opened, _, _ = pair(tmp_path / 'original', file['chunks'][0], clock,
                         clock()[0], clock()[0] + 240000000, [0])
    close_pair(opened)
    original = tmp_path / 'original/receiver'
    run = tmp_path / 'fresh'
    run.mkdir(mode=0o700)
    for name in ('grant.json', 'secret.bin', 'active'):
        path = run / name
        path.write_bytes((original / name).read_bytes())
        path.chmod(0o600)
    raw = json.dumps(file).encode()
    manifest_path = tmp_path / 'manifest.json'
    manifest_path.write_bytes(raw)
    admission_path = tmp_path / 'admission.json'
    admission_raw = json.dumps(dict(country='MY', frequencies_hz=[921375000], powers_dbm=[0],
        min_lease_ms=1000, max_lease_ms=180000, max_boot_attempts=256,
        max_boot_estimated_airtime_us=60000000)).encode()
    admission_path.write_bytes(admission_raw)
    admission_path.chmod(0o600)
    source_path, source_pin, source = source_metadata(tmp_path)
    calls = []

    class Channel:
        def __init__(self, *args, **kwargs):
            calls.append('open')

        def close(self):
            calls.append('close')

    class Owner:
        def __init__(self, *args, **kwargs):
            pass

        def current(self):
            return True  # Explicit model ownership; never the production callback.

        def close(self):
            pass

    class Worker:
        def __init__(self, channel):
            pass

        def discover(self, **kwargs):
            return FirmwareIdentity(bytes.fromhex('0000545662f61b440000000000000012'),
                                    1, source['identity'])

        def arm(self, **kwargs):
            calls.append('partial-arm')
            assert kwargs['authorize']()
            raise WireError('source_partial_arm_failure')

        def disarm(self):
            calls.append('disarm')

    monkeypatch.setattr(example, 'ExclusiveSerial', Channel)
    monkeypatch.setattr(example, 'HeldHardware', Owner)
    monkeypatch.setattr(example, 'Gfsk250SerialWorker', Worker)
    monkeypatch.setattr(example, 'host_boot', lambda: BOOT)
    monkeypatch.setattr(example, 'clock', clock)
    args = SimpleNamespace(manifest=manifest_path, manifest_sha256=example.sha(raw), chunk=0,
        grant_directory=run, grant_sha256=example.sha((run / 'grant.json').read_bytes()),
        file_issued_epoch_us=1000000000, file_expires_epoch_us=1240000000,
        admission=admission_path, admission_sha256=example.sha(admission_raw), input=None,
        unit='receiver', firmware_source=source_path, firmware_source_sha256=source_pin,
        chunks=tmp_path / 'new-cache', interface='usb', device='/source-model', usb_serial='model')
    with pytest.raises(BaseExceptionGroup, match='cleanup failed') as failure:
        example.transfer_chunk(args)
    assert str(failure.value.exceptions[0]) == 'source_partial_arm_failure'
    assert calls == ['open', 'partial-arm', 'disarm', 'close']
    assert (run / 'started').exists() and (run / 'attempts.db').exists()
    with sqlite3.connect(run / 'wire.db') as db:
        assert db.execute('SELECT retired FROM state').fetchone() == (1,)
