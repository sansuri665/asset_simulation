from asset_simulation.model.engine import run_global_macro
from asset_simulation.model.oil_futures_overlay import oil_futures_payload
from asset_simulation.model.oil_short_term_forecast import generate_oil_short_term_forecast
from asset_simulation.model.oil_trading_strategy import build_oil_strategy_decision

run = run_global_macro(42, 7)
market = oil_futures_payload(run, as_of_year=2030, as_of_month=1, as_of_half=1)
forecast = generate_oil_short_term_forecast(run, as_of_year=2030, as_of_month=1, as_of_half=1)
decision = build_oil_strategy_decision(market, forecast)
print('riskBudget', decision['riskBudget'])
print('strategyLimits', decision['strategyRisk']['strategyLimits'])
print('strategySummary', decision['strategyRisk']['approvalSummary'])
print('companyLimits', decision['corporateRisk']['company_limits'])
print('companySummary', decision['corporateRisk']['approval_summary'])
for item in decision['targets']:
    print('TARGET', item['contract_id'], {
        'strategy_intent': item.get('strategy_intent_target_position_lots'),
        'strategy_approved': item.get('strategy_risk_approved_target_position_lots'),
        'company_input': item.get('company_risk_input_target_lots'),
        'final': item.get('target_position_lots'),
        'strategy_binding': item.get('strategy_risk_binding_rules'),
        'company_binding': item.get('risk_binding_rules'),
        'strategy_margin': item.get('strategy_estimated_initial_margin_usd'),
        'company_margin': item.get('estimated_initial_margin_usd'),
    })
