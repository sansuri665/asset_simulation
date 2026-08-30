from __future__ import annotations

import json
import unittest

from asset_simulation.model.commodity_overlay import COMMODITY_MODEL_VERSION, CONTRACT_ORDER
from asset_simulation.model.oil_futures_overlay import (
    OIL_FUTURES_MODEL_VERSION,
    oil_futures_payload,
)
from asset_simulation.server import (
    SERVICE_ID,
    build_oil_execution_desk_roster_payload,
    build_oil_investment_competition_payload,
    build_oil_short_term_profile_payload,
    build_oil_strategy_research_roster_payload,
    build_run_payload,
    cache_info,
    clear_cache,
    get_cached_run,
)


class ServiceAndViewerTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()

    def test_forecast_research_profile_api_exposes_style_without_a_style_total(self) -> None:
        payload = build_oil_short_term_profile_payload(
            seed=42,
            score_min=65,
            score_max=75,
        )
        institution = payload["institution"]

        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(institution["capability_total_score"], 65.0)
        self.assertLessEqual(institution["capability_total_score"], 75.0)
        self.assertEqual(
            {
                "trend_reversion_bias",
                "fundamental_market_bias",
                "confirmation_lead_bias",
                "confidence_style",
                "revision_style",
            },
            set(institution["research_style"]),
        )
        self.assertNotIn("research_style_total_score", institution)

    def test_cache_and_minimum_payload(self) -> None:
        first = get_cached_run(42, 12)
        second = get_cached_run(42, 12)
        self.assertIs(first, second)
        self.assertEqual(1, cache_info()["entries"])
        payload = build_run_payload(first)
        self.assertTrue(payload["ok"])
        self.assertEqual(13, len(payload["globalMacroSnapshots"]))
        self.assertEqual(13, len(payload["nextYearInputs"]))
        self.assertEqual(13, len(payload["viewerSupportRows"]))
        self.assertNotIn("diagnostics", payload)
        self.assertEqual(42, len(payload["globalMacroSnapshots"][0]))
        self.assertNotIn("industries", payload)
        self.assertNotIn("issuers", payload)
        self.assertIn("commodities", payload)
        self.assertEqual(COMMODITY_MODEL_VERSION, payload["commodities"]["identity"]["model_version"])
        self.assertEqual(list(CONTRACT_ORDER), payload["commodities"]["identity"]["contract_ids"])
        self.assertEqual(13, len(payload["commodities"]["contracts"]["brent"]))
        self.assertAlmostEqual(
            payload["commodities"]["contracts"]["brent"][0]["nominal_price_usd"],
            payload["globalMacroSnapshots"][0]["brent_oil_price_usd"],
            places=6,
        )
        self.assertIn("nominal_price_usd", payload["commodities"]["contracts"]["wti"][0])
        self.assertIn("real_index", payload["commodities"]["kinds"]["energy"][0])
        self.assertFalse(payload["commodities"]["identity"]["write_back"])
        self.assertEqual(12, len(payload["commodities"]["contracts"]["brent"][3]["monthly"]))
        self.assertEqual(4, len(payload["commodities"]["contracts"]["brent"][3]["monthly"][0]["weekly"]))
        self.assertNotIn("monthly", payload["commodities"]["contracts"]["copper"][0])

    def test_full_diagnostics_are_opt_in(self) -> None:
        run = get_cached_run(5, 5, "full")
        payload = build_run_payload(run, include_support=False)
        self.assertEqual(5, len(payload["diagnostics"]))
        self.assertNotIn("viewerSupportRows", payload)

    def test_010509_oil_futures_half_month_curve_and_roll(self) -> None:
        long_run = get_cached_run(42, 60)
        january_h1 = oil_futures_payload(long_run, as_of_year=2030, as_of_month=1, as_of_half=1)
        january_h2 = oil_futures_payload(long_run, as_of_year=2030, as_of_month=1, as_of_half=2)
        february_h1 = oil_futures_payload(long_run, as_of_year=2030, as_of_month=2, as_of_half=1)
        april_h1 = oil_futures_payload(long_run, as_of_year=2030, as_of_month=4, as_of_half=1)
        april_h2 = oil_futures_payload(long_run, as_of_year=2030, as_of_month=4, as_of_half=2)
        may_h1 = oil_futures_payload(long_run, as_of_year=2030, as_of_month=5, as_of_half=1)
        may_h2 = oil_futures_payload(long_run, as_of_year=2030, as_of_month=5, as_of_half=2)

        self.assertTrue(january_h1["ok"])
        self.assertEqual(OIL_FUTURES_MODEL_VERSION, january_h1["identity"]["model_version"])
        self.assertFalse(january_h1["identity"]["write_back"])
        self.assertFalse(january_h1["identity"]["orders_enabled"])
        self.assertEqual(24, january_h1["identity"]["turns_per_year"])
        specification = january_h1["contractSpecification"]
        self.assertEqual(1000, specification["contract_size_bbl"])
        self.assertEqual(1, specification["minimum_order_lots"])
        self.assertEqual(0.01, specification["minimum_price_fluctuation_usd_per_bbl"])
        self.assertEqual(10.0, specification["tick_value_usd_per_lot"])
        self.assertEqual(15.0, specification["initial_margin_rate_pct"])
        self.assertEqual(12.0, specification["maintenance_margin_rate_pct"])
        self.assertEqual(
            "enforced_by_oil_futures_account_v1",
            specification["margin_status"],
        )
        self.assertEqual("cash", specification["settlement_method"])
        self.assertEqual("settlement_only", specification["expiry_month_position_mode"])
        policy = january_h1["participantLimitsPolicy"]
        self.assertEqual(3.0, policy["single_contract_open_interest_rate_pct"])
        self.assertEqual(75_000, policy["single_contract_hard_cap_lots"])
        self.assertEqual(150_000, policy["all_contract_gross_position_cap_lots"])
        self.assertEqual(0.8, policy["turn_volume_rate_pct"])
        self.assertEqual(4, policy["turn_volume_reference_weeks"])
        self.assertEqual(2, policy["turn_volume_equivalent_weeks"])
        self.assertFalse(policy["enforced"])
        self.assertEqual(4, len(january_h1["curve"]["contracts"]))
        self.assertEqual(
            ["OIL-3001", "OIL-3005", "OIL-3009", "OIL-3101"],
            [item["contract_id"] for item in january_h1["curve"]["contracts"]],
        )
        self.assertEqual(16, january_h1["identity"]["contract_lifecycle_months"])
        self.assertEqual(32, january_h1["identity"]["contract_lifecycle_turns"])
        self.assertEqual([31, 23, 15, 7], [
            item["visible_turn_count"] for item in january_h1["curve"]["contracts"]
        ])
        self.assertEqual(2, len(january_h1["reference"]["monthly"][-1]["weekly"]))
        self.assertEqual(4, len(january_h2["reference"]["monthly"][-1]["weekly"]))
        self.assertEqual("OIL-3001", january_h1["curve"]["nearest_contract_id"])
        self.assertEqual("OIL-3005", january_h1["curve"]["main_contract_id"])
        self.assertEqual(0.5, january_h1["curve"]["contracts"][0]["months_to_expiry"])
        self.assertEqual(0, january_h2["curve"]["contracts"][0]["months_to_expiry"])
        self.assertAlmostEqual(
            january_h2["reference"]["price_usd"],
            january_h2["curve"]["contracts"][0]["futures_price_usd"],
            places=8,
        )
        self.assertEqual("OIL-3005", february_h1["curve"]["nearest_contract_id"])
        self.assertEqual("OIL-3105", february_h1["curve"]["contracts"][-1]["contract_id"])
        self.assertEqual([25, 17, 9, 1], [
            item["visible_turn_count"] for item in february_h1["curve"]["contracts"]
        ])
        self.assertEqual("backward_ratio_adjusted", may_h1["mainContinuous"]["adjustment"])
        self.assertEqual("OIL-3009", may_h1["mainContinuous"]["active_contract_id"])
        self.assertTrue(any(
            item["year"] == 2030
            and item["month"] == 5
            and item["roll_from_contract_id"] == "OIL-3005"
            for item in may_h1["mainContinuous"]["monthly"]
        ))
        self.assertEqual("2030-05-H1", may_h1["mainContinuous"]["rolls"][-1]["label"])
        self.assertEqual("OIL-3009", may_h1["mainContinuous"]["rolls"][-1]["to_contract_id"])
        self.assertFalse(january_h1["futuresLiquidity"]["spot_included"])
        self.assertNotIn("volume_lots", january_h1["reference"]["monthly"][-1]["weekly"][-1])
        latest_market = january_h1["futuresLiquidity"]["latest"]
        latest_contract_weeks = [
            item["monthly"][-1]["weekly"][-1]
            for item in january_h1["curve"]["contracts"]
        ]
        self.assertEqual(
            latest_market["volume_lots"],
            sum(item["volume_lots"] for item in latest_contract_weeks),
        )
        self.assertEqual(
            latest_market["open_interest_lots"],
            sum(item["open_interest_lots"] for item in latest_contract_weeks),
        )
        self.assertTrue(all(item["volume_lots"] >= 0 for item in latest_contract_weeks))
        self.assertTrue(all(item["open_interest_lots"] >= 0 for item in latest_contract_weeks))
        self.assertIn(
            "volume_lots", january_h1["mainContinuous"]["monthly"][-1]["weekly"][-1]
        )
        january_contracts = {
            item["contract_id"]: item for item in january_h1["curve"]["contracts"]
        }
        january_main = january_contracts["OIL-3005"]
        main_limits = january_main["participantLimits"]
        self.assertEqual(
            min(int(january_main["open_interest_lots"] * 0.03), 75_000),
            main_limits["single_contract_position_limit_lots"],
        )
        self.assertEqual(150_000, main_limits["all_contract_gross_position_cap_lots"])
        visible_main_weeks = [
            week
            for month in january_main["monthly"]
            for week in month["weekly"]
        ]
        recent_main_volume = sum(week["volume_lots"] for week in visible_main_weeks[-4:])
        turn_equivalent_volume = round(recent_main_volume * 2 / 4)
        self.assertEqual(
            round(turn_equivalent_volume * 0.008),
            main_limits["turn_trade_limit_lots"],
        )
        self.assertEqual(
            turn_equivalent_volume, main_limits["turn_equivalent_volume_lots"]
        )
        self.assertAlmostEqual(
            main_limits["single_contract_position_limit_lots"]
            * specification["contract_size_bbl"]
            * january_main["price_usd"],
            main_limits["position_limit_notional_usd"],
            delta=1.0,
        )
        self.assertEqual(
            main_limits, january_h1["mainContinuous"]["participantLimits"]
        )
        expiry_limits = january_contracts["OIL-3001"]["participantLimits"]
        self.assertEqual(
            min(int(january_contracts["OIL-3001"]["open_interest_lots"] * 0.03), 8_000),
            expiry_limits["single_contract_position_limit_lots"],
        )
        self.assertFalse(expiry_limits["new_trades_allowed"])
        self.assertEqual(0, expiry_limits["turn_trade_limit_lots"])
        self.assertNotIn("participantLimits", january_h1["reference"])
        april_h1_contracts = {
            item["contract_id"]: item for item in april_h1["curve"]["contracts"]
        }
        april_h2_contracts = {
            item["contract_id"]: item for item in april_h2["curve"]["contracts"]
        }
        april_h1_old_share = (
            april_h1_contracts["OIL-3005"]["latest_weekly_volume_lots"]
            / april_h1["futuresLiquidity"]["latest"]["volume_lots"]
        )
        april_h2_old_share = (
            april_h2_contracts["OIL-3005"]["latest_weekly_volume_lots"]
            / april_h2["futuresLiquidity"]["latest"]["volume_lots"]
        )
        self.assertLess(april_h2_old_share, april_h1_old_share)
        may_h1_contracts = {
            item["contract_id"]: item for item in may_h1["curve"]["contracts"]
        }
        may_h1_old_weeks = may_h1_contracts["OIL-3005"]["monthly"][-1]["weekly"]
        self.assertGreater(
            may_h1_old_weeks[-1]["open_interest_lots"]
            / may_h1["futuresLiquidity"]["latest"]["open_interest_lots"],
            0.08,
        )
        self.assertEqual(
            15_000,
            april_h1_contracts["OIL-3005"]["participantLimits"][
                "single_contract_position_limit_lots"
            ],
        )
        self.assertEqual(
            8_000,
            april_h2_contracts["OIL-3005"]["participantLimits"][
                "single_contract_position_limit_lots"
            ],
        )
        self.assertFalse(
            may_h1_contracts["OIL-3005"]["participantLimits"]["new_trades_allowed"]
        )
        self.assertEqual(0, may_h2["curve"]["contracts"][0]["open_interest_lots"])
        end_run = oil_futures_payload(
            long_run, as_of_year=2085, as_of_month=12, as_of_half=2
        )
        self.assertTrue(all(
            2_500_000 <= item["volume_lots"] <= 25_000_000
            for item in end_run["futuresLiquidity"]["weekly"]
        ))
        self.assertTrue(all(
            1_800_000 <= item["open_interest_lots"] <= 5_800_000
            for item in end_run["futuresLiquidity"]["weekly"]
        ))

        short_run = get_cached_run(42, 5)
        january_short = oil_futures_payload(short_run, as_of_year=2030, as_of_month=1, as_of_half=1)
        self.assertEqual(january_h1["reference"], january_short["reference"])
        self.assertEqual(january_h1["curve"], january_short["curve"])
        self.assertEqual(january_h1["mainContinuous"], january_short["mainContinuous"])

    def test_strategy_research_roster_api_exposes_appointments_not_sliders(self) -> None:
        first = build_oil_strategy_research_roster_payload(
            seed=42, candidate_count=5
        )
        repeat = build_oil_strategy_research_roster_payload(
            seed=42, candidate_count=5
        )
        self.assertEqual(first, repeat)
        self.assertTrue(first["ok"])
        self.assertEqual(5, first["candidateCount"])
        self.assertFalse(first["selectionPolicy"]["player_can_edit_radar"])
        self.assertFalse(
            first["selectionPolicy"]["preference_total_score_available"]
        )
        self.assertEqual(
            5, len({item["profile_hash"] for item in first["candidates"]})
        )
        self.assertTrue(all(
            item["appointment"]["role"] == "oil_strategy_research_director"
            for item in first["candidates"]
        ))
        forbidden_pm_scores = {
            "capability_total_score",
            "quality_score",
            "alpha_score",
            "investment_skill",
            "compatibility_score",
        }
        self.assertTrue(all(
            item["preference_total_score"] is None
            and forbidden_pm_scores.isdisjoint(item)
            for item in first["candidates"]
        ))

    def test_execution_desk_roster_api_exposes_continuous_appointments(self) -> None:
        first = build_oil_execution_desk_roster_payload(seed=42, candidate_count=5)
        self.assertEqual(
            first,
            build_oil_execution_desk_roster_payload(seed=42, candidate_count=5),
        )
        self.assertTrue(first["selectionPolicy"]["scores_are_continuous"])
        self.assertFalse(first["selectionPolicy"]["player_can_edit_radar"])
        self.assertEqual(5, first["candidateCount"])
        self.assertTrue(
            all(
                item["appointment"]["role"] == "oil_execution_director"
                for item in first["candidates"]
            )
        )

    def test_retired_cro_roster_is_not_exposed_by_the_service(self) -> None:
        from asset_simulation import server

        source = server.Path(server.__file__).read_text(encoding="utf-8")
        self.assertNotIn("/api/corporate-risk-roster", source)
        self.assertFalse(hasattr(server, "build_corporate_risk_roster_payload"))

    def test_static_viewer_contract(self) -> None:
        from asset_simulation import server

        html = (server.VIEWER_ROOT / "index.html").read_text(encoding="utf-8")
        script = (server.VIEWER_ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        game_html = (server.VIEWER_ROOT / "game.html").read_text(encoding="utf-8")
        game_script = (server.VIEWER_ROOT / "static" / "js" / "game.js").read_text(encoding="utf-8")
        css = (server.VIEWER_ROOT / "static" / "css" / "viewer.css").read_text(encoding="utf-8")
        catalog = (server.VIEWER_ROOT / "static" / "data" / "commodities.json").read_text(encoding="utf-8")
        page = html + script + catalog
        self.assertIn("Asset Simulation", html)
        self.assertIn("单一计价单位下的全球宏观环境", html)
        self.assertIn("一级商品", html)
        self.assertIn("二级商品", html)
        self.assertIn('data-layer="macro"', html)
        self.assertIn('data-layer="commodity"', html)
        self.assertIn('id="gameEntry"', html)
        self.assertIn("游戏运转", html)
        self.assertNotIn('data-layer="industry"', html)
        self.assertNotIn('data-layer="stock"', html)
        self.assertNotIn("regionSelect", html)
        self.assertNotIn("stockInput", html)
        self.assertNotIn("issuerCard", html)
        self.assertIn("/static/data/commodities.json", script)
        self.assertNotIn("/static/data/shenwan_l2_2021.json", script)
        self.assertNotIn("/static/data/issuers.json", script)
        self.assertIn("COMMODITY_MODES", script)
        self.assertIn("GLOBAL_MODES", script)
        self.assertIn("commonDetailFields", script)
        self.assertNotIn("commodityDetailFields", script)
        self.assertIn("实际油价指数（右轴）", script)
        self.assertIn("ui_real_oil_index", script)
        self.assertIn("美元资金条件", script)
        self.assertNotIn("美元资金代理", script)
        self.assertIn("总体通胀", script)
        self.assertIn("投资级利差", script)
        self.assertIn("高收益利差", script)
        self.assertIn("周期动量偏弱", script)
        self.assertNotIn('contraction: "收缩"', script)
        self.assertIn("企业盈利参考", script)
        self.assertIn("产出缺口", script)
        self.assertIn("风险偏好 / ERP", script)
        self.assertIn("宏观资产复利参考", script)
        self.assertIn("权益复利参考", script)
        self.assertIn("主权债复利参考", script)
        self.assertIn("原油名义价格", script)
        self.assertNotIn("权益总回报", script)
        self.assertNotIn("主权债总回报", script)
        self.assertIn("原油", page)
        self.assertIn("天然气", page)
        self.assertNotIn("Brent", page)
        self.assertNotIn("WTI", page)
        self.assertNotIn("Henry Hub", page)
        self.assertNotIn("TTF", page)
        self.assertIn("黄金", page)
        self.assertNotIn("中国工商银行", page)
        self.assertNotIn("申万", script)
        macro_block = script[script.index("function macroStats") : script.index("function commodityStats")]
        self.assertEqual(9, macro_block.count("stat("))
        self.assertIn('stat("原油"', macro_block)
        self.assertNotIn("Brent", macro_block)
        self.assertIn(".commodity-picker[hidden] { display: none; }", css)
        self.assertIn('$("commodityPicker").hidden = state.layer !== "commodity"', script)
        self.assertIn("function isCommodityChartOpen", script)
        self.assertIn("function renderBlankCommodityChart", script)
        self.assertIn("OPEN_COMMODITY_CONTRACT", script)
        self.assertIn(".workspace.price-only { grid-template-columns: 1fr; }", css)
        self.assertIn(".overview-button { margin-left: auto;", css)
        self.assertIn('data-chart-view="overview"', script)
        self.assertIn('data-chart-view="annual"', script)
        self.assertIn('data-chart-view="monthly"', script)
        self.assertIn('data-chart-view="weekly"', script)
        self.assertIn("function renderAnnualCandles", script)
        self.assertIn("function renderMonthlyCandles", script)
        self.assertIn("function renderWeeklyCandles", script)
        self.assertIn("ui_monthly", script)
        self.assertIn("function weeklyBars", script)
        self.assertIn('class="candle-hit"', script)
        self.assertIn("ui_contract_high", script)
        self.assertIn("ui_contract_low", script)
        self.assertIn("y(high)", script)
        self.assertIn("y(low)", script)
        self.assertNotIn("最高／最低未单独模拟", script)
        annual_block = script[script.index("function renderAnnualCandles") : script.index("function renderChart")]
        self.assertIn('class="cursor-hit"', annual_block)
        self.assertIn("wrap.onpointermove", annual_block)
        self.assertIn("wrap.setPointerCapture", annual_block)
        self.assertIn("indexFromPointer", annual_block)
        self.assertIn("ANNUAL_PAN_RATE", script)
        self.assertIn("ANNUAL_DRAG_THRESHOLD", script)
        self.assertIn("state.annualPan.startScrollLeft", annual_block)
        self.assertIn("annualSuppressClick", annual_block)
        self.assertIn("annualClickFallback", annual_block)
        self.assertNotIn('if (hit) selectIndex(Number(hit.dataset.index));\n  };\n  const endPointerDrag', annual_block)
        self.assertIn("wrap.setPointerCapture", annual_block)
        self.assertIn("const panMoved", annual_block)
        self.assertNotIn("annualMousePan", annual_block)
        self.assertIn("function jumpToYear", script)
        self.assertIn("data-year-jump-input", script)
        self.assertIn("annualCenterIndex", script)
        self.assertIn("overflow-x: auto", css)
        self.assertIn("scrollbar-width: none", css)
        self.assertIn("::-webkit-scrollbar", css)
        self.assertIn(".chart-price-axis", css)
        self.assertIn(".chart-shell", css)
        self.assertIn(".price-tick", css)
        self.assertIn('id="chartAxis"', page)
        self.assertIn("removeAttribute(\"hidden\")", script)
        self.assertIn("function renderPriceAxis", script)
        self.assertIn(".cursor-hit", css)
        self.assertIn("background: transparent; overflow: visible;", css)
        self.assertIn('panel.hidden = priceOnly', script)
        self.assertIn("`Seed ${current().seed} · ${state.rows.length} 个年度状态`", script)
        self.assertNotIn("result_hash.slice", script)
        self.assertNotIn("×10", script)
        self.assertNotIn("×5", script)
        self.assertIn('axis === "right"', script)
        self.assertIn('class="chart-hit"', script)
        self.assertIn('addEventListener("pointermove"', script)
        self.assertEqual("asset-simulation-macro-ui-v5.42", SERVICE_ID)
        self.assertNotIn("公司风控", game_html + game_script)
        self.assertIn("策略约束", game_html + game_script)
        self.assertNotIn("/api/world", script)
        self.assertIn("/api/global", script)
        self.assertIn("2030年01月上半月", game_html)
        self.assertIn("2025—2029", game_html)
        self.assertIn("推进半个月", game_html)
        self.assertIn("第 1 / 1344 回合", game_html)
        self.assertIn('id="jumpHalf"', game_html)
        self.assertIn("查看 K 线", game_html)
        self.assertIn('id="gameMarketPicker"', game_html)
        self.assertIn("能源化工", game_html)
        self.assertIn('<option value="crude_oil">原油</option>', game_html)
        self.assertIn('data-period="weekly"', game_html)
        self.assertNotIn('data-period="forecast"', game_html)
        self.assertNotIn('id="forecastConfigButton"', game_html)
        self.assertNotIn('id="forecastConfigModal"', game_html)
        self.assertNotIn("采用并生成预测", game_html)
        self.assertIn('id="gamePrimaryNav"', game_html)
        self.assertIn('data-game-view="market"', game_html)
        self.assertIn('data-game-view="investment"', game_html)
        self.assertIn('id="investmentScreen"', game_html)
        self.assertIn('id="committeeRoster"', game_html)
        self.assertIn('id="competitionLeaderboard"', game_html)
        self.assertIn('id="turnReportRows"', game_html)
        self.assertIn('data-period="monthly"', game_html)
        self.assertIn('data-period="annual"', game_html)
        self.assertIn("function visibleMonths", game_script)
        self.assertIn("TURNS_PER_MONTH = 2", game_script)
        self.assertIn("half: String(cutoff.half)", game_script)
        self.assertIn("function annualBars", game_script)
        self.assertIn("function monthlyBars", game_script)
        self.assertIn("function weeklyBars", game_script)
        self.assertIn("instrument?.type === \"contract\"", game_script)
        self.assertNotIn("/api/oil-short-term-forecast", game_script)
        self.assertNotIn("/api/oil-short-term-profile", game_script)
        self.assertIn("/api/oil-investment-competition", game_script)
        self.assertIn("function renderCommitteeRoster", game_script)
        self.assertIn("function renderCompetitionLeaderboard", game_script)
        self.assertIn("function renderTurnReport", game_script)
        self.assertIn("gameView", game_script)
        self.assertIn('id="liquidityMetrics"', game_html)
        self.assertIn("volume_lots", game_script)
        self.assertIn("open_interest_lots", game_script)
        self.assertIn("volume-bar", game_script)
        self.assertIn("oi-line", game_script)
        self.assertIn("普通推进不能回到过去", game_script)
        self.assertIn("localStorage.setItem", game_script)
        self.assertIn("while (state.turn < next)", game_script)
        self.assertIn("/api/oil-futures", game_script)
        self.assertNotIn("/api/global", game_script)
        self.assertIn('id="futuresCurveChart"', game_html)
        self.assertIn('id="specContractSize"', game_html)
        self.assertIn('id="detailContractSpec"', game_html)
        self.assertIn('id="specSinglePositionLimit"', game_html)
        self.assertIn('id="detailSinglePositionLimit"', game_html)
        self.assertIn("participantLimitsPolicy", game_script)
        self.assertIn("single_contract_position_limit_lots", game_script)
        self.assertIn('id="marketRows"', game_html)
        self.assertNotIn('id="futuresRows"', game_html)
        self.assertIn("原油主连", game_html)
        self.assertIn("function marketInstruments", game_script)
        self.assertIn("data-instrument", game_script)
        self.assertIn("game-roll-line", game_script)
        self.assertIn("function renderContractSpecification", game_script)
        self.assertIn("tick_value_usd_per_lot", game_script)
        self.assertIn("升水 / CONTANGO", game_script)
        self.assertIn("market-board-head", css)
        self.assertIn("game-chart-wrap", css)
        self.assertIn("futures-board-head", css)
        self.assertIn("账户 / 保证金", game_html)
        self.assertIn(".report-table", css)
        self.assertIn("scrollbar-gutter: stable", css)
        self.assertIn("overscroll-behavior-x: contain", css)

    def test_investment_competition_service_payload(self) -> None:
        opening = build_oil_investment_competition_payload(
            seed=42,
            years=6,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=1,
        )
        settled = build_oil_investment_competition_payload(
            seed=42,
            years=6,
            as_of_year=2030,
            as_of_month=1,
            as_of_half=2,
        )
        self.assertTrue(opening["ok"])
        self.assertEqual(0, opening["completed_turns"])
        self.assertEqual(4, len(opening["participants"]))
        self.assertEqual(1, settled["completed_turns"])
        self.assertEqual(4, len(settled["latest_report"]["participants"]))

    def test_model_context_guide_tracks_runtime_baseline(self) -> None:
        from asset_simulation import server
        from asset_simulation.model.engine import MODEL_VERSION as GLOBAL_MODEL_VERSION
        from asset_simulation.model.registry import load_registered_assets

        package_root = server.VIEWER_ROOT.parent
        entry = (package_root / "CLAUDE.md").read_text(encoding="utf-8")
        docs_root = package_root / "docs"
        guide = (docs_root / "MODEL_CONTEXT_GUIDE.md").read_text(encoding="utf-8")
        index = (docs_root / "INDEX.md").read_text(encoding="utf-8")
        runtime = (docs_root / "current" / "RUNTIME_ARCHITECTURE.md").read_text(encoding="utf-8")
        contracts_doc = (docs_root / "current" / "CONTRACTS_AND_UNITS.md").read_text(encoding="utf-8")
        engine = (package_root / "model" / "engine.py").read_text(encoding="utf-8")
        assets = load_registered_assets()

        self.assertIn("asset_simulation/docs/MODEL_CONTEXT_GUIDE.md", entry)
        self.assertIn("仓库根下的 `asset_simulation/`", guide)
        self.assertLess(len(guide), 10_000)
        for relative_path in (
            "current/RUNTIME_ARCHITECTURE.md",
            "current/CONTRACTS_AND_UNITS.md",
            "current/MODEL_QUALITY_AUDIT.md",
            "current/VIEWER_PROJECTION.md",
            "current/COMMODITY_LAYER.md",
            "current/CORPORATE_RISK_CONTROL.md",
            "current/OIL_INVESTMENT_COMPETITION.md",
            "current/INSTITUTION_ORGANIZATION.md",
            "components/GLOBAL_ORDINARY_CYCLE.md",
            "design/FUTURE_LAYERS.md",
            "decisions/ADR-001-ANNUAL-SEQUENCING.md",
            "decisions/ADR-002-NOMINAL-REAL-PRICES.md",
            "decisions/ADR-003-REGIONAL-ONE-WAY-AND-G17.md",
            "decisions/ADR-004-SINGLE-COUPLED-WORLD.md",
            "decisions/ADR-005-MACRO-ENVIRONMENT-AND-CAPITAL-MARKETS.md",
            "decisions/ADR-006-SINGLE-GLOBAL-FEDERATION.md",
        ):
            self.assertTrue((docs_root / relative_path).is_file(), relative_path)
            self.assertIn(relative_path, index)
        self.assertFalse((docs_root / "current" / "INDUSTRY_LAYER.md").exists())
        self.assertFalse((docs_root / "current" / "ISSUER_ICBC.md").exists())
        self.assertFalse((docs_root / "current" / "G17_LITE.md").exists())
        self.assertFalse((docs_root / "regions").exists())
        archived = docs_root / "archive" / "GLOBAL_MACRO_MINIMUM_VIABLE_FUNCTION_2026-08-13.md"
        self.assertTrue(archived.is_file())
        self.assertIn("历史归档，不是当前实现权威", archived.read_text(encoding="utf-8"))
        self.assertIn("model/funding_credit.py", guide)
        self.assertIn("model/impulses.py", guide)
        self.assertIn("model/oil_strategy_research.py", guide)
        self.assertIn("model/oil_execution_desk.py", guide)
        self.assertIn("model/corporate_risk_control.py", guide)
        self.assertIn("model/oil_strategy_risk.py", guide)
        self.assertIn("model/oil_investment_competition.py", guide)
        self.assertIn("model/institution_organization.py", guide)
        self.assertIn("`limit: 100` 只能报告为“前 100 行”", guide)
        self.assertIn("失败的 Read 也必须在报告中列出", guide)
        self.assertIn("不得宣称“可独立实施变更”", guide)
        self.assertIn(GLOBAL_MODEL_VERSION, runtime)
        self.assertNotIn("north_america", assets)
        self.assertNotIn("industry_banks_config", assets)
        self.assertIn(assets["field_contract"]["contract_id"], contracts_doc)
        self.assertIn("commodity_overlay_v1", contracts_doc)
        self.assertIn("oil_futures_overlay_v8", contracts_doc)
        self.assertIn("oil_strategy_research_v2", contracts_doc)
        self.assertIn("oil_execution_desk_v2", contracts_doc)
        self.assertIn("corporate_risk_control_v2", contracts_doc)
        self.assertIn("oil_strategy_risk_v1", contracts_doc)
        self.assertIn("oil_trading_strategy_v8", contracts_doc)
        self.assertIn("institution_organization_v1", contracts_doc)
        self.assertEqual(
            "oil_strategy_research_v2",
            assets["oil_strategy_research_contract"]["contract_id"],
        )
        self.assertEqual(
            "oil_execution_desk_v2",
            assets["oil_execution_desk_contract"]["contract_id"],
        )
        self.assertEqual(
            "corporate_risk_control_v2",
            assets["corporate_risk_control_contract"]["contract_id"],
        )
        self.assertEqual(
            "oil_strategy_risk_v1",
            assets["oil_strategy_risk_contract"]["contract_id"],
        )
        self.assertEqual(
            "oil_trading_strategy_v8",
            assets["oil_trading_strategy_contract"]["contract_id"],
        )

        field_count = sum(
            len(assets["field_contract"][section])
            for section in (
                "identity_fields",
                "a_level_fields",
                "b_level_fields",
                "derived_identities",
            )
        )
        self.assertEqual(42, field_count)
        self.assertIn("26 个 A 级字段", contracts_doc)
        self.assertIn("共 42 项", contracts_doc)

        runtime_order = [
            engine.index("real_economy.step"),
            engine.index("inflation_nominal.step"),
            engine.index("rates.step"),
            engine.index("funding_credit.step"),
            engine.index("oil_commodity.step"),
            engine.index("asset_reference.step"),
        ]
        self.assertEqual(sorted(runtime_order), runtime_order)
        documented_order = [
            runtime.index("real_economy.step"),
            runtime.index("inflation_nominal.step"),
            runtime.index("rates.step"),
            runtime.index("funding_credit.step"),
            runtime.index("oil_commodity.step"),
            runtime.index("asset_reference.step"),
        ]
        self.assertEqual(sorted(documented_order), documented_order)

    def test_commodity_catalog_has_kinds_and_contracts(self) -> None:
        from asset_simulation import server

        catalog = json.loads(
            (server.VIEWER_ROOT / "static" / "data" / "commodities.json").read_text(encoding="utf-8")
        )
        kinds = catalog["kinds"]
        self.assertEqual(["energy", "industrial_metals", "precious_metals", "agriculture"], [item["id"] for item in kinds])
        energy = kinds[0]
        self.assertEqual("能源", energy["name"])
        self.assertEqual(
            ["brent", "henry_hub"],
            [item["id"] for item in energy["children"]],
        )
        self.assertEqual("原油", energy["children"][0]["name"])
        self.assertEqual("天然气", energy["children"][1]["name"])
        gold = next(item for item in kinds if item["id"] == "precious_metals")
        self.assertEqual("黄金", gold["children"][0]["name"])
        self.assertTrue(all(item["children"] for item in kinds))
