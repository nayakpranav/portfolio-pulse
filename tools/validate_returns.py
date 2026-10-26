"""Offline, optional independent validation; never used for displayed returns.

Input is a private canonical result JSON or --synthetic. Only safe counts and
checks are printed. QuantStats receives no identifiers, flows, EUR wealth,
benchmark, provider calls or reports; no missing observation is filled.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def validate_model(model, stats=None):
    import numpy as np
    import pandas as pd
    from pulse.analytics import drawdown
    result = dict(status='UNAVAILABLE', observations=0, checks={},
                  scope=model.get('returns_scope_label', 'Stocks & funds'))
    if not model['drawdown']['available']:
        return result
    nav = model['nav']
    raw = model['raw'].get('unaffected_scope') or model['raw']
    # Revalidate the complete aligned canonical history, not a dropped-NaN slice.
    if not drawdown(nav, raw['historical_metrics'], result['scope'])['available']:
        return result
    dates = pd.DatetimeIndex(pd.to_datetime(nav['date'], utc=True)).tz_localize(None)
    returns = pd.Series(pd.to_numeric(nav['daily_twr_return_pct']).to_numpy()/100, index=dates)
    if pd.notna(returns.iloc[0]) and abs(returns.iloc[0]) > 1e-12:
        return result
    returns.iloc[0] = 0  # The explicit initial index baseline, never an interior gap.
    if not np.isfinite(returns).all() or (returns < -1).any():
        return result
    if stats is None:
        import quantstats
        stats = quantstats.stats
    index = pd.Series(pd.to_numeric(nav['stockfund_nav_index']).to_numpy(), index=dates)
    canonical = pd.Series(pd.to_numeric(nav['stockfund_drawdown_pct']).to_numpy()/100, index=dates)
    linked = np.cumprod(1+returns.to_numpy())
    # Explicit NAV prices avoid QuantStats' return/price magnitude heuristic.
    # This is the canonical performance index, never cash-flow-sensitive wealth.
    independent_dd = stats.to_drawdown_series(index.copy())
    checks = dict(
        linked_nav=np.allclose(linked*100, index, rtol=1e-8, atol=1e-6),
        compounded_twr=bool(np.isclose(stats.comp(returns.copy())*100,
                                      raw['historical_metrics']['stockfund_twr_since_inception_pct'], atol=1e-6, rtol=1e-8)),
        drawdown_dates=independent_dd.index.equals(canonical.index),
        drawdown_series=bool(len(independent_dd)==len(canonical) and np.allclose(independent_dd, canonical, atol=1e-8, rtol=1e-8)),
        maximum_drawdown=bool(np.isclose(independent_dd.min()*100, model['drawdown']['maximum'], atol=1e-6)),
        current_drawdown=bool(np.isclose(independent_dd.iloc[-1]*100, model['drawdown']['current'], atol=1e-6)))
    return dict(result, status='PASS' if all(checks.values()) else 'FAIL',
                observations=len(nav), checks={key: bool(value) for key,value in checks.items()})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--input', type=Path, help='Private canonical result JSON; not the transaction CSV.')
    group.add_argument('--synthetic', action='store_true', help='Run a fabricated canonical scenario without network calls.')
    args = parser.parse_args(argv)
    try:
        if args.synthetic:
            from pulse.synthetic import fixture
            from pulse.runner import run_analysis
            data, prices = fixture()
            raw = run_analysis(data, prices=prices)
        else:
            if args.input.stat().st_size > 64*1024*1024:
                raise ValueError('Result size limit')
            raw = json.loads(args.input.read_text(encoding='utf-8'))
        from pulse.adapter import prepare
        report = validate_model(prepare(raw))
    except ImportError:
        print(json.dumps({'status':'UNAVAILABLE', 'reason':'Install the optional requirements-validation.txt in a separate developer environment.'}))
        return 2
    except Exception:
        print(json.dumps({'status':'UNAVAILABLE', 'reason':'Private canonical result or validation dependency could not be validated; no private values logged.'}))
        return 2
    print(json.dumps(report))
    return 0 if report['status']=='PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
