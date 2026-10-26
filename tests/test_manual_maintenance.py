"""Synthetic maintenance coverage; no real portfolios or market observations."""
from copy import deepcopy
from datetime import date
from io import BytesIO,StringIO
from pathlib import Path
import csv
import pytest
from pulse.derivative_display import derivative_label,manual_positions,table_rows
from pulse.synthetic import fixture
from pulse.private_config import export_digest
from pulse.runner import run_analysis
from pulse.adapter import prepare
from pulse.html_report import html_report
from pulse.pdf import summary_pdf
from pypdf import PdfReader

DAY=date(2026,9,30)


@pytest.fixture(scope='module')
def reports():
    data,prices=fixture('open_derivatives')
    isins=sorted({r['symbol'] for r in csv.DictReader(StringIO(data.decode())) if r['asset_class']=='DERIVATIVE'})
    def run(count,price=1.25,observed='2026-09-30'):
        quotes={i:dict(price_eur=price,date=observed,source='SYNTHETIC software-test input; not a market quotation') for i in isins[:count]}
        return run_analysis(data,prices=prices,private_config=dict(export_sha256=export_digest(data),derivative_quotes=quotes))
    return dict(empty=run(0),mixed=run(3),complete=run(7),higher=run(7,2.25),stale=run(7,observed='2026-09-01'))


def test_names_use_exact_descriptions_and_never_guess_expiry_or_turbo_level():
    pos=dict(isin='ZZ0000000016',security_name='Call 220 $',derivative_kind='Warrant',direction='CALL')
    ledger=[dict(isin=pos['isin'],description_clean='Buy Optionsschein auf Fictional Company, quantity 2')]
    assert derivative_label(pos,ledger)=='Fictional Company — Call $220 — Expiry unavailable — '+pos['isin']
    assert derivative_label(pos,[dict(ledger[0],isin='ZZ0000000024')])==pos['security_name']+' · '+pos['isin']
    assert derivative_label(dict(pos,derivative_kind='Turbo'),ledger)=='Fictional Company — Long Turbo — Expiry unavailable — '+pos['isin']
    metadata=dict(isin=pos['isin'],identity='EXACT_ISIN',underlying='Verified Company',maturity='2028-01-21')
    assert '21 Jan 2028' in derivative_label(pos,ledger,metadata)
    assert 'Verified Company' not in derivative_label(pos,ledger,dict(metadata,isin='ZZ0000000024'))
    conflicting=ledger+[dict(ledger[0],description_clean='Optionsschein auf Different Company')]
    assert derivative_label(pos,conflicting)==pos['security_name']+' · '+pos['isin']


@pytest.mark.parametrize('case,count',[('empty',0),('mixed',3),('complete',7),('stale',0)])
def test_coverage_projects_canonical_value_and_original_freshness(reports,case,count):
    raw=deepcopy(reports[case]);before=deepcopy(raw);coverage=manual_positions(raw,DAY)
    assert coverage['active']==7 and coverage['confirmed']==count and raw==before
    rows=table_rows(coverage);assert len(rows)==7
    assert sum(r['Position value']!='Unavailable' for r in rows)==count
    if case=='complete':assert manual_positions(raw,date(2026,10,8))['confirmed']==0
    closed=deepcopy(raw['active_derivatives'][0]);closed['position_status']='CLOSED';raw['active_derivatives'].append(closed)
    assert manual_positions(raw,DAY)['active']==7


def test_all_manual_inputs_restore_full_totals_without_changing_stock_performance(reports):
    complete=prepare(reports['complete'],DAY,'full_portfolio');higher=prepare(reports['higher'],DAY,'full_portfolio')
    assert complete['dependencies']['full_valued'] and complete['manual_derivatives']['confirmed']==7
    assert complete['by_key']['value'].value is not None and complete['by_key']['profit'].value is not None
    units=sum(r['current_quantity'] for r in reports['complete']['active_derivatives'])
    assert higher['full_totals']['value']-complete['full_totals']['value']==pytest.approx(units)
    assert higher['full_totals']['profit']-complete['full_totals']['profit']==pytest.approx(units)
    assert complete['full_totals']['value']==pytest.approx(complete['dependencies']['stock_value']+sum(r['value'] for r in complete['manual_derivatives']['rows']))
    for key in ('historical_metrics','daily_nav_history','period_performance','dividends','interest','derivative_ledger'):
        assert reports['empty'][key]==reports['complete'][key]
    assert {k:v for k,v in reports['empty']['advanced_metrics'].items() if k.startswith('stock_fund_')}=={k:v for k,v in reports['complete']['advanced_metrics'].items() if k.startswith('stock_fund_')}
    for name in ('empty','mixed','stale'):
        assert prepare(reports[name],DAY,'full_portfolio')['full_totals']['value'] is None


def test_complete_prices_do_not_erase_other_data_health_or_accounting_warnings(reports):
    raw=deepcopy(reports['complete']);raw['historical_metrics']['historical_analytics_status']='OK_WITH_LOW_CONFIDENCE_FALLBACK'
    model=prepare(raw,DAY);assert model['health']=='Partial' and model['valuation_summary'].startswith('Current valuations: complete')
    assert any('estimate' in issue for issue in model['issues'])
    raw['accounting_status']='INCOMPLETE';raw['pre_diagnostics'].append(dict(check='unknown_transaction_rows',severity='BLOCKING'))
    blocked=prepare(raw,DAY,'full_portfolio')
    assert blocked['full_totals']['profit'] is None and 'accounting review' in blocked['valuation_summary']


def test_html_manual_table_is_escaped_and_pdf_stays_one_page(reports):
    raw=deepcopy(reports['complete'])
    raw['active_derivatives'][0]['quote_source']='<img src=https://invalid.example/x onerror=alert(1)>'
    model=prepare(raw,DAY,'full_portfolio');original=deepcopy(raw)
    text=html_report(model).decode();pdf=PdfReader(BytesIO(summary_pdf(model)))
    assert len(pdf.pages)==1 and 'Manual derivative valuations: 7/7' in pdf.pages[0].extract_text()
    assert 'Derivative positions and manual valuations' in text and '&lt;img' in text and '<img src=' not in text
    assert 'connect-src &#x27;none&#x27;' in text and 'Confirmed EUR/unit' in text
    assert all(m.display in text and m.display.replace('€','EUR ') in pdf.pages[0].extract_text() for m in model['metrics'])
    assert raw==original


def test_complete_scope_switch_updates_both_exports_without_engine(reports,monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('FOLIOLENS_MODE','personal')
    def forbidden(*a,**kw):raise AssertionError('Scope switching must not run accounting')
    monkeypatch.setattr('pulse.runner.run_analysis',forbidden)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py'))
    app.session_state['model']=prepare(deepcopy(reports['complete']),DAY,'stocks_funds');app.run();assert not app.exception
    stock_pdf=app.session_state['pdf'];stock_value=app.metric[0].value
    app.radio[0].set_value('Full portfolio').run();assert not app.exception
    assert app.metric[0].value!='Unavailable' and app.metric[0].value!=stock_value
    assert app.session_state['pdf']!=stock_pdf
    assert 'Analysis: Full portfolio' in app.session_state['html'].decode()
    assert 'Analysis: Full portfolio' in PdfReader(BytesIO(app.session_state['pdf'])).pages[0].extract_text()
    app.radio[0].set_value('Stocks & funds').run();assert not app.exception and app.metric[0].value==stock_value
