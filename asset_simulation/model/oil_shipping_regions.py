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
        "crude_run_shares": {
            str(region["region_id"]): float(region["initial_crude_run_share"])
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
        "brazil_guyana_cycle": {
            "project_target_mbd": 0.0,
            "project_deviation_mbd": 0.0,
            "operational_deviation_mbd": 0.0,
        },
        "west_africa_cycle": {
            "project_target_mbd": 0.0,
            "project_deviation_mbd": 0.0,
            "operational_deviation_mbd": 0.0,
        },
        "other_export_cycle": {
            "operational_deviation_mbd": 0.0,
        },
        "east_asia_cycle": {
            "refinery_operational_deviation_mbd": 0.0,
            "refinery_deviation_mbd": 0.0,
        },
        "south_asia_cycle": {
            "refinery_operational_deviation_mbd": 0.0,
            "refinery_deviation_mbd": 0.0,
        },
        "europe_cycle": {
            "refinery_operational_deviation_mbd": 0.0,
            "refinery_deviation_mbd": 0.0,
        },
        "north_america_import_cycle": {
            "refinery_operational_deviation_mbd": 0.0,
            "refinery_deviation_mbd": 0.0,
        },
        "rest_of_world_cycle": {
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
    month: int,
    persistence: float,
    news_scale: float,
) -> dict[str, float]:
    raw: dict[str, float] = {}
    for region in regions:
        region_id = str(region["region_id"])
        baseline = float(region[f"initial_{field_prefix}_share"])
        low, high = map(float, region[f"{field_prefix}_share_bounds"])
        annual_bias = (
            float(region.get(f"{field_prefix}_share_annual_bias_pct", 0.0))
            if month == 1 and turn_index > 0
            else 0.0
        )
        raw[region_id] = clamp(
            baseline
            + persistence * (float(previous[region_id]) - baseline)
            + float(previous[region_id]) * annual_bias / 100.0
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


def _apply_owner_excluded_overlay(
    values: dict[str, float],
    conservation_adjustments: dict[str, float],
    *,
    owner_region_id: str,
    overlay_mbd: float,
    weights: Mapping[str, float],
) -> None:
    """Apply one named regional overlay without attenuating its owner.

    The complete policy or operating overlay lands in its originating region.
    Its zero-sum counterpart is distributed across every *other* region using
    the registered physical shares.  This keeps the causal direction inside
    the regional allocation stage while preventing one visible basin from
    becoming the exclusive accounting absorber.
    """

    if owner_region_id not in values:
        raise KeyError(f"unknown overlay owner: {owner_region_id}")
    recipients = [region_id for region_id in values if region_id != owner_region_id]
    weight_sum = sum(max(float(weights[region_id]), 0.0) for region_id in recipients)
    if weight_sum <= 0.0:
        raise ValueError("owner-excluded conservation has no positive recipient weight")
    overlay = float(overlay_mbd)
    values[owner_region_id] += overlay
    distributed = 0.0
    for region_id in recipients:
        offset = -overlay * max(float(weights[region_id]), 0.0) / weight_sum
        values[region_id] += offset
        conservation_adjustments[region_id] += offset
        distributed += offset
    rounding_residual = overlay + distributed
    if rounding_residual:
        anchor = max(recipients, key=lambda region_id: float(weights[region_id]))
        values[anchor] -= rounding_residual
        conservation_adjustments[anchor] -= rounding_residual


def _close_conservation_rounding(
    values: dict[str, float],
    conservation_adjustments: dict[str, float],
    *,
    global_total: float,
    weights: Mapping[str, float],
) -> None:
    """Put only floating-point closure dust on the largest physical region."""

    residual = float(global_total) - sum(values.values())
    if residual:
        anchor = max(values, key=lambda region_id: float(weights[region_id]))
        values[anchor] += residual
        conservation_adjustments[anchor] += residual


def _advance_refinery_operating_cycle(
    previous: Mapping[str, float],
    *,
    seed: int,
    turn_index: int,
    month: int,
    refinery_config: Mapping[str, Any],
    news_prefix: str,
    profile_label: str,
) -> tuple[dict[str, float], float]:
    """Advance a regional refinery maintenance window and operating noise."""

    monthly_profile = list(map(float, refinery_config["baseline_monthly_profile_mbd"]))
    if len(monthly_profile) != 12:
        raise ValueError(
            f"{profile_label} refinery baseline monthly profile must contain 12 values"
        )
    year_index = turn_index // 12
    maintenance_kernel = list(map(float, refinery_config["maintenance_kernel"]))
    if not maintenance_kernel or any(value < 0.0 for value in maintenance_kernel):
        raise ValueError(
            f"{profile_label} refinery maintenance kernel must be nonnegative"
        )
    for season in ("spring", "fall"):
        maintenance = refinery_config[f"{season}_maintenance"]
        start_month = int(
            round(
                clamp(
                    float(maintenance["start_center_month"])
                    + float(maintenance["start_news_scale_months"])
                    * normal(
                        seed,
                        f"{news_prefix}_{season}_maintenance_timing",
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
                f"{news_prefix}_{season}_maintenance_depth",
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
        * normal(seed, f"{news_prefix}_refinery_operations", turn_index),
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
            "refinery_operational_deviation_mbd": operational_deviation,
            "refinery_deviation_mbd": refinery_deviation,
        },
        refinery_target,
    )


def _advance_production_policy(
    previous: Mapping[str, float],
    crude_turn: Mapping[str, float],
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
            * float(crude_turn["crude_inventory_supply_pressure_pct"])
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

    refinery_state, refinery_target = _advance_refinery_operating_cycle(
        previous,
        seed=seed,
        turn_index=turn_index,
        month=month,
        refinery_config=cycle["refinery"],
        news_prefix="oil_region_us_gulf",
        profile_label="US Gulf",
    )
    return (
        {
            "production_target_mbd": production_target,
            "production_deviation_mbd": production_deviation,
            **refinery_state,
        },
        is_production_decision_month,
        refinery_target,
    )


def _advance_brazil_guyana_cycle(
    previous: Mapping[str, float],
    *,
    seed: int,
    turn_index: int,
    month: int,
    cycle: Mapping[str, Any],
) -> dict[str, float]:
    """Advance offshore project timing and persistent operating variability.

    The secular production-share bias owns long-run Brazil/Guyana growth. This
    overlay only changes the timing around that trend: annual FPSO/project
    commissioning news moves a sticky target, while offshore uptime,
    maintenance, and weather create smaller persistent monthly deviations.
    """

    project_target = float(previous["project_target_mbd"])
    if month == int(cycle["decision_month"]):
        year_index = turn_index // 12
        project_target = clamp(
            float(cycle["project_target_persistence"]) * project_target
            + float(cycle["annual_project_news_scale_mbd"])
            * normal(
                seed,
                "oil_region_brazil_guyana_project_timing",
                year_index,
            ),
            *map(float, cycle["project_target_bounds_mbd"]),
        )

    previous_project = float(previous["project_deviation_mbd"])
    project_change = clamp(
        float(cycle["project_adjustment_speed"])
        * (project_target - previous_project),
        -float(cycle["maximum_monthly_project_adjustment_mbd"]),
        float(cycle["maximum_monthly_project_adjustment_mbd"]),
    )
    project_deviation = previous_project + project_change

    previous_operations = float(previous["operational_deviation_mbd"])
    operational_deviation = clamp(
        float(cycle["operational_persistence"]) * previous_operations
        + float(cycle["operational_news_scale_mbd"])
        * normal(
            seed,
            "oil_region_brazil_guyana_offshore_operations",
            turn_index,
        ),
        *map(float, cycle["operational_bounds_mbd"]),
    )

    return {
        "project_target_mbd": project_target,
        "project_deviation_mbd": project_deviation,
        "operational_deviation_mbd": operational_deviation,
    }


def _advance_west_africa_cycle(
    previous: Mapping[str, float],
    *,
    seed: int,
    turn_index: int,
    month: int,
    cycle: Mapping[str, Any],
) -> dict[str, float]:
    """Advance terminal disruptions and slower Atlantic project timing.

    The secular production-share bias owns long-run West African growth or
    decline. This overlay is the ordinary basin cycle: sticky force-majeure
    and export-terminal outages, plus slower FPSO/project timing around
    Angola turnarounds and new Atlantic barrels.
    """

    project_target = float(previous["project_target_mbd"])
    if month == int(cycle["decision_month"]):
        year_index = turn_index // 12
        project_target = clamp(
            float(cycle["project_target_persistence"]) * project_target
            + float(cycle["annual_project_news_scale_mbd"])
            * normal(
                seed,
                "oil_region_west_africa_project_timing",
                year_index,
            ),
            *map(float, cycle["project_target_bounds_mbd"]),
        )

    previous_project = float(previous["project_deviation_mbd"])
    project_change = clamp(
        float(cycle["project_adjustment_speed"])
        * (project_target - previous_project),
        -float(cycle["maximum_monthly_project_adjustment_mbd"]),
        float(cycle["maximum_monthly_project_adjustment_mbd"]),
    )
    project_deviation = previous_project + project_change

    previous_operations = float(previous["operational_deviation_mbd"])
    operational_deviation = clamp(
        float(cycle["operational_persistence"]) * previous_operations
        + float(cycle["operational_news_scale_mbd"])
        * normal(
            seed,
            "oil_region_west_africa_disruptions",
            turn_index,
        ),
        *map(float, cycle["operational_bounds_mbd"]),
    )

    return {
        "project_target_mbd": project_target,
        "project_deviation_mbd": project_deviation,
        "operational_deviation_mbd": operational_deviation,
    }


def _advance_other_export_cycle(
    previous: Mapping[str, float],
    *,
    seed: int,
    turn_index: int,
    cycle: Mapping[str, Any],
) -> dict[str, float]:
    """Advance ordinary operating noise for the residual export basket.

    Long-run other-export growth or decline stays in the production-share
    bias. This overlay only represents mixed mature-basin uptime: North Sea
    and Caspian maintenance, Russian operating variability, and similar
    uncorrelated noise that a basket should keep after share allocation.
    """

    operational_deviation = clamp(
        float(cycle["operational_persistence"])
        * float(previous["operational_deviation_mbd"])
        + float(cycle["operational_news_scale_mbd"])
        * normal(seed, "oil_region_other_export_operations", turn_index),
        *map(float, cycle["operational_bounds_mbd"]),
    )
    return {"operational_deviation_mbd": operational_deviation}


def _advance_south_asia_cycle(
    previous: Mapping[str, float],
    *,
    seed: int,
    turn_index: int,
    month: int,
    cycle: Mapping[str, Any],
) -> tuple[dict[str, float], float]:
    """Advance South Asian pre-monsoon and post-monsoon turnarounds.

    The crude-run share bias owns long-run South Asian refining growth, the
    fastest import-basin expansion in the catalog. This overlay is the
    ordinary operating cycle around that trend: April–May turnarounds before
    the monsoon, a smaller September–October window, and persistent coastal
    refinery operating noise.
    """

    return _advance_refinery_operating_cycle(
        previous,
        seed=seed,
        turn_index=turn_index,
        month=month,
        refinery_config=cycle["refinery"],
        news_prefix="oil_region_south_asia",
        profile_label="South Asia",
    )


def _advance_europe_cycle(
    previous: Mapping[str, float],
    *,
    seed: int,
    turn_index: int,
    month: int,
    cycle: Mapping[str, Any],
) -> tuple[dict[str, float], float]:
    """Advance European spring and autumn turnarounds around winter heating.

    The crude-run share bias owns long-run European refining contraction.
    This overlay is the ordinary Atlantic-basin operating cycle: March–April
    turnarounds after winter, an earlier August–September window before
    heating demand, and persistent ARA/Mediterranean operating noise.
    """

    return _advance_refinery_operating_cycle(
        previous,
        seed=seed,
        turn_index=turn_index,
        month=month,
        refinery_config=cycle["refinery"],
        news_prefix="oil_region_europe",
        profile_label="Europe",
    )


def _advance_north_america_import_cycle(
    previous: Mapping[str, float],
    *,
    seed: int,
    turn_index: int,
    month: int,
    cycle: Mapping[str, Any],
) -> tuple[dict[str, float], float]:
    """Advance East Coast/Midwest/Canada turnarounds away from the US Gulf.

    US Gulf already owns the January–February PADD 3 window. This overlay is
    the remaining North American import-basin cycle: May–June Midwest and
    Canadian turnarounds, a later October–November window after driving
    season, and persistent Atlantic/Great Lakes operating noise. Long-run
    inland production growth and Atlantic refining contraction stay in the
    share biases.
    """

    return _advance_refinery_operating_cycle(
        previous,
        seed=seed,
        turn_index=turn_index,
        month=month,
        refinery_config=cycle["refinery"],
        news_prefix="oil_region_north_america_import",
        profile_label="North America import",
    )


def _advance_rest_of_world_cycle(
    previous: Mapping[str, float],
    *,
    seed: int,
    turn_index: int,
    month: int,
    cycle: Mapping[str, Any],
) -> tuple[dict[str, float], float]:
    """Advance mixed-latitude remaining-importer turnarounds and operating noise.

    Rest of world is the leftover import basket — Southeast Asian hubs,
    Oceania, Pacific Latin America, the Caribbean, and African importers —
    not a single OECD calendar and not the mechanical inverse of US Gulf
    maintenance. The crude-run share bias owns the slow refining drift.
    This overlay is the ordinary basket cycle: February–March Southeast
    Asian and southern-hemisphere turnarounds after the US Gulf window, a
    July–August tropical mid-year window that no other basin owns, and
    persistent independent-plant operating noise. The registered US Gulf
    refinery pair is applied separately before this overlay.
    """

    return _advance_refinery_operating_cycle(
        previous,
        seed=seed,
        turn_index=turn_index,
        month=month,
        refinery_config=cycle["refinery"],
        news_prefix="oil_region_rest_of_world",
        profile_label="Rest of world",
    )


def _advance_east_asia_cycle(
    previous: Mapping[str, float],
    *,
    seed: int,
    turn_index: int,
    month: int,
    cycle: Mapping[str, Any],
) -> tuple[dict[str, float], float]:
    """Advance Northeast Asian spring/autumn turnarounds and operating noise.

    The crude-run share bias owns long-run East Asian refining growth. This
    overlay is the ordinary import-basin cycle: later spring and autumn
    maintenance than the US Gulf, plus persistent independent-refinery and
    petrochemical operating variability.
    """

    return _advance_refinery_operating_cycle(
        previous,
        seed=seed,
        turn_index=turn_index,
        month=month,
        refinery_config=cycle["refinery"],
        news_prefix="oil_region_east_asia",
        profile_label="East Asia",
    )


def advance_regional_balance(
    state: Mapping[str, Mapping[str, float]],
    crude_turn: Mapping[str, float],
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
    conservation = regional.get("production_conservation", {})
    if str(conservation.get("method", "")) != "owner_excluded_share_weighted_offsets":
        raise ValueError("unsupported regional production conservation method")
    if str(conservation.get("weighting", "production_share")) != "production_share":
        raise ValueError("only production-share conservation weighting is implemented")

    production_shares = _evolve_shares(
        state["production_shares"],
        regions,
        field_prefix="production",
        seed=seed,
        turn_index=turn_index,
        month=month,
        persistence=float(regional["share_persistence"]),
        news_scale=float(regional["production_share_news_scale"]),
    )
    crude_run_shares = _evolve_shares(
        state["crude_run_shares"],
        regions,
        field_prefix="crude_run",
        seed=seed,
        turn_index=turn_index,
        month=month,
        persistence=float(regional["share_persistence"]),
        news_scale=float(regional["crude_run_share_news_scale"]),
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

    global_production = float(crude_turn["crude_production_mbd"])
    global_crude_runs = float(crude_turn["crude_refinery_runs_mbd"])
    global_inventory_change = float(crude_turn["crude_inventory_change_mmbbl"])
    days = int(crude_turn["days"])
    production = {
        region_id: global_production * share
        for region_id, share in production_shares.items()
    }
    crude_runs = {
        region_id: global_crude_runs * share
        for region_id, share in crude_run_shares.items()
    }
    base_production = dict(production)
    base_crude_runs = dict(crude_runs)
    inventory = {
        str(region["region_id"]): global_inventory_change
        * float(region["inventory_change_share"])
        for region in regions
    }
    inventory[balancing_region_id] += global_inventory_change - sum(inventory.values())

    policy_config = regional["production_policy"]
    policy_region_id = str(policy_config["region_id"])
    if policy_region_id not in known_region_ids:
        raise KeyError(f"unknown production-policy region: {policy_region_id}")
    production_policy, is_policy_decision_month = _advance_production_policy(
        state["production_policy"],
        crude_turn,
        seed=seed,
        turn_index=turn_index,
        policy=policy_config,
    )
    policy_adjustments = {region_id: 0.0 for region_id in known_region_ids}
    policy_adjustments[policy_region_id] = production_policy["deviation_mbd"]

    us_gulf_config = regional["us_gulf_cycle"]
    us_gulf_region_id = str(us_gulf_config["region_id"])
    us_gulf_refinery_balancing_id = str(
        us_gulf_config["refinery_balancing_region_id"]
    )
    for region_id in {us_gulf_region_id, us_gulf_refinery_balancing_id}:
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
    refinery_cycle_adjustments = {region_id: 0.0 for region_id in known_region_ids}
    refinery_conservation_adjustments = {
        region_id: 0.0 for region_id in known_region_ids
    }
    refinery_cycle_adjustments[us_gulf_region_id] = us_gulf_cycle[
        "refinery_deviation_mbd"
    ]
    refinery_cycle_adjustments[us_gulf_refinery_balancing_id] = -us_gulf_cycle[
        "refinery_deviation_mbd"
    ]
    for region_id in known_region_ids:
        crude_runs[region_id] += refinery_cycle_adjustments[region_id]

    east_asia_config = regional["east_asia_cycle"]
    east_asia_region_id = str(east_asia_config["region_id"])
    if east_asia_region_id not in known_region_ids:
        raise KeyError(f"unknown East Asia cycle region: {east_asia_region_id}")
    east_asia_cycle, east_asia_refinery_target = _advance_east_asia_cycle(
        state["east_asia_cycle"],
        seed=seed,
        turn_index=turn_index,
        month=month,
        cycle=east_asia_config,
    )
    refinery_cycle_adjustments[east_asia_region_id] += east_asia_cycle[
        "refinery_deviation_mbd"
    ]
    _apply_owner_excluded_overlay(
        crude_runs,
        refinery_conservation_adjustments,
        owner_region_id=east_asia_region_id,
        overlay_mbd=east_asia_cycle["refinery_deviation_mbd"],
        weights=crude_run_shares,
    )

    south_asia_config = regional["south_asia_cycle"]
    south_asia_region_id = str(south_asia_config["region_id"])
    if south_asia_region_id not in known_region_ids:
        raise KeyError(f"unknown South Asia cycle region: {south_asia_region_id}")
    south_asia_cycle, south_asia_refinery_target = _advance_south_asia_cycle(
        state["south_asia_cycle"],
        seed=seed,
        turn_index=turn_index,
        month=month,
        cycle=south_asia_config,
    )
    refinery_cycle_adjustments[south_asia_region_id] += south_asia_cycle[
        "refinery_deviation_mbd"
    ]
    _apply_owner_excluded_overlay(
        crude_runs,
        refinery_conservation_adjustments,
        owner_region_id=south_asia_region_id,
        overlay_mbd=south_asia_cycle["refinery_deviation_mbd"],
        weights=crude_run_shares,
    )

    europe_config = regional["europe_cycle"]
    europe_region_id = str(europe_config["region_id"])
    if europe_region_id not in known_region_ids:
        raise KeyError(f"unknown Europe cycle region: {europe_region_id}")
    europe_cycle, europe_refinery_target = _advance_europe_cycle(
        state["europe_cycle"],
        seed=seed,
        turn_index=turn_index,
        month=month,
        cycle=europe_config,
    )
    refinery_cycle_adjustments[europe_region_id] += europe_cycle[
        "refinery_deviation_mbd"
    ]
    _apply_owner_excluded_overlay(
        crude_runs,
        refinery_conservation_adjustments,
        owner_region_id=europe_region_id,
        overlay_mbd=europe_cycle["refinery_deviation_mbd"],
        weights=crude_run_shares,
    )

    north_america_config = regional["north_america_import_cycle"]
    north_america_region_id = str(north_america_config["region_id"])
    if north_america_region_id not in known_region_ids:
        raise KeyError(
            f"unknown North America import cycle region: {north_america_region_id}"
        )
    north_america_import_cycle, north_america_refinery_target = (
        _advance_north_america_import_cycle(
            state["north_america_import_cycle"],
            seed=seed,
            turn_index=turn_index,
            month=month,
            cycle=north_america_config,
        )
    )
    refinery_cycle_adjustments[north_america_region_id] += (
        north_america_import_cycle["refinery_deviation_mbd"]
    )
    _apply_owner_excluded_overlay(
        crude_runs,
        refinery_conservation_adjustments,
        owner_region_id=north_america_region_id,
        overlay_mbd=north_america_import_cycle["refinery_deviation_mbd"],
        weights=crude_run_shares,
    )

    rest_of_world_config = regional["rest_of_world_cycle"]
    rest_of_world_region_id = str(rest_of_world_config["region_id"])
    if rest_of_world_region_id not in known_region_ids:
        raise KeyError(
            f"unknown rest-of-world cycle region: {rest_of_world_region_id}"
        )
    if rest_of_world_region_id != us_gulf_refinery_balancing_id:
        raise ValueError(
            "rest-of-world cycle must stay on the US Gulf refinery balancing region"
        )
    rest_of_world_cycle, rest_of_world_refinery_target = (
        _advance_rest_of_world_cycle(
            state["rest_of_world_cycle"],
            seed=seed,
            turn_index=turn_index,
            month=month,
            cycle=rest_of_world_config,
        )
    )
    refinery_cycle_adjustments[rest_of_world_region_id] += rest_of_world_cycle[
        "refinery_deviation_mbd"
    ]
    _apply_owner_excluded_overlay(
        crude_runs,
        refinery_conservation_adjustments,
        owner_region_id=rest_of_world_region_id,
        overlay_mbd=rest_of_world_cycle["refinery_deviation_mbd"],
        weights=crude_run_shares,
    )
    unconstrained_crude_runs = {
        str(region["region_id"]): base_crude_runs[str(region["region_id"])]
        + refinery_cycle_adjustments[str(region["region_id"])]
        for region in regions
    }
    _close_conservation_rounding(
        crude_runs,
        refinery_conservation_adjustments,
        global_total=global_crude_runs,
        weights=crude_run_shares,
    )
    for region_id, value in refinery_cycle_adjustments.items():
        refinery_cycle_adjustments[region_id] = round(value, 8)
    for region_id, value in refinery_conservation_adjustments.items():
        refinery_conservation_adjustments[region_id] = round(value, 8)

    brazil_guyana_config = regional["brazil_guyana_cycle"]
    brazil_guyana_region_id = str(brazil_guyana_config["region_id"])
    if brazil_guyana_region_id not in known_region_ids:
        raise KeyError(f"unknown Brazil/Guyana cycle region: {brazil_guyana_region_id}")
    brazil_guyana_cycle = _advance_brazil_guyana_cycle(
        state["brazil_guyana_cycle"],
        seed=seed,
        turn_index=turn_index,
        month=month,
        cycle=brazil_guyana_config,
    )
    brazil_guyana_adjustment = clamp(
        brazil_guyana_cycle["project_deviation_mbd"]
        + brazil_guyana_cycle["operational_deviation_mbd"],
        *map(float, brazil_guyana_config["combined_adjustment_bounds_mbd"]),
    )
    production_cycle_adjustments[brazil_guyana_region_id] += brazil_guyana_adjustment
    is_brazil_guyana_decision_month = month == int(
        brazil_guyana_config["decision_month"]
    )

    west_africa_config = regional["west_africa_cycle"]
    west_africa_region_id = str(west_africa_config["region_id"])
    if west_africa_region_id not in known_region_ids:
        raise KeyError(f"unknown West Africa cycle region: {west_africa_region_id}")
    west_africa_cycle = _advance_west_africa_cycle(
        state["west_africa_cycle"],
        seed=seed,
        turn_index=turn_index,
        month=month,
        cycle=west_africa_config,
    )
    west_africa_adjustment = clamp(
        west_africa_cycle["project_deviation_mbd"]
        + west_africa_cycle["operational_deviation_mbd"],
        *map(float, west_africa_config["combined_adjustment_bounds_mbd"]),
    )
    production_cycle_adjustments[west_africa_region_id] += west_africa_adjustment
    is_west_africa_decision_month = month == int(
        west_africa_config["decision_month"]
    )

    other_export_config = regional["other_export_cycle"]
    other_export_region_id = str(other_export_config["region_id"])
    if other_export_region_id not in known_region_ids:
        raise KeyError(f"unknown other-export cycle region: {other_export_region_id}")
    other_export_cycle = _advance_other_export_cycle(
        state["other_export_cycle"],
        seed=seed,
        turn_index=turn_index,
        cycle=other_export_config,
    )
    production_cycle_adjustments[other_export_region_id] += other_export_cycle[
        "operational_deviation_mbd"
    ]

    unconstrained_production = {
        str(region["region_id"]): base_production[str(region["region_id"])]
        + policy_adjustments[str(region["region_id"])]
        + production_cycle_adjustments[str(region["region_id"])]
        for region in regions
    }
    production = dict(base_production)
    conservation_adjustments = {
        region_id: 0.0 for region_id in known_region_ids
    }
    for region_id, adjustment in policy_adjustments.items():
        if adjustment:
            _apply_owner_excluded_overlay(
                production,
                conservation_adjustments,
                owner_region_id=region_id,
                overlay_mbd=adjustment,
                weights=production_shares,
            )
    for region_id, adjustment in production_cycle_adjustments.items():
        if adjustment:
            _apply_owner_excluded_overlay(
                production,
                conservation_adjustments,
                owner_region_id=region_id,
                overlay_mbd=adjustment,
                weights=production_shares,
            )
    _close_conservation_rounding(
        production,
        conservation_adjustments,
        global_total=global_production,
        weights=production_shares,
    )
    for region_id, value in production_cycle_adjustments.items():
        production_cycle_adjustments[region_id] = round(value, 8)
    for region_id, value in conservation_adjustments.items():
        conservation_adjustments[region_id] = round(value, 8)

    _apply_zero_sum_impulses(
        production,
        impulse.get("regional_crude_production_impulse_mbd", {}),
        known_region_ids=known_region_ids,
        balancing_region_id=str(
            regional["crude_production_impulse_balancing_region_id"]
        ),
        field_name="regional_crude_production_impulse_mbd",
    )
    _apply_zero_sum_impulses(
        crude_runs,
        impulse.get("regional_crude_runs_impulse_mbd", {}),
        known_region_ids=known_region_ids,
        balancing_region_id=str(regional["crude_runs_impulse_balancing_region_id"]),
        field_name="regional_crude_runs_impulse_mbd",
    )
    _apply_zero_sum_impulses(
        inventory,
        impulse.get("regional_crude_inventory_impulse_mmbbl", {}),
        known_region_ids=known_region_ids,
        balancing_region_id=str(
            regional["crude_inventory_impulse_balancing_region_id"]
        ),
        field_name="regional_crude_inventory_impulse_mmbbl",
    )
    if any(value < 0.0 for value in production.values()):
        raise ValueError("regional production impulse creates negative production")
    if any(value < 0.0 for value in crude_runs.values()):
        raise ValueError("regional crude-run impulse creates negative refinery runs")

    balances: list[dict[str, Any]] = []
    for region in regions:
        region_id = str(region["region_id"])
        inventory_change_mbd = inventory[region_id] / days
        net_balance = (
            production[region_id]
            - crude_runs[region_id]
            - inventory_change_mbd
            - pipeline[region_id]
        )
        balances.append(
            {
                "region_id": region_id,
                "region_name": str(region["region_name"]),
                "base_crude_production_mbd": round(
                    base_production[region_id],
                    8,
                ),
                "crude_production_mbd": round(production[region_id], 8),
                "unconstrained_crude_production_mbd": round(
                    unconstrained_production[region_id],
                    8,
                ),
                "conservation_adjustment_mbd": round(
                    conservation_adjustments[region_id],
                    8,
                ),
                "effective_production_adjustment_mbd": round(
                    production[region_id] - base_production[region_id],
                    8,
                ),
                "production_policy_adjustment_mbd": round(
                    policy_adjustments[region_id],
                    8,
                ),
                "production_policy_target_mbd": round(
                    production_policy["target_mbd"]
                    if region_id == policy_region_id
                    else 0.0,
                    8,
                ),
                "production_policy_decision_month": bool(
                    is_policy_decision_month and region_id == policy_region_id
                ),
                "production_cycle_adjustment_mbd": round(
                    production_cycle_adjustments[region_id],
                    8,
                ),
                "production_cycle_target_mbd": round(
                    (
                        us_gulf_cycle["production_target_mbd"]
                        if region_id == us_gulf_region_id
                        else 0.0
                    )
                    + (
                        brazil_guyana_cycle["project_target_mbd"]
                        if region_id == brazil_guyana_region_id
                        else 0.0
                    )
                    + (
                        west_africa_cycle["project_target_mbd"]
                        if region_id == west_africa_region_id
                        else 0.0
                    )
                    + (
                        other_export_cycle["operational_deviation_mbd"]
                        if region_id == other_export_region_id
                        else 0.0
                    ),
                    8,
                ),
                "production_cycle_decision_month": bool(
                    (
                        is_us_gulf_production_decision_month
                        and region_id == us_gulf_region_id
                    )
                    or (
                        is_brazil_guyana_decision_month
                        and region_id == brazil_guyana_region_id
                    )
                    or (
                        is_west_africa_decision_month
                        and region_id == west_africa_region_id
                    )
                ),
                "base_crude_refinery_runs_mbd": round(
                    base_crude_runs[region_id],
                    8,
                ),
                "crude_refinery_runs_mbd": round(crude_runs[region_id], 8),
                "unconstrained_crude_refinery_runs_mbd": round(
                    unconstrained_crude_runs[region_id],
                    8,
                ),
                "refinery_cycle_adjustment_mbd": round(
                    refinery_cycle_adjustments[region_id],
                    8,
                ),
                "refinery_conservation_adjustment_mbd": round(
                    refinery_conservation_adjustments[region_id],
                    8,
                ),
                "effective_refinery_adjustment_mbd": round(
                    crude_runs[region_id] - base_crude_runs[region_id],
                    8,
                ),
                "refinery_cycle_target_mbd": round(
                    (
                        refinery_cycle_target
                        if region_id == us_gulf_region_id
                        else 0.0
                    )
                    + (
                        east_asia_refinery_target
                        if region_id == east_asia_region_id
                        else 0.0
                    )
                    + (
                        south_asia_refinery_target
                        if region_id == south_asia_region_id
                        else 0.0
                    )
                    + (
                        europe_refinery_target
                        if region_id == europe_region_id
                        else 0.0
                    )
                    + (
                        north_america_refinery_target
                        if region_id == north_america_region_id
                        else 0.0
                    )
                    + (
                        rest_of_world_refinery_target
                        if region_id == rest_of_world_region_id
                        else 0.0
                    )
                    + (
                        -refinery_cycle_target
                        if region_id == us_gulf_refinery_balancing_id
                        else 0.0
                    ),
                    8,
                ),
                "crude_inventory_change_mmbbl": round(inventory[region_id], 8),
                "crude_inventory_change_mbd": round(inventory_change_mbd, 8),
                "crude_pipeline_net_exports_mbd": round(pipeline[region_id], 8),
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
        "regional_crude_production_residual_mbd": round(
            sum(production.values()) - global_production,
            8,
        ),
        "regional_crude_runs_residual_mbd": round(
            sum(crude_runs.values()) - global_crude_runs,
            8,
        ),
        "regional_crude_inventory_residual_mmbbl": round(
            sum(inventory.values()) - global_inventory_change,
            8,
        ),
        "regional_crude_pipeline_residual_mbd": round(sum(pipeline.values()), 8),
        "regional_net_balance_residual_mbd": round(
            sum(float(region["net_seaborne_balance_mbd"]) for region in balances),
            8,
        ),
        "regional_production_conservation_residual_mbd": round(
            sum(conservation_adjustments.values())
            + sum(policy_adjustments.values())
            + sum(production_cycle_adjustments.values()),
            8,
        ),
        "regional_refinery_conservation_residual_mbd": round(
            sum(refinery_cycle_adjustments.values())
            + sum(refinery_conservation_adjustments.values()),
            8,
        ),
    }
    next_state = {
        "production_shares": production_shares,
        "crude_run_shares": crude_run_shares,
        "pipeline_net_exports_mbd": pipeline,
        "production_policy": production_policy,
        "us_gulf_cycle": us_gulf_cycle,
        "brazil_guyana_cycle": brazil_guyana_cycle,
        "west_africa_cycle": west_africa_cycle,
        "other_export_cycle": other_export_cycle,
        "east_asia_cycle": east_asia_cycle,
        "south_asia_cycle": south_asia_cycle,
        "europe_cycle": europe_cycle,
        "north_america_import_cycle": north_america_import_cycle,
        "rest_of_world_cycle": rest_of_world_cycle,
    }
    return next_state, record
