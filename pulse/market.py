"""Worker-local provider controls and replayable public market inputs.

No portfolio accounting lives here. Canonical resolution, FIFO, FX conversion,
historical reconstruction and PME are called unchanged.
"""
from datetime import date
import json
import logging
import os
from pathlib import Path
import time
from urllib.parse import urlparse
import numpy as np
import pandas as pd

def allowed_host(url):
    parsed=urlparse(str(url))
    host=parsed.hostname or ''
    return parsed.scheme=='https' and (host=='yahoo.com' or host.endswith('.yahoo.com') or host=='api.openfigi.com')

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
            provider='openfigi' if urlparse(str(url)).hostname=='api.openfigi.com' else 'yahoo'
            if not allowed_host(url) or provider in state['restricted_providers'] or state['requests']>=800:
                raise RuntimeError('Provider unavailable or request budget exceeded')
            timeout=kwargs.get('timeout',10)
            kwargs['timeout']=min(float(timeout or 10),10) if not isinstance(timeout,tuple) else (5,10)
            # Providers have fixed destinations. Refuse redirects to unapproved services.
            kwargs['allow_redirects']=False
            for attempt in range(2):
                if state['requests']>=800:raise RuntimeError('Provider request budget exceeded')
                state['requests']+=1
                try:
                    response=_original(self,method,url,**kwargs)
                    if response.status_code in {401,403,429,999}:
                        if provider not in state['restricted_providers']:state['restricted_providers'].append(provider)
                    if 300<=response.status_code<400:
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
            if not quote_date or (date.today()-date.fromisoformat(quote_date)).days>7:rate=np.nan
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
    """Use canonical valuation arithmetic with explicit private quotes or Yahoo only."""
    quotes=config.get('derivative_quotes',{})
    if synthetic and not quotes:return
    ns['ENABLE_DERIVATIVE_QUOTE_PROBE']=True
    def probe(isin):
        if isin in quotes:
            q=quotes[isin]
            return dict(quote_status='MANUAL_CONFIRMED',live_price_eur=float(q['price_eur']),quote_currency='EUR',
                quote_date=q['date'],quote_source=q['source'],quote_price_type='MANUAL_EUR_PER_UNIT',
                quote_confidence='USER_CONFIRMED',quote_error='',quote_source_url='',quote_bid=np.nan,quote_ask=np.nan,
                quote_last=float(q['price_eur']),quote_raw_match='Explicit dated EUR-per-unit confirmation',all_quote_attempts=[])
        attempt=ns['yahoo_derivative_probe'](isin)
        # Canonical Yahoo-only probe is low-confidence and cannot prove an ISIN identity.
        # Retain diagnostics but require explicit auditable valuation instead of accepting it.
        return dict(quote_status='YAHOO_DERIVATIVE_IDENTITY_UNVERIFIED',live_price_eur=np.nan,
            quote_currency=attempt.get('currency',''),quote_date=attempt.get('timestamp',''),quote_source='Yahoo',
            quote_source_url='',quote_bid=np.nan,quote_ask=np.nan,quote_last=np.nan,quote_price_type='NONE',quote_raw_match='',
            quote_confidence='UNVERIFIED',quote_error='No verified derivative identity/quote. Supply a dated manual EUR valuation.',all_quote_attempts=[attempt])
    ns['multi_source_derivative_quote_probe']=probe
