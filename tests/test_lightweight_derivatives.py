"""Synthetic dated-quote, local-cache and supplementary-estimate regressions."""
from copy import deepcopy
from dataclasses import replace,asdict
from datetime import datetime,timezone,timedelta,date
from io import BytesIO
import json
from pathlib import Path
import pytest
from pulse.derivative_quotes import (Collector,DocumentTransport,Observation,validate_indicative,
    validate_observation,VONTOBEL_HOST,MAX_DOCUMENT_BYTES,COLLECTION_SECONDS)
from pulse.derivative_cache import QuoteCache
from test_derivative_quotes import ISIN,OTHER,URL,NOW,observation,Session,Response,document


def dated(**changes):return validate_indicative(observation(**changes),now=NOW,allowed_hosts=(VONTOBEL_HOST,))


@pytest.mark.parametrize('changes',[
    dict(ask=None,delay_seconds=900,observed_at='2026-10-26T11:44:00Z'),
    dict(ask=None,market_state='CLOSED',observed_at='2026-10-23T16:00:00Z'),
    dict(ask=None,market_state='',delay_seconds=None),
    dict(ask=None,observed_at='2026-10-26T10:00:00Z'),
])
def test_bid_does_not_require_ask_or_live_market(changes):
    result=dated(**changes)
    assert result['eligible_indicative'] and not result['accepted']
    assert result['bid']==1.25 and result['price_type']=='BID' and result['denomination']=='PER_UNIT'


@pytest.mark.parametrize('changes',[
    dict(verified_isin=OTHER),dict(currency='USD'),dict(denomination='PERCENT'),dict(denomination=''),
    dict(bid=None,ask=1.27,price_type='ASK'),dict(bid=None,last=1.27,price_type='LAST'),
    dict(bid=0),dict(bid=float('nan')),dict(observed_at='12:00'),dict(observed_at='2026-10-26T13:00:00Z'),
    dict(observed_at='2026-10-22T16:00:00Z'),dict(ask=1.24),dict(market_state='KNOCKED_OUT'),
    dict(market_state='EXPIRED'),dict(source_url='https://evil.invalid/'+ISIN),
])
def test_weak_or_expired_evidence_never_becomes_indicative_value(changes):
    assert not dated(**changes)['eligible_indicative']


def test_weekend_previous_friday_and_conservative_holiday_floor():
    quote=observation(observed_at='2026-10-23T16:00:00Z',market_state='CLOSED')
    assert validate_indicative(quote,now=datetime(2026,10,25,12,tzinfo=timezone.utc),allowed_hosts=(VONTOBEL_HOST,))['eligible_indicative']
    assert not validate_indicative(quote,now=NOW+timedelta(days=1),allowed_hosts=(VONTOBEL_HOST,))['eligible_indicative']


def record(quote=None):
    quote=quote or observation(ask=None,delay_seconds=900)
    current=validate_observation(quote,now=NOW,allowed_hosts=(VONTOBEL_HOST,))
    return dict(attempts=[current],metadata={},evaluated_at=NOW.isoformat(),status=current['reason'])


def test_cache_revalidates_exact_public_evidence_and_never_stores_portfolio(tmp_path):
    cache=QuoteCache(tmp_path);data=record();data['attempts'][0]['raw_evidence']='PRIVATE_RAW_BODY';before=deepcopy(data)
    cache.put(ISIN,data);assert data==before
    later=cache.get(ISIN,NOW+timedelta(hours=1))
    assert later['cache']=='HIT' and later['indicative']['bid']==1.25 and later['selected'] is None
    assert later['network']['requests']==0 and not later['attempts'][0]['raw_evidence']
    assert QuoteCache(tmp_path/'other-owner').get(ISIN,NOW) is None
    assert cache.get(OTHER,NOW) is None and cache.get(ISIN,NOW-timedelta(seconds=1)) is None
    assert cache.get(ISIN,NOW+timedelta(days=1)) is None
    assert cache.get(ISIN,NOW+timedelta(days=2),allow_previous=True) is None


def test_once_per_weekday_including_negative_result_cache(tmp_path,monkeypatch):
    import pulse.derivative_quotes as module
    class Fixed(datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    monkeypatch.setattr(module,'datetime',Fixed)
    cache=QuoteCache(tmp_path);first=Collector(DocumentTransport(Session([Response(status=404)])),cache=cache).quote(ISIN)
    assert first['network']['requests']==1
    session=Session([]);second=Collector(DocumentTransport(session),cache=cache).quote(ISIN)
    assert second['cache']=='HIT' and not session.calls and second['attempts'][0]['reason']=='INSTRUMENT_NOT_FOUND'


def test_next_day_failure_can_reuse_only_still_valid_observation(tmp_path,monkeypatch):
    import pulse.derivative_quotes as module
    class Fixed(datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    monkeypatch.setattr(module,'datetime',Fixed)
    cache=QuoteCache(tmp_path)
    quote=observation(observed_at='2026-10-23T16:00:00Z',ask=None,market_state='CLOSED')
    old=record(quote);old['evaluated_at']='2026-10-23T17:00:00Z';cache.put(ISIN,old)
    result=Collector(DocumentTransport(Session([Response(status=404)])),cache=cache).quote(ISIN)
    assert result['cache']=='DATED_FALLBACK' and result['indicative']['observed_at']==quote.observed_at
    assert result['selected'] is None and cache.get(ISIN,NOW)['indicative']


def test_cache_corruption_lock_and_unwritable_store_are_nonblocking(tmp_path):
    cache=QuoteCache(tmp_path);tmp_path.mkdir(exist_ok=True)
    cache.path.write_bytes(b'broken');assert cache.get(ISIN,NOW) is None
    (tmp_path/'write.lock').write_bytes(b'')
    cache.put(ISIN,record());assert cache.warning=='CACHE_WRITE_UNAVAILABLE'
    assert QuoteCache(tmp_path).get(ISIN,NOW) is None


def test_same_day_current_cache_demotes_to_indicative_without_resetting_quote_time(tmp_path):
    cache=QuoteCache(tmp_path);cache.put(ISIN,record(observation(ask=None)))
    assert cache.get(ISIN,NOW)['selected']['accepted']
    later=cache.get(ISIN,NOW+timedelta(hours=2))
    assert later['selected'] is None and later['indicative']['observed_at']==NOW.isoformat()
    assert later['indicative']['age_seconds']==7200


def test_mixed_manual_and_indicative_position_values_remain_separate():
    from pulse.derivative_reporting import quote_center
    from test_derivative_quotes import position
    dated_bid=dated(ask=None,delay_seconds=900)
    raw=dict(active_derivatives=[dict(position(),live_price_eur=2,quote_status='MANUAL_CONFIRMED'),
        dict(position(),isin=OTHER,live_price_eur=None,quote_status='UNAVAILABLE')],
        derivative_quote_evidence={OTHER:dict(indicative=dict(dated_bid,requested_isin=OTHER,verified_isin=OTHER),evaluated_at=NOW.isoformat())})
    center=quote_center(raw)
    assert center['indicative_total']==6.5 and center['missing']==1 and center['indicative']==1
    assert center['rows'][0]['value']==4 and center['rows'][1]['value'] is None
    assert center['rows'][1]['indicative_value']==2.5


def test_no_bulk_adapter_import_and_small_budget():
    import inspect,pulse.derivative_quotes as module
    assert 'DelayedFileProvider' not in inspect.getsource(Collector)
    assert COLLECTION_SECONDS<=10 and MAX_DOCUMENT_BYTES<=512*1024
    assert not Path(module.__file__).with_name('derivative_stuttgart.py').exists()
    for url in ['https://www.boerse-stuttgart.de/file','https://ddl.service.boerse-stuttgart.de/s/x','https://d3s5kssjdy247b.cloudfront.net/a.gz']:
        with pytest.raises(module.ProviderError,match='UNAPPROVED_SOURCE'):DocumentTransport(Session([])).document(url)


def test_oversized_document_rejected_before_body_consumption():
    response=Response();response.headers['Content-Length']=str(MAX_DOCUMENT_BYTES+1)
    transport=DocumentTransport(Session([response]))
    from pulse.derivative_quotes import ProviderError
    with pytest.raises(ProviderError,match='PROVIDER_RESPONSE_TOO_LARGE'):transport._download(URL)
    assert transport.bytes==0 and transport.count==1


def test_separate_rollup_reports_and_canonical_unavailability_remain_unchanged():
    from pulse.synthetic import fixture
    from pulse.runner import run_analysis
    from pulse.adapter import prepare
    from pulse.derivative_reporting import indicative_summary
    from pulse.html_report import html_report
    from pulse.pdf import summary_pdf
    from pypdf import PdfReader
    data,prices=fixture('open_derivatives');raw=run_analysis(data,prices=prices);before=prepare(raw,date(2026,10,26))
    raw['synthetic']=False;raw['derivative_quote_evidence']={}
    total=0
    for index,pos in enumerate(raw['active_derivatives'],1):
        from pulse.derivative_quotes import isin_valid
        prefix=f'ZZ{index:09d}'
        isin=next(prefix+str(n) for n in range(10) if isin_valid(prefix+str(n)))
        pos['isin']=isin
        quote=replace(observation(ask=None,delay_seconds=900),requested_isin=isin,verified_isin=isin)
        bid=validate_indicative(quote,now=NOW,allowed_hosts=(VONTOBEL_HOST,))
        assert bid['eligible_indicative'],bid['reason']
        raw['derivative_quote_evidence'][isin]=dict(indicative=bid,attempts=[],metadata={},evaluated_at=NOW.isoformat())
        total+=pos['current_quantity']*1.25
    original=deepcopy(raw);model=prepare(raw,date(2026,10,26));center=model['derivative_quotes']
    assert center['indicative_total']==total and center['indicative_coverage']==7 and center['automatic']==0 and center['missing']==7
    assert center['full_portfolio_estimate']==model['dependencies']['stock_value']+total
    assert model['full_totals']['value'] is None and model['full_totals']['profit'] is None
    assert raw==original and before['by_key']['mwr']==model['by_key']['mwr'] and before['by_key']['twr']==model['by_key']['twr']
    assert not model['dependencies']['full_valued']
    text=html_report(model).decode();pdf=PdfReader(BytesIO(summary_pdf(model)))
    assert 'Indicative derivative total' in text and 'Indicative full-portfolio estimate' in text and len(pdf.pages)==1
    assert f'{total:,.2f}' in pdf.pages[0].extract_text() and f'{total:,.2f}' in text
    raw['derivative_quote_evidence'].pop(raw['active_derivatives'][0]['isin'])
    partial=prepare(raw,date(2026,10,26))['derivative_quotes']
    assert partial['indicative_total'] is None and partial['full_portfolio_estimate'] is None
    assert 'known subtotal' in indicative_summary(partial)
