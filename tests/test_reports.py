"""Display/export regressions use only invented portfolios and evidence."""
from copy import deepcopy
from datetime import date
from html.parser import HTMLParser
from io import BytesIO, StringIO
import csv
import hashlib
import base64
from pathlib import Path
import pytest
from pypdf import PdfReader
from pulse.runner import run_analysis
from pulse.synthetic import fixture, row
from pulse.adapter import prepare
from pulse.pdf import summary_pdf, fit_size
from pulse.html_report import html_report, SCRIPT
from pulse.reporting import holding_cards
from worker_hooks import trade_amount_audit


@pytest.fixture(scope='module')
def report_results():
    return {kind:run_analysis(data=data,prices=prices) for kind in
            ('demo','stocks','etf_only','derivatives','open_derivatives','missing_price','unsupported_action')
            for data,prices in [fixture(kind)]}


class Elements(HTMLParser):
    def __init__(self):
        super().__init__();self.elements=[];self.text=[]
    def handle_starttag(self,tag,attrs):self.elements.append((tag,dict(attrs)))
    def handle_data(self,data):self.text.append(data)


@pytest.mark.parametrize('kind',['demo','stocks','etf_only','derivatives','open_derivatives','missing_price','unsupported_action'])
def test_two_reports_same_selected_model_and_figures(kind,report_results):
    raw=deepcopy(report_results[kind]);raw['synthetic']=False
    for scope in ('stocks_funds','full_portfolio'):
        model=prepare(raw,date(2026,10,4),scope);original=deepcopy(raw)
        html=html_report(model).decode();pdf=PdfReader(BytesIO(summary_pdf(model)))
        assert len(pdf.pages)==1
        parsed=Elements();parsed.feed(html)
        metrics={a['data-metric']:a for tag,a in parsed.elements if 'data-metric' in a}
        assert len(metrics)==8
        pdf_text=pdf.pages[0].extract_text()
        for m in model['metrics']:
            assert m.display in ''.join(parsed.text)
            assert m.display.replace('€','EUR ') in pdf_text
            assert (float(metrics[m.key]['data-value']) if 'data-value' in metrics[m.key] else None)==m.value
        for r in model['snapshot']['top_holdings'][:5]:
            assert f"EUR {r['value']:,.2f}" in pdf_text
            assert f"€{r['value']:,.2f}" in ''.join(parsed.text)
        assert model['scope_label'] in ''.join(parsed.text) and model['scope_label'] in pdf_text
        assert raw==original


@pytest.mark.parametrize('kind',['stocks','etf_only','derivatives'])
def test_complete_full_portfolio_automatic_without_derivative_warnings(kind,report_results):
    raw=deepcopy(report_results[kind]);raw['synthetic']=False
    m=prepare(raw,date(2026,10,4))
    assert m['analysis_scope']=='full_portfolio' and m['health']=='Complete'
    assert all(metric.value is not None for metric in m['metrics'])
    assert m['by_key']['value'].value==raw['lifetime_metrics']['lifetime_current_tracked_open_value_eur']
    assert m['by_key']['profit'].value==raw['lifetime_metrics']['lifetime_economic_profit_eur']
    assert not m['issues'] and m['dependencies']['missing_derivative']==0
    assert 'Missing derivative valuations' not in PdfReader(BytesIO(summary_pdf(m))).pages[0].extract_text()


def test_html_offline_allowlist_and_private_state_exclusion(report_results):
    raw=deepcopy(report_results['demo'])
    for key in ('market_capture','private_config','event_registry','transaction_export'):
        raw[key]={'secret':'PRIVATE_WORKER_STATE_NOT_FOR_EXPORT'}
    m=prepare(raw,date(2026,10,4));html=html_report(m).decode()
    assert 'PRIVATE_WORKER_STATE_NOT_FOR_EXPORT' not in html
    assert 'synthetic-demo-0' not in html and 'ZZ0000000001' not in html
    parsed=Elements();parsed.feed(html)
    assert sum(tag=='svg' for tag,_ in parsed.elements)==3
    assert len([a for tag,a in parsed.elements if 'data-period' in a])==5
    for tag,attrs in parsed.elements:
        assert tag not in {'iframe','object','embed','link','img','form','base'}
        assert not any(k in {'src','href','xlink:href','srcdoc'} or k.startswith('on') for k in attrs)
    assert 'fetch(' not in html and 'XMLHttpRequest' not in html and 'eval(' not in html
    assert SCRIPT in html
    digest=base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
    csp=next(a['content'] for tag,a in parsed.elements if a.get('http-equiv')=='Content-Security-Policy')
    assert "connect-src 'none'" in csp and 'sha256-'+digest in csp
    assert 'script-src \'unsafe-inline\'' not in csp


@pytest.mark.parametrize('payload',[
    '<script>window.injected=true</script>',
    '</script><img src="https://invalid.example/leak" onerror="window.injected=true">',
    '<svg onload="window.injected=true"><a href="javascript:alert(1)">x</a></svg>',
    'data:text/html,<script>alert(1)</script>',
    '" onmouseover="window.injected=true',
])
def test_uploaded_text_never_becomes_executable(payload,report_results):
    m=prepare(deepcopy(report_results['demo']),date(2026,10,4))
    m['snapshot']['top_holdings'][0]['name']=payload
    m['holdings'][0]['name']=payload;m['insights']=[payload];m['issues']=[payload]
    m['raw']['benchmark_name']=payload
    html=html_report(m).decode();parsed=Elements();parsed.feed(html)
    assert payload in ''.join(parsed.text)
    assert sum(t=='script' for t,_ in parsed.elements)==1
    assert not any(t in {'img','a'} or any(k.startswith('on') for k in a) for t,a in parsed.elements)
    assert payload not in holding_cards([dict(name=payload,value=12,weight_pct=50)])


def test_distinct_exports_and_sessions_clear_together(report_results,monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.delenv('PULSE_ENABLE_UPLOADS',raising=False);monkeypatch.setenv('FOLIOLENS_MODE','public_demo')
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py'),default_timeout=30)
    model=prepare(deepcopy(report_results['demo']),date(2026,10,4))
    app.session_state['model']=model;app.session_state['pdf']=b'old';app.session_state['html']=b'old'
    app.run();assert not app.exception
    assert app.session_state['pdf'].startswith(b'%PDF') and app.session_state['html'].startswith(b'<!doctype html>')
    first=app.session_state['html'];assert first!=html_report(model)
    other=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py'),default_timeout=30)
    other.session_state['model']=prepare(deepcopy(report_results['stocks']),date(2026,10,4));other.run()
    other_pdf,other_html=other.session_state['pdf'],other.session_state['html']
    next(b for b in app.button if b.label=='Clear session results').click().run()
    for key in ('model','pdf','html','export_schema'):assert key not in app.session_state
    assert other.session_state['pdf']==other_pdf and other.session_state['html']==other_html
    assert not other.exception


def test_long_holdings_large_values_and_unavailable_pdf(report_results):
    m=prepare(deepcopy(report_results['demo']),date(2026,10,4))
    for r in m['snapshot']['top_holdings'][:5]:
        r.update(name='Synthetic Worldwide Diversified Equities Exchange Traded Accumulating Fund '+('LongName'*12),value=1234567890123.45)
    pdf=PdfReader(BytesIO(summary_pdf(m)))
    assert len(pdf.pages)==1 and '1,234,567,890,123.45' in pdf.pages[0].extract_text()
    from reportlab.pdfbase.pdfmetrics import stringWidth
    value='EUR 1,234,567,890,123.45';size=fit_size(value,131)
    assert stringWidth(value,'Helvetica-Bold',size)<=131.001 and size>=9
    m['snapshot']['top_holdings']=[]
    assert 'No supported holding ranking available' in PdfReader(BytesIO(summary_pdf(m))).pages[0].extract_text()


def test_missing_amount_ipo_evidence_remains_warning():
    data,prices=fixture('stocks');rows=list(csv.DictReader(StringIO(data.decode())))
    rows[0]['amount']=''
    subscription=row('2025-01-05','IPO_SUBSCRIPTION',amount=-220,fee=-1,category='TRADING')
    subscription['transaction_id']='fictional-ipo-cash-evidence'
    rows.insert(0,subscription);out=StringIO();writer=csv.DictWriter(out,fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
    raw=run_analysis(out.getvalue().encode(),prices=prices)
    assert raw['trade_amount_audit']==dict(missing_count=1,ipo_cash_reconciled=1,quantity_price_inferred=0,other_missing=0)
    assert next(r for r in raw['pre_diagnostics'] if r['check']=='missing_trade_amount_rows')['severity']=='WARNING'
    assert raw['holdings'][0]['total_acquisition_cost_basis_original_eur']==pytest.approx(222)
    m=prepare(raw,date(2026,10,4))
    assert any('reconciled to IPO subscription cash and fees' in s for s in m['issues'])
    assert m['by_key']['value'].value is not None and m['by_key']['profit'].value is not None


def test_genuine_missing_amount_is_not_described_as_reconciled():
    data,prices=fixture('stocks');rows=list(csv.DictReader(StringIO(data.decode())));rows[0]['amount']=''
    out=StringIO();writer=csv.DictWriter(out,fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
    raw=run_analysis(out.getvalue().encode(),prices=prices)
    assert raw['trade_amount_audit']['ipo_cash_reconciled']==0 and raw['trade_amount_audit']['quantity_price_inferred']==1
    assert any('verify broker cash' in s for s in prepare(raw)['issues'])


@pytest.mark.parametrize('verified',[True,False])
def test_sidebar_review_only_for_unresolved_events(monkeypatch,tmp_path,verified):
    from types import SimpleNamespace
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    data,_=fixture('unsupported_action')
    from pulse.event_review import review
    candidate=review(data,{})[0];candidate['verified']=verified
    monkeypatch.setenv('FOLIOLENS_MODE','personal')
    monkeypatch.setenv('FOLIOLENS_CONFIG_DIRECTORY',str(tmp_path))
    monkeypatch.setattr(st,'file_uploader',lambda *a,**kw:SimpleNamespace(getvalue=lambda:data))
    monkeypatch.setattr('pulse.event_review.review',lambda *a,**kw:[candidate])
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py')).run()
    assert not app.exception
    labels=[e.label for e in app.expander]
    assert 'Private event / valuation review' not in labels
    assert ('Action required: review transaction' in labels)==(not verified)
    assert not app.text_area
    assert len(app.checkbox)==(0 if verified else 1)


def test_scope_switch_regenerates_both_downloads(report_results,monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('FOLIOLENS_MODE','personal')
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py'))
    raw=deepcopy(report_results['open_derivatives']);raw['synthetic']=False
    app.session_state['model']=prepare(raw,date(2026,10,4),'stocks_funds');app.run()
    before=app.session_state['html']
    app.radio[0].set_value('Full portfolio').run();assert not app.exception
    assert app.session_state['html']!=before and app.session_state['model']['analysis_scope']=='full_portfolio'
    text=PdfReader(BytesIO(app.session_state['pdf'])).pages[0].extract_text()
    assert 'Analysis: Full portfolio' in text and 'Analysis: Full portfolio' in app.session_state['html'].decode()
    assert app.metric[0].value=='Unavailable' and app.metric[1].value=='Unavailable'


def test_one_reconciled_ipo_does_not_hide_other_zero_cost_buys(report_results):
    raw=deepcopy(report_results['stocks'])
    raw['trade_amount_audit']=dict(missing_count=1,ipo_cash_reconciled=1,quantity_price_inferred=0,other_missing=0)
    raw['pre_diagnostics'] += [dict(check='missing_trade_amount_rows',value=1,severity='WARNING'),
                               dict(check='zero_or_missing_cost_buy_rows',value=2,severity='WARNING')]
    m=prepare(raw,date(2026,10,4))
    assert 'zero or missing cost buy rows: 2' in m['issues']
    from pulse.reporting import warning_summary
    raw['trade_amount_audit'].update(missing_count=2,quantity_price_inferred=1)
    assert 'other missing amounts still require source review' in warning_summary(prepare(raw,date(2026,10,4)))
