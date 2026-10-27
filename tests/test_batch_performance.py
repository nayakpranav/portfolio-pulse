"""Fabricated batch evidence, session captures and aggregate-only diagnostics."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import csv
from io import StringIO
import json
import pytest
from pypdf import PdfReader
from pulse.manual_batch import validate, drafts
from pulse.replay import retain, eligible, recompute, MAX_AGE_SECONDS
from pulse.private_config import export_digest
from pulse.valuations import remember_quotes, saved_quotes
from pulse.synthetic import fixture
from pulse.runner import run_analysis
from pulse.adapter import prepare
from pulse.html_report import html_report
from pulse.pdf import summary_pdf
from pulse.performance import Timings

DAY=date(2026,9,30)
NOW=datetime(2026,9,30,12,tzinfo=timezone.utc)


def rows():
    return [dict(ISIN=f'ZZ00000000{i:02d}',**{'EUR per unit':i+.25,'Observation date':None,'Source':''}) for i in range(7)]


def test_batch_common_defaults_individual_overrides_and_missing_rows():
    values=rows();values[0].update({'EUR per unit':None});values[1].update({'Observation date':DAY-timedelta(days=1),'Source':'Other fictional bid'})
    accepted,errors=validate(values,[r['ISIN'] for r in values],DAY,'Fictional common bid',DAY)
    assert not errors and len(accepted)==6
    assert accepted[values[1]['ISIN']]['date']=='2026-09-29'
    assert accepted[values[1]['ISIN']]['source']=='Other fictional bid'
    assert accepted[values[2]['ISIN']]==dict(price_eur=2.25,date='2026-09-30',source='Fictional common bid')


@pytest.mark.parametrize('field,value',[
    ('EUR per unit',0),('EUR per unit',-1),('EUR per unit',True),('EUR per unit',float('inf')),
    ('EUR per unit',100001),('Observation date',date(2026,10,1)),
    ('Observation date',date(2026,9,22)),('Observation date','bad'),('Source','x'*161),('ISIN','not-active')])
def test_invalid_rows_preserve_valid_drafts(field,value):
    values=rows();expected=[r['ISIN'] for r in values];values[2][field]=value;before=deepcopy(values)
    accepted,errors=validate(values,expected,DAY,'Fictional common bid',DAY)
    assert errors and len(accepted)==6 and values==before
    assert any(row==3 for row,reason in errors)


def test_immutable_identities_duplicate_and_missing_rows():
    values=rows();identities=[r['ISIN'] for r in values]
    swapped=deepcopy(values);swapped[0],swapped[1]=swapped[1],swapped[0]
    assert validate(swapped,identities,DAY,'Fictional',DAY)[1]
    assert validate(values[:-1],identities,DAY,'Fictional',DAY)[1]
    values[1]['ISIN']=values[0]['ISIN']
    assert validate(values,identities,DAY,'Fictional',DAY)[1]


def test_atomic_encrypted_idempotent_batch_and_activity_expiry(tmp_path,monkeypatch):
    monkeypatch.setenv('FOLIOLENS_MODE','personal');monkeypatch.setenv('FOLIOLENS_CONFIG_DIRECTORY',str(tmp_path))
    monkeypatch.setattr('pulse.valuations.today',lambda:DAY)
    data,_=fixture('open_derivatives')
    identities={r['symbol'] for r in csv.DictReader(StringIO(data.decode())) if r['asset_class']=='DERIVATIVE'}
    quotes={isin:dict(price_eur=1.75,date=DAY.isoformat(),source='Fictional batch bid') for isin in identities}
    assert len(quotes)==7 and remember_quotes(data,quotes)
    path=tmp_path/'derivative-valuations.dat';before=path.read_bytes();stamp=path.stat().st_mtime_ns
    assert saved_quotes(data)==quotes and remember_quotes(data,quotes)
    assert path.read_bytes()==before and path.stat().st_mtime_ns==stamp
    bad=deepcopy(quotes);bad[next(iter(bad))]['price_eur']=-1
    with pytest.raises(ValueError):remember_quotes(data,bad)
    assert path.read_bytes()==before
    monkeypatch.setattr('pulse.valuations.today',lambda:DAY+timedelta(days=8))
    assert not saved_quotes(data) and path.read_bytes()==before
    assert not saved_quotes(data,tmp_path/'other-user')


def envelope():
    data=b'fictional export only';config=dict(known_events=[],worthless_confirmations=[])
    raw=dict(synthetic=False,market_capture={'calls':{'public market input':{'date':'2026-09-29'}}})
    return data,config,retain(data,'IWDA.AS',config,raw,NOW)


def test_session_capture_binding_and_original_dates():
    data,config,capture=envelope();before=deepcopy(capture)
    original,_=eligible(capture,data,'IWDA.AS',dict(config,derivative_quotes={'fictional':123}),NOW+timedelta(minutes=1))
    assert original==capture['capture'] and capture==before
    for changed in [(data+b'changed','IWDA.AS',config),(data,'SPY',config),(data,'IWDA.AS',dict(config,worthless_confirmations=[2]))]:
        assert eligible(capture,*changed,now=NOW)[0] is None
    for moment in [NOW-timedelta(seconds=1),NOW+timedelta(seconds=MAX_AGE_SECONDS+1),NOW+timedelta(days=1)]:
        assert eligible(capture,data,'IWDA.AS',config,moment)[0] is None
    assert eligible(None,data,'IWDA.AS',config,NOW)[0] is None


def test_recompute_exact_capture_fallback_and_no_silent_live_retry(monkeypatch):
    data,config,capture=envelope()
    monkeypatch.setattr('pulse.replay.eligible',lambda *a,**k:(capture['capture'],''))
    calls=[]
    def run(data,**kwargs):calls.append(kwargs);return {'replay_missing_inputs':0}
    result,reason=recompute(data,benchmark='IWDA.AS',config=config,envelope=capture,run=run)
    assert not reason and calls[0]['replay']==capture['capture']
    def missing(*a,**k):return {'replay_missing_inputs':1}
    with pytest.raises(ValueError,match='No external requests'):recompute(data,benchmark='IWDA.AS',config=config,envelope=capture,run=missing)
    monkeypatch.setattr('pulse.replay.eligible',lambda *a,**k:(None,'Capture expired'))
    recompute(data,benchmark='IWDA.AS',config=config,envelope=capture,run=run)
    assert calls[-1]['replay'] is None and len(calls)==2


def test_timings_do_not_record_arguments_exceptions_or_values():
    timer=Timings()
    def f(private):raise ValueError(private)
    with pytest.raises(ValueError):timer.wrap(f,'fixed_stage')('PRIVATE_IDENTIFIER_PRICE_OR_RESPONSE')
    assert timer.rows['fixed_stage']['calls']==1
    assert timer.rows['fixed_stage']['wall_seconds']>=0 and timer.rows['fixed_stage']['cpu_seconds']>=0
    assert 'PRIVATE' not in json.dumps(timer.rows)


def test_complete_valuation_header_retains_independent_quality_warnings():
    data,prices=fixture('open_derivatives')
    ids={r['symbol'] for r in csv.DictReader(StringIO(data.decode())) if r['asset_class']=='DERIVATIVE'}
    config=dict(export_sha256=export_digest(data),derivative_quotes={s:dict(price_eur=2.5,date=DAY.isoformat(),source='Fictional broker bid') for s in ids})
    raw=run_analysis(data,prices=prices,private_config=config)
    raw['historical_metrics']['historical_analytics_status']='OK_WITH_LOW_CONFIDENCE_FALLBACK'
    model=prepare(raw,DAY,'full_portfolio');assert model['health']=='Partial'
    assert model['report_status']['headline']=='Full portfolio — Valuation complete'
    assert model['report_status']['quality']=='Data quality: review notes'
    html=html_report(model).decode();pdf=PdfReader(BytesIO(summary_pdf(model)))
    assert model['report_status']['headline'] in html and len(pdf.pages)==1
    assert 'Valuation complete' in pdf.pages[0].extract_text() and 'History includes price estimates' in pdf.pages[0].extract_text()
    assert all(m.display in html and m.display.replace('€','EUR ') in pdf.pages[0].extract_text() for m in model['metrics'])
    raw['pre_diagnostics'].append({'check':'unknown_transaction_rows','severity':'BLOCKING'});raw['accounting_status']='BLOCKING'
    blocked=prepare(raw,DAY,'full_portfolio');assert not blocked['report_status']['full_complete']
    assert 'Valuation complete' not in blocked['report_status']['headline']
