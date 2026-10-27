"""Aggregate-only monotonic instrumentation. Never retain call arguments or results.

Function timings are inclusive (nested rows must not be added together). Network
elapsed time includes transport work, not a claim of pure socket wait time.
"""
from contextlib import contextmanager
from functools import wraps
import time


class Timings:
    def __init__(self):
        self.rows = {}

    @contextmanager
    def stage(self, name):
        wall, cpu = time.perf_counter(), time.process_time()
        try:
            yield
        finally:
            row = self.rows.setdefault(name, {'calls': 0, 'wall_seconds': 0., 'cpu_seconds': 0.})
            row['calls'] += 1
            row['wall_seconds'] += time.perf_counter() - wall
            row['cpu_seconds'] += time.process_time() - cpu

    def wrap(self, fn, name):
        @wraps(fn)
        def measured(*args, **kwargs):
            with self.stage(name):
                return fn(*args, **kwargs)
        return measured


def install(ns):
    timings = Timings()
    ns['_foliolens_timings'] = timings
    groups = {
        'csv_normalization': ['normalize_raw_dataframe', 'classify_for_diagnostics'],
        'private_evidence_matching': ['match_known_worthless_derecognitions'],
        'canonical_fifo': ['fifo_realized_and_positions', 'fifo_derivative_positions'],
        'stock_enrichment': ['enrich_holdings_with_dynamic_live_prices'],
        'identity_resolution': ['find_best_yahoo_ticker'],
        'openfigi_prefetch': ['prefetch_openfigi_identities'],
        'stock_quotes': ['fetch_latest_yahoo_price'],
        'fx_quotes': ['get_fx_to_eur'],
        'historical_downloads': ['_fetch_yahoo_history'],
        'dividend_enrichment': ['fetch_yahoo_annual_dividends', 'build_actual_observed_dividend_yoc_table'],
        'canonical_lifetime': ['build_lifetime_performance', 'build_advanced_portfolio_insights'],
        'canonical_historical': ['build_historical_analytics'],
        'canonical_periods': ['build_period_performance'],
        'canonical_benchmark_pme': ['_benchmark_pme'],
    }
    for name, functions in groups.items():
        for function in functions:
            if function in ns:
                ns[function] = timings.wrap(ns[function], name)
    return timings
