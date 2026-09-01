"""Addressed deterministic random stream.

Every draw is addressed by model version, seed, component, year and lane.  New
components therefore do not shift existing histories and short runs remain
strict prefixes of longer runs.
"""

from __future__ import annotations

import hashlib
import math


RANDOM_STREAM_VERSION = "asset-simulation-addressed-rng-v1"


def _uniform(seed: int, component: str, year_index: int, lane: int) -> float:
    payload = (
        f"{RANDOM_STREAM_VERSION}|{seed}|{component}|{year_index}|{lane}"
    ).encode("utf-8")
    raw = hashlib.sha256(payload).digest()
    integer = int.from_bytes(raw[:8], "big")
    return (integer + 0.5) / (2**64)


def uniform(seed: int, component: str, year_index: int, lane: int = 0) -> float:
    """Return one deterministic uniform draw in (0, 1) for an address."""

    return _uniform(seed, component, year_index, lane)


def normal(seed: int, component: str, year_index: int) -> float:
    """Return one deterministic standard-normal draw for an address."""

    u1 = max(_uniform(seed, component, year_index, 0), 1e-15)
    u2 = _uniform(seed, component, year_index, 1)
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
