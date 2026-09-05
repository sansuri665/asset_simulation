"""Fixed, identified, homogeneous VLCCs on the 0+2+1+2 turn contract.

A FleetState is the END of the preceding turn. advance_fleet opens the next
turn exactly once; dispatch_fleet then loads instantly. No price or money here.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math

STATES = ("gulf_prompt", "laden_1", "laden_2", "ea_discharge", "ballast_1", "ballast_2")


def count_integer(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


@dataclass(frozen=True)
class FleetState:
    size: int
    gulf_prompt: tuple[int, ...] = ()
    laden_1: tuple[int, ...] = ()
    laden_2: tuple[int, ...] = ()
    ea_discharge: tuple[int, ...] = ()
    ballast_1: tuple[int, ...] = ()
    ballast_2: tuple[int, ...] = ()
    turn_index: int = -1
    phase: str = "closed"

    def counts(self) -> dict[str, int]:
        return {name: len(getattr(self, name)) for name in STATES}

    def cargo_vessel_count(self) -> int:
        return len(self.laden_1) + len(self.laden_2) + len(self.ea_discharge)

    def validate(self) -> None:
        count_integer(self.size, "fleet size")
        ids = [ship for name in STATES for ship in getattr(self, name)]
        if any(isinstance(ship, bool) or not isinstance(ship, int) for ship in ids):
            raise ValueError("vessel IDs must be integers")
        if len(ids) != self.size or set(ids) != set(range(1, self.size + 1)):
            raise ValueError("fleet identity conservation failed: duplicate, lost or foreign ship")
        if self.phase not in {"open", "closed"}:
            raise ValueError("invalid fleet step phase")
        if self.phase == "open" and self.laden_1:
            raise ValueError("an open turn cannot already contain new departures")


def initial_fleet(size: int, *, reference_departures: float,
                  initialization: str = "phased", phase_rotation: int = 0) -> FleetState:
    """Seed existing voyages from a fixed reference, never from future demand.

    Each of five cohorts differs by at most one vessel. all_prompt is a cold
    start diagnostic, NOT the default calibration. Integer IDs are permanent.
    """
    count_integer(size, "fleet size")
    count_integer(phase_rotation, "phase rotation")
    if phase_rotation >= 5:
        raise ValueError("phase rotation must be in 0..4")
    if isinstance(reference_departures, bool) or not math.isfinite(reference_departures) or reference_departures < 0:
        raise ValueError("reference departures must be finite and nonnegative")
    if initialization not in {"phased", "all_prompt"}:
        raise ValueError("initialization must be phased or all_prompt")
    active = min(size, int(math.floor(5 * reference_departures + 0.5))) if initialization == "phased" else 0
    cohorts = [active // 5 + int(i < active % 5) for i in range(5)]
    cohorts = cohorts[phase_rotation:] + cohorts[:phase_rotation]
    groups: dict[str, tuple[int, ...]] = {}
    offset = 1
    for name, count in zip(STATES[1:], cohorts):
        groups[name] = tuple(range(offset, offset + count))
        offset += count
    result = FleetState(size=size, gulf_prompt=tuple(range(offset, size + 1)), **groups)
    result.validate()
    return result


def advance_fleet(state: FleetState) -> tuple[FleetState, dict[str, tuple[int, ...]]]:
    """Open a turn: discharge completions release oil, B2 ships return to Gulf."""
    state.validate()
    if state.phase != "closed":
        raise ValueError("cannot advance a turn twice before dispatch/close")
    result = FleetState(
        size=state.size,
        gulf_prompt=tuple(sorted(state.gulf_prompt + state.ballast_2)),
        laden_2=state.laden_1,
        ea_discharge=state.laden_2,
        ballast_1=state.ea_discharge,
        ballast_2=state.ballast_1,
        turn_index=state.turn_index + 1,
        phase="open",
    )
    result.validate()
    return result, {"delivered_ship_ids": state.ea_discharge, "returned_ship_ids": state.ballast_2}


def dispatch_fleet(state: FleetState, count: int) -> tuple[FleetState, tuple[int, ...]]:
    """Load up to count prompt ships, then close the turn. Waiting stays supply."""
    state.validate()
    count_integer(count, "dispatch count")
    if state.phase != "open":
        raise ValueError("dispatch requires an open turn")
    if count > len(state.gulf_prompt):
        raise ValueError("cannot dispatch vessels that are not prompt")
    ids = state.gulf_prompt[:count]
    result = replace(state, gulf_prompt=state.gulf_prompt[count:], laden_1=ids, phase="closed")
    result.validate()
    return result, ids
