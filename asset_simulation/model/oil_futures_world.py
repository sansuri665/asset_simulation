"""Incremental indexed oil-futures world for deterministic replay.

This owner keeps one mutable, lock-protected build state per immutable
``GlobalMacroRun``.  It advances each half-month at most once, retains named
contract histories, and can publish any already-built earlier cutoff by slicing
those histories rather than replaying the market from 2025.

The public payload remains owned by :mod:`oil_futures_overlay`; this module uses
the same helper functions and deliberately preserves the existing response and
identity contracts.  The old full-rebuild implementation remains available in
``oil_futures_overlay._rebuild_oil_futures_payload`` as a parity oracle during
the P0-B validation period.
"""

from __future__ import annotations

from bisect import bisect_right
import copy
import math
import threading
from collections import OrderedDict, defaultdict
from typing import Any, Mapping

from .engine import GlobalMacroRun
from .performance_cache import deterministic_projection_cache
from .registry import load_registered_assets, sha256_json
from .oil_futures_overlay import (
    OIL_FUTURES_MODEL_VERSION,
    _advance_curve_factors,
    _advance_market_liquidity,
    _aggregate_turns_to_months,
    _allocate_lots,
    _contract_liquidity_shares,
    _contract_turn_bar,
    _curve_contracts,
    _curve_state,
    _half_turn_serial,
    _latest_completed_macro_row,
    _multiply_price_history,
    _participant_limits_for_contract,
    _reference_half_turns,
    _refresh_turn_liquidity,
    _round_nested,
    _summary_from_months,
    _week_serial,
)


def _validated_assets(
    global_run: GlobalMacroRun,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assets = load_registered_assets()
    config = assets["oil_futures_overlay_config"]
    contract = assets["oil_futures_overlay_contract"]
    if config["model_version"] != OIL_FUTURES_MODEL_VERSION:
        raise ValueError("registered oil futures overlay config version mismatch")
    if contract["contract_id"] != "oil_futures_overlay_v8":
        raise ValueError("registered oil futures overlay contract id mismatch")

    specification = config["contract_specification"]
    participant_policy = config["participant_limits"]
    expected_tick_value = (
        float(specification["contract_size_bbl"])
        * float(specification["minimum_price_fluctuation_usd_per_bbl"])
    )
    if not math.isclose(
        float(specification["tick_value_usd_per_lot"]),
        expected_tick_value,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError(
            "oil futures tick value does not match contract size and price tick"
        )
    if not 0.0 < float(specification["maintenance_margin_rate_pct"]) < float(
        specification["initial_margin_rate_pct"]
    ) < 100.0:
        raise ValueError("oil futures margin rates are invalid")
    if not 0.0 < float(
        participant_policy["single_contract_open_interest_rate_pct"]
    ) < 100.0:
        raise ValueError("oil futures participant open-interest limit is invalid")
    if not 0.0 < float(participant_policy["turn_volume_rate_pct"]) < 100.0:
        raise ValueError("oil futures participant turnover limit is invalid")
    if not 1 <= int(participant_policy["turn_volume_equivalent_weeks"]) <= int(
        participant_policy["turn_volume_reference_weeks"]
    ):
        raise ValueError("oil futures participant turnover smoothing window is invalid")

    years = [int(row["year"]) for row in global_run.rows]
    if not years:
        raise ValueError("oil futures world requires a non-empty global run")
    return assets, config, contract


class OilFuturesWorld:
    """Incrementally build and index one immutable-Seed oil-futures world."""

    def __init__(self, global_run: GlobalMacroRun):
        self.global_run = global_run
        self.assets, self.config, self.contract = _validated_assets(global_run)
        self.specification = self.config["contract_specification"]
        self.participant_policy = self.config["participant_limits"]
        self.curve_config = self.config["curve"]
        self.liquidity_config = self.config["liquidity"]
        if str(self.liquidity_config.get("roll_migration_curve")) != "smoothstep":
            raise ValueError("oil futures roll migration curve must be smoothstep")

        self.minimum_year = min(int(row["year"]) for row in global_run.rows)
        self.maximum_year = max(int(row["year"]) for row in global_run.rows)
        self.anchor_macro_row = _latest_completed_macro_row(
            global_run.rows, int(self.liquidity_config["anchor_year"])
        )

        self.reference_turns = _reference_half_turns(
            global_run,
            as_of_year=self.maximum_year,
            as_of_month=12,
            as_of_half=2,
        )
        if not self.reference_turns:
            raise ValueError("oil futures world has no reference half-month data")
        self.reference_serials = [
            _half_turn_serial(
                int(item["year"]), int(item["month"]), int(item["half"])
            )
            for item in self.reference_turns
        ]
        self.reference_serial_set = set(self.reference_serials)

        self.lock = threading.RLock()
        self.processed_count = 0
        self.closes: list[float] = []
        self.factors: dict[str, float] | None = None
        self.liquidity_state: dict[str, float] | None = None
        self.liquidity_weeks: list[dict[str, Any]] = []
        self.previous_reference_week_close: float | None = None
        self.previous_contract_open_interest: dict[str, int] = {}
        self.histories: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        self.history_serials: defaultdict[str, list[int]] = defaultdict(list)
        self.contract_history_cache: OrderedDict[
            tuple[str, int], list[dict[str, Any]]
        ] = OrderedDict()
        self.contract_history_cache_max_entries = 96
        # Main turns are intentionally retained in raw source-contract prices.
        # View construction applies only the rolls visible at that cutoff.
        self.main_turns_raw: list[dict[str, Any]] = []
        self.main_serials: list[int] = []
        self.rolls: list[dict[str, Any]] = []
        self.roll_serials: list[int] = []
        self.roll_prefix_counts: list[int] = []
        self.previous_main_id: str | None = None
        self.cutoff_states: dict[int, dict[str, Any]] = {}

    def _validate_cutoff(self, year: int, month: int, half: int) -> int:
        if not 1 <= int(month) <= 12:
            raise ValueError("as_of_month must be between 1 and 12")
        if int(half) not in (1, 2):
            raise ValueError("as_of_half must be 1 or 2")
        if int(year) < self.minimum_year or int(year) > self.maximum_year:
            raise ValueError("as_of_year is outside the generated global run")
        serial = _half_turn_serial(int(year), int(month), int(half))
        if serial not in self.reference_serial_set:
            raise ValueError("oil reference has no visible half-month data at the cutoff")
        return serial

    def _process_reference(self, reference: Mapping[str, Any], serial: int) -> None:
        year = int(reference["year"])
        month = int(reference["month"])
        half = int(reference["half"])
        self.closes.append(float(reference["close"]))
        macro_row = _latest_completed_macro_row(self.global_run.rows, year)
        targets = self._curve_targets(macro_row, month)
        self.factors = _advance_curve_factors(
            seed=self.global_run.seed,
            month_address=serial,
            previous=self.factors,
            targets=targets,
            curve_config=self.curve_config,
        )
        curve_contracts = _curve_contracts(
            as_of_year=year,
            as_of_month=month,
            as_of_half=half,
            spot=float(reference["close"]),
            factors=self.factors,
            config=self.config,
        )
        for item in curve_contracts:
            contract_id = str(item["contract_id"])
            previous_bar = (
                self.histories[contract_id][-1]
                if self.histories[contract_id]
                else None
            )
            self.histories[contract_id].append(
                _contract_turn_bar(reference, item, previous_bar)
            )
            self.history_serials[contract_id].append(serial)

        roll_threshold = float(self.config["main_roll_months_before_expiry"])
        main_item = (
            curve_contracts[1]
            if float(curve_contracts[0]["months_to_expiry"]) < roll_threshold
            else curve_contracts[0]
        )
        main_id = str(main_item["contract_id"])
        for week_index, reference_week in enumerate(reference.get("weekly", ())):
            week_number = int(reference_week["week"])
            week_address = _week_serial(year, month, week_number)
            self.liquidity_state = _advance_market_liquidity(
                seed=self.global_run.seed,
                week_address=week_address,
                macro_row=macro_row,
                anchor_macro_row=self.anchor_macro_row,
                reference_close=float(reference_week["close"]),
                previous_reference_close=self.previous_reference_week_close,
                previous=self.liquidity_state,
                liquidity_config=self.liquidity_config,
            )
            volume_shares, open_interest_shares = _contract_liquidity_shares(
                curve_contracts,
                main_contract_id=main_id,
                current_year=year,
                current_month=month,
                week=week_number,
                liquidity_config=self.liquidity_config,
            )
            volume_allocations = _allocate_lots(
                int(self.liquidity_state["weekly_volume_lots"]),
                curve_contracts,
                volume_shares,
                seed=self.global_run.seed,
                week_address=week_address,
                stream="contract_volume",
                noise_scale=float(
                    self.liquidity_config["contract_volume_noise_scale"]
                ),
            )
            open_interest_allocations = _allocate_lots(
                int(self.liquidity_state["total_open_interest_lots"]),
                curve_contracts,
                open_interest_shares,
                seed=self.global_run.seed,
                week_address=week_address,
                stream="contract_open_interest",
                noise_scale=float(
                    self.liquidity_config["contract_open_interest_noise_scale"]
                ),
            )
            for index, item in enumerate(curve_contracts):
                contract_id = str(item["contract_id"])
                contract_week = self.histories[contract_id][-1]["weekly"][
                    week_index
                ]
                open_interest = int(open_interest_allocations[index])
                previous_open_interest = self.previous_contract_open_interest.get(
                    contract_id, 0
                )
                contract_week.update(
                    {
                        "volume_lots": int(volume_allocations[index]),
                        "open_interest_lots": open_interest,
                        "open_interest_change_lots": (
                            open_interest - previous_open_interest
                        ),
                    }
                )
                self.previous_contract_open_interest[contract_id] = open_interest
            self.liquidity_weeks.append(
                {
                    "year": year,
                    "month": month,
                    "week": week_number,
                    "volume_lots": int(
                        self.liquidity_state["weekly_volume_lots"]
                    ),
                    "open_interest_lots": int(
                        self.liquidity_state["total_open_interest_lots"]
                    ),
                    "turnover_ratio": float(
                        self.liquidity_state["weekly_turnover_ratio"]
                    ),
                    "structural_scale": float(
                        self.liquidity_state["structural_scale"]
                    ),
                    "activity_scale": float(
                        self.liquidity_state["activity_scale"]
                    ),
                }
            )
            self.previous_reference_week_close = float(reference_week["close"])
        for item in curve_contracts:
            _refresh_turn_liquidity(
                self.histories[str(item["contract_id"])][-1]
            )

        roll_from: str | None = None
        if self.previous_main_id is not None and main_id != self.previous_main_id:
            new_history = self.histories[main_id]
            if len(new_history) < 2 or not self.main_turns_raw:
                raise ValueError("main roll lacks a common prior settlement")
            # The last turn of the outgoing main segment has not itself been
            # back-adjusted by this new roll, so its raw close is the same
            # denominator used by the legacy sequential rebuild.
            old_prior = float(self.main_turns_raw[-1]["close"])
            new_prior = float(new_history[-2]["close"])
            link_ratio = new_prior / old_prior
            self.rolls.append(
                {
                    "year": year,
                    "month": month,
                    "half": half,
                    "label": f"{year}-{month:02d}-H{half}",
                    "from_contract_id": self.previous_main_id,
                    "to_contract_id": main_id,
                    "old_prior_close_usd": old_prior,
                    "new_prior_close_usd": new_prior,
                    "back_adjustment_ratio": link_ratio,
                }
            )
            self.roll_serials.append(serial)
            # The legacy rebuild multiplies every main turn already present,
            # then appends the new source-contract turn.
            self.roll_prefix_counts.append(len(self.main_turns_raw))
            roll_from = self.previous_main_id

        main_bar = copy.deepcopy(self.histories[main_id][-1])
        main_bar["source_contract_id"] = main_id
        main_bar["roll_from_contract_id"] = roll_from
        main_bar["adjustment_multiplier"] = 1.0
        self.main_turns_raw.append(main_bar)
        self.main_serials.append(serial)
        self.previous_main_id = main_id

        self.cutoff_states[serial] = {
            "reference_count": self.processed_count + 1,
            "liquidity_week_count": len(self.liquidity_weeks),
            "main_count": len(self.main_turns_raw),
            "roll_count": len(self.rolls),
            "main_contract_id": main_id,
            "latest_contracts": tuple(dict(item) for item in curve_contracts),
            "latest_inputs": {**targets, **self.factors},
        }

    def _curve_targets(
        self, macro_row: Mapping[str, Any], month: int
    ) -> dict[str, float]:
        # Local import avoids widening the already-large public helper surface.
        from .oil_futures_overlay import _curve_targets

        return _curve_targets(
            macro_row,
            self.closes,
            month=month,
            curve_config=self.curve_config,
        )

    def ensure(self, *, year: int, month: int, half: int) -> dict[str, Any]:
        serial = self._validate_cutoff(year, month, half)
        target_count = bisect_right(self.reference_serials, serial)
        with self.lock:
            while self.processed_count < target_count:
                index = self.processed_count
                self._process_reference(
                    self.reference_turns[index], self.reference_serials[index]
                )
                self.processed_count += 1
            return self.cutoff_states[serial]

    def contract_monthly_history(
        self,
        *,
        contract_id: str,
        as_of_year: int,
        as_of_month: int,
        as_of_half: int,
    ) -> list[dict[str, Any]]:
        """Return one named contract's visible history without packing a market."""

        serial = self._validate_cutoff(as_of_year, as_of_month, as_of_half)
        self.ensure(year=as_of_year, month=as_of_month, half=as_of_half)
        with self.lock:
            key = (str(contract_id), serial)
            cached = self.contract_history_cache.get(key)
            if cached is not None:
                self.contract_history_cache.move_to_end(key)
                return cached
            serials = self.history_serials.get(str(contract_id), ())
            count = bisect_right(serials, serial)
            if count <= 0:
                return []
            monthly = _round_nested(
                _aggregate_turns_to_months(
                    self.histories[str(contract_id)][:count]
                )
            )
            self.contract_history_cache[key] = monthly
            self.contract_history_cache.move_to_end(key)
            while (
                len(self.contract_history_cache)
                > self.contract_history_cache_max_entries
            ):
                self.contract_history_cache.popitem(last=False)
            return monthly

    def _adjusted_main_turns(
        self, *, main_count: int, roll_count: int
    ) -> list[dict[str, Any]]:
        # Use the same multiplication order as the legacy implementation so
        # fixed-Seed parity is not dependent on floating-point reassociation.
        turns = copy.deepcopy(self.main_turns_raw[:main_count])
        for index in range(roll_count):
            prefix_count = min(self.roll_prefix_counts[index], len(turns))
            if prefix_count <= 0:
                continue
            _multiply_price_history(
                turns[:prefix_count],
                float(self.rolls[index]["back_adjustment_ratio"]),
            )
        return turns

    def payload(
        self,
        *,
        as_of_year: int,
        as_of_month: int,
        as_of_half: int = 2,
    ) -> dict[str, Any]:
        state = self.ensure(
            year=as_of_year, month=as_of_month, half=as_of_half
        )
        serial = _half_turn_serial(as_of_year, as_of_month, as_of_half)
        with self.lock:
            current_contracts: list[dict[str, Any]] = []
            for item in state["latest_contracts"]:
                packed = dict(item)
                contract_id = str(item["contract_id"])
                turn_count = bisect_right(
                    self.history_serials[contract_id], serial
                )
                contract_turns = self.histories[contract_id][:turn_count]
                packed["visible_turn_count"] = len(contract_turns)
                packed["monthly"] = _aggregate_turns_to_months(contract_turns)
                packed.update(_summary_from_months(packed["monthly"]))
                packed["participantLimits"] = _participant_limits_for_contract(
                    contract=packed,
                    contract_turns=contract_turns,
                    price_usd=float(packed["price_usd"]),
                    specification=self.specification,
                    policy=self.participant_policy,
                )
                packed["is_main_source"] = (
                    contract_id == state["main_contract_id"]
                )
                current_contracts.append(packed)

            reference_turns = self.reference_turns[
                : int(state["reference_count"])
            ]
            references = _aggregate_turns_to_months(reference_turns)
            main_turns = self._adjusted_main_turns(
                main_count=int(state["main_count"]),
                roll_count=int(state["roll_count"]),
            )
            main_months = _aggregate_turns_to_months(main_turns)
            reference_summary = _summary_from_months(references)
            main_summary = _summary_from_months(main_months)
            active_contract = next(
                item
                for item in current_contracts
                if item["contract_id"] == state["main_contract_id"]
            )
            curve_state = _curve_state(
                current_contracts,
                threshold_pct=float(
                    self.curve_config["flat_curve_threshold_pct"]
                ),
            )
            visible_rolls = [
                dict(item) for item in self.rolls[: int(state["roll_count"])]
            ]
            visible_liquidity = [
                dict(item)
                for item in self.liquidity_weeks[
                    : int(state["liquidity_week_count"])
                ]
            ]

            result = {
                "schemaVersion": "asset-simulation-oil-futures-response-v8",
                "asOf": {
                    "year": int(as_of_year),
                    "month": int(as_of_month),
                    "half": int(as_of_half),
                    "label": (
                        f"{int(as_of_year)}-{int(as_of_month):02d}-"
                        f"H{int(as_of_half)}"
                    ),
                },
                "contractSpecification": dict(self.specification),
                "participantLimitsPolicy": {
                    **dict(self.participant_policy),
                    "applies_to": "futures_only",
                    "enforced": False,
                },
                "futuresLiquidity": {
                    "market": "global_oil_benchmark",
                    "spot_included": False,
                    "weekly": visible_liquidity,
                    "latest": dict(visible_liquidity[-1]),
                },
                "reference": {
                    "instrument_id": "OIL-REF",
                    "code": "SPOT",
                    "name": "原油现货参考",
                    "unit": "usd_per_bbl",
                    "tradable": False,
                    "monthly": references,
                    **reference_summary,
                },
                "curve": {
                    "state": curve_state,
                    "nearest_contract_id": current_contracts[0]["contract_id"],
                    "main_contract_id": state["main_contract_id"],
                    "far_contract_id": current_contracts[-1]["contract_id"],
                    "contracts": current_contracts,
                    "inputs": dict(state["latest_inputs"]),
                },
                "mainContinuous": {
                    "instrument_id": "OIL-MAIN",
                    "code": "MAIN",
                    "name": "原油主连",
                    "unit": "usd_per_bbl",
                    "active_contract_id": state["main_contract_id"],
                    "adjustment": "backward_ratio_adjusted",
                    "roll_months_before_expiry": int(
                        self.config["main_roll_months_before_expiry"]
                    ),
                    "rolls": visible_rolls,
                    "tradable": False,
                    "monthly": main_months,
                    "participantLimits": copy.deepcopy(
                        active_contract["participantLimits"]
                    ),
                    **main_summary,
                },
            }
            identity = {
                "schema_version": "asset-simulation-oil-futures-identity-v8",
                "model_version": OIL_FUTURES_MODEL_VERSION,
                "config_id": self.config["config_id"],
                "config_hash": self.assets[
                    "oil_futures_overlay_config_hash"
                ],
                "field_contract_id": self.contract["contract_id"],
                "field_contract_hash": self.assets[
                    "oil_futures_overlay_contract_hash"
                ],
                "upstream_global_identity_hash": self.global_run.identity[
                    "identity_hash"
                ],
                "information_cutoff": (
                    "visible_half_month_weeks_and_latest_completed_annual_macro_row"
                ),
                "listed_contract_count": int(
                    self.config["listed_contract_count"]
                ),
                "contract_lifecycle_months": int(
                    self.config["contract_lifecycle_months"]
                ),
                "contract_lifecycle_turns": int(
                    self.config["contract_lifecycle_months"]
                )
                * 2,
                "turns_per_year": 24,
                "curve_factors": [
                    "long_slope_pct",
                    "near_pressure_pct",
                    "curvature_pct",
                ],
                "liquidity_model": (
                    "bounded_global_liquidity_with_eight_week_smoothstep_roll"
                ),
                "liquidity_scope": "futures_only_spot_unchanged",
                "participant_limits_policy_id": str(
                    self.participant_policy["policy_id"]
                ),
                "participant_limits_status": str(
                    self.participant_policy["status"]
                ),
                "player_price_feedback": False,
                "write_back": False,
                "orders_enabled": False,
                "result_hash": sha256_json(result),
            }
            return _round_nested({"ok": True, "identity": identity, **result})

    def stats(self) -> dict[str, Any]:
        with self.lock:
            return {
                "upstream_global_identity_hash": self.global_run.identity[
                    "identity_hash"
                ],
                "processed_half_turns": self.processed_count,
                "available_half_turns": len(self.reference_turns),
                "named_contract_count": len(self.histories),
                "stored_contract_turns": sum(
                    len(items) for items in self.histories.values()
                ),
                "stored_liquidity_weeks": len(self.liquidity_weeks),
                "stored_rolls": len(self.rolls),
                "contract_history_cache_entries": len(self.contract_history_cache),
                "contract_history_cache_max_entries": self.contract_history_cache_max_entries,
            }


@deterministic_projection_cache(max_entries=4)
def get_oil_futures_world(global_run: GlobalMacroRun) -> OilFuturesWorld:
    """Return the shared incremental futures world for one global identity."""

    return OilFuturesWorld(global_run)
