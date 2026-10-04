"""Shared presentation range; never changes the underlying wealth series."""
import math

CHART_SCHEMA_VERSION = 2

def wealth_range(values):
    finite = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not finite:
        return (0., 1.)
    low, high = min(finite), max(finite)
    # A minimum 5% span avoids magnifying tiny differences in a near-flat series.
    span = max(high-low, max(abs(low), abs(high))*0.05, 1.)
    padding = span*0.12
    center = (low+high)/2
    lower,upper=center-span/2-padding,center+span/2+padding
    # Preserve negative observations. Otherwise remove only unused negative
    # padding; the common minimum span still prevents exaggerated differences.
    return (max(0.,lower) if low>=0 else lower,upper)


def benchmark_alias(model):
    """Concise chart identity; full verified name remains in comparison details."""
    from pulse.benchmarks import PRESETS
    identity=model['raw'].get('benchmark_identity',{})
    ticker=str(identity.get('ticker') or identity.get('symbol') or model['raw'].get('benchmark_ticker') or '').strip()
    full=str(model['raw'].get('benchmark_name','Selected benchmark'))
    if not ticker:
        import re
        match=re.search(r'\(([A-Z0-9^][A-Z0-9.\^=\-]{0,31})\)\s*$',full)
        ticker=match.group(1) if match else ''
        if model['raw'].get('synthetic'):
            ticker=next((symbol for symbol,label in PRESETS.items() if full=='Synthetic '+label+' illustration'),ticker)
    if not model['raw'].get('benchmark_enabled',False):return 'No comparison'
    if ticker in PRESETS:label=PRESETS[ticker]
    else:
        label=str(identity.get('short_name') or identity.get('shortName') or identity.get('name') or full)
        if ticker and label.endswith('('+ticker+')'):label=label[:-(len(ticker)+2)].strip()
        if len(label)>40:label=label[:37].rstrip()+'…'
    if model['raw'].get('synthetic'):label='Synthetic '+PRESETS.get(ticker,label.removeprefix('Synthetic ').removesuffix(' illustration'))
    return label+(' ('+ticker+')' if ticker else '')
