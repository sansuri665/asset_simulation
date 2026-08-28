"""Asset Simulation: compact global macro environment for capital markets."""

from .model.engine import MODEL_VERSION, run_global_macro, run_global_macro_with_impulses

__all__ = [
    "MODEL_VERSION",
    "run_global_macro",
    "run_global_macro_with_impulses",
]
