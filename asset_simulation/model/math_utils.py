"""Numerical helpers shared by the compact macro components."""

from __future__ import annotations

import math
from typing import Any, Mapping


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def number(mapping: Mapping[str, Any], key: str) -> float:
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{key} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{key} must be finite")
    return result


def round_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("annual output contains a non-finite value")
        return round(value, 8)
    return value


def round_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: round_value(value) for key, value in record.items()}
