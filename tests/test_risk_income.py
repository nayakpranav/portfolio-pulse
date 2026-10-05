"""Canonical risk/forward reporting scenarios; exclusively fabricated inputs."""
from copy import deepcopy
from datetime import date
from io import BytesIO
import base64
import hashlib
import math
from pathlib import Path
import pandas as pd
import pytest
from pypdf import PdfReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from pulse.analytics import drawdown, forward_dividends, risk_summary, forward_display
from pulse.adapter import prepare
from pulse.synthetic import fixture
from pulse.runner import run_analysis
from pulse.pdf import compact_return, summary_pdf
from pulse.html_report import html_report, SCRIPT
from types import SimpleNamespace
import json


def history(levels):
    levels=pd.Series(levels,dtype=float)
    nav=pd.DataFrame({'date':pd.bdate_range('2026-09-01',periods=len(levels)),
                      'stockfund_nav_index':levels,'daily_twr_return_pct':levels.pct_change()*100,
                      'stockfund_drawdown_pct':(levels/levels.cummax()-1)*100,'price_coverage_pct':100})
    metrics=dict(stockfund_twr_since_inception_pct=levels.iloc[-1]-100,
                 maximum_drawdown_pct=nav.stockfund_drawdown_pct.min(),current_drawdown_pct=nav.stockfund_drawdown_pct.iloc[-1],historical_analytics_status='OK')
    return nav,metrics


@pytest.mark.parametrize('levels,maximum,current',[
    ([100,110,88,99],-20,-10),([100,90,110],-10,0),([100,95,80],-20,-20),
    ([100,100,100],0,0),([100,120,100,130],-100/6,0),([100,0,0],-100,-100)])
def test_drawdown_canonical_index_initial_peak(levels,maximum,current):
    nav,metrics=history(levels)
    if levels==[100,0,0]:nav.loc[2,'daily_twr_return_pct']=0
    r=drawdown(nav,metrics,'Stocks & funds')
    assert r['available'] and r['maximum']==pytest.approx(maximum) and r['current']==pytest.approx(current)
    assert all(row['drawdown_pct']<=0 for row in r['series'])
    assert nav.stockfund_nav_index.iloc[-1]-100==pytest.approx(metrics['stockfund_twr_since_inception_pct'])


@pytest.mark.parametrize('problem',['missing_return','missing_date','duplicate_date','unsorted_date','missing_index','missing_coverage','partial_coverage','wrong_risk','wrong_twr','wrong_initial','infinite','blocked'])
def test_drawdown_rejects_gaps_and_inconsistent_inputs(problem):
    nav,metrics=history([100,110,88,99])
    if problem=='missing_return':nav.loc[2,'daily_twr_return_pct']=None
    if problem=='missing_date':nav=nav.drop(2)
    if problem=='duplicate_date':nav.loc[2,'date']=nav.loc[1,'date']
    if problem=='unsorted_date':nav=nav.iloc[::-1]
    if problem=='missing_index':nav.loc[2,'stockfund_nav_index']=None
    if problem=='missing_coverage':nav.loc[2,'price_coverage_pct']=None
    if problem=='partial_coverage':nav.loc[2,'price_coverage_pct']=50
    if problem=='wrong_risk':nav.loc[2,'stockfund_drawdown_pct']=2
    if problem=='wrong_twr':metrics['stockfund_twr_since_inception_pct']=123
    if problem=='wrong_initial':nav.loc[0,'stockfund_nav_index']=101
    if problem=='infinite':nav.loc[2,'daily_twr_return_pct']=float('inf')
    assert not drawdown(nav,metrics,'Stocks & funds',problem!='blocked')['available']


def test_wealth_cash_flows_cannot_change_drawdown_and_estimates_remain_disclosed():
    nav,metrics=history([100,110,88,99]);nav['stockfund_value_eur']=[100,10000,1,700]
    nav['external_flow_to_stockfund_eur']=[100,9990,-9000,600]
    metrics['historical_analytics_status']='OK_WITH_LOW_CONFIDENCE_FALLBACK'
    r=drawdown(nav,metrics,'Unaffected stocks & funds (partial)')
    assert r['maximum']==pytest.approx(-20) and 'estimates' in r['note'] and 'partial' in r['scope']


@pytest.fixture(scope='module')
def results():
    return {kind:run_analysis(data,prices=prices) for kind in ('demo','stocks','etf_only','open_derivatives','missing_price','unsupported_action') for data,prices in [fixture(kind)]}


def test_canonical_daily_series_not_wealth_and_derivative_independence(results):
    a,b=prepare(results['stocks']),prepare(results['open_derivatives'])
    assert a['drawdown']['available'] and b['drawdown']['available']
    assert a['drawdown']==b['drawdown']
    nav=b['nav'];risk=b['drawdown']
    assert risk['maximum']==pytest.approx(b['raw']['historical_metrics']['maximum_drawdown_pct'])
    assert risk['current']==pytest.approx(nav.iloc[-1]['stockfund_drawdown_pct'])
    assert b['full_totals']['value'] is None
    demo=prepare(results['demo']);nav=demo['nav']
    withdrawals=nav['external_flow_to_stockfund_eur']<0
    assert withdrawals.any() and (nav.loc[withdrawals,'stockfund_value_eur']-nav['stockfund_value_eur'].shift(1).loc[withdrawals]).lt(0).any()
    assert demo['drawdown']['maximum']==0  # The sale-related wealth drop is not a TWR loss.


def test_canonical_dividend_field_mapping_and_no_historical_changes(results):
    raw=deepcopy(results['demo']);before=deepcopy(raw);m=prepare(raw)
    source=raw['forward_dividends'];f=m['forward_dividends']
    assert f['available'] and f['amount']==source['metrics']['forward_12m_estimated_net_known_subtotal_eur']
    assert source['growth_assumption_pct']==0 and f['covered_count']==6
    assert m['snapshot']['ytd_income']==62 and f['amount']==44
    assert all(r['category'] in {'DECLARED','ESTIMATED'} for r in source['forecast'])
    assert raw==before and 'excludes interest' in f['note']
    # Clearing the independent forecast cannot alter any established figures.
    no=deepcopy(raw);no.pop('forward_dividends');other=prepare(no)
    assert m['metrics']==other['metrics'] and m['snapshot']==other['snapshot'] and m['holdings']==other['holdings']


@pytest.mark.parametrize('case',['missing','partial_net','missing_position','all_unknown','blocked','stale','future','zero','accumulating','unknown_payment','review'])
def test_forward_coverage_missing_zero_and_reference_dates(results,case):
    raw=deepcopy(results['demo']);source=raw['forward_dividends'];metrics=source['metrics']
    if case=='missing':raw.pop('forward_dividends')
    if case=='partial_net':metrics['forward_12m_net_forecast_completeness']='PARTIAL'
    if case=='missing_position':source['projection'][0]['growth_adjusted_forward_12m_net_dividend_eur']=None
    if case=='all_unknown':
        metrics['forward_12m_estimated_net_known_subtotal_eur']=0;metrics['forward_12m_net_estimable_event_count']=0
        for p in source['projection']:p['growth_adjusted_forward_12m_net_dividend_eur']=None
    if case=='stale':metrics['asof']='2025-09-30'
    if case=='future':metrics['asof']='2027-09-30'
    if case=='zero':metrics['forward_12m_estimated_net_known_subtotal_eur']=0;metrics['forward_12m_net_estimable_event_count']=0
    if case=='accumulating':source['projection'][0].update(external_dividend_status='APPARENTLY_ACCUMULATING_NAME_AND_NO_HISTORY',growth_adjusted_forward_12m_net_dividend_eur=0)
    if case=='unknown_payment':metrics['pending_unknown_payment_date_events']=1
    if case=='review':metrics['receipt_association_review_events']=1
    r=forward_dividends(raw,date(2026,10,5),case!='blocked')
    if case in {'missing','all_unknown','blocked','stale','future'}:assert not r['available'] and r['amount'] is None
    elif case in {'partial_net','missing_position','unknown_payment','review'}:assert r['available'] and r['status']=='Partial coverage'
    elif case=='zero':assert r['available'] and r['amount']==0
    else:assert r['available']


def test_provider_timeout_does_not_change_accounting_or_publish_unknown_zero(results):
    data,prices=fixture('stocks');prices.pop('dividend_snapshots')
    failed=run_analysis(data,prices=prices)
    assert failed['lifetime_metrics']==results['stocks']['lifetime_metrics']
    assert failed['daily_nav_history']==results['stocks']['daily_nav_history']
    assert not prepare(failed)['forward_dividends']['available']


@pytest.mark.parametrize('value,kind',[(123.45,'decimal'),(-99.99,'decimal'),(123456789.12,'integer'),(1e100,'overflow'),(-1e100,'overflow'),(None,'missing'),(0,'decimal')])
def test_pdf_returns_measured_width_and_explicit_fallback(value,kind):
    width=(stringWidth('+123,456,789.12%','Helvetica-Bold',8)+stringWidth('+123,456,789%','Helvetica-Bold',8))/2+17 if kind=='integer' else 105
    label,arrow=compact_return(value,width)
    assert stringWidth(label,'Helvetica-Bold',8)+(17 if arrow else 0)<=width
    if kind=='decimal':assert label.endswith('.00%') if value==0 else label.endswith('45%') if value==123.45 else label.endswith('99%')
    if kind=='integer':assert label=='+123,456,789%'
    if kind=='overflow':assert label=='See HTML*' and not arrow
    if kind=='missing':assert label=='N/A' and not arrow


def test_reports_same_risk_forecast_precision_csp_and_intro_placement(results):
    model=prepare(results['demo']);html=html_report(model).decode();pdf=PdfReader(BytesIO(summary_pdf(model)))
    assert len(pdf.pages)==1
    text=pdf.pages[0].extract_text()
    assert risk_summary(model['drawdown']) in text and risk_summary(model['drawdown']) in html
    assert forward_display(model['forward_dividends'],'EUR ') in text
    assert forward_display(model['forward_dividends']) in html
    assert html.index('Badges and Return show unrealized return')<html.index('<article class="holding-card"')
    assert 'data-view="wealth" aria-controls="wealth-view" aria-pressed="true"' in html
    assert 'id="drawdown-view" hidden aria-hidden="true"' in html
    digest=base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
    assert digest in html and "[hidden]{display:none!important}" in html


def test_streamlit_toggle_and_session_isolation(results,monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('FOLIOLENS_MODE','public_demo')
    a,b=(AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py')) for _ in range(2))
    for app in (a,b):app.session_state['model']=prepare(deepcopy(results['demo']));app.run()
    controls=a.get('segmented_control');assert controls[0].value=='Wealth (EUR)'
    controls[0].set_value('Drawdown (%)').run();assert not a.exception
    assert any('Max TWR Drawdown' in x.value for x in a.markdown)
    assert b.get('segmented_control')[0].value=='Wealth (EUR)'
    next(x for x in a.button if x.label=='Clear session results').click().run()
    assert 'performance_view' not in a.session_state and 'model' not in a.session_state


@pytest.mark.parametrize('currency,fx,expected',[('USD',.8,14.4),('EUR',1,18),('GBp',.9,.162),('UNKNOWN',float('nan'),None)])
def test_reporting_only_canonical_net_retention_and_currency(tmp_path,monkeypatch,currency,fx,expected):
    from pulse.forward import forward_source
    symbol='ZZ0000000001'
    config=tmp_path/'prices.json'
    config.write_text(json.dumps({'dividend_snapshots':{symbol:dict(ticker=symbol,history=[{'ex_date':'2025-10-02','dps':2}],calendar={},currency=currency,history_status='OK',calendar_status='EMPTY_OR_UNAVAILABLE',retrieved_at='2026-09-30T00:00:00+00:00')}}))
    monkeypatch.setenv('PULSE_PRICE_INPUT',str(config));monkeypatch.delenv('FOLIOLENS_REPLAY_PATH',raising=False)
    ns=dict(combined=pd.DataFrame([dict(isin=symbol,security_name='Synthetic',asset_class='STOCK',position_status='ACTIVE',current_quantity=12,yahoo_ticker=symbol,live_price_currency=currency)]),
            trades=pd.DataFrame([dict(isin=symbol,event_date='2026-01-06',type_norm='BUY',signed_quantity=12)]),
            dividends=pd.DataFrame([dict(isin=symbol,payment_date='2026-02-16',gross_dividend_eur=12,net_dividend_eur=9)]),
            corporate_action_audit=pd.DataFrame(),dividend_projection_by_holding=pd.DataFrame([dict(isin=symbol)]),
            max_date=pd.Timestamp('2026-09-30'),yf=SimpleNamespace(Ticker=lambda ticker:pytest.fail('Synthetic forecasts cannot request Yahoo')),get_fx_to_eur=lambda c:fx)
    before=deepcopy(ns['dividends']);source=forward_source(ns)
    amount=source['metrics']['forward_12m_estimated_net_known_subtotal_eur']
    if expected is None:
        assert source['metrics']['forward_12m_net_estimable_event_count']==0
        assert source['forecast'][0]['estimated_net_dividend_eur'] is None
    else:assert amount==pytest.approx(expected)
    assert ns['dividends'].equals(before) and source['growth_assumption_pct']==0


def test_partial_scope_and_price_gaps_preserve_required_availability(results):
    blocked=prepare(results['unsupported_action'])
    assert not blocked['drawdown']['available'] and not blocked['forward_dividends']['available']
    missing=prepare(results['missing_price'])
    # Canonical filled prices are qualified; unavailable current value is not
    # an excuse to invent a drawdown or suppress independently sound history.
    if missing['drawdown']['available']:assert 'estimates' in missing['drawdown']['note']


def test_forecast_request_deadline_stops_without_provider_bypass(monkeypatch):
    import requests
    from yfinance._http import requests as yf_requests
    from pulse.market import install_transport_controls
    calls=[]
    def response(self,method,url,**kwargs):
        calls.append(kwargs['timeout']);return SimpleNamespace(status_code=200)
    for cls in {requests.sessions.Session,yf_requests.Session}:monkeypatch.setattr(cls,'request',response)
    state=install_transport_controls({'prefetch_openfigi_identities':lambda x:None})
    state['forecast_deadline']=0
    with pytest.raises(RuntimeError):requests.Session().get('https://query1.finance.yahoo.com/test')
    assert not calls
    monkeypatch.setattr('pulse.market.time.monotonic',lambda:100)
    state['forecast_deadline']=100.2
    requests.Session().get('https://query1.finance.yahoo.com/test')
    assert calls[-1]==pytest.approx(.2)
    state.pop('forecast_deadline')
    assert requests.Session().get('https://query1.finance.yahoo.com/test').status_code==200
