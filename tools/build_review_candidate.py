"""One-shot, guarded candidate construction. Never modifies main or parameters.

The bootstrap workflow removes this helper after applying and testing its
changes. The enduring branch contains ordinary source, tests and read-only CI.
"""
from pathlib import Path
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = '1f077c609111e6767866d1adc6b1796e2ccf87c2'


def replace(path, old, new, count=1):
    p = ROOT / path
    text = p.read_text(encoding='utf-8')
    actual = text.count(old)
    if actual != count:
        raise RuntimeError(f'{path}: expected {count} matches, got {actual}: {old[:100]!r}')
    p.write_text(text.replace(old, new), encoding='utf-8')


def write(path, content):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding='utf-8')


# Import only the isolated pricing experiment; no global fleet or monopoly code.
for path in (
    'asset_simulation/model/single_route_pricing.py',
    'asset_simulation/config/gulf_east_asia_pricing_v0.2.json',
    'asset_simulation/tests/test_single_route_pricing.py',
    'asset_simulation/audit_single_route_pricing.py',
):
    write(path, subprocess.check_output(['git', 'show', f'{SOURCE}:{path}'], cwd=ROOT, text=True))

p = 'asset_simulation/model/single_route_pricing.py'
replace(p, 'v0.2.0', 'v0.2.1')
replace(p, 'from .oil_shipping_world import OilShippingWorld', 'from .oil_shipping_world import OilShippingWorld\nfrom .registry import sha256_json')
replace(p, '    market = load_single_route_pricing_config() if config is None else dict(config)', '    market = load_single_route_pricing_config() if config is None else dict(config)', count=4)
# Validate public values and all numeric configuration before logarithms/rounding.
replace(p, 'def _validate_config(config: Mapping[str, Any]) -> None:\n', '''def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number, not bool")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _positive_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _validate_config(config: Mapping[str, Any]) -> None:
    def check_finite(node: Any, path: str = "config") -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                check_finite(value, f"{path}.{key}")
        elif isinstance(node, (int, float)):
            _finite(node, path)
    check_finite(config)
    for name in ("reference_route_cargo_mbd", "reference_prompt_supply_vlcc"):
        if _finite(config[name], name) <= 0:
            raise ValueError(f"{name} must be positive")
    for name, value in config["state_contract"].items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"state_contract.{name} must be an integer")
    if config["pricing"]["liquidity_smoothing_vlcc"] <= 0:
        raise ValueError("liquidity smoothing must be positive")
    for name in ("supply_demand_log_sensitivity", "inventory_urgency_log_sensitivity_per_day"):
        if config["pricing"][name] < 0:
            raise ValueError(f"{name} cannot be negative")
    for name in ("recovery_fraction_per_turn", "maximum_recovery_fraction_of_structural_flow"):
        if not 0 <= config["inventory"][name] <= 1:
            raise ValueError(f"{name} must lie in [0, 1]")
    if config["inventory"]["maximum_abs_gap_days_for_pricing"] <= 0:
        raise ValueError("maximum priced gap must be positive")
''')
replace(p, '    if month_days < 28 or month_days > 31:', '    _positive_integer(month_days, "month_days")\n    if month_days < 28 or month_days > 31:')
replace(p, '    cargo_mbd = float(route["cargo_mbd"])\n    if cargo_mbd <= 0.0:\n        raise ValueError(f"route cargo must remain positive: {route_id}")', '    cargo_mbd = _finite(route["cargo_mbd"], "route cargo")\n    if cargo_mbd < 0.0:\n        raise ValueError(f"route cargo cannot be negative: {route_id}")')
replace(p, '''    cargo_mbd = float(structural_cargo_mbd)
    days = int(turn_days)
    prompt_supply = float(prompt_supply_vlcc)
    origin = float(origin_inventory_deviation_mmbbl)
    destination = float(destination_inventory_deviation_mmbbl)
    cpi = float(cpi_price_level_index_2025_100)

    if cargo_mbd <= 0.0:
        raise ValueError("structural cargo rate must be positive")''', '''    cargo_mbd = _finite(structural_cargo_mbd, "structural cargo")
    days = _positive_integer(turn_days, "turn_days")
    prompt_supply = _finite(prompt_supply_vlcc, "prompt supply")
    origin = _finite(origin_inventory_deviation_mmbbl, "origin deviation")
    destination = _finite(destination_inventory_deviation_mmbbl, "destination deviation")
    cpi = _finite(cpi_price_level_index_2025_100, "CPI")

    if cargo_mbd < 0.0:
        raise ValueError("structural cargo rate cannot be negative")''')
replace(p, '    structural_cargo = cargo_mbd * days', '    structural_cargo = _finite(cargo_mbd * days, "turn cargo")')
replace(p, '    inventory_gap = 0.5 * (origin - destination)\n    inventory_gap_days = inventory_gap / cargo_mbd', '''    inventory_gap = _finite(0.5 * origin - 0.5 * destination, "inventory gap")
    # No-flow periods keep a diagnostic scale, but do not invent recovery cargo.
    gap_denominator = cargo_mbd if cargo_mbd > 0 else float(market["reference_route_cargo_mbd"])
    inventory_gap_days = _finite(inventory_gap / gap_denominator, "inventory gap days")''')
replace(p, '        else float(previous_real_tce_2025_usd_per_day)', '        else _finite(previous_real_tce_2025_usd_per_day, "previous TCE")')
replace(p, '    raw_real_tce = base_tce * math.exp(settled_log_signal)', '''    # Clamp before exponentiation as well as at the published-price guard.
    raw_real_tce = base_tce * math.exp(_clamp(settled_log_signal, -600.0, 600.0))
    if cargo_mbd == 0.0:
        raw_real_tce = previous_tce  # carry last indication; there is no new quote
    market_status = (
        "no_demand" if pricing_cargo_demand <= 0.0
        else "no_supply" if prompt_supply <= 0.0
        else "indicative_quote"
    )''')
replace(p, '    nominal_tce = real_tce * cpi / 100.0', '    nominal_tce = _finite(real_tce * (cpi / 100.0), "nominal TCE")')
replace(p, '        "model_version": MODEL_VERSION,\n        "route_id": str(market["route_id"]),', '''        "model_version": MODEL_VERSION,
        "pricing_config_hash": sha256_json(market),
        "market_status": market_status,
        "price_observation_available": market_status != "no_demand",
        "is_transaction_price": False,
        "route_id": str(market["route_id"]),''')
# The test supply must lag DAILY rate, then scale it to the CURRENT window.
replace(p, '    if supply_multiplier <= 0.0:', '    supply_multiplier = _finite(supply_multiplier, "supply multiplier")\n    if supply_multiplier <= 0.0:')
replace(p, '    if lag_turns < 0:', '    if isinstance(lag_turns, bool) or not isinstance(lag_turns, int) or lag_turns < 0:')
start = '    demand_equivalents: list[float] = []\n'
end = '    return tuple(supply)\n'
text = (ROOT / p).read_text(encoding='utf-8')
a = text.index(start)
b = text.index(end, a) + len(end)
text = text[:a] + '''    daily_rates: list[float] = []
    current_window_days: list[int] = []
    for month in months:
        daily_rate = _finite(month["cargo_mbd"], "monthly cargo")
        if daily_rate < 0:
            raise ValueError("monthly cargo cannot be negative")
        for days in shipping_turn_days(month["days"]):
            daily_rates.append(daily_rate)
            current_window_days.append(days)

    deltas = {} if temporary_supply_delta_by_turn is None else dict(temporary_supply_delta_by_turn)
    for key, value in deltas.items():
        if isinstance(key, bool) or not isinstance(key, int) or not 0 <= key < len(daily_rates):
            raise ValueError("supply delta index is outside the path")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("supply delta must be an integer vessel count")
    reference_days = float(market["reference_turn_days"])
    supply: list[int] = []
    for index, days in enumerate(current_window_days):
        lagged_daily_rate = (
            float(market["reference_route_cargo_mbd"])
            if index < lag_turns else daily_rates[index - lag_turns]
        )
        offered = _nearest_int(
            supply_multiplier * lagged_daily_rate * days / cargo_capacity
            + reference_buffer * days / reference_days
        )
        offered += deltas.get(index, 0)
        supply.append(max(0, offered))
    return tuple(supply)
''' + text[b:]
write(p, text)
replace(p, '    expected_turns = sum(3 for _ in months)', '''    if not months:
        raise ValueError("pricing path requires at least one month")
    expected_turns = sum(3 for _ in months)''')
replace(p, '        cpi = float(cpi_by_year[year])', '        cpi = _finite(cpi_by_year[year], "CPI")')
replace(p, '            prompt_supply = float(prompt_supply_by_turn[turn_index])', '            prompt_supply = _finite(prompt_supply_by_turn[turn_index], "prompt supply")')
replace(p, '                    "loaded_fixture_vlcc": loaded_fixtures,', '''                    "loaded_fixture_vlcc": loaded_fixtures,
                    "execution_status": "matched" if loaded_fixtures else "no_match",
                    "executed_fixture_tce_2025_usd_per_day": (
                        quote["real_tce_2025_usd_per_day"] if loaded_fixtures else None
                    ),''')
replace(p, '        "total_unfilled_fixture_vlcc": sum(', '''        # Compatibility alias: this is a sum of turn observations, NOT backlog.
        "total_unfilled_fixture_vlcc": sum(''')
replace(p, '''    return {
        "identity": {
            "model_version": MODEL_VERSION,''', '''    summary["cumulative_unfilled_fixture_observations_vlcc"] = summary["total_unfilled_fixture_vlcc"]
    summary["closing_origin_inventory_deviation_mmbbl"] = round(origin_deviation, 8)
    summary["closing_destination_inventory_deviation_mmbbl"] = round(destination_deviation, 8)
    summary["matched_fixture_count"] = sum(r["loaded_fixture_vlcc"] for r in records)
    summary["no_match_turn_count"] = sum(r["loaded_fixture_vlcc"] == 0 for r in records)
    summary["covered_calendar_year_count"] = len({r["year"] for r in records})
    summary["calendar_day_count"] = sum(r["turn_days"] for r in records)
    result = {
        "identity": {
            "model_version": MODEL_VERSION,
            "pricing_config_hash": sha256_json(market),
            "demand_input_hash": sha256_json(months),
            "prompt_supply_path_hash": sha256_json(prompt_supply_by_turn),
            "cpi_input_hash": sha256_json(dict(cpi_by_year)),
            "inventory_semantics": "deviation_from_lagged_normal_transport_plan_not_absolute_stocks",
            "unfilled_metric_semantics": "cumulative_turn_observations_not_unique_cargoes_or_terminal_backlog",
            "result_hash": sha256_json({"turns": records, "summary": summary}),''')
replace(p, '''        "summary": summary,
    }


def run_seeded_gulf_east_asia_pricing(''', '''        "summary": summary,
    }
    result["identity"]["identity_hash"] = sha256_json(result["identity"])
    return result


def run_seeded_gulf_east_asia_pricing(''')
config_path = ROOT / 'asset_simulation/config/gulf_east_asia_pricing_v0.2.json'
config = json.loads(config_path.read_text(encoding='utf-8'))
config['model_version'] = config['model_version'].replace('v0.2.0', 'v0.2.1')
config['notes']['validation_supply'] = 'lag daily cargo rate, scale to current turn days; never lag a differently sized window total'
config['notes']['zero_demand'] = 'carry last indicative TCE without a new price observation; no executed fixture price'
config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

# Keep normal IPF arithmetic unchanged; fail on incompatible original margins.
p = 'asset_simulation/model/oil_shipping_routes.py'
replace(p, '''    row_total = sum(row_targets.values())
    column_total = sum(column_targets.values())''', '''    if not row_targets or not column_targets:
        raise ValueError("route margins cannot be empty")
    for name, targets in (("export", row_targets), ("import", column_targets)):
        if any(not math.isfinite(float(v)) or float(v) <= 0 for v in targets.values()):
            raise ValueError(f"{name} margins must be finite and positive")
    row_total = sum(row_targets.values())
    column_total = sum(column_targets.values())
    # Inputs are rounded to eight decimals by the regional owner: only dust
    # may be rescaled, never a genuine missing supply or demand quantity.
    margin_tolerance = 1e-7
    if not math.isclose(row_total, column_total, rel_tol=0.0, abs_tol=margin_tolerance):
        raise ValueError(f"original export/import totals disagree: {row_total} vs {column_total}")
    for origin in row_targets:
        for destination in column_targets:
            weight = float(preferences[_pair_id(origin, destination)])
            if not math.isfinite(weight) or weight <= 0:
                raise ValueError("IPF preferences must be finite and positive")''')
replace(p, '    return flows\n', '''    original_export_error = max(
        abs(sum(flows[_pair_id(o, d)] for d in column_targets) - target)
        for o, target in row_targets.items()
    )
    original_import_error = max(
        abs(sum(flows[_pair_id(o, d)] for o in row_targets) - target)
        for d, target in column_targets.items()
    )
    if max(original_export_error, original_import_error) > margin_tolerance:
        raise ValueError("IPF did not satisfy the ORIGINAL regional margins")
    return flows
''')

# Viewer: distinguish missing/empty parameters from a valid zero seed.
for filename, helper in (('app.js', 'numberParam'), ('overview.js', 'numericParam'), ('physical.js', 'numberParam')):
    p = 'asset_simulation/viewer/js/' + filename
    text = (ROOT / p).read_text(encoding='utf-8')
    pattern = r'function ' + helper + r'\(params, (?:name|key), fallback\) \{.*?\n\}'
    new = '''function finiteNumberOr(raw, fallback) {
  if (raw === null || raw === undefined || String(raw).trim() === "") return fallback;
  const value = Number(raw);
  return Number.isFinite(value) ? value : fallback;
}

function HELPER(params, key, fallback) {
  return finiteNumberOr(params.get(key), fallback);
}'''.replace('HELPER', helper)
    text, n = re.subn(pattern, new, text, flags=re.S)
    if n != 1:
        raise RuntimeError(f'{p}: URL helper not uniquely found')
    text = text.replace('Number($("seedInput").value) || 42', 'finiteNumberOr($("seedInput").value, 42)')
    text = text.replace('Number($("yearsInput").value) || 60', 'finiteNumberOr($("yearsInput").value, 60)')
    write(p, text)

p = 'asset_simulation/server.py'
replace(p, 'from .model.registry import clear_registered_assets_cache', 'from .model.registry import clear_registered_assets_cache, load_registered_assets\nfrom .model.decision_view import build_decision_snapshot')
replace(p, '        "schemaVersion": "asset-simulation-global-run-response-v1",', '        "schemaVersion": "asset-simulation-global-run-response-v1",\n        "scope": "research_full_path_not_for_agent_decisions",')
replace(p, '                        "explicitRouteCount": 9,', '''                        "explicitRouteCount": len(load_registered_assets()["oil_shipping_demand_config"]["route_network"]["explicit_routes"]),''')
replace(p, '            if parsed.path == "/api/global":', '''            if parsed.path == "/api/decision":
                query = parse_qs(parsed.query, keep_blank_values=True)
                seed = int(_single_query(query, "seed", "42"))
                years = int(_single_query(query, "years", "60"))
                year = int(_single_query(query, "year", "2030"))
                month = int(_single_query(query, "month", "1"))
                run = get_cached_run(seed, years)
                self._json(HTTPStatus.OK, build_decision_snapshot(
                    run, run_oil_shipping_world(run), run_oil_price_projection(run),
                    as_of_year=year, as_of_month=month,
                ))
                return
            if parsed.path == "/api/global":''')
replace(p, '                            "/api/health",', '                            "/api/health",\n                            "/api/decision?seed=42&years=60&year=2030&month=1",')
p = 'asset_simulation/model/oil_price_projection.py'
replace(p, '        "schemaVersion": OIL_PRICE_PROJECTION_SCHEMA_VERSION,', '        "schemaVersion": OIL_PRICE_PROJECTION_SCHEMA_VERSION,\n        "scope": "research_full_path_not_for_agent_decisions",')

# Correct stale user-facing counts, keeping historical research untouched.
p = 'README.md'
text = (ROOT / p).read_text(encoding='utf-8').replace('九条', '十四条')
text += '''
## Main 改进候选（2026-09-05）

此候选从 main `75722a1` 创建，新增独立单航线定价库，但没有船队或公司层。
需求参数、区域角色、14条主要航线和参考距离保持不变。

- `/api/global` 与 `/api/oil-price` 是全路径研究接口，不能直接提供给玩家/AI。
- `/api/decision?seed=42&years=60&year=2030&month=1` 是月末已知信息快照：
  只提供允许的已完成宏观、月线和当前运输字段，不带年末油价锚或全路径哈希。
- `years=20` 表示初始年后20次年度转移；月度世界实际覆盖2025–2045年，共21个日历年。
- 定价验证的供给适配器滞后每日货量，并按当前回合日数转换；不代表真实船池。
- 报价、运输匹配、成本结算分离。零供给可以有指示价格，但没有执行价格或收入。

详情见 `asset_simulation/docs/current/MAIN_REVIEW_FIXES.md`。
'''
write(p, text)
for p in ('asset_simulation/CLAUDE.md', 'asset_simulation/docs/MODEL_CONTEXT_GUIDE.md', 'asset_simulation/docs/current/PROJECT_STATUS.md', 'asset_simulation/docs/current/RUNTIME_ARCHITECTURE.md', 'asset_simulation/docs/current/CONTRACTS_AND_UNITS.md', 'asset_simulation/docs/INDEX.md'):
    text = (ROOT / p).read_text(encoding='utf-8')
    text += '''

### 2026-09-05 候选增补（优先于上述旧进度描述）
新增独立 `single_route_pricing` v0.2.1；它只读供需和计划库存偏离并输出TCE，不属于已实现的船队。
定价不接入现有Viewer，也不反写原油需求。研究接口保留；新的 `/api/decision` 只发布月末可见字段。
详细修复范围、兼容性与验证见 `docs/current/MAIN_REVIEW_FIXES.md`。
'''
    write(p, text)
print('Guarded source repairs applied; upstream economic parameters were not changed.')
