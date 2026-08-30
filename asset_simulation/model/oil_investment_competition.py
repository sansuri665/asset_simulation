"""Deterministic multi-institution investment-committee demo for the game UI."""

from __future__ import annotations

from collections import Counter
import math
import threading
from typing import Any, Mapping

from .engine import GlobalMacroRun
from .math_utils import clamp
from .oil_execution_desk import generate_oil_execution_desk_roster
from .oil_futures_overlay import oil_futures_payload
from .oil_futures_account import (
    apply_oil_futures_account_constraints,
    create_oil_futures_account,
    settle_oil_futures_account_turn,
)
from .oil_short_term_forecast import (
    generate_institution_profile_for_score_range,
    generate_oil_short_term_forecast,
)
from .oil_strategy_research import generate_oil_strategy_research_roster
from .oil_strategy_risk import build_oil_strategy_risk_review
from .oil_trading_strategy import (
    _half_turn_serial,
    _turn_from_serial,
    build_oil_strategy_decision,
    settle_oil_strategy_turn,
)
from .institution_organization import initial_proprietary_capital_usd
from .random_stream import normal
from .registry import load_registered_assets, sha256_json


OIL_INVESTMENT_COMPETITION_MODEL_VERSION = (
    "asset-simulation-oil-investment-competition-v0.6.0"
)
GAME_START = (2030, 1, 1)
PARTICIPANT_NAMES = (
    ("PLAYER", "玩家机构", True),
    ("NORTHSTAR", "北辰全球商品", False),
    ("PELAGIC", "瀚海资源资本", False),
    ("EQUATOR", "赤道宏观基金", False),
)


def _round_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_nested(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("oil investment competition contains a non-finite value")
        return round(value, 8)
    return value


def _pick_index(seed: int, address: str, size: int) -> int:
    draw = normal(int(seed), address, 0)
    uniform = 0.5 * (1.0 + math.erf(draw / math.sqrt(2.0)))
    return min(size - 1, int(uniform * size))


def _participant_seed(seed: int, index: int) -> int:
    return int(seed) * 10_007 + (index + 1) * 1_009 + 20_260_825


def _appointment_summary(profile: Mapping[str, Any], kind: str) -> dict[str, Any]:
    if kind == "forecast":
        return {
            "kind": kind,
            "personnel_id": profile["institution_id"],
            "display_name": profile["display_name"],
            "candidate_index": None,
            "profile_hash": profile["profile_hash"],
            "capability_total_score": profile["capability_total_score"],
            "radar": profile["capability_radar"],
            "tags": [],
        }
    appointment = profile["appointment"]
    result: dict[str, Any] = {
        "kind": kind,
        "personnel_id": appointment["personnel_id"],
        "display_name": appointment["display_name"],
        "candidate_index": appointment.get("candidate_index"),
        "profile_hash": profile["profile_hash"],
    }
    if kind == "strategy":
        result.update(
            {
                "capability_total_score": None,
                "radar": profile["style_radar"],
                "tags": profile["style_tags"],
                "construction_capability_radar": profile[
                    "construction_capability_radar"
                ],
                "construction_capability_tags": profile[
                    "construction_capability_tags"
                ],
            }
        )
    else:
        result.update(
            {
                "capability_total_score": profile["capability_total_score"],
                "radar": profile["capability_radar"],
                "tags": profile["capability_tags"],
            }
        )
    return result


def build_competition_participants(seed: int) -> list[dict[str, Any]]:
    """Draw one deterministic three-role investment team per institution."""

    participants = []
    for index, (participant_id, display_name, is_player) in enumerate(
        PARTICIPANT_NAMES
    ):
        participant_seed = _participant_seed(seed, index)
        forecast_center = clamp(
            50.0
            + 17.5
            * normal(
                participant_seed,
                f"oil_investment_demo.{participant_id}.forecast_level",
                index,
            ),
            18.0,
            82.0,
        )
        forecast_profile = generate_institution_profile_for_score_range(
            seed=participant_seed + 11,
            score_min=max(0.0, forecast_center - 5.0),
            score_max=min(100.0, forecast_center + 5.0),
        )
        forecast_profile = {
            **forecast_profile,
            "display_name": f"{display_name}研究所",
        }
        forecast_profile["profile_hash"] = sha256_json(
            {key: value for key, value in forecast_profile.items() if key != "profile_hash"}
        )

        strategy_roster = generate_oil_strategy_research_roster(
            seed=participant_seed + 101,
            candidate_count=5,
        )["candidates"]
        execution_roster = generate_oil_execution_desk_roster(
            seed=participant_seed + 307,
            candidate_count=5,
        )["candidates"]
        strategy_index = _pick_index(
            participant_seed, f"oil_investment_demo.{participant_id}.strategy", 5
        )
        execution_index = _pick_index(
            participant_seed, f"oil_investment_demo.{participant_id}.execution", 5
        )
        strategy_profile = strategy_roster[strategy_index]
        execution_profile = execution_roster[execution_index]
        strategy_risk_review = build_oil_strategy_risk_review(strategy_profile)
        appointments = {
            "forecast": _appointment_summary(forecast_profile, "forecast"),
            "strategy": _appointment_summary(strategy_profile, "strategy"),
            "execution": _appointment_summary(execution_profile, "execution"),
        }
        participants.append(
            {
                "participant_id": participant_id,
                "display_name": display_name,
                "is_player": is_player,
                "draw_seed": participant_seed,
                "appointments": appointments,
                "profiles": {
                    "forecast": forecast_profile,
                    "strategy": strategy_profile,
                    "execution": execution_profile,
                },
                "strategy_risk_review": {
                    "strategy": strategy_risk_review["strategy"],
                    "riskMandate": strategy_risk_review["riskMandate"],
                    "strategyRiskPressures": strategy_risk_review[
                        "strategyRiskPressures"
                    ],
                    "proposedPolicy": strategy_risk_review["proposedPolicy"],
                    "rationales": strategy_risk_review["rationales"],
                    "review_hash": strategy_risk_review["identity"][
                        "result_hash"
                    ],
                },
                "roster_hash": sha256_json(appointments),
            }
        )
    return participants


def _new_account(account_id: str, initial_equity_usd: float) -> dict[str, Any]:
    return {
        "formal_account": create_oil_futures_account(
            account_id=account_id,
            initial_cash_usd=initial_equity_usd,
        ),
        "previous_vintage": None,
        "strategy_risk_state": None,
        "thesis_state": None,
        "gross_turnover_history": [],
        "cumulative_pnl_usd": 0.0,
        "cumulative_execution_cost_usd": 0.0,
        "cumulative_idle_cash_interest_usd": 0.0,
        "cumulative_margin_financing_cost_usd": 0.0,
        "cumulative_forced_liquidation_cost_usd": 0.0,
        "cumulative_traded_lots": 0,
        "winning_turns": 0,
        "losing_turns": 0,
        "equity_peak_usd": float(initial_equity_usd),
        "maximum_drawdown_pct": 0.0,
        "strategy_risk_status_counts": Counter(),
        "strategy_position_risk_tier_counts": Counter(),
        "thesis_status_counts": Counter(),
    }


def _public_participant(participant: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: participant[key]
        for key in (
            "participant_id",
            "display_name",
            "is_player",
            "draw_seed",
            "appointments",
            "strategy_risk_review",
            "roster_hash",
        )
    }


class OilInvestmentCompetitionSession:
    """Incrementally replays all institutions against the same immutable market."""

    def __init__(self, run: GlobalMacroRun):
        self.run = run
        self.seed = int(run.seed)
        self.lock = threading.Lock()
        self.participants = build_competition_participants(self.seed)
        self.initial_equity_usd = initial_proprietary_capital_usd()
        self._reset_runtime()

    def _reset_runtime(self) -> None:
        self.current_serial = _half_turn_serial(*GAME_START)
        self.current_market = oil_futures_payload(
            self.run,
            as_of_year=GAME_START[0],
            as_of_month=GAME_START[1],
            as_of_half=GAME_START[2],
        )
        self.accounts = {
            participant["participant_id"]: _new_account(
                str(participant["participant_id"]), self.initial_equity_usd
            )
            for participant in self.participants
        }
        self.reports: list[dict[str, Any]] = []

    def _advance_one(self) -> None:
        year, month, half = _turn_from_serial(self.current_serial)
        next_year, next_month, next_half = _turn_from_serial(self.current_serial + 1)
        next_market = oil_futures_payload(
            self.run,
            as_of_year=next_year,
            as_of_month=next_month,
            as_of_half=next_half,
        )
        participant_reports = []
        for participant in self.participants:
            participant_id = str(participant["participant_id"])
            account = self.accounts[participant_id]
            formal_before = account["formal_account"]
            profiles = participant["profiles"]
            before_equity = float(formal_before["equity_usd"])
            before_positions = {
                str(key): int(value)
                for key, value in formal_before["positions"].items()
            }
            if bool(formal_before.get("ever_insolvent")):
                participant_reports.append(
                    {
                        "participant_id": participant_id,
                        "display_name": participant["display_name"],
                        "is_player": participant["is_player"],
                        "equity_before_usd": before_equity,
                        "equity_after_usd": before_equity,
                        "turn_pnl_usd": 0.0,
                        "strategy_turn_pnl_usd": 0.0,
                        "turn_return_pct": 0.0,
                        "cumulative_return_pct": 100.0
                        * (before_equity / self.initial_equity_usd - 1.0),
                        "maximum_drawdown_pct": account["maximum_drawdown_pct"],
                        "positions": {},
                        "gross_position_lots": 0,
                        "margin_to_equity_pct": None,
                        "cash_balance_usd": before_equity,
                        "restricted_initial_margin_usd": 0.0,
                        "maintenance_margin_usd": 0.0,
                        "available_funds_usd": max(0.0, before_equity),
                        "excess_liquidity_usd": before_equity,
                        "account_status": "insolvent",
                        "margin_call_triggered": False,
                        "margin_call_amount_usd": 0.0,
                        "forced_liquidation_lots": 0,
                        "idle_cash_interest_usd": 0.0,
                        "margin_financing_cost_usd": 0.0,
                        "forced_liquidation_cost_usd": 0.0,
                        "account_authorization": None,
                        "account_ledger": None,
                        "traded_lots": 0,
                        "buy_lots": 0,
                        "sell_lots": 0,
                        "execution_cost_usd": 0.0,
                        "pnl_attribution": {},
                        "gross_pnl_before_cost_usd": 0.0,
                        "risk_status": "insolvent",
                        "strategy_risk_status": "insolvent",
                        "strategy_position_risk_tier": "insolvent",
                        "strategy_position_risk_current_utilization": None,
                        "strategy_position_risk_proposed_utilization": None,
                        "strategy_position_risk_approved_utilization": None,
                        "strategy_position_risk_gap_completion": 0.0,
                        "strategy_position_risk_current_reduce_only": True,
                        "thesis_statuses": {},
                        "capital_authorization_pct_of_company_equity": 0.0,
                        "capital_authorization_usd": 0.0,
                        "strategy_mandate_reference_capital_pct_of_company_equity": 0.0,
                        "strategy_risk_clipped_gross_lots": 0,
                        "risk_clipped_gross_lots": 0,
                        "targets": [],
                    }
                )
                continue
            vintage = generate_oil_short_term_forecast(
                self.run,
                as_of_year=year,
                as_of_month=month,
                as_of_half=half,
                institution_profile=profiles["forecast"],
                previous_vintage=account["previous_vintage"],
                market=self.current_market,
            )
            decision = build_oil_strategy_decision(
                self.current_market,
                vintage,
                positions=before_positions,
                equity_usd=before_equity,
                strategy_research_profile=profiles["strategy"],
                execution_desk_profile=profiles["execution"],
                strategy_risk_state=account["strategy_risk_state"],
                thesis_state=account["thesis_state"],
                fee_state={
                    "rolling_gross_turnover_lots": sum(
                        account["gross_turnover_history"][-24:]
                    )
                },
            )
            account_authorization = apply_oil_futures_account_constraints(
                formal_before,
                self.current_market,
                decision,
            )
            decision = account_authorization["decision"]
            settlement = settle_oil_strategy_turn(
                self.current_market,
                next_market,
                decision,
                positions=before_positions,
                equity_usd=before_equity,
                allow_equity_exhaustion=True,
            )
            account_settlement = settle_oil_futures_account_turn(
                formal_before,
                self.current_market,
                next_market,
                settlement,
            )
            after = account_settlement["accountAfter"]
            account_ledger = account_settlement["ledger"]
            execution = settlement["executionSummary"]
            portfolio_risk = decision["portfolioRisk"]
            strategy_risk = decision["strategyRisk"]
            after_equity = float(after["equity_usd"])
            turn_pnl = float(account_ledger["account_net_pnl_usd"])
            strategy_turn_pnl = float(settlement["accountAfter"]["turn_pnl_usd"])
            account["formal_account"] = account_settlement["state"]
            account["previous_vintage"] = vintage
            account["strategy_risk_state"] = dict(strategy_risk["state"])
            account["thesis_state"] = dict(
                settlement["thesisInvalidation"]["state"]
            )
            for thesis_contract in account["thesis_state"].get(
                "contracts", {}
            ).values():
                account["thesis_status_counts"][
                    str(thesis_contract["status"])
                ] += 1
            account["gross_turnover_history"].append(
                int(execution["gross_turnover_lots"])
            )
            account["cumulative_pnl_usd"] += turn_pnl
            account["cumulative_execution_cost_usd"] += float(
                execution["execution_cost_usd"]
            )
            account["cumulative_idle_cash_interest_usd"] += float(
                account_ledger["idle_cash_interest_usd"]
            )
            account["cumulative_margin_financing_cost_usd"] += float(
                account_ledger["margin_financing_cost_usd"]
            )
            account["cumulative_forced_liquidation_cost_usd"] += float(
                account_ledger["forced_liquidation_cost_usd"]
            )
            account["cumulative_traded_lots"] += int(execution["traded_lots"])
            account["winning_turns"] += turn_pnl > 0.0
            account["losing_turns"] += turn_pnl < 0.0
            account["equity_peak_usd"] = max(
                float(account["equity_peak_usd"]), after_equity
            )
            drawdown_pct = 100.0 * (
                after_equity / float(account["equity_peak_usd"]) - 1.0
            )
            account["maximum_drawdown_pct"] = min(
                float(account["maximum_drawdown_pct"]), drawdown_pct
            )
            strategy_risk_status = str(strategy_risk["state"]["risk_status"])
            account["strategy_risk_status_counts"][strategy_risk_status] += 1
            position_risk = strategy_risk["positionRisk"]
            position_risk_tier = str(position_risk["effective_tier"])
            account["strategy_position_risk_tier_counts"][
                position_risk_tier
            ] += 1
            targets = [
                {
                    "contract_id": item["contract_id"],
                    "role": item["role"],
                    "signal": item["signal"],
                    "thesis_status": item["thesis_status"],
                    "thesis_action": item["thesis_action"]["action"],
                    "pre_thesis_target_lots": item[
                        "pre_thesis_target_position_lots"
                    ],
                    "thesis_adjusted_target_lots": item[
                        "thesis_adjusted_target_position_lots"
                    ],
                    "strategy_intent_target_lots": item[
                        "strategy_intent_target_position_lots"
                    ],
                    "strategy_risk_approved_target_lots": item[
                        "strategy_risk_approved_target_position_lots"
                    ],
                    "strategy_target_lots": item["strategy_target_position_lots"],
                    "approved_target_lots": item["risk_approved_target_position_lots"],
                    "company_risk_approved_target_lots": item[
                        "company_risk_approved_target_position_lots"
                    ],
                    "strategy_risk_clip_lots": item["strategy_risk_clip_lots"],
                    "strategy_position_risk_tier": item[
                        "strategy_position_risk_tier"
                    ],
                    "strategy_position_risk_gap_completion": item[
                        "strategy_position_risk_gap_completion"
                    ],
                    "risk_clip_lots": item["risk_clip_lots"],
                }
                for item in decision["targets"]
            ]
            participant_reports.append(
                {
                    "participant_id": participant_id,
                    "display_name": participant["display_name"],
                    "is_player": participant["is_player"],
                    "equity_before_usd": before_equity,
                    "equity_after_usd": after_equity,
                    "turn_pnl_usd": turn_pnl,
                    "strategy_turn_pnl_usd": strategy_turn_pnl,
                    "turn_return_pct": 100.0 * turn_pnl / before_equity,
                    "cumulative_return_pct": 100.0
                    * (after_equity / self.initial_equity_usd - 1.0),
                    "maximum_drawdown_pct": account["maximum_drawdown_pct"],
                    "positions": dict(after["positions"]),
                    "gross_position_lots": sum(
                        abs(int(value)) for value in after["positions"].values()
                    ),
                    "margin_to_equity_pct": (
                        None
                        if after["margin_to_equity_pct"] is None
                        else float(after["margin_to_equity_pct"])
                    ),
                    "cash_balance_usd": float(after["cash_balance_usd"]),
                    "restricted_initial_margin_usd": float(
                        after["restricted_initial_margin_usd"]
                    ),
                    "maintenance_margin_usd": float(
                        after["maintenance_margin_usd"]
                    ),
                    "available_funds_usd": float(after["available_funds_usd"]),
                    "excess_liquidity_usd": float(after["excess_liquidity_usd"]),
                    "account_status": str(after["status"]),
                    "margin_call_triggered": bool(
                        account_ledger["margin_call_triggered"]
                    ),
                    "margin_call_amount_usd": float(
                        account_ledger["margin_call_amount_usd"]
                    ),
                    "forced_liquidation_lots": int(
                        account_ledger["forced_liquidation_lots"]
                    ),
                    "idle_cash_interest_usd": float(
                        account_ledger["idle_cash_interest_usd"]
                    ),
                    "margin_financing_cost_usd": float(
                        account_ledger["margin_financing_cost_usd"]
                    ),
                    "forced_liquidation_cost_usd": float(
                        account_ledger["forced_liquidation_cost_usd"]
                    ),
                    "account_authorization": account_authorization[
                        "authorization"
                    ],
                    "account_ledger": account_ledger,
                    "traded_lots": int(execution["traded_lots"]),
                    "buy_lots": int(execution["buy_lots"]),
                    "sell_lots": int(execution["sell_lots"]),
                    "execution_cost_usd": float(execution["execution_cost_usd"]),
                    "pnl_attribution": dict(settlement["pnlAttribution"]),
                    "gross_pnl_before_cost_usd": float(
                        execution["gross_pnl_before_cost_usd"]
                    ),
                    "risk_status": strategy_risk_status,
                    "portfolio_risk_status": str(portfolio_risk["status"]),
                    "strategy_risk_status": strategy_risk_status,
                    "strategy_position_risk_tier": position_risk_tier,
                    "strategy_position_risk_current_utilization": (
                        position_risk["current"]["maximum_utilization"]
                    ),
                    "strategy_position_risk_proposed_utilization": (
                        position_risk["proposed"]["maximum_utilization"]
                    ),
                    "strategy_position_risk_approved_utilization": (
                        position_risk["approved"]["maximum_utilization"]
                    ),
                    "strategy_position_risk_gap_completion": position_risk[
                        "risk_increasing_gap_completion"
                    ],
                    "strategy_position_risk_current_reduce_only": position_risk[
                        "current_reduce_only"
                    ],
                    "thesis_statuses": {
                        key: value["status"]
                        for key, value in account["thesis_state"]
                        .get("contracts", {})
                        .items()
                    },
                    "capital_authorization_pct_of_company_equity": decision[
                        "riskBudget"
                    ]["capital_authorization_pct_of_company_equity"],
                    "capital_authorization_usd": decision["riskBudget"][
                        "allocated_strategy_capital_usd"
                    ],
                    "strategy_mandate_reference_capital_pct_of_company_equity": decision[
                        "riskBudget"
                    ]["strategy_mandate_reference_capital_pct_of_company_equity"],
                    "strategy_risk_clipped_gross_lots": int(
                        strategy_risk["approvalSummary"]["clipped_gross_lots"]
                    ),
                    "risk_clipped_gross_lots": int(
                        strategy_risk["approvalSummary"]["clipped_gross_lots"]
                    ),
                    "portfolio_risk_clipped_gross_lots": int(
                        portfolio_risk["approval_summary"]["clipped_gross_lots"]
                    ),
                    "targets": targets,
                }
            )
        ordered = sorted(
            participant_reports,
            key=lambda item: float(item["equity_after_usd"]),
            reverse=True,
        )
        ranks = {
            str(item["participant_id"]): rank
            for rank, item in enumerate(ordered, start=1)
        }
        for item in participant_reports:
            item["rank"] = ranks[str(item["participant_id"])]
        main_before = float(self.current_market["mainContinuous"]["price_usd"])
        main_after = float(next_market["mainContinuous"]["price_usd"])
        report = {
            "report_id": f"{year:04d}-{month:02d}-H{half}",
            "turn_number": len(self.reports) + 1,
            "from_as_of": {"year": year, "month": month, "half": half},
            "to_as_of": {
                "year": next_year,
                "month": next_month,
                "half": next_half,
            },
            "market": {
                "main_contract_id": self.current_market["curve"]["main_contract_id"],
                "main_price_before_usd": main_before,
                "main_price_after_usd": main_after,
                "main_return_pct": 100.0 * (main_after / main_before - 1.0),
                "curve_state_before": self.current_market["curve"]["state"],
                "curve_state_after": next_market["curve"]["state"],
            },
            "winner_participant_id": ordered[0]["participant_id"],
            "participants": participant_reports,
        }
        report["report_hash"] = sha256_json(report)
        self.reports.append(_round_nested(report))
        self.current_serial += 1
        self.current_market = next_market

    def _leaderboard(self) -> list[dict[str, Any]]:
        rows = []
        for participant in self.participants:
            account = self.accounts[str(participant["participant_id"])]
            formal = account["formal_account"]
            rows.append(
                {
                    "participant_id": participant["participant_id"],
                    "display_name": participant["display_name"],
                    "is_player": participant["is_player"],
                    "equity_usd": formal["equity_usd"],
                    "cash_balance_usd": formal["cash_balance_usd"],
                    "cumulative_pnl_usd": account["cumulative_pnl_usd"],
                    "cumulative_return_pct": 100.0
                    * (
                        float(formal["equity_usd"]) / self.initial_equity_usd
                        - 1.0
                    ),
                    "maximum_drawdown_pct": account["maximum_drawdown_pct"],
                    "gross_position_lots": sum(
                        abs(int(value)) for value in formal["positions"].values()
                    ),
                    "account_status": formal["status"],
                    "restricted_initial_margin_usd": (
                        self.reports[-1]["participants"][
                            next(
                                index
                                for index, item in enumerate(
                                    self.reports[-1]["participants"]
                                )
                                if item["participant_id"]
                                == participant["participant_id"]
                            )
                        ]["restricted_initial_margin_usd"]
                        if self.reports
                        else 0.0
                    ),
                    "margin_call_count": formal["margin_call_count"],
                    "forced_liquidation_count": formal[
                        "forced_liquidation_count"
                    ],
                    "cumulative_forced_liquidation_lots": formal[
                        "cumulative_forced_liquidation_lots"
                    ],
                    "cumulative_idle_cash_interest_usd": account[
                        "cumulative_idle_cash_interest_usd"
                    ],
                    "cumulative_margin_financing_cost_usd": account[
                        "cumulative_margin_financing_cost_usd"
                    ],
                    "cumulative_forced_liquidation_cost_usd": account[
                        "cumulative_forced_liquidation_cost_usd"
                    ],
                    "cumulative_traded_lots": account["cumulative_traded_lots"],
                    "cumulative_execution_cost_usd": account[
                        "cumulative_execution_cost_usd"
                    ],
                    "winning_turns": account["winning_turns"],
                    "losing_turns": account["losing_turns"],
                }
            )
        rows.sort(key=lambda item: float(item["equity_usd"]), reverse=True)
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
        return rows

    def _report_catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "report_id": report["report_id"],
                "turn_number": report["turn_number"],
                "from_as_of": dict(report["from_as_of"]),
                "to_as_of": dict(report["to_as_of"]),
                "winner_participant_id": report["winner_participant_id"],
                "report_hash": report["report_hash"],
            }
            for report in reversed(self.reports)
        ]

    def report_payload(self, report_id: str) -> dict[str, Any]:
        """Return one already-computed detailed report for lazy UI loading."""

        requested = str(report_id)
        with self.lock:
            report = next(
                (
                    item
                    for item in self.reports
                    if str(item["report_id"]) == requested
                ),
                None,
            )
            if report is None:
                raise ValueError(
                    f"investment competition report is unavailable: {requested}"
                )
            return _round_nested(
                {
                    "ok": True,
                    "schemaVersion": "asset-simulation-oil-investment-report-v1",
                    "report": report,
                }
            )

    def payload(
        self,
        *,
        as_of_year: int,
        as_of_month: int,
        as_of_half: int,
        history_limit: int | None = None,
    ) -> dict[str, Any]:
        target = _half_turn_serial(as_of_year, as_of_month, as_of_half)
        start = _half_turn_serial(*GAME_START)
        if target < start:
            raise ValueError("investment competition cutoff precedes game start")
        with self.lock:
            if target < self.current_serial:
                self._reset_runtime()
            while self.current_serial < target:
                self._advance_one()
            leaderboard = self._leaderboard()
            if history_limit is not None:
                if isinstance(history_limit, bool) or not isinstance(history_limit, int):
                    raise ValueError("competition history_limit must be an integer")
                if history_limit < 0 or history_limit > 240:
                    raise ValueError("competition history_limit must be between 0 and 240")
                visible_reports = (
                    list(reversed(self.reports[-history_limit:]))
                    if history_limit
                    else []
                )
            else:
                visible_reports = list(reversed(self.reports))
            result = {
                "ok": True,
                "schemaVersion": "asset-simulation-oil-investment-competition-v6",
                "asOf": {
                    "year": int(as_of_year),
                    "month": int(as_of_month),
                    "half": int(as_of_half),
                },
                "completed_turns": len(self.reports),
                "initial_equity_usd": self.initial_equity_usd,
                "participants": [
                    _public_participant(participant)
                    for participant in self.participants
                ],
                "leaderboard": leaderboard,
                "latest_report": self.reports[-1] if self.reports else None,
                "report_history": visible_reports,
                "governance": {
                    "selection_method": "seeded_random_draw_once_per_world",
                    "player_and_ai_use_same_runtime": True,
                    "same_market_path": True,
                    "market_write_back": False,
                    "committee_can_override_profiles": False,
                    "demo_status": "random_appointments_read_only",
                    "formal_futures_accounts": True,
                    "account_external_capital_flows_enabled": False,
                },
            }
            if history_limit is not None:
                result["report_catalog"] = self._report_catalog()
                result["report_history_limit"] = history_limit
                result["report_history_complete"] = (
                    len(visible_reports) == len(self.reports)
                )
            identity = {
                "schema_version": "asset-simulation-oil-investment-competition-identity-v6",
                "model_version": OIL_INVESTMENT_COMPETITION_MODEL_VERSION,
                "seed": self.seed,
                "upstream_global_identity_hash": self.run.identity["identity_hash"],
                "participant_roster_hash": sha256_json(
                    [participant["roster_hash"] for participant in self.participants]
                ),
                "write_back": False,
                "result_hash": sha256_json(result),
            }
            return _round_nested({"identity": identity, **result})
