"""Worker-local provider controls and replayable public market inputs.

No portfolio accounting lives here. Canonical resolution, FIFO, FX conversion,
historical reconstruction and PME are called unchanged.
"""
from datetime import date
import json
import logging
import math
import os
from pathlib import Path
import time
from urllib.parse import urlparse
import numpy as np
import pandas as pd

def allowed_host(url, derivative_documents=False):
    parsed=urlparse(str(url))
    host=parsed.hostname or ''
    return parsed.scheme=='https' and (host=='yahoo.com' or host.endswith('.yahoo.com') or host=='api.openfigi.com' or derivative_documents and host=='markets.vontobel.com')

def install_transport_controls(ns):
    """Clamp requests; stop immediately on access restrictions, never evade them."""
    import requests
    from yfinance._http import requests as yf_requests
    state={'requests':0,'restricted_providers':[]}
    classes=set([requests.sessions.Session,yf_requests.Session])
    timeout_types=(requests.exceptions.Timeout,TimeoutError,getattr(yf_requests.exceptions,'Timeout',TimeoutError))
    for cls in classes:
        original=cls.request
        def controlled(self, method, url, _original=original, **kwargs):
            host=urlparse(str(url)).hostname
            provider='openfigi' if host=='api.openfigi.com' else 'vontobel_private_document' if host=='markets.vontobel.com' else 'yahoo'
            if not allowed_host(url,os.environ.get('FOLIOLENS_AUTO_DERIVATIVE_DOCUMENTS')=='1') or provider in state['restricted_providers'] or state['requests']>=800 or time.monotonic()>=state.get('forecast_deadline',float('inf')):
                raise RuntimeError('Provider unavailable or request budget exceeded')
            timeout=kwargs.get('timeout',10)
            kwargs['timeout']=min(float(timeout or 10),10) if not isinstance(timeout,tuple) else (5,10)
            remaining=state.get('forecast_deadline',float('inf'))-time.monotonic()
            if math.isfinite(remaining):
                cap=max(.001,remaining)
                kwargs['timeout']=tuple(min(v,cap/2) for v in kwargs['timeout']) if isinstance(kwargs['timeout'],tuple) else min(kwargs['timeout'],cap)
            # Providers have fixed destinations. Refuse redirects to unapproved services.
            kwargs['allow_redirects']=False
            for attempt in range(1 if provider=='vontobel_private_document' else 2):
                if state['requests']>=800:raise RuntimeError('Provider request budget exceeded')
                if time.monotonic()>=state.get('forecast_deadline',float('inf')):raise RuntimeError('Dividend reporting request budget exceeded')
                state['requests']+=1
                try:
                    response=_original(self,method,url,**kwargs)
                    if response.status_code in {401,403,429,999}:
                        if provider not in state['restricted_providers']:state['restricted_providers'].append(provider)
                    if 300<=response.status_code<400 and provider!='vontobel_private_document':
                        raise RuntimeError('Provider redirect requires review')
                    return response
                except timeout_types:
                    if attempt: raise
                    time.sleep(.25)
        cls.request=controlled
    # Suppress provider log payloads; worker stdout/stderr are discarded too.
    logging.getLogger('yfinance').disabled=True
    logging.getLogger('urllib3').disabled=True
    logging.getLogger('curl_cffi').disabled=True
    ns['prefetch_openfigi_identities']=ns['prefetch_openfigi_identities']
    return state

def benchmark_identity(info, ticker):
    actual=str(info.get('symbol','')).upper().strip()
    name=str(info.get('longName') or info.get('shortName') or '').strip()
    kind=str(info.get('quoteType','')).upper()
    currency=str(info.get('currency','')).strip()
    if actual!=ticker or not name or not currency:
        return name or ticker, 'Benchmark identity or currency could not be verified.'
    if kind=='INDEX':
        return name, 'Price-only or unverified total-return index: dividend-inclusive comparison unavailable. Select a suitable ETF; this ticker is not substituted.'
    if kind not in {'ETF','EQUITY','MUTUALFUND'}:
        return name, 'Instrument type is unsuitable for the adjusted-price benchmark methodology.'
    return name,''

def install_market(ns):
    from worker_hooks import serialize, coverage_issue, benchmark_history_issue
    replay_path=os.environ.get('FOLIOLENS_REPLAY_PATH')
    replay=json.loads(Path(replay_path).read_text(encoding='utf-8')) if replay_path else None
    capture={}
    metadata={'name':ns['BENCHMARK_NAME'],'ticker':ns['BENCHMARK_TICKER'],'warning':'','basis':'Adjusted-price total-return proxy'}
    ns['_foliolens_benchmark']=metadata
    if replay is None:
        transport=install_transport_controls(ns)
        ns['_foliolens_transport']=transport
        # Reuse public provider objects only inside this worker, never across users.
        ticker_factory=ns['yf'].Ticker
        tickers={}
        def ticker(symbol,*args,**kwargs):
            if symbol not in tickers:tickers[symbol]=ticker_factory(symbol,*args,**kwargs)
            return tickers[symbol]
        ns['yf'].Ticker=ticker
    else:
        def blocked(*args,**kwargs): raise RuntimeError('Replay cannot request external data')
        ns['yf'].Ticker=blocked
        ns['requests'].sessions.Session.request=blocked

    def store_call(kind, args, producer):
        key=json.dumps([kind,*args],sort_keys=True,default=str)
        if key in capture:
            return capture[key]
        if replay is not None:
            # Missing captured inputs are never replaced with a new live observation.
            if key not in replay['calls']: raise RuntimeError('Required captured market input unavailable')
            value=replay['calls'][key]
        else:
            value=serialize(producer())
        capture[key]=value
        return value

    original_resolve=ns['find_best_yahoo_ticker']
    def resolve(isin,security_name='',asset_class=''):
        return store_call('identity',[isin,security_name,asset_class],lambda:original_resolve(isin,security_name,asset_class))
    ns['find_best_yahoo_ticker']=resolve
    if replay is not None: ns['prefetch_openfigi_identities']=lambda isins:None

    original_latest=ns['fetch_latest_yahoo_price']
    def latest(ticker):
        def produce():
            value=original_latest(ticker)
            if ticker:
                try:
                    native=str(ns['yf'].Ticker(ticker).fast_info.get('currency','') or '')
                    if native:
                        value['native_currency']=native
                        value['currency']='GBX' if native=='GBp' else native
                except Exception:pass
            from pulse.valuations import today, MAX_QUOTE_AGE_DAYS
            observed=pd.to_datetime(value.get('price_date'),errors='coerce',utc=True)
            if pd.isna(observed) or not 0 <= (today()-observed.date()).days <= MAX_QUOTE_AGE_DAYS:
                value.update(price_native=None,status='STALE_OR_FUTURE_CURRENT_QUOTE',price_diagnostic_detail='A recent dated market observation is required.')
            return value
        value=store_call('latest',[ticker],produce).copy()
        # Do not use a quote whose currency/date is unknown; do not infer today's date.
        if not value.get('currency') or value.get('currency')=='UNKNOWN' or not value.get('price_date'):
            value.update(price_native=None,status='UNVERIFIED_CURRENCY_OR_DATE')
        for field in ('error','price_diagnostic_detail'): value[field]='Provider request failed' if value.get(field) else ''
        return value
    ns['fetch_latest_yahoo_price']=latest

    original_fx=ns['get_fx_to_eur']
    fx_observations={}
    ns['_foliolens_fx']=fx_observations
    def fx(currency):
        raw=str(currency or '').strip()
        currency,factor=ns['_normalize_currency_for_history'](raw)
        if currency in {'','UNKNOWN'}: return np.nan
        def produce():
            rate=original_fx(currency)*factor
            quote_date=''
            if currency=='EUR':
                return {'rate':rate,'date':'','source':'EUR identity conversion','native_currency':raw}
            try:
                history=ns['yf'].Ticker(f'{currency}EUR=X').history(period='5d',timeout=10)
                if not history.empty:quote_date=str(pd.Timestamp(history.index[-1]).date())
            except Exception:pass
            if not quote_date or not 0 <= (date.today()-date.fromisoformat(quote_date)).days <= 7:rate=np.nan
            return {'rate':rate,'date':quote_date,'source':'Yahoo latest daily FX close','native_currency':raw}
        value=store_call('fx',[raw],produce)
        if isinstance(value,dict):
            fx_observations[raw]=value
            return value['rate'] if value['rate'] is not None else np.nan
        # Older captured canonical scalar inputs are usable only in explicitly controlled replay.
        fx_observations[raw]={'rate':value,'date':'','source':'Legacy controlled capture; FX quote date unavailable'}
        return value if value is not None else np.nan
    ns['get_fx_to_eur']=fx

    original_history=ns['_fetch_yahoo_history']
    def fetch(ticker,start,end):
        start,end=str(pd.Timestamp(start).date()),str(pd.Timestamp(end).date())
        def produce():
            if ticker==ns['BENCHMARK_TICKER']:
                try:
                    provider=ns['yf'].Ticker(ticker)
                    info=provider.info
                    name,warning=benchmark_identity(info,ticker)
                    metadata.update(name=f'{name} ({ticker})',warning=warning)
                    if warning: return [[],str(info.get('currency','')),'UNSUITABLE_BENCHMARK',warning,metadata.copy()]
                    raw=provider.history(start=start,end=str((pd.Timestamp(end)+pd.Timedelta(days=2)).date()),auto_adjust=False,actions=False,timeout=10)
                    frame=ns['_normalize_yahoo_history'](raw)
                    reason=benchmark_history_issue(raw,frame,info['currency'],start,end)
                    currency,_=ns['_normalize_currency_for_history'](info['currency'])
                    if not reason and currency!='EUR':
                        fxframe,_,status,_=original_history(f'{currency}EUR=X',start,end)
                        reason=coverage_issue(fxframe,'close_native',start,end)
                        if status!='OK':reason='Reliable EUR conversion history unavailable'
                    if reason:
                        metadata['warning']=reason
                        return [[],currency,'UNRELIABLE_BENCHMARK',reason,metadata.copy()]
                    records=frame.reset_index(names='date')
                    return [serialize(records),info['currency'],'OK','',metadata.copy()]
                except Exception:
                    metadata['warning']='Benchmark provider unavailable, timed out or denied the request.'
                    return [[], '', 'BENCHMARK_ERROR',metadata['warning'],metadata.copy()]
            frame,currency,status,error=original_history(ticker,start,end)
            if not frame.empty:
                try:currency=str(ns['yf'].Ticker(ticker).fast_info.get('currency','') or currency)
                except Exception:pass
            if not currency or currency=='UNKNOWN':
                return [[],currency,'UNKNOWN_CURRENCY','Historical currency unavailable',None]
            return [serialize(frame.reset_index(names='date')) if not frame.empty else [],currency,status,'Provider history unavailable' if error else '',None]
        try:
            rows,currency,status,error,meta=store_call('history',[ticker,start,end],produce)
        except RuntimeError:
            if replay is None:raise
            if ticker==ns['BENCHMARK_TICKER']:
                metadata['warning']='Selected benchmark is absent from the controlled capture. No substitute or live request is used.'
            return pd.DataFrame(),'','NOT_CAPTURED','Required captured history unavailable'
        if meta:metadata.update(meta)
        frame=pd.DataFrame(rows)
        if not frame.empty:
            frame['date']=pd.to_datetime(frame['date']);frame=frame.set_index('date')
        return frame,currency,status,error
    ns['_fetch_yahoo_history']=fetch
    ns['_foliolens_capture']=capture

def install_derivatives(ns,config,synthetic=False):
    """Canonical arithmetic requires recent evidence; no unverifiable ISIN probe."""
    quotes=config.get('derivative_quotes',{})
    if synthetic and not quotes:return
    ns['ENABLE_DERIVATIVE_QUOTE_PROBE']=True
    evidence={};ns['_foliolens_derivative_quotes']=evidence
    collector=None
    collect_enabled=not synthetic and os.environ.get('FOLIOLENS_AUTO_DERIVATIVE_DOCUMENTS')=='1' and not os.environ.get('FOLIOLENS_REPLAY_PATH')
    replay_quotes=None
    if not synthetic and os.environ.get('FOLIOLENS_REPLAY_PATH'):
        replay_quotes=json.loads(Path(os.environ['FOLIOLENS_REPLAY_PATH']).read_text(encoding='utf-8')).get('derivative_quotes',{})
    def probe(isin):
        nonlocal collector
        if not synthetic:
            from pulse.derivative_quotes import canonical_quote,timestamp
            if replay_quotes is not None:
                evidence[isin]=replay_quotes.get(isin,dict(attempts=[],selected=None,metadata={},status='NO_CAPTURED_AUTOMATIC_OBSERVATION'))
            elif collect_enabled:
                if collector is None:
                    from pulse.derivative_quotes import Collector
                    positions=ns.get('derivative_positions',pd.DataFrame())
                    isins=positions.loc[positions['position_status'].eq('ACTIVE'),'isin'].tolist() if not positions.empty else [isin]
                    from pulse.derivative_cache import QuoteCache
                    directory=os.environ.get('FOLIOLENS_DERIVATIVE_CACHE_DIRECTORY')
                    collector=Collector(isins=isins,cache=QuoteCache(directory) if directory else None)
                evidence[isin]=collector.quote(isin)
            evaluation=timestamp(evidence.get(isin,{}).get('evaluated_at')) if replay_quotes is not None else None
            automatic=canonical_quote(evidence.get(isin,{}),now=evaluation)
            if automatic:return automatic
        if isin in quotes:
            q=quotes[isin]
            from pulse.valuations import quote_status
            # Fabricated fixtures have an explicit historical analysis date.
            reference=pd.Timestamp(ns['max_date']).date() if synthetic else None
            status=quote_status(q,reference)
            return dict(quote_status=status,live_price_eur=float(q['price_eur']) if status=='MANUAL_CONFIRMED' else np.nan,quote_currency='EUR',
                quote_date=q['date'],quote_source=q['source'],quote_price_type='MANUAL_EUR_PER_UNIT',
                quote_confidence='USER_CONFIRMED' if status=='MANUAL_CONFIRMED' else 'UNUSABLE',quote_error='' if status=='MANUAL_CONFIRMED' else 'Manual observation is stale, future-dated or invalid; refresh its evidence.',quote_source_url='',quote_bid=np.nan,quote_ask=np.nan,
                quote_last=float(q['price_eur']) if status=='MANUAL_CONFIRMED' else np.nan,quote_raw_match='Explicit dated EUR-per-unit confirmation',all_quote_attempts=[])
        # A Yahoo lookup of an ISIN cannot establish the required derivative
        # identity and was always rejected. Avoid spending provider requests on it.
        return dict(quote_status='VERIFIED_DERIVATIVE_QUOTE_UNAVAILABLE',live_price_eur=np.nan,
            quote_currency='',quote_date='',quote_source='',
            quote_source_url='',quote_bid=np.nan,quote_ask=np.nan,quote_last=np.nan,quote_price_type='NONE',quote_raw_match='',
            quote_confidence='UNVERIFIED',quote_error=('No current exact-ISIN EUR-per-unit bid passed automatic validation. See Derivative Quote Center; optional manual evidence remains available.' if isin in evidence else 'No verified derivative identity/quote provider. Optional recent EUR-per-unit evidence can be supplied locally; automatic scraping is disabled.'),all_quote_attempts=[])
    ns['multi_source_derivative_quote_probe']=probe
