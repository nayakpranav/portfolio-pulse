"""Read-only research contracts. All portfolios and issuer evidence are invented."""
from copy import deepcopy
from datetime import date
from io import BytesIO
from types import SimpleNamespace
import base64
import hashlib
import math
import numpy as np
import pandas as pd
import pytest
from pypdf import PdfReader
from pulse.adapter import prepare
from pulse.analytics import drawdown
from pulse.composition import composition
from pulse.intelligence import attribution,capital_bridge,benchmark_risk,underlying_exposure,income_intelligence
from pulse.intelligence_view import intelligence_html
from pulse.html_report import html_report,SCRIPT
from pulse.pdf import summary_pdf
from pulse.runner import run_analysis
from pulse.synthetic import fixture


@pytest.fixture(scope='module')
def canonical_results():
    return {kind:run_analysis(data,prices=prices) for kind in ('demo','reinvestment','derivatives','open_derivatives','missing_price','unsupported_action') for data,prices in [fixture(kind)]}


def test_canonical_attribution_and_closed_derivative_settlements(canonical_results):
    for kind in ('demo','derivatives','reinvestment'):
        raw=deepcopy(canonical_results[kind]);model=prepare(raw,date(2026,9,30),'full_portfolio');a=model['intelligence']['attribution']
        assert a['available'],(kind,a)
        assert math.fsum(r['value'] for r in a['components'])==pytest.approx(model['full_totals']['profit'],abs=1e-7)
        assert math.fsum(r['contribution'] for r in a['rows'])==pytest.approx(a['authoritative'],abs=1e-7)
        assert raw==canonical_results[kind]
    rows=prepare(canonical_results['derivatives'])['intelligence']['attribution']['rows']
    derivative=next(r for r in rows if r['instrument_type']=='DERIVATIVE')
    assert derivative['position_status']=='CLOSED' and derivative['realized']!=0 and derivative['unrealized']==0


@pytest.mark.parametrize('kind',['open_derivatives','missing_price','unsupported_action'])
def test_incomplete_attribution_never_zero_fills(canonical_results,kind):
    m=prepare(canonical_results[kind],date(2026,9,30));a=m['intelligence']['attribution']
    assert not a['available'] and a['authoritative'] is None
    if kind=='open_derivatives':
        assert all(r['unrealized'] is None and r['contribution'] is None for r in a['rows'] if r['instrument_type']=='DERIVATIVE')
        assert a['components'][1]['value'] is not None


def test_attribution_does_not_hide_residual_or_duplicate_security(canonical_results):
    m=prepare(canonical_results['demo'],date(2026,9,30));scoped=deepcopy(m['raw'])
    m['full_totals']['profit']+=1
    assert not attribution(m,scoped)['available']
    m=prepare(canonical_results['demo'],date(2026,9,30));scoped['wealth_contribution'].append(deepcopy(scoped['wealth_contribution'][0]))
    a=attribution(m,scoped);assert not a['available']
    assert a['rows'][0]['contribution'] is None


@pytest.mark.parametrize('values,expected',[([1,1,1,1],4),([99,1],1/(.99**2+.01**2)),([.0001],1),([1,2,3],36/14),([],None)])
def test_effective_holdings_unrounded_concentration(values,expected):
    from test_composition import result,DAY
    r=result(values);c=composition(r,DAY)
    assert c['effective_holdings']==pytest.approx(expected) if expected is not None else c['effective_holdings'] is None
    if values:
        r['holdings'][0]['live_current_value_eur']=None
        assert composition(r,DAY)['effective_holdings'] is None


def history_model():
    dates=pd.to_datetime(['2025-12-30','2025-12-31','2026-01-01','2026-01-02'])
    nav=pd.DataFrame(dict(date=dates,stockfund_value_eur=[100,220,170,190],external_flow_to_stockfund_eur=[100,100,-50,0],
        internal_dividend_reinvestment_flow_eur=[0,0,0,4],net_dividend_return_eur=[0,0,0,10],
        twr_neutralizing_flow_eur=[100,100,-50,4],price_coverage_pct=100,
        benchmark_price_total_return_eur=[100,110,88,99],benchmark_daily_return_pct=[None,10,-20,12.5],
        benchmark_pme_value_eur=[100,9000,1,190]))
    levels=np.array([100,120,120,130.8]);nav['stockfund_nav_index']=levels;nav['daily_twr_return_pct']=[None,20,0,9]
    nav['stockfund_drawdown_pct']=(levels/np.maximum.accumulate(levels)-1)*100
    metrics=dict(stockfund_twr_since_inception_pct=30.8,maximum_drawdown_pct=0,current_drawdown_pct=0,historical_analytics_status='OK')
    model=dict(nav=nav,dependencies=dict(stock_accounting=True),returns_scope_label='Stocks & funds',drawdown=drawdown(nav,metrics,'Stocks & funds'),by_key={'benchmark':SimpleNamespace(value=10)},raw={})
    ledger=[]
    for dt,kind,amount in [(dates[0],'BUY',100),(dates[1],'BUY',100),(dates[2],'SELL',50),(dates[3],'DIVIDEND_REINVESTMENT',4)]:
        ledger.append(dict(event_date=dt.isoformat(),asset_scope='STOCK_FUND',cashflow_type=kind,acquisition_outflow_eur=amount if kind!='SELL' else 0,core_recovery_eur=amount if kind=='SELL' else 0))
    ledger.append(dict(event_date=dates[3].isoformat(),asset_scope='INCOME',cashflow_type='DIVIDEND_NET',core_recovery_eur=10))
    return model,dict(lifetime_cashflow_ledger=ledger)


def test_deployment_reconciles_external_capital_and_distributions_separately():
    m,r=history_model();c=capital_bridge(m,r)
    assert c['available'] and [x['capital_eur'] for x in c['series']]==[100,200,150,150]
    assert c['years'][1]['beginning_wealth']==220
    assert c['years'][1]['wealth_gain_loss']==20
    assert c['years'][1]['investment_result_including_dividends']==26
    for y in c['years']:
        assert y['beginning_wealth']+y['net_external_invested']+y['investment_result_including_dividends']-y['net_distributions_outside_sleeve']==y['ending_wealth']


@pytest.mark.parametrize('problem',['wrong_flow','weekend_income','gap','coverage','duplicate','opening_only','accounting','unknown_flow','missing'])
def test_capital_bridge_fails_closed_on_unreconciled_inputs(problem):
    m,r=history_model()
    if problem=='wrong_flow':r['lifetime_cashflow_ledger'][0]['acquisition_outflow_eur']+=1
    if problem=='weekend_income':r['lifetime_cashflow_ledger'][-1]['event_date']='2026-01-03'
    if problem=='gap':m['nav']=m['nav'].drop(1)
    if problem=='coverage':m['nav'].loc[1,'price_coverage_pct']=90
    if problem=='duplicate':m['nav'].loc[1,'date']=m['nav'].loc[0,'date']
    if problem=='opening_only':r['lifetime_cashflow_ledger']=[];m['nav'].loc[:,['external_flow_to_stockfund_eur','internal_dividend_reinvestment_flow_eur','twr_neutralizing_flow_eur','net_dividend_return_eur']]=0
    if problem=='accounting':m['dependencies']['stock_accounting']=False
    if problem=='unknown_flow':r['lifetime_cashflow_ledger'][0]['cashflow_type']='UNKNOWN_TRANSFER'
    if problem=='missing':m['nav'].loc[1,'stockfund_value_eur']=None
    assert not capital_bridge(m,r)['available']


def test_benchmark_drawdown_ignores_pme_contribution_balance():
    m,_=history_model();risk=benchmark_risk(m)
    assert risk['available'] and risk['maximum']==pytest.approx(-20) and risk['current']==pytest.approx(-10)
    assert risk['underwater_days']==2
    assert all(r['portfolio_drawdown_pct']==0 for r in risk['series'])
    m['nav']['benchmark_pme_value_eur']=[1,2,3,4]
    assert benchmark_risk(m)==risk


@pytest.mark.parametrize('problem',['price','returns','gap','portfolio','identity','currency_validation','discontinuous','price_only'])
def test_benchmark_risk_rejects_unreliable_series(problem):
    m,_=history_model()
    if problem=='price':m['nav'].loc[1,'benchmark_price_total_return_eur']=None
    if problem=='returns':m['nav'].loc[1,'benchmark_daily_return_pct']=9
    if problem=='gap':m['nav']=m['nav'].drop(1)
    if problem=='portfolio':m['drawdown']['available']=False
    if problem=='identity':m['raw']['benchmark_identity']={'warning':'Instrument unsuitable'}
    if problem=='currency_validation':m['by_key']['benchmark'].value=None
    if problem=='discontinuous':m['drawdown']['series'][1]['date']='2026-01-09'
    if problem=='price_only':m['raw']['benchmark_identity']={'warning':'Price-only index'}
    assert not benchmark_risk(m)['available']


def exposure_model():
    m=dict(raw=dict(synthetic=False,active_derivatives=[dict(isin='ZZ0000000007',security_name='Call $10')],
        derivative_ledger=[dict(isin='ZZ0000000007',description_clean='Call auf Fictional Company')]),
        holdings=[dict(isin='ZZSTOCK00001',value=100,open_pl=20)],dependencies=dict(stock_accounting=True,ecosystem_accounting=True),
        manual_derivatives=dict(rows=[dict(isin='ZZ0000000007',name='Fictional call',quantity=2,value=20,confirmed=True)]),
        full_totals=dict(value=200),report_status=dict(full_complete=True))
    r=dict(holdings=[dict(isin='ZZSTOCK00001',asset_class='STOCK',position_status='ACTIVE',ticker_match_status='EXACT_ISIN',ticker_matched_name='Fictional Company',security_name='Fictional Company'),dict(isin='ZZFUND000001',asset_class='FUND',position_status='ACTIVE')])
    return m,r


def test_allocated_underlying_value_not_notional_or_etf_lookthrough():
    m,r=exposure_model();e=underlying_exposure(m,r)
    assert len(e['rows'])==1 and e['rows'][0]['allocated_market_value']==120
    assert e['rows'][0]['full_portfolio_share_pct']==60 and e['funds_excluded']==1
    assert 'NOT delta exposure' in e['note']
    m['manual_derivatives']['rows'][0]['confirmed']=False
    e=underlying_exposure(m,r);assert e['rows'][0]['allocated_market_value'] is None
    m['report_status']['full_complete']=False
    assert all(row['full_portfolio_share_pct'] is None for row in underlying_exposure(m,r)['rows'])


def test_unreliable_or_similar_underlying_names_not_merged():
    m,r=exposure_model();m['raw']['derivative_ledger'][0]['description_clean']='Call auf Fictional Other Company'
    assert len(underlying_exposure(m,r)['rows'])==2
    m['raw']['derivative_ledger']=[];e=underlying_exposure(m,r)
    assert len(e['unmapped'])==1 and len(e['rows'])==1


def income_model():
    h=dict(isin='ZZSTOCK00001',position_status='ACTIVE',asset_class='STOCK',security_name='Invented dividend payer',current_quantity=2)
    p=dict(isin=h['isin'],current_quantity=2,external_dividend_status='EXTERNAL_HISTORY_FOUND',growth_adjusted_forward_12m_net_dividend_eur=8)
    m=dict(raw=dict(forward_dividends=dict(growth_assumption_pct=0,projection=[p],forecast=[dict(isin=h['isin'],method='EXTERNAL_HISTORY',ex_dividend_date='2027-01-01')])),
        forward_dividends=dict(available=True,status='Known estimate',asof=date(2026,10,27)),holdings=[dict(isin=h['isin'],open_pl=30,value=200,basis=100)],
        composition=dict(valued_count=1,active_count=1),by_key=dict(income=SimpleNamespace(value=9)))
    receipts=[dict(gross_dividend_eur=10,net_dividend_eur=8,reconciliation_error=0,foreign_withholding_tax_eur=None,foreign_withholding_source='NOT_SEPARATELY_CLASSIFIED_FOR_ORDINARY_CASH_DIVIDEND')]
    return m,dict(holdings=[h],dividends=receipts)


def test_forward_yield_cohort_cost_and_observed_retention_exclude_interest():
    m,r=income_model();i=income_intelligence(m,r)
    assert i['complete'] and i['eligible_current_yield_pct']==4 and i['eligible_yield_on_cost_pct']==8
    assert i['complete_portfolio_yield_pct']==4 and i['observed_net_retention_pct']==80
    assert i['observed_gross_minus_net']==2 and i['separately_identified_foreign_withholding'] is None
    r['holdings'].append(dict(r['holdings'][0],isin='ZZUNKNOWN001'))
    i=income_intelligence(m,r);assert not i['complete'] and i['complete_portfolio_yield_pct'] is None and i['eligible_current_yield_pct']==4
    assert i['rows'][1]['known_net'] is None


@pytest.mark.parametrize('problem',['unknown','stale','quantity','reinvestment_basis','old_entitlement','seasonality','missing_date','missing_price','growth','expired'])
def test_yield_unknown_evidence_not_zero(problem):
    m,r=income_model();source=m['raw']['forward_dividends'];p=source['projection'][0]
    if problem=='unknown':p['growth_adjusted_forward_12m_net_dividend_eur']=None
    if problem in {'stale','expired'}:m['forward_dividends']['available']=False
    if problem=='quantity':p['current_quantity']=1
    if problem=='reinvestment_basis':m['holdings'][0]['basis']=None
    if problem=='old_entitlement':source['forecast'][0]['ex_dividend_date']='2026-01-01'
    if problem=='seasonality':source['forecast'][0]['method']='BROKER_RECEIPT_SEASONALITY_ESTIMATE'
    if problem=='missing_date':source['forecast'][0]['ex_dividend_date']=None
    if problem=='missing_price':m['holdings'][0]['open_pl']=None
    if problem=='growth':source['growth_assumption_pct']=4
    i=income_intelligence(m,r)
    if problem=='reinvestment_basis':assert i['eligible_current_yield_pct']==4 and i['eligible_yield_on_cost_pct'] is None
    else:assert i['eligible_current_yield_pct'] is None and i['known_net'] is None


def test_known_zero_distribution_refunds_and_withholding_subtotal():
    m,r=income_model();m['raw']['forward_dividends']['projection'][0]['growth_adjusted_forward_12m_net_dividend_eur']=0
    r['dividends'][0].update(net_dividend_eur=11,foreign_withholding_tax_eur=1,foreign_withholding_source='EXPLICIT_SOURCE')
    i=income_intelligence(m,r);assert i['complete_portfolio_yield_pct']==0 and i['observed_net_retention_pct']==pytest.approx(110)
    assert i['separately_identified_foreign_withholding']==1 and i['observed_gross_minus_net']==-1


def test_all_outputs_scope_switching_no_mutation_or_network(canonical_results,monkeypatch):
    import requests
    monkeypatch.setattr(requests.sessions.Session,'request',lambda *a,**k:pytest.fail('Post-processing attempted network access'))
    original=deepcopy(canonical_results['demo'])
    for scope in ('stocks_funds','full_portfolio'):
        m=prepare(canonical_results['demo'],date(2026,9,30),scope);figures=[x.value for x in m['metrics']]
        h=html_report(m).decode();pdf=summary_pdf(m)
        assert len(PdfReader(BytesIO(pdf)).pages)==1 and len(figures)==8
        assert 'Portfolio Intelligence · optional' in h and 'data-capital-toggle' in h
        digest=base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode();assert 'sha256-'+digest in h
        assert 'data-intel-sort' in h and 'connect-src &#x27;none&#x27;' in h
        assert not '<script src=' in h and 'https://cdn' not in h
        m['intelligence']['attribution']['rows'][0]['name']='<img src=x onerror=alert(1)>'
        escaped=intelligence_html(m);assert '<img src=x' not in escaped and '&lt;img src=x' in escaped
        assert [x.value for x in m['metrics']]==figures
    assert original==canonical_results['demo']


def test_optional_dashboard_controls_do_not_run_engine_or_change_batch(canonical_results,monkeypatch,tmp_path):
    from test_private_review_ui import app_for
    app=app_for((fixture('open_derivatives')[0],canonical_results['open_derivatives']),monkeypatch,tmp_path)
    monkeypatch.setattr('pulse.runner.run_analysis',lambda *a,**k:pytest.fail('Presentation control reran the engine'))
    before=[m.value for m in app.metric]
    app.checkbox(key='intelligence_show').check().run()
    assert not app.exception and len(app.metric)==8 and [m.value for m in app.metric]==before
    assert any(e.label=='Profit Attribution' for e in app.expander)
    app.checkbox(key='intelligence_capital').check().run()
    assert not app.exception and len(app.metric)==8
    app.segmented_control(key='performance_view').set_value('Drawdown (%)').run()
    assert not app.exception and any(b.label=='Save and update portfolio' for b in app.button)
    app.radio(key='analysis_scope').set_value('Full portfolio').run()
    assert not app.exception and len(app.metric)==8
    next(b for b in app.button if b.label=='Clear session results').click().run()
    assert not app.exception and not app.session_state.to_dict().get('intelligence_show')


def test_polished_drawdown_legend_and_cash_interest_are_display_only(canonical_results):
    from pulse.charts import drawdown_legend, BENCHMARK_COLOR
    from pulse.html_report import drawdown_chart,wealth_chart
    m=prepare(canonical_results['demo'],date(2026,9,30),'full_portfolio')
    original=deepcopy(m['intelligence']);a=m['intelligence']['attribution']
    cash=next(r['value'] for r in a['components'] if r['component']=='Recognized net interest')
    assert sum(r['contribution'] for r in a['groups'])+cash==pytest.approx(a['authoritative'],abs=1e-7)
    h=drawdown_chart(m)
    assert h.index('drawdown-legend')<h.index('<svg')
    assert 'Portfolio Drawdown' in h and 'Benchmark Drawdown' in h
    assert f'stroke="{BENCHMARK_COLOR}"' in h and 'stroke-dasharray="8 5"' in h
    assert 'fill-opacity=".11"' in h and 'role="listitem"' in h
    assert f'stroke="{BENCHMARK_COLOR}"' in wealth_chart(m)
    view=intelligence_html(m)
    assert 'Non-security cash interest' in view and f'€{cash:,.2f}' in view
    assert m['intelligence']==original
    m['intelligence']['benchmark_risk']['available']=False
    assert 'Benchmark Drawdown' not in drawdown_legend(m)


def test_drawdown_legend_uses_selected_verified_identity_and_escapes():
    from pulse.charts import drawdown_legend
    m=dict(raw=dict(benchmark_enabled=True,benchmark_ticker='IWDA.AS'),intelligence=dict(benchmark_risk=dict(available=True)))
    assert 'MSCI World Benchmark Drawdown (IWDA.AS)' in drawdown_legend(m)
    m['raw'].update(benchmark_ticker='CUSTOM',benchmark_name='<img src=x onerror=1>')
    h=drawdown_legend(m);assert '<img' not in h and '&lt;img' in h and 'CUSTOM' in h
