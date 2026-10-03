from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
from pathlib import Path
import pytest
from pulse.synthetic import fixture
from pulse.runner import run_analysis,validate_upload,AnalysisError
from pulse.adapter import prepare
from pulse.pdf import summary_pdf
from pypdf import PdfReader
from io import BytesIO

CASES=['etf_only','stocks','dividends','interest','no_dividends','no_saveback','no_derivatives','realized_sales','reinvestment','unsupported_action','missing_price','short_history','incomplete_accounting','derivatives','demo']

@pytest.fixture(scope='session')
def results():
    outputs={}
    for kind in CASES:
        data,prices=fixture(kind)
        outputs[kind]=run_analysis(data,prices=prices)
    return outputs

@pytest.mark.parametrize('kind',CASES)
def test_scenarios(kind,results):
    model=prepare(results[kind],date(2026,10,3))
    assert len(model['metrics'])==8
    assert 1<=len(model['insights'])<=5
    assert len(PdfReader(BytesIO(summary_pdf(model))).pages)==1
    if kind in {'unsupported_action','incomplete_accounting'}:
        assert model['health']=='Review required'
        assert all(model['by_key'][key].value is None for key in ('value','profit','capital','recovery','mwr','benchmark','twr'))
        assert not model['snapshot']['top_holdings']
    if kind=='missing_price':
        assert model['by_key']['value'].value is None
        assert model['by_key']['profit'].value is None
        assert model['by_key']['capital'].value is not None
    if kind=='short_history':
        assert model['by_key']['twr'].value is None
    if kind in {'no_dividends','no_saveback','no_derivatives','etf_only','stocks'}:
        assert model['by_key']['income'].value==0

def test_demo_numeric_mapping_and_pdf(results):
    result=results['demo'];m=prepare(result,date(2026,10,3))
    life=result['lifetime_metrics']
    assert m['by_key']['value'].value==life['lifetime_current_tracked_open_value_eur']
    assert m['by_key']['profit'].value==pytest.approx(221.8,abs=1e-9)
    assert m['by_key']['capital'].value==pytest.approx(5893,abs=1e-9)
    assert m['by_key']['recovery'].value==pytest.approx(133,abs=1e-9)
    assert m['by_key']['income'].value==62
    assert m['by_key']['mwr'].value==result['advanced_metrics']['stock_fund_mwr_acquisition_pct']
    assert m['by_key']['benchmark'].value==result['historical_metrics']['benchmark_mwr_pct']
    assert m['by_key']['twr'].value==result['historical_metrics']['stockfund_twr_since_inception_pct']
    assert m['health']=='Complete'
    assert m['snapshot']['months'][9]['total'] is None
    assert m['snapshot']['months'][0]['status']=='complete'
    assert m['snapshot']['months'][0]['total']==2
    assert sum(x['weight_pct'] for x in m['snapshot']['top_holdings'])==pytest.approx(100)
    assert [x['value'] for x in m['snapshot']['top_holdings']]==sorted([x['value'] for x in m['snapshot']['top_holdings']],reverse=True)
    pdf=PdfReader(BytesIO(summary_pdf(m)))
    assert float(pdf.pages[0].mediabox.width)==pytest.approx(841.8898,abs=.001)
    text=pdf.pages[0].extract_text()
    for phrase in ('PORTFOLIO PULSE','6,114.80','221.80','1.95%','5.10%','3.27%','62.00','30 Sep 2026'):
        assert phrase in text

def test_reinvestment_income_once(results):
    r=results['reinvestment'];m=prepare(r,date(2026,10,3))
    assert len(r['dividend_reinvestment_events'])==1
    assert m['by_key']['income'].value==pytest.approx(4)
    assert r['holdings'][0]['current_quantity']==pytest.approx(11.2)
    assert r['holdings'][0]['remaining_acquisition_cost_basis_eur']==pytest.approx(226)

def test_disabled_benchmark():
    d,p=fixture();m=prepare(run_analysis(d,prices=p,benchmark=None))
    assert m['by_key']['benchmark'].value is None
    assert m['by_key']['mwr'].value is not None
    assert not any('percentage points' in x for x in m['insights'])

def test_ambiguous_return_is_not_a_confident_metric(results):
    r=json.loads(json.dumps(results['demo']))
    r['advanced_metrics']['stock_fund_mwr_acquisition_status']='MULTIPLE_ROOTS'
    assert prepare(r)['by_key']['mwr'].value is None

def test_invalid_income_does_not_become_zero(results):
    r=json.loads(json.dumps(results['demo']))
    r['interest'][0]['net_interest_eur']=None
    m=prepare(r)
    assert m['by_key']['income'].value is None
    assert m['snapshot']['ytd_income'] is None

def test_partial_month_and_real_zero(results):
    r=json.loads(json.dumps(results['no_dividends']))
    r['latest_transaction_date']='2026-09-15'
    m=prepare(r,date(2026,10,3))
    months=m['snapshot']['months']
    assert months[7]['status']=='complete' and months[7]['total']==0
    assert months[8]['status']=='partial' and months[8]['total']==0
    assert months[9]['status']=='unavailable' and months[9]['total'] is None

@pytest.mark.parametrize('data',[b'',b'date,type\nnot-a-date,BUY\n',b'not,csv\n1,2',b'\xff',b'x'* (5*1024*1024+1)],ids=['empty','invalid_date','unsupported','invalid_encoding','oversized'])
def test_input_limits(data):
    with pytest.raises(AnalysisError):validate_upload(data)

def test_process_session_isolation_and_cleanup(tmp_path,monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile,'tempdir',str(tmp_path))
    a,ap=fixture('stocks');b,bp=fixture('etf_only');bp['securities']['ZZ0000000001']['end']=100
    with ThreadPoolExecutor(max_workers=2) as pool:
        one=pool.submit(run_analysis,a,prices=ap);two=pool.submit(run_analysis,b,prices=bp)
        r1,r2=one.result(),two.result()
    assert r1['lifetime_metrics']['lifetime_current_tracked_open_value_eur'] != r2['lifetime_metrics']['lifetime_current_tracked_open_value_eur']
    assert r1['holdings'][0]['security_name'] != r2['holdings'][0]['security_name']
    assert not list(tmp_path.iterdir())
    assert summary_pdf(prepare(r1)) != summary_pdf(prepare(r2))

def test_ui_demo_and_clear():
    from streamlit.testing.v1 import AppTest
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py'),default_timeout=30).run()
    assert not app.exception
    next(b for b in app.button if b.label=='Try with Demo Portfolio').click().run()
    assert not app.exception
    assert len(app.metric)==8
    assert 'pdf' in app.session_state
    next(b for b in app.button if b.label=='Clear session results').click().run()
    assert not app.exception
    assert len(app.metric)==0

def test_ui_two_independent_sessions():
    from streamlit.testing.v1 import AppTest
    path=str(Path(__file__).resolve().parents[1]/'streamlit_app.py')
    one=AppTest.from_file(path,default_timeout=30).run()
    two=AppTest.from_file(path,default_timeout=30).run()
    next(b for b in one.button if b.label=='Try with Demo Portfolio').click().run()
    two.selectbox[0].select('No comparison').run()
    next(b for b in two.button if b.label=='Try with Demo Portfolio').click().run()
    assert not one.exception and not two.exception
    assert one.session_state['model']['by_key']['benchmark'].value is not None
    assert two.session_state['model']['by_key']['benchmark'].value is None
    pdf_one=one.session_state['pdf']
    next(b for b in two.button if b.label=='Clear session results').click().run()
    assert one.session_state['pdf']==pdf_one
    assert len(two.metric)==0 and len(one.metric)==8

def test_public_registry_empty():
    from security_events import KNOWN_WORTHLESS_DERECOGNITIONS
    assert KNOWN_WORTHLESS_DERECOGNITIONS==()

def test_temporary_cleanup_retries_a_transient_lock(tmp_path,monkeypatch):
    import tempfile
    original=tempfile.TemporaryDirectory
    calls=[]
    def transient_directory(*args,**kwargs):
        directory=original(*args,**kwargs,dir=tmp_path)
        cleanup=directory.cleanup
        def retryable_cleanup():
            calls.append(True)
            if len(calls)==1:raise PermissionError('Synthetic transient Windows file lock')
            cleanup()
        directory.cleanup=retryable_cleanup
        return directory
    monkeypatch.setattr(tempfile,'TemporaryDirectory',transient_directory)
    data,prices=fixture('stocks');run_analysis(data,prices=prices)
    assert len(calls)==2 and not list(tmp_path.iterdir())
