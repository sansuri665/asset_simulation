"""Typed exogenous impulse ports reserved for future event layers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping


IMPULSE_SCHEMA_VERSION = "asset-simulation-exogenous-impulses-v1"
IMPULSE_FIELDS = (
    "demand_growth_impulse_pp",
    "potential_growth_impulse_pp",
    "inflation_impulse_pp",
    "dollar_funding_impulse_index",
    "credit_spread_impulse_bps",
    "oil_supply_growth_impulse_pp",
)
IMPULSE_OWNERS = {
    "demand_growth_impulse_pp": "real_economy",
    "potential_growth_impulse_pp": "real_economy",
    "inflation_impulse_pp": "inflation_nominal",
    "dollar_funding_impulse_index": "funding_credit",
    "credit_spread_impulse_bps": "funding_credit",
    "oil_supply_growth_impulse_pp": "oil_commodity",
}
IMPULSE_UNITS = {
    "demand_growth_impulse_pp": "percentage_points",
    "potential_growth_impulse_pp": "percentage_points",
    "inflation_impulse_pp": "percentage_points",
    "dollar_funding_impulse_index": "index_points",
    "credit_spread_impulse_bps": "basis_points",
    "oil_supply_growth_impulse_pp": "percentage_points",
}


@dataclass(frozen=True)
class ExogenousImpulseBundle:
    """One transition-start bundle; ordinary worlds always use the zero bundle."""

    seed: int
    source_year_index: int
    source_year: int
    target_year_index: int
    target_year: int
    demand_growth_impulse_pp: float = 0.0
    potential_growth_impulse_pp: float = 0.0
    inflation_impulse_pp: float = 0.0
    dollar_funding_impulse_index: float = 0.0
    credit_spread_impulse_bps: float = 0.0
    oil_supply_growth_impulse_pp: float = 0.0

    @property
    def is_zero(self) -> bool:
        return all(getattr(self, field) == 0.0 for field in IMPULSE_FIELDS)

    def values(self) -> dict[str, float]:
        return {field: float(getattr(self, field)) for field in IMPULSE_FIELDS}

    def as_record(self) -> dict[str, Any]:
        return {
            "schema_version": IMPULSE_SCHEMA_VERSION,
            "owner": "external_event_layer",
            "timing": "exogenous_at_transition_start",
            "field_owners": IMPULSE_OWNERS,
            "field_units": IMPULSE_UNITS,
            **asdict(self),
        }


def build_impulse_bundle(
    *,
    seed: int,
    target_year_index: int,
    target_year: int,
    values: Mapping[str, Any] | ExogenousImpulseBundle | None = None,
) -> ExogenousImpulseBundle:
    """Validate and normalize one event-layer bundle for a target transition."""

    if isinstance(values, ExogenousImpulseBundle):
        bundle = values
    else:
        raw = {} if values is None else dict(values)
        unknown = set(raw) - set(IMPULSE_FIELDS)
        if unknown:
            raise KeyError(f"unknown exogenous impulse fields: {sorted(unknown)}")
        numeric: dict[str, float] = {}
        for field in IMPULSE_FIELDS:
            value = raw.get(field, 0.0)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{field} must be numeric")
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(f"{field} must be finite")
            numeric[field] = value
        bundle = ExogenousImpulseBundle(
            seed=seed,
            source_year_index=target_year_index,
            source_year=target_year,
            target_year_index=target_year_index,
            target_year=target_year,
            **numeric,
        )
    if (
        bundle.seed != seed
        or bundle.source_year_index != target_year_index
        or bundle.target_year_index != target_year_index
        or bundle.source_year != target_year
        or bundle.target_year != target_year
    ):
        raise ValueError("exogenous impulse seed or transition timing mismatch")
    return bundle


def zero_impulse_bundle(*, seed: int, target_year_index: int, target_year: int) -> ExogenousImpulseBundle:
    """Construct the only bundle used by the ordinary-world product runner."""

    return build_impulse_bundle(
        seed=seed,
        target_year_index=target_year_index,
        target_year=target_year,
    )
