"""Read-only adapter from main's crude route world into Preview3 turns."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..mixed_cargo_market import build_mixed_inputs
from ..registry import sha256_json
from .types import BoardSpec, ImportRequirement


def build_main_linked_preview3_inputs(
    board_spec: BoardSpec,
    *,
    seed: int,
    years: int,
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    """Project main's two selected crude OD routes onto the fixed 10-day clock.

    The same main-owned route volumes become the natural cargo prior and the
    eligible physical source release for this isolated submarket. Independent
    destination requirements preserve the original route service dates even if
    Preview3 subsequently changes the source mix. The source world is never
    mutated and Preview3 cannot write back into main accounting.
    """
    raw, source = build_mixed_inputs(board_spec.market.physical, seed=seed, years=years)
    lags = dict(board_spec.market.due_lags)
    records = []
    for turn, item in enumerate(raw):
        natural = dict(item["scheduled_by_origin_bbl"])
        requirements = tuple(
            ImportRequirement(f"main-link:{origin}:{turn}", turn + lags[origin], amount)
            for origin, amount in natural.items() if amount
        )
        records.append({
            "turn": turn,
            "year": item["year"],
            "month": item["month"],
            "turn_in_month": item["turn_in_month"],
            "label": item["label"],
            "natural_cargo_plan_bbl": natural,
            "source_release_bbl": dict(natural),
            "new_requirements": requirements,
            "cpi": item["cpi"],
            "cpi_information_year": item["cpi_information_year"],
            "source_route_cargo_mbd": dict(item["source_route_cargo_mbd"]),
        })
    hashable_records = [
        {**row, "new_requirements": [asdict(requirement) for requirement in row["new_requirements"]]}
        for row in records
    ]
    result = {
        "adapter": "main-crude-route-world-to-stage6c-preview3-v0.1.0",
        "seed": seed,
        "years": years,
        "turn_count": len(records),
        "main_source": source,
        "main_source_unchanged": source["source_unchanged"],
        "selected_origins": list(board_spec.origins),
        "destination": board_spec.market.destination,
        "clock": "three_equal_10_day_operating_turns_per_main_calendar_month",
        "write_back_to_main": False,
        "records_hash": sha256_json(hashable_records),
    }
    result["adapter_hash"] = sha256_json(result)
    return tuple(records), result
