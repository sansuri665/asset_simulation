"""Standard-library JSON service for macro and crude-shipping demand."""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
from collections import OrderedDict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .model.engine import MODEL_VERSION, GlobalMacroRun, run_global_macro
from .model.oil_shipping_world import (
    OIL_SHIPPING_DEMAND_MODEL_VERSION,
    build_oil_shipping_payload,
    run_oil_shipping_world,
)
from .model.oil_price_projection import (
    OIL_PRICE_PROJECTION_MODEL_VERSION,
    build_oil_price_payload,
    run_oil_price_projection,
)
from .model.registry import clear_registered_assets_cache, load_registered_assets
from .model.decision_view import build_decision_snapshot


SERVICE_ID = "asset-simulation-macro-oil-ui-v0.7"
VIEWER_ROOT = Path(__file__).resolve().parent / "viewer"
MAX_CACHE_ENTRIES = 32
_RUN_CACHE: OrderedDict[tuple[int, int, str, str], GlobalMacroRun] = OrderedDict()
_CACHE_LOCK = threading.Lock()


def get_cached_run(
    seed: int,
    years: int,
    diagnostics_level: str = "minimal",
) -> GlobalMacroRun:
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
    run_oil_shipping_world.cache_clear()
    run_oil_price_projection.cache_clear()
    clear_registered_assets_cache()


def cache_info() -> dict[str, int]:
    with _CACHE_LOCK:
        return {
            "entries": len(_RUN_CACHE),
            "maximumEntries": MAX_CACHE_ENTRIES,
        }


def build_run_payload(run: GlobalMacroRun) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "schemaVersion": "asset-simulation-global-run-response-v1",
        "scope": "research_full_path_not_for_agent_decisions",
        "identity": run.identity,
        "summary": run.summary,
        "globalMacroSnapshots": run.snapshots,
        "nextYearInputs": run.next_year_inputs,
    }
    if run.diagnostics_level == "full":
        payload["diagnostics"] = run.diagnostics
    return payload


def _single_query(query: dict[str, list[str]], key: str, default: str) -> str:
    values = query.get(key)
    if not values:
        return default
    if len(values) != 1:
        raise ValueError(f"{key} must appear once")
    return values[0]


class AssetSimulationHandler(BaseHTTPRequestHandler):
    server_version = "AssetSimulation/0.2"

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _viewer_file(self, relative_path: str) -> None:
        root = VIEWER_ROOT.resolve()
        target = (root / relative_path).resolve()
        if root not in target.parents and target != root:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = target.read_bytes()
        content_type, _ = mimetypes.guess_type(target.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/", "/index.html"}:
                self._viewer_file("overview.html")
                return
            if parsed.path in {"/physical", "/physical.html"}:
                self._viewer_file("physical.html")
                return
            if parsed.path in {"/shipping", "/shipping.html"}:
                self._viewer_file("index.html")
                return
            if parsed.path.startswith("/static/"):
                self._viewer_file(parsed.path.removeprefix("/static/"))
                return
            if parsed.path == "/api":
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "serviceId": SERVICE_ID,
                        "status": "stage_4_crude_physical_route_network",
                        "viewerAvailable": True,
                        "endpoints": [
                            "/api/health",
                            "/api/decision?seed=42&years=60&year=2030&month=1",
                            "/api/global?seed=42&years=60",
                            "/api/oil-price?seed=42&years=60",
                            "/api/oil-shipping?seed=42&years=60&year=2030&month=1",
                        ],
                    },
                )
                return
            if parsed.path == "/api/health":
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "serviceId": SERVICE_ID,
                        "modelVersion": MODEL_VERSION,
                        "oilShippingDemandModelVersion": OIL_SHIPPING_DEMAND_MODEL_VERSION,
                        "oilPriceProjectionModelVersion": OIL_PRICE_PROJECTION_MODEL_VERSION,
                        "scope": "global_physical_pool_with_regional_balances_and_route_network",
                        "explicitRouteCount": len(load_registered_assets()["oil_shipping_demand_config"]["route_network"]["explicit_routes"]),
                        "regionalBalanceCount": 10,
                        "cargoGeneration": "regional_physical_surplus_and_deficit",
                        "scenarioExposure": "test_only_not_exposed_by_service_or_viewer",
                        "freightRateAvailable": False,
                        "cache": cache_info(),
                    },
                )
                return
            if parsed.path == "/api/decision":
                query = parse_qs(parsed.query, keep_blank_values=True)
                seed = int(_single_query(query, "seed", "42"))
                years = int(_single_query(query, "years", "60"))
                year = int(_single_query(query, "year", "2030"))
                month = int(_single_query(query, "month", "1"))
                run = get_cached_run(seed, years)
                self._json(HTTPStatus.OK, build_decision_snapshot(
                    run, run_oil_shipping_world(run), run_oil_price_projection(run),
                    as_of_year=year, as_of_month=month,
                ))
                return
            if parsed.path == "/api/global":
                query = parse_qs(parsed.query, keep_blank_values=True)
                seed = int(_single_query(query, "seed", "42"))
                years = int(_single_query(query, "years", "60"))
                diagnostics = _single_query(query, "diagnostics", "minimal")
                self._json(
                    HTTPStatus.OK,
                    build_run_payload(get_cached_run(seed, years, diagnostics)),
                )
                return
            if parsed.path == "/api/oil-price":
                query = parse_qs(parsed.query, keep_blank_values=True)
                seed = int(_single_query(query, "seed", "42"))
                years = int(_single_query(query, "years", "60"))
                run = get_cached_run(seed, years)
                self._json(
                    HTTPStatus.OK,
                    build_oil_price_payload(run_oil_price_projection(run)),
                )
                return
            if parsed.path == "/api/oil-shipping":
                query = parse_qs(parsed.query, keep_blank_values=True)
                seed = int(_single_query(query, "seed", "42"))
                years = int(_single_query(query, "years", "60"))
                as_of_year = int(_single_query(query, "year", "2030"))
                as_of_month = int(_single_query(query, "month", "1"))
                run = get_cached_run(seed, years)
                world = run_oil_shipping_world(run)
                self._json(
                    HTTPStatus.OK,
                    build_oil_shipping_payload(
                        world,
                        as_of_year=as_of_year,
                        as_of_month=as_of_month,
                    ),
                )
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except (KeyError, TypeError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Exception as exc:  # keep the local development service inspectable
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": str(exc)},
            )

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve the Asset Simulation macro/oil, physical-balance, shipping, and JSON views."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8783)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), AssetSimulationHandler)
    print(f"Asset Simulation macro/oil UI: http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
