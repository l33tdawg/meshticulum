"""Same-process composition with real durable state and modeled hardware only."""
from dataclasses import asdict
import json
import sqlite3
from types import SimpleNamespace

import pytest

from meshticulum.bench.serial_protocol import FirmwareIdentity
from meshticulum.bench.wire import FrameKind, WireError
from meshticulum.resources import byte_bundle, knowledge_pack as kp
from tests.test_radio_bundle_example import bundle, base, published
from tests.test_radio_file_example import Clock, BOOT, PROFILE, ADMISSION, PEERS


def options(tmp_path, published, monkeypatch, *, unit='sender', order=(4, 0)):
    _, raw, signed, trust, digest, manifest, chunks = published
    pack = tmp_path / 'pack'
    pack.mkdir()
    kp.write_pack(pack, manifest, chunks, json.loads(signed))
    trust_path = tmp_path / 'trust.json'
    trust_path.write_bytes(kp.canonical_json(trust))
    admission = tmp_path / 'admission.json'
    admission.write_text(json.dumps(asdict(ADMISSION)))
    admission.chmod(0o600)
    clock = Clock()
    monkeypatch.setattr(base, 'clock', clock)
    monkeypatch.setattr(base, 'host_boot', lambda: BOOT)
    monkeypatch.setattr(base, 'firmware_identity', lambda *args: 'mtb1-0123456789')
    expected = byte_bundle.descriptor(manifest)
    parts = []
    for index in order:
        entry = manifest['chunks'][index]
        payload = bundle.response_for(digest, entry, kp.chunk_bytes(chunks[entry['sha256']]))
        grant = tmp_path / ('grant-' + str(index))
        grant.mkdir(mode=0o700)
        plan = dict(schema='meshticulum.service-run.v1', unit=unit, host_boot_id=BOOT,
            local_peer=PEERS[unit == 'receiver'], initiator=PEERS[0], responder=PEERS[1],
            session_id=f'{index + 1:032x}', generation='33' * 16,
            issued_epoch_us=clock()[0], expires_epoch_us=clock()[0] + 240_000_000,
            profile=PROFILE.to_dict(), radio_modem='gfsk250', sender_unit='sender',
            country='MY', antenna_confirmed=True, max_frames=512,
            object=dict(object_id=expected['sha256'], chunk_index=index,
                        bytes=len(payload), sha256=base.sha(payload)),
            service=bundle.service_for(entry, payload, clock()[0] + 240_000_000))
        for name, value in (('grant.json', kp.canonical_json(plan)),
                            ('secret.bin', b'K' * 32), ('active', b'active\n')):
            path = grant / name
            path.write_bytes(value)
            path.chmod(0o600)
        parts.append(SimpleNamespace(part=index, pack=pack, manifest_sha256=digest,
            trust_root=trust_path, trust_root_sha256=base.sha(trust_path.read_bytes()),
            state=tmp_path / 'state', file_issued_epoch_us=clock()[0],
            file_expires_epoch_us=clock()[0] + 240_000_000,
            grant_directory=grant, grant_sha256=base.sha((grant / 'grant.json').read_bytes()),
            admission=admission, admission_sha256=base.sha(admission.read_bytes()),
            firmware_source=None, firmware_source_sha256=None, unit=unit, interface='usb',
            device='/source-model', usb_serial='model', confirm_receipt=True, profile_host=False))
    return parts, clock


def model_hardware(monkeypatch, published, *, fault=None):
    """Keep RunAuthority, WAL/FULL journals, wire stores and ciphers real."""
    _, _, _, _, _, _, chunks = published
    events, sessions, workers = [], [], []

    class Channel:
        def __init__(self, *args, **kwargs):
            self.number = len(workers)
            events.append(('open', self.number))

        def close(self):
            events.append(('close', self.number))

    class Owner:
        def __init__(self, *args, **kwargs):
            pass

        def current(self):
            return True

        def close(self):
            pass

    class Worker:
        def __init__(self, channel, **kwargs):
            self.number = len(workers)
            workers.append(self)

        def discover(self, **kwargs):
            return FirmwareIdentity(bytes.fromhex('0000545662f61b440000000000000012'),
                                    1, 'mtb1-0123456789')

        def arm(self, **kwargs):
            self.journal = kwargs['journal']
            assert kwargs['authorize']()
            events.append(('arm', self.number))
            if self.number == 0 and fault == 'partial_arm':
                raise WireError('modeled_partial_arm')

        def disarm(self):
            events.append(('disarm', self.number))

    class Session:
        def __init__(self, authority, worker, journal, cipher, entry, digest, index,
                     receiver, window, **kwargs):
            self.authority, self.worker, self.journal, self.cipher = authority, worker, journal, cipher
            self.entry, self.index, self.receiver, self.window = entry, index, receiver, window
            self.endpoint = SimpleNamespace(receipt_confirmation_enabled=False)
            self.number = len(sessions)
            sessions.append(self)

        def send(self, payload):
            # One real encrypted reservation proves fresh wire state and debt.
            packet = self.cipher.seal(b'modeled DATA', FrameKind.DATA,
                                     authorize=self.authority.application_live)
            request = self.journal.reserve(packet, 12000)
            outcome = 'UNKNOWN' if self.number == 0 and fault == 'unknown' else 'RF_DONE'
            self.journal.settle(request, packet, outcome, 'source-only model')

        def step(self):
            if self.number == 0 and fault in ('transfer', 'unknown'):
                raise WireError('modeled_' + fault)
            if self.receiver is not None:
                self.receiver.accept(self.index, kp.chunk_bytes(chunks[self.entry['sha256']]))
            return True

        def close(self):
            events.append(('session_close', self.number))
            if self.number == 0 and fault == 'cleanup':
                raise WireError('modeled_cleanup')

    monkeypatch.setattr(base, 'ExclusiveSerial', Channel)
    monkeypatch.setattr(base, 'HeldHardware', Owner)
    monkeypatch.setattr(base, 'Gfsk250SerialWorker', Worker)
    monkeypatch.setattr(bundle, 'BundleTransfer', Session)
    return events, sessions, workers


def test_batch_fresh_states_close_before_next_arm_and_keep_original_window(
        tmp_path, published, monkeypatch):
    parts, clock = options(tmp_path, published, monkeypatch)
    events, sessions, workers = model_hardware(monkeypatch, published)
    batch = bundle.transfer_batch(parts)
    parts[1].part = 1  # The batch copied caller options before consuming grants.
    results = list(batch)
    assert [row['part'] for row in results] == [4, 0]
    assert all('chunk' not in vars(args) for args in parts)
    assert events == [('open', 0), ('arm', 0), ('session_close', 0), ('disarm', 0),
                      ('arm', 1), ('session_close', 1), ('disarm', 1), ('close', 0)]
    for name in ('authority', 'worker', 'journal', 'cipher'):
        assert getattr(sessions[0], name) is not getattr(sessions[1], name)
    assert sessions[0].cipher.store is not sessions[1].cipher.store
    assert sessions[0].cipher.grant.digest != sessions[1].cipher.grant.digest
    assert sessions[0].window.raw == sessions[1].window.raw
    assert sessions[0].window.deadline_us == clock()[1] + 240_000_000
    for args, row in zip(parts, results):
        assert row['result']['attempt_journal']['attempts'] == 1
        assert row['result']['attempt_journal']['reserved_estimated_airtime_us'] == 12000
        with sqlite3.connect(args.grant_directory / 'wire.db') as db:
            assert db.execute('SELECT tx,retired FROM state').fetchone() == (1, 1)
    assert (parts[0].state / 'sent-4.json').exists()
    assert (parts[0].state / 'sent-0.json').exists()


@pytest.mark.parametrize('fault', ['partial_arm', 'transfer', 'cleanup', 'unknown'])
def test_batch_failure_stops_next_grant_and_preserves_cleanup_and_debt(
        tmp_path, published, monkeypatch, fault):
    parts, _ = options(tmp_path, published, monkeypatch)
    events, _, _ = model_hardware(monkeypatch, published, fault=fault)
    with pytest.raises(BaseExceptionGroup) as caught:
        list(bundle.transfer_batch(parts))
    assert any('modeled_' in str(error) for error in caught.value.exceptions)
    assert events[-2:] == [('disarm', 0), ('close', 0)]
    assert not (parts[1].grant_directory / 'started').exists()
    assert (parts[0].grant_directory / 'started').exists()
    with sqlite3.connect(parts[0].grant_directory / 'wire.db') as db:
        assert db.execute('SELECT retired FROM state').fetchone() == (1,)
    with sqlite3.connect(parts[0].grant_directory / 'attempts.db') as db:
        assert db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0] == (0 if fault == 'partial_arm' else 1)
        if fault == 'unknown':
            assert db.execute('SELECT fenced FROM owner').fetchone() == (1,)
    assert not (parts[0].state / 'sent-4.json').exists()


@pytest.mark.parametrize('change', ['expiry', 'grant', 'admission', 'receipt', 'boot'])
def test_batch_revalidates_before_next_hardware_open(tmp_path, published, monkeypatch, change):
    parts, clock = options(tmp_path, published, monkeypatch)
    events, _, _ = model_hardware(monkeypatch, published)
    batch = bundle.transfer_batch(parts)
    assert next(batch)['part'] == 4
    if change == 'expiry':
        clock.advance(240_000_000)
    elif change == 'grant':
        (parts[1].grant_directory / 'grant.json').write_bytes(b'{}')
    elif change == 'admission':
        parts[1].admission.write_bytes(b'{}')
    elif change == 'receipt':
        (parts[0].state / 'sent-4.json').write_bytes(b'{}')
    else:
        monkeypatch.setattr(base, 'host_boot', lambda: 'different-boot')
    with pytest.raises((ValueError, OSError)):
        next(batch)
    assert [event for event in events if event[0] == 'open'] == [('open', 0)]
    assert not (parts[1].grant_directory / 'started').exists()


def test_expired_first_part_is_refused_before_lazy_hardware_open(tmp_path, published, monkeypatch):
    parts, clock = options(tmp_path, published, monkeypatch)
    events, _, _ = model_hardware(monkeypatch, published)
    batch = bundle.transfer_batch(parts)
    clock.advance(240_000_000)
    with pytest.raises(WireError):
        next(batch)
    assert events == []


def test_publication_failure_poisons_explicit_held_owner(tmp_path, published, monkeypatch):
    parts, _ = options(tmp_path, published, monkeypatch)
    events, _, _ = model_hardware(monkeypatch, published)
    publish = base.publish

    def fail_receipt(path, raw):
        if path.name == 'sent-4.json':
            raise OSError('modeled_receipt_publication')
        return publish(path, raw)

    monkeypatch.setattr(base, 'publish', fail_receipt)
    with base.FileHardware(parts[0]) as hardware:
        with pytest.raises(OSError, match='modeled_receipt_publication'):
            bundle.transfer(parts[0], hardware=hardware)
        assert hardware.poisoned and not hardware.active
        with pytest.raises(WireError, match='file_hardware_not_available'):
            bundle.transfer(parts[1], hardware=hardware)
    assert [event for event in events if event[0] == 'arm'] == [('arm', 0)]
    assert events[-1] == ('close', 0)
    assert not (parts[1].grant_directory / 'started').exists()


def test_outer_poison_failure_preserves_original_exception(tmp_path, published, monkeypatch):
    parts, _ = options(tmp_path, published, monkeypatch)
    model_hardware(monkeypatch, published)
    original = KeyboardInterrupt('modeled cancellation')
    cleanup = RuntimeError('modeled ownership failure')

    def fail_transfer(*args, **kwargs):
        raise original

    def fail_poison(self):
        raise cleanup

    monkeypatch.setattr(bundle, '_transfer', fail_transfer)
    monkeypatch.setattr(base.FileHardware, 'poison', fail_poison)
    with base.FileHardware(parts[0]) as hardware:
        with pytest.raises(BaseExceptionGroup) as caught:
            bundle.transfer(parts[0], hardware=hardware)
        assert caught.value.exceptions == (original, cleanup)


def test_public_parser_exposes_transfer_options_without_hardware():
    parsed = bundle.parser().parse_args(['describe', '--pack', '/public-model',
        '--manifest-sha256', 'a' * 64, '--trust-root', '/trust-model',
        '--trust-root-sha256', 'b' * 64, '--part', '4', '--file-expires-epoch-us', '123'])
    assert parsed.command == 'describe' and parsed.part == 4


def test_unordered_receiver_finishes_only_after_all_verified_parts(tmp_path, published, monkeypatch):
    parts, _ = options(tmp_path, published, monkeypatch, unit='receiver', order=(4, 2, 0, 3, 1))
    model_hardware(monkeypatch, published)
    batch = bundle.transfer_batch(parts)
    for expected in (4, 2, 0, 3):
        row = next(batch)
        assert row['part'] == expected and row['result']['whole_file_verified'] is False
        assert not (parts[0].state / 'receiver/bundle.bin').exists()
    row = next(batch)
    assert row['part'] == 1 and row['result']['whole_file_verified'] is True
    assert (parts[0].state / 'receiver/bundle.bin').read_bytes() == published[0]
    with pytest.raises(StopIteration):
        next(batch)


def test_batch_close_between_results_releases_held_hardware(tmp_path, published, monkeypatch):
    parts, _ = options(tmp_path, published, monkeypatch)
    events, _, _ = model_hardware(monkeypatch, published)
    batch = bundle.transfer_batch(parts)
    next(batch)
    assert not any(event[0] == 'close' for event in events)
    batch.close()
    assert events[-1] == ('close', 0)
    assert not (parts[1].grant_directory / 'started').exists()


@pytest.mark.parametrize('change', ['duplicate_part', 'boolean_part', 'reused_grant', 'file_window', 'manifest'])
def test_batch_rejects_changed_binding_before_consumption(tmp_path, published, monkeypatch, change):
    parts, _ = options(tmp_path, published, monkeypatch)
    if change == 'duplicate_part':
        parts[1].part = parts[0].part
    elif change == 'boolean_part':
        parts[1].part = True
    elif change == 'reused_grant':
        parts[1].grant_directory = parts[0].grant_directory
    elif change == 'file_window':
        parts[1].file_expires_epoch_us += 1
    else:
        parts[1].manifest_sha256 = '0' * 64
    with pytest.raises(WireError):
        bundle.transfer_batch(parts)
    assert all(not (args.grant_directory / 'started').exists() for args in parts)


@pytest.mark.parametrize('parts', [[], (), iter(()), [None] * (bundle.MAX_BATCH_PARTS + 1)])
def test_batch_input_is_bounded(parts):
    with pytest.raises(WireError, match='batch_bound'):
        bundle.transfer_batch(parts)
