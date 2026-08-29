from __future__ import annotations

from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1]

def path(rel: str) -> Path:
    return ROOT / rel

def rep(rel: str, old: str, new: str) -> None:
    p = path(rel)
    text = p.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{rel}: expected exactly one replacement, found {count}: {old[:120]!r}')
    p.write_text(text.replace(old, new), encoding='utf-8')

def write(rel: str, content: str) -> None:
    p = path(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).lstrip('\n'), encoding='utf-8')

rep(
    'asset_simulation/config/oil_strategy_research_v0.2.json',
    '  "config_id": "asset-simulation-oil-strategy-research-base-v0.2.1",\n  "model_version": "asset-simulation-oil-strategy-research-v0.2.1",',
    '  "config_id": "asset-simulation-oil-strategy-research-base-v0.2.2",\n  "model_version": "asset-simulation-oil-strategy-research-v0.2.2",',
)
rep(
    'asset_simulation/config/oil_strategy_research_v0.2.json',
    '    "capital_deployment_pct_of_allocated_equity": {"minimum": 25, "neutral": 50, "maximum": 75, "curve": "centered_linear"},\n',
    '',
)
rep(
    'asset_simulation/model/oil_strategy_research.py',
    '    "asset-simulation-oil-strategy-research-v0.2.1"\n',
    '    "asset-simulation-oil-strategy-research-v0.2.2"\n',
)
rep(
    'asset_simulation/model/oil_strategy_research.py',
    '''    for key, lower_bound, upper_bound in (\n        ("continuation_weight", 0.0, 1.0),\n        ("capital_deployment_pct_of_allocated_equity", 0.0, 100.0),\n    ):\n        values = mapping[key]\n        low = float(values["minimum"])\n        neutral = float(values["neutral"])\n        high = float(values["maximum"])\n        if not lower_bound <= low <= neutral <= high <= upper_bound:\n            raise ValueError(f"oil strategy research centered bounds are invalid: {key}")\n''',
    '''    values = mapping["continuation_weight"]\n    low = float(values["minimum"])\n    neutral = float(values["neutral"])\n    high = float(values["maximum"])\n    if not 0.0 <= low <= neutral <= high <= 1.0:\n        raise ValueError(\n            "oil strategy research centered bounds are invalid: continuation_weight"\n        )\n''',
)
rep(
    'asset_simulation/model/oil_strategy_research.py',
    '''        "risk": {\n            "role_weights": {\n                "main": main_weight,\n                "next_main": 1.0 - main_weight,\n            },\n            "capital_deployment_score": deployment_score,\n            "capital_deployment_pct_of_allocated_equity": _centered_linear(\n                mapping["capital_deployment_pct_of_allocated_equity"],\n                deployment_score,\n            ),\n        },\n''',
    '''        "risk": {\n            "role_weights": {\n                "main": main_weight,\n                "next_main": 1.0 - main_weight,\n            },\n            "capital_deployment_score": deployment_score,\n            "capital_deployment_semantics": "risk_preference_input_only",\n        },\n''',
)
rep(
    'asset_simulation/model/oil_strategy_research.py',
    '            "hard_risk_owner": "market_and_account_rules",\n',
    '            "hard_risk_owner": "market_and_account_rules",\n            "capital_deployment_direct_capital_haircut": False,\n',
)
rep(
    'asset_simulation/contracts/oil_strategy_research_v2.json',
    '"role": "Deterministic appointed-personnel profiles for the oil strategy research department. The eight-axis radar controls continuation-versus-reversion philosophy, deployment of allocated capital, timing, turnover and contract preferences without granting capital-allocation or market-limit authority.",',
    '"role": "Deterministic appointed-personnel profiles for the oil strategy research department. The eight-axis radar controls continuation-versus-reversion philosophy, capital-expression appetite used by risk review, timing, turnover and contract preferences without granting capital-allocation or market-limit authority.",',
)
rep(
    'asset_simulation/contracts/oil_strategy_research_v2.json',
    '"strategy_personnel_capital_scope": "deployment_within_authorized_capital"',
    '"strategy_personnel_capital_scope": "risk_preference_input_only_no_direct_capital_haircut"',
)
rep(
    'asset_simulation/contracts/oil_strategy_research_v2.json',
    '    "capital_deployment_pct_of_allocated_equity": {"unit": "percent"},\n',
    '',
)
rep(
    'asset_simulation/contracts/oil_strategy_research_v2.json',
    '"capital deployment describes the PM tendency inside capital allocated by the investment-decision owner and never grants or reallocates company capital",',
    '"capital deployment is a PM risk-expression preference consumed by strategy-risk and investment-decision review; it never directly multiplies committee-authorized capital",',
)

rep(
    'asset_simulation/config/oil_trading_strategy_v1.2.json',
    '  "config_id": "asset-simulation-oil-continuation-reversion-v1.3.0",\n  "model_version": "asset-simulation-oil-trading-strategy-v1.3.0",',
    '  "config_id": "asset-simulation-oil-continuation-reversion-v1.3.1",\n  "model_version": "asset-simulation-oil-trading-strategy-v1.3.1",',
)
rep(
    'asset_simulation/config/oil_trading_strategy_v1.2.json',
    '''    "consecutive_failure_turns_to_invalidate": 2,\n    "material_band_breach_z": 1.00,\n    "severe_band_breach_z": 2.00,\n    "minimum_direction_move_log": 0.004,\n''',
    '''    "consecutive_failure_turns_to_invalidate": 2,\n    "material_band_breach_z": 1.25,\n    "severe_band_breach_z": 2.00,\n    "minimum_direction_move_log": 0.004,\n    "minimum_direction_forecast_z": 0.35,\n''',
)
rep(
    'asset_simulation/model/oil_trading_strategy.py',
    '    "asset-simulation-oil-trading-strategy-v1.3.0"\n',
    '    "asset-simulation-oil-trading-strategy-v1.3.1"\n',
)
rep(
    'asset_simulation/model/oil_trading_strategy.py',
    '    thesis = config["thesis_invalidation"]\n    scales = {\n',
    '    thesis = config["thesis_invalidation"]\n    if "minimum_direction_forecast_z" not in thesis:\n        raise ValueError(\n            "oil trading strategy minimum_direction_forecast_z is required"\n        )\n    scales = {\n',
)
rep(
    'asset_simulation/model/oil_trading_strategy.py',
    '        or float(thesis["minimum_direction_move_log"]) <= 0.0\n        or not 0.0 <= float(thesis["direction_reversal_signal_threshold"]) <= 1.0\n',
    '        or float(thesis["minimum_direction_move_log"]) <= 0.0\n        or not 0.0 < float(thesis["minimum_direction_forecast_z"]) <= 5.0\n        or not 0.0 <= float(thesis["direction_reversal_signal_threshold"]) <= 1.0\n',
)
rep(
    'asset_simulation/model/oil_trading_strategy.py',
    '''    capital_deployment_pct = float(\n        risk["capital_deployment_pct_of_allocated_equity"]\n    )\n    capital_deployment_budget = (\n        allocated_strategy_capital\n        * capital_deployment_pct\n        / 100.0\n    )\n''',
    '''    capital_deployment_score = float(risk["capital_deployment_score"])\n    capital_capacity_budget = allocated_strategy_capital\n''',
)
rep(
    'asset_simulation/model/oil_trading_strategy.py',
    '''        capital_deployment_capacity = math.floor(\n            capital_deployment_budget * role_weight / max(1e-9, margin_per_lot)\n        )\n        risk_capacity = max(\n            0, min(market_role_capacity, capital_deployment_capacity)\n        )\n        binding_capacity = (\n            "market_position_limit"\n            if market_role_capacity <= capital_deployment_capacity\n            else "capital_deployment_budget"\n        )\n''',
    '''        authorized_capital_capacity = math.floor(\n            capital_capacity_budget * role_weight / max(1e-9, margin_per_lot)\n        )\n        risk_capacity = max(\n            0, min(market_role_capacity, authorized_capital_capacity)\n        )\n        binding_capacity = (\n            "market_position_limit"\n            if market_role_capacity <= authorized_capital_capacity\n            else "committee_authorized_capital"\n        )\n''',
)
rep(
    'asset_simulation/model/oil_trading_strategy.py',
    '            "capital_deployment_capacity_lots": capital_deployment_capacity,\n',
    '            "authorized_capital_capacity_lots": authorized_capital_capacity,\n',
)
rep(
    'asset_simulation/model/oil_trading_strategy.py',
    '                "capital_deployment_capacity_lots": 0,\n',
    '                "authorized_capital_capacity_lots": 0,\n',
)
rep(
    'asset_simulation/model/oil_trading_strategy.py',
    '''            "allocated_strategy_capital_usd": allocated_strategy_capital,\n            "capital_deployment_budget_usd": capital_deployment_budget,\n            "capital_deployment_pct_of_allocated_equity": capital_deployment_pct,\n''',
    '''            "allocated_strategy_capital_usd": allocated_strategy_capital,\n            "authorized_strategy_capital_usd": allocated_strategy_capital,\n            "capital_capacity_budget_usd": capital_capacity_budget,\n            "pm_capital_deployment_score": capital_deployment_score,\n            "capital_capacity_owner": "investment_decision_committee",\n''',
)
rep(
    'asset_simulation/model/oil_trading_strategy.py',
    '            "investment_committee_owns_capital_authorization": True,\n',
    '            "investment_committee_owns_capital_authorization": True,\n            "pm_capital_deployment_directly_scales_authorized_capital": False,\n',
)

rep(
    'asset_simulation/model/oil_strategy_thesis.py',
    '    direction_threshold = float(policy["minimum_direction_move_log"])\n    contracts: dict[str, dict[str, Any]] = {}\n',
    '    direction_threshold = float(policy["minimum_direction_move_log"])\n    direction_forecast_threshold = float(policy["minimum_direction_forecast_z"])\n    contracts: dict[str, dict[str, Any]] = {}\n',
)
rep(
    'asset_simulation/model/oil_strategy_thesis.py',
    '''        predicted_direction = _sign(math.log(center / anchor), direction_threshold)\n        actual_direction = _sign(math.log(actual / anchor), direction_threshold)\n        direction_miss = (\n            predicted_direction != 0\n            and actual_direction != 0\n            and predicted_direction != actual_direction\n        )\n''',
    '''        forecast_direction_log = math.log(center / anchor)\n        forecast_direction_z = abs(forecast_direction_log) / uncertainty\n        direction_miss_eligible = (\n            forecast_direction_z >= direction_forecast_threshold\n        )\n        predicted_direction = _sign(forecast_direction_log, direction_threshold)\n        actual_direction = _sign(math.log(actual / anchor), direction_threshold)\n        direction_miss = (\n            direction_miss_eligible\n            and predicted_direction != 0\n            and actual_direction != 0\n            and predicted_direction != actual_direction\n        )\n''',
)
rep(
    'asset_simulation/model/oil_strategy_thesis.py',
    '            "predicted_direction": predicted_direction,\n            "realized_direction": actual_direction,\n            "direction_miss": direction_miss,\n',
    '            "forecast_direction_z": forecast_direction_z,\n            "minimum_direction_forecast_z": direction_forecast_threshold,\n            "direction_miss_eligible": direction_miss_eligible,\n            "predicted_direction": predicted_direction,\n            "realized_direction": actual_direction,\n            "direction_miss": direction_miss,\n',
)

rep(
    'asset_simulation/model/oil_calendar_spread_strategy.py',
    '    authorized_strategy_capital_usd: float,\n    capital_deployment_pct: float,\n    config: Mapping[str, Any],\n',
    '    authorized_strategy_capital_usd: float,\n    config: Mapping[str, Any],\n',
)
rep(
    'asset_simulation/model/oil_calendar_spread_strategy.py',
    '    deployment_budget = (\n        float(authorized_strategy_capital_usd)\n        * float(capital_deployment_pct)\n        / 100.0\n    )\n',
    '    capital_capacity_budget = float(authorized_strategy_capital_usd)\n',
)
rep(
    'asset_simulation/model/oil_calendar_spread_strategy.py',
    '    margin_budget = deployment_budget * float(\n',
    '    margin_budget = capital_capacity_budget * float(\n',
)
rep(
    'asset_simulation/model/oil_calendar_spread_strategy.py',
    '    stressed_loss_budget = deployment_budget * float(\n',
    '    stressed_loss_budget = capital_capacity_budget * float(\n',
)
rep(
    'asset_simulation/model/oil_calendar_spread_strategy.py',
    '        "authorized_strategy_capital_usd": float(authorized_strategy_capital_usd),\n        "capital_deployment_pct_of_authorized_capital": float(\n            capital_deployment_pct\n        ),\n        "capital_deployment_budget_usd": deployment_budget,\n',
    '        "authorized_strategy_capital_usd": float(authorized_strategy_capital_usd),\n        "capital_capacity_budget_usd": capital_capacity_budget,\n        "capital_capacity_owner": "investment_decision_committee",\n',
)
rep(
    'asset_simulation/model/oil_calendar_spread_strategy.py',
    '    ``authorized_strategy_capital_usd`` is intentionally explicit.  The PM may\n    deploy only a style-dependent share of this committee-owned allocation.\n',
    '    ``authorized_strategy_capital_usd`` is intentionally explicit.  The PM\n    capital-deployment style is a risk-review input and never haircuts this\n    committee-owned allocation inside the strategy.\n',
)
rep(
    'asset_simulation/model/oil_calendar_spread_strategy.py',
    '    capital_deployment_pct = float(\n        strategy_policy["risk"]["capital_deployment_pct_of_allocated_equity"]\n    )\n    capacity = _risk_capacity(\n',
    '    capacity = _risk_capacity(\n',
)
rep(
    'asset_simulation/model/oil_calendar_spread_strategy.py',
    '        authorized_strategy_capital_usd=authorized_capital,\n        capital_deployment_pct=capital_deployment_pct,\n        config=config,\n',
    '        authorized_strategy_capital_usd=authorized_capital,\n        config=config,\n',
)
rep(
    'asset_simulation/contracts/oil_calendar_spread_strategy_v1.json',
    '"committee-authorized capital remains an explicit input; PM capital_deployment only controls use of that authorized capital",',
    '"committee-authorized capital remains an explicit input and is never multiplied by a shared PM deployment percentage; PM capital_deployment is reserved for risk-review semantics",',
)
