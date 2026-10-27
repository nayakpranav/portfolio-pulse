"""Small shared, escaped offline presentation; all financial inputs are prepared."""
from html import escape
import pandas as pd


INTELLIGENCE_CSS='''.intelligence{margin:24px 0;border:1px solid #24415e;border-radius:12px;padding:18px;min-width:0}.intelligence>summary{font-size:1.2rem;color:#f6f9fd;font-weight:650}.intelligence details{margin:16px 0}.intel-facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin:16px 0}.intel-fact{font-variant-numeric:tabular-nums}.intel-fact strong{display:block;font-size:1.4rem;color:#a8ebbc}.intel-note{font-size:.875rem;color:#91a7bc;overflow-wrap:anywhere}.intel-table th button{padding:4px;background:transparent;border:0;color:inherit;text-align:inherit;font-size:inherit}.intel-table .number{text-align:right;white-space:nowrap}.table-scroll{overflow-x:auto;max-width:100%}.intel-table{width:100%;border-collapse:collapse;font-size:.875rem}.intel-table th,.intel-table td{text-align:left;padding:10px;border-bottom:1px solid #24415e;overflow-wrap:normal;word-break:normal}.intel-wide{min-width:900px}.intel-wide td{min-width:100px}.intel-wide td:first-child{min-width:160px}.intel-positive{color:#a8ebbc}.intel-negative{color:#fca5a5}'''

INTELLIGENCE_SCRIPT='''document.querySelectorAll('[data-intel-sort]').forEach(function(button){button.addEventListener('click',function(){var th=button.parentElement,table=th.closest('table'),body=table.tBodies[0],column=Number(button.dataset.intelSort),desc=th.getAttribute('aria-sort')!=='descending';table.querySelectorAll('th').forEach(function(h){h.removeAttribute('aria-sort');});th.setAttribute('aria-sort',desc?'descending':'ascending');Array.from(body.rows).sort(function(a,b){var x=a.cells[column],y=b.cells[column];if(x.dataset.missing==='true')return y.dataset.missing==='true'?0:1;if(y.dataset.missing==='true')return -1;var n=button.dataset.kind==='number';var result=n?Number(x.dataset.sort)-Number(y.dataset.sort):x.textContent.localeCompare(y.textContent);return desc?-result:result;}).forEach(function(row){body.appendChild(row);});});});'''


def money(value):return f'€{value:,.2f}' if value is not None else 'Unavailable'
def percent(value):return f'{value:.2f}%' if value is not None else 'Unavailable'
def text(value):return escape(str(value if value is not None else 'Unavailable'),quote=True)


def table(rows, columns, sortable=False):
    # Columns: prepared field, readable label, format. Never embed raw records.
    if not rows:return '<p class="intel-note">No applicable canonical rows.</p>'
    headers=''.join('<th'+(' class="number"' if fmt in {'money','percent','number'} else '')+'>'+('<button type="button" data-intel-sort="'+str(i)+'" data-kind="'+('number' if fmt in {'money','percent','number'} else 'text')+'">'+text(label)+'</button>' if sortable else text(label))+'</th>' for i,(_,label,fmt) in enumerate(columns))
    body=[]
    for row in rows:
        cells=[]
        for key,label,fmt in columns:
            value=row.get(key);numeric=fmt in {'money','percent','number'}
            display=money(value) if fmt=='money' else percent(value) if fmt=='percent' else f'{value:,.2f}' if fmt=='number' and value is not None else str(value) if value is not None else 'Unavailable'
            attrs=(' class="number"' if numeric else '')+(' data-missing="true"' if value is None else (' data-sort="'+text(value)+'"' if numeric else ''))
            cells.append('<td'+attrs+'>'+text(display)+'</td>')
        body.append('<tr>'+''.join(cells)+'</tr>')
    return '<div class="table-scroll" tabindex="0" role="region" aria-label="'+text(columns[0][1])+' table; scroll for details"><table class="intel-table'+(' intel-wide' if len(columns)>4 else '')+'"><thead><tr>'+headers+'</tr></thead><tbody>'+''.join(body)+'</tbody></table></div>'


CONTRIBUTION_COLUMNS=[('name','Security','text'),('isin','ISIN','text'),('instrument_type','Type','text'),('position_status','Position','text'),('unrealized','Unrealized P/L','money'),('realized','Realized P/L incl. settlements','money'),('net_income','Net dividends / interest','money'),('contribution','Lifetime contribution','money'),('status','Evidence','text')]
UNDERLYING_COLUMNS=[('company','Underlying company','text'),('direct_stock_value','Direct stocks','money'),('confirmed_derivative_value','Confirmed derivatives','money'),('allocated_market_value','Allocated market value','money'),('full_portfolio_share_pct','% of complete tracked value','percent'),('instruments','Exact instrument ISINs','text'),('source','Identity evidence','text'),('status','Coverage','text')]
YEAR_COLUMNS=[('year','Calendar year','text'),('beginning_wealth','Beginning wealth','money'),('net_external_invested','Net external invested','money'),('wealth_gain_loss','Wealth gain/loss','money'),('ending_wealth','Ending wealth','money'),('through','Through','text')]
INCOME_COLUMNS=[('name','Security','text'),('isin','ISIN','text'),('known_net','Eligible forward net estimate','money'),('reason','Coverage / exclusion reason','text')]


def facts(items):
    return '<div class="intel-facts">'+''.join('<div class="intel-fact"><span class="intel-note">'+text(label)+'</span><strong>'+text(value)+'</strong></div>' for label,value in items)+'</div>'


def sections(model,offline=True):
    data=model['intelligence'];a=data['attribution'];c=data['capital'];risk=data['benchmark_risk'];income=data['income'];under=data['underlying']
    profit=facts([('Authoritative full lifetime profit',money(a['authoritative'])),('Component bridge','Reconciled' if a['available'] else 'Incomplete'),('Stock/fund scope',model['returns_scope_label'])])
    profit+=table(a['components'],[('component','Canonical component','text'),('value','EUR','money')])
    profit+='<p class="intel-note">'+text(a['note'])+'</p>'+table(a['groups'],[('group','Historical position group','text'),('securities','Instruments','number'),('contribution','Lifetime contribution','money')])
    profit+='<h3>Largest '+('known ' if a['rankings_partial'] else '')+'lifetime winners and detractors</h3>'
    profit+=table(a['winners']+a['detractors'],[('name','Security','text'),('position_status','Position','text'),('contribution','Lifetime contribution','money')])
    if offline:profit+='<details><summary>All historical security contributions · sortable</summary>'+table(a['rows'],CONTRIBUTION_COLUMNS,True)+'</details>'
    effective=model['composition'].get('effective_holdings')
    diversification=facts([('Active stock/fund instruments',str(model['composition']['active_count'])),('Effective holdings',f'{effective:.2f}' if effective is not None else 'Unavailable'),('Top-five concentration',percent(model['composition']['top_pct']))])
    diversification+='<p class="intel-note">Nominal HHI equal-weight equivalent; not correlation-adjusted independent bets. Complete stock/fund valuation coverage is required. '+text(model['composition']['note'])+'</p><h3>Allocated Market Value by Underlying</h3>'+table(under['rows'],UNDERLYING_COLUMNS,offline)
    diversification+='<p class="intel-note">'+text(under['note'])+' '+text(under['funds_excluded'])+' fund instruments excluded from constituent mapping.</p>'
    diversification+='<details><summary>Unmapped instruments</summary>'+table(under['unmapped'],[('name','Instrument','text'),('isin','ISIN','text'),('reason','Missing evidence','text')])+'</details>'
    deployment='<p class="intel-note">'+text(c['scope'])+' · EUR</p>'+table(c['years'],YEAR_COLUMNS)+'<p class="intel-note">'+text(c['note'])+'</p>'
    deployment+='<details><summary>Distribution and performance bridge details</summary>'+table(c['years'],[('year','Year','text'),('net_dividends','Recognized net dividends','money'),('reinvested_dividends','Reinvested dividends','money'),('net_distributions_outside_sleeve','Net distributions outside holdings','money'),('investment_result_including_dividends','Investment result incl. dividends','money')])+'</details>'
    deployment+='<p class="intel-note">MWR is annualized and depends on dated investment sizes; TWR is cumulative and neutralizes flows. A difference does not establish skill, causal market timing or a timing advantage. No vintage IRRs are inferred.</p>'
    benchmark=facts([('Portfolio max / current drawdown',percent(model['drawdown']['maximum'])+' / '+percent(model['drawdown']['current'])),('Benchmark max / current drawdown',percent(risk['maximum'])+' / '+percent(risk['current']))])
    benchmark+='<p class="intel-note">'+text(model['raw'].get('benchmark_name','No comparison'))+' · '+text(risk['note'])+'</p>'
    if risk['available']:benchmark+='<p class="intel-note">Benchmark current time under water: '+str(risk['underwater_days'])+' calendar days since its last observed peak.</p>'
    earnings=facts([('Eligible-cohort forward net yield',percent(income['eligible_current_yield_pct'])),('Eligible-cohort yield on remaining cost',percent(income['eligible_yield_on_cost_pct'])),('Complete stock/fund forecast yield',percent(income['complete_portfolio_yield_pct']))])
    earnings+='<p class="intel-note">Eligible current holdings: '+str(income['eligible_count'])+'/'+str(income['active_count'])+' · known net numerator '+money(income['known_net'])+' · matching valued assets '+money(income['eligible_value'])+'.</p>'
    earnings+=facts([('Observed net/gross retention',percent(income['observed_net_retention_pct'])),('Matched gross minus net',money(income['observed_gross_minus_net'])),('Separately identified foreign tax subtotal',money(income['separately_identified_foreign_withholding']))])
    earnings+='<p class="intel-note">Matched gross/net receipts: '+str(income['receipt_count'])+'/'+str(income['total_receipt_count'])+'; separately classified foreign-tax rows: '+str(income['foreign_coverage_count'])+'. '+text(income['note'])+'</p>'+table(income['rows'],INCOME_COLUMNS,offline)
    return [('Profit Attribution',profit),('Diversification & Underlying Exposure',diversification),('Capital Deployment',deployment),('Benchmark Risk',benchmark),('Income Intelligence',earnings)]


def intelligence_html(model):
    return '<details class="intelligence"><summary>Portfolio Intelligence · optional</summary><p class="intel-note">Read-only insights from existing canonical outputs. Each subsection states its investment scope; no additional market-data requests.</p>'+''.join('<details><summary>'+text(title)+'</summary>'+body+'</details>' for title,body in sections(model))+'</details>'


def streamlit_intelligence(model):
    import streamlit as st
    st.markdown('### Portfolio Intelligence')
    st.caption('Optional read-only research · uses existing results, with no provider calls. Expand a subsection for its scope and limitations.')
    st.markdown('<style>'+INTELLIGENCE_CSS+'</style>',unsafe_allow_html=True)
    data=model['intelligence']
    for index,(title,body) in enumerate(sections(model,offline=False)):
        with st.expander(title,expanded=index==0):
            # Shared formatted facts/tables are fully escaped. Streamlit's native
            # dataframe below supplies keyboard/sort controls for full histories.
            st.markdown(body,unsafe_allow_html=True)
            rows,columns=(data['attribution']['rows'],CONTRIBUTION_COLUMNS) if index==0 else ([],[])
            if rows:
                st.caption('Sortable canonical detail table')
                st.dataframe(pd.DataFrame([{label:r.get(key) for key,label,fmt in columns} for r in rows]),hide_index=True,use_container_width=True)
