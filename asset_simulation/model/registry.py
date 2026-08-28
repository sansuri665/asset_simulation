"""Load and hash immutable JSON assets for the compact global model."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PACKAGE_ROOT / "config" / "global_macro_v0.8.json"
FIELD_CONTRACT_PATH = PACKAGE_ROOT / "contracts" / "global_macro_minimum_v3.json"
COMMODITY_OVERLAY_CONFIG_PATH = PACKAGE_ROOT / "config" / "commodity_overlay_v0.1.json"
COMMODITY_OVERLAY_CONTRACT_PATH = PACKAGE_ROOT / "contracts" / "commodity_overlay_v1.json"
OIL_FUTURES_OVERLAY_CONFIG_PATH = PACKAGE_ROOT / "config" / "oil_futures_overlay_v0.8.json"
OIL_FUTURES_OVERLAY_CONTRACT_PATH = PACKAGE_ROOT / "contracts" / "oil_futures_overlay_v8.json"
OIL_SHORT_TERM_FORECAST_CONFIG_PATH = (
    PACKAGE_ROOT / "config" / "oil_short_term_forecast_v0.2.json"
)
OIL_SHORT_TERM_FORECAST_CONTRACT_PATH = (
    PACKAGE_ROOT / "contracts" / "oil_short_term_forecast_v2.json"
)
OIL_STRATEGY_RESEARCH_CONFIG_PATH = (
    PACKAGE_ROOT / "config" / "oil_strategy_research_v0.2.json"
)
OIL_STRATEGY_RESEARCH_CONTRACT_PATH = (
    PACKAGE_ROOT / "contracts" / "oil_strategy_research_v2.json"
)
OIL_EXECUTION_DESK_CONFIG_PATH = (
    PACKAGE_ROOT / "config" / "oil_execution_desk_v0.2.json"
)
OIL_EXECUTION_DESK_CONTRACT_PATH = (
    PACKAGE_ROOT / "contracts" / "oil_execution_desk_v2.json"
)
CORPORATE_RISK_CONTROL_CONFIG_PATH = (
    PACKAGE_ROOT / "config" / "corporate_risk_control_v0.2.json"
)
CORPORATE_RISK_CONTROL_CONTRACT_PATH = (
    PACKAGE_ROOT / "contracts" / "corporate_risk_control_v2.json"
)
OIL_STRATEGY_RISK_CONFIG_PATH = (
    PACKAGE_ROOT / "config" / "oil_strategy_risk_v0.1.json"
)
OIL_STRATEGY_RISK_CONTRACT_PATH = (
    PACKAGE_ROOT / "contracts" / "oil_strategy_risk_v1.json"
)
OIL_TRADING_STRATEGY_CONFIG_PATH = (
    PACKAGE_ROOT / "config" / "oil_trading_strategy_v1.2.json"
)
OIL_TRADING_STRATEGY_CONTRACT_PATH = (
    PACKAGE_ROOT / "contracts" / "oil_trading_strategy_v8.json"
)
OIL_FUTURES_ACCOUNT_CONFIG_PATH = (
    PACKAGE_ROOT / "config" / "oil_futures_account_v0.1.json"
)
OIL_FUTURES_ACCOUNT_CONTRACT_PATH = (
    PACKAGE_ROOT / "contracts" / "oil_futures_account_v1.json"
)
INSTITUTION_ORGANIZATION_CONFIG_PATH = (
    PACKAGE_ROOT / "config" / "institution_organization_v0.1.json"
)
INSTITUTION_ORGANIZATION_CONTRACT_PATH = (
    PACKAGE_ROOT / "contracts" / "institution_organization_v1.json"
)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"registered asset must be an object: {path}")
    return value


@lru_cache(maxsize=1)
def load_registered_assets() -> dict[str, Any]:
    """Load immutable registered JSON assets once per process.

    Callers must treat the returned mapping as read-only. Development
    tools that edit registered JSON in a live process should call
    ``clear_registered_assets_cache`` before rebuilding projections.
    """

    config = load_json(DEFAULT_CONFIG_PATH)
    field_contract = load_json(FIELD_CONTRACT_PATH)
    commodity_overlay_config = load_json(COMMODITY_OVERLAY_CONFIG_PATH)
    commodity_overlay_contract = load_json(COMMODITY_OVERLAY_CONTRACT_PATH)
    oil_futures_overlay_config = load_json(OIL_FUTURES_OVERLAY_CONFIG_PATH)
    oil_futures_overlay_contract = load_json(OIL_FUTURES_OVERLAY_CONTRACT_PATH)
    oil_short_term_forecast_config = load_json(OIL_SHORT_TERM_FORECAST_CONFIG_PATH)
    oil_short_term_forecast_contract = load_json(OIL_SHORT_TERM_FORECAST_CONTRACT_PATH)
    oil_strategy_research_config = load_json(OIL_STRATEGY_RESEARCH_CONFIG_PATH)
    oil_strategy_research_contract = load_json(OIL_STRATEGY_RESEARCH_CONTRACT_PATH)
    oil_execution_desk_config = load_json(OIL_EXECUTION_DESK_CONFIG_PATH)
    oil_execution_desk_contract = load_json(OIL_EXECUTION_DESK_CONTRACT_PATH)
    corporate_risk_control_config = load_json(CORPORATE_RISK_CONTROL_CONFIG_PATH)
    corporate_risk_control_contract = load_json(CORPORATE_RISK_CONTROL_CONTRACT_PATH)
    oil_strategy_risk_config = load_json(OIL_STRATEGY_RISK_CONFIG_PATH)
    oil_strategy_risk_contract = load_json(OIL_STRATEGY_RISK_CONTRACT_PATH)
    oil_trading_strategy_config = load_json(OIL_TRADING_STRATEGY_CONFIG_PATH)
    oil_trading_strategy_contract = load_json(OIL_TRADING_STRATEGY_CONTRACT_PATH)
    oil_futures_account_config = load_json(OIL_FUTURES_ACCOUNT_CONFIG_PATH)
    oil_futures_account_contract = load_json(OIL_FUTURES_ACCOUNT_CONTRACT_PATH)
    institution_organization_config = load_json(
        INSTITUTION_ORGANIZATION_CONFIG_PATH
    )
    institution_organization_contract = load_json(
        INSTITUTION_ORGANIZATION_CONTRACT_PATH
    )
    return {
        "config": config,
        "config_hash": sha256_json(config),
        "field_contract": field_contract,
        "field_contract_hash": sha256_json(field_contract),
        "commodity_overlay_config": commodity_overlay_config,
        "commodity_overlay_config_hash": sha256_json(commodity_overlay_config),
        "commodity_overlay_contract": commodity_overlay_contract,
        "commodity_overlay_contract_hash": sha256_json(commodity_overlay_contract),
        "oil_futures_overlay_config": oil_futures_overlay_config,
        "oil_futures_overlay_config_hash": sha256_json(oil_futures_overlay_config),
        "oil_futures_overlay_contract": oil_futures_overlay_contract,
        "oil_futures_overlay_contract_hash": sha256_json(oil_futures_overlay_contract),
        "oil_short_term_forecast_config": oil_short_term_forecast_config,
        "oil_short_term_forecast_config_hash": sha256_json(
            oil_short_term_forecast_config
        ),
        "oil_short_term_forecast_contract": oil_short_term_forecast_contract,
        "oil_short_term_forecast_contract_hash": sha256_json(
            oil_short_term_forecast_contract
        ),
        "oil_strategy_research_config": oil_strategy_research_config,
        "oil_strategy_research_config_hash": sha256_json(
            oil_strategy_research_config
        ),
        "oil_strategy_research_contract": oil_strategy_research_contract,
        "oil_strategy_research_contract_hash": sha256_json(
            oil_strategy_research_contract
        ),
        "oil_execution_desk_config": oil_execution_desk_config,
        "oil_execution_desk_config_hash": sha256_json(oil_execution_desk_config),
        "oil_execution_desk_contract": oil_execution_desk_contract,
        "oil_execution_desk_contract_hash": sha256_json(oil_execution_desk_contract),
        "corporate_risk_control_config": corporate_risk_control_config,
        "corporate_risk_control_config_hash": sha256_json(corporate_risk_control_config),
        "corporate_risk_control_contract": corporate_risk_control_contract,
        "corporate_risk_control_contract_hash": sha256_json(corporate_risk_control_contract),
        "oil_strategy_risk_config": oil_strategy_risk_config,
        "oil_strategy_risk_config_hash": sha256_json(oil_strategy_risk_config),
        "oil_strategy_risk_contract": oil_strategy_risk_contract,
        "oil_strategy_risk_contract_hash": sha256_json(oil_strategy_risk_contract),
        "oil_trading_strategy_config": oil_trading_strategy_config,
        "oil_trading_strategy_config_hash": sha256_json(
            oil_trading_strategy_config
        ),
        "oil_trading_strategy_contract": oil_trading_strategy_contract,
        "oil_trading_strategy_contract_hash": sha256_json(
            oil_trading_strategy_contract
        ),
        "oil_futures_account_config": oil_futures_account_config,
        "oil_futures_account_config_hash": sha256_json(
            oil_futures_account_config
        ),
        "oil_futures_account_contract": oil_futures_account_contract,
        "oil_futures_account_contract_hash": sha256_json(
            oil_futures_account_contract
        ),
        "institution_organization_config": institution_organization_config,
        "institution_organization_config_hash": sha256_json(
            institution_organization_config
        ),
        "institution_organization_contract": (
            institution_organization_contract
        ),
        "institution_organization_contract_hash": sha256_json(
            institution_organization_contract
        ),
    }


def clear_registered_assets_cache() -> None:
    """Forget cached registered assets after a development-time config edit."""

    load_registered_assets.cache_clear()
