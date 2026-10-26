"""Allowlisted canonical evidence for private review, without new analytics."""
import math


def number(value):
    try:
        return float(value) if math.isfinite(float(value)) else None
    except (ValueError, TypeError):
        return None


def historical_evidence(raw):
    metrics = raw.get('historical_metrics', {})
    observed, effective, fallback, unresolved = (number(metrics.get(key)) for key in (
        'externally_observed_market_price_coverage_pct', 'external_market_price_coverage_pct',
        'fallback_price_coverage_pct', 'unresolved_price_coverage_pct'))
    valid = all(v is not None and 0 <= v <= 100.000001 for v in (observed, effective, fallback, unresolved))
    valid = valid and observed <= effective + 1e-6 and abs(effective + fallback + unresolved - 100) < 1e-5
    shares = dict(observed=observed, carried_market=effective-observed, fallback=fallback, unresolved=unresolved) if valid else {}
    allowed = ('security_name', 'isin', 'yahoo_ticker', 'history_source', 'history_status',
               'history_observations', 'externally_observed_market_price_observations',
               'external_market_effective_observations', 'transaction_or_filled_fallback_observations',
               'unresolved_observations')
    return dict(available=bool(valid), shares=shares,
                note='Coverage is measured over active instrument-days, not portfolio value. Carried market prices include non-trading days. Transaction/filled estimates are not observed market quotes. Per-instrument counts cover its canonical history window.',
                rows=[{key: row.get(key) for key in allowed} for row in raw.get('historical_price_audit', [])])


def dividend_evidence(raw):
    source = raw.get('forward_dividends') or {}
    projections = {row.get('isin'): row for row in source.get('projection', [])}
    rows = []
    for holding in raw.get('holdings', []):
        if holding.get('position_status') != 'ACTIVE' or holding.get('asset_class') not in {'STOCK', 'FUND'}:
            continue
        item = projections.get(holding.get('isin'), {})
        status = item.get('external_dividend_status') or 'UNAVAILABLE'
        net = number(item.get('growth_adjusted_forward_12m_net_dividend_eur'))
        reason = 'Known net estimate; not guaranteed future income.'
        if net is None:
            reason = 'Net estimate unavailable: distribution, observed net retention or EUR conversion evidence is incomplete.'
        elif status in {'NO_EXTERNAL_DIVIDEND_EVIDENCE', 'APPARENTLY_ACCUMULATING_NAME_AND_NO_HISTORY'}:
            reason = 'No known distributions; this is not a guarantee of zero future payments.'
        if 'UNSCHEDULED' in status:
            reason += ' A declared payment date or amount is unresolved.'
        if any(r.get('isin') == holding.get('isin') and r.get('method') == 'BROKER_RECEIPT_SEASONALITY_ESTIMATE' for r in source.get('forecast', [])):
            reason += ' Low-confidence broker-receipt seasonality; does not infer current quantities.'
        rows.append(dict(name=holding.get('security_name', ''), isin=holding.get('isin', ''),
                         ticker=holding.get('yahoo_ticker', ''), status=status, known_net_estimate=net, reason=reason))
    return rows


def history_summary(evidence):
    if not evidence['available']:
        return 'Historical pricing evidence unavailable; coverage is not inferred.'
    s = evidence['shares']
    return (f"Historical instrument-days: observed {s['observed']:.1f}% · carried market {s['carried_market']:.1f}% · "
            f"transaction/filled estimates {s['fallback']:.1f}% · unresolved {s['unresolved']:.1f}%.")
