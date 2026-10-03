"""Fabricated exports/provider responses only; live acceptance stays local."""
import csv
from io import StringIO,BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import pandas as pd
from pulse.runner import run_analysis,validate_upload,AnalysisError
from pulse.synthetic import fixture
from pulse.private_config import event_candidates,export_digest,validate_private_config
from pulse.market import benchmark_identity,allowed_host,install_market
from pulse.adapter import prepare
from pulse.pdf import summary_pdf
from pypdf import PdfReader

@pytest.mark.parametrize('ticker',['SPY','VOO','^GSPC','IWDA.AS','VWCE.DE','^NSEI','NIFTYBEES.NS'])
def test_yahoo_symbol_syntax(ticker):
    from pulse.benchmarks import validate_ticker
    assert validate_ticker(ticker)==ticker

def test_price_index_is_not_a_total_return_comparison():
    name,warning=benchmark_identity({'symbol':'^GSPC','shortName':'S&P 500','currency':'USD','quoteType':'INDEX'},'^GSPC')
    assert name=='S&P 500' and 'Price-only' in warning
    assert benchmark_identity({'symbol':'VOO','longName':'Verified ETF','currency':'USD','quoteType':'ETF'},'VOO')[1]==''
    assert benchmark_identity({'symbol':'SPY','longName':'Other ETF','currency':'USD','quoteType':'ETF'},'VOO')[1]
    assert benchmark_identity({},'INVALID')[1]

@pytest.mark.parametrize('url',['http://query1.finance.yahoo.com','https://query1.finance.yahoo.com.evil.invalid','https://127.0.0.1','https://metadata.google.internal','file:///secret'])
def test_provider_destinations_are_fixed(url):
    assert not allowed_host(url)

def test_private_event_is_exact_export_bound_and_isolated():
    data,prices=fixture('unsupported_action')
    candidates=event_candidates(data)
    assert len(candidates)==1
    config={'export_sha256':export_digest(data),'worthless_confirmations':[candidates[0]['source_row']]}
    confirmed=run_analysis(data,prices=prices,private_config=config)
    assert confirmed['accounting_status']=='COMPLETE'
    assert len(confirmed['worthless_derecognition_events'])==1
    assert not confirmed['holdings'] or confirmed['holdings'][0]['current_quantity']==0
    unconfirmed=run_analysis(data,prices=prices)
    assert unconfirmed['accounting_status']=='BLOCKING'
    other,_=fixture('stocks')
    with pytest.raises(ValueError):validate_private_config(config,other)
    import security_events
    assert security_events.KNOWN_WORTHLESS_DERECOGNITIONS==()

def test_confirmed_partial_removal_is_still_blocking():
    data,prices=fixture('unsupported_action')
    rows=list(csv.DictReader(StringIO(data.decode())))
    for row in rows:
        if row['type']=='FREE_RECEIPT':row['shares']='-1'
    out=StringIO();writer=csv.DictWriter(out,fieldnames=rows[0]);writer.writeheader();writer.writerows(rows);data=out.getvalue().encode()
    config={'export_sha256':export_digest(data),'worthless_confirmations':[event_candidates(data)[0]['source_row']]}
    result=run_analysis(data,prices=prices,private_config=config)
    assert result['accounting_status']=='BLOCKING' and not result['worthless_derecognition_events']

def active_derivative():
    data,prices=fixture('derivatives')
    rows=[r for r in csv.DictReader(StringIO(data.decode())) if not(r['asset_class']=='DERIVATIVE' and r['type']=='SELL')]
    out=StringIO();writer=csv.DictWriter(out,fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
    return out.getvalue().encode(),prices

def test_manual_derivative_valuation_and_gap():
    data,prices=active_derivative()
    missing=prepare(run_analysis(data,prices=prices))
    assert missing['by_key']['value'].value is None and missing['by_key']['profit'].value is None
    assert missing['by_key']['mwr'].value is not None
    config={'export_sha256':export_digest(data),'derivative_quotes':{'ZZ0000000007':{'price_eur':10,'date':'2026-09-30','source':'Fabricated confirmed test quote'}}}
    result=run_analysis(data,prices=prices,private_config=config)
    assert result['active_derivatives'][0]['estimated_live_value_eur']==20
    assert result['active_derivatives'][0]['quote_status']=='MANUAL_CONFIRMED'
    assert prepare(result)['by_key']['value'].value is not None
    assert summary_pdf(prepare(result))!=summary_pdf(missing)

@pytest.mark.parametrize('payload',[b'\x00date,type',b'date,date,type\n2026-01-01,2026-01-01,BUY',b'date,type\n"unterminated'])
def test_malformed_content(payload):
    with pytest.raises(AnalysisError):validate_upload(payload)

def test_real_worker_timeout_and_cleanup(tmp_path,monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile,'tempdir',str(tmp_path))
    data,prices=fixture()
    with pytest.raises(AnalysisError,match='time limit'):run_analysis(data,prices=prices,timeout=.01)
    assert not list(tmp_path.iterdir())

def test_admission_limit_and_memory_limit(tmp_path):
    import os,sys
    from pulse.resources import execute_worker,WorkerLimitError,_slots
    _slots.acquire();_slots.acquire()
    try:
        with pytest.raises(WorkerLimitError,match='busy'):execute_worker([sys.executable,'-c','pass'],tmp_path,os.environ.copy(),1)
    finally:_slots.release();_slots.release()
    with pytest.raises(WorkerLimitError,match='memory'):execute_worker([sys.executable,'-c','import time; data=bytearray(30000000);time.sleep(2)'],tmp_path,os.environ.copy(),5,memory_mb=1)

def fake_namespace():
    index=pd.bdate_range('2025-01-06','2026-09-30')
    raw=pd.DataFrame({'Close':100.,'Adj Close':102.},index=index)
    frame=raw.rename(columns={'Close':'close_native','Adj Close':'adjusted_close_native'})
    provider=SimpleNamespace(info={'symbol':'SPY','longName':'Resolved Test ETF','quoteType':'ETF','currency':'USD'},fast_info={'currency':'USD'},history=lambda **kw:raw)
    return {'BENCHMARK_NAME':'SPY','BENCHMARK_TICKER':'SPY','yf':SimpleNamespace(Ticker=lambda ticker:provider),
        'requests':SimpleNamespace(sessions=SimpleNamespace(Session=type('S',(),{'request':lambda *a:None}))),
        'prefetch_openfigi_identities':lambda x:None,'find_best_yahoo_ticker':lambda *a:{'ticker':'TEST','match_status':'EXACT'},
        'fetch_latest_yahoo_price':lambda ticker:{'price_native':100.,'currency':'USD','price_date':'2026-09-30','status':'OK'},
        'get_fx_to_eur':lambda currency:.9,'_fetch_yahoo_history':lambda *a:(frame,'USD','OK',''),
        '_normalize_yahoo_history':lambda value:frame,'_normalize_currency_for_history':lambda currency:(currency,1)}

def test_public_capture_replay_identity_history_fx(monkeypatch,tmp_path):
    monkeypatch.delenv('FOLIOLENS_REPLAY_PATH',raising=False)
    monkeypatch.setattr('pulse.market.install_transport_controls',lambda ns:{'requests':0})
    one=fake_namespace();install_market(one)
    resolve=one['find_best_yahoo_ticker']('ZZ0000000001','Test','FUND')
    latest=one['fetch_latest_yahoo_price']('TEST');fx=one['get_fx_to_eur']('USD')
    history=one['_fetch_yahoo_history']('SPY','2025-01-06','2026-09-30')
    assert history[2]=='OK' and one['_foliolens_benchmark']['name']=='Resolved Test ETF (SPY)'
    path=tmp_path/'capture.json';path.write_text(json.dumps({'calls':one['_foliolens_capture']}))
    monkeypatch.setenv('FOLIOLENS_REPLAY_PATH',str(path));two=fake_namespace();install_market(two)
    assert two['find_best_yahoo_ticker']('ZZ0000000001','Test','FUND')==resolve
    assert two['fetch_latest_yahoo_price']('TEST')==latest and two['get_fx_to_eur']('USD')==fx
    pd.testing.assert_frame_equal(two['_fetch_yahoo_history']('SPY','2025-01-06','2026-09-30')[0],history[0])
    assert two['_fetch_yahoo_history']('NOT_CAPTURED','2025-01-06','2026-09-30')[2]=='NOT_CAPTURED'

def test_public_owner_modes_fail_closed(monkeypatch):
    from pulse.mode import uploads_enabled
    monkeypatch.setenv('FOLIOLENS_MODE','owner_hosted');monkeypatch.delenv('FOLIOLENS_OWNER_GATE_VERIFIED',raising=False)
    assert not uploads_enabled()
    monkeypatch.setenv('FOLIOLENS_OWNER_GATE_VERIFIED','1');monkeypatch.setenv('FOLIOLENS_DATA_RIGHTS_VERIFIED','1')
    assert uploads_enabled()
    monkeypatch.setenv('FOLIOLENS_MODE','public_demo')
    assert not uploads_enabled()

def test_provider_timeout_retry_and_rate_limit_stop(monkeypatch):
    import requests
    from yfinance._http import requests as yf_requests
    from pulse.market import install_transport_controls
    calls=[]
    def response(self,method,url,**kwargs):
        calls.append((url,kwargs['timeout']))
        if 'timeout' in url and len(calls)==1:raise requests.exceptions.Timeout()
        return SimpleNamespace(status_code=429 if 'restricted' in url else 200)
    for cls in {requests.sessions.Session,yf_requests.Session}:monkeypatch.setattr(cls,'request',response)
    state=install_transport_controls({'prefetch_openfigi_identities':lambda x:None})
    session=requests.Session()
    assert session.get('https://query1.finance.yahoo.com/timeout',timeout=100).status_code==200
    assert len(calls)==2 and all(timeout==10 for _,timeout in calls)
    session.get('https://query1.finance.yahoo.com/restricted')
    with pytest.raises(RuntimeError):session.get('https://query2.finance.yahoo.com/anything')
    assert session.get('https://api.openfigi.com/v3/mapping').status_code==200
    assert state['restricted_providers']==['yahoo']

def test_numeric_and_security_count_bounds():
    from pulse.synthetic import row,COLUMNS
    def encode(rows):
        out=StringIO();writer=csv.DictWriter(out,fieldnames=COLUMNS);writer.writeheader();writer.writerows(rows);return out.getvalue().encode()
    for number in ('inf','1e300'):
        with pytest.raises(AnalysisError):validate_upload(encode([row('2026-01-01','BUY',shares=1,price=number,amount=-1)]))
    with pytest.raises(AnalysisError,match='150-security'):
        validate_upload(encode([row('2026-01-01','BUY',i=i,shares=1,price=1,amount=-1) for i in range(1,152)]))

def test_missing_current_currency_blocks_quote(monkeypatch):
    monkeypatch.delenv('FOLIOLENS_REPLAY_PATH',raising=False)
    monkeypatch.setattr('pulse.market.install_transport_controls',lambda ns:{'requests':0})
    ns=fake_namespace();ns['yf'].Ticker=lambda symbol:SimpleNamespace(fast_info={})
    ns['fetch_latest_yahoo_price']=lambda ticker:{'price_native':100,'currency':'UNKNOWN','price_date':'2026-09-30','status':'OK'}
    install_market(ns)
    assert ns['fetch_latest_yahoo_price']('UNKNOWN')['price_native'] is None
    assert pd.isna(ns['get_fx_to_eur']('UNKNOWN'))

def test_stale_quotes_are_disclosed():
    data,prices=fixture();model=prepare(run_analysis(data,prices=prices),pd.Timestamp('2027-01-01').date())
    assert any('seven days old' in issue for issue in model['issues'])

def test_public_live_processing_requires_mode(monkeypatch):
    monkeypatch.setenv('FOLIOLENS_MODE','public_demo')
    data,_=fixture()
    with pytest.raises(AnalysisError,match='personal-use'):run_analysis(data)

def test_worker_environment_excludes_application_secrets(monkeypatch):
    from pulse.runner import worker_environment
    monkeypatch.setenv('FOLIOLENS_PRIVATE_CONFIG','private-file')
    monkeypatch.setenv('ARBITRARY_API_SECRET','fictional-secret')
    assert 'FOLIOLENS_PRIVATE_CONFIG' not in worker_environment()
    assert 'ARBITRARY_API_SECRET' not in worker_environment()
    assert worker_environment().get('PATH') is not None

def test_private_configuration_size_limit():
    data,_=fixture()
    with pytest.raises(ValueError,match='size limit'):
        validate_private_config({'export_sha256':export_digest(data),'extra':'x'*65536},data)
