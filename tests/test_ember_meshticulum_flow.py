# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Acceptance checks for Ember's isolated Meshticulum flow candidate (MT05).

Revision 2. The tests below the original seventeen are the discriminating
negative controls requested by the independent review of revision 1
(work/rook-meshticulum-mt05-review-20261004/, 17 RED / 3 GREEN on the frozen r1
bytes). Each carries one review case; r1 is preserved in
work/ember-meshticulum-mt05-20261004/frozen-r1/ so the repair stays auditable.

A simulated carrier with deterministic faults, a bounded reliable flow, and a
fair multiplexer. No radio, no socket, no broker, no runtime import, no thread
and no wall-clock assertion: every scenario advances a virtual clock, so the
same seed produces the same deliveries on every run.

Run from the repository root:

    PYTHONPATH=.:software/runtime/src python3 -m pytest scripts/tests/test_ember_meshticulum_flow.py -q
"""
from __future__ import annotations

import re
import struct
import types
import zlib
from pathlib import Path
import sys

import pytest

# The reserved candidate directory keeps its hyphenated path boundary, so it is
# not a valid Python identifier. Register the package chain synthetically, as
# the streaming-speech suite does, rather than renaming the directory or adding
# __init__.py markers outside the reserved paths.
ROOT = Path(__file__).resolve().parents[1]
_CANDIDATE = ROOT / "src/meshticulum/flow"

from meshticulum.flow.carrier import (  # noqa: E402
    LinkProfile, SimLink)
from meshticulum.flow.flow import (  # noqa: E402
    ACK_BYTES, GRANT_REV_MAX, GRANT_REV_MIN, MAGIC, SACK_BITS, FlowError, FlowLimits, FlowState,
    ReliableFlow, StartupGrant, admitted_payload_bound, build_ack_frame,
    build_data_frame, data_frame_fits, frames_from_byte_grant, parse_frame,
    remote_startup_credit)
from meshticulum.flow.mux import FlowMux, run  # noqa: E402

DATA = bytes(range(256)) * 4
FRAME = FlowLimits().frame_payload_bytes


def link_with(forward=None, reverse=None, *, seed=1):
    forward = forward or LinkProfile("host", 2000.0, one_way_delay_s=0.02)
    reverse = reverse or LinkProfile("host-rev", 2000.0, one_way_delay_s=0.02)
    return SimLink(forward, reverse, seed=seed)


def shared_startup(limits=None):
    """The fixture's identical startup snapshot for both endpoints.

    The decision permits an isolated fixture to establish one shared startup
    snapshot rather than making the sender wait for a startup grant on every
    test. C0 comes from the *receiver's* own bounds - free receive bytes over the
    fixed admitted quantum, reorder slots and the agreed window - because a
    sender is never allowed to read remote room out of its own local limits.
    """
    limits = limits or FlowLimits()
    return StartupGrant(credit_frames=remote_startup_credit(limits))


def flow(endpoint, link, limits=None, **kwargs):
    """One endpoint with the fixture's shared startup snapshot established."""
    kwargs.setdefault("startup", shared_startup(limits))
    return ReliableFlow(endpoint, link, limits, **kwargs)


def pair(link, limits=None, *, flow_id=1, generation=1):
    tx = flow("a", link, limits, flow_id=flow_id, generation=generation, receive=False)
    rx = flow("b", link, limits, flow_id=flow_id, generation=generation,
              sends=False, receive=True)
    return tx, rx, [tx, rx]


def resign(body: bytes) -> bytes:
    """Re-attach a valid CRC, so a malformed field is rejected on its own merits."""
    return body + struct.pack("!I", zlib.crc32(body) & 0xFFFFFFFF)


def next_grant(tx, step: int = 1) -> int:
    """A revision strictly ahead of whatever this sender currently holds.

    A hand-built acknowledgement has to say which statement it is. Reusing the
    revision the endpoint already holds would make it a *conflicting* equal
    revision, which revision 6 refuses without mutation - a different claim from
    the one these cases are making.
    """
    held = tx._peer_grant_rev
    return 1 if held is None else held + step


# -- reconstruction and bounded failure ---------------------------------


def test_clean_carrier_reconstructs_byte_for_byte():
    link = link_with()
    tx, rx, flows = pair(link)
    tx.send(DATA)
    run(link, flows, deadline_s=60.0, until_settled=True)

    assert tx.state is FlowState.COMPLETE
    assert rx.state is FlowState.COMPLETE
    assert rx.delivered == DATA
    assert tx.settled()
    assert tx.retransmits == 0
    assert link.stats.sent_by_endpoint["a"] == len(DATA) // FRAME


def test_loss_duplicate_and_reorder_still_reconstruct():
    forward = LinkProfile("lossy", 2000.0, one_way_delay_s=0.02, jitter_s=0.02,
                          loss_p=0.15, duplicate_p=0.10, reorder_p=0.20)
    reverse = LinkProfile("lossy-rev", 2000.0, one_way_delay_s=0.02, jitter_s=0.02,
                          loss_p=0.10, reorder_p=0.10)
    link = SimLink(forward, reverse, seed=7)
    tx, rx, flows = pair(link)
    tx.send(DATA)
    run(link, flows, deadline_s=120.0, until_settled=True)

    assert link.stats.dropped > 0
    assert link.stats.duplicated > 0
    assert tx.retransmits > 0
    assert rx.delivered == DATA
    assert tx.state is FlowState.COMPLETE


def test_corrupt_frames_are_refused_by_the_integrity_check():
    forward = LinkProfile("noisy", 4000.0, one_way_delay_s=0.01, corrupt_p=0.25)
    link = SimLink(forward, LinkProfile("noisy-rev", 4000.0, one_way_delay_s=0.01), seed=3)
    tx, rx, flows = pair(link)
    tx.send(DATA)
    run(link, flows, deadline_s=120.0, until_settled=True)

    assert link.stats.corrupted > 0
    assert rx.corrupt_rejected > 0
    assert rx.delivered == DATA
    assert tx.state is FlowState.COMPLETE


def test_unrecoverable_loss_fails_bounded_and_honestly():
    forward = LinkProfile("dead", 2000.0, one_way_delay_s=0.01, loss_p=1.0)
    link = SimLink(forward, LinkProfile("dead-rev", 2000.0, one_way_delay_s=0.01), seed=1)
    tx, rx, flows = pair(link)
    tx.send(DATA)
    stopped_at = run(link, flows, deadline_s=600.0, until_settled=True)

    assert tx.state is FlowState.FAILED
    assert tx.failure == "retry_ceiling"
    assert stopped_at < 600.0
    assert rx.delivered == b""
    assert not tx.complete


# -- flow control, ceilings and memory ----------------------------------


def test_receiver_credit_exhausts_and_recovers():
    limits = FlowLimits(frame_payload_bytes=64, window_frames=8, initial_credit_frames=8,
                        max_reassembly_frames=8, max_queued_bytes=4096)
    link = link_with()
    tx, rx, flows = pair(link, limits)
    data = bytes(range(256)) * 4
    rx.stall()
    tx.send(data)
    run(link, flows, deadline_s=1.0)

    assert rx.buffered_frames() == limits.max_reassembly_frames
    assert tx.in_flight() <= limits.window_frames
    assert tx.queued_bytes() == len(data) - limits.window_frames * limits.frame_payload_bytes
    assert rx.delivered == b""

    rx.unstall()
    run(link, flows, deadline_s=120.0, until_settled=True)
    assert rx.delivered == data
    assert rx.state is FlowState.COMPLETE
    assert tx.state is FlowState.COMPLETE


def test_queues_and_reassembly_stay_bounded_under_a_stalled_peer():
    limits = FlowLimits(frame_payload_bytes=64, window_frames=8, initial_credit_frames=8,
                        max_reassembly_frames=8, max_queued_bytes=4096, max_retries=6)
    forward = LinkProfile("bounded", 2000.0, one_way_delay_s=0.02, queue_bytes=512)
    link = SimLink(forward, LinkProfile("bounded-rev", 2000.0, one_way_delay_s=0.02), seed=4)
    tx, rx, flows = pair(link, limits)
    data = bytes(4096)
    rx.stall()
    tx.send(data)
    run(link, flows, deadline_s=120.0, until_settled=True)

    assert tx.state is FlowState.FAILED
    assert tx.failure == "retry_ceiling"
    assert link.stats.refused_queue > 0
    assert tx.queued_bytes() <= limits.max_queued_bytes
    assert tx.in_flight() <= limits.window_frames
    assert tx.peak_buffered_frames <= limits.max_reassembly_frames
    assert rx.buffered_frames() <= limits.max_reassembly_frames
    assert rx.peak_delivered_bytes <= limits.max_delivered_bytes
    assert link.queued_bytes("a") <= forward.queue_bytes
    assert rx.delivered != data


def test_queue_ceiling_refuses_an_oversized_send():
    limits = FlowLimits(max_queued_bytes=256)
    link = link_with()
    tx, _, _ = pair(link, limits)
    with pytest.raises(FlowError):
        tx.send(bytes(257))


# -- generations, restarts and honest teardown --------------------------


def test_stale_generation_is_refused_and_never_resurrected():
    link = link_with()
    limits = FlowLimits()
    rx = flow("b", link, limits, flow_id=1, generation=10, sends=False, receive=True)
    stale = build_data_frame(flow_id=1, generation=9, seq=0, total=1, last=True,
                             payload=b"old-session", limits=limits)
    assert link.send("a", stale)
    link.advance(1.0)
    rx.pump(link.now)

    assert rx.stale_rejected == 1
    assert rx.delivered == b""
    assert rx.state is FlowState.OPEN
    assert rx.buffered_frames() == 0


def test_a_foreign_flow_id_is_not_admitted():
    link = link_with()
    limits = FlowLimits()
    rx = flow("b", link, limits, flow_id=4, generation=1, sends=False, receive=True)
    other = build_data_frame(flow_id=5, generation=1, seq=0, total=1, last=True,
                             payload=b"someone-else", limits=limits)
    assert link.send("a", other)
    link.advance(1.0)
    rx.pump(link.now)

    assert rx.foreign_frames == 1
    assert rx.delivered == b""


def test_peer_restart_is_reported_not_hidden():
    link = link_with()
    tx, rx, flows = pair(link)
    tx.send(DATA)
    run(link, flows, deadline_s=0.3)

    assert 0 < len(rx.delivered) < len(DATA)
    delivered_before = rx.delivered
    link.restart("b")
    run(link, flows, deadline_s=5.0)

    assert tx.state is FlowState.FAILED
    assert tx.failure == "peer_restarted"
    assert rx.delivered.startswith(delivered_before)
    assert rx.delivered != DATA
    assert not tx.complete


def test_cancel_drains_and_the_sender_goes_quiet():
    link = link_with()
    limits = FlowLimits(frame_payload_bytes=64, window_frames=4, initial_credit_frames=4,
                        max_reassembly_frames=16)
    tx, rx, flows = pair(link, limits)
    data = DATA * 6
    tx.send(data)
    run(link, flows, deadline_s=0.2)

    tx.cancel()
    assert tx.state is FlowState.CANCELLED
    assert tx.queued_bytes() == 0
    assert tx.in_flight() == 0
    sent_by_sender = link.stats.sent_by_endpoint["a"]
    run(link, flows, deadline_s=5.0)

    assert link.stats.sent_by_endpoint["a"] == sent_by_sender
    assert link.queued_bytes("a") == 0
    assert not tx.complete
    # Frames already handed to the radio still arrive; what a cancel prevents is
    # everything the sender had not yet transmitted.
    assert len(rx.delivered) < len(data)


def test_new_generation_after_restart_does_not_replay_the_old_session():
    link = link_with()
    tx, rx, flows = pair(link)
    tx.send(DATA)
    run(link, flows, deadline_s=60.0, until_settled=True)
    assert rx.delivered == DATA

    link.restart("b")
    new_tx, new_rx, new_flows = pair(link, generation=2)
    run(link, new_flows, deadline_s=1.0)

    assert link.incarnation("b") == 1
    assert new_rx.delivered == b""
    assert new_rx.state is FlowState.OPEN
    assert new_tx.state is FlowState.OPEN


# -- multiplexing, accounting and carrier changes -----------------------


def test_one_stalled_flow_does_not_stall_another():
    limits = FlowLimits(frame_payload_bytes=64, window_frames=8, initial_credit_frames=8,
                        max_reassembly_frames=8, max_queued_bytes=4096)
    link = link_with(seed=13)
    stalled_tx = flow("a", link, limits, flow_id=1, generation=1, receive=False)
    stalled_rx = flow("b", link, limits, flow_id=1, generation=1,
                              sends=False, receive=True)
    healthy_tx = flow("a", link, limits, flow_id=2, generation=1, receive=False)
    healthy_rx = flow("b", link, limits, flow_id=2, generation=1,
                              sends=False, receive=True)
    mux = FlowMux(link, [stalled_tx, stalled_rx, healthy_tx, healthy_rx], quantum_frames=2)
    small = b"slow" * 64
    healthy_payload = bytes(range(256)) * 4
    stalled_rx.stall()
    stalled_tx.send(small)
    healthy_tx.send(healthy_payload)

    mux.run(deadline_s=5.0)
    assert healthy_tx.state is FlowState.COMPLETE
    assert healthy_rx.delivered == healthy_payload
    assert stalled_tx.state is FlowState.OPEN
    assert stalled_rx.buffered_frames() <= limits.max_reassembly_frames

    mux.run(deadline_s=60.0, until_settled=True)
    assert stalled_tx.state is FlowState.FAILED
    assert stalled_tx.failure == "retry_ceiling"


def test_same_generation_flows_are_demultiplexed_by_flow_id():
    limits = FlowLimits(frame_payload_bytes=64, window_frames=8, initial_credit_frames=8,
                        max_reassembly_frames=16, max_queued_bytes=4096)
    link = link_with(seed=29)
    one_tx = flow("a", link, limits, flow_id=1, generation=7, receive=False)
    one_rx = flow("b", link, limits, flow_id=1, generation=7, sends=False, receive=True)
    two_tx = flow("a", link, limits, flow_id=2, generation=7, receive=False)
    two_rx = flow("b", link, limits, flow_id=2, generation=7, sends=False, receive=True)
    mux = FlowMux(link, [one_tx, one_rx, two_tx, two_rx], quantum_frames=2)
    first = bytes(range(256)) * 2
    second = bytes(range(255, -1, -1)) * 2
    one_tx.send(first)
    two_tx.send(second)
    mux.run(deadline_s=60.0, until_settled=True)

    assert one_rx.delivered == first
    assert two_rx.delivered == second
    assert one_rx.foreign_frames == 0
    assert two_rx.foreign_frames == 0
    assert one_rx.stale_rejected == 0
    assert two_rx.stale_rejected == 0


def test_reverse_ack_budget_is_separate_from_firmware_ack_cost():
    forward = LinkProfile("radio", 4000.0, one_way_delay_s=0.01, firmware_ack_bytes=12)
    reverse = LinkProfile("radio-rev", 4000.0, one_way_delay_s=0.01,
                          ack_bytes_per_second=40.0, queue_bytes=64)
    link = SimLink(forward, reverse, seed=5)
    limits = FlowLimits(frame_payload_bytes=64, window_frames=4, initial_credit_frames=4,
                        max_reassembly_frames=16)
    tx, rx, flows = pair(link, limits)
    tx.send(DATA)
    run(link, flows, deadline_s=120.0, until_settled=True)

    assert rx.delivered == DATA
    assert link.stats.refused_ack_budget > 0
    assert link.stats.ack_frames > 0
    # Only data frames pay the radio's own per-frame acknowledgement cost; the
    # overlay's ACK frames add nothing to it. A host scheduler that aggregates
    # ACKs cannot claim to have removed the firmware cost.
    assert link.stats.firmware_ack_bytes == link.stats.sent_by_endpoint["a"] * 12
    assert link.stats.firmware_ack_bytes < link.stats.sent_frames * 12


def test_sequence_wraparound_reconstructs():
    limits = FlowLimits(frame_payload_bytes=16, window_frames=8, initial_credit_frames=8,
                        max_reassembly_frames=8, max_queued_bytes=4096, max_retries=12,
                        seq_modulus=32)
    link = link_with()
    tx, rx, flows = pair(link, limits)
    data = bytes(range(256)) * 6
    tx.send(data)
    run(link, flows, deadline_s=120.0, until_settled=True)

    assert len(data) // 16 == 96
    assert rx.delivered == data
    assert tx.state is FlowState.COMPLETE
    assert rx.out_of_window == 0


def test_route_switch_mid_transfer_renegotiates_and_completes():
    link = link_with(seed=11)
    limits = FlowLimits(frame_payload_bytes=64, window_frames=4, initial_credit_frames=4,
                        max_reassembly_frames=16, max_retries=10)
    tx, rx, flows = pair(link, limits)
    data = bytes(range(256)) * 8
    tx.send(data)
    run(link, flows, deadline_s=0.1)
    assert 0 < len(rx.delivered) < len(data)

    link.switch_profile("a", LinkProfile("slower-lane", 400.0, one_way_delay_s=0.4))
    tx.carrier_changed()
    rx.carrier_changed()
    run(link, flows, deadline_s=180.0, until_settled=True)

    assert rx.delivered == data
    assert tx.state is FlowState.COMPLETE
    assert tx.failure is None


def test_the_harness_is_deterministic():
    def scenario():
        forward = LinkProfile("d", 2000.0, one_way_delay_s=0.02, jitter_s=0.01,
                              loss_p=0.2, duplicate_p=0.1, reorder_p=0.1, corrupt_p=0.05)
        link = SimLink(forward, LinkProfile("d-rev", 2000.0, one_way_delay_s=0.02), seed=99)
        tx, rx, flows = pair(link)
        tx.send(DATA)
        run(link, flows, deadline_s=120.0, until_settled=True)
        return link.stats.as_dict(), rx.delivered, tx.state

    assert scenario() == scenario()


def test_candidate_stays_isolated_from_the_runtime():
    """The candidate must not grow a runtime, radio or socket dependency by accident."""
    sources = sorted(_CANDIDATE.glob("*.py"))
    assert sources
    forbidden = ("sage_lantern", "socket", "serial", "http", "urllib", "requests", "ssl")
    for path in sources:
        text = path.read_text()
        # Relative imports inside the candidate are fine; absolute ones are not.
        imports = re.findall(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", text, re.MULTILINE)
        for module in imports:
            assert not module.startswith(forbidden), f"{path.name} imports {module}"


# -- review-driven negative controls (revision 2) -----------------------
#
# One test per independent review case. These are the tests that were RED
# against revision 1; they are kept as the regression for each repair.


def test_lost_final_ack_is_repaired_by_duplicate_reassertion():
    """Review case 1: a dropped final ACK must not strand the sender forever."""
    forward = LinkProfile("fwd", 2000.0, one_way_delay_s=0.02)
    down = LinkProfile("rev-down", 2000.0, one_way_delay_s=0.02, loss_p=1.0)
    link = SimLink(forward, down, seed=17)
    tx, rx, flows = pair(link)
    data = bytes(range(64))
    tx.send(data)
    run(link, flows, deadline_s=3.0)

    assert rx.state is FlowState.COMPLETE
    assert rx.delivered == data
    assert tx.state is FlowState.OPEN
    assert tx.in_flight() == 1

    link.switch_profile("b", LinkProfile("rev-clean", 2000.0, one_way_delay_s=0.02))
    tx.carrier_changed()
    rx.carrier_changed()
    run(link, flows, deadline_s=60.0, until_settled=True)

    assert rx.duplicate_prompted_acks >= 1
    assert tx.state is FlowState.COMPLETE


def test_terminal_receive_state_fences_late_same_generation_data():
    """Review case 2: completed and cancelled receivers must not be revived."""
    link = link_with()
    limits = FlowLimits()
    rx = flow("b", link, limits, flow_id=1, generation=5, sends=False, receive=True)
    data = bytes(range(64))
    first = build_data_frame(flow_id=1, generation=5, seq=0, total=1, last=True,
                             payload=data, limits=limits)
    link.send("a", first)
    run(link, [rx], deadline_s=0.5)
    assert rx.state is FlowState.COMPLETE
    assert rx.delivered == data

    late = build_data_frame(flow_id=1, generation=5, seq=1, total=2, last=True,
                            payload=b"x" * 64, limits=limits)
    link.send("a", late)
    run(link, [rx], deadline_s=1.0)

    assert rx.state is FlowState.COMPLETE
    assert rx.delivered == data
    assert rx.post_terminal_rejected == 1


def test_cancelled_flow_cannot_be_revived_by_late_frames():
    """Review case 2, cancellation half."""
    link = link_with()
    limits = FlowLimits()
    tx, rx, flows = pair(link, limits)
    rx.cancel()
    late = build_data_frame(flow_id=1, generation=1, seq=0, total=1, last=True,
                            payload=b"x" * 64, limits=limits)
    link.send("a", late)
    run(link, flows, deadline_s=1.0)

    assert rx.state is FlowState.CANCELLED
    assert rx.delivered == b""
    assert rx.post_terminal_rejected == 1


def test_failed_receiver_cannot_be_revived_by_late_frames():
    """Review case 2, failure half: a receive ceiling failure is terminal too."""
    link = link_with()
    limits = FlowLimits(frame_payload_bytes=64, max_delivered_bytes=64, max_total_frames=8)
    rx = flow("b", link, limits, flow_id=1, generation=1, sends=False, receive=True)
    for seq in (0, 1):
        frame = build_data_frame(flow_id=1, generation=1, seq=seq, total=8, last=False,
                                 payload=b"a" * 64, limits=limits)
        link.send("a", frame)
        link.advance(0.1)
        rx.pump(link.now)

    assert rx.recv_state is FlowState.FAILED
    assert rx.failure == "receive_ceiling"
    assert rx.delivered == b"a" * 64

    rx.carrier_changed()
    late = build_data_frame(flow_id=1, generation=1, seq=2, total=8, last=False,
                            payload=b"c" * 64, limits=limits)
    link.send("a", late)
    link.advance(0.1)
    rx.pump(link.now)

    assert rx.recv_state is FlowState.FAILED
    assert rx.delivered == b"a" * 64
    assert rx.post_terminal_rejected == 1


def test_impossible_cumulative_ack_cannot_fabricate_completion():
    """Review case 3: an ACK may not account for sequence numbers never sent."""
    link = link_with()
    limits = FlowLimits()
    tx = flow("a", link, limits, flow_id=3, generation=1, receive=False)
    tx.send(DATA)
    run(link, [tx], deadline_s=0.5)
    assert tx.in_flight() == len(DATA) // FRAME

    impossible = build_ack_frame(flow_id=3, generation=1, ack_seq=100, credit=16, sack=0,
                                 limits=limits, grant_revision=next_grant(tx))
    link.send("b", impossible, kind="ack")
    run(link, [tx], deadline_s=1.0)
    assert tx.impossible_acks == 1
    assert tx.in_flight() == len(DATA) // FRAME
    assert tx.state is FlowState.OPEN

    # A well-ordered sequence with an impossible credit claim is refused too:
    # credit is bounded by the window this endpoint negotiated, not by the peer.
    roomy = FlowLimits(seq_modulus=1 << 16, window_frames=16, max_reassembly_frames=16384)
    greedy = build_ack_frame(flow_id=3, generation=1, ack_seq=len(DATA) // FRAME, credit=16384,
                             sack=0, limits=roomy, grant_revision=next_grant(tx))
    link.send("b", greedy, kind="ack")
    run(link, [tx], deadline_s=1.25)
    assert tx.impossible_acks == 2
    assert tx.state is FlowState.OPEN

    valid = build_ack_frame(flow_id=3, generation=1, ack_seq=len(DATA) // FRAME, credit=16,
                            sack=0, limits=limits, grant_revision=next_grant(tx))
    link.send("b", valid, kind="ack")
    run(link, [tx], deadline_s=1.5)
    assert tx.state is FlowState.COMPLETE


def test_directions_complete_independently():
    """Review case 4: one direction finishing must not finish the other."""
    link = link_with(seed=23)
    limits = FlowLimits(frame_payload_bytes=64, window_frames=4, initial_credit_frames=4,
                        max_reassembly_frames=16, max_queued_bytes=8192)
    a = flow("a", link, limits, flow_id=1, generation=1, sends=True, receive=True)
    b = flow("b", link, limits, flow_id=1, generation=1, sends=True, receive=True)
    flows = [a, b]
    short = bytes(range(64))
    long_payload = bytes(range(256)) * 32
    a.send(short)
    b.send(long_payload)
    run(link, flows, deadline_s=1.0)

    assert b.recv_state is FlowState.COMPLETE
    assert b.delivered == short
    assert b.queued_bytes() > 0
    assert b.state is FlowState.OPEN
    assert a.recv_state is FlowState.OPEN
    assert a.state is FlowState.OPEN

    run(link, flows, deadline_s=300.0, until_settled=True)
    assert b.delivered == short
    assert a.delivered == long_payload
    assert a.state is FlowState.COMPLETE
    assert b.state is FlowState.COMPLETE


def test_oversized_payload_is_refused():
    """Review case 5a: a payload beyond the negotiated frame size is not admitted."""
    link = link_with()
    limits = FlowLimits(frame_payload_bytes=64)
    rx = flow("b", link, limits, flow_id=1, generation=1, sends=False, receive=True)
    permissive = FlowLimits(frame_payload_bytes=200)
    big = build_data_frame(flow_id=1, generation=1, seq=0, total=1, last=True,
                           payload=b"z" * 200, limits=permissive)
    link.send("a", big)
    run(link, [rx], deadline_s=0.5)

    assert rx.oversized_rejected == 1
    assert rx.delivered == b""
    assert rx.state is FlowState.OPEN


def test_unconsumed_delivery_is_bounded_and_consume_returns_credit():
    """Review case 5b: reorder ceilings alone do not bound delivered retention."""
    limits = FlowLimits(frame_payload_bytes=64, window_frames=4, initial_credit_frames=4,
                        max_reassembly_frames=8, max_delivered_bytes=256, max_queued_bytes=4096)
    link = link_with()
    tx, rx, flows = pair(link, limits)
    data = bytes(range(256)) * 2
    tx.send(data)
    run(link, flows, deadline_s=2.0)

    assert len(rx.delivered) <= limits.max_delivered_bytes
    assert rx.peak_delivered_bytes <= limits.max_delivered_bytes
    assert rx.delivered == data[:len(rx.delivered)]
    assert len(rx.delivered) < len(data)

    taken = len(rx.delivered)
    assert rx.consume(taken) == data[:taken]
    run(link, flows, deadline_s=60.0, until_settled=True)

    assert rx.delivered == data[taken:]
    assert tx.state is FlowState.COMPLETE


def test_invalid_total_is_refused():
    """Review case 5c: a frame count outside the negotiated schema is refused."""
    link = link_with()
    limits = FlowLimits(frame_payload_bytes=64, max_total_frames=4)
    rx = flow("b", link, limits, flow_id=1, generation=1, sends=False, receive=True)
    permissive = FlowLimits(frame_payload_bytes=64, max_total_frames=5)
    frame = build_data_frame(flow_id=1, generation=1, seq=0, total=5, last=False,
                             payload=b"a" * 64, limits=permissive)
    link.send("a", frame)
    run(link, [rx], deadline_s=0.5)

    assert rx.invalid_total == 1
    assert rx.delivered == b""

    # FIN semantics: a frame claiming LAST before the declared end is refused
    # before any state is mutated.
    early_fin = build_data_frame(flow_id=1, generation=1, seq=0, total=3, last=True,
                                 payload=b"a" * 64, limits=permissive)
    link.send("a", early_fin)
    run(link, [rx], deadline_s=1.0)
    assert rx.invalid_fin == 1
    assert rx.delivered == b""


def test_generation_and_flow_id_out_of_range_are_refused_not_wrapped():
    """Control: revision 1 normalised generation 257 to 1 and admitted stale data."""
    link = link_with()
    limits = FlowLimits()
    with pytest.raises(FlowError):
        flow("a", link, limits, generation=257, receive=False)
    with pytest.raises(FlowError):
        flow("a", link, limits, flow_id=256, receive=False)
    with pytest.raises(FlowError):
        build_data_frame(flow_id=1, generation=256, seq=0, total=1, last=True, payload=b"x",
                         limits=limits)


def test_strict_parsing_rejects_malformed_frames():
    """Controls: sequence normalisation, unknown flags and bitmap length."""
    small = FlowLimits(frame_payload_bytes=8, seq_modulus=32, window_frames=8,
                       max_reassembly_frames=8, initial_credit_frames=8, max_delivered_bytes=64)
    good = build_data_frame(flow_id=1, generation=1, seq=5, total=9, last=False, payload=b"abc",
                            modulus=32, limits=small)
    assert parse_frame(good, 32)["seq"] == 5

    body = bytearray(good)
    body[6] |= 0x80  # an unknown flag, with a valid CRC
    assert parse_frame(resign(bytes(body[:-4])), 32) is None

    wide = build_data_frame(flow_id=1, generation=1, seq=32, total=2, last=False, payload=b"a",
                            modulus=64, limits=FlowLimits(seq_modulus=64, frame_payload_bytes=8,
                                                          window_frames=8, max_reassembly_frames=8,
                                                          initial_credit_frames=8))
    assert parse_frame(wide, 32) is None

    zero_total = build_data_frame(flow_id=1, generation=1, seq=0, total=0, last=True, payload=b"a",
                                  modulus=32, limits=small)
    assert parse_frame(zero_total, 32) is None

    ack = build_ack_frame(flow_id=1, generation=1, ack_seq=1, credit=4, sack=1, modulus=32,
                          limits=small)
    assert parse_frame(ack, 32)["credit"] == 4
    assert parse_frame(resign(ack[:-5]), 32) is None
    assert parse_frame(good[:5], 32) is None
    assert parse_frame(b"XXX" + good[3:], 32) is None


def test_invalid_limits_are_refused():
    with pytest.raises(FlowError):
        FlowLimits(frame_payload_bytes=0)
    with pytest.raises(FlowError):
        FlowLimits(window_frames=0)
    with pytest.raises(FlowError):
        FlowLimits(seq_modulus=32, window_frames=16)
    with pytest.raises(FlowError):
        FlowLimits(seq_modulus=32, max_reassembly_frames=16)
    with pytest.raises(FlowError):
        FlowLimits(min_rto_s=5.0, max_rto_s=1.0)
    with pytest.raises(FlowError):
        FlowLimits(frame_payload_bytes=64, max_delivered_bytes=8)


def test_carrier_queue_accounts_for_duplicate_copies():
    """Review case 9: a 40-byte frame on a 64-byte queue with forced duplication."""
    reverse = LinkProfile("dup-rev", 2000.0, one_way_delay_s=0.01)
    duplicating = LinkProfile("dup", 2000.0, one_way_delay_s=0.01, duplicate_p=1.0,
                              queue_bytes=64)
    link = SimLink(duplicating, reverse, seed=2)
    assert link.send("a", b"f" * 40) is False
    assert link.stats.refused_queue == 1
    assert link.queued_bytes("a") == 0

    # The paired control: the same frame fits when it is not duplicated.
    clean = LinkProfile("clean", 2000.0, one_way_delay_s=0.01, queue_bytes=64)
    other = SimLink(clean, LinkProfile("clean-rev", 2000.0, one_way_delay_s=0.01), seed=2)
    assert other.send("a", b"f" * 40) is True
    assert other.queued_bytes("a") == 40


def test_duplicate_flow_registration_is_refused():
    """Seam limit L01: colliding identities must be rejected, not silently shared."""
    link = link_with()
    limits = FlowLimits()
    first = flow("b", link, limits, flow_id=1, generation=1, sends=False, receive=True)
    second = flow("b", link, limits, flow_id=1, generation=1, sends=False, receive=True)
    with pytest.raises(FlowError):
        FlowMux(link, [first, second])

    distinct = flow("b", link, limits, flow_id=1, generation=2, sends=False, receive=True)
    assert isinstance(FlowMux(link, [first, distinct]), FlowMux)


def test_repeated_send_is_refused():
    """Control: revision 1 silently recomputed the frame count on a second send()."""
    link = link_with()
    tx, _, _ = pair(link)
    tx.send(b"a" * 64)
    with pytest.raises(FlowError):
        tx.send(b"b" * 64)


# -- review-driven negative controls (revision 4) ------------------------
#
# One test per R3 finding (work/rook-meshticulum-mt05-r3-review-20261004/).
# The independent probes for these same cases are RED on revision 3 and GREEN
# on revision 4; these keep the lane's own regressions alongside theirs.


def test_lost_credit_grant_cannot_idle_the_transfer():
    """R3-F01: the grant that restarts a sender can be the frame the link loses."""
    limits = FlowLimits(frame_payload_bytes=64, window_frames=4, initial_credit_frames=4,
                        max_reassembly_frames=8, max_delivered_bytes=256)
    clean = LinkProfile("clean", 2000.0, one_way_delay_s=0.02)
    down = LinkProfile("down", 2000.0, one_way_delay_s=0.02, loss_p=1.0)
    link = SimLink(clean, clean, seed=31)
    tx, rx, flows = pair(link, limits)
    tx.send(bytes(range(256)) * 2)
    run(link, flows, deadline_s=2.0)
    assert tx.in_flight() == 0 and tx.queued_bytes() > 0, "credit exhausted, all acknowledged"

    rx.consume(64)
    link.switch_profile("b", down)
    run(link, flows, deadline_s=3.0)
    link.switch_profile("b", clean)
    tx.carrier_changed()
    rx.carrier_changed()
    stuck = tx.queued_bytes()
    run(link, flows, deadline_s=60.0)
    assert tx.queued_bytes() < stuck, "a lost grant must not idle the sender forever"


def test_ack_naming_an_unsent_sequence_is_refused_whole():
    """R3-F02: a selective bit may only name data that was actually exposed."""
    link = link_with()
    limits = FlowLimits()
    tx = flow("a", link, limits, flow_id=1, generation=1, receive=False)
    tx.send(bytes(range(256)) * 8)
    run(link, [tx], deadline_s=0.5)
    assert tx.in_flight() == limits.window_frames

    unsent = build_ack_frame(flow_id=1, generation=1, ack_seq=1, credit=16,
                             sack=1 << (SACK_BITS - 16), limits=limits,
                             grant_revision=next_grant(tx))
    link.send("b", unsent, kind="ack")
    run(link, [tx], deadline_s=0.75)
    assert tx.impossible_acks == 1
    assert tx.acked_frames() == 0, "an impossible assertion changes nothing at all"

    # The paired control: a bit for a sequence that was sent is applied.
    sent = build_ack_frame(flow_id=1, generation=1, ack_seq=1, credit=16,
                           sack=1 << (SACK_BITS - 1), limits=limits,
                           grant_revision=next_grant(tx))
    link.send("b", sent, kind="ack")
    run(link, [tx], deadline_s=1.0)
    # Frame 0 cumulatively and frame 2 selectively; the window then refills.
    assert tx.acked_frames() == 2
    assert tx.in_flight() == limits.window_frames


def test_stale_ack_cannot_roll_credit_backward():
    """R3-F03: cumulative progress only moves forward."""
    link = link_with()
    limits = FlowLimits(window_frames=2, initial_credit_frames=2)
    tx = flow("a", link, limits, flow_id=1, generation=1, receive=False)
    tx.send(DATA)
    run(link, [tx], deadline_s=0.5)

    newer = build_ack_frame(flow_id=1, generation=1, ack_seq=2, credit=8, sack=0, limits=limits,
                            grant_revision=next_grant(tx))
    link.send("b", newer, kind="ack")
    run(link, [tx], deadline_s=0.75)
    assert tx.in_flight() > 0, "the grant opened the window"

    stale = build_ack_frame(flow_id=1, generation=1, ack_seq=0, credit=0, sack=0, limits=limits,
                            grant_revision=next_grant(tx))
    link.send("b", stale, kind="ack")
    run(link, [tx], deadline_s=1.0)
    assert tx.stale_acks == 1
    assert tx.in_flight() > 0, "an old acknowledgement may not close the window"


def test_cancel_fences_output_after_a_completed_receive():
    """R3-F04: cancellation is session-wide, completed receive included."""
    link = link_with()
    limits = FlowLimits()
    tx, rx, flows = pair(link, limits)
    tx.send(bytes(range(64)))
    run(link, flows, deadline_s=1.0)
    assert rx.recv_state is FlowState.COMPLETE

    rx.cancel()
    before = link.stats.ack_frames
    duplicate = build_data_frame(flow_id=1, generation=1, seq=0, total=1, last=True,
                                 payload=bytes(range(64)), limits=limits)
    link.send("a", duplicate)
    run(link, flows, deadline_s=2.0)
    assert link.stats.ack_frames == before, "a cancelled session does not answer"
    assert rx.post_terminal_rejected == 1


def test_duplicate_reassertions_obey_the_ack_delay():
    """R3-F05: a completed receiver's reassertions are coalesced like any other."""
    link = link_with()
    limits = FlowLimits(ack_delay_s=0.05)
    tx, rx, flows = pair(link, limits)
    tx.send(bytes(range(64)))
    run(link, flows, deadline_s=1.0)
    assert rx.recv_state is FlowState.COMPLETE

    before = link.stats.ack_frames
    duplicate = build_data_frame(flow_id=1, generation=1, seq=0, total=1, last=True,
                                 payload=bytes(range(64)), limits=limits)
    for _ in range(3):
        link.send("a", duplicate)
    link.advance(0.2)
    rx.pump(link.now)
    assert link.stats.ack_frames == before + 1, "three duplicates in one instant are one ACK"


def test_buffered_bytes_are_reserved_against_advertised_credit():
    """R3-F07: held bytes already occupy the capacity that credit advertises."""
    limits = FlowLimits(frame_payload_bytes=64, window_frames=8, initial_credit_frames=8,
                        max_reassembly_frames=8, max_delivered_bytes=256)
    link = link_with()
    rx = flow("b", link, limits, flow_id=1, generation=1, sends=False, receive=True)
    for seq in (1, 2, 3):
        link.send("a", build_data_frame(flow_id=1, generation=1, seq=seq, total=4, last=False,
                                        payload=b"a" * 64, limits=limits))
    link.advance(0.5)
    rx.pump(link.now)
    assert rx.buffered_frames() == 3
    assert rx._credit() == 1, "192 held bytes leave room for exactly one more frame"


def test_buffered_data_past_the_declared_end_is_refused():
    """R3-F08: admission is against the remaining transfer, not just the window."""
    link = link_with()
    limits = FlowLimits()
    tx, rx, flows = pair(link, limits)
    link.send("a", build_data_frame(flow_id=1, generation=1, seq=2, total=2, last=False,
                                    payload=b"x" * 64, limits=limits))
    run(link, [rx], deadline_s=0.5)
    assert rx.out_of_transfer == 1
    assert rx.buffered_frames() == 0

    for seq in (0, 1):
        link.send("a", build_data_frame(flow_id=1, generation=1, seq=seq, total=2,
                                        last=seq == 1, payload=b"y" * 64, limits=limits))
    run(link, [rx], deadline_s=1.0)
    assert rx.delivered == b"y" * 128, "a declared two-frame transfer yields two frames"


def test_a_wider_ack_bitmap_is_refused():
    """R3-F09: one fixed bitmap width, or the offsets acknowledge the wrong frame."""
    header = struct.pack("!3sBBBHHB", b"MT2", 1, 1, 1, 1, 16, 64)
    wider = resign(header + (1 << 31).to_bytes(8, "big"))
    assert parse_frame(wider) is None


def test_incompatible_and_nonfinite_limits_are_refused():
    """CR-LIMIT-01: a fractional or infinite limit reached the wire packer."""
    for bad in ({"frame_payload_bytes": 1.5}, {"seq_modulus": 65536.5}, {"max_retries": True},
                {"window_frames": True}, {"ack_delay_s": float("inf")},
                {"max_retries": float("inf")}, {"min_rto_s": float("nan")}):
        with pytest.raises(FlowError):
            FlowLimits(**bad)


def test_limits_beyond_the_wire_fields_are_refused():
    """CR-LIMIT-02: a modulus or frame count wider than its 16-bit field."""
    with pytest.raises(FlowError):
        FlowLimits(seq_modulus=1 << 20, window_frames=4, max_reassembly_frames=8)
    with pytest.raises(FlowError):
        FlowLimits(max_total_frames=65536)
    assert FlowLimits(seq_modulus=1 << 16, window_frames=4, max_reassembly_frames=8)
    assert FlowLimits(max_total_frames=65535)


# -- revision 5: credit recovery, session fence and RTT provenance ---------
#
# Rook's r4 review (work/rook-meshticulum-mt05-r4-review-20261004/) holds five
# findings, each with a paired control in his own probe. These are this file's
# controls for the same behaviours, so a later regression is caught by the suite
# that owns the candidate and not only by the reviewer's instrument.


def stalled_grant_loss(*, answer: bool):
    """A receiver whose app consumed, with every credit grant lost.

    Returns the carrier, both endpoints and the delivered bytes with the flow in
    the state R4-F02 describes: queued data, an empty window, nothing in flight.
    """
    forward = LinkProfile("clean", 4000.0, one_way_delay_s=0.01)
    reverse = LinkProfile("clean-rev", 4000.0, one_way_delay_s=0.01)
    link = SimLink(forward, reverse, seed=3)
    limits = FlowLimits(window_frames=4, initial_credit_frames=4, max_reassembly_frames=8,
                        max_delivered_bytes=256, max_queued_bytes=4096)
    tx, rx, flows = pair(link, limits)
    data = bytes(range(256)) * 2
    tx.send(data)
    run(link, flows, deadline_s=2.0)
    assert rx.delivered == data[:256] and tx.queued_bytes() == 256 and tx.in_flight() == 0
    assert tx._peer_credit == 0
    # The granted credit is lost: the reverse link goes dark for the grant and,
    # when `answer` is false, stays dark so the probe is never answered either.
    rx.consume(256)
    link.switch_profile("b", LinkProfile("dark-rev", 4000.0, one_way_delay_s=0.01, loss_p=1))
    rx.pump(link.now)
    if answer:
        link.switch_profile("b", reverse)
    return link, tx, rx, flows, data


def test_a_lost_grant_is_probed_for_rather_than_left_open_forever():
    link, tx, rx, flows, data = stalled_grant_loss(answer=True)
    run(link, flows, deadline_s=60.0, until_settled=True)

    # The recovery is the probe: the sender re-asks with a frame the receiver is
    # known to hold, the duplicate re-assertion answers it with current credit,
    # and the transfer finishes. It is not a retransmission of anything the
    # receiver refused.
    assert tx.zero_window_probes >= 1
    assert tx.state is FlowState.COMPLETE and rx.state is FlowState.COMPLETE
    assert rx.delivered == data[256:]
    assert rx.out_of_transfer == 0


def test_a_closed_window_is_probed_with_backoff_and_never_fails_the_flow():
    # The receiver's application never consumes, so its window is genuinely
    # closed and every answer grants nothing. That is not a fault and must not
    # fail the flow, and it must not be probed once per round trip either.
    forward = LinkProfile("clean", 4000.0, one_way_delay_s=0.01)
    reverse = LinkProfile("clean-rev", 4000.0, one_way_delay_s=0.01)
    link = SimLink(forward, reverse, seed=3)
    limits = FlowLimits(window_frames=4, initial_credit_frames=4, max_reassembly_frames=8,
                        max_delivered_bytes=256, max_queued_bytes=4096)
    tx, rx, flows = pair(link, limits)
    tx.send(bytes(range(256)) * 2)
    run(link, flows, deadline_s=2.0)
    assert tx._peer_credit == 0 and tx.queued_bytes() == 256

    run(link, flows, deadline_s=42.0)
    assert tx.send_state is FlowState.OPEN and tx.failure is None
    # Bounded chatter: with backoff to `max_rto_s`, forty seconds is a handful of
    # probes, not one per round trip.
    assert 2 <= tx.zero_window_probes <= 12, tx.zero_window_probes
    assert tx.retransmits == 0


def test_silence_ends_a_stalled_sender_instead_of_idling_with_no_event():
    link, tx, rx, flows, data = stalled_grant_loss(answer=False)
    run(link, flows, deadline_s=60.0)

    # No answer to a bounded number of probes is a dead peer, not a closed
    # window: the flow ends with a reason rather than sitting open forever.
    assert tx.send_state is FlowState.FAILED
    assert tx.failure == "credit_stalled"
    # The receiver is unharmed and never saw the second half: the failure is the
    # sender's bounded answer to silence, not damage to the receive direction.
    assert rx.recv_state is FlowState.OPEN
    assert rx.delivered == b""


def test_a_completed_receiver_stops_answering_once_the_session_ends():
    forward = LinkProfile("clean", 4000.0, one_way_delay_s=0.01)
    reverse = LinkProfile("clean-rev", 4000.0, one_way_delay_s=0.01)
    link = SimLink(forward, reverse, seed=5)
    limits = FlowLimits(frame_payload_bytes=64, window_frames=2, initial_credit_frames=2)
    tx, rx, flows = pair(link, limits)
    payload = b"x" * 64
    tx.send(payload)
    run(link, flows, deadline_s=5.0, until_settled=True)
    assert rx.recv_state is FlowState.COMPLETE
    duplicate = build_data_frame(flow_id=1, generation=1, seq=0, total=1, last=True,
                                 payload=payload, limits=limits)

    def deliver_duplicate():
        # Past the ACK rate window, then one carrier delivery: a response to a
        # duplicate is rate bounded, so the clock has to leave that window first.
        link.advance(0.5)
        link.send("a", duplicate)
        link.advance(0.05)
        rx.pump(link.now)

    # A live session answers a duplicate so a lost final ACK can be repaired.
    before = link.stats.sent_by_endpoint["b"]
    deliver_duplicate()
    assert link.stats.sent_by_endpoint["b"] > before

    # A peer restart ends the session. The completed receive direction is not
    # rewritten - it stays COMPLETE - but it stops answering for a session that
    # no longer exists.
    link.restart("a")
    rx.pump(link.now)
    assert rx.failure == "peer_restarted"
    assert rx.recv_state is FlowState.COMPLETE
    before = link.stats.sent_by_endpoint["b"]
    deliver_duplicate()
    assert link.stats.sent_by_endpoint["b"] == before
    assert rx.post_terminal_rejected == 1


def test_a_duplicated_queue_estimate_cannot_produce_a_negative_rtt_sample():
    # The queue-depth estimate counts duplicated bytes while the radio serialises
    # one slot, so it can overstate when a frame finished. The RTT sample is taken
    # from the handover instant, which no acknowledgement can precede.
    forward = LinkProfile("dup", 1000.0, one_way_delay_s=0.01, duplicate_p=1.0)
    reverse = LinkProfile("rev", 1000.0, one_way_delay_s=0.01)
    link = SimLink(forward, reverse, seed=11)
    limits = FlowLimits(frame_payload_bytes=64, window_frames=2, initial_credit_frames=2)
    tx, rx, flows = pair(link, limits)
    tx.send(b"y" * 64)
    run(link, flows, deadline_s=10.0, until_settled=True)

    assert tx.state is FlowState.COMPLETE
    assert tx._srtt is not None and tx._srtt > 0
    assert tx._rttvar is not None and tx._rttvar > 0
    assert tx.impossible_rtt_samples == 0


def test_a_stale_equal_base_grant_cannot_move_credit_in_either_direction():
    """C01 negative: an older revision is ignored as an entire acknowledgement.

    The cumulative floor refuses an ACK naming an earlier position. An equal one
    is where a consume grant and a stale snapshot look identical, so credit is
    ordered by the revision of the exact canonical tuple. Revision 6 ignores an
    older revision *whole*: it may not move credit in either direction, and it
    may not retire receipt or touch recovery timing either, because each of those
    is a capacity decision an old statement is not entitled to make.
    """
    forward = LinkProfile("clean", 4000.0, one_way_delay_s=0.01)
    reverse = LinkProfile("clean-rev", 4000.0, one_way_delay_s=0.01)
    link = SimLink(forward, reverse, seed=3)
    limits = FlowLimits(window_frames=4, initial_credit_frames=4, max_reassembly_frames=8,
                        max_delivered_bytes=256, max_queued_bytes=4096)
    tx, rx, flows = pair(link, limits)
    data = bytes(range(256)) * 2
    tx.send(data)
    run(link, flows, deadline_s=2.0)
    rx.consume(256)
    link.advance(0.5)
    rx.pump(link.now)
    link.advance(0.5)
    tx.pump(link.now)
    assert tx._peer_credit == 4, tx._peer_credit
    assert tx._peer_grant_rev is not None, "the live grant carries a revision"

    for stale_credit in (0, 8):
        # Same base, an *older* revision: the snapshot the receiver already
        # superseded.
        stale = build_ack_frame(flow_id=1, generation=1, ack_seq=4, credit=stale_credit,
                                sack=0, limits=limits, grant_revision=1)
        probe_before = (tx._probe_at, tx._probe_attempts, tx._probe_gap_scale)
        before = tx.stale_grants
        tx.absorb(parse_frame(stale), link.now)
        assert tx._peer_credit == 4, f"credit {stale_credit} moved the window"
        assert tx.stale_grants == before + 1, "the refusal is counted, not silent"
        assert (tx._probe_at, tx._probe_attempts, tx._probe_gap_scale) == probe_before, \
            "a stale snapshot cannot change probe, gap or silence timing"

    # A newer statement from the same base is still a statement: the receiver's
    # next revision updates credit exactly as before.
    newer = build_ack_frame(flow_id=1, generation=1, ack_seq=4, credit=2,
                            sack=0, limits=limits, grant_revision=next_grant(tx))
    tx.absorb(parse_frame(newer), link.now)
    assert tx._peer_credit == 2


def _running_sender():
    """A sender mid-transfer with a live receiver, for the freshness controls."""
    forward = LinkProfile("clean", 4000.0, one_way_delay_s=0.01)
    reverse = LinkProfile("clean-rev", 4000.0, one_way_delay_s=0.01)
    link = SimLink(forward, reverse, seed=3)
    limits = FlowLimits(window_frames=4, initial_credit_frames=4, max_reassembly_frames=8,
                        max_delivered_bytes=256, max_queued_bytes=4096)
    tx, rx, flows = pair(link, limits)
    tx.send(bytes(range(256)) * 2)
    run(link, flows, deadline_s=2.0)
    return link, limits, tx, rx


def test_an_identical_equal_revision_grant_is_idempotent():
    """C02 positive: the same canonical tuple again is idempotent and refills nothing."""
    link, limits, tx, rx = _running_sender()
    rx.consume(256)
    link.advance(0.5)
    rx.pump(link.now)
    link.advance(0.5)
    tx.pump(link.now)
    granted = tx._peer_credit
    revision = tx._peer_grant_rev
    base, sack, credit = tx._peer_grant_snapshot
    inflight = tx.in_flight()

    same = build_ack_frame(flow_id=1, generation=1, ack_seq=base, credit=credit, sack=sack,
                           limits=limits, grant_revision=revision)
    repeats = tx.repeat_grants
    tx.absorb(parse_frame(same), link.now)
    assert tx.repeat_grants == repeats + 1
    assert tx._peer_credit == granted, "an identical snapshot cannot replenish capacity"
    assert tx._peer_grant_rev == revision
    assert tx.in_flight() <= inflight, "no attempt is retired twice"


def test_a_conflicting_equal_revision_grant_is_refused_without_mutation():
    """C02 negative: one revision, two different tuples - the change is refused."""
    link, limits, tx, rx = _running_sender()
    rx.consume(256)
    link.advance(0.5)
    rx.pump(link.now)
    link.advance(0.5)
    tx.pump(link.now)
    held = tx._peer_grant_rev
    assert tx._peer_credit == 4, tx._peer_credit

    conflicting = build_ack_frame(flow_id=1, generation=1, ack_seq=4, credit=8, sack=0,
                                  limits=limits, grant_revision=held)
    before = tx.conflicting_grants
    tx.absorb(parse_frame(conflicting), link.now)
    assert tx.conflicting_grants == before + 1
    assert tx._peer_credit == 4, "a conflicting equal revision may not widen the window"
    assert tx._peer_grant_rev == held
    assert tx._ack_floor == 4


def test_the_grant_revision_is_a_strict_u32_with_no_wrap():
    """C06: the endpoints encode, zero is not a revision, and the top does not wrap."""
    assert GRANT_REV_MAX == 4294967295
    lowest = build_ack_frame(flow_id=1, generation=1, ack_seq=0, credit=1, sack=0,
                             grant_revision=1)
    highest = build_ack_frame(flow_id=1, generation=1, ack_seq=0, credit=1, sack=0,
                              grant_revision=GRANT_REV_MAX)
    assert parse_frame(lowest)["grant_revision"] == 1
    assert parse_frame(highest)["grant_revision"] == GRANT_REV_MAX

    for bad in (0, -1, GRANT_REV_MAX + 1, True, 1.0, "1", None):
        with pytest.raises(FlowError):
            build_ack_frame(flow_id=1, generation=1, ack_seq=0, credit=1, sack=0,
                            grant_revision=bad)
    # A CRC-valid frame carrying zero refuses at the parser, before a "missing
    # revision" fallback could mutate anything.
    body = struct.pack("!3sBBBHHBI", MAGIC, 1, 1, 1, 0, 1, SACK_BITS, 0) + bytes(4)
    assert parse_frame(resign(body)) is None

    # Exhaustion fences the direction instead of reusing a statement. An
    # unchanged tuple at the maximum may still repeat idempotently; a changed one
    # has nowhere above the maximum to move to.
    link, limits, tx, rx = _running_sender()
    rx._grant_revision = GRANT_REV_MAX
    rx._grant_snapshot = (rx._expected, rx._sack_bitmap(), 7)
    assert rx._snapshot_revision(rx._grant_snapshot) == GRANT_REV_MAX
    assert rx._grant_fenced is False
    assert rx._snapshot_revision((rx._expected, rx._sack_bitmap(), 8)) is None
    assert rx._grant_fenced is True
    assert rx.recv_state is FlowState.FAILED
    assert rx.failure == "grant_revision_exhausted"


def test_mt2_and_the_older_ack_shapes_are_refused_before_mutation():
    """C07 negative: the candidate incompatibility is deliberate and explicit."""
    link, limits, tx, rx = _running_sender()
    revision = next_grant(tx)
    modern = build_ack_frame(flow_id=1, generation=1, ack_seq=1, credit=2, sack=0,
                             limits=limits, grant_revision=revision)
    assert parse_frame(modern) is not None
    assert modern[:3] == MAGIC
    assert len(modern) == ACK_BYTES

    # MT2, an unknown magic, and a truncated or trailing frame all refuse.
    assert parse_frame(b"MT2" + modern[3:]) is None
    assert parse_frame(b"MT9" + modern[3:]) is None
    assert parse_frame(modern[:-1]) is None
    assert parse_frame(modern + b"\x00") is None

    # The older 19-byte shape (no revision field) and the MT2 21-byte shape.
    old = struct.pack("!3sBBBHHB", MAGIC, 1, 1, 1, 1, 2, SACK_BITS) + bytes(4)
    mt2 = struct.pack("!3sBBBHHBH", b"MT2", 1, 1, 1, 1, 2, SACK_BITS, 1) + bytes(4)
    assert len(resign(old)) == 19 and parse_frame(resign(old)) is None
    assert len(resign(mt2)) == 21 and parse_frame(resign(mt2)) is None

    # A coherently declared 16/2-byte (21 total) or 64/8-byte (27 total) bitmap is
    # refused on the declared width itself, not mistaken for a malformed old shape.
    narrow = struct.pack("!3sBBBHHBI", MAGIC, 1, 1, 1, 1, 2, 16, revision) + bytes(2)
    wide = struct.pack("!3sBBBHHBI", MAGIC, 1, 1, 1, 1, 2, 64, revision) + bytes(8)
    assert len(resign(narrow)) == 21 and parse_frame(resign(narrow)) is None
    assert len(resign(wide)) == 27 and parse_frame(resign(wide)) is None

    # A corrupt frame is refused by the reader and reaches no state at all.
    before = (tx._peer_credit, tx._peer_grant_rev, tx._peer_grant_snapshot,
              tx.in_flight(), tx.acked_frames())
    corrupt = tx.corrupt_rejected
    for bad in (b"MT2" + modern[3:], resign(old), resign(mt2), resign(narrow), resign(wide)):
        assert link.send("b", bad, kind="ack"), "the carrier accepted the frame"
    run(link, [tx], deadline_s=link.now + 1.0)
    assert tx.corrupt_rejected == corrupt + 5
    assert (tx._peer_credit, tx._peer_grant_rev, tx._peer_grant_snapshot,
            tx.in_flight(), tx.acked_frames()) == before


def test_a_stale_revision_cannot_retire_receipt_or_mint_capacity():
    """C01 negative, indirect path: an old snapshot may not release capacity.

    Withholding only the credit update was not enough. An old equal-base snapshot
    can also name receipt the endpoint has moved past, and retiring that receipt
    releases window slots - capacity minted by a statement the peer has already
    superseded. The whole acknowledgement has to be ignored.
    """
    link, limits, tx, rx = _running_sender()
    rx.consume(256)
    link.advance(0.5)
    rx.pump(link.now)
    link.advance(0.5)
    tx.pump(link.now)
    held = tx._peer_grant_rev
    assert held > 1, held
    credit, floor = tx._peer_credit, tx._ack_floor
    pending, acked = tx.in_flight(), tx.acked_frames()

    stale = build_ack_frame(flow_id=1, generation=1, ack_seq=floor, credit=credit + 4,
                            sack=0, limits=limits, grant_revision=held - 1)
    stale_grants = tx.stale_grants
    tx.absorb(parse_frame(stale), link.now)
    assert tx.stale_grants == stale_grants + 1
    assert tx._peer_credit == credit, "an old snapshot cannot widen the window"
    assert tx.in_flight() == pending, "an old snapshot cannot retire receipt"
    assert tx.acked_frames() == acked
    assert tx._ack_floor == floor
    assert tx.stale_acks == 0, "an equal base is not an older base"


def test_an_inadmissible_higher_revision_cannot_poison_a_later_valid_one():
    """C03: all checks precede mutation, and a refused statement burns nothing."""
    link = link_with(seed=29)
    limits = FlowLimits()
    tx = flow("a", link, limits, flow_id=1, generation=1, receive=False)
    tx.send(bytes(range(256)) * 8)
    run(link, [tx], deadline_s=0.5)
    held, credit, floor = tx._peer_grant_rev, tx._peer_credit, tx._ack_floor
    pending = tx.in_flight()

    # A *newer* revision whose selective receipt names a sequence never exposed.
    bad = build_ack_frame(flow_id=1, generation=1, ack_seq=1, credit=2,
                          sack=1 << (SACK_BITS - 32), limits=limits,
                          grant_revision=next_grant(tx))
    tx.absorb(parse_frame(bad), link.now)
    assert tx.impossible_acks == 1
    assert (tx._peer_grant_rev, tx._peer_credit, tx._ack_floor) == (held, credit, floor)
    assert tx.in_flight() == pending

    # The refusal consumed no revision, so the same one arriving valid is applied.
    good = build_ack_frame(flow_id=1, generation=1, ack_seq=1, credit=2, sack=0,
                           limits=limits, grant_revision=held + 1)
    tx.absorb(parse_frame(good), link.now)
    assert tx._peer_grant_rev == held + 1
    assert tx._peer_credit == 2


def test_without_an_established_snapshot_the_sender_holds_no_credit():
    """C05/C09: no local-capacity inference, and an explicit startup grant."""
    link = link_with(seed=31)
    limits = FlowLimits(window_frames=4, initial_credit_frames=4, max_reassembly_frames=8,
                        max_delivered_bytes=256, max_queued_bytes=4096)
    tx = ReliableFlow("a", link, limits, flow_id=1, generation=1, receive=False)
    rx = ReliableFlow("b", link, limits, flow_id=1, generation=1, sends=False, receive=True)
    assert tx._peer_credit == 0 and tx._peer_grant_rev is None
    tx.send(b"z" * 256)
    # Nothing has established a snapshot yet, so pump only the sender: a local
    # limit is not room, and there is nothing to probe because nothing is
    # acknowledged.
    while link.now < 1.0:
        link.advance(0.01)
        tx.pump(link.now)
    assert tx.in_flight() == 0, "a sender with no established grant sends nothing"
    assert tx.retransmits == 0
    assert tx.zero_window_probes == 0, "and it cannot fabricate a probe to bootstrap itself"
    assert not tx.complete and tx.queued_bytes() == 256

    # The receiver's first statement is the explicit startup grant. Only then is
    # there room, and the room is the receiver's, not a reading of tx's limits.
    rx.pump(link.now)
    assert rx._last_ack_at >= 0, "the receiver spoke first"
    run(link, [tx, rx], deadline_s=60.0, until_settled=True)
    assert tx.complete and rx.complete
    assert rx.delivered == b"z" * 256
    assert tx._peer_grant_rev is not None


def test_a_refused_ack_emission_mints_no_revision_and_no_state():
    """C08 negative: a transport refusal creates no canonical state or credit."""
    link = link_with(seed=37)
    limits = FlowLimits(window_frames=4, initial_credit_frames=4)
    startup = shared_startup(limits)
    rx = flow("b", link, limits, flow_id=1, generation=1, sends=False, receive=True,
              startup=startup)
    assert (rx._grant_revision, rx._grant_snapshot) == (startup.revision, startup.snapshot)

    rx.grant_startup()
    original = link.send
    link.send = lambda *args, **kwargs: False
    try:
        rx.pump(0.0)
    finally:
        link.send = original
    assert (rx._grant_revision, rx._grant_snapshot) == (startup.revision, startup.snapshot)
    assert rx._last_ack_at == -1.0, "a refused emission is not an emission"

    # The same snapshot, actually sent, mints exactly one higher revision.
    rx.pump(0.0)
    assert rx._grant_revision == startup.revision + 1
    base, sack, credit = rx._grant_snapshot
    assert rx._grant_snapshot == (0, 0, credit)

    # Actual receipt and consumption move the tuple; an unchanged tuple does not.
    mints = rx._grant_revision
    link.advance(1.0)
    rx._ack_dirty = True
    rx.pump(link.now)
    assert rx._grant_revision == mints, "an unchanged tuple keeps its revision"
    packet = build_data_frame(flow_id=1, generation=1, seq=0, total=1, last=True,
                              payload=b"q" * 64, limits=limits)
    assert link.send("a", packet)
    link.advance(1.0)
    rx.pump(link.now)
    assert rx._grant_revision == mints + 1, "changed receipt mints one higher revision"


def test_the_byte_grant_mapping_never_clamps_up_to_one_frame():
    """C09 positive: fixed quantum P, floor, and zero or sub-P stays zero."""
    for byte_grant, expected in ((0, 0), (63, 0), (64, 1), (255, 3), (256, 4)):
        assert frames_from_byte_grant(byte_grant, 64) == expected, byte_grant
    for bad in (-1, 1.0, True, "64", None):
        with pytest.raises(FlowError):
            frames_from_byte_grant(bad, 64)
    for bad in (0, -1, 1.5, True):
        with pytest.raises(FlowError):
            frames_from_byte_grant(64, bad)

    # P is the fixed admitted quantum and it must leave room for the MT05
    # encoding inside the admitted bound. Named profile overhead is deducted once.
    assert admitted_payload_bound(contract_max_payload_bytes=128,
                                  profile_payload_capacity=100) == 100
    assert admitted_payload_bound(contract_max_payload_bytes=64,
                                  profile_payload_capacity=100) == 64
    assert admitted_payload_bound(contract_max_payload_bytes=128,
                                  profile_payload_capacity=100,
                                  unaccounted_overhead_bytes=12) == 88
    assert data_frame_fits(FRAME, 100)
    assert not data_frame_fits(FRAME, 70), "P must leave room for the 12-byte encoding"
    assert not data_frame_fits(0, 100)


# -- r7 REPAIR06: the zero-startup branch is bounded on both sides ---------------


def test_a_lost_startup_grant_is_reannounced_and_then_fails_bounded():
    """R6-CR-F01: one lost startup ACK must not idle the flow open forever.

    Frozen r6 sent the first startup statement exactly once, then had no event
    left: after 60 virtual seconds the sender was still OPEN with zero credit,
    nothing in flight, no probe eligible and no recovery event at all.
    """
    reverse = LinkProfile("host-rev", 2000.0, one_way_delay_s=0.02, loss_p=1.0)
    link = SimLink(LinkProfile("host", 2000.0, one_way_delay_s=0.02), reverse, seed=43)
    limits = FlowLimits(window_frames=4, initial_credit_frames=4, max_reassembly_frames=8,
                        max_delivered_bytes=256, max_queued_bytes=4096)
    tx = ReliableFlow("a", link, limits, flow_id=1, generation=1, receive=False)
    rx = ReliableFlow("b", link, limits, flow_id=1, generation=1, sends=False, receive=True)
    tx.send(b"z" * 128)
    assert tx._peer_grant_rev is None and tx._peer_credit == 0

    while link.now < 60.0 and tx.state is FlowState.OPEN:
        link.advance(0.01)
        tx.pump(link.now)
        rx.pump(link.now)

    assert tx.state is not FlowState.OPEN, "a sender cannot wait for a grant forever"
    assert tx.failure == "startup_grant_absent", tx.failure
    assert rx.state is not FlowState.OPEN, "and the receiver cannot reannounce forever"
    assert rx.failure == "startup_grant_unanswered", rx.failure
    # Bounded, not merely eventual: both ends stop inside the derived budget.
    assert link.now < 10.0, link.now
    assert rx._startup_announce_left == 0
    assert tx.retransmits == 0, "no DATA was ever fabricated to bootstrap the flow"


def test_a_link_that_refuses_every_startup_statement_still_fails_bounded():
    """R6-CR-F01: the count budget alone is not a bound if nothing is ever emitted."""
    link = link_with(seed=47)
    limits = FlowLimits(window_frames=4, initial_credit_frames=4, max_reassembly_frames=8,
                        max_delivered_bytes=256, max_queued_bytes=4096)
    rx = ReliableFlow("b", link, limits, flow_id=1, generation=1, sends=False, receive=True)
    original = link.send
    link.send = lambda *args, **kwargs: False
    try:
        while link.now < 60.0 and rx.state is FlowState.OPEN:
            link.advance(0.01)
            rx.pump(link.now)
    finally:
        link.send = original
    assert rx.state is not FlowState.OPEN
    assert rx.failure == "startup_grant_unanswered", rx.failure
    assert rx._last_ack_at == -1.0, "nothing was ever on the wire"


def test_a_recovered_startup_lane_still_completes_the_exact_bytes():
    """R6-CR-F01 positive: the clean no-G1 path remains green after the repair."""
    link = link_with(seed=53)
    limits = FlowLimits(window_frames=4, initial_credit_frames=4, max_reassembly_frames=8,
                        max_delivered_bytes=256, max_queued_bytes=4096)
    tx = ReliableFlow("a", link, limits, flow_id=1, generation=1, receive=False)
    rx = ReliableFlow("b", link, limits, flow_id=1, generation=1, sends=False, receive=True)
    tx.send(b"q" * 128)
    run(link, [tx, rx], deadline_s=60.0, until_settled=True)
    assert tx.complete and rx.complete
    assert rx.delivered == b"q" * 128
    assert rx.startup_pending is False, "the answered grant stops being reannounced"


# -- r7 REPAIR07: the shared startup snapshot is validated, not assumed ---------


def test_a_startup_snapshot_must_be_the_canonical_initial_state():
    """R6-CR-F02: r6 installed base1/nonempty SACK/revision2 without objection."""
    for bad in (dict(ack_base=1), dict(sack=1), dict(revision=2), dict(ack_base=65536)):
        with pytest.raises(FlowError):
            StartupGrant(credit_frames=1, **bad)
    # A compatible one is still exactly that.
    good = StartupGrant(credit_frames=1)
    assert good.snapshot == (0, 0, 1) and good.revision == GRANT_REV_MIN


def test_a_receiver_cannot_advertise_more_room_than_it_can_hold():
    """R6-CR-F02 CR-06: r6 admitted C0=4 for a one-slot/64-byte receiver."""
    narrow = FlowLimits(frame_payload_bytes=64, window_frames=1, initial_credit_frames=4,
                        max_reassembly_frames=1, max_delivered_bytes=64, max_queued_bytes=4096)
    assert remote_startup_credit(narrow) == 1
    with pytest.raises(FlowError) as error:
        ReliableFlow("b", link_with(), narrow, sends=False, receive=True,
                     startup=StartupGrant(credit_frames=4))
    assert "local receive capacity" in str(error.value)
    # The sender side of the same malformed shared snapshot refuses too, rather
    # than reading remote room out of anything of its own.
    with pytest.raises(FlowError):
        ReliableFlow("a", link_with(), narrow, flow_id=1, generation=1, receive=False,
                     startup=StartupGrant(credit_frames=4))
    # An explicit trusted host/fixture capacity is the only way to widen it.
    wider = ReliableFlow("a", link_with(), narrow, flow_id=1, generation=1, receive=False,
                         startup=StartupGrant(credit_frames=4),
                         agreed_startup_capacity_frames=4)
    assert wider._peer_credit == 4
    with pytest.raises(FlowError):
        ReliableFlow("a", link_with(), narrow, flow_id=1, generation=1, receive=False,
                     startup=StartupGrant(credit_frames=4), agreed_startup_capacity_frames=3)


def test_a_compatible_shared_snapshot_for_a_one_slot_receiver_completes():
    """R6-CR-F02 CR-05 positive: C0=1 against a one-slot/64-byte receiver."""
    link = link_with(seed=59)
    narrow = FlowLimits(frame_payload_bytes=64, window_frames=1, initial_credit_frames=1,
                        max_reassembly_frames=1, max_delivered_bytes=64, max_queued_bytes=4096)
    startup = StartupGrant(credit_frames=remote_startup_credit(narrow))
    assert startup.credit_frames == 1
    tx = ReliableFlow("a", link, narrow, flow_id=1, generation=1, receive=False,
                      startup=startup)
    rx = ReliableFlow("b", link, narrow, flow_id=1, generation=1, sends=False, receive=True,
                      startup=startup)
    tx.send(b"w" * 64)
    run(link, [tx, rx], deadline_s=60.0, until_settled=True)
    assert tx.complete and rx.complete
    assert rx.delivered == b"w" * 64
