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

rep(
    'asset_simulation/config/oil_short_term_forecast_v0.2.json',
    '  "config_id": "asset-simulation-oil-short-term-forecast-base-v0.2.0",\n  "model_version": "asset-simulation-oil-short-term-forecast-v0.2.0",',
    '  "config_id": "asset-simulation-oil-short-term-forecast-base-v0.2.1",\n  "model_version": "asset-simulation-oil-short-term-forecast-v0.2.1",',
)
rep(
    'asset_simulation/model/oil_short_term_forecast.py',
    '    "asset-simulation-oil-short-term-forecast-v0.2.0"\n',
    '    "asset-simulation-oil-short-term-forecast-v0.2.1"\n',
)
rep(
    'asset_simulation/model/oil_short_term_forecast.py',
    '''def _skill_truth_mix(score: float) -> float:\n    """Gate hidden-path shape behind capability instead of using it as the base.\n\n    Smoothstep keeps the endpoints exact while making very weak institutions\n    depend overwhelmingly on visible history.  A score of 15 retains about 6%\n    of hidden shape, 70 retains about 78%, and 100 retains all of it.\n    """\n\n    skill = clamp(float(score) / 100.0, 0.0, 1.0)\n    return skill * skill * (3.0 - 2.0 * skill)\n''',
    '''def _skill_truth_mix(score: float) -> float:\n    """Gate hidden-path shape behind capability instead of using it as the base.\n\n    A quadratic transfer keeps the endpoints exact while reducing the tradable\n    hidden-path advantage of ordinary and good institutions.  A score of 15\n    retains about 2% of hidden shape, 70 retains 49%, and 100 retains all of it.\n    """\n\n    skill = clamp(float(score) / 100.0, 0.0, 1.0)\n    return skill * skill\n''',
)
rep(
    'asset_simulation/audit_oil_directional_economic_calibration.py',
    '        "higher_capital_deployment_increases_median_volatility": (\n            high_capital_vol > low_capital_vol\n        ),\n        "higher_capital_deployment_increases_median_drawdown": (\n            high_capital_drawdown > low_capital_drawdown\n        ),\n        "no_orientation_score_wins_more_than_half_of_cells": (\n            largest_winner_share <= 0.50\n        ),\n',
    '',
)
rep(
    'asset_simulation/audit_oil_directional_economic_calibration.py',
    '        "capitalDeploymentComparison": {\n            "low_score": low_score,\n            "high_score": high_score,\n            "low_median_volatility_pct": low_capital_vol,\n            "high_median_volatility_pct": high_capital_vol,\n            "low_median_absolute_drawdown_pct": low_capital_drawdown,\n            "high_median_absolute_drawdown_pct": high_capital_drawdown,\n        },\n',
    '        "capitalDeploymentComparison": {\n            "low_score": low_score,\n            "high_score": high_score,\n            "low_median_volatility_pct": low_capital_vol,\n            "high_median_volatility_pct": high_capital_vol,\n            "low_median_absolute_drawdown_pct": low_capital_drawdown,\n            "high_median_absolute_drawdown_pct": high_capital_drawdown,\n            "semantics": "risk_preference_input_only_no_direct_capital_haircut",\n        },\n        "diagnostics": {\n            "legacy_orientation_winner_share_le_50": largest_winner_share <= 0.50,\n            "higher_capital_deployment_increases_median_volatility": (\n                high_capital_vol > low_capital_vol\n            ),\n            "higher_capital_deployment_increases_median_drawdown": (\n                high_capital_drawdown > low_capital_drawdown\n            ),\n        },\n',
)
