"""Continuous half-month oil forecast vintage sessions.

The standalone forecast API previously rebuilt only the immediately preceding
vintage, while the investment competition carried the entire revision chain.
This owner makes a direct request to a later cutoff replay the same continuous
sequence of vintages from game start.  Sessions are cached by the service, so
ordinary forward requests advance incrementally.
"""

from __future__ import annotations

import threading
from typing import Any, Mapping

from .engine import GlobalMacroRun
from .oil_futures_overlay import oil_futures_payload
from .oil_short_term_forecast import (
    OIL_SHORT_TERM_FORECAST_MODEL_VERSION,
    generate_oil_short_term_forecast,
    resolve_oil_short_term_institution_profile,
)


GAME_START = (2030, 1, 1)


def _month_serial(year: int, month: int) -> int:
    return int(year) * 12 + int(month) - 1


def _half_turn_serial(year: int, month: int, half: int) -> int:
    return _month_serial(year, month) * 2 + int(half) - 1


def _turn_from_serial(serial: int) -> tuple[int, int, int]:
    month_serial, half_index = divmod(int(serial), 2)
    return month_serial // 12, month_serial % 12 + 1, half_index + 1


class OilShortTermForecastSession:
    """Incrementally replay one institution's immutable forecast vintage chain."""

    def __init__(
        self,
        run: GlobalMacroRun,
        institution_profile: Mapping[str, Any] | None = None,
    ):
        self.run = run
        self.profile = resolve_oil_short_term_institution_profile(
            institution_profile
        )
        self.lock = threading.RLock()
        self.start_serial = _half_turn_serial(*GAME_START)
        self.current_serial = self.start_serial - 1
        self.current_vintage: dict[str, Any] | None = None

    @property
    def profile_hash(self) -> str:
        return str(self.profile["profile_hash"])

    def _reset(self) -> None:
        self.current_serial = self.start_serial - 1
        self.current_vintage = None

    def payload(
        self,
        *,
        as_of_year: int,
        as_of_month: int,
        as_of_half: int,
    ) -> dict[str, Any]:
        target = _half_turn_serial(as_of_year, as_of_month, as_of_half)
        if target < self.start_serial:
            raise ValueError("oil forecast cutoff precedes game start")
        with self.lock:
            if target < self.current_serial:
                self._reset()
            while self.current_serial < target:
                next_serial = self.current_serial + 1
                year, month, half = _turn_from_serial(next_serial)
                market = oil_futures_payload(
                    self.run,
                    as_of_year=year,
                    as_of_month=month,
                    as_of_half=half,
                )
                self.current_vintage = generate_oil_short_term_forecast(
                    self.run,
                    as_of_year=year,
                    as_of_month=month,
                    as_of_half=half,
                    institution_profile=self.profile,
                    previous_vintage=self.current_vintage,
                    market=market,
                )
                self.current_serial = next_serial
            if self.current_vintage is None:
                raise ValueError("oil forecast session failed to build a vintage")
            return self.current_vintage

    def info(self) -> dict[str, Any]:
        with self.lock:
            return {
                "model_version": OIL_SHORT_TERM_FORECAST_MODEL_VERSION,
                "upstream_global_identity_hash": self.run.identity[
                    "identity_hash"
                ],
                "institution_profile_hash": self.profile_hash,
                "current_serial": self.current_serial,
                "has_vintage": self.current_vintage is not None,
            }
