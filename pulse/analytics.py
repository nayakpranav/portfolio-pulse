"""Availability-checked presentation of canonical risk and dividend outputs."""
import math
import pandas as pd
import numpy as np


def finite(value):
    try:return float(value) if math.isfinite(float(value)) else None
    except (ValueError,TypeError):return None


def drawdown(nav,historical,scope,eligible=True):
    out=dict(available=False,series=[],maximum=None,current=None,date=None,scope=scope,
             note='Reliable continuous canonical stock/fund TWR history unavailable.')
    columns={'date','stockfund_nav_index','stockfund_drawdown_pct','daily_twr_return_pct','price_coverage_pct'}
    if not eligible or nav.empty or not columns.issubset(nav.columns):return out
    dates=pd.to_datetime(nav['date'],errors='coerce',utc=True).dt.tz_localize(None).dt.normalize()
    index=pd.to_numeric(nav['stockfund_nav_index'],errors='coerce')
    daily=pd.to_numeric(nav['daily_twr_return_pct'],errors='coerce')/100
    risk=pd.to_numeric(nav['stockfund_drawdown_pct'],errors='coerce')
    coverage=pd.to_numeric(nav['price_coverage_pct'],errors='coerce')
    if dates.isna().any() or not dates.is_monotonic_increasing or dates.duplicated().any():return out
    if not set(pd.bdate_range(dates.iloc[0],dates.iloc[-1])).issubset(set(dates)):return out
    # Only the initial performance baseline may lack a return. Never bridge
    # an interior gap using the canonical carried-forward NAV placeholder.
    if len(nav)<2 or daily.iloc[1:].isna().any() or not np.isfinite(daily.iloc[1:]).all():return out
    if not np.isfinite(index).all() or (index<0).any() or not math.isclose(index.iloc[0],100,abs_tol=1e-7):return out
    if coverage.isna().any() or (coverage<99.999).any() or not np.isfinite(risk).all():return out
    linked=100*np.concatenate(([1.0],np.cumprod(1+daily.iloc[1:].to_numpy())))
    expected=(index/index.cummax()-1)*100
    if not np.allclose(index,linked,rtol=1e-8,atol=1e-6) or not np.allclose(risk,expected,atol=1e-6) or (risk>1e-7).any():return out
    twr=finite(historical.get('stockfund_twr_since_inception_pct'))
    maximum,current=finite(historical.get('maximum_drawdown_pct')),finite(historical.get('current_drawdown_pct'))
    if any(v is None for v in (twr,maximum,current)):return out
    if not math.isclose(index.iloc[-1]-100,twr,abs_tol=1e-6) or not math.isclose(risk.min(),maximum,abs_tol=1e-6) or not math.isclose(risk.iloc[-1],current,abs_tol=1e-6):return out
    note='Cash-flow-adjusted cumulative stock/fund TWR; cash and derivatives excluded. Initial index baseline: 100.'
    if historical.get('historical_analytics_status')=='OK_WITH_LOW_CONFIDENCE_FALLBACK':note+=' History includes transaction or filled-price estimates; interpret with caution.'
    return dict(available=True,series=[dict(date=d.isoformat(),drawdown_pct=min(0,float(v))) for d,v in zip(dates,risk)],
                maximum=min(0,maximum),current=min(0,current),date=dates.iloc[-1].date(),scope=scope,note=note)


def forward_dividends(raw,report_date,eligible=True):
    out=dict(available=False,amount=None,label='Forward 12M Net Dividends (Est.)',status='Unavailable',
             note='Canonical distribution, net-income or currency inputs unavailable; unknown income is not zero.',asof=None,end=None,
             active_count=0,covered_count=0)
    source=raw.get('forward_dividends') or {}
    metrics=source.get('metrics',{})
    asof=pd.to_datetime(metrics.get('asof'),errors='coerce')
    if pd.isna(asof):return out
    out.update(asof=asof.date(),end=(asof+pd.DateOffset(years=1)).date())
    if not eligible:
        out['note']='Forward estimate unavailable for unresolved or partial stock/fund accounting.';return out
    if (report_date-asof.date()).days>31 or asof.date()>report_date:
        out['note']='Forecast reference date is stale or in the future; reanalyze a current export.';return out
    positions=[r for r in raw.get('holdings',[]) if r.get('position_status')=='ACTIVE' and r.get('asset_class') in {'STOCK','FUND'}]
    # Public projection is per canonical ISIN; no security-name inference here.
    projections={r.get('isin'):r for r in source.get('projection',[])}
    covered=[r for r in positions if finite(projections.get(r.get('isin'),{}).get('growth_adjusted_forward_12m_net_dividend_eur')) is not None
             and projections.get(r.get('isin'),{}).get('external_dividend_status','').startswith(('EXTERNAL_HISTORY_FOUND','DECLARED_UPCOMING_FOUND','NO_EXTERNAL_DIVIDEND_EVIDENCE','APPARENTLY_ACCUMULATING_NAME_AND_NO_HISTORY'))]
    out.update(active_count=len(positions),covered_count=len(covered))
    amount=finite(metrics.get('forward_12m_estimated_net_known_subtotal_eur'))
    estimable=metrics.get('forward_12m_net_estimable_event_count',0)
    if amount is None or (not estimable and len(covered)!=len(positions)) or not source.get('audit'):return out
    partial=len(covered)!=len(positions) or metrics.get('forward_12m_net_forecast_completeness')=='PARTIAL' or metrics.get('pending_unknown_amount_events',0) or metrics.get('pending_unknown_payment_date_events',0) or metrics.get('receipt_association_review_events',0)
    note=f"As of {asof:%d %b %Y}, through {out['end']:%d %b %Y}. {len(covered)}/{len(positions)} active stock/fund positions have net-estimable distribution coverage. "
    note+='Known subtotal only; incomplete holdings or distribution coverage. ' if partial else 'Known-distribution estimate; no growth uplift. '
    note+='Canonical declared events and seasonal estimates, observed issuer net retention and EUR FX; excludes interest. Not guaranteed; no known distributions does not guarantee zero future income. Earned dividends from recently closed positions may be included.'
    if any(r.get('method')=='BROKER_RECEIPT_SEASONALITY_ESTIMATE' for r in source.get('forecast',[])):
        note+=' Includes low-confidence broker-receipt seasonality where external history is absent; that fallback does not infer current quantities.'
    return {**out,'available':True,'amount':amount,'label':'Forward 12M Net Dividends (Known Est.)','status':'Partial coverage' if partial else 'Known estimate','note':note}


def risk_summary(risk):
    return (f"Max TWR Drawdown: {risk['maximum']:.2f}% | Current: {risk['current']:.2f}%"
            if risk['available'] else 'TWR drawdown: Unavailable - reliable continuous history required')


def forward_display(forecast,currency='€'):
    return f"~{currency}{forecast['amount']:,.2f}" if forecast['available'] else 'Unavailable'
