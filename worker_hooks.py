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
    private=json.loads(Path(os.environ['FOLIOLENS_WORKER_CONFIG']).read_text(encoding='utf-8')) if os.environ.get('FOLIOLENS_WORKER_CONFIG') else {}
    from pulse.private_config import install_private_events
    install_private_events(ns,private)
    # yfinance's auxiliary caches are worker-local and removed with the workspace.
    if hasattr(ns['yf'],'set_tz_cache_location'):
        ns['yf'].set_tz_cache_location(str(Path.cwd()/'provider-cache'))
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
            missing = data.get('missing') or data.get('missing_current')
            return dict(price_native=np.nan if missing else data.get('end',np.nan),currency='EUR',
                        price_date=config['asof'],source_col='Close',status='MISSING' if missing else 'OK',error='',label='Synthetic controlled price')
        def history(ticker,start_date,end_date):
            data = securities.get(ticker, config.get('benchmarks', {'IWDA.AS': config.get('benchmark', {})}).get(ticker,{}))
            if not data or data.get('missing') or data.get('missing_history'):
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
    if not config and 'find_best_yahoo_ticker' in ns:
        from pulse.market import install_market
        install_market(ns)
    from pulse.market import install_derivatives
    if 'yahoo_derivative_probe' in ns:
        install_derivatives(ns,private,synthetic=bool(config))
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
    # Expose existing canonical sources, without rerunning or changing accounting.
    result['scope_sources'] = serialize({
        'stockfund_current_value_eur': ns['stock_live_value'],
        'stockfund_open_pl_eur': ns['stock_unrealized'],
        'stockfund_realized_pl_eur': ns['lifetime_metrics']['lifetime_stock_realized_pl_eur'],
        'net_dividends_eur': ns['lifetime_metrics']['lifetime_net_dividend_recovery_eur'],
    })
    result['lifetime_cashflow_ledger'] = serialize(ns['lifetime_cashflow_ledger'])
    result['derivative_ledger'] = serialize(ns['derivative_ledger'])
    result['latest_transaction_date'] = serialize(ns['max_date'])
    result['benchmark_identity']=ns.get('_foliolens_benchmark',{})
    result['provider_diagnostics']=ns.get('_foliolens_transport',{})
    result['fx_observations']=ns.get('_foliolens_fx',{})
    result['active_derivatives']=serialize(ns.get('active_derivatives',pd.DataFrame()))
    result['worthless_candidates']=serialize(ns.get('worthless_derecognition_diagnostics',pd.DataFrame()))
    from pulse.partial import projection
    result['unaffected_scope']=serialize(projection(ns))
    review_rows=[]
    frame=ns['df']
    for _,rule in ns['transaction_support_matrix'].iterrows():
        if not rule['blocking']:continue
        matching=frame[frame['type_norm'].eq(rule['type']) & frame['category'].eq(rule['category']) & frame['asset_class_clean'].eq(rule['asset_class'])]
        for _,row in matching.iterrows():
            review_rows.append(dict(source_row=int(row['source_row']),date=str(row['event_date'].date()),
                name=str(row['security_name']),isin=str(row['isin']),type=str(row['type_norm']),
                quantity=serialize(row['shares']),reason=str(rule['notes'])))
    result['review_transactions']=review_rows
    if os.environ.get('FOLIOLENS_CAPTURE')=='1':
        result['market_capture']={'calls':ns.get('_foliolens_capture',{}),'benchmark':ns.get('_foliolens_benchmark',{})}
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
