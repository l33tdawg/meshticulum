"""Five-part public MB1 composition. Models are not native/RF qualification."""
from dataclasses import asdict
from functools import partial
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.objects import VerifiedChunks
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import FrameKind, HEADER, SessionCipher, WireError
from meshticulum.resources import byte_bundle, knowledge_pack as kp
from tests.test_radio_file_example import Clock, ModelWorker, BOOT, PROFILE, ADMISSION, PEERS, finish

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'examples'))
import radio_bundle as bundle
base = bundle.base


@pytest.mark.parametrize('modem', ['lora', 'gfsk250'])
def test_transfer_parses_modem_admission_before_running_object(tmp_path, published, monkeypatch, modem):
    from tests.radio_profiles import SOURCE_ADMISSION

    _, raw, signed, trust, digest, manifest, chunks = published
    expected = byte_bundle.descriptor(manifest)
    pack = tmp_path / 'pack'
    pack.mkdir()
    kp.write_pack(pack, manifest, chunks, json.loads(signed))
    monkeypatch.setattr(bundle, 'load_bundle', lambda args: (raw, signed, trust, manifest, expected))
    monkeypatch.setattr(base, 'firmware_identity', lambda *args: 'mtb1-0123456789')
    monkeypatch.setattr(base, 'FileWindow', partial(base.FileWindow, boot_reader=lambda: BOOT))
    issued = base.clock()[0]
    expires = issued + 240_000_000
    entry = manifest['chunks'][0]
    payload = bundle.response_for(digest, entry, kp.chunk_bytes(chunks[entry['sha256']]))
    plan = dict(schema='meshticulum.service-run.v1', sender_unit='sender',
        issued_epoch_us=issued, expires_epoch_us=expires,
        object=dict(object_id=expected['sha256'], chunk_index=0,
                    bytes=len(payload), sha256=base.sha(payload)),
        service=bundle.service_for(entry, payload, expires))
    admission = SOURCE_ADMISSION if modem == 'lora' else ADMISSION
    if modem == 'gfsk250':
        plan['radio_modem'] = modem
    grant = tmp_path / 'grant.json'
    grant.write_bytes(kp.canonical_json(plan))
    grant.chmod(0o600)
    admitted = tmp_path / 'admission.json'
    admitted.write_text(json.dumps(asdict(admission)))
    admitted.chmod(0o600)
    args = SimpleNamespace(part=0, file_issued_epoch_us=issued, file_expires_epoch_us=expires,
        grant_directory=tmp_path, grant_sha256=base.sha(grant.read_bytes()),
        admission=admitted, admission_sha256=base.sha(admitted.read_bytes()),
        firmware_source=None, firmware_source_sha256=None, state=tmp_path / 'state',
        manifest_sha256=digest, unit='sender', interface='hat', pack=pack)
    observed = []

    def run_object(args, raw_plan, parsed, version, actual_payload, window, factory):
        observed.append(parsed)
        assert raw_plan == grant.read_bytes()
        assert version == 'mtb1-0123456789' and actual_payload == payload
        return {'parsed': True}

    monkeypatch.setattr(base, 'run_object', run_object)
    assert bundle.transfer(args) == {'parsed': True}
    assert observed == [admission]
    assert (args.state / 'sent-0.json').is_file()


@pytest.mark.parametrize(('part_count', 'expected'), [
    (1, 240_000_000),
    (5, 240_000_000),
    (11, 520_000_000),
    (21, 720_000_000),
    (42, 1_140_000_000),
])
def test_large_bundle_window_is_bounded_by_ack_grace_and_reviewed_headroom(part_count, expected):
    assert bundle.max_file_window_us(part_count) == expected


def test_large_bundle_window_is_retained_by_the_file_clock(tmp_path):
    clock = Clock()
    issued = clock()[0]
    window = base.FileWindow(tmp_path, dict(sha256='a' * 64),
        issued, issued + bundle.max_file_window_us(11), time_source=clock,
        boot_reader=lambda: BOOT, max_window_us=bundle.max_file_window_us(11))
    assert window.deadline_us == clock()[1] + bundle.max_file_window_us(11)


@pytest.mark.parametrize('part_count', [0, -1, True, 1.5])
def test_invalid_bundle_window_part_count_is_refused(part_count):
    with pytest.raises(WireError, match='invalid_example_part_count'):
        bundle.max_file_window_us(part_count)


@pytest.fixture
def published():
    data = bytes(range(256)) * 390 + bytes(range(160))
    key = Ed25519PrivateKey.generate()
    manifest, chunks, signature = byte_bundle.build(data, pack_id='public-example-model',
        created_at='2026-10-15T00:00:00Z', private_key=key)
    raw, signed = kp.manifest_bytes(manifest), kp.canonical_json(signature)
    trust = dict(schema='sage-foundation.knowledge-pack.trust.v1', keys=[dict(
        key_id=signature['key_id'], public_key_hex=kp.public_key_hex(key),
        status='active', pack_classes=['reference'])])
    return data, raw, signed, trust, base.sha(raw), manifest, chunks


class FaultWorker(ModelWorker):
    def transmit(self, packet):
        result = super().transmit(packet)
        if (getattr(self, 'lose_final_ack', False) and self.endpoint.flow.complete
                and FrameKind(HEADER.unpack(packet[:HEADER.size])[2]) is FrameKind.ACK):
            self.lose_final_ack = False
            self.peer.incoming.pop()
            self.lost_final_ack = True
        return result


def open_pair(path, published, index, clock, window, receiver, mcu_counter, fault, *, confirm_receipt=False):
    _, _, _, _, digest, manifest, chunks = published
    entry = manifest['chunks'][index]
    payload = bundle.response_for(digest, entry, kp.chunk_bytes(chunks[entry['sha256']]))
    expected = byte_bundle.descriptor(manifest)
    obj = dict(object_id=expected['sha256'], chunk_index=index,
               bytes=len(payload), sha256=base.sha(payload))
    service = bundle.service_for(entry, payload, window.record['expires_epoch_us'])
    opened, transfers, workers = [], [], []
    for direction, unit in enumerate(('sender', 'receiver')):
        root = path / unit
        root.mkdir(mode=0o700, parents=True)
        plan = dict(schema='meshticulum.service-run.v1', unit=unit, host_boot_id=BOOT,
            local_peer=PEERS[direction], initiator=PEERS[0], responder=PEERS[1],
            session_id=f'{index + 1:032x}', generation='33' * 16,
            issued_epoch_us=window.record['issued_epoch_us'], expires_epoch_us=window.record['expires_epoch_us'],
            profile=PROFILE.to_dict(), radio_modem='gfsk250', sender_unit='sender',
            country='MY', antenna_confirmed=True, max_frames=512, object=obj, service=service)
        for name, content in (('grant.json', json.dumps(plan).encode()),
                              ('secret.bin', b'K' * 32), ('active', b'active\n')):
            fd = os.open(root / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as output:
                output.write(content)
        journal = AttemptJournal(root / 'attempts.db', binding=bytes(32),
            max_attempts=256, max_airtime_us=60000000, create=True)
        worker = FaultWorker(journal, clock, [0] if direction == 0 else mcu_counter)
        authority = RunAuthority(root, expected_unit=unit, host_boot_id=BOOT, clock=clock,
            owner_probe=lambda w=worker: w.owned and window.current(), radio_admission=ADMISSION)
        store = SessionStore(root / 'wire.db', authority.grant, authority.local_peer,
                             boot_id=BOOT, clock=clock, create=True)
        transfer = bundle.BundleTransfer(authority, worker, journal,
            SessionCipher(authority.grant, authority.local_peer, store), entry, digest,
            index, receiver if direction else None, window, clock=clock, confirm_receipt=confirm_receipt)
        worker.endpoint = transfer.endpoint
        transfers.append(transfer)
        workers.append(worker)
        opened.append((transfer, store, authority, journal))
        assert authority.profile.max_boot_attempts == 256
        assert transfer.endpoint.maximum_bytes == 32768
        assert transfer.endpoint.flow.limits.max_total_frames == 256
    workers[0].peer, workers[1].peer = workers[1], workers[0]
    if fault == 'corruption':
        workers[0].corrupt_once = True
    elif fault == 'lost_final_ack':
        workers[1].lose_final_ack = True
    transfers[0].send(payload)
    return opened, transfers, workers


def close_pair(opened):
    for transfer, store, authority, journal in opened:
        transfer.close()
        store.retire()
        store.close()
        authority.close()
        journal.close()


@pytest.mark.parametrize('fault', ['none', 'corruption', 'lost_final_ack'])
def test_five_parts_100k_keep_original_window_publisher_hash_caps_and_debt(tmp_path, published, fault):
    data, raw, signature, trust, digest, manifest, _ = published
    assert len(data) == 100000 and len(manifest['chunks']) == 5
    clock = Clock()
    issued, expires, first_end = clock()[0], clock()[0] + 240000000, clock()[1] + 240000000
    state, quarantine = tmp_path / 'state', tmp_path / 'receiver'
    state.mkdir(mode=0o700)
    quarantine.mkdir(mode=0o700)
    mcu_counter, debt, retries = [0], [], 0
    for index in range(5):
        window = base.FileWindow(state, dict(sha256=digest, parts=5), issued, expires,
                                 time_source=clock, boot_reader=lambda: BOOT)
        assert window.deadline_us == first_end
        window.require_capacity(5 - index)
        with byte_bundle.Receiver(quarantine, raw, signature, trust, digest,
                expires_epoch_us=expires, clock=clock, boot_reader=lambda: BOOT) as receiver:
            assert receiver.reused == index
            opened, transfers, workers = open_pair(tmp_path / str(index), published, index,
                clock, window, receiver, mcu_counter, fault if index == 0 else 'none')
            try:
                finish(transfers, clock)
                assert clock()[1] - transfers[1].completed_at >= base.ACK_GRACE_US
                retries += transfers[0].endpoint.flow.retransmits
                for worker in workers:
                    snap = worker.journal.snapshot()
                    assert snap['attempts'] > 0 and not snap['fenced']
                    debt.append(snap['reserved_estimated_airtime_us'])
            finally:
                close_pair(opened)
            if index == 4:
                result = receiver.finish()
                assert result['complete'] and result['publisher_verified']
                assert result['useful_decoded_bytes'] == 100000
                assert result['whole_file_sha256'] == base.sha(data)
                assert result['original_expires_epoch_us'] == expires
                assert result['original_deadline_monotonic_us'] <= first_end
    assert 0 < mcu_counter[0] < 256
    assert all(value > 0 for value in debt)
    assert clock()[1] < first_end and (quarantine / 'bundle.bin').read_bytes() == data
    if fault != 'none':
        assert retries > 0


def test_missing_part_never_publishes_whole_file(tmp_path, published):
    _, raw, signed, trust, digest, manifest, chunks = published
    clock = Clock()
    with byte_bundle.Receiver(tmp_path, raw, signed, trust, digest,
            expires_epoch_us=clock()[0] + 240000000, clock=clock, boot_reader=lambda: BOOT) as receiver:
        for index, entry in enumerate(manifest['chunks'][:-1]):
            receiver.accept(index, kp.chunk_bytes(chunks[entry['sha256']]))
        with pytest.raises(FileNotFoundError):
            receiver.finish()
    assert not (tmp_path / 'bundle.bin').exists()


def test_original_monotonic_end_survives_wall_clock_rollback_between_commands(tmp_path):
    clock = Clock()
    file = dict(sha256='a' * 64, parts=5)
    issued, expires = clock()[0], clock()[0] + 240000000
    window = base.FileWindow(tmp_path, file, issued, expires, time_source=clock, boot_reader=lambda: BOOT)
    original_end = window.deadline_us
    clock.advance(200000000)
    clock.epoch -= 100000000
    reopened = base.FileWindow(tmp_path, file, issued, expires, time_source=clock, boot_reader=lambda: BOOT)
    assert reopened.deadline_us == original_end
    with pytest.raises(WireError, match='remaining_ack_graces'):
        reopened.require_capacity(5)
    clock.advance(40000000)
    assert not reopened.current()
    with pytest.raises(WireError, match='file_window_expired'):
        base.FileWindow(tmp_path, file, issued, expires, time_source=clock, boot_reader=lambda: BOOT)


def test_impossible_graces_refuse_before_hardware_or_grant_claim(tmp_path):
    clock = Clock()
    window = base.FileWindow(tmp_path, dict(sha256='a' * 64), clock()[0], clock()[0] + 240000000,
                             time_source=clock, boot_reader=lambda: BOOT)
    for count in (12, 64):
        with pytest.raises(WireError, match='remaining_ack_graces'):
            window.require_capacity(count)
    clock.advance(150000000)
    with pytest.raises(WireError, match='remaining_ack_graces'):
        window.require_capacity(5)


def test_host_and_mcu_boot_changes_cannot_refresh_file_window(tmp_path):
    from meshticulum.bench.serial_protocol import FirmwareIdentity
    clock = Clock()
    window = base.FileWindow(tmp_path, dict(sha256='a' * 64), clock()[0], clock()[0] + 240000000,
                             time_source=clock, boot_reader=lambda: BOOT)
    original = FirmwareIdentity(bytes.fromhex('0000545662f61b440000000000000012'), 1, 'mtb1-0123456789')
    window.pin_mcu(original)
    window.pin_mcu(original)
    with pytest.raises(WireError, match='mcu_boot_or_identity_changed'):
        window.pin_mcu(FirmwareIdentity(original.boot[:-1] + b'\x13', 1, original.version))
    with pytest.raises(WireError, match='original_file_window_changed'):
        base.FileWindow(tmp_path, dict(sha256='a' * 64), clock()[0], clock()[0] + 240000000,
                        time_source=clock, boot_reader=lambda: 'different-boot')


def test_expiry_during_final_ack_grace_preserves_received_part_and_charged_debt(tmp_path, published):
    _, raw, signed, trust, digest, _, _ = published
    clock = Clock()
    state, quarantine = tmp_path / 'state', tmp_path / 'receiver'
    state.mkdir(mode=0o700)
    quarantine.mkdir(mode=0o700)
    expires = clock()[0] + 240000000
    window = base.FileWindow(state, dict(sha256=digest, parts=5), clock()[0], expires,
                             time_source=clock, boot_reader=lambda: BOOT)
    with byte_bundle.Receiver(quarantine, raw, signed, trust, digest,
            expires_epoch_us=expires, clock=clock, boot_reader=lambda: BOOT) as receiver:
        opened, transfers, workers = open_pair(tmp_path / 'run', published, 0, clock,
            window, receiver, [0], 'none')
        try:
            for _ in range(1000):
                transfers[0].step(); transfers[1].step()
                if transfers[1].completed_at is not None:
                    break
                clock.advance(20000)
            assert transfers[1].completed_at is not None
            before = [w.journal.snapshot() for w in workers]
            clock.advance(240000001)
            with pytest.raises(WireError):
                transfers[1].step()
            assert [w.journal.snapshot() for w in workers] == before
            assert len(list((quarantine / 'parts').iterdir())) == 1
        finally:
            close_pair(opened)
    assert not (quarantine / 'bundle.bin').exists()


@pytest.mark.parametrize('phase', ['accept', 'finish'])
@pytest.mark.parametrize('retained_clock', [False, True], ids=['v2-independent-clock', 'v3-retained-clock'])
def test_internal_publication_checks_enforce_retained_end_during_io(
        tmp_path, published, monkeypatch, phase, retained_clock):
    """Exact v2 seam witness: a later quarantine mapping must not renew IO."""
    _, raw, signed, trust, digest, manifest, chunks = published
    clock = Clock()
    state, quarantine = tmp_path / 'state', tmp_path / 'receiver'
    state.mkdir(mode=0o700)
    quarantine.mkdir(mode=0o700)
    issued, expires = clock()[0], clock()[0] + 240000000
    original = base.FileWindow(state, dict(sha256=digest, parts=5), issued, expires,
                               time_source=clock, boot_reader=lambda: BOOT)
    clock.advance(100000000)
    clock.epoch -= 80000000  # Between commands; still above the first epoch observation.
    window = base.FileWindow(state, dict(sha256=digest, parts=5), issued, expires,
                             time_source=clock, boot_reader=lambda: BOOT)
    assert window.deadline_us == original.deadline_us
    receiver_clock = window.check if retained_clock else clock
    with byte_bundle.Receiver(quarantine, raw, signed, trust, digest,
            expires_epoch_us=expires, clock=receiver_clock, boot_reader=lambda: BOOT) as receiver:
        # The independently mapped SDK context is later than the original file
        # end in both cases. The v3 callback must enforce the earlier end inside IO.
        assert receiver.context['deadline_monotonic_us'] > window.deadline_us
        if phase == 'finish':
            for index, entry in enumerate(manifest['chunks']):
                receiver.accept(index, kp.chunk_bytes(chunks[entry['sha256']]))
        clock.advance(130000000)
        assert clock()[1] < window.deadline_us
        original_fsync, crossed = os.fsync, []

        def cross_file_end(fd):
            original_fsync(fd)
            if not crossed:
                crossed.append(True)
                clock.advance(20000000)  # The payload write/sync crosses the retained end.

        monkeypatch.setattr(os, 'fsync', cross_file_end)
        operation = (lambda: receiver.accept(0, kp.chunk_bytes(
            chunks[manifest['chunks'][0]['sha256']]))) if phase == 'accept' else receiver.finish
        if retained_clock:
            with pytest.raises(WireError, match='original_file_window_expired'):
                operation()
        else:
            # Preserve the old-fail/new-pass witness without changing SDK code.
            operation()
        assert crossed == [True] and not window.current()
        target = (quarantine / 'parts' / (manifest['chunks'][0]['sha256'] + '.json')
                  if phase == 'accept' else quarantine / 'bundle.bin')
        assert target.exists() is (not retained_clock)
        assert not list(quarantine.glob('.bundle-*'))
        assert not list((quarantine / 'parts').glob('.bundle-*'))
