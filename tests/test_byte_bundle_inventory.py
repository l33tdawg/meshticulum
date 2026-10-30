"""Verified inventory uses real signed parts and retained files, not counters."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from meshticulum.resources import byte_bundle as bb
from tests.test_byte_bundle import Clock, inputs, package, payload, receive, signer


@pytest.mark.parametrize('finish', ['finish', 'finish_file'])
def test_out_of_order_parts_publish_only_after_complete_verification(tmp_path, signer, finish):
    packaged = package(signer); parts = inputs(packaged); path = tmp_path / 'receiver'
    with receive(path, packaged, Clock()) as receiver:
        assert receiver.verified_parts() == ()
        assert receiver.missing_parts() == (0, 1, 2, 3, 4)
        accepted = set()
        for index in (4, 2, 0, 3, 1):
            receiver.accept(index, parts[index]); accepted.add(index)
            assert receiver.verified_parts() == tuple(sorted(accepted))
            assert receiver.missing_parts() == tuple(i for i in range(5) if i not in accepted)
            assert not (path / 'bundle.bin').exists()
        result = getattr(receiver, finish)()
        assert result['complete'] and result['publisher_verified']
        assert (path / 'bundle.bin').read_bytes() == payload()


def test_sparse_resume_reverifies_indexes_without_renewing_context(tmp_path, signer):
    packaged = package(signer); parts = inputs(packaged); path = tmp_path / 'receiver'; clock = Clock()
    with receive(path, packaged, clock) as receiver:
        receiver.accept(4, parts[4]); receiver.accept(1, parts[1])
        with pytest.raises(FileNotFoundError): receiver.finish_file()
        assert not (path / 'bundle.bin').exists()
    context = (path / 'context.json').read_bytes()
    clock.epoch += 1000000; clock.mono += 1000000
    with receive(path, packaged, clock) as receiver:
        assert receiver.reused == 2
        assert receiver.verified_parts() == (1, 4)
        assert receiver.missing_parts() == (0, 2, 3)
        receiver.reused = 42  # An informational count cannot authorize a part.
        receiver.accept(3, parts[3])
        assert receiver.verified_parts() == (1, 3, 4)
        assert receiver.missing_parts() == (0, 2)
        for index in receiver.missing_parts(): receiver.accept(index, parts[index])
        receiver.finish_file()
    assert (path / 'context.json').read_bytes() == context
    assert (path / 'bundle.bin').read_bytes() == payload()


def test_wrong_index_and_duplicate_do_not_change_verified_inventory(tmp_path, signer):
    packaged = package(signer); parts = inputs(packaged); path = tmp_path / 'receiver'
    with receive(path, packaged, Clock()) as receiver:
        with pytest.raises(ValueError): receiver.accept(0, parts[4])
        assert receiver.verified_parts() == ()
        receiver.accept(4, parts[4])
        with pytest.raises(ValueError, match='duplicate bundle part'): receiver.accept(4, parts[4])
        assert receiver.verified_parts() == (4,)
        assert receiver.missing_parts() == (0, 1, 2, 3)
        assert not (path / 'bundle.bin').exists()


@pytest.mark.parametrize('method', ['verified_parts', 'missing_parts'])
@pytest.mark.parametrize('fault', ['bytes', 'symlink', 'foreign_entry', 'trust', 'epoch', 'monotonic', 'context'])
def test_inventory_refuses_invalid_retained_state(tmp_path, signer, method, fault):
    packaged = package(signer); parts = inputs(packaged); path = tmp_path / 'receiver'; clock = Clock()
    with receive(path, packaged, clock) as receiver:
        receiver.accept(2, parts[2]); assert receiver.verified_parts() == (2,)
        target = path / 'parts' / (packaged[4]['chunks'][2]['sha256'] + '.json')
        if fault == 'bytes': target.write_bytes(b'{}')
        elif fault == 'symlink':
            target.unlink(); target.symlink_to(path / 'context.json')
        elif fault == 'foreign_entry': (path / 'parts' / 'unexpected.json').write_bytes(b'{}')
        elif fault == 'trust': packaged[2]['keys'][0]['status'] = 'revoked'
        elif fault == 'epoch': clock.epoch = receiver.context['expires_epoch_us']
        elif fault == 'monotonic': clock.mono = receiver.context['deadline_monotonic_us']
        else: (path / 'context.json').write_bytes(b'{}')
        with pytest.raises((OSError, ValueError)): getattr(receiver, method)()
        assert not (path / 'bundle.bin').exists()


@pytest.mark.parametrize('method', ['verified_parts', 'missing_parts'])
def test_inventory_rechecks_clock_after_part_read(tmp_path, signer, monkeypatch, method):
    packaged = package(signer); parts = inputs(packaged); path = tmp_path / 'receiver'; clock = Clock()
    with receive(path, packaged, clock) as receiver:
        receiver.accept(4, parts[4]); original = receiver._part
        def expire(index):
            value = original(index); clock.mono = receiver.context['deadline_monotonic_us']; return value
        monkeypatch.setattr(receiver, '_part', expire)
        with pytest.raises(ValueError, match='original bundle deadline expired'): getattr(receiver, method)()


def test_later_read_cannot_invalidate_an_earlier_verified_part(tmp_path, signer, monkeypatch):
    packaged = package(signer); parts = inputs(packaged); path = tmp_path / 'receiver'
    with receive(path, packaged, Clock()) as receiver:
        receiver.accept(0, parts[0]); receiver.accept(4, parts[4]); original = receiver._part
        def tamper(index):
            value = original(index)
            if index == 4:
                (path / 'parts' / (packaged[4]['chunks'][0]['sha256'] + '.json')).write_bytes(b'{}')
            return value
        monkeypatch.setattr(receiver, '_part', tamper)
        with pytest.raises(ValueError, match='bundle inventory changed'): receiver.verified_parts()


def test_missing_file_is_recomputed_and_no_cross_thread_owner_is_accepted(tmp_path, signer):
    packaged = package(signer); parts = inputs(packaged); path = tmp_path / 'receiver'
    with receive(path, packaged, Clock()) as receiver:
        receiver.accept(3, parts[3]); assert receiver.verified_parts() == (3,)
        (path / 'parts' / (packaged[4]['chunks'][3]['sha256'] + '.json')).unlink()
        assert receiver.verified_parts() == () and receiver.missing_parts() == (0, 1, 2, 3, 4)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(ValueError, match='bundle owner changed'): pool.submit(receiver.verified_parts).result()
    with pytest.raises(ValueError, match='bundle owner changed'): receiver.missing_parts()


def test_inventory_retains_the_42_part_one_megabyte_bound(tmp_path, signer):
    packaged = package(signer, 1000000); parts = inputs(packaged)
    with receive(tmp_path / 'receiver', packaged, Clock()) as receiver:
        receiver.accept(41, parts[41]); receiver.accept(0, parts[0])
        assert receiver.verified_parts() == (0, 41)
        assert receiver.missing_parts() == tuple(range(1, 41))
        with pytest.raises(ValueError): receiver.accept(42, parts[-1])
