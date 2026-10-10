"""Multiplexing and the scenario driver for the MT05 candidate.

The scheduler here is deliberately small. It exists to show that one stalled
flow cannot stall another, and to demonstrate the demultiplexing rule that
several flows sharing one carrier require. The real service scheduler, with
weighted classes, airtime accounting and admission control, is MT04; it should
be able to replace :class:`FlowMux` without changing the flow.
"""

from __future__ import annotations

from .flow import FlowError, FlowState, parse_frame

TERMINAL = (FlowState.CANCELLED, FlowState.FAILED, FlowState.PEER_LOST)


def _done(flows) -> bool:
    return all(flow.state is not FlowState.OPEN or flow.settled() for flow in flows)


def run(link, flows, *, deadline_s: float, step_s: float = 0.01,
        until_settled: bool = False) -> float:
    """Advance the virtual clock in fixed steps, pumping each flow directly.

    Use this when one flow sits at each endpoint. When several flows share a
    link, use :class:`FlowMux`, which demultiplexes before pumping.
    """
    flows = list(flows)
    while link.now < deadline_s:
        link.advance(step_s)
        for flow in flows:
            flow.pump(link.now)
        if until_settled and _done(flows):
            break
    return link.now


class FlowMux:
    """Deficit round robin plus generation demultiplexing over one carrier."""

    def __init__(self, link, flows, *, quantum_frames: int = 4):
        self.link = link
        self.flows = list(flows)
        registered = set()
        for flow in self.flows:
            identity = (flow.endpoint, flow.flow_id, flow.generation)
            if identity in registered:
                raise FlowError(f"duplicate flow registration: {identity}")
            registered.add(identity)
        self.quantum_frames = quantum_frames
        self._cursor = 0
        self.visits = 0
        self.corrupt_frames = 0
        self.unmatched_frames = 0

    def pump(self, now: float) -> None:
        self._demux(now)
        for _ in range(len(self.flows)):
            flow = self.flows[self._cursor]
            self._cursor = (self._cursor + 1) % len(self.flows) if self.flows else 0
            self.visits += 1
            if flow.state in TERMINAL:
                continue
            flow.pump(now, max_frames=self.quantum_frames, drain=False)

    def _demux(self, now: float) -> None:
        """Hand each arrived frame to the flow it is addressed to.

        Demultiplexing is by flow id, never by generation. Generation is a
        fencing value: two live flows must not have to pick different
        generations to be told apart.
        """
        for endpoint in ("a", "b"):
            for delivery in self.link.inbound(endpoint):
                candidates = [flow for flow in self.flows if flow.endpoint == endpoint]
                matched = False
                corrupt = False
                for flow in candidates:
                    frame = parse_frame(delivery.payload, flow.seq_modulus)
                    if frame is None:
                        corrupt = True
                        continue
                    if (frame["flow_id"] == flow.flow_id
                            and frame["generation"] == flow.generation):
                        flow.absorb(frame, now)
                        matched = True
                if not matched:
                    if corrupt:
                        self.corrupt_frames += 1
                    else:
                        self.unmatched_frames += 1

    def run(self, *, deadline_s: float, step_s: float = 0.01,
            until_settled: bool = False) -> float:
        while self.link.now < deadline_s:
            self.link.advance(step_s)
            self.pump(self.link.now)
            if until_settled and _done(self.flows):
                break
        return self.link.now
