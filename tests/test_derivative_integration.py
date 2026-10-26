"""Synthetic provider/portfolio integration. No financial inputs from the owner."""
import gzip
import json
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timezone,date
import pandas as pd
import pytest
from pulse.derivative_stuttgart import parse_pretrade,DelayedFileProvider,LISTING,MAX_COMPRESSED_BYTES
from pulse.derivative_quotes import ProviderError,Observation,validate_observation
from pulse.market import install_derivatives,allowed_host
from test_derivative_quotes import ISIN,OTHER,URL,NOW,observation,validate,Session,Response,document


def feed_row(**changes):
    return dict(Type='pre-trade',VenueOfPublication='XSTU',Isin=ISIN,Bid=1.25,Ask=1.27,
                BidSize=1000,AskSize=2000,TransactionTime=NOW.isoformat(),PriceCurrency='EUR',InstrumentStatus=' ',**changes)


def compressed(rows):return gzip.compress(('['+',\n'.join(json.dumps(row) for row in rows)+']').encode())


def test_official_schema_exact_filter_bid_ask_time_and_unknown_units():
    rows=[feed_row(),dict(feed_row(),Isin=OTHER,Bid=9.5,Ask=9.6)]
    observations=parse_pretrade(compressed(rows),[ISIN],LISTING)
    assert set(observations)=={ISIN}
    found=observations[ISIN];assert found.bid==1.25 and found.ask==1.27 and found.observed_at==NOW.isoformat()
    assert found.denomination=='' and found.delay_seconds==900
    decision=validate_observation(found,now=NOW,allowed_hosts=('www.boerse-stuttgart.de',))
    assert not decision['accepted'] and decision['reason']=='PER_UNIT_DENOMINATION_NOT_VERIFIED'


def test_latest_observation_and_ambiguous_duplicate():
    old=dict(feed_row(),TransactionTime='2026-10-26T11:59:00Z',Bid=1.2)
    assert parse_pretrade(compressed([old,feed_row()]),[ISIN],LISTING)[ISIN].bid==1.25
    with pytest.raises(ProviderError,match='AMBIGUOUS_SAME_TIME_QUOTES'):
        parse_pretrade(compressed([feed_row(),dict(feed_row(),Bid=1.24)]),[ISIN],LISTING)


@pytest.mark.parametrize('data',[b'bad gzip',gzip.compress(b'{"wrong":"schema"}'),gzip.compress(b'['+b'x'*8193)])
def test_bad_truncated_or_pathological_feeds_fail_closed(data):
    with pytest.raises(ProviderError):parse_pretrade(data,[ISIN],LISTING)


def test_compressed_and_expansion_limits(monkeypatch):
    import pulse.derivative_stuttgart as module
    monkeypatch.setattr(module,'MAX_COMPRESSED_BYTES',8)
    with pytest.raises(ProviderError,match='DATASET_EXCEEDS_QUOTE_BUDGET'):parse_pretrade(b'x'*9,[ISIN],LISTING)
    monkeypatch.setattr(module,'MAX_COMPRESSED_BYTES',1000);monkeypatch.setattr(module,'MAX_EXPANDED_BYTES',100)
    with pytest.raises(ProviderError,match='DATASET_EXCEEDS_QUOTE_BUDGET'):parse_pretrade(compressed([feed_row()]),[ISIN],LISTING)


def test_buffered_stream_checks_crc_and_http_close_without_loading_universe():
    from io import BytesIO,BufferedReader
    from pulse.derivative_stuttgart import BoundedRaw,parse_stream
    class AutoClosing(BytesIO):
        def read(self,size=-1):
            data=super().read(size)
            if self.tell()==len(self.getvalue()):self.close()
            return data
    data=compressed([feed_row()])
    with BufferedReader(BoundedRaw(AutoClosing(data)),buffer_size=1024) as stream:
        assert parse_stream(stream,[ISIN],LISTING)[ISIN].bid==1.25
    broken=data[:-8]+b'bad-CRC!'
    with pytest.raises(ProviderError,match='MALFORMED_PROVIDER_FEED'):parse_pretrade(broken,[ISIN],LISTING)
    assert not parse_pretrade(compressed([]),[ISIN],LISTING)


def test_stream_deadline_discards_even_an_already_found_quote():
    from io import BytesIO
    from pulse.derivative_stuttgart import parse_stream
    with pytest.raises(ProviderError,match='QUOTE_REQUEST_BUDGET'):
        parse_stream(BytesIO(compressed([feed_row()])),[ISIN],LISTING,deadline=0)


def test_published_file_uses_only_reviewed_exchange_issued_redirect():
    listing=b'<table><tr><td>XSTU-pretrade-VHP-20261026T1200.json.gz</td><td><a href="https://ddl.service.boerse-stuttgart.de/s/abc123">Download</a></td></tr></table>'
    fileurl='https://d3s5kssjdy247b.cloudfront.net/XSTU-pretrade-VHP-20261026T1200.json.gz?public-signature=not-a-secret'
    session=Session([Response(data=b'User-agent: *\nAllow: /'),Response(data=listing),Response(status=302,location=fileurl),Response(data=compressed([feed_row()]))])
    source=DelayedFileProvider([ISIN,OTHER],session);found=source.quote(ISIN)
    assert found.verified_isin==ISIN and found.bid==1.25 and source.quote(OTHER).response_status=='ISIN_NOT_IN_FEED'
    assert len(session.calls)==4 and found.source_url==LISTING and 'signature' not in found.raw_evidence


def test_large_actual_dataset_is_rejected_from_header_before_reading():
    source=DelayedFileProvider([ISIN],Session([]));response=Response();response.headers['Content-Length']=str(MAX_COMPRESSED_BYTES+1)
    source.session=Session([response])
    with pytest.raises(ProviderError,match='DATASET_EXCEEDS_QUOTE_BUDGET'):
        source._get('https://d3s5kssjdy247b.cloudfront.net/file.gz',MAX_COMPRESSED_BYTES)


def test_official_listing_timeout_is_bounded_and_sanitized():
    session=Session([TimeoutError('PRIVATE_ERROR_CONTENT')]);source=DelayedFileProvider([ISIN],session)
    with pytest.raises(ProviderError,match='^PROVIDER_TIMEOUT$'):
        source._get(LISTING,4*1024*1024)
    assert session.calls[0][1]['timeout']<=10


def test_feed_access_restriction_is_memoized_no_retry():
    session=Session([Response(data=b'User-agent: *\nAllow: /'),Response(status=403)]);source=DelayedFileProvider([ISIN,OTHER],session)
    assert source.quote(ISIN).response_status=='PROVIDER_RESTRICTED'
    assert source.quote(OTHER).response_status=='PROVIDER_RESTRICTED' and len(session.calls)==2


@pytest.mark.parametrize('robots_status',[401,403,404])
def test_unavailable_robots_does_not_erase_explicit_official_download_grant(robots_status):
    session=Session([Response(status=robots_status),Response(data=b'<table></table>')]);source=DelayedFileProvider([ISIN],session)
    assert source.quote(ISIN).response_status=='UNVERIFIED_FEED_LISTING' and len(session.calls)==2
    limited=DelayedFileProvider([ISIN],Session([Response(status=429)]))
    assert limited.quote(ISIN).response_status=='PROVIDER_RESTRICTED'


def test_automatic_bid_precedes_optional_manual_and_missing_remains_nan(monkeypatch):
    import pulse.derivative_quotes as quotes
    class Fixed(datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    monkeypatch.setattr(quotes,'datetime',Fixed)
    monkeypatch.setenv('FOLIOLENS_AUTO_DERIVATIVE_DOCUMENTS','1');monkeypatch.delenv('FOLIOLENS_REPLAY_PATH',raising=False)
    selected=validate(observation());results={ISIN:dict(selected=selected,attempts=[selected],metadata={},status='AUTO_VERIFIED_BID')}
    class Fake:
        def __init__(self,*,isins):assert isins==[ISIN,OTHER]
        def quote(self,isin):return results.get(isin,dict(selected=None,attempts=[],metadata={},status='MISSING'))
    monkeypatch.setattr(quotes,'Collector',Fake)
    monkeypatch.setattr('pulse.valuations.today',lambda:date(2026,10,26))
    ns=dict(derivative_positions=pd.DataFrame([dict(isin=ISIN,position_status='ACTIVE'),dict(isin=OTHER,position_status='ACTIVE')]))
    config={'derivative_quotes':{ISIN:dict(price_eur=9,date='2026-10-26',source='Fictional manual fallback')}}
    install_derivatives(ns,config)
    assert ns['multi_source_derivative_quote_probe'](ISIN)['live_price_eur']==1.25
    missing=ns['multi_source_derivative_quote_probe'](OTHER);assert pd.isna(missing['live_price_eur'])
    results.clear();install_derivatives(ns,config)
    assert ns['multi_source_derivative_quote_probe'](ISIN)['live_price_eur']==9


def test_no_unreviewed_destinations_or_default_hosted_access():
    for url in [URL,LISTING,'https://ddl.service.boerse-stuttgart.de/s/example']:
        assert not allowed_host(url) and allowed_host(url,True)
    assert not allowed_host('https://www.boerse-stuttgart.de.evil.invalid/file',True)


@pytest.mark.parametrize('deployment,synthetic,enabled',[
    ('personal',False,'1'),('personal',True,'0'),('owner_hosted',False,'0'),('public_demo',True,'0')])
def test_worker_gate_is_derived_not_inherited(tmp_path,monkeypatch,deployment,synthetic,enabled):
    from pulse.runner import run_analysis
    from pulse.synthetic import fixture
    from pulse.private_config import export_digest
    monkeypatch.setenv('FOLIOLENS_MODE',deployment);monkeypatch.setenv('FOLIOLENS_OWNER_GATE_VERIFIED','1');monkeypatch.setenv('FOLIOLENS_DATA_RIGHTS_VERIFIED','1')
    monkeypatch.setenv('FOLIOLENS_AUTO_DERIVATIVE_DOCUMENTS','1');monkeypatch.delenv('STREAMLIT_SHARING_MODE',raising=False);monkeypatch.delenv('IS_STREAMLIT_CLOUD',raising=False)
    data,prices=fixture()
    def execute(command,work,env,timeout):
        assert env['FOLIOLENS_AUTO_DERIVATIVE_DOCUMENTS']==enabled
        (work/'result.json').write_text('{}');return 0
    monkeypatch.setattr('pulse.runner.execute_worker',execute)
    run_analysis(data,prices=prices if synthetic else None,private_config=dict(export_sha256=export_digest(data)))


def test_report_model_excludes_raw_quotes_and_escapes_labels():
    from pulse.synthetic import fixture
    from pulse.runner import run_analysis
    from pulse.adapter import prepare
    from pulse.html_report import html_report
    from pulse.pdf import summary_pdf
    from pypdf import PdfReader
    from io import BytesIO
    data,prices=fixture('open_derivatives');raw=run_analysis(data,prices=prices)
    raw['synthetic']=False;target=raw['active_derivatives'][0]['isin']
    raw['derivative_quote_evidence']={target:dict(attempts=[dict(raw_evidence='PRIVATE_PROVIDER_BODY')],metadata=dict(isin=target,identity='EXACT_ISIN',underlying='<img src=x onerror=alert(1)>'),status='NO_QUOTE')}
    model=prepare(raw,date(2026,10,26));before=deepcopy(raw)
    text=html_report(model).decode();pdf=PdfReader(BytesIO(summary_pdf(model)))
    assert 'Derivative Quote Center' in text and 'PRIVATE_PROVIDER_BODY' not in text and '<img src=x' not in text
    assert '&lt;img' in text and len(pdf.pages)==1 and raw==before
    assert all(m.display in text and m.display.replace('€','EUR ') in pdf.pages[0].extract_text() for m in model['metrics'])
