"""Reporting-only conservative invocation of the retained dividend event engine.

No accounting output is replaced. Provider inputs are public, worker-local,
bounded by the existing transport controls and captured for exact replay.
"""
import os
import json
from pathlib import Path
from dataclasses import asdict
import time


def forward_source(ns):
    from dividend_engine import YahooDividendProvider, DividendSnapshot, build_dividend_analytics
    from worker_hooks import serialize
    config=json.loads(Path(os.environ['PULSE_PRICE_INPUT']).read_text()) if os.environ.get('PULSE_PRICE_INPUT') else None
    replay=json.loads(Path(os.environ['FOLIOLENS_REPLAY_PATH']).read_text(encoding='utf-8')) if os.environ.get('FOLIOLENS_REPLAY_PATH') else None
    capture=ns.get('_foliolens_capture',{})
    hints=dict(zip(ns['combined'].get('yahoo_ticker',[]),ns['combined'].get('live_price_currency',[])))
    public=YahooDividendProvider(ns['yf'].Ticker,hints)
    deadline=min(time.monotonic()+25,float(os.environ.get('FOLIOLENS_FORECAST_DEADLINE','inf')))

    class Provider:
        def fetch(self,ticker):
            key=json.dumps(['dividend_snapshot',ticker],sort_keys=True)
            if config is not None:
                value=config.get('dividend_snapshots',{}).get(ticker)
            elif replay is not None:
                value=replay['calls'].get(key)
            else:
                transport=ns.get('_foliolens_transport',{})
                transport['forecast_deadline']=deadline
                value=json.loads(json.dumps(serialize(asdict(public.fetch(ticker))),default=lambda v:v.isoformat()))
            if value is None:
                return DividendSnapshot(ticker,history_status='UNAVAILABLE',calendar_status='UNAVAILABLE',diagnostic='DIVIDEND_INPUT_NOT_CAPTURED_OR_DEFINED',retrieved_at='')
            capture[key]=value
            return DividendSnapshot(**value)

    def fx(currency):
        # Same canonical minor-unit conversion; never infer UNKNOWN as EUR.
        if currency in {'GBp','GBX'}:return ns['get_fx_to_eur']('GBP')/100
        if currency in {'ZAc','ZAC'}:return ns['get_fx_to_eur']('ZAR')/100
        if currency in {'ILA','ILa'}:return ns['get_fx_to_eur']('ILS')/100
        return ns['get_fx_to_eur'](currency) if currency and currency!='UNKNOWN' else float('nan')

    try:
        source=build_dividend_analytics(
            ns['dividend_projection_by_holding'].copy(),ns['combined'].copy(),
            ns['trades'].copy(),ns['dividends'].copy(),ns['corporate_action_audit'].copy(),ns['max_date'],
            provider=Provider(),candidate_resolver=lambda row:[row.get('yahoo_ticker')] if row.get('yahoo_ticker') else [],
            fx_resolver=fx,growth_stats=lambda annual:{'growth_clipped_pct':0.0},enabled=True)
    finally:
        ns.get('_foliolens_transport',{}).pop('forecast_deadline',None)
    # The existing canonical function selects holdings/earned entitlements,
    # reconciles receipts and estimates net using observed issuer retention.
    return serialize(dict(metrics=source['metrics'],projection=source['projection'],forecast=source['forecast'],
                          audit=source['audit'],growth_assumption_pct=0.0,
                          source='V6.7.8 build_dividend_analytics; zero-growth reporting scenario'))
