"""Fictional, portfolio-bound manual evidence; no owner records or quotations."""
from copy import deepcopy
from datetime import date, timedelta
import csv
from io import StringIO
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from pulse.synthetic import fixture, row
from pulse.valuations import quote_status, saved_quotes, remember_quote, activity_anchor, diagnostic_rows
from pulse.private_config import personal_defaults, export_digest
from pulse.market import install_derivatives, install_market


def encoded(rows):
    output=StringIO();writer=csv.DictWriter(output,fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
    return output.getvalue().encode()


@pytest.fixture
def personal(tmp_path, monkeypatch):
    monkeypatch.setenv('FOLIOLENS_MODE','personal');monkeypatch.setenv('FOLIOLENS_CONFIG_DIRECTORY',str(tmp_path))
    monkeypatch.delenv('STREAMLIT_SHARING_MODE',raising=False);monkeypatch.delenv('IS_STREAMLIT_CLOUD',raising=False)
    monkeypatch.setattr('pulse.valuations.today',lambda:date(2026,10,26))
    return tmp_path


def instrument(data):
    return next(r['symbol'] for r in csv.DictReader(StringIO(data.decode())) if r['asset_class']=='DERIVATIVE')


def quote(**kwargs):
    return dict(price_eur=1.25,date='2026-10-26',source='Fictional broker observation',**kwargs)


@pytest.mark.parametrize('offset,expected',[(0,'MANUAL_CONFIRMED'),(7,'MANUAL_CONFIRMED'),(8,'MANUAL_STALE'),(-1,'MANUAL_FUTURE')])
def test_manual_observation_dates(offset,expected):
    value=quote();value['date']=(date(2026,10,26)-timedelta(days=offset)).isoformat()
    assert quote_status(value,date(2026,10,26))==expected


@pytest.mark.parametrize('key,value',[('price_eur',0),('price_eur',-1),('price_eur',True),('price_eur',float('inf')),('price_eur',100001),('source',''),('source',7),('date','bad'),('date',None)])
def test_invalid_observations_never_become_quotes(key,value):
    item=quote();item[key]=value
    assert quote_status(item,date(2026,10,26))=='MANUAL_INVALID'


def test_saved_quote_same_activity_new_export_and_isolation(personal):
    data,_=fixture('open_derivatives');isin=instrument(data);assert remember_quote(data,isin,quote())
    assert saved_quotes(data)=={isin:quote()}
    assert personal_defaults(data)['derivative_quotes']=={isin:quote()}
    rows=list(csv.DictReader(StringIO(data.decode())))
    extra=row('2026-10-26','INTEREST_PAYMENT',asset='',amount=3,category='CASH');extra['transaction_id']='fictional-other-income'
    newer=encoded([extra,*reversed(rows)])
    assert export_digest(newer)!=export_digest(data) and saved_quotes(newer)=={isin:quote()}
    affected=next(r for r in rows if r['symbol']==isin);affected['transaction_id']='different-investor-derivative'
    assert not saved_quotes(encoded(rows))
    assert not saved_quotes(data,personal/'another-user')
    stored=(personal/'derivative-valuations.dat').read_bytes()
    if __import__('os').name=='nt':assert b'Fictional broker observation' not in stored


@pytest.mark.parametrize('field,value',[('shares','7'),('price','12'),('fee','-2'),('amount','-100'),('name','Other contract')])
def test_changed_security_activity_does_not_reuse_evidence(personal,field,value):
    data,_=fixture('open_derivatives');isin=instrument(data);remember_quote(data,isin,quote())
    rows=list(csv.DictReader(StringIO(data.decode())));next(r for r in rows if r['symbol']==isin)[field]=value
    assert not saved_quotes(encoded(rows))


def test_expired_and_corrupt_optional_profiles_do_not_block_stocks(personal,monkeypatch):
    data,_=fixture('open_derivatives');isin=instrument(data);remember_quote(data,isin,quote())
    monkeypatch.setattr('pulse.valuations.today',lambda:date(2026,11,3))
    assert not saved_quotes(data) and not personal_defaults(data).get('derivative_quotes')
    (personal/'derivative-valuations.dat').write_bytes(b'corrupt-evidence')
    with pytest.raises((ValueError,OSError)):saved_quotes(data)
    assert not personal_defaults(data).get('derivative_quotes')


def test_hosted_quote_evidence_is_session_only(personal,monkeypatch):
    data,_=fixture('open_derivatives');isin=instrument(data)
    monkeypatch.setenv('FOLIOLENS_MODE','owner_hosted')
    assert not remember_quote(data,isin,quote()) and not saved_quotes(data)
    assert not (personal/'derivative-valuations.dat').exists()


def test_multiple_quotes_saved_without_lost_updates(personal):
    data,_=fixture('open_derivatives');isins=sorted({r['symbol'] for r in csv.DictReader(StringIO(data.decode())) if r['asset_class']=='DERIVATIVE'})
    with ThreadPoolExecutor(max_workers=3) as executor:
        assert all(executor.map(lambda isin:remember_quote(data,isin,quote()),isins))
    assert len(saved_quotes(data))==7


def test_no_unverifiable_derivative_network_probe_and_stale_not_zero(monkeypatch):
    def forbidden(*args):raise AssertionError('Rejected derivative identities must not generate a provider request')
    ns={'yahoo_derivative_probe':forbidden}
    install_derivatives(ns,{})
    result=ns['multi_source_derivative_quote_probe']('ZZ0000000007')
    assert result['quote_status']=='VERIFIED_DERIVATIVE_QUOTE_UNAVAILABLE' and np.isnan(result['live_price_eur']) and not result['all_quote_attempts']
    monkeypatch.setattr('pulse.valuations.today',lambda:date(2026,10,26))
    install_derivatives(ns,{'derivative_quotes':{'ZZ0000000007':dict(quote(),date='2026-10-01')}})
    result=ns['multi_source_derivative_quote_probe']('ZZ0000000007')
    assert result['quote_status']=='MANUAL_STALE' and np.isnan(result['live_price_eur']) and np.isnan(result['quote_last'])


def test_valuation_report_metadata_never_claims_scrapers_or_loses_quote_date():
    rows=[dict(instrument_type='DERIVATIVE',isin='ZZ0000000007',attempted_sources='ONVISTA;YAHOO',quote_date='')]
    before=deepcopy(rows)
    fixed=diagnostic_rows(rows,[dict(isin='ZZ0000000007',quote_status='MANUAL_CONFIRMED',quote_date='2026-10-26')])
    assert fixed[0]['quote_date']=='2026-10-26' and fixed[0]['attempted_sources']=='USER_SUPPLIED_EUR_OBSERVATION'
    assert rows==before


@pytest.mark.parametrize('day,valid',[('2026-10-26',True),('2026-10-19',True),('2026-10-18',False),('2026-10-27',False),('bad',False)])
def test_live_stock_quote_freshness_is_captured_and_replayable(monkeypatch,tmp_path,day,valid):
    from test_real_workflow import fake_namespace
    monkeypatch.setattr('pulse.market.install_transport_controls',lambda ns:{'requests':0})
    monkeypatch.setattr('pulse.valuations.today',lambda:date(2026,10,26))
    ns=fake_namespace();ns['fetch_latest_yahoo_price']=lambda ticker:dict(price_native=10,currency='EUR',price_date=day,status='OK')
    install_market(ns);latest=ns['fetch_latest_yahoo_price']('TEST')
    assert (latest['price_native'] is not None)==valid
    capture=tmp_path/'capture.json';capture.write_text(json.dumps({'calls':ns['_foliolens_capture']}));monkeypatch.setenv('FOLIOLENS_REPLAY_PATH',str(capture))
    replay=fake_namespace();install_market(replay)
    assert replay['fetch_latest_yahoo_price']('TEST')==latest
