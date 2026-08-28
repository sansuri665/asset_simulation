"""Shared read-only fixtures for deterministic model tests.

Tests should use these fixtures when they only need a stable world.  Tests that
specifically verify production cache construction/identity may still call the
production owner directly.
"""

from __future__ import annotations

from functools import lru_cache

from asset_simulation.model.engine import GlobalMacroRun, run_global_macro


@lru_cache(maxsize=32)
def cached_global_run(
    seed: int,
    years: int,
    diagnostics_level: str = "minimal",
) -> GlobalMacroRun:
    """Build one immutable deterministic macro run per test-process key."""

    return run_global_macro(
        int(seed),
        int(years),
        diagnostics_level=str(diagnostics_level),
    )
