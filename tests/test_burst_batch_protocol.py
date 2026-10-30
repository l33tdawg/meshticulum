"""Offline MBQ2 records and prepaid host uncertainty; never serial or RF."""
from dataclasses import replace

import pytest

from meshticulum.bench.burst_batch_protocol import (
    BatchKind, BatchPlan, BatchRecord, PrepaidCommit, ReleaseEntry,
    check_begin, check_commit, check_release, check_stage, release_record,
)
from meshticulum.bench.tx_burst import (
    Binding, BurstContractError, PacketDescriptor, QueueDescriptor, Stage, UINT64_MAX,
)


def plan():
    payloads = tuple(bytes((i + n) % 256 for i in range(255)) for n in range(4))
    packets = tuple(PacketDescriptor.from_ciphertext(11 + n, data, 100_000, 2_500_000)
                    for n, data in enumerate(payloads))
    queue = QueueDescriptor(Binding(b'\x01' + bytes(15), b'\x02' + bytes(31)),
                            1_000_000, 2_500_000, 2_500_000, packets)
    return BatchPlan(queue, 1, 10, payloads)


def entries(value):
    return tuple(ReleaseEntry(p.request, Stage.RF_DONE, 1_100_000 + n * 110_000, p.digest)
                 for n, p in enumerate(value.queue.packets))


def test_all_records_fit_unchanged_320_byte_body():
    value = plan()
    check_begin(value.begin(), value)
    assert len(value.begin().body) == 49
    for n in range(4):
        assert len(value.stage(n).body) == 314
        assert check_stage(value.stage(n), value) == value.queue.packets[n]
    check_commit(value.commit(15), value, 15)
    record = release_record(value, commit_request=15, request=16, entries=entries(value))
    assert len(record.body) == 245
    check_release(record, value, commit_request=15, request=16, entries=entries(value))


@pytest.mark.parametrize('field,new', [('boot', b'\x03' * 16), ('request', 99),
                                      ('kind', BatchKind.COMMIT)])
def test_begin_exact_binding(field, new):
    value = plan()
    with pytest.raises(BurstContractError):
        check_begin(replace(value.begin(), **{field: new}), value)


@pytest.mark.parametrize('offset', [0, 8, 16, 17, 25, 27, 58, 59, 313])
def test_each_stage_component_and_payload_is_bound(offset):
    value = plan()
    body = bytearray(value.stage(0).body)
    body[offset] ^= 1
    with pytest.raises(BurstContractError):
        check_stage(replace(value.stage(0), body=bytes(body)), value)


@pytest.mark.parametrize('size', [0, 1, 58, 59, 313, 315, 320])
def test_stage_truncation_and_padding_rejected(size):
    value = plan()
    body = (value.stage(0).body + bytes(320))[:size]
    with pytest.raises(BurstContractError):
        check_stage(replace(value.stage(0), body=body), value)


@pytest.mark.parametrize('mutation', ['boot', 'context', 'token', 'job', 'request', 'deadline', 'ciphertext', 'estimate'])
def test_manifest_covers_every_execution_binding(mutation):
    value = plan()
    changed = value
    if mutation in ('boot', 'context'):
        binding = value.queue.binding
        binding = replace(binding, **{('boot' if mutation == 'boot' else 'context_sha256'):
                                      (b'\x03' * (16 if mutation == 'boot' else 32))})
        changed = replace(value, queue=replace(value.queue, binding=binding))
    elif mutation in ('token', 'job'):
        changed = replace(value, **{mutation: getattr(value, mutation) + (1 if mutation == 'token' else -1)})
    else:
        packets = value.queue.packets
        payloads = value.ciphertexts
        deadline = value.queue.deadline_us
        if mutation == 'request':
            packets = tuple(replace(p, request=p.request + 10) for p in packets)
        elif mutation == 'deadline':
            deadline -= 1
            packets = tuple(replace(p, deadline_us=deadline) for p in packets)
        elif mutation == 'estimate':
            packets = (replace(packets[0], estimated_airtime_us=100001),) + packets[1:]
        else:
            payloads = (b'X' * 255,) + payloads[1:]
            packets = (PacketDescriptor.from_ciphertext(11, payloads[0], 100000, deadline),) + packets[1:]
        changed = replace(value, queue=replace(value.queue, packets=packets, deadline_us=deadline),
                          ciphertexts=payloads)
    assert value.manifest != changed.manifest


def test_plan_rejects_ciphertext_digest_mismatch_and_partial_vector():
    value = plan()
    for payloads in (value.ciphertexts[:-1], (b'X' * 255,) + value.ciphertexts[1:]):
        with pytest.raises(BurstContractError):
            replace(value, ciphertexts=payloads)


@pytest.mark.parametrize('command_request', [0, True, 14, UINT64_MAX + 1])
def test_commit_requires_fresh_finite_request(command_request):
    with pytest.raises(BurstContractError):
        plan().commit(command_request)


def test_release_requires_complete_ordered_matching_terminal_vector():
    value = plan()
    outcomes = entries(value)
    malformed = (outcomes[:-1], outcomes[::-1], outcomes[:3] + (outcomes[0],),
                 outcomes[:3] + (replace(outcomes[3], digest=b'\x02' * 32),))
    for vector in malformed:
        with pytest.raises(BurstContractError):
            release_record(value, commit_request=15, request=16, entries=vector)
    with pytest.raises(BurstContractError):
        replace(outcomes[0], stage=Stage.QUEUED)
    exact = release_record(value, commit_request=15, request=16, entries=outcomes)
    with pytest.raises(BurstContractError):
        check_release(exact, value, commit_request=15, request=16,
                      entries=(replace(outcomes[0], event_us=1),) + outcomes[1:])


def test_prepaid_commit_never_refunds_acceptance_refusal_or_unknown():
    value = plan()
    charges = (111001,) * 4
    for action in ('accept', 'refuse', 'unknown'):
        tracked = PrepaidCommit(value, 15, charges)
        if action == 'unknown':
            tracked.unknown()
        else:
            tracked.reply(value.commit(15), accepted=action == 'accept')
        assert tracked.charges_us == charges and sum(tracked.charges_us) == 444004
        assert tracked.fenced is (action == 'unknown')


def test_lost_commit_reply_is_sticky_even_after_exact_replay():
    value = plan()
    tracked = PrepaidCommit(value, 15, (110001,) * 4)
    record = tracked.record
    tracked.unknown()
    tracked.reply(record, accepted=True)
    tracked.reply(record, accepted=False)
    assert tracked.state == 'UNKNOWN' and tracked.fenced
    assert tracked.record == record and tracked.charges_us == (110001,) * 4


@pytest.mark.parametrize('mutation', ['boot', 'request', 'manifest', 'partial', 'non_bool'])
def test_bad_commit_reply_fences_entire_prepaid_vector(mutation):
    value = plan()
    tracked = PrepaidCommit(value, 15, (110001,) * 4)
    record = tracked.record
    accepted = True
    if mutation == 'boot':
        record = replace(record, boot=b'\x03' * 16)
    elif mutation == 'request':
        record = replace(record, request=16)
    elif mutation == 'manifest':
        record = replace(record, body=record.body[:-1] + bytes([record.body[-1] ^ 1]))
    elif mutation == 'partial':
        record = replace(record, body=record.body[:-1])
    else:
        accepted = 1
    with pytest.raises(BurstContractError):
        tracked.reply(record, accepted=accepted)
    assert tracked.state == 'UNKNOWN' and sum(tracked.charges_us) == 440004


def test_conflicting_reply_cannot_replace_original_admission():
    value = plan()
    tracked = PrepaidCommit(value, 15, (110001,) * 4)
    tracked.reply(tracked.record, accepted=True)
    tracked.reply(tracked.record, accepted=True)
    with pytest.raises(BurstContractError):
        tracked.reply(tracked.record, accepted=False)
    assert tracked.fenced


@pytest.mark.parametrize('charges', [(100000,) * 3, (99999,) * 4, (True,) * 4, (UINT64_MAX,) * 4])
def test_missing_underpaid_or_overflowing_host_reservation(charges):
    with pytest.raises(BurstContractError):
        PrepaidCommit(plan(), 15, charges)


def test_codec_records_are_not_production_serial_frames():
    from meshticulum.bench.serial_protocol import Frame, Message
    assert not isinstance(plan().commit(15), Frame)
    assert not any('BATCH' in member.name for member in Message)
    with pytest.raises(BurstContractError):
        BatchRecord(BatchKind.STAGE, b'\x01' * 16, 1, bytes(321))
