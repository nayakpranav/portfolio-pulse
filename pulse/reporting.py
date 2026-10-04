"""Display-only report content, shared by exports; never serialize worker state."""
from html import escape
from pulse.adapter import number
from pulse.design import COLORS, CSS_TOKENS

MINT = COLORS['mint']
HOLDING_CSS = CSS_TOKENS+'''.holding-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin:16px 0 24px;align-items:stretch}
.holding-card{background:#112238;border:1px solid #24415e;border-radius:12px;padding:18px;display:flex;flex-direction:column;min-width:0}
.holding-rank{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:var(--fl-actual);color:var(--fl-background);font-size:.85rem;font-weight:750;font-variant-numeric:tabular-nums;flex-shrink:0}
.holding-name{color:#f6f9fd;font-size:1.05rem;line-height:1.4;font-weight:650;overflow-wrap:anywhere;flex:1;margin:12px 0 20px}
.holding-value{color:#a8ebbc;font-size:clamp(1.1rem,1.65vw,1.65rem);line-height:1.35;font-weight:750;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.holding-weight{color:#91a7bc;font-size:.85rem;margin-top:6px}
.holding-track{height:4px;background:#24415e;border-radius:2px;margin-top:16px;overflow:hidden}.holding-fill{height:100%;background:#42cbea;border-radius:2px}
@media(max-width:950px){.holding-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.holding-value{font-size:1.5rem}}
@media(max-width:480px){.holding-grid{grid-template-columns:1fr}.holding-name{font-size:1.1rem}.holding-value{font-size:1.7rem}}'''

def money(value):
    value = number(value)
    return f'€{value:,.2f}' if value is not None else 'Unavailable'

def holding_cards(holdings):
    cards=[]
    largest=max([number(r.get('value')) or 0 for r in holdings[:5]] or [0])
    for index,row in enumerate(holdings[:5],1):
        weight=number(row.get('weight_pct'))
        value=number(row.get('value'));relative=max(0,min(100,value/largest*100)) if value is not None and largest>0 else None
        bar='<div class="holding-track" aria-hidden="true"><div class="holding-fill" style="width:'+f'{relative:.3f}'+'%"></div></div>' if relative is not None else ''
        cards.append('<article class="holding-card"><div class="holding-rank" aria-label="Rank '+str(index)+'">'+f'{index:02d}'+
            '</div><div class="holding-name">'+escape(str(row['name']))+
            '</div><div class="holding-value">'+money(row.get('value'))+
            '</div><div class="holding-weight">'+(f'{weight:.1f}% of valued stocks/funds' if weight is not None else 'Weight unavailable')+'</div>'+bar+'</article>')
    return '<div class="holding-grid">'+''.join(cards)+'</div>' if cards else '<p>No supported valued holding ranking available.</p>'

def warning_summary(model):
    """Compact PDF disclosure; full diagnostic list is kept in the HTML and UI."""
    d=model['dependencies'];parts=[]
    if d['accounting_checks'] or d['unexplained_accounting']:
        from pulse.scopes import blocker_message
        parts.extend(blocker_message(c) for c in sorted(d['accounting_checks'],key=lambda c:c=='unknown_transaction_rows'))
        if d['unexplained_accounting']:parts.append('Unclassified accounting blocker; dependent figures unavailable.')
    if model.get('excluded_securities'):parts.insert(0,'Partial scope excludes entire affected security histories; full totals unavailable.')
    if d['missing_derivative']:parts.append(f"Missing derivative valuations: {d['missing_derivative']}; full portfolio value and profit unavailable.")
    if d['missing_stock']:parts.append('Unpriced stocks/funds omitted from rankings; dependent values unavailable.')
    for phrase,short in [
        ('seven days old','Stale quotes limit current-value freshness.'),
        ('lacks a verified quote date','Quote date not verified.'),
        ('price estimates','History includes price estimates.'),
        ('filled-price estimates','History includes price estimates.'),
        ('comparison unavailable','Benchmark unavailable: adjusted prices, EUR/FX or dates incomplete.'),
        ('price-only','Price-only index is not a total-return comparison.'),
        ('denied or rate-limited','Provider access limited; missing inputs stay unavailable.'),
        ('manual derivative','Dated manual derivative valuations used.'),
        ('unresolved amount','Income requires review.'),
    ]:
        if any(phrase.lower() in issue.lower() for issue in model['issues']):parts.append(short)
    audit=model['raw'].get('trade_amount_audit',{})
    if audit.get('ipo_cash_reconciled') and audit.get('missing_count')==audit['ipo_cash_reconciled']:parts.append('Missing CSV trade amount reconciled to IPO subscription cash; canonical warning retained.')
    elif audit.get('ipo_cash_reconciled'):parts.append('Some missing trade amounts reconcile to IPO cash; other missing amounts still require source review.')
    elif any('missing trade amount' in issue.lower() for issue in model['issues']):parts.append('Missing CSV trade amount: canonical inference used where supported; verify broker evidence.')
    if not parts and model['issues']:parts.append('Non-blocking source warnings; inspect Data health in the application or HTML report.')
    return ' '.join(dict.fromkeys(parts)) or 'No blocking issues in the selected scope.'
