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
OIL_SHIPPING_DEMAND_CONFIG_PATH = (
    PACKAGE_ROOT / "config" / "oil_shipping_demand_v0.5.json"
)
OIL_SHIPPING_DEMAND_CONTRACT_PATH = (
    PACKAGE_ROOT / "contracts" / "oil_shipping_demand_v5.json"
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
    oil_shipping_demand_config = load_json(OIL_SHIPPING_DEMAND_CONFIG_PATH)
    oil_shipping_demand_contract = load_json(OIL_SHIPPING_DEMAND_CONTRACT_PATH)
    return {
        "config": config,
        "config_hash": sha256_json(config),
        "field_contract": field_contract,
        "field_contract_hash": sha256_json(field_contract),
        "oil_shipping_demand_config": oil_shipping_demand_config,
        "oil_shipping_demand_config_hash": sha256_json(
            oil_shipping_demand_config
        ),
        "oil_shipping_demand_contract": oil_shipping_demand_contract,
        "oil_shipping_demand_contract_hash": sha256_json(
            oil_shipping_demand_contract
        ),
    }


def clear_registered_assets_cache() -> None:
    """Forget cached registered assets after a development-time config edit."""

    load_registered_assets.cache_clear()
