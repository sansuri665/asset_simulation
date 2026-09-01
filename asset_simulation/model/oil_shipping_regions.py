"""Regional crude balances that generate seaborne export and import margins."""

from __future__ import annotations

from typing import Any, Mapping

from .math_utils import clamp
from .random_stream import normal


def initial_regional_state(config: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    regions = config["regional_oil"]["regions"]
    return {
        "production_shares": {
            str(region["region_id"]): float(region["initial_production_share"])
            for region in regions
        },
        "refinery_shares": {
            str(region["region_id"]): float(region["initial_refinery_share"])
            for region in regions
        },
        "pipeline_net_exports_mbd": {
            str(region["region_id"]): float(region["pipeline_net_exports_mbd"])
            for region in regions
        },
        "production_policy": {
            "target_mbd": 0.0,
            "deviation_mbd": 0.0,
        },
        "us_gulf_cycle": {
            "production_target_mbd": 0.0,
            "production_deviation_mbd": 0.0,
            "refinery_operational_deviation_mbd": 0.0,
            "refinery_deviation_mbd": 0.0,
        },
    }


def _evolve_shares(
    previous: Mapping[str, float],
    regions: list[Mapping[str, Any]],
    *,
    field_prefix: str,
    seed: int,
    turn_index: int,
    persistence: float,
    news_scale: float,
) -> dict[str, float]:
    raw: dict[str, float] = {}
    for region in regions:
        region_id = str(region["region_id"])
        baseline = float(region[f"initial_{field_prefix}_share"])
        low, high = map(float, region[f"{field_prefix}_share_bounds"])
        raw[region_id] = clamp(
            baseline
            + persistence * (float(previous[region_id]) - baseline)
            + news_scale
            * normal(
                seed,
                f"oil_region_{field_prefix}_share_{region_id}",
                turn_index,
            ),
            low,
            high,
        )
    total = sum(raw.values())
    if total <= 0.0:
        raise ValueError(f"regional {field_prefix} shares have no positive mass")
    normalized = {region_id: value / total for region_id, value in raw.items()}
    final_id = str(regions[-1]["region_id"])
    normalized[final_id] += 1.0 - sum(normalized.values())
    return normalized


def _evolve_pipeline(
    previous: Mapping[str, float],
    regions: list[Mapping[str, Any]],
    *,
    seed: int,
    turn_index: int,
    persistence: float,
    news_scale_mbd: float,
    balancing_region_id: str,
) -> dict[str, float]:
    values: dict[str, float] = {}
    for region in regions:
        region_id = str(region["region_id"])
        baseline = float(region["pipeline_net_exports_mbd"])
        values[region_id] = (
            baseline
            + persistence * (float(previous[region_id]) - baseline)
            + news_scale_mbd
            * normal(seed, f"oil_region_pipeline_{region_id}", turn_index)
        )
    values[balancing_region_id] -= sum(values.values())
    return values


def _apply_zero_sum_impulses(
    values: dict[str, float],
    impulses: Mapping[str, float],
    *,
    known_region_ids: set[str],
    balancing_region_id: str,
    field_name: str,
) -> None:
    unknown = set(impulses) - known_region_ids
    if unknown:
        raise KeyError(f"unknown regions for {field_name}: {sorted(unknown)}")
    total_impulse = 0.0
    for region_id, impulse in impulses.items():
        numeric = float(impulse)
        values[region_id] += numeric
        total_impulse += numeric
    values[balancing_region_id] -= total_impulse


def _advance_production_policy(
    previous: Mapping[str, float],
    physical_turn: Mapping[str, float],
    *,
    seed: int,
    turn_index: int,
    policy: Mapping[str, Any],
) -> tuple[dict[str, float], bool]:
    """Advance a sticky producer-policy target and its gradual execution."""

    decision_interval = int(policy["decision_interval_months"])
    if decision_interval <= 0:
        raise ValueError("production-policy decision interval must be positive")
    persistence = float(policy["target_persistence"])
    if not 0.0 <= persistence < 1.0:
        raise ValueError("production-policy target persistence must be in [0, 1)")

    target = float(previous["target_mbd"])
    is_decision_month = turn_index % decision_interval == 0
    if is_decision_month:
        decision_index = turn_index // decision_interval
        market_target = (
            float(policy["inventory_pressure_response_mbd_per_pct_point"])
            * float(physical_turn["inventory_supply_pressure_pct"])
        )
        target = clamp(
            persistence * target
            + (1.0 - persistence) * market_target
            + float(policy["decision_news_scale_mbd"])
            * normal(
                seed,
                "oil_region_gulf_production_policy_decision",
                decision_index,
            ),
            *map(float, policy["target_bounds_mbd"]),
        )

    previous_deviation = float(previous["deviation_mbd"])
    unconstrained_change = float(policy["monthly_adjustment_speed"]) * (
        target - previous_deviation
    )
    adjustment = clamp(
        unconstrained_change,
        -float(policy["maximum_monthly_adjustment_mbd"]),
        float(policy["maximum_monthly_adjustment_mbd"]),
    )
    deviation = previous_deviation + adjustment
    return {"target_mbd": target, "deviation_mbd": deviation}, is_decision_month


def _advance_us_gulf_cycle(
    previous: Mapping[str, float],
    *,
    seed: int,
    turn_index: int,
    month: int,
    lagged_real_oil_price_index: float,
    cycle: Mapping[str, Any],
) -> tuple[dict[str, float], bool, float]:
    """Advance lagged shale supply and the seasonal refinery maintenance cycle."""

    production_config = cycle["production"]
    is_production_decision_month = month == 1
    production_target = float(previous["production_target_mbd"])
    if is_production_decision_month:
        year_index = turn_index // 12
        market_target = float(
            production_config["lagged_real_price_response_mbd_per_index_point"]
        ) * (lagged_real_oil_price_index - 100.0)
        persistence = float(production_config["target_persistence"])
        production_target = clamp(
            persistence * production_target
            + (1.0 - persistence) * market_target
            + float(production_config["annual_news_scale_mbd"])
            * normal(seed, "oil_region_us_gulf_shale_investment", year_index),
            *map(float, production_config["target_bounds_mbd"]),
        )
    previous_production = float(previous["production_deviation_mbd"])
    production_change = clamp(
        float(production_config["monthly_adjustment_speed"])
        * (production_target - previous_production),
        -float(production_config["maximum_monthly_adjustment_mbd"]),
        float(production_config["maximum_monthly_adjustment_mbd"]),
    )
    production_deviation = previous_production + production_change

    refinery_config = cycle["refinery"]
    monthly_profile = list(
        map(float, refinery_config["baseline_monthly_profile_mbd"])
    )
    if len(monthly_profile) != 12:
        raise ValueError(
            "US Gulf refinery baseline monthly profile must contain 12 values"
        )
    year_index = turn_index // 12
    maintenance_kernel = list(map(float, refinery_config["maintenance_kernel"]))
    if not maintenance_kernel or any(value < 0.0 for value in maintenance_kernel):
        raise ValueError("US Gulf refinery maintenance kernel must be nonnegative")
    for season in ("spring", "fall"):
        maintenance = refinery_config[f"{season}_maintenance"]
        start_month = int(
            round(
                clamp(
                    float(maintenance["start_center_month"])
                    + float(maintenance["start_news_scale_months"])
                    * normal(
                        seed,
                        f"oil_region_us_gulf_{season}_maintenance_timing",
                        year_index,
                    ),
                    *map(float, maintenance["start_bounds_month"]),
                )
            )
        )
        amplitude = clamp(
            float(maintenance["amplitude_base_mbd"])
            + float(maintenance["amplitude_news_scale_mbd"])
            * normal(
                seed,
                f"oil_region_us_gulf_{season}_maintenance_depth",
                year_index,
            ),
            *map(float, maintenance["amplitude_bounds_mbd"]),
        )
        for offset, weight in enumerate(maintenance_kernel):
            month_index = start_month - 1 + offset
            if month_index < 12:
                monthly_profile[month_index] -= amplitude * weight
    annual_profile_mean = sum(monthly_profile) / 12.0
    monthly_profile = [value - annual_profile_mean for value in monthly_profile]
    previous_operations = float(previous["refinery_operational_deviation_mbd"])
    operational_deviation = clamp(
        float(refinery_config["operational_persistence"]) * previous_operations
        + float(refinery_config["operational_news_scale_mbd"])
        * normal(seed, "oil_region_us_gulf_refinery_operations", turn_index),
        *map(float, refinery_config["operational_bounds_mbd"]),
    )
    refinery_target = clamp(
        monthly_profile[month - 1] + operational_deviation,
        *map(float, refinery_config["target_bounds_mbd"]),
    )
    previous_refinery = float(previous["refinery_deviation_mbd"])
    refinery_change = clamp(
        float(refinery_config["monthly_adjustment_speed"])
        * (refinery_target - previous_refinery),
        -float(refinery_config["maximum_monthly_adjustment_mbd"]),
        float(refinery_config["maximum_monthly_adjustment_mbd"]),
    )
    refinery_deviation = previous_refinery + refinery_change
    return (
        {
            "production_target_mbd": production_target,
            "production_deviation_mbd": production_deviation,
            "refinery_operational_deviation_mbd": operational_deviation,
            "refinery_deviation_mbd": refinery_deviation,
        },
        is_production_decision_month,
        refinery_target,
    )


def advance_regional_balance(
    state: Mapping[str, Mapping[str, float]],
    physical_turn: Mapping[str, float],
    *,
    seed: int,
    turn_index: int,
    month: int,
    lagged_real_oil_price_index: float,
    config: Mapping[str, Any],
    impulse: Mapping[str, Any] | None = None,
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    """Split one global physical turn and expose exact regional sea margins."""

    impulse = {} if impulse is None else impulse
    regional = config["regional_oil"]
    regions = list(regional["regions"])
    known_region_ids = {str(region["region_id"]) for region in regions}
    balancing_region_id = str(regional["balancing_region_id"])

    production_shares = _evolve_shares(
        state["production_shares"],
        regions,
        field_prefix="production",
        seed=seed,
        turn_index=turn_index,
        persistence=float(regional["share_persistence"]),
        news_scale=float(regional["production_share_news_scale"]),
    )
    refinery_shares = _evolve_shares(
        state["refinery_shares"],
        regions,
        field_prefix="refinery",
        seed=seed,
        turn_index=turn_index,
        persistence=float(regional["share_persistence"]),
        news_scale=float(regional["refinery_share_news_scale"]),
    )
    pipeline = _evolve_pipeline(
        state["pipeline_net_exports_mbd"],
        regions,
        seed=seed,
        turn_index=turn_index,
        persistence=float(regional["pipeline_persistence"]),
        news_scale_mbd=float(regional["pipeline_news_scale_mbd"]),
        balancing_region_id=balancing_region_id,
    )

    global_production = float(physical_turn["production_mbd"])
    global_refinery_demand = float(physical_turn["realized_demand_mbd"])
    global_inventory_change = float(physical_turn["inventory_change_mmbbl"])
    days = int(physical_turn["days"])
    production = {
        region_id: global_production * share
        for region_id, share in production_shares.items()
    }
    refinery = {
        region_id: global_refinery_demand * share
        for region_id, share in refinery_shares.items()
    }
    inventory = {
        str(region["region_id"]): global_inventory_change
        * float(region["inventory_change_share"])
        for region in regions
    }
    inventory[balancing_region_id] += global_inventory_change - sum(inventory.values())

    policy_config = regional["production_policy"]
    policy_region_id = str(policy_config["region_id"])
    policy_balancing_region_id = str(policy_config["balancing_region_id"])
    if policy_region_id not in known_region_ids:
        raise KeyError(f"unknown production-policy region: {policy_region_id}")
    if policy_balancing_region_id not in known_region_ids:
        raise KeyError(
            f"unknown production-policy balancing region: {policy_balancing_region_id}"
        )
    production_policy, is_policy_decision_month = _advance_production_policy(
        state["production_policy"],
        physical_turn,
        seed=seed,
        turn_index=turn_index,
        policy=policy_config,
    )
    policy_adjustments = {region_id: 0.0 for region_id in known_region_ids}
    policy_adjustments[policy_region_id] = production_policy["deviation_mbd"]
    policy_adjustments[policy_balancing_region_id] = -production_policy[
        "deviation_mbd"
    ]
    for region_id, adjustment in policy_adjustments.items():
        production[region_id] += adjustment

    us_gulf_config = regional["us_gulf_cycle"]
    us_gulf_region_id = str(us_gulf_config["region_id"])
    us_gulf_production_balancing_id = str(
        us_gulf_config["production_balancing_region_id"]
    )
    us_gulf_refinery_balancing_id = str(
        us_gulf_config["refinery_balancing_region_id"]
    )
    for region_id in {
        us_gulf_region_id,
        us_gulf_production_balancing_id,
        us_gulf_refinery_balancing_id,
    }:
        if region_id not in known_region_ids:
            raise KeyError(f"unknown US Gulf cycle region: {region_id}")
    us_gulf_cycle, is_us_gulf_production_decision_month, refinery_cycle_target = (
        _advance_us_gulf_cycle(
            state["us_gulf_cycle"],
            seed=seed,
            turn_index=turn_index,
            month=month,
            lagged_real_oil_price_index=lagged_real_oil_price_index,
            cycle=us_gulf_config,
        )
    )
    production_cycle_adjustments = {
        region_id: 0.0 for region_id in known_region_ids
    }
    production_cycle_adjustments[us_gulf_region_id] = us_gulf_cycle[
        "production_deviation_mbd"
    ]
    production_cycle_adjustments[us_gulf_production_balancing_id] = -us_gulf_cycle[
        "production_deviation_mbd"
    ]
    refinery_cycle_adjustments = {region_id: 0.0 for region_id in known_region_ids}
    refinery_cycle_adjustments[us_gulf_region_id] = us_gulf_cycle[
        "refinery_deviation_mbd"
    ]
    refinery_cycle_adjustments[us_gulf_refinery_balancing_id] = -us_gulf_cycle[
        "refinery_deviation_mbd"
    ]
    for region_id in known_region_ids:
        production[region_id] += production_cycle_adjustments[region_id]
        refinery[region_id] += refinery_cycle_adjustments[region_id]

    _apply_zero_sum_impulses(
        production,
        impulse.get("regional_production_impulse_mbd", {}),
        known_region_ids=known_region_ids,
        balancing_region_id=str(regional["production_impulse_balancing_region_id"]),
        field_name="regional_production_impulse_mbd",
    )
    _apply_zero_sum_impulses(
        refinery,
        impulse.get("regional_refinery_impulse_mbd", {}),
        known_region_ids=known_region_ids,
        balancing_region_id=str(regional["refinery_impulse_balancing_region_id"]),
        field_name="regional_refinery_impulse_mbd",
    )
    _apply_zero_sum_impulses(
        inventory,
        impulse.get("regional_inventory_impulse_mmbbl", {}),
        known_region_ids=known_region_ids,
        balancing_region_id=str(regional["inventory_impulse_balancing_region_id"]),
        field_name="regional_inventory_impulse_mmbbl",
    )
    if any(value < 0.0 for value in production.values()):
        raise ValueError("regional production impulse creates negative production")
    if any(value < 0.0 for value in refinery.values()):
        raise ValueError("regional refinery impulse creates negative refinery demand")

    balances: list[dict[str, Any]] = []
    for region in regions:
        region_id = str(region["region_id"])
        inventory_change_mbd = inventory[region_id] / days
        net_balance = (
            production[region_id]
            - refinery[region_id]
            - inventory_change_mbd
            - pipeline[region_id]
        )
        balances.append(
            {
                "region_id": region_id,
                "region_name": str(region["region_name"]),
                "production_mbd": round(production[region_id], 8),
                "production_policy_adjustment_mbd": round(
                    policy_adjustments[region_id],
                    8,
                ),
                "production_policy_target_mbd": round(
                    production_policy["target_mbd"]
                    if region_id == policy_region_id
                    else (
                        -production_policy["target_mbd"]
                        if region_id == policy_balancing_region_id
                        else 0.0
                    ),
                    8,
                ),
                "production_policy_decision_month": bool(
                    is_policy_decision_month
                    and region_id in {policy_region_id, policy_balancing_region_id}
                ),
                "production_cycle_adjustment_mbd": round(
                    production_cycle_adjustments[region_id],
                    8,
                ),
                "production_cycle_target_mbd": round(
                    us_gulf_cycle["production_target_mbd"]
                    if region_id == us_gulf_region_id
                    else (
                        -us_gulf_cycle["production_target_mbd"]
                        if region_id == us_gulf_production_balancing_id
                        else 0.0
                    ),
                    8,
                ),
                "production_cycle_decision_month": bool(
                    is_us_gulf_production_decision_month
                    and region_id
                    in {us_gulf_region_id, us_gulf_production_balancing_id}
                ),
                "refinery_demand_mbd": round(refinery[region_id], 8),
                "refinery_cycle_adjustment_mbd": round(
                    refinery_cycle_adjustments[region_id],
                    8,
                ),
                "refinery_cycle_target_mbd": round(
                    refinery_cycle_target
                    if region_id == us_gulf_region_id
                    else (
                        -refinery_cycle_target
                        if region_id == us_gulf_refinery_balancing_id
                        else 0.0
                    ),
                    8,
                ),
                "inventory_change_mmbbl": round(inventory[region_id], 8),
                "inventory_change_mbd": round(inventory_change_mbd, 8),
                "pipeline_net_exports_mbd": round(pipeline[region_id], 8),
                "net_seaborne_balance_mbd": round(net_balance, 8),
                "trade_role": "exporter" if net_balance > 0.0 else "importer",
            }
        )

    export_supply = sum(
        max(0.0, float(region["net_seaborne_balance_mbd"]))
        for region in balances
    )
    import_requirement = sum(
        max(0.0, -float(region["net_seaborne_balance_mbd"]))
        for region in balances
    )
    record = {
        "regional_balances": balances,
        "regional_export_supply_mbd": round(export_supply, 8),
        "regional_import_requirement_mbd": round(import_requirement, 8),
        "regional_production_residual_mbd": round(
            sum(production.values()) - global_production,
            8,
        ),
        "regional_refinery_residual_mbd": round(
            sum(refinery.values()) - global_refinery_demand,
            8,
        ),
        "regional_inventory_residual_mmbbl": round(
            sum(inventory.values()) - global_inventory_change,
            8,
        ),
        "regional_pipeline_residual_mbd": round(sum(pipeline.values()), 8),
        "regional_net_balance_residual_mbd": round(
            sum(float(region["net_seaborne_balance_mbd"]) for region in balances),
            8,
        ),
    }
    next_state = {
        "production_shares": production_shares,
        "refinery_shares": refinery_shares,
        "pipeline_net_exports_mbd": pipeline,
        "production_policy": production_policy,
        "us_gulf_cycle": us_gulf_cycle,
    }
    return next_state, record
