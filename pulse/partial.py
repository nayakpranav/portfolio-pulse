"""Explicit unaffected-security projections using canonical functions only.

The full input, full accounting status and full diagnostic contract are retained.
An affected instrument's entire lineage is excluded from this separate scope,
never just its unresolved transaction. Unlocalized failures stay fail-closed.
"""
import pandas as pd


def projection(ns):
    checks={r['check'] for _,r in ns['pre_diagnostics'].iterrows()
            if r['severity']=='BLOCKING' and r['check']!='current_valuation_blocking_positions'}
    localized={'unknown_transaction_rows','worthless_derecognition_unresolved_events',
               'unsupported_or_failed_corporate_actions','unmatched_stock_sell_rows',
               'unmatched_derivative_sell_rows','dividend_reinvestment_unresolved_events'}
    if not checks or not checks <= localized:
        return None
    affected=set()
    df=ns['df']
    support=ns['transaction_support_matrix']
    for _,rule in support[support['blocking'].eq(True)].iterrows():
        rows=df[df['type_norm'].eq(rule['type']) & df['category'].eq(rule['category']) & df['asset_class_clean'].eq(rule['asset_class'])]
        if rows.empty or not rows['asset_class_clean'].isin({'STOCK','FUND','DERIVATIVE'}).all():
            return None
        stock=rows[rows['asset_class_clean'].isin({'STOCK','FUND'})]
        if stock['isin'].eq('').any():return None
        affected.update(stock['isin'])
    for name in ('worthless_derecognition_diagnostics','dividend_reinvestment_diagnostics'):
        table=ns.get(name,pd.DataFrame())
        if not table.empty:
            for _,r in table[table['validation_status'].ne('PASS')].iterrows():
                isin=str(r.get('isin',''))
                if not isin or isin=='nan':return None
                affected.add(isin)
    for name,column in [('unmatched_stock_sells','isin'),('corporate_action_blockers','old_isin')]:
        table=ns.get(name,pd.DataFrame())
        if not table.empty:
            for _,r in table.iterrows():
                isin=str(r.get(column,''))
                if not isin or isin=='nan':return None
                affected.add(isin)
    trades=ns['trades']
    # Expand to all aliases before selecting any canonical ledger.
    while True:
        prior=len(affected)
        for _,r in trades.iterrows():
            aliases=set(str(r.get('historical_isin_aliases','')).split(';')) | {str(r.get('isin','')),str(r.get('raw_isin',''))}
            if affected & aliases:affected.update(aliases-{'','nan'})
        if len(affected)==prior:break
    def select(table):
        return table[~table['isin'].astype(str).isin(affected)].copy() if not table.empty and 'isin' in table else table.copy()
    chosen={name:select(ns[name]) for name in ('trades','holdings','combined','open_lots','realized','dividends')}
    if chosen['trades'].empty:
        return None
    blank=pd.DataFrame()
    scope_df=df[~df['isin'].astype(str).isin(affected)].copy()
    advanced=ns['build_advanced_portfolio_insights'](
        df=scope_df,trades=chosen['trades'],dividends=chosen['dividends'],interest=ns['interest'],
        combined=chosen['combined'],open_lots=chosen['open_lots'],realized=chosen['realized'],
        derivative_ledger=blank,derivative_realized=blank,derivative_open_lots=blank,
        active_derivatives=blank,dividend_projection_by_holding=blank,dividend_projection_by_month=blank,
        max_date=ns['max_date'],ttm_start=ns['ttm_start'])
    life=ns['build_lifetime_performance'](
        df=scope_df,trades=chosen['trades'],dividends=chosen['dividends'],interest=ns['interest'],
        combined=chosen['combined'],derivative_ledger=blank,derivative_positions=blank,active_derivatives=blank,
        advanced_metrics=advanced['metrics'])
    historical=ns['build_historical_analytics'](
        df=scope_df,trades=chosen['trades'],dividends=chosen['dividends'],interest=ns['interest'],
        holdings=chosen['holdings'],combined=chosen['combined'],derivative_positions=blank,
        realized_behavior_events=advanced['realized_events'],advanced_metrics=advanced['metrics'],
        max_date=ns['max_date'],benchmark_ticker=ns['BENCHMARK_TICKER'],benchmark_name=ns['BENCHMARK_NAME'],
        annual_risk_free_rate_pct=ns['RISK_FREE_RATE_PCT'])
    current=chosen['combined']
    active=current[current['position_status'].eq('ACTIVE')]
    def complete_sum(table,column):
        return ns['complete_numeric_sum'](table[column]) if not table.empty else 0.0
    sources=dict(stockfund_current_value_eur=complete_sum(active,'live_current_value_eur'),
        stockfund_open_pl_eur=complete_sum(active,'live_unrealized_pl_acquisition_basis_eur'),
        stockfund_realized_pl_eur=life['metrics']['lifetime_stock_realized_pl_eur'],
        net_dividends_eur=life['metrics']['lifetime_net_dividend_recovery_eur'])
    return dict(excluded=[dict(isin=isin,name=next(iter(df.loc[df['isin'].eq(isin),'security_name']),isin)) for isin in sorted(affected)],
        holdings=chosen['holdings'],scope_dividends=chosen['dividends'],advanced_metrics=advanced['metrics'],
        historical_metrics=historical['metrics'],daily_nav_history=historical['daily_nav'],
        period_performance=historical['period_performance'],wealth_contribution=historical['wealth_contribution'],
        scope_sources=sources,lifetime_cashflow_ledger=life['cashflow_ledger'],
        valuation_diagnostics=ns['build_valuation_diagnostics'](chosen['holdings'],ns['active_derivatives']))
