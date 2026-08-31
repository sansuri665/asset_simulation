"""Compact, dependency-free global macro model."""

from .engine import MODEL_VERSION, GlobalMacroRun, run_global_macro
from .oil_trading_strategy import (
    OIL_TRADING_STRATEGY_MODEL_VERSION,
    simulate_oil_trading_strategy,
)
from .oil_futures_account import (
    OIL_FUTURES_ACCOUNT_MODEL_VERSION,
    apply_oil_futures_account_constraints,
    create_oil_futures_account,
    oil_futures_account_snapshot,
    settle_oil_futures_account_turn,
)
from .oil_strategy_research import (
    OIL_STRATEGY_RESEARCH_MODEL_VERSION,
    generate_oil_strategy_research_roster,
)
from .oil_calendar_spread_strategy import (
    OIL_CALENDAR_SPREAD_STRATEGY_MODEL_VERSION,
    attribute_oil_calendar_spread_pnl,
    build_oil_calendar_spread_execution_report,
    build_oil_calendar_spread_research_decision,
    evaluate_oil_calendar_spread_thesis_state,
)
from .oil_multi_strategy_gate_b import (
    OIL_MULTI_STRATEGY_GATE_B_MODEL_VERSION,
    allocate_gate_b_strategy_orders,
    amend_strategy_capital_authorization_state,
    build_gate_b_market_limits_from_oil_futures_payload,
    create_strategy_capital_authorization_state,
    evaluate_strategy_capital_authorization_status,
    load_oil_multi_strategy_gate_b_assets,
)
from .oil_execution_desk import (
    OIL_EXECUTION_DESK_MODEL_VERSION,
    generate_oil_execution_desk_roster,
)

__all__ = [
    "MODEL_VERSION",
    "GlobalMacroRun",
    "run_global_macro",
    "OIL_TRADING_STRATEGY_MODEL_VERSION",
    "simulate_oil_trading_strategy",
    "OIL_FUTURES_ACCOUNT_MODEL_VERSION",
    "apply_oil_futures_account_constraints",
    "create_oil_futures_account",
    "oil_futures_account_snapshot",
    "settle_oil_futures_account_turn",
    "OIL_STRATEGY_RESEARCH_MODEL_VERSION",
    "generate_oil_strategy_research_roster",
    "OIL_CALENDAR_SPREAD_STRATEGY_MODEL_VERSION",
    "build_oil_calendar_spread_research_decision",
    "build_oil_calendar_spread_execution_report",
    "attribute_oil_calendar_spread_pnl",
    "evaluate_oil_calendar_spread_thesis_state",
    "OIL_MULTI_STRATEGY_GATE_B_MODEL_VERSION",
    "create_strategy_capital_authorization_state",
    "amend_strategy_capital_authorization_state",
    "evaluate_strategy_capital_authorization_status",
    "build_gate_b_market_limits_from_oil_futures_payload",
    "allocate_gate_b_strategy_orders",
    "load_oil_multi_strategy_gate_b_assets",
    "OIL_EXECUTION_DESK_MODEL_VERSION",
    "generate_oil_execution_desk_roster",
]
