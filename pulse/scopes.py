"""Presentation dependencies and reconciled canonical sleeve projections.

The complete transaction input and V6.7.8 engine are never filtered or altered.
"""
import math


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def availability(result):
    ledger_checks = [r['check'] for r in result['pre_diagnostics']
                     if r.get('severity') == 'BLOCKING'
                     and r.get('check') != 'current_valuation_blocking_positions']
    unexplained = result['accounting_status'] != 'COMPLETE' and not ledger_checks
    stock_checks = [c for c in ledger_checks if c != 'unmatched_derivative_sell_rows']
    stock_accounting = not stock_checks and not unexplained
    ecosystem_accounting = not ledger_checks and not unexplained
    missing_stock = [r for r in result['valuation_diagnostics']
                     if r.get('valuation_status') == 'BLOCKING'
                     and r.get('instrument_type') != 'DERIVATIVE']
    missing_derivative = [r for r in result['valuation_diagnostics']
                          if r.get('valuation_status') == 'BLOCKING'
                          and r.get('instrument_type') == 'DERIVATIVE']
    active = [r for r in result['holdings'] if r.get('position_status') == 'ACTIVE']
    stock_valued = not missing_stock and all(number(r.get('live_current_value_eur')) is not None for r in active)
    full_valued = stock_valued and not missing_derivative
    stock_value = None
    stock_profit = None
    profit_status = 'INCOMPLETE_ACCOUNTING' if not stock_accounting else 'INCOMPLETE_VALUATION' if not stock_valued else 'CANONICAL_AUDIT_REQUIRED'
    if stock_accounting and stock_valued:
        stock_value = sum(number(r['live_current_value_eur']) for r in active)
        sources = result.get('scope_sources', {})
        canonical_value = number(sources.get('stockfund_current_value_eur'))
        if canonical_value is not None and abs(stock_value-canonical_value) > .000001:
            stock_value = None
        components = [number(sources.get(k)) for k in ('stockfund_open_pl_eur', 'stockfund_realized_pl_eur', 'net_dividends_eur')]
        ledger = result.get('lifetime_cashflow_ledger')
        rows = [r for r in result['wealth_contribution'] if r.get('instrument_type') in {'STOCK', 'FUND', 'STOCK/FUND'}]
        contributions = [number(r.get('economic_contribution_eur')) for r in rows]
        dividends_sound = all(number(r.get('net_dividend_eur', r.get('net_dividend_income_eur'))) is not None for r in result['dividends'])
        if not dividends_sound:profit_status = 'INCOMPLETE_DIVIDEND_LEDGER'
        if stock_value is not None and ledger is not None and dividends_sound and all(v is not None for v in components+contributions):
            acquisitions = [number(r.get('acquisition_outflow_eur')) for r in ledger if r.get('asset_scope') == 'STOCK_FUND']
            recovery = [number(r.get('core_recovery_eur')) for r in ledger if r.get('asset_scope') == 'STOCK_FUND' or r.get('cashflow_type') == 'DIVIDEND_NET']
            if all(v is not None for v in acquisitions+recovery):
                component_profit = sum(components)
                ledger_profit = stock_value + sum(recovery) - sum(acquisitions)
                if abs(component_profit-ledger_profit) <= .05 and abs(component_profit-sum(contributions)) <= .05:
                    stock_profit = component_profit
                    profit_status = 'AVAILABLE'
                else:
                    profit_status = 'SLEEVE_RECONCILIATION_REQUIRED'
    return dict(stock_accounting=stock_accounting, ecosystem_accounting=ecosystem_accounting,
                accounting_checks=ledger_checks, unexplained_accounting=unexplained,
                stock_valued=stock_valued, full_valued=full_valued,
                stock_value=stock_value, stock_profit=stock_profit, stock_profit_status=profit_status,
                missing_stock=len(missing_stock), missing_derivative=len(missing_derivative))


def blocker_message(check):
    messages = {
        'worthless_derecognition_unresolved_events': 'Security removal has not matched private verified event evidence; affected accounting remains blocked.',
        'unknown_transaction_rows': 'Unsupported transaction classifications require review; affected accounting remains blocked.',
        'unsupported_or_failed_corporate_actions': 'A corporate action failed canonical validation; affected positions remain blocked.',
        'unmatched_stock_sell_rows': 'A stock/fund sale lacks supported prior acquisition quantity.',
        'unmatched_derivative_sell_rows': 'A derivative sale lacks supported prior acquisition quantity; full-ecosystem accounting requires review.',
        'duplicate_transaction_id_rows': 'Duplicate transaction identifiers require review before dependent accounting or income is valid.',
        'dividend_reinvestment_unresolved_events': 'Dividend reinvestment legs have not reconciled; affected accounting remains blocked.',
        'dividend_reconciliation_errors': 'Recognized dividend amounts failed reconciliation.',
        'missing_trade_shares_rows': 'A transaction lacks the quantity required for accounting.',
        'date_parse_failed_rows': 'A transaction lacks a valid accounting date.',
    }
    return messages.get(check, 'Canonical accounting check requires review: '+check.replace('_', ' ')+'.')
