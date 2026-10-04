from copy import deepcopy
from datetime import date
from io import BytesIO, StringIO
import csv
import json
import pytest
from pypdf import PdfReader
from pulse.adapter import prepare
from pulse.runner import run_analysis
from pulse.synthetic import fixture, row
from pulse.pdf import summary_pdf


@pytest.fixture(scope='module')
def unpriced():
    data, prices = fixture('open_derivatives')
    return run_analysis(data, prices=prices)


def test_seven_unpriced_derivatives_preserve_independent_scopes(unpriced):
    original=deepcopy(unpriced)
    m=prepare(unpriced,date(2026,10,4),'stocks_funds')
    assert m['dependencies']['missing_derivative']==7
    assert all(v.value is not None for v in m['metrics'])
    assert m['by_key']['value'].value==pytest.approx(253)
    assert m['by_key']['profit'].value==pytest.approx(35)
    assert m['scope_label']=='Stocks & funds'
    assert m['full_totals']=={'value':None,'profit':None}
    assert not m['nav'].empty and m['periods'] and m['snapshot']['top_holdings'] and m['holdings']
    assert len(unpriced['derivative_ledger'])==8
    assert unpriced==original
    full=prepare(unpriced,analysis_scope='full_portfolio')
    assert all(full['by_key'][k].value is None for k in ('value','profit'))
    assert all(full['by_key'][k].value==m['by_key'][k].value for k in ('capital','recovery','mwr','benchmark','twr','income'))
    assert not full['nav'].empty and full['snapshot']['top_holdings']


def test_real_auto_scope_and_public_demo_scope(unpriced):
    r=deepcopy(unpriced);r['synthetic']=False
    assert prepare(r)['analysis_scope']=='stocks_funds'
    assert prepare(unpriced)['analysis_scope']=='full_portfolio'


def test_current_stock_gap_does_not_erase_reliable_history():
    data,prices=fixture('open_derivatives');prices['securities']['ZZ0000000001']['missing_current']=True
    r=run_analysis(data,prices=prices);m=prepare(r,analysis_scope='stocks_funds')
    assert m['by_key']['value'].value is None and m['by_key']['profit'].value is None
    assert m['by_key']['mwr'].value is None
    assert m['by_key']['capital'].value is not None and m['by_key']['recovery'].value is not None
    assert m['by_key']['twr'].value is not None and not m['nav'].empty
    assert m['by_key']['benchmark'].value is not None and m['by_key']['income'].value==5
    assert m['holdings'][0]['status']=='Unpriced' and m['holdings'][0]['value'] is None


def test_unknown_stock_event_blocks_affected_accounting_and_not_income(unpriced):
    data,prices=fixture('open_derivatives')
    rows=list(csv.DictReader(StringIO(data.decode())))
    event=row('2026-06-01','FREE_RECEIPT',shares=-11,category='DELIVERY');event['transaction_id']='FICTIONAL_UNKNOWN_EVENT'
    rows.append(event);out=StringIO();writer=csv.DictWriter(out,fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
    r=run_analysis(out.getvalue().encode(),prices=prices);m=prepare(r,analysis_scope='stocks_funds')
    assert r['accounting_status']=='BLOCKING'
    assert all(m['by_key'][k].value is None for k in ('value','profit','capital','recovery','mwr','benchmark','twr'))
    assert m['by_key']['income'].value==5
    assert m['nav'].empty and not m['periods'] and not m['holdings'] and not m['snapshot']['top_holdings']
    assert any('private verified event evidence' in issue for issue in m['issues'])
    text=PdfReader(BytesIO(summary_pdf(m))).pages[0].extract_text()
    assert 'private verified event evidence' in text and 'Missing derivative valuations' in text


def test_derivative_basis_block_does_not_discard_stock_accounting():
    data,prices=fixture('open_derivatives')
    # Explicit controlled extra sell beyond all supported derivative acquisition lots.
    rows=list(csv.DictReader(StringIO(data.decode())));extra=row('2026-07-01','SELL',i=7,asset='DERIVATIVE',shares=30,price=7,amount=210)
    extra['transaction_id']='FICTIONAL_DERIVATIVE_BASIS_GAP';rows.append(extra)
    out=StringIO();writer=csv.DictWriter(out,fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
    r=run_analysis(out.getvalue().encode(),prices=prices);m=prepare(r,analysis_scope='stocks_funds')
    assert r['accounting_status']=='BLOCKING'
    assert all(m['by_key'][k].value is not None for k in ('value','profit','mwr','benchmark','twr','income'))
    assert all(m['by_key'][k].value is None for k in ('capital','recovery'))
    assert not m['nav'].empty and m['holdings']


def test_unclassified_blocker_stays_fail_closed(unpriced):
    r=deepcopy(unpriced);r['accounting_status']='BLOCKING'
    m=prepare(r,analysis_scope='stocks_funds')
    assert m['dependencies']['unexplained_accounting']
    assert m['by_key']['mwr'].value is None and m['nav'].empty


def test_bad_history_and_benchmark_do_not_hide_current_holdings(unpriced):
    r=deepcopy(unpriced);r['historical_metrics']['historical_analytics_status']='INCOMPLETE_PRICE_HISTORY'
    r['historical_metrics']['benchmark_mwr_status']='MISSING_PRICE';r['historical_metrics']['benchmark_mwr_pct']=None
    m=prepare(r,analysis_scope='stocks_funds')
    assert m['by_key']['value'].value is not None and m['holdings']
    assert m['by_key']['mwr'].value is not None
    assert m['by_key']['twr'].value is None and m['by_key']['benchmark'].value is None
    assert m['nav'].empty and not m['periods'] and not m['snapshot']['trend']


def test_sleeve_profit_requires_independent_reconciliation(unpriced):
    r=deepcopy(unpriced);r['wealth_contribution'][0]['economic_contribution_eur']+=1
    m=prepare(r,analysis_scope='stocks_funds')
    assert m['by_key']['value'].value is not None
    assert m['by_key']['profit'].value is None and m['by_key']['profit'].status=='SLEEVE_RECONCILIATION_REQUIRED'
    assert any('did not reconcile' in issue for issue in m['issues'])


def test_unresolved_income_blocks_only_income_dependent_results(unpriced):
    r=deepcopy(unpriced);r['interest'][0]['net_interest_eur']=None
    m=prepare(r,analysis_scope='stocks_funds')
    assert all(m['by_key'][k].value is None for k in ('income','capital','recovery'))
    assert all(m['by_key'][k].value is not None for k in ('value','profit','mwr','twr','benchmark'))
    r=deepcopy(unpriced);r['dividends'][0]['net_dividend_eur']=None
    m=prepare(r,analysis_scope='stocks_funds')
    assert m['by_key']['value'].value is not None and m['holdings']
    assert all(m['by_key'][k].value is None for k in ('profit','income','capital','recovery','mwr','twr','benchmark'))
    assert m['nav'].empty and not m['periods']


def test_scoped_pdf_declares_both_scopes(unpriced):
    m=prepare(unpriced,date(2026,10,4),'stocks_funds');pdf=PdfReader(BytesIO(summary_pdf(m)))
    assert len(pdf.pages)==1
    text=pdf.pages[0].extract_text()
    for phrase in ('Analysis: Stocks & funds','Full portfolio value: Unavailable','STOCKS & FUNDS VALUE','Full ecosystem','FIFO P/L + net dividends','Missing derivative valuations: 7'):
        assert phrase in text
    r=deepcopy(unpriced);r['historical_metrics']['historical_analytics_status']='OK_WITH_LOW_CONFIDENCE_FALLBACK'
    assert 'History includes price estimates' in PdfReader(BytesIO(summary_pdf(prepare(r,analysis_scope='stocks_funds')))).pages[0].extract_text()


def test_personal_profile_fallback_requires_personal_mode(tmp_path,monkeypatch):
    from pulse.private_config import personal_defaults,export_digest
    monkeypatch.delenv('FOLIOLENS_CONFIG_DIRECTORY',raising=False)
    monkeypatch.delenv('IS_STREAMLIT_CLOUD',raising=False);monkeypatch.delenv('STREAMLIT_SHARING_MODE',raising=False)
    monkeypatch.setenv('USERPROFILE',str(tmp_path));monkeypatch.setenv('FOLIOLENS_MODE','personal')
    data,_=fixture('stocks');profile=tmp_path/'FolioLensPersonal/exports';profile.mkdir(parents=True)
    config={'export_sha256':export_digest(data),'worthless_confirmations':[]}
    (profile/(export_digest(data)+'.json')).write_text(json.dumps(config))
    assert personal_defaults(data)==config
    monkeypatch.setenv('FOLIOLENS_MODE','public_demo')
    assert personal_defaults(data)=={}


def test_scope_selection_pdfs_and_clear_are_session_specific(unpriced,monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('FOLIOLENS_MODE','personal')
    monkeypatch.delenv('IS_STREAMLIT_CLOUD',raising=False);monkeypatch.delenv('STREAMLIT_SHARING_MODE',raising=False)
    monkeypatch.setattr('pulse.runner.run_analysis',lambda *args,**kwargs:deepcopy(unpriced))
    path=str(Path(__file__).resolve().parents[1]/'streamlit_app.py')
    one=AppTest.from_file(path,default_timeout=30).run();two=AppTest.from_file(path,default_timeout=30).run()
    next(b for b in one.button if b.label=='Try with Demo Portfolio').click().run()
    next(b for b in two.button if b.label=='Try with Demo Portfolio').click().run()
    next(r for r in one.radio if r.label=='Analysis scope').set_value('Stocks & funds').run()
    assert not one.exception and not two.exception
    assert one.session_state['model']['by_key']['value'].value is not None
    assert two.session_state['model']['by_key']['value'].value is None
    assert one.session_state['pdf']!=two.session_state['pdf']
    personal_pdf=one.session_state['pdf']
    next(b for b in two.button if b.label=='Clear session results').click().run()
    assert len(two.metric)==0 and len(one.metric)==8
    assert one.session_state['pdf']==personal_pdf
    assert 'analysis_scope' not in two.session_state


def test_hot_release_refreshes_legacy_session_without_reprocessing(monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    import pulse.adapter as adapter
    import pulse.pdf as pdf
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py'),default_timeout=30).run()
    next(b for b in app.button if b.label=='Try with Demo Portfolio').click().run()
    legacy=app.session_state['model'];legacy.pop('model_schema_version')
    legacy.pop('dependencies');legacy.pop('full_totals')
    app.session_state['model']=legacy
    monkeypatch.setattr(adapter,'MODEL_SCHEMA_VERSION',1);monkeypatch.setattr(pdf,'PDF_SCHEMA_VERSION',1)
    def no_worker(*args,**kwargs):raise AssertionError('A display migration must not reprocess private input')
    monkeypatch.setattr('pulse.runner.run_analysis',no_worker)
    app.run()
    assert not app.exception and len(app.metric)==8
    assert app.session_state['model']['model_schema_version']==adapter.MODEL_SCHEMA_VERSION
    assert 'dependencies' in app.session_state['model']
