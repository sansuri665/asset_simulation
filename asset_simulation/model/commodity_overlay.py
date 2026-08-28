"""Read-only named commodity prices on a cleared global macro year.

Brent is a pass-through of the global oil close. The Brent year high/low
envelope is derived from the same cleared oil state and does not write back.
Monthly bars expand that envelope inside the year; weekly bars expand each
month. Other contracts are light satellites of actual oil, the broad
commodity index, and the dollar proxy. This overlay does not recompute
inflation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from . import oil_commodity
from .engine import GlobalMacroRun
from .math_utils import clamp, round_record
from .performance_cache import deterministic_projection_cache
from .random_stream import normal
from .registry import load_registered_assets, sha256_json


COMMODITY_MODEL_VERSION = "asset-simulation-commodity-overlay-v0.1.2"
KIND_ORDER = ("energy", "industrial_metals", "precious_metals", "agriculture")
CONTRACT_ORDER = (
    "brent",
    "wti",
    "henry_hub",
    "ttf",
    "copper",
    "aluminum",
    "iron_ore",
    "gold",
    "silver",
    "wheat",
    "corn",
    "soybean",
)


@dataclass(frozen=True)
class CommodityOverlayRun:
    identity: dict[str, Any]
    kinds: dict[str, tuple[dict[str, Any], ...]]
    contracts: dict[str, tuple[dict[str, Any], ...]]


def _growth(current: float, previous: float | None) -> float | None:
    if previous in (None, 0.0):
        return None
    return 100.0 * (current / float(previous) - 1.0)


def _paired_weeks(
    *,
    seed: int,
    year_index: int,
    month: int,
    nominal_bar: Mapping[str, Any],
    real_bar: Mapping[str, Any],
    volatility_regime_index: float,
) -> list[dict[str, Any]]:
    nominal_weeks = oil_commodity.expand_month_to_weeks(
        seed=seed,
        year_index=year_index,
        month=month,
        open_px=nominal_bar["open"],
        close_px=nominal_bar["close"],
        high_px=nominal_bar["high"],
        low_px=nominal_bar["low"],
        volatility_regime_index=volatility_regime_index,
    )
    real_weeks = oil_commodity.expand_month_to_weeks(
        seed=seed,
        year_index=year_index,
        month=month,
        open_px=real_bar["open"],
        close_px=real_bar["close"],
        high_px=real_bar["high"],
        low_px=real_bar["low"],
        volatility_regime_index=volatility_regime_index,
    )
    return [
        {
            "week": int(nominal_week["week"]),
            "open": nominal_week["open"],
            "high": nominal_week["high"],
            "low": nominal_week["low"],
            "close": nominal_week["close"],
            "real_open": real_week["open"],
            "real_high": real_week["high"],
            "real_low": real_week["low"],
            "real_close": real_week["close"],
        }
        for nominal_week, real_week in zip(nominal_weeks, real_weeks, strict=True)
    ]


def _brent_row(
    global_row: Mapping[str, Any],
    previous_global: Mapping[str, Any] | None,
    spec: Mapping[str, Any],
    *,
    seed: int,
    real_bounds: tuple[float, float] | list[float],
) -> dict[str, Any]:
    nominal = float(global_row["brent_oil_price_usd"])
    real_index = float(global_row["global_real_oil_price_index"])
    open_real = (
        real_index
        if previous_global is None
        else float(previous_global["global_real_oil_price_index"])
    )
    open_nominal = (
        nominal if previous_global is None else float(previous_global["brent_oil_price_usd"])
    )
    volatility_regime = float(global_row["global_oil_volatility_regime_index"])
    envelope = oil_commodity.annual_price_envelope(
        seed=seed,
        year_index=int(global_row["year_index"]),
        open_real=open_real,
        close_real=real_index,
        inventory_tightness_index=float(global_row["global_oil_inventory_tightness_index"]),
        oil_demand_index=float(global_row["global_oil_demand_index"]),
        oil_supply_index=float(global_row["global_oil_supply_index"]),
        dollar_yoy_change_pct=float(global_row["dollar_yoy_change_pct"]),
        real_bounds=real_bounds,
        volatility_regime_index=volatility_regime,
    )
    body_high_real = max(open_real, real_index)
    body_low_real = min(open_real, real_index)
    high_ratio = envelope["real_high_index"] / body_high_real if body_high_real else 1.0
    low_ratio = envelope["real_low_index"] / body_low_real if body_low_real else 1.0
    nominal_high = max(open_nominal, nominal) * high_ratio
    nominal_low = min(open_nominal, nominal) * low_ratio
    real_months = oil_commodity.expand_annual_to_months(
        seed=seed,
        year_index=int(global_row["year_index"]),
        open_px=open_real,
        close_px=real_index,
        high_px=envelope["real_high_index"],
        low_px=envelope["real_low_index"],
        volatility_regime_index=volatility_regime,
    )
    nominal_months = oil_commodity.expand_annual_to_months(
        seed=seed,
        year_index=int(global_row["year_index"]),
        open_px=open_nominal,
        close_px=nominal,
        high_px=nominal_high,
        low_px=nominal_low,
        volatility_regime_index=volatility_regime,
    )
    monthly = tuple(
        {
            "month": int(nominal_bar["month"]),
            "open": nominal_bar["open"],
            "high": nominal_bar["high"],
            "low": nominal_bar["low"],
            "close": nominal_bar["close"],
            "real_open": real_bar["open"],
            "real_high": real_bar["high"],
            "real_low": real_bar["low"],
            "real_close": real_bar["close"],
            "weekly": _paired_weeks(
                seed=seed,
                year_index=int(global_row["year_index"]),
                month=int(nominal_bar["month"]),
                nominal_bar=nominal_bar,
                real_bar=real_bar,
                volatility_regime_index=volatility_regime,
            ),
        }
        for nominal_bar, real_bar in zip(nominal_months, real_months, strict=True)
    )
    return {
        "kind_id": spec["kind"],
        "contract_id": "brent",
        "unit": spec["unit"],
        "unit_label": spec["unit_label"],
        "nominal_price_usd": nominal,
        "real_price_index": real_index,
        "nominal_high_usd": nominal_high,
        "nominal_low_usd": nominal_low,
        "real_high_index": envelope["real_high_index"],
        "real_low_index": envelope["real_low_index"],
        "monthly": list(monthly),
        "source": "global_brent",
    }


def _wti_spread(seed: int, year_index: int, previous: float, spec: Mapping[str, Any]) -> float:
    target = float(spec["spread_center"])
    persist = float(spec["spread_persist"])
    news = float(spec["spread_news_scale"]) * normal(seed, "commodity.wti.spread", year_index)
    raw = persist * previous + (1.0 - persist) * target + news
    low, high = map(float, spec["spread_bounds"])
    return clamp(raw, low, high)


def _satellite_real(
    *,
    seed: int,
    year_index: int,
    contract_id: str,
    previous_real: float,
    global_row: Mapping[str, Any],
    previous_global: Mapping[str, Any] | None,
    spec: Mapping[str, Any],
) -> float:
    if previous_global is None:
        return 100.0
    oil_yoy = _growth(
        float(global_row["global_real_oil_price_index"]),
        float(previous_global["global_real_oil_price_index"]),
    ) or 0.0
    broad_yoy = _growth(
        float(global_row["global_real_broad_commodity_index"]),
        float(previous_global["global_real_broad_commodity_index"]),
    ) or 0.0
    dollar_yoy = float(global_row["dollar_yoy_change_pct"])
    news = float(spec["news_scale"]) * normal(seed, f"commodity.{contract_id}", year_index)
    real_return = (
        float(spec["oil_beta"]) * oil_yoy
        + float(spec["broad_beta"]) * broad_yoy
        + float(spec["dollar_beta"]) * dollar_yoy
        - float(spec["mean_reversion"]) * (previous_real - 100.0)
        + news
    )
    low, high = map(float, spec["real_bounds"])
    return clamp(previous_real * (1.0 + clamp(real_return, -28.0, 36.0) / 100.0), low, high)


def _satellite_row(
    *,
    contract_id: str,
    spec: Mapping[str, Any],
    real_index: float,
    cpi: float,
) -> dict[str, Any]:
    nominal = float(spec["anchor_usd"]) * real_index / 100.0 * cpi / 100.0
    return {
        "kind_id": spec["kind"],
        "contract_id": contract_id,
        "unit": spec["unit"],
        "unit_label": spec["unit_label"],
        "nominal_price_usd": nominal,
        "real_price_index": real_index,
        "source": spec["source"],
    }


def _finish_row(
    *,
    seed: int,
    year_index: int,
    year: int,
    row: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
) -> dict[str, Any]:
    payload = {
        "seed": seed,
        "year_index": year_index,
        "year": year,
        **row,
        "yoy_change_pct": None
        if previous is None
        else _growth(float(row["nominal_price_usd"]), float(previous["nominal_price_usd"])),
        "real_yoy_change_pct": None
        if previous is None
        else _growth(float(row["real_price_index"]), float(previous["real_price_index"])),
    }
    return round_record(payload)


def _kind_row(
    *,
    seed: int,
    year_index: int,
    year: int,
    kind_id: str,
    name: str,
    members: Mapping[str, Mapping[str, Any]],
    weights: Mapping[str, float],
    cpi: float,
    previous: Mapping[str, Any] | None,
) -> dict[str, Any]:
    real_index = sum(float(weights[contract_id]) * float(members[contract_id]["real_price_index"]) for contract_id in weights)
    nominal_index = real_index * cpi / 100.0
    payload = {
        "seed": seed,
        "year_index": year_index,
        "year": year,
        "kind_id": kind_id,
        "name": name,
        "real_index": real_index,
        "nominal_index": nominal_index,
        "yoy_change_pct": None if previous is None else _growth(nominal_index, float(previous["nominal_index"])),
        "real_yoy_change_pct": None if previous is None else _growth(real_index, float(previous["real_index"])),
    }
    return round_record(payload)


@deterministic_projection_cache(max_entries=8)
def run_commodity_overlay(global_run: GlobalMacroRun) -> CommodityOverlayRun:
    assets = load_registered_assets()
    config = assets["commodity_overlay_config"]
    contract = assets["commodity_overlay_contract"]
    if config["model_version"] != COMMODITY_MODEL_VERSION:
        raise ValueError("registered commodity overlay config version mismatch")
    if contract["contract_id"] != "commodity_overlay_v1":
        raise ValueError("registered commodity overlay contract id mismatch")

    specs = config["contracts"]
    kinds_cfg = config["kinds"]
    real_oil_bounds = assets["config"]["bounds"]["real_oil_price_index"]
    packed: dict[str, list[dict[str, Any]]] = {contract_id: [] for contract_id in CONTRACT_ORDER}
    kind_series: dict[str, list[dict[str, Any]]] = {kind_id: [] for kind_id in KIND_ORDER}
    wti_spread = float(specs["wti"]["spread_center"])
    satellite_real = {contract_id: 100.0 for contract_id, spec in specs.items() if spec["source"] == "satellite"}

    for index, global_row in enumerate(global_run.rows):
        year_index = int(global_row["year_index"])
        year = int(global_row["year"])
        cpi = float(global_row["cpi_price_level_index_2025_100"])
        previous_global = None if index == 0 else global_run.rows[index - 1]
        year_contracts: dict[str, dict[str, Any]] = {}

        brent = _brent_row(
            global_row,
            previous_global,
            specs["brent"],
            seed=global_run.seed,
            real_bounds=real_oil_bounds,
        )
        year_contracts["brent"] = brent

        if index == 0:
            wti_spread = float(specs["wti"]["spread_center"])
        else:
            wti_spread = _wti_spread(global_run.seed, year_index, wti_spread, specs["wti"])
        year_contracts["wti"] = {
            "kind_id": specs["wti"]["kind"],
            "contract_id": "wti",
            "unit": specs["wti"]["unit"],
            "unit_label": specs["wti"]["unit_label"],
            "nominal_price_usd": float(brent["nominal_price_usd"]) * (1.0 + wti_spread),
            "real_price_index": float(brent["real_price_index"]) * (1.0 + wti_spread) / (1.0 + float(specs["wti"]["spread_center"])),
            "source": "brent_spread",
            "spread": wti_spread,
        }

        for contract_id, spec in specs.items():
            if spec["source"] != "satellite":
                continue
            previous_real = satellite_real[contract_id]
            real_index = _satellite_real(
                seed=global_run.seed,
                year_index=year_index,
                contract_id=contract_id,
                previous_real=previous_real,
                global_row=global_row,
                previous_global=previous_global,
                spec=spec,
            )
            satellite_real[contract_id] = real_index
            year_contracts[contract_id] = _satellite_row(
                contract_id=contract_id,
                spec=spec,
                real_index=real_index,
                cpi=cpi,
            )

        for contract_id in CONTRACT_ORDER:
            previous = packed[contract_id][-1] if packed[contract_id] else None
            packed[contract_id].append(
                _finish_row(
                    seed=global_run.seed,
                    year_index=year_index,
                    year=year,
                    row=year_contracts[contract_id],
                    previous=previous,
                )
            )

        for kind_id in KIND_ORDER:
            previous = kind_series[kind_id][-1] if kind_series[kind_id] else None
            kind_series[kind_id].append(
                _kind_row(
                    seed=global_run.seed,
                    year_index=year_index,
                    year=year,
                    kind_id=kind_id,
                    name=str(kinds_cfg[kind_id]["name"]),
                    members=year_contracts,
                    weights=kinds_cfg[kind_id]["weights"],
                    cpi=cpi,
                    previous=previous,
                )
            )

    kinds = {kind_id: tuple(kind_series[kind_id]) for kind_id in KIND_ORDER}
    contracts = {contract_id: tuple(packed[contract_id]) for contract_id in CONTRACT_ORDER}
    identity = {
        "schema_version": "asset-simulation-commodity-overlay-identity-v1",
        "model_version": COMMODITY_MODEL_VERSION,
        "config_id": config["config_id"],
        "config_hash": assets["commodity_overlay_config_hash"],
        "field_contract_id": contract["contract_id"],
        "field_contract_hash": assets["commodity_overlay_contract_hash"],
        "upstream_global_identity_hash": global_run.identity["identity_hash"],
        "kind_ids": list(KIND_ORDER),
        "contract_ids": list(CONTRACT_ORDER),
        "brent_owner": "oil_commodity",
        "write_back": False,
        "result_hash": sha256_json({"kinds": kinds, "contracts": contracts}),
    }
    return CommodityOverlayRun(identity=identity, kinds=kinds, contracts=contracts)


def commodities_payload(global_run: GlobalMacroRun) -> dict[str, Any]:
    overlay = run_commodity_overlay(global_run)
    return {
        "schemaVersion": "asset-simulation-commodity-overlay-v1",
        "identity": overlay.identity,
        "kinds": overlay.kinds,
        "contracts": overlay.contracts,
    }
