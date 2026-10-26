"""Strict quote evidence, isolated from accounting and broker activity.

Only an exact-ISIN, recent EUR-per-unit BID can cross the valuation boundary.
The private-copy provider currently often supplies identity without a quote.
Missing evidence is an unsuccessful result, never a reason to scrape another API.
"""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import time
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

USER_AGENT = 'FolioLens-Personal/1.0 (private noncommercial document retrieval)'
VONTOBEL_HOST = 'markets.vontobel.com'
LIVE_AGE_SECONDS = 180
DELAYED_AGE_SECONDS = 1200
MAX_DOCUMENT_BYTES = 4*1024*1024


def isin_valid(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Z]{2}[A-Z0-9]{9}[0-9]', value):
        return False
    digits=''.join(str(int(c,36)) if c.isalpha() else c for c in value)
    return sum(sum(divmod(int(c)*(2 if i%2 else 1),10)) for i,c in enumerate(reversed(digits)))%10==0


def finite_positive(value):
    if isinstance(value,bool):return None
    try:
        number=float(value)
        return number if math.isfinite(number) and 0<number<=100000 else None
    except (ValueError,TypeError,OverflowError):return None


def timestamp(value):
    # A full date and offset are essential; neither today's date nor a timezone
    # is inferred from a date-only / time-only / relative website label.
    if not isinstance(value,str) or not re.match(r'^\d{4}-\d{2}-\d{2}T',value):return None
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except ValueError:return None


@dataclass(frozen=True)
class Observation:
    requested_isin: str
    verified_isin: str = ''
    provider: str = ''
    source_url: str = ''
    instrument_name: str = ''
    issuer: str = ''
    venue: str = ''
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    currency: str = ''
    denomination: str = ''
    observed_at: str = ''
    market_state: str = ''
    delay_seconds: int | None = None
    price_type: str = ''
    response_status: str = ''
    raw_evidence: str = ''
    response_sha256: str = ''


def validate_observation(observation, *, now=None, allowed_hosts=()):
    """Return evidence with a fixed safe reason, without mutating the observation."""
    row=asdict(observation);now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError('An aware evaluation timestamp is required')
    row.update(accepted=False,status='UNAVAILABLE',freshness='UNKNOWN',identity='UNVERIFIED',confidence='NONE',reason='NO_QUOTE')
    def reject(reason, status='REJECTED'):
        row.update(reason=reason,status=status);return row
    if not observation.verified_isin and observation.response_status not in {'','OK'}:
        return reject(observation.response_status,'UNAVAILABLE')
    if not isin_valid(observation.requested_isin) or observation.verified_isin!=observation.requested_isin:
        return reject('EXACT_ISIN_NOT_VERIFIED')
    row['identity']='EXACT_ISIN'
    try:
        url=urlparse(observation.source_url)
        source_ok=url.scheme=='https' and url.hostname in allowed_hosts and not url.username and not url.password and url.port in {None,443}
    except (ValueError,TypeError):source_ok=False
    if not source_ok:
        return reject('UNAPPROVED_SOURCE')
    if observation.response_status!='OK':return reject(observation.response_status or 'NO_QUOTE','UNAVAILABLE')
    observed=timestamp(observation.observed_at)
    if observed is None:return reject('FULL_QUOTE_TIMESTAMP_REQUIRED')
    age=(now.astimezone(timezone.utc)-observed).total_seconds();row['age_seconds']=age
    if age<0:return reject('FUTURE_QUOTE')
    if age>DELAYED_AGE_SECONDS:
        row['freshness']='STALE';return reject('STALE_QUOTE')
    row['freshness']='DELAYED' if type(observation.delay_seconds) is int and observation.delay_seconds>0 else 'RECENT'
    if observation.currency!='EUR':return reject('EUR_CURRENCY_NOT_VERIFIED')
    if observation.denomination!='PER_UNIT':return reject('PER_UNIT_DENOMINATION_NOT_VERIFIED')
    bid,ask,last=(finite_positive(v) for v in (observation.bid,observation.ask,observation.last))
    if observation.bid is not None and bid is None:return reject('INVALID_BID')
    if observation.ask is not None and ask is None:return reject('INVALID_ASK')
    if bid is not None and ask is not None:
        if bid>ask:return reject('INVERTED_SPREAD')
        if (ask-bid)/bid>.5:return reject('SPREAD_REQUIRES_REVIEW')
    def valid_size(size):
        try:return not isinstance(size,bool) and math.isfinite(float(size)) and 0<float(size)<=1e15
        except (ValueError,TypeError,OverflowError):return False
    if observation.bid_size is not None and not valid_size(observation.bid_size):
        return reject('NO_VERIFIED_BID_LIQUIDITY')
    if observation.ask_size is not None and not valid_size(observation.ask_size):
        return reject('INVALID_ASK_SIZE')
    if observation.market_state in {'EXPIRED','KNOCKED_OUT','SUSPENDED'}:return reject('INACTIVE_INSTRUMENT')
    local=now.astimezone(ZoneInfo('Europe/Berlin'))
    # A weekday is not proof of trading. The provider must independently state
    # OPEN; weekends/closures never become live through a recent HTTP response.
    if observation.market_state!='OPEN' or local.weekday()>=5:
        row['freshness']='CLOSING_OR_CLOSED';return reject('MARKET_NOT_VERIFIED_OPEN','INDICATIVE_ONLY')
    if type(observation.delay_seconds) is not int or observation.delay_seconds<0:return reject('DELAY_NOT_VERIFIED')
    if observation.delay_seconds>0:
        row['freshness']='DELAYED';return reject('DELAYED_OBSERVATION_NOT_CURRENT_BID','INDICATIVE_ONLY')
    if age>LIVE_AGE_SECONDS:
        row['freshness']='STALE';return reject('LIVE_QUOTE_TOO_OLD')
    row['freshness']='RECENT'
    if observation.price_type!='BID' or bid is None:
        return reject('ASK_OR_LAST_IS_NOT_BID','INDICATIVE_ONLY' if ask is not None or last is not None else 'UNAVAILABLE')
    row.update(accepted=True,status='AUTO_VERIFIED_BID',confidence='VERIFIED_EVIDENCE',reason='RECENT_EXACT_ISIN_EUR_BID')
    return row


class ProviderError(Exception):
    """Only fixed diagnostic codes cross this boundary."""


class DocumentTransport:
    """Bounded transparent private document requests; no credentials or JS APIs."""
    def __init__(self, session=None, *, budget_seconds=25, request_limit=20, clock=time.monotonic):
        if session is None:
            import requests
            session=requests.Session()
        self.session=session;self.clock=clock;self.budget=budget_seconds;self.limit=request_limit
        self.started=None;self.count=0;self.restricted=set();self.robots={};self.last_request={}

    def _download(self,url):
        parsed=urlparse(url);host=parsed.hostname
        if parsed.scheme!='https' or host not in {VONTOBEL_HOST} or parsed.username or parsed.password or parsed.port not in {None,443}:
            raise ProviderError('UNAPPROVED_SOURCE')
        if host in self.restricted:raise ProviderError('PROVIDER_RESTRICTED')
        if self.started is None:self.started=self.clock()
        remaining=self.budget-(self.clock()-self.started)
        if remaining<=0 or self.count>=self.limit:raise ProviderError('QUOTE_REQUEST_BUDGET')
        wait=max(0,.25-(self.clock()-self.last_request.get(host,-1e9)))
        if wait:
            if wait>=remaining:raise ProviderError('QUOTE_REQUEST_BUDGET')
            time.sleep(wait)
        self.count+=1;self.last_request[host]=self.clock()
        try:
            response=self.session.get(url,headers={'User-Agent':USER_AGENT,'Accept':'text/html'},timeout=min(5,remaining),allow_redirects=False,stream=True)
            with response:
                if response.status_code in {401,403,429}:
                    self.restricted.add(host);raise ProviderError('PROVIDER_RESTRICTED')
                if response.status_code==404:raise ProviderError('INSTRUMENT_NOT_FOUND')
                if 300<=response.status_code<400:return None,response.headers.get('Location','')
                if response.status_code!=200:raise ProviderError('PROVIDER_UNAVAILABLE')
                chunks=[];size=0
                for chunk in response.iter_content(65536):
                    size+=len(chunk)
                    if size>MAX_DOCUMENT_BYTES:raise ProviderError('PROVIDER_RESPONSE_TOO_LARGE')
                    if self.clock()-self.started>self.budget:raise ProviderError('QUOTE_REQUEST_BUDGET')
                    chunks.append(chunk)
                return b''.join(chunks),''
        except ProviderError:raise
        except Exception:raise ProviderError('PROVIDER_NETWORK_FAILURE') from None

    def document(self,url):
        origin='https://'+VONTOBEL_HOST
        if VONTOBEL_HOST not in self.robots:
            data,redirect=self._download(origin+'/robots.txt')
            if redirect or data is None:raise ProviderError('ROBOTS_UNVERIFIED')
            robot=RobotFileParser();robot.parse(data.decode('utf-8',errors='replace').splitlines());self.robots[VONTOBEL_HOST]=robot
        for _ in range(3):
            if not self.robots[VONTOBEL_HOST].can_fetch(USER_AGENT,url):raise ProviderError('ROBOTS_DISALLOWS')
            data,redirect=self._download(url)
            if not redirect:return data,url
            target=urljoin(url,redirect)
            if urlparse(target).hostname!=VONTOBEL_HOST or urlparse(target).scheme!='https':raise ProviderError('REDIRECT_REQUIRES_REVIEW')
            url=target
        raise ProviderError('TOO_MANY_REDIRECTS')


def parse_vontobel_document(isin,body,url):
    """Published FinancialProduct identity, not arbitrary page/search numbers.

    Private document copies are permitted by the German website terms. This is
    not an authorization to reverse engineer its streaming/internal services.
    The observed public document contains a quote skeleton: fail closed unless
    the instrument-scoped elements include all independently required evidence.
    """
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(body,'html.parser');products=[]
    for script in soup.find_all('script',type='application/ld+json'):
        try:value=json.loads(script.string or '')
        except (ValueError,TypeError):continue
        values=value if isinstance(value,list) else [value]
        products.extend(p for p in values if isinstance(p,dict) and p.get('@type')=='FinancialProduct')
    matches=[p for p in products if isinstance(p.get('identifier'),dict) and p['identifier'].get('propertyID')=='ISIN' and p['identifier'].get('value')==isin]
    if len(matches)!=1:return Observation(isin,provider='Vontobel private document',source_url=url,response_status='EXACT_ISIN_NOT_VERIFIED'),{}
    product=matches[0];properties={p.get('propertyID'):p.get('value') for p in product.get('additionalProperty',[]) if isinstance(p,dict)}
    if not urlparse(url).path.endswith('/'+isin) or product.get('url')!=url:
        return Observation(isin,provider='Vontobel private document',source_url=url,response_status='EXACT_INSTRUMENT_PAGE_REQUIRED'),{}
    metadata=dict(isin=isin,underlying=str(properties.get('underlying') or '')[:160],product_type=str(product.get('category') or '')[:100],maturity=str(properties.get('maturity') or '')[:40],direction=str(properties.get('direction') or '')[:20],name=str(product.get('name') or '')[:200],identity='EXACT_ISIN')
    box=soup.select_one('[data-testid="quote_box"]')
    evidence=box.get_text(' ',strip=True)[:1500] if box else ''
    # Never borrow the underlying's quote or timestamp from the same container.
    def price(selector):
        node=box.select_one(selector) if box else None
        if node is None or node.select_one('[data-testid="priceSkeleton"]'):return None
        text=node.get_text(' ',strip=True)
        if not re.fullmatch(r'\d+(?:\.\d{3})*(?:,\d+)?',text):return None
        return finite_positive(text.replace('.','').replace(',','.'))
    bid=price('[data-testid="sell_price_label"]');ask=price('[data-testid="buy_price_label"]')
    currencies=[n.get_text(strip=True) for n in box.select('[data-testid="sell_currency_label"], [data-testid="buy_currency_label"]')] if box else []
    currency='EUR' if currencies and all(c=='EUR' for c in currencies) else ''
    # No proven full dated/offset quote timestamp, delay or market status was
    # present in the inspected SSR contract. Do not invent selectors/semantics.
    provider=product.get('provider') or {};issuer=str(provider.get('name') or '')[:160] if isinstance(provider,dict) else ''
    observation=Observation(isin,verified_isin=isin,provider='Vontobel private document',source_url=url,instrument_name=metadata['name'],issuer=issuer,bid=bid,ask=ask,currency=currency,response_status='FULL_QUOTE_TIMESTAMP_REQUIRED' if bid is not None or ask is not None else 'NO_QUOTE_IN_PUBLIC_DOCUMENT',raw_evidence=evidence,response_sha256=hashlib.sha256(body).hexdigest())
    return observation,metadata


class Collector:
    """Per-worker collection and audit. No global or persistent portfolio cache."""
    def __init__(self,transport=None,*,isins=(),delayed=None):
        self.transport=transport or DocumentTransport();self.results={}
        if delayed is None and isins:
            from pulse.derivative_stuttgart import DelayedFileProvider
            delayed=DelayedFileProvider(isins)
        self.delayed=delayed

    def quote(self,isin):
        if isin in self.results:return self.results[isin]
        if not isin_valid(isin):
            result=dict(attempts=[],selected=None,metadata={},status='INVALID_ISIN')
        else:
            try:
                body,url=self.transport.document('https://'+VONTOBEL_HOST+'/cms/de-de/productredirect/'+isin)
                observation,metadata=parse_vontobel_document(isin,body,url)
                decision=validate_observation(observation,allowed_hosts=(VONTOBEL_HOST,))
                result=dict(attempts=[decision],selected=decision if decision['accepted'] else None,metadata=metadata,status=decision['reason'])
            except ProviderError as error:
                result=dict(attempts=[dict(provider='Vontobel private document',requested_isin=isin,status='UNAVAILABLE',reason=str(error),accepted=False)],selected=None,metadata={},status=str(error))
            except Exception:
                result=dict(attempts=[dict(provider='Vontobel private document',requested_isin=isin,status='UNAVAILABLE',reason='MALFORMED_PROVIDER_DOCUMENT',accepted=False)],selected=None,metadata={},status='MALFORMED_PROVIDER_DOCUMENT')
        if self.delayed is not None and isin_valid(isin):
            observation=self.delayed.quote(isin)
            decision=validate_observation(observation,allowed_hosts=('www.boerse-stuttgart.de',))
            result['attempts'].append(decision)
            result['status']='; '.join(dict.fromkeys(a.get('reason','NO_QUOTE') for a in result['attempts']))
        result['evaluated_at']=datetime.now(timezone.utc).isoformat()
        self.results[isin]=result
        return result


def canonical_quote(decision, *, now=None):
    """Only validated BID evidence enters the existing canonical price boundary."""
    import numpy as np
    selected=decision.get('selected')
    if not selected or selected.get('accepted') is not True or selected.get('status')!='AUTO_VERIFIED_BID':return None
    try:
        observation=Observation(**{name:selected[name] for name in Observation.__dataclass_fields__})
        checked=validate_observation(observation,now=now,allowed_hosts=(VONTOBEL_HOST,))
    except (ValueError,TypeError,KeyError):return None
    if not checked['accepted'] or selected['provider']!='Vontobel private document':return None
    return dict(quote_status='AUTO_VERIFIED_BID',live_price_eur=selected['bid'],quote_currency='EUR',quote_date=selected['observed_at'],quote_source=selected['provider'],quote_source_url=selected['source_url'],quote_bid=selected['bid'],quote_ask=selected['ask'] if selected.get('ask') is not None else np.nan,quote_last=np.nan,quote_price_type='BID',quote_confidence='VERIFIED_EVIDENCE',quote_error='',quote_raw_match='Exact-ISIN EUR-per-unit bid; see private quote evidence',all_quote_attempts=[])
