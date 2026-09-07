"""Stage6C previews: freight trials, cargo formation and bilateral firmness."""
from ..shipping_v3.types import BallastOrder
from .types import BoardSpec, BoardState, BoardSnapshot, ImportRequirement, InventoryBand, TrialResult, make_board_spec
from .board import initial_board, open_board, quote_trial
from .pricing import recompute_trial_price
from .session import BoardSession
from .cargo_formation import (
    CargoFormationSpec, CargoFormationResult, make_cargo_formation_spec, form_cargo_plan,
)
from .bilateral_formation import (
    BilateralFormationSpec, BilateralFormationResult, make_bilateral_formation_spec,
    make_preview3_board_spec, form_seller_offers, form_bilateral_cargo_plan,
    structured_hard_violations,
)
from .bilateral_session import BilateralSession, FirmCommitment
from .main_bridge import build_main_linked_preview3_inputs
__all__ = ['BallastOrder', 'BoardSpec', 'BoardState', 'BoardSnapshot', 'ImportRequirement',
           'InventoryBand', 'TrialResult', 'make_board_spec', 'initial_board', 'open_board',
           'quote_trial', 'recompute_trial_price', 'BoardSession',
           'CargoFormationSpec', 'CargoFormationResult', 'make_cargo_formation_spec', 'form_cargo_plan',
           'BilateralFormationSpec', 'BilateralFormationResult', 'make_bilateral_formation_spec',
           'make_preview3_board_spec', 'form_seller_offers', 'form_bilateral_cargo_plan',
           'structured_hard_violations', 'BilateralSession', 'FirmCommitment',
           'build_main_linked_preview3_inputs']
