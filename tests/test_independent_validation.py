"""Independent validation contract, using fabricated performance indices only."""
from copy import deepcopy
from types import SimpleNamespace
import pandas as pd
import numpy as np
import pytest
from pulse.analytics import drawdown
from pulse.evidence import historical_evidence, dividend_evidence, history_summary
from tools.validate_returns import validate_model
from test_risk_income import history


def model_for(levels):
    nav,metrics=history(levels)
    return dict(nav=nav,raw={'historical_metrics':metrics},returns_scope_label='Stocks & funds',drawdown=drawdown(nav,metrics,'Stocks & funds'))


@pytest.mark.parametrize('levels',[[100,90,110,88],[100,100,100],[100,110,120],[100,80,70],[100,300,150]])
def test_validator_units_baseline_scope_and_complete_series(levels):
    model=model_for(levels);seen={}
    def comp(returns):
        seen['returns']=returns.copy();return levels[-1]/100-1
    def dd(index):
        seen['index']=index.copy();return pd.Series(np.array(levels)/np.maximum.accumulate(levels)-1,index=index.index)
    outcome=validate_model(model,SimpleNamespace(comp=comp,to_drawdown_series=dd))
    assert outcome['status']=='PASS' and all(outcome['checks'].values())
    assert seen['returns'].iloc[0]==0 and list(seen['index'])==levels
    assert seen['returns'].iloc[1]==pytest.approx(levels[1]/100-1)


def test_validator_refuses_interior_gaps_and_wrong_initial_return():
    model=model_for([100,110,88]);model['nav'].loc[1,'daily_twr_return_pct']=None
    stats=SimpleNamespace(comp=lambda x:pytest.fail('A gap must be rejected before any library call'))
    assert validate_model(model,stats)['status']=='UNAVAILABLE'
    model=model_for([100,110,88]);model['nav'].loc[0,'daily_twr_return_pct']=1
    assert validate_model(model,stats)['status']=='UNAVAILABLE'


def test_independent_disagreement_fails_and_does_not_change_model():
    model=model_for([100,110,88]);before=model['nav'].copy()
    stats=SimpleNamespace(comp=lambda r:99,to_drawdown_series=lambda i:pd.Series(0.,index=i.index))
    assert validate_model(model,stats)['status']=='FAIL'
    pd.testing.assert_frame_equal(model['nav'],before)


def test_history_evidence_uses_canonical_instrument_days_and_allowlists():
    raw=dict(historical_metrics=dict(externally_observed_market_price_coverage_pct=80,external_market_price_coverage_pct=90,fallback_price_coverage_pct=8,unresolved_price_coverage_pct=2),historical_price_audit=[dict(security_name='Fictional fund',error='PRIVATE_ERROR',transaction_id='PRIVATE_EVENT')])
    evidence=historical_evidence(raw)
    assert evidence['shares']==dict(observed=80,carried_market=10,fallback=8,unresolved=2)
    assert sum(evidence['shares'].values())==100 and 'instrument-days' in evidence['note']
    assert 'PRIVATE' not in str(evidence) and 'observed 80.0%' in history_summary(evidence)
    raw['historical_metrics']['external_market_price_coverage_pct']=50
    assert not historical_evidence(raw)['available']


def test_dividend_coverage_never_infers_unknown_net_as_zero_or_interest():
    raw=dict(holdings=[dict(position_status='ACTIVE',asset_class='FUND',isin='ZZ0000000001',security_name='Fictional fund',yahoo_ticker='FAKE'),dict(position_status='CLOSED',asset_class='STOCK',isin='ZZ0000000002')],forward_dividends={'projection':[dict(isin='ZZ0000000001',external_dividend_status='DECLARED_UPCOMING_FOUND_UNSCHEDULED_PAYMENT',growth_adjusted_forward_12m_net_dividend_eur=None)],'forecast':[]},interest=[{'net_interest_eur':1000}])
    evidence=dividend_evidence(raw)
    assert len(evidence)==1 and evidence[0]['known_net_estimate'] is None and 'payment date or amount is unresolved' in evidence[0]['reason']
