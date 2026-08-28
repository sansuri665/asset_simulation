from __future__ import annotations

from copy import deepcopy
import unittest

from asset_simulation.model.institution_organization import (
    initial_proprietary_capital_usd,
    resolve_institution_organization,
    validate_strategy_capital_reference,
)
from asset_simulation.model.registry import load_registered_assets


class InstitutionOrganizationTests(unittest.TestCase):
    def test_current_institution_is_ten_million_dollar_prop_firm(self) -> None:
        assets = load_registered_assets()
        config = assets["institution_organization_config"]
        contract = assets["institution_organization_contract"]

        self.assertEqual("proprietary_trading_firm", config["institution_type"])
        self.assertEqual(
            10_000_000.0,
            config["capital_base"]["initial_proprietary_capital_usd"],
        )
        self.assertFalse(config["capital_base"]["external_aum_enabled"])
        self.assertFalse(
            config["capital_base"]["fund_management_company_split_enabled"]
        )
        self.assertEqual("institution_organization_v1", contract["contract_id"])
        self.assertEqual(
            "institution_organization_v1",
            config["capital_base"]["runtime_capital_owner"],
        )
        self.assertFalse(
            config["capital_base"][
                "market_capacity_binding_expected_at_initial_scale"
            ]
        )
        self.assertEqual(10_000_000.0, initial_proprietary_capital_usd())

    def test_strategy_compatibility_capital_cannot_drift_from_owner(self) -> None:
        assets = load_registered_assets()
        strategy = assets["oil_trading_strategy_config"]
        self.assertEqual(
            initial_proprietary_capital_usd(assets),
            validate_strategy_capital_reference(strategy, assets=assets),
        )
        drifted = deepcopy(strategy)
        drifted["initial_reference_equity_usd"] += 1.0
        with self.assertRaises(ValueError):
            validate_strategy_capital_reference(drifted, assets=assets)

    def test_registered_organization_assets_validate_as_one_contract(self) -> None:
        config, contract = resolve_institution_organization()
        self.assertEqual(
            contract["department_ids"],
            [item["department_id"] for item in config["departments"]],
        )

    def test_investment_decision_is_governance_shell_not_department(self) -> None:
        config = load_registered_assets()["institution_organization_config"]
        decision = config["governance_layers"]["investment_decision"]

        self.assertEqual("governance_not_department", decision["layer_type"])
        self.assertEqual(
            100.0,
            decision["single_strategy_default_capital_authorization_pct"],
        )
        self.assertEqual(
            {
                "risk_policy_approval",
                "strategy_capital_authorization",
            },
            set(decision["current_scope"]),
        )
        self.assertFalse(decision["member_roster_enabled"])
        self.assertFalse(decision["voting_enabled"])
        self.assertFalse(decision["personnel_capability_model_enabled"])
        self.assertFalse(decision["multi_strategy_allocation_enabled"])

    def test_current_runtime_proxy_is_full_single_strategy_authorization(self) -> None:
        assets = load_registered_assets()
        proxy = assets["oil_strategy_risk_config"]["committee_proxy"]
        self.assertEqual(
            100.0,
            proxy["default_capital_authorization_pct_of_company_equity"],
        )
        self.assertEqual(
            "single_strategy_full_allocation_proxy",
            proxy["capital_authorization_method"],
        )

    def test_administration_is_shell_only(self) -> None:
        config = load_registered_assets()["institution_organization_config"]
        departments = {
            item["department_id"]: item
            for item in config["departments"]
        }
        self.assertEqual(
            {
                "forecast_research",
                "investment_strategy",
                "corporate_risk",
                "trading_execution",
                "administration",
            },
            set(departments),
        )
        admin = departments["administration"]
        self.assertEqual("shell_only", admin["status"])
        self.assertFalse(admin["runtime_logic_enabled"])
        self.assertFalse(admin["personnel_model_enabled"])
        self.assertFalse(admin["cost_model_enabled"])
        self.assertFalse(admin["payroll_enabled"])
        self.assertFalse(admin["recruiting_system_enabled"])


if __name__ == "__main__":
    unittest.main()
