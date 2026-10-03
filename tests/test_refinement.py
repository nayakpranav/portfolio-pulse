from datetime import date
from io import BytesIO
import pytest
import pandas as pd
from pypdf import PdfReader
from pulse.benchmarks import validate_ticker
from pulse.charts import wealth_range
from pulse.runner import run_analysis, AnalysisError
from pulse.synthetic import fixture
from pulse.adapter import prepare
from pulse.pdf import summary_pdf
from worker_hooks import benchmark_history_issue, coverage_issue, install_hooks

@pytest.mark.parametrize('ticker', ['IWDA.AS','VWCE.DE','SXR8.DE',None])
def test_explicit_benchmarks(ticker):
    data,prices=fixture()
    result=run_analysis(data,prices=prices,benchmark=ticker)
    model=prepare(result,date(2026,10,4))
    assert model['by_key']['profit'].value==pytest.approx(221.8)
    assert model['by_key']['mwr'].value==pytest.approx(1.950474771,abs=1e-7)
    if ticker:
        assert model['by_key']['benchmark'].value is not None
        assert result['benchmark_name'].startswith('Synthetic ')
        assert result['benchmark_name'] in model['by_key']['benchmark'].scope
        assert any(result['benchmark_name'] in line for line in model['insights'])
        text=PdfReader(BytesIO(summary_pdf(model))).pages[0].extract_text()
        assert 'Axis does not start at zero' in text
        assert 'recognized net investment income' in text
    else:
        assert model['by_key']['benchmark'].value is None

@pytest.mark.parametrize('ticker', ['', 'A B', '../secret','https://host','X'*33])
def test_bad_ticker(ticker):
    with pytest.raises(ValueError): validate_ticker(ticker)

def test_custom_never_fabricated_or_silently_substituted(monkeypatch):
    data,prices=fixture()
    with pytest.raises(AnalysisError,match='synthetic data'): run_analysis(data,prices=prices,benchmark='INVALID.DE')
    monkeypatch.delenv('PULSE_ENABLE_UPLOADS',raising=False)
    with pytest.raises(AnalysisError,match='local personal-use'): run_analysis(data,benchmark='INVALID.DE')
    assert validate_ticker(' brk-b ')=='BRK-B'

def test_history_guards():
    idx=pd.bdate_range('2026-01-05','2026-01-30')
    raw=pd.DataFrame({'Close':100.,'Adj Close':100.},index=idx)
    frame=pd.DataFrame({'close_native':100.,'adjusted_close_native':100.},index=idx)
    assert not benchmark_history_issue(raw,frame,'EUR',idx[0],idx[-1])
    assert benchmark_history_issue(raw.drop(columns='Adj Close'),frame,'EUR',idx[0],idx[-1])
    assert benchmark_history_issue(raw,frame,'',idx[0],idx[-1])
    assert coverage_issue(frame.iloc[1:],'close_native',idx[0],idx[-1])
    assert coverage_issue(frame.iloc[:-5],'close_native',idx[0],idx[-1])
    assert coverage_issue(frame.iloc[[0,-1]],'close_native',idx[0],idx[-1])
    frame.iloc[2,1]=float('nan')
    assert benchmark_history_issue(raw,frame,'EUR',idx[0],idx[-1])

def test_provider_failure_contained_without_fallback(monkeypatch):
    monkeypatch.delenv('PULSE_PRICE_INPUT',raising=False)
    monkeypatch.setenv('PULSE_BENCHMARK_DISABLED','0')
    called=[]
    def fail(ticker):
        called.append(ticker)
        raise RuntimeError('controlled provider failure')
    from types import SimpleNamespace
    ns={'yf':SimpleNamespace(Ticker=fail),'BENCHMARK_TICKER':'INVALID.DE','_fetch_yahoo_history':lambda *a: None}
    install_hooks(ns)
    frame,currency,status,error=ns['_fetch_yahoo_history']('INVALID.DE','2026-01-01','2026-01-31')
    assert frame.empty and status=='BENCHMARK_ERROR'
    assert called==['INVALID.DE']

def test_nonzero_range_does_not_amplify_tiny_difference():
    low,high=wealth_range([100,100.01])
    assert low<100 and high>100.01 and high-low>=5

def test_public_landing_has_no_upload_or_custom(monkeypatch):
    monkeypatch.delenv('PULSE_ENABLE_UPLOADS',raising=False)
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py'),default_timeout=30).run()
    assert not app.exception
    assert not app.get('file_uploader')
    assert 'Custom Yahoo Finance ticker' not in app.selectbox[0].options
    assert app.title[0].value=='FolioLens'
    next(b for b in app.button if b.key=='landing_demo').click().run()
    assert not app.exception and len(app.metric)==8

def test_local_custom_input_validation(monkeypatch):
    monkeypatch.setenv('PULSE_ENABLE_UPLOADS','1')
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py')).run()
    app.selectbox[0].select('Custom Yahoo Finance ticker').run()
    app.text_input[0].set_value('../bad').run()
    assert not app.exception and app.error
    assert next(b for b in app.button if b.label=='Analyze Portfolio').disabled

@pytest.mark.parametrize('fx_available',[True,False])
def test_custom_non_eur_benchmark_requires_reliable_fx(monkeypatch,fx_available):
    from types import SimpleNamespace
    monkeypatch.delenv('PULSE_PRICE_INPUT',raising=False)
    monkeypatch.setenv('PULSE_BENCHMARK_DISABLED','0')
    idx=pd.bdate_range('2026-01-05','2026-01-30')
    raw=pd.DataFrame({'Close':100.,'Adj Close':101.},index=idx)
    frame=pd.DataFrame({'close_native':100.,'adjusted_close_native':101.},index=idx)
    provider=SimpleNamespace(history=lambda **kwargs:raw,fast_info={'currency':'USD'})
    calls=[]
    def fx(ticker,*args):
        calls.append(ticker)
        return (frame if fx_available else pd.DataFrame()),'USD','OK' if fx_available else 'ERROR',''
    ns={'yf':SimpleNamespace(Ticker=lambda ticker:provider),'BENCHMARK_TICKER':'CUSTOM',
        '_fetch_yahoo_history':fx,'_normalize_yahoo_history':lambda data:frame,
        '_normalize_currency_for_history':lambda currency:(currency,1)}
    install_hooks(ns)
    result,currency,status,error=ns['_fetch_yahoo_history']('CUSTOM',idx[0],idx[-1])
    assert calls==['USDEUR=X']
    assert (status=='OK')==fx_available
    assert result.empty != fx_available
