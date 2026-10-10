# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
import base64
from concurrent.futures import ThreadPoolExecutor

import pytest

from meshticulum.integrations.sage.mesh_fragments import (
    ASSEMBLY_SECONDS, HEADER, INGRESS_BURST, MAX_MESSAGE_BYTES, PREFIX,
    FragmentAssembler, FragmentError, decode_fragment, split_envelope,
)


PEER = b"p" * 32


@pytest.mark.parametrize("size", [1, 47, 48, 49, 96, MAX_MESSAGE_BYTES])
def test_roundtrip_out_of_order_and_duplicate_suppression(size):
    payload = bytes(index % 256 for index in range(size))
    fragments = split_envelope(payload)
    assert all(len(text.encode("ascii")) <= 128 for text in fragments)
    receiver = FragmentAssembler()
    outputs = [receiver.accept(PEER, text) for text in reversed(fragments)]
    assert [value for value in outputs if value is not None] == [payload]
    assert all(receiver.accept(PEER, text) is None for text in fragments)


@pytest.mark.parametrize("payload", [b"", b"x" * (MAX_MESSAGE_BYTES + 1), "secret", None])
def test_size_and_type_bound(payload):
    with pytest.raises(FragmentError):
        split_envelope(payload)


@pytest.mark.parametrize("text", ["", "SL2:AA==", "SL1:!", "SL1:é", "SL1:" + "A" * 125, None])
def test_malformed_wire(text):
    with pytest.raises(FragmentError):
        decode_fragment(text)


def altered(text, *, index=None, count=None, size=None, payload=None):
    fragment = decode_fragment(text)
    header = HEADER.pack(fragment.digest, fragment.index if index is None else index,
                         fragment.count if count is None else count, fragment.size if size is None else size)
    return PREFIX + base64.b64encode(header + (fragment.payload if payload is None else payload)).decode("ascii")


@pytest.mark.parametrize("changes", [{"index": 3}, {"count": 1}, {"size": 4097}, {"payload": b""}])
def test_inconsistent_header(changes):
    text = split_envelope(b"x" * 60)[0]
    with pytest.raises(FragmentError):
        decode_fragment(altered(text, **changes))


def test_conflict_poison_and_content_hash_verification():
    first, second = split_envelope(b"x" * 60)
    receiver = FragmentAssembler()
    assert receiver.accept(PEER, first) is None
    with pytest.raises(FragmentError):
        receiver.accept(PEER, altered(first, payload=b"y" * 48))
    assert receiver.accept(PEER, second) is None
    receiver = FragmentAssembler()
    assert receiver.accept(PEER, altered(first, payload=b"y" * 48)) is None
    with pytest.raises(FragmentError):
        receiver.accept(PEER, second)


def test_sender_bound_and_per_peer_capacity():
    receiver = FragmentAssembler()
    first, last = split_envelope(b"a" * 60)
    assert receiver.accept(PEER, first) is None
    assert receiver.accept(b"q" * 32, last) is None
    assert receiver.accept(PEER, split_envelope(b"b" * 60)[0]) is None
    with pytest.raises(FragmentError):
        receiver.accept(PEER, split_envelope(b"c" * 60)[0])
    assert receiver.accept(PEER, last) == b"a" * 60


def test_expiry_not_extended_by_duplicates():
    clock = [0]
    receiver = FragmentAssembler(clock=lambda: clock[0])
    first, last = split_envelope(b"a" * 60)
    assert receiver.accept(PEER, first) is None
    clock[0] = ASSEMBLY_SECONDS - 1
    assert receiver.accept(PEER, first) is None
    clock[0] = ASSEMBLY_SECONDS
    assert receiver.accept(PEER, last) is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, True])
def test_bad_clock_permanently_closes(bad):
    clock = [2]
    receiver = FragmentAssembler(clock=lambda: clock[0])
    text = split_envelope(b"a")[0]
    receiver.accept(PEER, text)
    clock[0] = bad
    with pytest.raises(FragmentError):
        receiver.accept(PEER, text)
    clock[0] = 3
    with pytest.raises(FragmentError):
        receiver.accept(PEER, text)


def test_concurrent_completion_only_once_and_close():
    receiver = FragmentAssembler()
    text = split_envelope(b"a")[0]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: receiver.accept(PEER, text), range(16)))
    assert results.count(b"a") == 1
    receiver.close()
    with pytest.raises(FragmentError):
        receiver.accept(PEER, text)


def test_global_assembly_and_terminal_capacity_are_bounded():
    receiver = FragmentAssembler()
    text = split_envelope(b"a" * 60)[0]
    for peer in range(8):
        assert receiver.accept(bytes([peer]) * 32, text) is None
    with pytest.raises(FragmentError):
        receiver.accept(b"z" * 32, text)
    receiver = FragmentAssembler()
    for number in range(64):
        payload = bytes([number])
        assert receiver.accept(bytes([number // 8]) * 32, split_envelope(payload)[0]) == payload
    with pytest.raises(FragmentError):
        receiver.accept(PEER, split_envelope(b"new")[0])


def test_noncanonical_base64_padding_rejected():
    text = split_envelope(b"a")[0]
    with pytest.raises(FragmentError):
        decode_fragment(text + "=")


def test_backwards_clock_permanently_closes():
    clock = [20]
    receiver = FragmentAssembler(clock=lambda: clock[0])
    text = split_envelope(b"a" * 60)[0]
    receiver.accept(PEER, text)
    clock[0] = 19
    with pytest.raises(FragmentError):
        receiver.accept(PEER, text)
    clock[0] = 21
    with pytest.raises(FragmentError):
        receiver.accept(PEER, text)


def test_terminal_flood_is_limited_to_one_peer():
    receiver = FragmentAssembler()
    for number in range(8):
        receiver.accept(PEER, split_envelope(bytes([number]))[0])
    with pytest.raises(FragmentError):
        receiver.accept(PEER, split_envelope(b"ninth")[0])
    assert receiver.accept(b"q" * 32, split_envelope(b"other")[0]) == b"other"


def test_ingress_bucket_limits_duplicates_and_refills():
    clock = [0]
    receiver = FragmentAssembler(clock=lambda: clock[0])
    text = split_envelope(b"a")[0]
    for _ in range(INGRESS_BURST):
        receiver.accept(PEER, text)
    with pytest.raises(FragmentError):
        receiver.accept(PEER, text)
    assert receiver.accept(b"q" * 32, text) == b"a"
    clock[0] = 1
    assert receiver.accept(PEER, text) is None
