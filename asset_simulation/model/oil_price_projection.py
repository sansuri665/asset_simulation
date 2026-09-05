"""Read-only monthly crude-price path inside annual macro settlements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .engine import GlobalMacroRun
from .math_utils import round_record
from .oil_commodity import annual_price_envelope, expand_annual_to_months
from .performance_cache import deterministic_projection_cache
from .registry import load_registered_assets, sha256_json


OIL_PRICE_PROJECTION_MODEL_VERSION = "asset-simulation-monthly-oil-price-v0.1.0"
OIL_PRICE_PROJECTION_SCHEMA_VERSION = "asset-simulation-oil-price-response-v1"


@dataclass(frozen=True)
class OilPriceProjection:
    annual: tuple[dict[str, Any], ...]
    monthly: tuple[dict[str, Any], ...]
    identity: dict[str, Any]


@deterministic_projection_cache(max_entries=8)
def run_oil_price_projection(global_run: GlobalMacroRun) -> OilPriceProjection:
    """Expand annual oil-price anchors without writing back to macro."""

    config = load_registered_assets()["config"]
    real_bounds = config["bounds"]["real_oil_price_index"]
    annual: list[dict[str, Any]] = []
    monthly: list[dict[str, Any]] = []

    for index, row in enumerate(global_run.rows):
        previous = row if index == 0 else global_run.rows[index - 1]
        year = int(row["year"])
        year_index = int(row["year_index"])
        nominal_open = float(previous["brent_oil_price_usd"])
        nominal_close = float(row["brent_oil_price_usd"])
        real_open = float(previous["global_real_oil_price_index"])
        real_close = float(row["global_real_oil_price_index"])
        envelope = annual_price_envelope(
            seed=global_run.seed,
            year_index=year_index,
            open_real=real_open,
            close_real=real_close,
            inventory_tightness_index=float(row["global_oil_inventory_tightness_index"]),
            oil_demand_index=float(row["global_oil_demand_index"]),
            oil_supply_index=float(row["global_oil_supply_index"]),
            dollar_yoy_change_pct=float(row["dollar_yoy_change_pct"]),
            real_bounds=real_bounds,
            volatility_regime_index=float(row["global_oil_volatility_regime_index"]),
        )
        nominal_body_high = max(nominal_open, nominal_close)
        nominal_body_low = min(nominal_open, nominal_close)
        real_body_high = max(real_open, real_close)
        real_body_low = min(real_open, real_close)
        nominal_high = nominal_body_high * envelope["real_high_index"] / real_body_high
        nominal_low = nominal_body_low * envelope["real_low_index"] / real_body_low
        bars = expand_annual_to_months(
            seed=global_run.seed,
            year_index=year_index,
            open_px=nominal_open,
            close_px=nominal_close,
            high_px=nominal_high,
            low_px=nominal_low,
            volatility_regime_index=float(row["global_oil_volatility_regime_index"]),
        )
        annual.append(
            round_record(
                {
                    "seed": global_run.seed,
                    "year": year,
                    "year_index": year_index,
                    "open_usd_per_bbl": nominal_open,
                    "high_usd_per_bbl": nominal_high,
                    "low_usd_per_bbl": nominal_low,
                    "close_usd_per_bbl": nominal_close,
                    "real_close_index": real_close,
                    "volatility_regime_index": float(
                        row["global_oil_volatility_regime_index"]
                    ),
                }
            )
        )
        monthly.extend(
            round_record(
                {
                    "seed": global_run.seed,
                    "year": year,
                    "month": int(bar["month"]),
                    "label": f"{year}-{int(bar['month']):02d}",
                    "open_usd_per_bbl": float(bar["open"]),
                    "high_usd_per_bbl": float(bar["high"]),
                    "low_usd_per_bbl": float(bar["low"]),
                    "close_usd_per_bbl": float(bar["close"]),
                    "annual_close_anchor_usd_per_bbl": nominal_close,
                }
            )
            for bar in bars
        )

    result = {"annual": annual, "monthly": monthly}
    identity = {
        "schema_version": "asset-simulation-oil-price-projection-identity-v1",
        "model_version": OIL_PRICE_PROJECTION_MODEL_VERSION,
        "seed": global_run.seed,
        "start_year": int(global_run.rows[0]["year"]),
        "end_year": int(global_run.rows[-1]["year"]),
        "annual_price_owner": "global_macro_oil_commodity",
        "monthly_path_owner": "oil_price_projection",
        "write_back": False,
        "upstream_global_identity_hash": global_run.identity["identity_hash"],
        "result_hash": sha256_json(result),
    }
    identity["identity_hash"] = sha256_json(identity)
    return OilPriceProjection(
        annual=tuple(annual),
        monthly=tuple(monthly),
        identity=identity,
    )


def build_oil_price_payload(projection: OilPriceProjection) -> dict[str, Any]:
    return {
        "ok": True,
        "schemaVersion": OIL_PRICE_PROJECTION_SCHEMA_VERSION,
        "scope": "research_full_path_not_for_agent_decisions",
        "identity": projection.identity,
        "unit": "usd_per_barrel",
        "annual": projection.annual,
        "monthly": projection.monthly,
    }
