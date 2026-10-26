"""Fabricated exact-ISIN documents and observations; never owner quotations."""
from dataclasses import replace,asdict
from datetime import datetime,timezone,timedelta
from copy import deepcopy
import json
import numpy as np
import pytest
from pulse.derivative_quotes import (Observation,isin_valid,validate_observation,canonical_quote,
    DocumentTransport,ProviderError,parse_vontobel_document,Collector,VONTOBEL_HOST)
from pulse.derivative_reporting import derivative_label,quote_center,quote_table_rows

ISIN=next('ZZ000000001'+str(digit) for digit in range(10) if isin_valid('ZZ000000001'+str(digit)))
OTHER=next('ZZ000000002'+str(digit) for digit in range(10) if isin_valid('ZZ000000002'+str(digit)))
NOW=datetime(2026,10,26,12,0,tzinfo=timezone.utc)
URL='https://markets.vontobel.com/de-de/produkte/test/'+ISIN


def observation(**changes):
    return replace(Observation(ISIN,verified_isin=ISIN,provider='Vontobel private document',source_url=URL,
        bid=1.25,ask=1.27,currency='EUR',denomination='PER_UNIT',observed_at=NOW.isoformat(),
        market_state='OPEN',delay_seconds=0,price_type='BID',response_status='OK'),**changes)


def validate(value,now=NOW):return validate_observation(value,now=now,allowed_hosts=(VONTOBEL_HOST,))


def test_exact_recent_bid_and_separate_ask_no_data_mutation():
    value=observation();before=asdict(value);result=validate(value)
    assert result['accepted'] and result['bid']==1.25 and result['ask']==1.27 and asdict(value)==before
    mapped=canonical_quote(dict(selected=result),now=NOW)
    assert mapped['live_price_eur']==1.25 and mapped['quote_bid']==1.25 and mapped['quote_ask']==1.27
    assert np.isnan(mapped['quote_last']) and mapped['quote_price_type']=='BID'


@pytest.mark.parametrize('changes,reason',[
    ({'verified_isin':OTHER},'EXACT_ISIN_NOT_VERIFIED'),({'verified_isin':''},'EXACT_ISIN_NOT_VERIFIED'),
    ({'requested_isin':ISIN[:-1]+'x'},'EXACT_ISIN_NOT_VERIFIED'),
    ({'source_url':'https://markets.vontobel.com.evil.invalid/product'},'UNAPPROVED_SOURCE'),
    ({'source_url':'http://markets.vontobel.com/product'},'UNAPPROVED_SOURCE'),
    ({'source_url':'https://user:secret@markets.vontobel.com/product'},'UNAPPROVED_SOURCE'),
    ({'source_url':'https://markets.vontobel.com:bad/product'},'UNAPPROVED_SOURCE'),
    ({'currency':'USD'},'EUR_CURRENCY_NOT_VERIFIED'),({'currency':''},'EUR_CURRENCY_NOT_VERIFIED'),
    ({'denomination':'PERCENT_OF_NOMINAL'},'PER_UNIT_DENOMINATION_NOT_VERIFIED'),
    ({'observed_at':'12:00:00'},'FULL_QUOTE_TIMESTAMP_REQUIRED'),({'observed_at':'2026-10-26'},'FULL_QUOTE_TIMESTAMP_REQUIRED'),
    ({'observed_at':'2026-10-26T12:00:00'},'FULL_QUOTE_TIMESTAMP_REQUIRED'),
    ({'observed_at':'2026-10-26T12:00:01+00:00'},'FUTURE_QUOTE'),
    ({'observed_at':'2026-10-26T11:30:00Z'},'STALE_QUOTE'),
    ({'observed_at':'2026-10-26T11:56:59Z'},'LIVE_QUOTE_TOO_OLD'),
    ({'bid':0},'INVALID_BID'),({'bid':float('nan')},'INVALID_BID'),({'bid':True},'INVALID_BID'),
    ({'ask':-1},'INVALID_ASK'),({'ask':1.24},'INVERTED_SPREAD'),({'ask':2.00},'SPREAD_REQUIRES_REVIEW'),
    ({'bid_size':0},'NO_VERIFIED_BID_LIQUIDITY'),({'ask_size':-1},'INVALID_ASK_SIZE'),
    ({'market_state':'KNOCKED_OUT'},'INACTIVE_INSTRUMENT'),({'market_state':'EXPIRED'},'INACTIVE_INSTRUMENT'),
    ({'market_state':'SUSPENDED'},'INACTIVE_INSTRUMENT'),({'market_state':'CLOSED'},'MARKET_NOT_VERIFIED_OPEN'),
    ({'delay_seconds':None},'DELAY_NOT_VERIFIED'),({'delay_seconds':True},'DELAY_NOT_VERIFIED'),
    ({'response_status':'NO_QUOTE_IN_PUBLIC_DOCUMENT'},'NO_QUOTE_IN_PUBLIC_DOCUMENT'),
])
def test_rejected_evidence_never_completes_valuation(changes,reason):
    result=validate(observation(**changes));assert not result['accepted'] and result['reason']==reason
    assert canonical_quote(dict(selected=result),now=NOW) is None


@pytest.mark.parametrize('changes',[
    dict(bid=None,ask=1.27,price_type='ASK'),dict(bid=None,ask=None,last=1.26,price_type='LAST'),
    dict(delay_seconds=900,observed_at='2026-10-26T11:44:00Z'),dict(market_state='CLOSED'),
])
def test_indicative_observations_remain_separate(changes):
    result=validate(observation(**changes));assert result['status']=='INDICATIVE_ONLY' and not result['accepted']


def test_weekends_new_timezone_and_bid_only():
    weekend=NOW+timedelta(days=5)
    assert not validate(observation(observed_at=weekend.isoformat()),weekend)['accepted']
    assert validate(observation(observed_at='2026-10-26T13:00:00+01:00',ask=None,bid_size=1000000))['accepted']
    assert validate(observation(observed_at='2026-10-26T11:57:00Z'))['accepted']


def test_forged_or_expired_selection_cannot_cross_canonical_boundary():
    selected=validate(observation());selected['currency']='USD'
    assert canonical_quote(dict(selected=selected),now=NOW) is None
    assert canonical_quote(dict(selected=validate(observation())),now=NOW+timedelta(seconds=181)) is None


def document(isin=ISIN,**overrides):
    product={'@type':'FinancialProduct','identifier':{'propertyID':'ISIN','value':isin},'url':URL,
             'name':'Example issuer turbo','category':'Turbo Open-End','provider':{'name':'Fictional issuer'},
             'additionalProperty':[dict(propertyID='underlying',value='Example Company'),dict(propertyID='maturity',value='Open-End'),dict(propertyID='direction',value='Call')]}
    product.update(overrides)
    return ('<script type="application/ld+json">'+json.dumps(product)+'</script><div data-testid="quote_box"><h2 data-testid="sell_price_label"><div data-testid="priceSkeleton"></div></h2><span data-testid="sell_currency_label">EUR</span><h2 data-testid="buy_price_label"><div data-testid="priceSkeleton"></div></h2><span data-testid="buy_currency_label">EUR</span><span data-testid="underlying_price_label">999.00 USD</span></div>').encode()


def test_real_document_contract_skeleton_never_uses_underlying_price():
    quote,metadata=parse_vontobel_document(ISIN,document(),URL)
    assert quote.verified_isin==ISIN and quote.bid is None and quote.ask is None and quote.observed_at==''
    assert metadata['underlying']=='Example Company' and not validate(quote)['accepted']
    assert quote.response_status=='NO_QUOTE_IN_PUBLIC_DOCUMENT'


def test_similar_product_or_search_page_is_not_identity():
    for body,url in [(document(OTHER),URL),(document(),URL+'/search'),(b'<h1>'+ISIN.encode()+b'</h1> Bid 1.25 Ask 1.26',URL)]:
        quote,metadata=parse_vontobel_document(ISIN,body,url);assert not metadata and not quote.verified_isin


class Response:
    def __init__(self,status=200,data=b'',location=''):
        from io import BytesIO
        self.status_code=status;self.data=data;self.headers={'Location':location};self.raw=BytesIO(data)
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def iter_content(self,size):yield self.data


class Session:
    def __init__(self,responses):self.responses=list(responses);self.calls=[]
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs));response=self.responses.pop(0)
        if isinstance(response,Exception):raise response
        return response


def test_bounded_transparent_identifier_only_requests_and_worker_cache():
    session=Session([Response(data=b'User-agent: *\nAllow: /'),Response(status=302,location=URL),Response(data=document())])
    collector=Collector(DocumentTransport(session));result=collector.quote(ISIN)
    assert result['metadata']['isin']==ISIN and result['selected'] is None
    assert collector.quote(ISIN) is result and len(session.calls)==3
    for url,kwargs in session.calls:
        assert 'data' not in kwargs and 'json' not in kwargs and kwargs['timeout']<=5 and not kwargs['allow_redirects']
        assert 'FolioLens' in kwargs['headers']['User-Agent']


@pytest.mark.parametrize('status',[401,403,429])
def test_restrictions_stop_further_requests_without_bypass(status):
    session=Session([Response(status=status)]);collector=Collector(DocumentTransport(session))
    assert collector.quote(ISIN)['status']=='PROVIDER_RESTRICTED'
    assert collector.quote(OTHER)['status']=='PROVIDER_RESTRICTED' and len(session.calls)==1


@pytest.mark.parametrize('responses,expected',[
    ([Response(data=b'User-agent: *\nDisallow: /')],'ROBOTS_DISALLOWS'),
    ([Response(data=b'User-agent: *\nAllow: /'),Response(status=302,location='https://127.0.0.1/private')],'REDIRECT_REQUIRES_REVIEW'),
    ([TimeoutError('PRIVATE_EXCEPTION_MUST_NOT_ESCAPE')],'PROVIDER_NETWORK_FAILURE'),
    ([Response(data=b'User-agent: *\nAllow: /'),Response(data=b'x'*(4*1024*1024+1))],'PROVIDER_RESPONSE_TOO_LARGE'),
])
def test_malformed_network_and_resource_failures_are_sanitized(responses,expected):
    collector=Collector(DocumentTransport(Session(responses)));assert collector.quote(ISIN)['status']==expected
    assert 'PRIVATE_EXCEPTION' not in json.dumps(collector.results)


def test_request_and_time_limits():
    session=Session([Response(data=b'User-agent: *\nAllow: /')]);collector=Collector(DocumentTransport(session,request_limit=1))
    assert collector.quote(ISIN)['status']=='QUOTE_REQUEST_BUDGET' and len(session.calls)==1
    assert Collector(DocumentTransport(Session([]),budget_seconds=0)).quote(ISIN)['status']=='QUOTE_REQUEST_BUDGET'


def position(**updates):return dict(isin=ISIN,security_name='Call 1.600 $',derivative_kind='Warrant / Option certificate',direction='CALL',position_status='ACTIVE',current_quantity=2,**updates)


def ledger(description):return [dict(isin=ISIN,description_clean=description)]


def test_names_are_dynamic_and_exact_isin_bound():
    p=position();g=ledger('Buy trade '+ISIN+' Optionsschein auf EXAMPLE COMPANY DL-,001, quantity: 2')
    label=derivative_label(p,g);assert label=='EXAMPLE COMPANY — Call $1,600 — Expiry unavailable — '+ISIN
    metadata=dict(isin=ISIN,identity='EXACT_ISIN',underlying='Verified Company',maturity='2029-01-19')
    assert 'Verified Company — Call $1,600 — 19 Jan 2029' in derivative_label(p,g,metadata)
    assert 'Verified Company' not in derivative_label(p,g,dict(metadata,isin=OTHER))
    assert derivative_label(p,[])==p['security_name']+' · '+ISIN


def test_turbo_historical_levels_never_become_current_strikes():
    p=dict(position(),security_name='Long 123,456 $',direction='LONG',derivative_kind='Open End Turbo')
    label=derivative_label(p,ledger('Buy trade '+ISIN+' Open End Turbo auf Example Inc.'))
    assert label=='Example Inc — Long Open End — '+ISIN and '123' not in label


def test_conflicting_descriptions_do_not_invent_a_company():
    p=position();g=ledger('Optionsschein auf One Company')+ledger('Optionsschein auf Another Company')
    assert derivative_label(p,g)==p['security_name']+' · '+ISIN


def test_display_counts_partial_manual_coverage_and_no_raw_evidence():
    raw=dict(active_derivatives=[dict(position(),live_price_eur=1.5,quote_status='MANUAL_CONFIRMED'),dict(position(),isin=OTHER,live_price_eur=None,quote_status='UNAVAILABLE')],
             derivative_ledger=ledger('Optionsschein auf Example Inc.'),derivative_quote_evidence={ISIN:dict(attempts=[dict(raw_evidence='PRIVATE_RAW_RESPONSE')],status='NO_BID')})
    before=deepcopy(raw);center=quote_center(raw)
    assert (center['active'],center['automatic'],center['missing'])==(2,0,1)
    assert center['rows'][0]['value']==3 and center['rows'][1]['value'] is None
    assert raw==before and 'PRIVATE_RAW_RESPONSE' not in json.dumps(quote_table_rows(center))
    assert quote_center(dict(active_derivatives=[]))['active']==0
