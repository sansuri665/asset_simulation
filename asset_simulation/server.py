"""Standard-library HTTP service for the compact global macro Viewer."""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import webbrowser
from collections import OrderedDict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .model.commodity_overlay import (
    COMMODITY_MODEL_VERSION,
    commodities_payload,
    run_commodity_overlay,
)
from .model.engine import MODEL_VERSION, GlobalMacroRun, run_global_macro
from .model.oil_futures_overlay import OIL_FUTURES_MODEL_VERSION, oil_futures_payload
from .model.oil_futures_world import get_oil_futures_world
from .model.oil_short_term_forecast import (
    OIL_SHORT_TERM_FORECAST_MODEL_VERSION,
    generate_institution_profile_for_score_range,
    resolve_oil_short_term_institution_profile,
)
from .model.oil_short_term_forecast_session import OilShortTermForecastSession
from .model.oil_strategy_research import (
    OIL_STRATEGY_RESEARCH_MODEL_VERSION,
    generate_oil_strategy_research_roster,
)
from .model.oil_execution_desk import (
    OIL_EXECUTION_DESK_MODEL_VERSION,
    generate_oil_execution_desk_roster,
)
from .model.oil_strategy_risk import OIL_STRATEGY_RISK_MODEL_VERSION
from .model.oil_trading_strategy import OIL_TRADING_STRATEGY_MODEL_VERSION
from .model.oil_futures_account import OIL_FUTURES_ACCOUNT_MODEL_VERSION
from .model.oil_investment_competition import (
    OIL_INVESTMENT_COMPETITION_MODEL_VERSION,
    OilInvestmentCompetitionSession,
)
from .model.registry import clear_registered_assets_cache


SERVICE_ID = "asset-simulation-macro-ui-v5.42"
VIEWER_ROOT = Path(__file__).resolve().parent / "viewer"
MAX_CACHE_ENTRIES = 64
MAX_COMPETITION_CACHE_ENTRIES = 8
MAX_FORECAST_SESSION_CACHE_ENTRIES = 16
GAME_START_CUTOFF = (2030, 1, 1)
_RUN_CACHE: OrderedDict[tuple[int, int, str, str], GlobalMacroRun] = OrderedDict()
_COMPETITION_CACHE: OrderedDict[
    tuple[int, int, str, str], OilInvestmentCompetitionSession
] = OrderedDict()
_FORECAST_SESSION_CACHE: OrderedDict[
    tuple[str, str, str], OilShortTermForecastSession
] = OrderedDict()
_CACHE_LOCK = threading.Lock()


def get_cached_run(seed: int, years: int, diagnostics_level: str = "minimal") -> GlobalMacroRun:
    key = (seed, years, diagnostics_level, MODEL_VERSION)
    with _CACHE_LOCK:
        cached = _RUN_CACHE.get(key)
        if cached is not None:
            _RUN_CACHE.move_to_end(key)
            return cached
    run = run_global_macro(seed, years, diagnostics_level=diagnostics_level)
    with _CACHE_LOCK:
        _RUN_CACHE[key] = run
        _RUN_CACHE.move_to_end(key)
        while len(_RUN_CACHE) > MAX_CACHE_ENTRIES:
            _RUN_CACHE.popitem(last=False)
    return run


def clear_cache() -> None:
    with _CACHE_LOCK:
        _RUN_CACHE.clear()
        _COMPETITION_CACHE.clear()
        _FORECAST_SESSION_CACHE.clear()
    run_commodity_overlay.cache_clear()
    oil_futures_payload.cache_clear()
    get_oil_futures_world.cache_clear()
    clear_registered_assets_cache()


def cache_info() -> dict[str, int]:
    with _CACHE_LOCK:
        return {
            "entries": len(_RUN_CACHE),
            "maximumEntries": MAX_CACHE_ENTRIES,
        }


def get_oil_investment_competition_session(
    seed: int, years: int = 60
) -> OilInvestmentCompetitionSession:
    run = get_cached_run(seed, years)
    key = (
        int(seed),
        int(years),
        str(run.identity["identity_hash"]),
        OIL_INVESTMENT_COMPETITION_MODEL_VERSION,
    )
    with _CACHE_LOCK:
        cached = _COMPETITION_CACHE.get(key)
        if cached is not None:
            _COMPETITION_CACHE.move_to_end(key)
            return cached
    session = OilInvestmentCompetitionSession(run)
    with _CACHE_LOCK:
        existing = _COMPETITION_CACHE.get(key)
        if existing is not None:
            _COMPETITION_CACHE.move_to_end(key)
            return existing
        _COMPETITION_CACHE[key] = session
        while len(_COMPETITION_CACHE) > MAX_COMPETITION_CACHE_ENTRIES:
            _COMPETITION_CACHE.popitem(last=False)
    return session


def get_oil_short_term_forecast_session(
    run: GlobalMacroRun,
    institution_profile: dict[str, Any] | None = None,
) -> OilShortTermForecastSession:
    profile = resolve_oil_short_term_institution_profile(institution_profile)
    key = (
        str(run.identity["identity_hash"]),
        str(profile["profile_hash"]),
        OIL_SHORT_TERM_FORECAST_MODEL_VERSION,
    )
    with _CACHE_LOCK:
        cached = _FORECAST_SESSION_CACHE.get(key)
        if cached is not None:
            _FORECAST_SESSION_CACHE.move_to_end(key)
            return cached
    session = OilShortTermForecastSession(run, profile)
    with _CACHE_LOCK:
        existing = _FORECAST_SESSION_CACHE.get(key)
        if existing is not None:
            _FORECAST_SESSION_CACHE.move_to_end(key)
            return existing
        _FORECAST_SESSION_CACHE[key] = session
        _FORECAST_SESSION_CACHE.move_to_end(key)
        while len(_FORECAST_SESSION_CACHE) > MAX_FORECAST_SESSION_CACHE_ENTRIES:
            _FORECAST_SESSION_CACHE.popitem(last=False)
    return session


def build_oil_investment_competition_payload(
    *,
    seed: int,
    years: int,
    as_of_year: int,
    as_of_month: int,
    as_of_half: int,
    history_limit: int | None = None,
) -> dict[str, Any]:
    session = get_oil_investment_competition_session(seed, years)
    return session.payload(
        as_of_year=as_of_year,
        as_of_month=as_of_month,
        as_of_half=as_of_half,
        history_limit=history_limit,
    )


def build_run_payload(run: GlobalMacroRun, *, include_support: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "schemaVersion": "asset-simulation-global-run-response-v1",
        "identity": run.identity,
        "summary": run.summary,
        "globalMacroSnapshots": run.snapshots,
        "nextYearInputs": run.next_year_inputs,
    }
    if include_support:
        support_fields = (
            "seed", "year_index", "year",
            "cpi_price_level_index_2025_100",
            "gdp_deflator_price_level_index_2025_100",
            "global_nominal_gdp_trillion_usd",
            "ordinary_cycle_index", "ordinary_cycle_momentum_index", "ordinary_cycle_phase",
            "global_oil_demand_index", "global_oil_supply_index",
            "global_oil_inventory_tightness_index", "global_real_oil_price_index",
            "global_real_broad_commodity_index", "broad_commodity_index",
            "global_corporate_earnings_reference_index",
            "global_corporate_profit_share_index",
            "global_equity_capitalization_reference_index",
            "global_equity_real_capitalization_reference_index",
            "global_equity_total_return_reference_index",
            "global_equity_real_total_return_reference_index",
            "global_sovereign_bond_wealth_reference_index",
            "global_sovereign_bond_real_wealth_reference_index",
        )
        payload["viewerSupportRows"] = tuple(
            {field: row[field] for field in support_fields} for row in run.rows
        )
    if run.diagnostics_level == "full":
        payload["diagnostics"] = run.diagnostics
    payload["commodities"] = commodities_payload(run)
    return payload


def _single_query(query: dict[str, list[str]], key: str, default: str) -> str:
    values = query.get(key)
    if not values:
        return default
    if len(values) != 1:
        raise ValueError(f"{key} must appear once")
    return values[0]


def _previous_half_month(
    year: int,
    month: int,
    half: int,
) -> tuple[int, int, int] | None:
    current = (int(year), int(month), int(half))
    if not 1 <= int(month) <= 12 or int(half) not in {1, 2}:
        raise ValueError("oil short-term forecast cutoff requires month 1..12 and half 1 or 2")
    if current < GAME_START_CUTOFF:
        raise ValueError("oil short-term forecast cutoff must be at or after 2030-01-H1")
    if current == GAME_START_CUTOFF:
        return None
    if half == 2:
        return year, month, 1
    if month > 1:
        return year, month - 1, 2
    return year - 1, 12, 2


def build_oil_short_term_forecast_payload(
    run: GlobalMacroRun,
    *,
    as_of_year: int,
    as_of_month: int,
    as_of_half: int,
    institution_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the same continuous vintage chain used by the competition runtime."""

    session = get_oil_short_term_forecast_session(run, institution_profile)
    return session.payload(
        as_of_year=as_of_year,
        as_of_month=as_of_month,
        as_of_half=as_of_half,
    )


def build_oil_short_term_profile_payload(
    *,
    seed: int,
    score_min: float,
    score_max: float,
) -> dict[str, Any]:
    profile = generate_institution_profile_for_score_range(
        seed=seed,
        score_min=score_min,
        score_max=score_max,
    )
    return {
        "ok": True,
        "schemaVersion": "asset-simulation-oil-short-term-profile-response-v1",
        "seed": int(seed),
        "requestedScoreRange": {
            "minimum": float(score_min),
            "maximum": float(score_max),
        },
        "institution": profile,
    }


def build_oil_strategy_research_roster_payload(
    *,
    seed: int,
    candidate_count: int | None = None,
) -> dict[str, Any]:
    """Publish appointable personnel without accepting free-form radar values."""

    return generate_oil_strategy_research_roster(
        seed=seed,
        candidate_count=candidate_count,
    )


def build_oil_execution_desk_roster_payload(
    *,
    seed: int,
    candidate_count: int | None = None,
) -> dict[str, Any]:
    """Publish appointable execution personnel with continuous scored abilities."""

    return generate_oil_execution_desk_roster(
        seed=seed,
        candidate_count=candidate_count,
    )


class AssetSimulationHandler(BaseHTTPRequestHandler):
    server_version = "AssetSimulation/0.1"

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, relative: str) -> None:
        candidate = (VIEWER_ROOT / relative).resolve()
        if VIEWER_ROOT.resolve() not in candidate.parents and candidate != VIEWER_ROOT.resolve():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = candidate.read_bytes()
        content_type, _ = mimetypes.guess_type(candidate.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", (content_type or "application/octet-stream") + ("; charset=utf-8" if candidate.suffix in {".html", ".css", ".js"} else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/health":
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "serviceId": SERVICE_ID,
                        "modelVersion": MODEL_VERSION,
                        "commodityOverlayModelVersion": COMMODITY_MODEL_VERSION,
                        "oilFuturesOverlayModelVersion": OIL_FUTURES_MODEL_VERSION,
                        "oilShortTermForecastModelVersion": OIL_SHORT_TERM_FORECAST_MODEL_VERSION,
                        "oilStrategyResearchModelVersion": OIL_STRATEGY_RESEARCH_MODEL_VERSION,
                        "oilExecutionDeskModelVersion": OIL_EXECUTION_DESK_MODEL_VERSION,
                        "oilStrategyRiskModelVersion": OIL_STRATEGY_RISK_MODEL_VERSION,
                        "oilTradingStrategyModelVersion": OIL_TRADING_STRATEGY_MODEL_VERSION,
                        "oilFuturesAccountModelVersion": OIL_FUTURES_ACCOUNT_MODEL_VERSION,
                        "oilInvestmentCompetitionModelVersion": OIL_INVESTMENT_COMPETITION_MODEL_VERSION,
                        "scope": "global",
                        "cache": cache_info(),
                    },
                )
                return
            if parsed.path == "/api/oil-investment-competition":
                query = parse_qs(parsed.query, keep_blank_values=True)
                seed = int(_single_query(query, "seed", "42"))
                years = int(_single_query(query, "years", "60"))
                as_of_year = int(_single_query(query, "year", "2030"))
                as_of_month = int(_single_query(query, "month", "1"))
                as_of_half = int(_single_query(query, "half", "1"))
                history_limit = (
                    int(_single_query(query, "historyLimit", "12"))
                    if "historyLimit" in query
                    else None
                )
                self._json(
                    HTTPStatus.OK,
                    build_oil_investment_competition_payload(
                        seed=seed,
                        years=years,
                        as_of_year=as_of_year,
                        as_of_month=as_of_month,
                        as_of_half=as_of_half,
                        history_limit=history_limit,
                    ),
                )
                return
            if parsed.path == "/api/oil-investment-report":
                query = parse_qs(parsed.query, keep_blank_values=True)
                seed = int(_single_query(query, "seed", "42"))
                years = int(_single_query(query, "years", "60"))
                report_id = _single_query(query, "reportId", "")
                if not report_id:
                    raise ValueError("reportId is required")
                session = get_oil_investment_competition_session(seed, years)
                self._json(HTTPStatus.OK, session.report_payload(report_id))
                return
            if parsed.path == "/api/oil-short-term-profile":
                query = parse_qs(parsed.query, keep_blank_values=True)
                seed = int(_single_query(query, "seed", "42"))
                score_min = float(_single_query(query, "scoreMin", "65"))
                score_max = float(_single_query(query, "scoreMax", "75"))
                self._json(
                    HTTPStatus.OK,
                    build_oil_short_term_profile_payload(
                        seed=seed,
                        score_min=score_min,
                        score_max=score_max,
                    ),
                )
                return
            if parsed.path == "/api/oil-strategy-research-roster":
                query = parse_qs(parsed.query, keep_blank_values=True)
                seed = int(_single_query(query, "seed", "42"))
                candidate_count = int(_single_query(query, "count", "5"))
                self._json(
                    HTTPStatus.OK,
                    build_oil_strategy_research_roster_payload(
                        seed=seed,
                        candidate_count=candidate_count,
                    ),
                )
                return
            if parsed.path == "/api/oil-execution-desk-roster":
                query = parse_qs(parsed.query, keep_blank_values=True)
                seed = int(_single_query(query, "seed", "42"))
                candidate_count = int(_single_query(query, "count", "5"))
                self._json(
                    HTTPStatus.OK,
                    build_oil_execution_desk_roster_payload(
                        seed=seed,
                        candidate_count=candidate_count,
                    ),
                )
                return
            if parsed.path == "/api/oil-short-term-forecast":
                query = parse_qs(parsed.query, keep_blank_values=True)
                seed = int(_single_query(query, "seed", "42"))
                years = int(_single_query(query, "years", "60"))
                as_of_year = int(_single_query(query, "year", "2030"))
                as_of_month = int(_single_query(query, "month", "1"))
                as_of_half = int(_single_query(query, "half", "1"))
                has_score_min = "scoreMin" in query
                has_score_max = "scoreMax" in query
                if has_score_min != has_score_max:
                    raise ValueError("scoreMin and scoreMax must be supplied together")
                institution_profile = None
                if has_score_min:
                    institution_profile = generate_institution_profile_for_score_range(
                        seed=seed,
                        score_min=float(_single_query(query, "scoreMin", "65")),
                        score_max=float(_single_query(query, "scoreMax", "75")),
                    )
                run = get_cached_run(seed, years)
                self._json(
                    HTTPStatus.OK,
                    build_oil_short_term_forecast_payload(
                        run,
                        as_of_year=as_of_year,
                        as_of_month=as_of_month,
                        as_of_half=as_of_half,
                        institution_profile=institution_profile,
                    ),
                )
                return
            if parsed.path == "/api/global":
                query = parse_qs(parsed.query, keep_blank_values=True)
                seed = int(_single_query(query, "seed", "42"))
                years = int(_single_query(query, "years", "60"))
                diagnostics = _single_query(query, "diagnostics", "minimal")
                include_support = _single_query(query, "support", "1") != "0"
                run = get_cached_run(seed, years, diagnostics)
                self._json(HTTPStatus.OK, build_run_payload(run, include_support=include_support))
                return
            if parsed.path == "/api/oil-futures":
                query = parse_qs(parsed.query, keep_blank_values=True)
                seed = int(_single_query(query, "seed", "42"))
                years = int(_single_query(query, "years", "60"))
                as_of_year = int(_single_query(query, "year", "2030"))
                as_of_month = int(_single_query(query, "month", "1"))
                as_of_half = int(_single_query(query, "half", "2"))
                run = get_cached_run(seed, years)
                self._json(
                    HTTPStatus.OK,
                    oil_futures_payload(
                        run,
                        as_of_year=as_of_year,
                        as_of_month=as_of_month,
                        as_of_half=as_of_half,
                    ),
                )
                return
            if parsed.path in {"/", "/index.html"}:
                self._static("index.html")
                return
            if parsed.path in {"/game", "/game.html"}:
                self._static("game.html")
                return
            if parsed.path.startswith("/static/"):
                self._static(parsed.path.removeprefix("/"))
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except (TypeError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Exception as exc:  # keep the local service inspectable
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the compact Asset Simulation macro Viewer.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8783)
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--open-path", default="/?seed=42&years=60")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), AssetSimulationHandler)
    url = f"http://{args.host}:{args.port}{args.open_path}"
    print(f"Asset Simulation macro Viewer: {url}")
    if args.open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
