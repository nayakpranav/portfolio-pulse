"""Canonical numeric outputs to display models; accounting remains in V6.7.8."""
from dataclasses import dataclass
from datetime import date
import math
import pandas as pd
from pulse.core import build_snapshot_data

@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    value: float | None
    scope: str
    explanation: str
    unit: str = 'EUR'
    status: str = 'AVAILABLE'

    @property
    def display(self):
        if self.value is None:
            return 'Unavailable'
        return f'{self.value:,.2f}%' if self.unit == '%' else f'€{self.value:,.2f}'

def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError,ValueError):
        return None

def prepare(result, report_date=None):
    report_date = report_date or date.today()
    life, advanced, historical = (result[k] for k in ('lifetime_metrics','advanced_metrics','historical_metrics'))
    blocked = result['accounting_status'] != 'COMPLETE'
    snapshot = build_snapshot_data(dividends=pd.DataFrame(result['dividends']),interest=pd.DataFrame(result['interest']),
        holdings=pd.DataFrame(result['holdings']),daily_nav=pd.DataFrame(result['daily_nav_history']),
        latest_transaction_date=result['latest_transaction_date'],report_date=report_date,reporting_year=report_date.year)
    # Known complete ledgers can remain valid independently of unrelated blockers.
    income_blocked = any(r.get('severity') == 'BLOCKING' and ('dividend' in r.get('check','') or 'duplicate' in r.get('check','')) for r in result['pre_diagnostics'])
    for key in ('net_dividend_eur','net_dividend_income_eur'):
        if result['dividends'] and key in result['dividends'][0] and any(number(r.get(key)) is None for r in result['dividends']):
            income_blocked = True
    income_blocked |= any(number(r.get('net_interest_eur')) is None for r in result['interest'])
    if income_blocked:
        for key in ('ytd_income','ytd_dividends','ytd_interest'):
            snapshot[key] = None
        for month in snapshot['months']:
            month.update(total=None,dividends=None,interest=None,status='unavailable')
    history_ok = historical.get('historical_analytics_status') in {'OK','OK_WITH_LOW_CONFIDENCE_FALLBACK'}
    defs = [
        ('value','Tracked Investment Value',life.get('lifetime_current_tracked_open_value_eur'),'Tracked investments · cash excluded',
         'Current value of tracked stocks, funds and any valued derivatives. Useful for seeing invested assets; brokerage cash is excluded and missing prices block the total.'),
        ('profit','Lifetime Economic Profit',life.get('lifetime_economic_profit_eur'),'Lifetime · tracked investments + net income',
         'Open and realized investment profit plus recognized net income. Measures investment outcomes; promotional personal benefit is separate and is not added again.'),
        ('capital','Net User Capital Committed',life.get('lifetime_net_committed_user_ecosystem_eur'),'Lifetime · recovery includes interest',
         'User-funded investment outflows less canonical recovery, including interest. Shows capital not yet recovered under this definition; it is not deposits or current cost basis and can be negative.'),
        ('recovery','Cash Recovered',life.get('lifetime_ecosystem_recovery_eur'),'Lifetime · sales, settlements + net income',
         'Canonical investment-sale, settlement and recognized income recovery, including interest. Helps distinguish capital recovery from profit; reinvestment and non-cash legs follow core semantics and this is not withdrawable account cash.'),
        ('mwr','Stock/Fund MWR',advanced.get('stock_fund_mwr_acquisition_pct'),'Annualized · actual dated stock/fund flows',
         'Annualized return including the timing and size of actual stock/fund cash flows. Useful for your invested experience; excludes derivatives and interest and can be unavailable or ambiguous.'),
        ('benchmark','Benchmark MWR',historical.get('benchmark_mwr_pct'),'Annualized · matched stock/fund cash flows',
         'Annualized return of the selected benchmark using matched investment cash flows. Supports a comparable reference; depends on benchmark prices and is not ordinary price appreciation.'),
        ('twr','Stock/Fund TWR',historical.get('stockfund_twr_since_inception_pct'),'Cumulative · reconstructed stock/fund sleeve',
         'Cumulative return with the core end-of-day cash-flow adjustment. Helps separate timing from portfolio performance; excludes cash and derivatives and depends on historical price coverage.'),
        ('income','Net Investment Income YTD',snapshot['ytd_income'],f'{snapshot["year"]} receipts · source cutoff respected',
         'Recognized net dividends plus net interest in the reporting year. Shows actual investment income; excludes sales, gains, transfers and SaveBack. Reinvested dividend income is recognized once and uncovered months are not zero.'),
    ]
    metrics = []
    for key,label,value,scope,explanation in defs:
        valid = not blocked if key != 'income' else not income_blocked
        status = 'INCOMPLETE_ACCOUNTING' if not valid else 'AVAILABLE'
        if key == 'mwr' and advanced.get('stock_fund_mwr_acquisition_status') != 'OK':
            valid = False; status = advanced.get('stock_fund_mwr_acquisition_status','UNAVAILABLE')
        if key == 'benchmark' and (not result['benchmark_enabled'] or historical.get('benchmark_mwr_status') != 'OK' or advanced.get('stock_fund_mwr_valuation_date') != str(historical.get('historical_valuation_date',''))[:10]):
            valid = False; status = 'DISABLED' if not result['benchmark_enabled'] else 'INCOMPARABLE_OR_UNAVAILABLE'
        if key == 'twr' and not history_ok:
            valid = False; status = historical.get('historical_analytics_status','UNAVAILABLE')
        value = number(value) if valid else None
        if value is None and status == 'AVAILABLE':
            status = 'INCOMPLETE_VALUATION'
        metrics.append(Metric(key,label,value,scope,explanation,'%' if key in {'mwr','benchmark','twr'} else 'EUR',status))
    by_key = {m.key:m for m in metrics}
    issues = []
    for item in result['pre_diagnostics']:
        if item.get('check','').startswith('sector_metadata'):
            continue  # Optional sector enrichment is not part of this product's scope.
        if item.get('severity') in {'BLOCKING','WARNING'}:
            issues.append(f"{item['check'].replace('_',' ')}: {item.get('value','review required')}")
    missing = sum(r.get('valuation_status') == 'BLOCKING' for r in result['valuation_diagnostics'])
    if missing:
        issues.append(f'{missing} open position(s) lack a required reliable valuation.')
    valued_quotes=[r.get('quote_date') for r in result['valuation_diagnostics'] if r.get('valuation_status')=='VALUED']
    parsed_quotes=pd.to_datetime(valued_quotes,errors='coerce',utc=True)
    if len(parsed_quotes) and parsed_quotes.isna().any():
        issues.append('At least one valued position lacks a verified quote date.')
    elif len(parsed_quotes) and any((report_date-d.date()).days>7 for d in parsed_quotes):
        issues.append('At least one quote is more than seven days old; current-value freshness is limited.')
    if not history_ok:
        issues.append('Stock/fund history: '+str(historical.get('historical_analytics_status','unavailable')).replace('_',' ').lower())
    elif historical.get('historical_analytics_status') == 'OK_WITH_LOW_CONFIDENCE_FALLBACK':
        issues.append('Historical returns include transaction or filled-price estimates; interpret with caution.')
    if result['benchmark_enabled'] and by_key['benchmark'].value is None:
        issues.append('Benchmark comparison is unavailable or lacks a comparable valuation period.')
    if income_blocked:
        issues.append('Recognized income contains an unresolved amount or reconciliation issue.')
    if blocked:
        snapshot['top_holdings'] = []
        snapshot['trend'] = []
    insights = []
    if by_key['mwr'].value is not None and by_key['benchmark'].value is not None:
        difference = by_key['mwr'].value-by_key['benchmark'].value
        insights.append(f'Stock/fund annualized MWR is {abs(difference):.2f} percentage points {"above" if difference >= 0 else "below"} the cash-flow-matched benchmark.')
    if by_key['profit'].value is not None:
        contributors = [r for r in result['wealth_contribution'] if r.get('instrument_type') != 'INTEREST' and number(r.get('economic_contribution_eur')) is not None]
        for positive in (True,False):
            selected = [r for r in contributors if (number(r['economic_contribution_eur']) > 0 if positive else number(r['economic_contribution_eur']) < 0)]
            if selected:
                r = (max if positive else min)(selected,key=lambda x:x['economic_contribution_eur'])
                insights.append(f"{r['security_name']} is the largest {'positive lifetime contributor' if positive else 'lifetime detractor'} at €{r['economic_contribution_eur']:,.2f}.")
    if by_key['income'].value is not None:
        lead = 'Dividends' if snapshot['ytd_dividends'] >= snapshot['ytd_interest'] else 'Interest'
        insights.append(f"Recognized {snapshot['year']} net investment income is €{snapshot['ytd_income']:,.2f}. {lead} account for the larger share.")
    if snapshot['top_holdings']:
        weight = sum(r['weight_pct'] or 0 for r in snapshot['top_holdings'][:5])
        insights.append(f'The five largest valued stock/fund holdings represent {weight:.1f}% of valued stock/fund assets. Funds are individual instruments, without look-through.')
    if not insights:
        insights = ['Data requires review before performance and attribution observations can be supported.']
    nav = pd.DataFrame(result['daily_nav_history']) if not blocked else pd.DataFrame()
    periods = [r for r in result['period_performance'] if r.get('period_key') in {'1M','3M','YTD','1Y','MAX'} and r.get('available') is True] if not blocked and history_ok else []
    return dict(metrics=metrics,by_key=by_key,snapshot=snapshot,nav=nav,issues=list(dict.fromkeys(issues)),
                health='Review required' if blocked or income_blocked else 'Partial' if issues else 'Complete',
                insights=insights[:5],periods=periods,raw=result,valuation_missing=missing)
