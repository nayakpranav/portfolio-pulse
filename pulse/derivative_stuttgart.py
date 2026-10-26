"""Official free MiFIR delayed pre-trade files, never the retail quote scraper.

The published bulk dataset can exceed a personal worker's bounded budget. Reject
it explicitly rather than hang, download indefinitely, or use an incomplete scan.
All observations from this source remain delayed/indicative, not current bids.
"""
import gzip
import io
import json
import re
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from pulse.derivative_quotes import Observation,ProviderError,USER_AGENT

LISTING='https://www.boerse-stuttgart.de/de-de/fuer-geschaeftspartner/reports/mifir-ii-delayed-data/xtsu-pre-trade-vhp/'
DOWNLOAD_HOST='ddl.service.boerse-stuttgart.de'
CDN_HOST='d3s5kssjdy247b.cloudfront.net'
MAX_COMPRESSED_BYTES=512*1024*1024
MAX_EXPANDED_BYTES=8*1024*1024*1024


class BoundedRaw(io.RawIOBase):
    """Buffer HTTP reads without retaining the compressed exchange universe."""
    def __init__(self,response):self.response=response;self.count=0
    def readable(self):return True
    def readinto(self,buffer):
        # urllib3 closes the raw stream on the last content-length read. Returning
        # EOF here lets gzip validate its footer/CRC rather than reading a closed
        # HTTP stream; a truncated gzip still raises and discards every result.
        if self.response.closed:return 0
        data=self.response.read(len(buffer));self.count+=len(data)
        if self.count>MAX_COMPRESSED_BYTES:raise ProviderError('DATASET_EXCEEDS_QUOTE_BUDGET')
        buffer[:len(data)]=data;return len(data)


def parse_pretrade(data,isins,source_url):
    """Observed official gzip/JSON-line contract, bounded and exact-ISIN scoped."""
    if len(data)>MAX_COMPRESSED_BYTES:raise ProviderError('DATASET_EXCEEDS_QUOTE_BUDGET')
    return parse_stream(io.BytesIO(data),isins,source_url)


def parse_stream(compressed,isins,source_url,deadline=float('inf')):
    keys={i for i in isins if isinstance(i,str) and re.fullmatch(r'[A-Z0-9]{12}',i)}
    if not keys:return {}
    pattern=re.compile(rb'"Isin"\s*:\s*"('+b'|'.join(re.escape(i.encode()) for i in sorted(keys))+rb')"')
    result={};expanded=0;first=True;carry=b''
    try:
        with gzip.GzipFile(fileobj=compressed) as stream:
            while True:
                block=stream.read(4*1024*1024)
                if not block:break
                expanded+=len(block)
                if expanded>MAX_EXPANDED_BYTES:raise ProviderError('DATASET_EXCEEDS_QUOTE_BUDGET')
                if time.monotonic()>deadline:raise ProviderError('QUOTE_REQUEST_BUDGET')
                if first:
                    if not block.lstrip().startswith(b'['):raise ProviderError('UNVERIFIED_FEED_SCHEMA')
                    first=False
                block=carry+block;end=block.rfind(b'}')+1;process=block[:end];carry=block[end:]
                if len(carry)>8192:raise ProviderError('DATASET_EXCEEDS_QUOTE_BUDGET')
                for match in pattern.finditer(process):
                    start=process.rfind(b'{',0,match.start());finish=process.find(b'}',match.end())+1
                    if start<0 or finish<=match.end() or finish-start>8192:raise ProviderError('UNVERIFIED_FEED_SCHEMA')
                    row=json.loads(process[start:finish])
                    if not isinstance(row,dict) or row.get('Type')!='pre-trade' or row.get('VenueOfPublication')!='XSTU' or row.get('Isin') not in keys:raise ProviderError('UNVERIFIED_FEED_SCHEMA')
                    isin=row['Isin'];previous=result.get(isin)
                    if previous and previous['TransactionTime']==row.get('TransactionTime') and any(previous.get(k)!=row.get(k) for k in ['Bid','Ask','BidSize','AskSize']):raise ProviderError('AMBIGUOUS_SAME_TIME_QUOTES')
                    if previous is None or str(row.get('TransactionTime',''))>str(previous.get('TransactionTime','')):result[isin]=row
        if carry.strip() not in {b']',b'[]'}:raise ProviderError('INCOMPLETE_FEED')
    except ProviderError:raise
    except Exception:raise ProviderError('MALFORMED_PROVIDER_FEED') from None
    observations={}
    for isin,row in result.items():
        # Currency is explicit. The feed does not verify per-unit convention or
        # current open/active status on its own, and its known 15m delay remains.
        observations[isin]=Observation(isin,verified_isin=isin,provider='Stuttgart MiFIR delayed pre-trade',source_url=source_url,venue='XSTU',bid=row.get('Bid'),ask=row.get('Ask'),bid_size=row.get('BidSize'),ask_size=row.get('AskSize'),currency=row.get('PriceCurrency',''),observed_at=row.get('TransactionTime',''),delay_seconds=900,price_type='BID',market_state='UNVERIFIED',response_status='OK',raw_evidence=json.dumps(row,separators=(',',':')))
    return observations


class DelayedFileProvider:
    def __init__(self,isins,session=None):
        if session is None:
            import requests
            session=requests.Session()
        self.session=session;self.isins=set(isins);self.loaded=False;self.observations={};self.status='NOT_REQUESTED';self.started=None;self.requests=0

    def _get(self,url,limit,redirect=False,feed=False):
        parsed=urlparse(url)
        if parsed.scheme!='https' or parsed.hostname not in {'www.boerse-stuttgart.de',DOWNLOAD_HOST,CDN_HOST} or parsed.username or parsed.password or parsed.port not in {None,443}:raise ProviderError('UNAPPROVED_SOURCE')
        if self.started is None:self.started=time.monotonic()
        remaining=40-(time.monotonic()-self.started)
        if remaining<=0 or self.requests>=5:raise ProviderError('QUOTE_REQUEST_BUDGET')
        self.requests+=1
        try:
            with self.session.get(url,headers={'User-Agent':USER_AGENT,'Cache-Control':'no-cache'},timeout=min(10,remaining),allow_redirects=False,stream=True) as response:
                # A robots 4xx is unavailable, not a Disallow rule. This exception
                # is confined to the explicitly licensed official file service;
                # it does not excuse a denied file, rate limit or parsed rule.
                if parsed.hostname=='www.boerse-stuttgart.de' and parsed.path=='/robots.txt' and response.status_code in {401,403,404}:return b''
                if response.status_code in {401,403,429}:raise ProviderError('PROVIDER_RESTRICTED')
                if redirect and response.status_code in {301,302,303,307,308}:return response.headers.get('Location','')
                if response.status_code!=200:raise ProviderError('PROVIDER_UNAVAILABLE')
                if int(response.headers.get('Content-Length') or 0)>limit:raise ProviderError('DATASET_EXCEEDS_QUOTE_BUDGET')
                if feed:
                    with io.BufferedReader(BoundedRaw(response.raw),buffer_size=1024*1024) as buffered:
                        return parse_stream(buffered,self.isins,LISTING,self.started+40)
                parts=[];size=0
                for part in response.iter_content(65536):
                    size+=len(part)
                    if size>limit:raise ProviderError('DATASET_EXCEEDS_QUOTE_BUDGET')
                    if time.monotonic()-self.started>40:raise ProviderError('QUOTE_REQUEST_BUDGET')
                    parts.append(part)
                return b''.join(parts)
        except ProviderError:raise
        except Exception as error:
            reason='PROVIDER_TIMEOUT' if isinstance(error,TimeoutError) or type(error).__name__ in {'ReadTimeout','ConnectTimeout','Timeout'} else 'PROVIDER_NETWORK_FAILURE'
            raise ProviderError(reason) from None

    def load(self):
        if self.loaded:return
        self.loaded=True
        try:
            robots=self._get('https://www.boerse-stuttgart.de/robots.txt',512*1024)
            policy=RobotFileParser();policy.parse(robots.decode('utf-8',errors='replace').splitlines())
            if not policy.can_fetch(USER_AGENT,LISTING):raise ProviderError('ROBOTS_DISALLOWS')
            from bs4 import BeautifulSoup
            page=BeautifulSoup(self._get(LISTING,4*1024*1024),'html.parser');candidates=[]
            for row in page.find_all('tr'):
                match=re.search(r'XSTU-pretrade-VHP-(\d{8}T\d{4})\.json\.gz',row.get_text())
                link=row.find('a',href=True)
                if match and link and re.fullmatch(r'https://'+re.escape(DOWNLOAD_HOST)+r'/s/[A-Za-z0-9]+',link['href']):candidates.append((match.group(1),link['href']))
            if not candidates:raise ProviderError('UNVERIFIED_FEED_LISTING')
            filename,link=max(candidates);target=self._get(link,0,redirect=True)
            parsed=urlparse(target)
            expected='/XSTU-pretrade-VHP-'+filename+'.json.gz'
            if parsed.scheme!='https' or parsed.hostname!=CDN_HOST or parsed.path!=expected:raise ProviderError('REDIRECT_REQUIRES_REVIEW')
            # Keep the stable official listing as provenance, never distribute
            # the exchange-issued temporary signed download capability.
            self.observations=self._get(target,MAX_COMPRESSED_BYTES,feed=True);self.status='DELAYED_FEED'
        except ProviderError as error:self.status=str(error)
        except Exception:self.status='MALFORMED_PROVIDER_FEED'

    def quote(self,isin):
        self.load()
        return self.observations.get(isin,Observation(isin,provider='Stuttgart MiFIR delayed pre-trade',source_url=LISTING,response_status=self.status if self.status!='DELAYED_FEED' else 'ISIN_NOT_IN_FEED'))
