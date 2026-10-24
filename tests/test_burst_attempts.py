"""Durable physical handover tests; no radio or application authority is opened."""
import hashlib
import sqlite3

import pytest

from meshticulum.bench.attempts import AttemptError, AttemptJournal


@pytest.fixture
def journal(tmp_path):
    tmp_path.chmod(0o700)
    value = AttemptJournal(tmp_path / 'queue.sqlite', binding=b'b' * 32,
        max_attempts=8, max_airtime_us=2_000_000, create=True, request_clock=lambda: 100)
    yield value
    value.close()


def test_queue_metadata_and_exact_debt_share_durable_reservation(journal, tmp_path):
    packet, metadata = b'ciphertext', b'boot/context/deadline/logical-identity'
    request = journal.reserve(packet, 110_000, queue_metadata=metadata)
    with sqlite3.connect((tmp_path / 'queue.sqlite').as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT a.packet_sha,a.bytes,a.estimated_us,a.state,q.metadata '
            'FROM attempts a JOIN queued_attempts q USING(request)').fetchone()
    assert row == (hashlib.sha256(packet).digest(), len(packet), 110_000, 'RESERVED', metadata)
    record = journal.queued_records()[0]
    assert record['request'] == request and not record['released']
    assert record['terminal_event'] is None


def test_terminal_settlement_precedes_exact_release_and_never_refunds(journal):
    packet = b'ciphertext'
    request = journal.reserve(packet, 110_000, queue_metadata=b'immutable')
    with pytest.raises(AttemptError, match='wrong_queue_release'):
        journal.release_queued(request, b'event')
    assert journal.settle_queued(request, packet, 'RF_DONE', 'driver', b'event')
    assert not journal.settle_queued(request, packet, 'RF_DONE', 'driver', b'event')
    with pytest.raises(AttemptError, match='attempt_already_settled'):
        journal.settle_queued(request, packet, 'RF_DONE', 'changed', b'changed-event')
    with pytest.raises(AttemptError, match='wrong_queue_release'):
        journal.release_queued(request, b'changed-event')
    assert journal.release_queued(request, b'event')
    assert not journal.release_queued(request, b'event')
    assert journal.snapshot()['reserved_estimated_airtime_us'] == 110_000


def test_recovery_keeps_metadata_and_fences_without_upgrading_unknown(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / 'recover.sqlite'
    options = dict(binding=b'b' * 32, max_attempts=8, max_airtime_us=2_000_000)
    first = AttemptJournal(path, create=True, **options)
    request = first.reserve(b'packet', 100_000, queue_metadata=b'exact-queue-metadata')
    first.close()
    recovered = AttemptJournal(path, **options)
    try:
        assert recovered.snapshot()['fenced'] and recovered.snapshot()['recovered_unknown'] == 1
        assert recovered.queued_records()[0]['metadata'] == b'exact-queue-metadata'
        with pytest.raises(AttemptError, match='attempt_run_fenced'):
            recovered.reserve(b'new', 100_000, queue_metadata=b'new')
        with pytest.raises(AttemptError, match='attempt_already_settled'):
            recovered.settle_queued(request, b'packet', 'RF_DONE', 'late', b'late-driver-event')
        # Preserve exact physical evidence solely to release retained native
        # state; the host's unresolved outcome and fence remain unchanged.
        recovered.reconcile_queued_unknown(request, packet_sha=hashlib.sha256(b'packet').digest(),
            size=6, terminal_event=b'late-driver-event', evidence='late-only')
        recovered.release_queued(request, b'late-driver-event')
        assert recovered.queued_records()[0]['state'] == 'UNKNOWN'
        assert recovered.snapshot()['fenced']
    finally:
        recovered.close()


@pytest.mark.parametrize('metadata', [b'', bytearray(b'x'), b'x' * 1025, 3])
def test_invalid_metadata_creates_no_attempt_or_debt(journal, metadata):
    with pytest.raises(AttemptError, match='invalid_queue_metadata'):
        journal.reserve(b'packet', 100_000, queue_metadata=metadata)
    assert journal.snapshot()['attempts'] == 0


def test_definitely_unwritten_queue_item_needs_no_native_release(journal):
    request = journal.reserve(b'packet', 100_000, queue_metadata=b'bound')
    journal.settle_queued(request, b'packet', 'NOT_SENT', 'authority_before_write', None)
    assert journal.queued_records()[0]['released']
    assert journal.snapshot()['reserved_estimated_airtime_us'] == 100_000
