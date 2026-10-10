"""Native entry controls with invented host facts and signed public fixtures."""
import hashlib
import json

import pytest

from meshticulum.bench import pilot_resource_node as node
from meshticulum.bench.pilot_resources import serve_chunk
from meshticulum.bench.wire import WireError
from meshticulum.ip.node import _write, clock
from tests.test_ip_node import facts
from tests.test_meshticulum_resources import pack
from tests.test_pilot_resources import setup, capture


def prepared(tmp_path, pack, monkeypatch):
    root, destination, request, original = setup(tmp_path, pack)
    response = serve_chunk(root, '1' * 32, facts=facts('unit-b'), clock=clock,
        seed=pack[0], trust=pack[1], manifest_sha256=pack[2])
    response_directory = capture(root, '2' * 32, response, 'MR1.response',
        original['application_expires_epoch_us'], original['application_deadline_monotonic_us'])
    request_directory = root / 'runs' / ('1' * 32)
    receipt_path = request_directory / 'result.json'
    receipt = json.loads(receipt_path.read_bytes())
    receipt.update(sends=True)
    receipt_path.write_text(json.dumps(receipt))
    (request_directory / 'receiver.bin').unlink()
    _write(request_directory / 'sender.bin', request)
    _write(request_directory / 'grant.json', json.dumps(dict(object=dict(
        bytes=len(request), sha256=hashlib.sha256(request).hexdigest()))).encode())
    public = root / 'public' / pack[2]
    public.mkdir(parents=True, mode=0o700)
    public.parent.chmod(0o700)
    destination.rename(public / 'received')
    _write(public / 'trust.json', json.dumps(pack[1]).encode())
    monkeypatch.setattr(node, 'STATE', root)
    monkeypatch.setattr(node, '_facts', facts)
    return root, request_directory, response_directory, original


def test_native_wrapper_requires_original_sent_request_and_records_verified_piece(pack, tmp_path, monkeypatch):
    root, request, response, original = prepared(tmp_path, pack, monkeypatch)
    result = node.verify('unit-b', 'c' * 32, '1' * 32, '2' * 32)
    assert result['publisher_verified'] and not result['private_admission'] and not result['sage_imported']
    assert json.loads((response / 'verified-piece.json').read_bytes()) == result
    assert result['original_application_deadline_us'] == original['application_deadline_monotonic_us']


@pytest.mark.parametrize('fault', ['schema', 'boot', 'receiver', 'incomplete', 'expired',
    'extended_deadline', 'retired', 'request_bytes'])
def test_native_wrapper_refuses_changed_or_expired_original_request_before_cache(pack, tmp_path, monkeypatch, fault):
    root, request, response, original = prepared(tmp_path, pack, monkeypatch)
    receipt_path = request / 'result.json'
    receipt = json.loads(receipt_path.read_bytes())
    if fault == 'schema': receipt['schema'] = 'untrusted-replacement'
    if fault == 'boot': receipt['host_boot_id'] = '31f9c178-6090-40d9-9359-ecf1c7b950b1'
    if fault == 'receiver': receipt['sends'] = False
    if fault == 'incomplete': receipt['complete'] = False
    if fault == 'expired': receipt['original_application_deadline_us'] = clock()[1] - 1
    if fault == 'extended_deadline': receipt['original_application_deadline_us'] += 1
    receipt_path.write_text(json.dumps(receipt))
    if fault == 'retired': (request / 'active').write_bytes(b'retired\n')
    if fault == 'request_bytes': (request / 'sender.bin').write_bytes(b'changed')
    with pytest.raises(WireError):
        node.verify('unit-b', 'c' * 32, '1' * 32, '2' * 32)
    assert not (response / 'verified-piece.json').exists()
    assert not (root / 'public' / pack[2] / 'received' / 'chunks').exists()


def test_changed_active_identity_during_verification_cannot_publish_success(pack, tmp_path, monkeypatch):
    root, request, response, original = prepared(tmp_path, pack, monkeypatch)
    original_verify = node.verify_chunk
    def replaced(*args, **kwargs):
        result = original_verify(*args, **kwargs)
        (request / 'active').unlink()
        _write(request / 'active', b'active\n')
        return result
    monkeypatch.setattr(node, 'verify_chunk', replaced)
    with pytest.raises(WireError, match='native_request_authority_retired_or_changed'):
        node.verify('unit-b', 'c' * 32, '1' * 32, '2' * 32)
    assert not (response / 'verified-piece.json').exists()


def test_tightened_request_deadline_is_checked_before_chunk_cache_commit(pack, tmp_path, monkeypatch):
    root, request, response, original = prepared(tmp_path, pack, monkeypatch)
    epoch, mono = clock()
    deadline = mono + 500000
    receipt_path = request / 'result.json'
    receipt = json.loads(receipt_path.read_bytes())
    receipt['original_application_deadline_us'] = deadline
    receipt_path.write_text(json.dumps(receipt))
    now = [epoch, mono]
    monkeypatch.setattr(node, 'clock', lambda: tuple(now))
    from meshticulum.bench import pilot_resources
    original_chunk = pilot_resources.ll.chunk
    def expires_during_publisher_check(*args, **kwargs):
        value = original_chunk(*args, **kwargs)
        now[1] = deadline
        return value
    monkeypatch.setattr(pilot_resources.ll, 'chunk', expires_during_publisher_check)
    with pytest.raises(WireError, match='pilot_resource_original_deadline_expired'):
        node.verify('unit-b', 'c' * 32, '1' * 32, '2' * 32)
    assert not (response / 'verified-piece.json').exists()
    assert not (root / 'public' / pack[2] / 'received' / 'chunks').exists()
