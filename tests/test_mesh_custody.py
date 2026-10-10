# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
import pytest

from meshticulum.integrations.sage.mesh_custody import (CustodyError, CustodyStore, MAX_TTL_SECONDS,
                                       message_digest, parse_custody, wrap_custody)


RECIPIENT = bytes.fromhex("112233445566")
OTHER = bytes.fromhex("aabbccddeeff")
MESSAGE_ID = "6f0d5f4a-9a2b-4c3d-8e1f-2a3b4c5d6e7f"
ENVELOPE = "LME1." + "A" * 64


def deposit(now=1000, message_id=MESSAGE_ID, recipient=RECIPIENT, envelope=ENVELOPE,
            expires_at=None, envelope_expires_at=None):
    return wrap_custody(recipient_prefix=recipient, message_id=message_id,
                        envelope_wire=envelope,
                        expires_at=now + 3600 if expires_at is None else expires_at,
                        envelope_expires_at=now + 3600 if envelope_expires_at is None else envelope_expires_at,
                        now=now)


def test_round_trip_keeps_the_sealed_envelope_opaque():
    parsed = parse_custody(deposit())
    assert parsed.recipient_prefix == RECIPIENT
    assert parsed.message_digest == message_digest(MESSAGE_ID)
    assert parsed.envelope_wire == ENVELOPE
    assert parsed.expires_at == 4600


@pytest.mark.parametrize("bad", [
    "",
    "SLC2.AAAA",
    "SLC1.",
    "SLC1.!!!!",
    "LME1." + "A" * 64,
    deposit()[:-8],
])
def test_malformed_wires_are_refused(bad):
    with pytest.raises(CustodyError):
        parse_custody(bad)


def test_wrap_bounds_and_prefixes_are_enforced():
    with pytest.raises(CustodyError):
        wrap_custody(recipient_prefix=b"short", message_id=MESSAGE_ID,
                     envelope_wire=ENVELOPE, expires_at=4600, envelope_expires_at=4600, now=1000)
    with pytest.raises(CustodyError):
        wrap_custody(recipient_prefix=RECIPIENT, message_id=MESSAGE_ID,
                     envelope_wire="not-sealed", expires_at=4600, envelope_expires_at=4600, now=1000)
    with pytest.raises(CustodyError):
        wrap_custody(recipient_prefix=RECIPIENT, message_id=MESSAGE_ID, envelope_wire=ENVELOPE,
                     expires_at=1000 + MAX_TTL_SECONDS + 1,
                     envelope_expires_at=1000 + MAX_TTL_SECONDS, now=1000)
    with pytest.raises(CustodyError):
        wrap_custody(recipient_prefix=RECIPIENT, message_id=MESSAGE_ID, envelope_wire=ENVELOPE,
                     expires_at=1030, envelope_expires_at=4600, now=1000)
    # A custody copy must not outlive the sealed envelope it carries.
    with pytest.raises(CustodyError):
        wrap_custody(recipient_prefix=RECIPIENT, message_id=MESSAGE_ID, envelope_wire=ENVELOPE,
                     expires_at=4000, envelope_expires_at=3600, now=1000)


def test_store_admits_dedupes_and_retrieves_oldest_first():
    store = CustodyStore()
    first = parse_custody(deposit(message_id="6f0d5f4a-9a2b-4c3d-8e1f-2a3b4c5d6e70"))
    second = parse_custody(deposit(message_id="6f0d5f4a-9a2b-4c3d-8e1f-2a3b4c5d6e71"))
    assert store.admit(first, source="peer-a", now=1000) == "stored"
    assert store.admit(first, source="peer-a", now=1001) == "duplicate"
    assert store.admit(second, source="peer-a", now=1002) == "stored"
    assert [item.message_digest for item in store.pending_for(RECIPIENT, now=1003)] == \
        [first.message_digest, second.message_digest]
    assert store.pending_for(OTHER, now=1003) == ()


def test_store_refuses_expired_bounds_and_delivers():
    store = CustodyStore(max_entries=2, max_bytes=2 * (len(ENVELOPE) + 32), max_per_recipient=1)
    first = parse_custody(deposit())
    assert store.admit(first, source="peer-a", now=1000) == "stored"
    with pytest.raises(CustodyError):
        store.admit("not-a-deposit", source="peer-a", now=1000)
    assert store.admit(parse_custody(deposit(message_id="6f0d5f4a-9a2b-4c3d-8e1f-2a3b4c5d6e72")),
                       source="peer-a", now=1000) == "rejected"
    assert store.confirm_delivered(RECIPIENT, first.message_digest) is True
    assert store.confirm_delivered(RECIPIENT, first.message_digest) is False
    assert store.admit(parse_custody(deposit(now=10 ** 6)), source="peer-a", now=10 ** 6) == "stored"
    assert store.purge(now=10 ** 6 + 3601) == 1
    assert store.stats(now=10 ** 6 + 3601)["entries"] == 0


def test_records_round_trip_through_load():
    store = CustodyStore()
    item = parse_custody(deposit())
    store.admit(item, source="peer-a", now=1000)
    rebuilt = CustodyStore.load(store.records())
    assert [entry.envelope_wire for entry in rebuilt.pending_for(RECIPIENT, now=1001)] == [ENVELOPE]
    assert rebuilt.stats(now=1001)["bytes"] == len(ENVELOPE)


def test_store_caps_one_sources_share():
    store = CustodyStore(max_per_source=1)
    first = parse_custody(deposit(message_id="6f0d5f4a-9a2b-4c3d-8e1f-2a3b4c5d6e73"))
    second = parse_custody(deposit(message_id="6f0d5f4a-9a2b-4c3d-8e1f-2a3b4c5d6e74"))
    assert store.admit(first, source="peer-a", now=1000) == "stored"
    assert store.admit(second, source="peer-a", now=1000) == "rejected"
    assert store.admit(second, source="peer-b", now=1000) == "stored"
