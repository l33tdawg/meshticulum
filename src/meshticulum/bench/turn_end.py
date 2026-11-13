"""Explicit bench-only authenticated turn termination admission.

This capability changes ACK scheduling, never credit, authority or radio guards.
Its complete value is included in the admitted route and grant key context.
"""
from dataclasses import dataclass, field
from .burst_protocol import policy_for_train32, validate_policy
from .wire import require

CAPABILITY = {
    'schema': 'meshticulum.data-turn-end-capability.v1',
    'seal_rule': 'carrier-last-admission.v1',
    'maximum_train_frames': 32,
    'host_pending_limit': 3,
}
_ITEMS = tuple(CAPABILITY.items())


def capability():
    return dict(_ITEMS)


def admitted(plan):
    if 'turn_end_capability' not in plan:
        return False
    value = plan['turn_end_capability']
    expected = dict(_ITEMS)
    require(type(value) is dict and set(value) == set(expected)
            and all(type(value[k]) is type(v) and value[k] == v
                    for k, v in expected.items()), 'invalid_turn_end_capability')
    require(plan.get('schema') == 'meshticulum.bench-run.v1'
            and plan.get('data_burst') == policy_for_train32()
            and 'operation_limits' in plan, 'turn_end_requires_explicit_train32_operation')
    validate_policy(plan['data_burst'])
    # Lazy import avoids authority's admission-module cycle; use its exact
    # existing operation validator rather than a weaker duplicated codec.
    from .authority import OperationLimits
    try:
        OperationLimits.from_plan(plan)
    except (KeyError, TypeError):
        require(False, 'invalid_turn_end_operation')
    return True


@dataclass(frozen=True, slots=True)
class FinalAdmission:
    """Retained references and original native identity, never a new deadline."""
    worker: object = field(repr=False)
    opened: object = field(repr=False)
    token: object = field(repr=False)
    generation: int
    deadline: int
    count: int
    logical_id: bytes = field(repr=False)
    owner_frame_id: int
    plaintext: bytes = field(repr=False)
