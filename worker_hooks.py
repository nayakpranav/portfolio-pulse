"""Non-accounting worker hooks: price inputs and a small canonical output contract."""
import json
import os
from pathlib import Path
import pandas as pd
import numpy as np

def install_hooks(ns):
    # No additive sector metadata is needed by Pulse; never copy private seed data.
    ns['fetch_yahoo_security_metadata'] = lambda ticker: {}
    config = json.loads(Path(os.environ['PULSE_PRICE_INPUT']).read_text()) if os.environ.get('PULSE_PRICE_INPUT') else None
    if config:
        def no_external_requests(*args,**kwargs):
            raise RuntimeError('External market-data calls are disabled for synthetic input')
        ns['yf'].Ticker = no_external_requests
        ns['requests'].sessions.Session.request = no_external_requests
        securities = config['securities']
        def resolve(isin, security_name='', asset_class=''):
            return dict(ticker=isin,match_status='SYNTHETIC_CONTROLLED_INPUT',match_score=100,matched_name=security_name,
                        matched_exchange='SYNTHETIC',matched_currency='EUR',matched_quote_type='ETF' if asset_class=='FUND' else 'EQUITY')
        def latest(ticker):
            data = securities.get(ticker,{})
            return dict(price_native=np.nan if data.get('missing') else data.get('end',np.nan),currency='EUR',
                        price_date=config['asof'],source_col='Close',status='MISSING' if data.get('missing') else 'OK',error='',label='Synthetic controlled price')
        def history(ticker,start_date,end_date):
            data = securities.get(ticker, config.get('benchmarks', {'IWDA.AS': config.get('benchmark', {})}).get(ticker,{}))
            if not data or data.get('missing'):
                return pd.DataFrame(), 'EUR','NO_HISTORY','Controlled missing price'
            full = pd.date_range('2025-01-06',config['asof'],freq='B')
            prices = pd.Series(np.linspace(data['start'],data['end'],len(full)),index=full)
            prices = prices[(prices.index >= pd.Timestamp(start_date)) & (prices.index <= pd.Timestamp(end_date))]
            return pd.DataFrame({'close_native':prices,'adjusted_close_native':prices}), 'EUR','OK',''
        ns['prefetch_openfigi_identities'] = lambda isins: None
        ns['find_best_yahoo_ticker'] = resolve
        ns['fetch_latest_yahoo_price'] = latest
        ns['_fetch_yahoo_history'] = history
    if config is None and os.environ.get('PULSE_BENCHMARK_DISABLED') != '1':
        original_history = ns['_fetch_yahoo_history']
        def guarded_benchmark(ticker, start_date, end_date):
            if ticker != ns['BENCHMARK_TICKER']:
                return original_history(ticker, start_date, end_date)
            try:
                provider = ns['yf'].Ticker(ticker)
                raw = provider.history(start=str(pd.Timestamp(start_date).date()),
                    end=str((pd.Timestamp(end_date)+pd.Timedelta(days=2)).date()), auto_adjust=False, actions=False)
                currency = str(provider.fast_info.get('currency', '') or '')
                frame = ns['_normalize_yahoo_history'](raw)
                reason = benchmark_history_issue(raw, frame, currency, start_date, end_date)
                normalized_currency, _ = ns['_normalize_currency_for_history'](currency)
                if not reason and normalized_currency != 'EUR':
                    fx, _, status, _ = original_history(f'{normalized_currency}EUR=X', start_date, end_date)
                    reason = coverage_issue(fx, 'close_native', start_date, end_date)
                    if status != 'OK': reason = 'Reliable EUR conversion history unavailable'
                if reason:
                    return pd.DataFrame(), currency, 'UNRELIABLE_BENCHMARK', reason
                return frame, currency, 'OK', ''
            except Exception:
                return pd.DataFrame(), '', 'BENCHMARK_ERROR', 'Ticker or required benchmark data unavailable'
        ns['_fetch_yahoo_history'] = guarded_benchmark
    if os.environ.get('PULSE_BENCHMARK_DISABLED') == '1':
        original = ns['_fetch_yahoo_history']
        def without_benchmark(ticker,start_date,end_date):
            if ticker == ns['BENCHMARK_TICKER']:
                return pd.DataFrame(), 'EUR','DISABLED','Comparison disabled by user'
            return original(ticker,start_date,end_date)
        ns['_fetch_yahoo_history'] = without_benchmark

def serialize(value):
    if isinstance(value,pd.DataFrame):
        return json.loads(value.to_json(orient='records',date_format='iso'))
    if isinstance(value,dict):
        return {str(k):serialize(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [serialize(v) for v in value]
    if isinstance(value,(pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value,np.generic):
        value = value.item()
    if isinstance(value,float) and not np.isfinite(value):
        return None
    return value

def write_results(ns):
    names = ['lifetime_metrics','advanced_metrics','historical_metrics','accounting_status','dividends','interest',
             'holdings','daily_nav_history','period_performance','wealth_contribution','pre_diagnostics',
             'transaction_support_matrix','valuation_diagnostics','corporate_action_audit',
             'dividend_reinvestment_events','worthless_derecognition_events','stockfund_concentration']
    result = {name:serialize(ns[name]) for name in names}
    result['latest_transaction_date'] = serialize(ns['max_date'])
    Path(os.environ['PULSE_RESULT_PATH']).write_text(json.dumps(result,allow_nan=False),encoding='utf-8')


def coverage_issue(frame, column, start_date, end_date):
    if frame.empty or column not in frame:
        return 'Required historical series unavailable'
    values = pd.to_numeric(frame[column], errors='coerce')
    if values.isna().any() or not np.isfinite(values).all() or (values <= 0).any():
        return 'Historical series contains missing or invalid prices'
    start, end = pd.Timestamp(start_date).normalize(), pd.Timestamp(end_date).normalize()
    if frame.index.min() > start or (end-frame.index.max()).days > 4:
        return 'Historical coverage does not reach the matched cash-flow dates'
    if len(frame.index)>1 and frame.index.to_series().diff().dt.days.max()>7:
        return 'Historical coverage contains an excessive gap'
    return ''

def benchmark_history_issue(raw, frame, currency, start_date, end_date):
    if 'Adj Close' not in raw or not currency or str(currency).upper()=='UNKNOWN':
        return 'Reliable adjusted total-return proxy or currency unavailable'
    return coverage_issue(frame, 'adjusted_close_native', start_date, end_date)
