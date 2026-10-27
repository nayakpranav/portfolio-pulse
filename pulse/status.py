"""Separate current valuation availability from retained quality disclosures."""


def report_status(dependencies, full_totals, scope, scope_label, issues, income_blocked):
    full_complete=(dependencies['ecosystem_accounting'] and dependencies['full_valued']
                   and all(v is not None for v in full_totals.values()))
    selected_complete=full_complete if scope=='full_portfolio' else (
        dependencies['stock_accounting'] and dependencies['stock_valued']
        and dependencies['stock_value'] is not None and dependencies['stock_profit'] is not None)
    blocked=not dependencies['ecosystem_accounting'] or income_blocked
    return dict(full_complete=full_complete,selected_complete=selected_complete,
        headline=scope_label+' — '+('Valuation complete' if selected_complete else 'Review required' if blocked else 'Valuation incomplete'),
        quality='Data quality: review notes' if issues else 'Data quality: no review notes',
        accounting='Accounting: review required' if blocked else 'Accounting: validated; source notes retained' if issues else 'Accounting: validated')
