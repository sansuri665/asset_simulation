"""Public field projection for the compact global snapshot."""

from __future__ import annotations

import math
from typing import Any, Mapping


def minimum_field_names(field_contract: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(field_contract["identity_fields"]) + tuple(
        field_contract["a_level_fields"]
    ) + tuple(field_contract["b_level_fields"])


def build_minimum_snapshot(
    row: Mapping[str, Any], field_contract: Mapping[str, Any]
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for key in minimum_field_names(field_contract):
        if key not in row:
            raise KeyError(f"global minimum contract field is missing: {key}")
        value = row[key]
        if key not in field_contract["identity_fields"] and value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"global minimum contract field is not numeric: {key}")
            if not math.isfinite(float(value)):
                raise ValueError(f"global minimum contract field is non-finite: {key}")
        snapshot[key] = value
    snapshot["real_policy_gap_pct"] = round(
        float(row["real_policy_rate_pct"])
        - float(row["neutral_real_policy_rate_pct"]),
        8,
    )
    snapshot["term_spread_10y_2y_pct"] = round(
        float(row["global_10y_yield_pct"]) - float(row["global_2y_yield_pct"]),
        8,
    )
    return snapshot
