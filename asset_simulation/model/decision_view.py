"""Month-close information boundary; full research objects never cross it.

This is an information adapter, not a game clock, save system or agent model.
At an intra-month decision use the last COMPLETED month. Annual row zero is
an initial condition; later annual rows are only visible after December closes.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .engine import GlobalMacroRun
from .oil_shipping_world import OilShippingWorld
from .oil_price_projection import OilPriceProjection
from .registry import sha256_json

MACRO_FIELDS = (
    'year', 'year_index', 'realized_growth_pct',
    'global_policy_rate_pct', 'global_2y_yield_pct', 'global_10y_yield_pct',
    'global_high_yield_spread_bps', 'headline_inflation_pct',
    'cpi_price_level_index_2025_100', 'brent_oil_price_usd',
)
PRICE_FIELDS = (
    'year', 'month', 'label', 'open_usd_per_bbl', 'high_usd_per_bbl',
    'low_usd_per_bbl', 'close_usd_per_bbl',
)
SHIPPING_FIELDS = (
    'year', 'month', 'label', 'days', 'macro_information_year',
    'realized_demand_mbd', 'production_mbd', 'closing_inventory_mmbbl',
    'crude_production_mbd', 'crude_refinery_runs_mbd',
    'crude_closing_inventory_mmbbl', 'seaborne_cargo_mbd',
    'average_haul_nm', 'tonne_nautical_miles_billion',
    'annualized_tonne_nautical_miles_billion',
)
ROUTE_FIELDS = (
    'route_id', 'route_name', 'origin_id', 'destination_id', 'is_other_pool',
    'cargo_mbd', 'cargo_million_tonnes', 'market_share',
    'baseline_haul_nm', 'effective_haul_nm', 'route_status',
)
REGION_FIELDS = (
    'region_id', 'region_name', 'crude_production_mbd',
    'crude_refinery_runs_mbd', 'crude_inventory_change_mmbbl',
    'crude_pipeline_net_exports_mbd', 'net_seaborne_balance_mbd', 'trade_role',
)


def _select(record: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    # An allowlist prevents newly added research diagnostics from leaking later.
    return deepcopy({key: record[key] for key in fields if key in record})


def build_decision_snapshot(
    global_run: GlobalMacroRun,
    shipping_world: OilShippingWorld,
    price_projection: OilPriceProjection,
    *,
    as_of_year: int,
    as_of_month: int,
) -> dict[str, Any]:
    """Publish data known at the END of a selected month, never future anchors."""
    for name, value in (('as_of_year', as_of_year), ('as_of_month', as_of_month)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f'{name} must be an integer')
    if not 1 <= as_of_month <= 12:
        raise ValueError('as_of_month must be between 1 and 12')
    if not shipping_world.start_year <= as_of_year <= shipping_world.end_year:
        raise ValueError('cutoff is outside the shipping world')
    if global_run.seed != shipping_world.seed or global_run.seed != price_projection.identity['seed']:
        raise ValueError('decision inputs must use the same seed')
    # Same seed alone is insufficient: both projections must come from this run.
    upstream = global_run.identity['identity_hash']
    if shipping_world.identity['upstream_global_identity_hash'] != upstream:
        raise ValueError('shipping projection belongs to a different macro run')
    if price_projection.identity['upstream_global_identity_hash'] != upstream:
        raise ValueError('price projection belongs to a different macro run')
    target = (as_of_year, as_of_month)
    annual_cutoff = as_of_year if as_of_month == 12 else as_of_year - 1
    macro_visible = [
        row for row in global_run.rows
        if int(row['year_index']) == 0 or int(row['year']) <= annual_cutoff
    ]
    shipping_visible = [
        row for row in shipping_world.turns
        if (int(row['year']), int(row['month'])) <= target
    ]
    prices_visible = [
        _select(row, PRICE_FIELDS) for row in price_projection.monthly
        if (int(row['year']), int(row['month'])) <= target
    ]
    if not shipping_visible or not prices_visible:
        raise ValueError('cutoff has no completed monthly observation')
    current = shipping_visible[-1]
    if (int(current['year']), int(current['month'])) != target:
        raise ValueError('shipping world does not contain the requested month')
    if (int(prices_visible[-1]['year']), int(prices_visible[-1]['month'])) != target:
        raise ValueError('price projection does not contain the requested month')
    shipping = _select(current, SHIPPING_FIELDS)
    shipping['routes'] = [_select(row, ROUTE_FIELDS) for row in current['routes']]
    shipping['regional_balances'] = [
        _select(row, REGION_FIELDS) for row in current['regional_balances']
    ]
    result = {
        'ok': True,
        'schemaVersion': 'asset-simulation-month-close-decision-v1',
        'scope': 'decision_month_close',
        'seed': global_run.seed,
        'asOf': {'year': as_of_year, 'month': as_of_month},
        'informationCutoff': 'completed_months_and_completed_annual_rows_only',
        'macro': _select(macro_visible[-1], MACRO_FIELDS),
        'oilPrices': prices_visible,
        'shipping': shipping,
    }
    # Hash only the visible data, not the full-run identity or future path.
    result['snapshot_hash'] = sha256_json(result)
    return result
