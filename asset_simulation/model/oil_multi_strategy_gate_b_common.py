"""Shared assets and validation helpers for the Gate B multi-strategy kernel."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

from .registry import load_json, sha256_json


OIL_MULTI_STRATEGY_GATE_B_MODEL_VERSION = (
    "asset-simulation-oil-multi-strategy-gate-b-v0.1.0"
)
OIL_MULTI_STRATEGY_GATE_B_CONTRACT_ID = "oil_multi_strategy_gate_b_v1"
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH = _PACKAGE_ROOT / "config" / "oil_multi_strategy_gate_b_v0.1.json"
_CONTRACT_PATH = _PACKAGE_ROOT / "contracts" / "oil_multi_strategy_gate_b_v1.json"


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Gate B payload contains a non-finite value")
        return round(value, 8)
    return value


def _finite_nonnegative(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return result


def _integer(value: Any, name: str, *, nonnegative: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    result = int(value)
    if nonnegative and result < 0:
        raise ValueError(f"{name} must be nonnegative")
    return result


def load_oil_multi_strategy_gate_b_assets() -> dict[str, Any]:
    """Load and hash the versioned Gate B candidate assets."""

    config = load_json(_CONFIG_PATH)
    contract = load_json(_CONTRACT_PATH)
    return {
        "oil_multi_strategy_gate_b_config": config,
        "oil_multi_strategy_gate_b_config_hash": sha256_json(config),
        "oil_multi_strategy_gate_b_contract": contract,
        "oil_multi_strategy_gate_b_contract_hash": sha256_json(contract),
    }


def _assets() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assets = load_oil_multi_strategy_gate_b_assets()
    config = assets["oil_multi_strategy_gate_b_config"]
    contract = assets["oil_multi_strategy_gate_b_contract"]
    if config["model_version"] != OIL_MULTI_STRATEGY_GATE_B_MODEL_VERSION:
        raise ValueError("registered Gate B config/model version mismatch")
    if contract["contract_id"] != OIL_MULTI_STRATEGY_GATE_B_CONTRACT_ID:
        raise ValueError("registered Gate B contract id mismatch")
    priorities = tuple(config["allocation"]["priority_order"])
    if priorities != (
        "mandatory_liquidation",
        "residual_remediation",
        "risk_reduction",
        "risk_increase",
    ):
        raise ValueError("Gate B priority order is unsupported")
    if not bool(config["authorization"]["percentages_are_diagnostic_only"]):
        raise ValueError("Gate B authorization percentages must remain diagnostic")
    if bool(config["authorization"]["automatic_percentage_rebalancing_enabled"]):
        raise ValueError("Gate B cannot enable automatic percentage rebalancing")
    return assets, config, contract


def build_gate_b_market_limits_from_oil_futures_payload(
    market: Mapping[str, Any], *, maximum_initial_margin_usd: float | None = None
) -> dict[str, Any]:
    """Adapt the current public oil-futures payload to the Gate B allocator."""

    if not bool(market.get("ok")):
        raise ValueError("Gate B market adapter requires a successful market payload")
    specification = dict(market["contractSpecification"])
    contract_size = _finite_nonnegative(
        specification["contract_size_bbl"], "oil contract size"
    )
    initial_margin_rate = _finite_nonnegative(
        specification["initial_margin_rate_pct"], "initial margin rate"
    ) / 100.0
    contracts: dict[str, dict[str, Any]] = {}
    for raw in market.get("curve", {}).get("contracts", ()):
        item = dict(raw)
        contract_id = str(item["contract_id"])
        limits = dict(item["participantLimits"])
        price = _finite_nonnegative(item["price_usd"], f"{contract_id} price")
        contracts[contract_id] = {
            "turn_trade_limit_lots": _integer(
                limits["turn_trade_limit_lots"],
                f"{contract_id} turn trade limit",
                nonnegative=True,
            ),
            "single_contract_position_limit_lots": _integer(
                limits["single_contract_position_limit_lots"],
                f"{contract_id} position limit",
                nonnegative=True,
            ),
            "new_trades_allowed": bool(limits["new_trades_allowed"]),
            "initial_margin_usd_per_lot": price * contract_size * initial_margin_rate,
        }
    result: dict[str, Any] = {
        "contracts": contracts,
        "all_contract_gross_position_cap_lots": _integer(
            market["participantLimitsPolicy"][
                "all_contract_gross_position_cap_lots"
            ],
            "all-contract gross position cap",
            nonnegative=True,
        ),
    }
    if maximum_initial_margin_usd is not None:
        result["maximum_initial_margin_usd"] = _finite_nonnegative(
            maximum_initial_margin_usd, "maximum initial margin"
        )
    return _round_nested(result)
