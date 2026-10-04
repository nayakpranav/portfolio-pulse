"""Canonical numeric outputs to display models; accounting remains in V6.7.8."""
from dataclasses import dataclass
from datetime import date
import math
import pandas as pd
from pulse.core import build_snapshot_data
from pulse.scopes import availability, blocker_message

MODEL_SCHEMA_VERSION = 4

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

def prepare(result, report_date=None, analysis_scope=None):
    report_date = report_date or date.today()
    complete_result=result
    partial=result.get('unaffected_scope')
    # Preserve the full result for auditing and scope toggling. Only explicitly
    # localized accounting failures may supply a separate canonical projection.
    if partial:
        result=dict(result,**{k:v for k,v in partial.items() if k not in {'excluded','scope_dividends'}})
        projection_result=dict(result,accounting_status='COMPLETE',pre_diagnostics=[])
        projection_result['dividends']=partial['scope_dividends']
        projected_dependencies=availability(projection_result)
    life, advanced, historical = (result[k] for k in ('lifetime_metrics','advanced_metrics','historical_metrics'))
    benchmark_label = result.get('benchmark_name', 'Selected benchmark')
    dependencies = availability(result)
    if partial:
        dependencies.update({k:projected_dependencies[k] for k in ('stock_accounting','stock_valued','stock_value','stock_profit','stock_profit_status','missing_stock')})
        dependencies['ecosystem_accounting']=False
        dependencies['full_valued']=False
    blocked = not dependencies['ecosystem_accounting']
    stock_blocked = not dependencies['stock_accounting']
    if analysis_scope is None:
        analysis_scope = 'stocks_funds' if partial or dependencies['missing_derivative'] and not result.get('synthetic') else 'full_portfolio'
    if analysis_scope not in {'stocks_funds','full_portfolio'}:
        raise ValueError('Unsupported analysis scope')
    stock_scope = analysis_scope == 'stocks_funds'
    snapshot = build_snapshot_data(dividends=pd.DataFrame(result['dividends']),interest=pd.DataFrame(result['interest']),
        holdings=pd.DataFrame(result['holdings']),daily_nav=pd.DataFrame(result['daily_nav_history']),
        latest_transaction_date=result['latest_transaction_date'],report_date=report_date,reporting_year=report_date.year)
    # Known complete ledgers can remain valid independently of unrelated blockers.
    income_blocked = any(r.get('severity') == 'BLOCKING' and ('dividend' in r.get('check','') or 'duplicate' in r.get('check','')) for r in result['pre_diagnostics'])
    for key in ('net_dividend_eur','net_dividend_income_eur'):
        if result['dividends'] and key in result['dividends'][0] and any(number(r.get(key)) is None for r in result['dividends']):
            income_blocked = True
    dividends_sound = not income_blocked
    if partial:
        dividends_sound=all(number(r.get('net_dividend_eur',r.get('net_dividend_income_eur'))) is not None
            and abs(number(r.get('reconciliation_error',0)) or 0)<=1e-6 for r in partial['scope_dividends'])
    income_blocked |= any(number(r.get('net_interest_eur')) is None for r in result['interest'])
    if income_blocked:
        for key in ('ytd_income','ytd_dividends','ytd_interest'):
            snapshot[key] = None
        for month in snapshot['months']:
            month.update(total=None,dividends=None,interest=None,status='unavailable')
    history_ok = historical.get('historical_analytics_status') in {'OK','OK_WITH_LOW_CONFIDENCE_FALLBACK'}
    defs = [
        ('value','Stocks & Funds Value' if stock_scope else 'Tracked Investment Value',dependencies['stock_value'] if stock_scope else life.get('lifetime_current_tracked_open_value_eur'),'Stocks & funds · cash/derivatives excluded' if stock_scope else 'Full portfolio · cash excluded',
         'Current value of the selected investment scope. Stock/fund value sums complete canonical valued holdings; no missing quotation is replaced with zero. Full tracked value also requires derivative quotes.'),
        ('profit','Stocks & Funds Lifetime Profit' if stock_scope else 'Lifetime Economic Profit',dependencies['stock_profit'] if stock_scope else life.get('lifetime_economic_profit_eur'),'Stocks & funds · FIFO P/L + net dividends' if stock_scope else 'Full portfolio · includes net income',
         'Stock/fund lifetime profit uses canonical open/realized FIFO P/L plus recognized net dividends, reconciled against the stock/fund cash-flow ledger and per-security contribution table. Derivatives, cash interest and promotional personal benefit are excluded. Full lifetime economic profit retains the original ecosystem definition.'),
        ('capital','Net User Capital Committed',life.get('lifetime_net_committed_user_ecosystem_eur'),'Full ecosystem · recovery includes interest',
         'User-funded investment outflows less canonical recovery, including interest. Shows capital not yet recovered under this definition; it is not deposits or current cost basis and can be negative.'),
        ('recovery','Cash Recovered',life.get('lifetime_ecosystem_recovery_eur'),'Full ecosystem · settlements + net income',
         'Canonical investment-sale, settlement and recognized income recovery, including interest. Helps distinguish capital recovery from profit; reinvestment and non-cash legs follow core semantics and this is not withdrawable account cash.'),
        ('mwr','Stock/Fund MWR',advanced.get('stock_fund_mwr_acquisition_pct'),'Annualized · actual dated stock/fund flows',
         'Annualized return including the timing and size of actual stock/fund cash flows. Useful for your invested experience; excludes derivatives and interest and can be unavailable or ambiguous.'),
        ('benchmark','Benchmark MWR',historical.get('benchmark_mwr_pct'),f'{benchmark_label} · annualized matched flows',
         'Annualized return of the selected benchmark using matched investment cash flows. Supports a comparable reference; depends on benchmark prices and is not ordinary price appreciation.'),
        ('twr','Stock/Fund TWR',historical.get('stockfund_twr_since_inception_pct'),'Cumulative · reconstructed stock/fund sleeve',
         'Cumulative return with the core end-of-day cash-flow adjustment. Helps separate timing from portfolio performance; excludes cash and derivatives and depends on historical price coverage.'),
        ('income','Net Investment Income YTD',snapshot['ytd_income'],f'{snapshot["year"]} recognized net investment income · source cutoff respected',
         'Recognized net dividends plus net interest in the reporting year. Shows actual investment income; excludes sales, gains, transfers and SaveBack. Reinvested dividend income is recognized once and uncovered months are not zero.'),
    ]
    metrics = []
    for key,label,value,scope,explanation in defs:
        if partial and (key in {'mwr','benchmark','twr'} or stock_scope and key in {'value','profit'}):
            scope='Partial · unaffected securities only · '+scope
        valid = not stock_blocked if key in {'mwr','benchmark','twr'} or stock_scope and key in {'value','profit'} else not blocked
        if key == 'income': valid = not income_blocked
        if key in {'capital','recovery'} or key=='profit' and not stock_scope:
            valid &= not income_blocked
        status = 'INCOMPLETE_ACCOUNTING' if not valid else 'AVAILABLE'
        if key in {'mwr','twr','benchmark'} and not dividends_sound:
            valid = False; status = 'INCOMPLETE_DIVIDEND_LEDGER'
        if key in {'value','profit'} and valid:
            valued = dependencies['stock_valued'] if stock_scope else dependencies['full_valued']
            if not valued:
                valid = False; status = 'INCOMPLETE_VALUATION'
            elif key == 'profit':
                profit_status = dependencies['stock_profit_status'] if stock_scope else life.get('lifetime_profit_status','UNAVAILABLE')
                if profit_status not in {'AVAILABLE','OK'}:
                    valid = False; status = profit_status
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
    issues = [blocker_message(c) for c in dependencies['accounting_checks']]
    if partial:
        issues.insert(0,'Partial stock/fund scope: entire affected security histories are excluded: '+', '.join(r['name'] for r in partial['excluded'])+'. Full portfolio totals and full stock/fund performance remain unavailable.')
    if dependencies['unexplained_accounting']:
        issues.append('Accounting is incomplete without a classified cause; dependent results remain blocked.')
    for item in result['pre_diagnostics']:
        if item.get('check','').startswith('sector_metadata'):
            continue  # Optional sector enrichment is not part of this product's scope.
        if item.get('severity') == 'WARNING':
            audit=result.get('trade_amount_audit',{})
            if item['check']=='missing_trade_amount_rows' and audit:
                detail=[]
                if audit['ipo_cash_reconciled']:detail.append(f"{audit['ipo_cash_reconciled']} reconciled to IPO subscription cash and fees")
                if audit['quantity_price_inferred']:detail.append(f"{audit['quantity_price_inferred']} inferred from quantity × price under V6.7.8; verify broker cash")
                if audit['other_missing']:detail.append(f"{audit['other_missing']} without a supported stock/fund inference; inspect source evidence")
                issues.append(f"Missing trade amount rows: {audit['missing_count']} — "+'; '.join(detail)+'. Original canonical warning retained; this is not a worthless-removal event.')
            elif item['check']=='zero_or_missing_cost_buy_rows' and item.get('value')==audit.get('missing_count')==audit.get('ipo_cash_reconciled') and audit.get('ipo_cash_reconciled'):
                issues.append('Raw buy amount absent: canonical IPO cash/fee allocation supplies the acquisition basis. Source warning retained.')
            else:issues.append(f"{item['check'].replace('_',' ')}: {item.get('value','review required')}")
    missing = sum(r.get('valuation_status') == 'BLOCKING' for r in result['valuation_diagnostics'])
    if missing:
        issues.append(f'{missing} open position(s) lack a required reliable valuation.')
        derivative_gaps=[r for r in result['valuation_diagnostics'] if r.get('valuation_status')=='BLOCKING' and r.get('instrument_type')=='DERIVATIVE']
        if derivative_gaps:
            issues.append('Tracked value and lifetime profit require derivative valuations: '+', '.join(str(r.get('security_name','Derivative')) for r in derivative_gaps[:3])+'.')
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
        issues.append(f'{benchmark_label}: comparison unavailable; reliable adjusted prices, currency/FX and matched-date coverage are required.')
    if result.get('benchmark_identity',{}).get('warning'):
        issues.append(result['benchmark_identity']['warning'])
    if result.get('provider_diagnostics',{}).get('restricted_providers'):
        issues.append('A market-data provider denied or rate-limited requests. Further requests to that provider were stopped; missing inputs remain unavailable.')
    if any(r.get('quote_status')=='MANUAL_CONFIRMED' for r in result.get('active_derivatives',[])):
        issues.append('Explicit dated manual derivative valuations are used. Their source/date is user-confirmed; a captured observation date is not necessarily an exchange quote timestamp.')
    if income_blocked:
        issues.append('Recognized income contains an unresolved amount or reconciliation issue.')
    if stock_blocked:
        snapshot['top_holdings'] = []
        snapshot['valued_stockfund_total'] = None
    if stock_blocked or not history_ok or not dividends_sound:
        snapshot['trend'] = []
        snapshot['valuation_date'] = None
    if stock_scope and dependencies['stock_profit_status']=='SLEEVE_RECONCILIATION_REQUIRED':
        issues.append('Stock/fund profit did not reconcile across canonical FIFO, cash-flow and contribution sources; this profit remains unavailable.')
    if stock_scope and dependencies['stock_profit_status']=='CANONICAL_AUDIT_REQUIRED':
        issues.append('This result lacks the canonical sources needed to audit stock/fund profit. Reanalyze the export with the current application.')
    insights = []
    if by_key['mwr'].value is not None and by_key['benchmark'].value is not None:
        difference = by_key['mwr'].value-by_key['benchmark'].value
        insights.append(f'Stock/fund annualized MWR is {abs(difference):.2f} percentage points {"above" if difference >= 0 else "below"} the cash-flow-matched {benchmark_label}.')
    if by_key['profit'].value is not None:
        contributors = [r for r in result['wealth_contribution'] if r.get('instrument_type') in ({'STOCK','FUND','STOCK/FUND'} if stock_scope else {'STOCK','FUND','STOCK/FUND','DERIVATIVE'}) and number(r.get('economic_contribution_eur')) is not None]
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
    nav = pd.DataFrame(result['daily_nav_history']) if not stock_blocked and history_ok and dividends_sound else pd.DataFrame()
    periods = [r for r in result['period_performance'] if r.get('period_key') in {'1M','3M','YTD','1Y','MAX'} and r.get('available') is True] if not stock_blocked and history_ok and dividends_sound else []
    full_totals = dict(value=number(life.get('lifetime_current_tracked_open_value_eur')) if not blocked and dependencies['full_valued'] else None,
                       profit=number(life.get('lifetime_economic_profit_eur')) if not blocked and not income_blocked and dependencies['full_valued'] and life.get('lifetime_profit_status')=='OK' else None)
    holdings = [dict(name=r.get('security_name','Security'),quantity=r.get('current_quantity'),value=number(r.get('live_current_value_eur')),
                     quote_date=r.get('live_price_date'),status='Valued' if number(r.get('live_current_value_eur')) is not None else 'Unpriced')
                for r in result['holdings'] if r.get('position_status')=='ACTIVE'] if not stock_blocked else []
    from pulse.composition import composition
    composition_date=report_date
    if result.get('synthetic'):
        dated=pd.to_datetime(result['historical_metrics'].get('historical_valuation_date'),errors='coerce')
        if pd.notna(dated):composition_date=dated.date()
    composition_data=composition(result,composition_date,not stock_blocked,bool(partial))
    return dict(composition=composition_data,metrics=metrics,by_key=by_key,snapshot=snapshot,nav=nav,issues=list(dict.fromkeys(issues)),
                health='Review required' if blocked or income_blocked else 'Partial' if issues else 'Complete',
                insights=insights[:5],periods=periods,raw=complete_result,valuation_missing=missing,
                analysis_scope=analysis_scope,scope_label='Unaffected stocks & funds (partial)' if stock_scope and partial else 'Stocks & funds' if stock_scope else 'Full portfolio',
                excluded_securities=partial['excluded'] if partial else [],
                returns_scope_label='Unaffected stocks & funds (partial)' if partial else 'Stocks & funds',
                dependencies=dependencies,full_totals=full_totals,holdings=holdings,model_schema_version=MODEL_SCHEMA_VERSION)
