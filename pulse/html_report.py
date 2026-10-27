"""Self-contained offline report. All portfolio strings are text, never markup/code.

Only explicit display fields are read; raw ledgers, event evidence, captures and
configuration are never embedded. No browser requests or external dependencies.
"""
import base64
import hashlib
from html import escape
import secrets
import pandas as pd
from pulse.adapter import number
from pulse.charts import wealth_range, benchmark_alias, drawdown_legend, BENCHMARK_COLOR
from pulse.design import CSS_TOKENS, KPI_CSS
from pulse.pdf import monthly_amount, monthly_label
from pulse.composition import COMPOSITION_CSS, composition_panel
from pulse.reporting import HOLDING_CSS, holding_cards, money, table_html, HOLDING_EXPLANATION
from pulse.analytics import risk_summary, forward_display
from pulse.intelligence_view import intelligence_html, INTELLIGENCE_CSS, INTELLIGENCE_SCRIPT

HTML_SCHEMA_VERSION = 12
SCRIPT = '''document.querySelectorAll('[data-period]').forEach(function(button){button.addEventListener('click',function(){document.querySelectorAll('[data-period]').forEach(function(other){other.setAttribute('aria-pressed','false');});button.setAttribute('aria-pressed','true');document.getElementById('period-value').textContent=button.dataset.display;document.getElementById('period-dates').textContent=button.dataset.dates;});});'''
SCRIPT += '''document.querySelectorAll('[data-view]').forEach(function(button){button.addEventListener('click',function(){document.querySelectorAll('[data-view]').forEach(function(other){other.setAttribute('aria-pressed',String(other===button));var panel=document.getElementById(other.dataset.view+'-view');panel.hidden=other!==button;panel.setAttribute('aria-hidden',String(other!==button));});});});'''
SCRIPT += INTELLIGENCE_SCRIPT
SCRIPT += '''document.querySelectorAll('[data-capital-toggle]').forEach(function(button){button.addEventListener('click',function(){var show=button.getAttribute('aria-pressed')!=='true';button.setAttribute('aria-pressed',String(show));['wealth-base','wealth-capital'].forEach(function(id,i){var panel=document.getElementById(id),hidden=i===0?show:!show;panel.hidden=hidden;panel.setAttribute('aria-hidden',String(hidden));});});});'''
STYLE = CSS_TOKENS+'''[hidden]{display:none!important}*{box-sizing:border-box}body{margin:0;background:var(--fl-background);color:var(--fl-text);font:16px/1.55 system-ui,sans-serif}main{max-width:1300px;margin:auto;padding:32px}
h1{font-size:2.5rem;letter-spacing:-1px;margin:0}h2{font-size:1.3125rem;letter-spacing:-.2px;margin:28px 0 12px}h3{font-size:1rem;margin:0 0 8px}p{margin:8px 0}.muted,small{color:var(--fl-muted)}
header{display:flex;justify-content:space-between;gap:24px;align-items:start;padding-bottom:16px;border-bottom:1px solid var(--fl-border)}.report-meta{text-align:right;font-size:.85rem}.badge{display:inline-block;border:1px solid var(--fl-border);border-radius:20px;padding:5px 12px;color:var(--fl-actual)}.scope-note{color:var(--fl-muted);font-size:.875rem;margin-top:16px;overflow-wrap:anywhere}
.panel{background:var(--fl-surface);border:1px solid var(--fl-border);border-radius:12px;padding:24px;min-width:0;margin-top:24px}.panel>h2{margin-top:0}.chart{width:100%;height:auto;display:block}.legend{display:flex;gap:20px;flex-wrap:wrap;font-size:.9rem;margin:16px 0 4px}.legend span{display:inline-flex;align-items:center;gap:8px}.legend-line{width:26px;border-top:3px solid var(--fl-actual)}.legend-line.benchmark{border-top:2px dashed var(--fl-benchmark)}
.endpoint-row{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;font-variant-numeric:tabular-nums;font-size:.9rem}.comparison-details{font-size:.875rem;margin-top:14px}.period-component{border-top:1px solid var(--fl-border);margin-top:20px;padding-top:20px}.period-top{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.period-value{font-size:2rem;color:var(--fl-actual);font-weight:750;font-variant-numeric:tabular-nums}.period-controls{display:flex;gap:8px;flex-wrap:wrap}
button{font:inherit;border:1px solid var(--fl-border);background:var(--fl-background);color:var(--fl-text);padding:8px 16px;border-radius:8px;cursor:pointer}button[aria-pressed=true]{border-color:var(--fl-actual);background:#19354a}button:focus-visible,summary:focus-visible{outline:2px solid var(--fl-mint);outline-offset:3px}
.income-layout{display:grid;grid-template-columns:minmax(0,2fr) minmax(220px,1fr);gap:24px;align-items:center}.income-layout .chart{max-height:320px}.income-facts{border-left:1px solid var(--fl-border);padding-left:24px}.income-number{font-size:1.625rem;color:var(--fl-mint);font-weight:700;font-variant-numeric:tabular-nums}.income-facts p{margin-bottom:16px}
.warning{border-left:3px solid var(--fl-warning);padding:8px 16px;margin:14px 0;background:var(--fl-surface);overflow-wrap:anywhere}.observations{border-top:1px solid var(--fl-border);padding-top:8px;margin-top:28px}li{margin:10px 0;overflow-wrap:anywhere}summary{cursor:pointer;color:var(--fl-muted)}.table-scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:.9rem}th,td{text-align:left;padding:12px;border-bottom:1px solid var(--fl-border);overflow-wrap:anywhere}td.number,th.number{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
footer{border-top:1px solid var(--fl-border);margin-top:32px;padding-top:20px;font-size:.875rem;display:grid;grid-template-columns:1fr 1fr;gap:24px}.disclaimer{grid-column:1/-1}.holding-description{font-size:.875rem;color:var(--fl-muted)}
@media(max-width:900px){main{padding:24px}.income-layout{grid-template-columns:1fr}.income-facts{border-left:0;border-top:1px solid var(--fl-border);padding:16px 0 0;display:grid;grid-template-columns:1fr 1fr;gap:16px}.income-facts .income-note{grid-column:1/-1}}
@media(max-width:560px){main{padding:16px}header{display:block}.report-meta{text-align:left;margin-top:18px}h1{font-size:2rem}.panel{padding:18px}footer{grid-template-columns:1fr}.income-facts{grid-template-columns:1fr}.period-controls{gap:6px}button{padding:8px 12px}.wealth-chart text{font-size:20px}.wealth-chart .minor-tick{display:none}}
'''+KPI_CSS+HOLDING_CSS+COMPOSITION_CSS+'''.monthly-values{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;font-size:.875rem;font-variant-numeric:tabular-nums;margin-top:8px}.monthly-values span{overflow-wrap:anywhere}.monthly-values strong{color:#f6f9fd}@media(max-width:560px){.monthly-values{grid-template-columns:repeat(2,minmax(0,1fr))}}'''

def text(value):
    return escape(str(value),quote=True)

def day(value):
    parsed=pd.to_datetime(value,errors='coerce')
    return parsed.strftime('%d %b %Y') if pd.notna(parsed) else 'Unavailable'

def svg_text(value,x,y,**attrs):
    attributes=' '.join(f'{key.rstrip("_").replace("_","-")}="{text(val)}"' for key,val in attrs.items())
    return f'<text x="{x}" y="{y}" {attributes}>{text(value)}</text>'

STYLE+=INTELLIGENCE_CSS


def wealth_chart(model,include_capital=False):
    nav=model['nav'];series=[]
    if nav.empty:return '<p>Historical wealth unavailable — data requires review.</p>'
    for col,color,label in [('stockfund_value_eur','#42cbea','Actual stock/fund wealth'),('benchmark_pme_value_eur',BENCHMARK_COLOR,benchmark_alias(model))]:
        if col in nav and (col!='benchmark_pme_value_eur' or model['by_key']['benchmark'].value is not None):series.append((list(nav[col]),color,label))
    if include_capital and model['intelligence']['capital']['available']:
        series.append(([r['capital_eur'] for r in model['intelligence']['capital']['series']],'#91a7bc','Cumulative net stock/fund capital invested'))
    values=[number(v) for seq,_,_ in series for v in seq if number(v) is not None]
    if not values:return '<p>Historical wealth unavailable — data requires review.</p>'
    low,high=wealth_range(values);w,h=600,260;gx,gy,gw,gh=90,20,488,190
    svg=['<svg class="chart wealth-chart" role="img" aria-label="Stock/fund wealth and cash-flow-matched benchmark, EUR" viewBox="0 0 600 260" xmlns="http://www.w3.org/2000/svg">']
    for frac in (0,.25,.5,.75,1):
        y=gy+gh*(1-frac);svg.append(f'<path d="M{gx},{y}H{gx+gw}" stroke="#24415e"/>')
        svg.append(svg_text(f'{low+(high-low)*frac:,.0f}',gx-10,y+4,fill='#91a7bc',font_size=8,text_anchor='end'))
    for j,(seq,color,label) in enumerate(series):
        path=[];connected=False
        for k,value in enumerate(seq):
            value=number(value)
            if value is None:connected=False;continue
            x=gx+gw*k/max(len(seq)-1,1);y=gy+gh*(high-value)/(high-low)
            path.append(f'{"L" if connected else "M"}{x:.3f},{y:.3f}');connected=True
        dash=' stroke-dasharray="8 5"' if j else ''
        svg.append(f'<path d="{" ".join(path)}" fill="none" stroke="{color}" stroke-width="{3 if j==0 else 2.3}"{dash}><title>{text(label)}</title></path>')
        last=number(seq[-1])
        if last is not None:
            y=gy+gh*(high-last)/(high-low)
            svg.append(f'<circle cx="{gx+gw}" cy="{y:.3f}" r="4" fill="{color}"><title>{text(label+": "+money(last))}</title></circle>')
    for frac in (0,.25,.5,.75,1):
        index=round((len(nav)-1)*frac);svg.append(svg_text(day(nav.iloc[index]['date']),gx+gw*frac,238,fill='#91a7bc',font_size=8,class_='minor-tick' if frac in (.25,.75) else 'major-tick',text_anchor='start' if frac==0 else 'end' if frac==1 else 'middle'))
    svg.append('</svg>')
    legend=''.join('<span><i class="legend-line'+(' benchmark' if i else '')+'" style="border-color:'+color+'" aria-hidden="true"></i>'+text(label)+'</span>' for i,(_,color,label) in enumerate(series))
    endpoints=''.join('<span>'+text(label+': '+money(seq[-1]))+'</span>' for seq,_,label in series)
    return '<div class="legend">'+legend+'</div>'+''.join(svg)+'<p class="muted">Wealth (EUR) · padded axis'+(' · axis does not start at zero' if low!=0 else ' · axis starts at zero')+'</p><div class="endpoint-row">'+endpoints+'</div><details class="comparison-details"><summary>Benchmark identity and comparison</summary><p>'+text(model['raw'].get('benchmark_name','No comparison'))+'</p><p>Exact selected instrument · EUR conversion · cash-flow-matched benchmark MWR. '+text(model['by_key']['benchmark'].status.replace('_',' ').capitalize())+'.</p></details>'

def wealth_views(model):
    base=wealth_chart(model);capital=model['intelligence']['capital']
    if not capital['available']:return base
    return '<button type="button" data-capital-toggle aria-pressed="false" aria-controls="wealth-base wealth-capital">Cumulative net stock/fund capital invested</button><div id="wealth-base" aria-hidden="false">'+base+'</div><div id="wealth-capital" hidden aria-hidden="true">'+wealth_chart(model,True)+'<p class="muted">'+text(capital['note'])+'</p></div>'


def drawdown_chart(model):
    risk=model['drawdown']
    if not risk['available']:return '<p>'+text(risk['note'])+'</p>'
    comparison=model['intelligence']['benchmark_risk']
    rows=risk['series'];low=min(-1,risk['maximum']*1.15,(comparison['maximum'] or 0)*1.15);gx,gy,gw,gh=90,25,488,180
    svg=['<svg class="chart wealth-chart drawdown-chart" role="img" aria-label="Cash-flow-adjusted stock/fund TWR drawdown, percent" viewBox="0 0 600 260" xmlns="http://www.w3.org/2000/svg">']
    for fraction in (0,.25,.5,.75,1):
        y=gy+gh*fraction;value=0 if fraction==0 else low*fraction
        svg.append(f'<path d="M{gx},{y}H{gx+gw}" stroke="#24415e"/>')
        svg.append(svg_text(f'{value:.1f}%',gx-10,y+4,fill='#91a7bc',font_size=10,text_anchor='end'))
    points=[(gx+gw*i/(len(rows)-1),gy+gh*r['drawdown_pct']/low) for i,r in enumerate(rows)]
    path=' '.join(f'{"M" if i==0 else "L"}{x:.3f},{y:.3f}' for i,(x,y) in enumerate(points))
    svg.append(f'<path d="{path} L{gx+gw},{gy} L{gx},{gy} Z" fill="#42cbea" fill-opacity=".11"/>')
    svg.append(f'<path d="{path}" fill="none" stroke="#42cbea" stroke-width="2.5"/>')
    if comparison['available']:
        benchmark=' '.join(f'{"M" if i==0 else "L"}{gx+gw*i/(len(rows)-1):.3f},{gy+gh*r["benchmark_drawdown_pct"]/low:.3f}' for i,r in enumerate(comparison['series']))
        svg.append(f'<path d="{benchmark}" fill="none" stroke="{BENCHMARK_COLOR}" stroke-width="2" stroke-dasharray="8 5"><title>Benchmark EUR adjusted-return drawdown</title></path>')
    svg.append(f'<path d="M{gx},{gy}H{gx+gw}" stroke="#91a7bc"/>')
    for i,((x,y),row) in enumerate(zip(points,rows)):
        caption=day(row['date'])+f": portfolio {row['drawdown_pct']:.2f}%"
        if comparison['available']:
            value=comparison['series'][i]['benchmark_drawdown_pct'];by=gy+gh*value/low
            caption+=f"; benchmark {value:.2f}%"
            svg.append(f'<circle cx="{x:.3f}" cy="{by:.3f}" r="3" fill="transparent"><title>'+text(caption)+'</title></circle>')
        svg.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="3" fill="transparent"><title>'+text(caption)+'</title></circle>')
    for fraction in (0,.5,1):
        row=rows[round((len(rows)-1)*fraction)]
        svg.append(svg_text(day(row['date']),gx+gw*fraction,238,fill='#91a7bc',font_size=10,text_anchor='start' if fraction==0 else 'end' if fraction==1 else 'middle'))
    benchmark_summary=(f"Dashed gold · benchmark max: {comparison['maximum']:.2f}% | current: {comparison['current']:.2f}%. " if comparison['available'] else '')+comparison['note']
    return drawdown_legend(model)+''.join(svg)+'</svg><p>'+text(risk_summary(risk))+'</p><p class="muted">Latest observation: '+day(risk['date'])+' · '+text(risk['scope'])+'</p><p class="muted">'+text(risk['note'])+'</p><p class="muted">'+text(benchmark_summary)+'</p>'


def income_chart(months):
    scale=max([abs(monthly_amount(m) or 0) for m in months] or [1]) or 1
    signed=any((monthly_amount(m) or 0)<0 for m in months);baseline=130 if signed else 235;gh=100 if signed else 205
    svg=['<svg class="chart" role="img" aria-label="Monthly recognized net investment income, EUR" viewBox="0 0 600 300" xmlns="http://www.w3.org/2000/svg">',f'<path d="M45,{baseline}H590" stroke="#24415e"/>']
    for i,m in enumerate(months):
        x=48+i*45;value=monthly_amount(m)
        if value is not None:
            height=abs(value)/scale*gh;y=baseline-height if value>=0 else baseline
            svg.append(f'<rect x="{x}" y="{y:.3f}" width="29" height="{max(height,.6):.3f}" fill="{"#397df5" if m["status"]=="complete" else "none"}" stroke="{"#f8bc62" if m["status"]=="partial" else "#397df5"}"><title>{text(m["label"]+": "+money(value)+" · "+m["status"])}</title></rect>')
        else:svg.append(svg_text('—',x+14,baseline-8,fill='#91a7bc',font_size=14,text_anchor='middle'))
        svg.append(svg_text(m['label'],x+14,264,fill='#91a7bc',font_size=12,text_anchor='middle'))
    svg.extend([svg_text('EUR · hover bars for amounts',48,290,fill='#91a7bc',font_size=13),'</svg>'])
    key='<div class="monthly-values" aria-label="Exact monthly income in EUR">'+''.join('<span>'+text(m['label'])+': <strong>'+text(monthly_label(m))+'</strong></span>' for m in months)+'</div><p class="muted">EUR · * partial coverage · —/dash means uncovered, not zero.</p>'
    return ''.join(svg)+key

def evidence_details(model):
    from pulse.evidence import history_summary
    history=model['history_evidence']
    parts=['<details class="evidence-details"><summary>Historical pricing evidence</summary><p>'+text(history_summary(history))+'</p><p class="muted">'+text(history['note'])+'</p>']
    columns=[('security_name','Holding'),('yahoo_ticker','Verified ticker'),('history_source','History source'),('externally_observed_market_price_observations','Observed dates'),('external_market_effective_observations','Market or carried dates'),('transaction_or_filled_fallback_observations','Fallback dates'),('unresolved_observations','Unresolved dates')]
    if history['rows']:
        parts.append('<div class="table-scroll"><table><thead><tr>'+''.join('<th>'+text(label)+'</th>' for _,label in columns)+'</tr></thead><tbody>')
        for row in history['rows']:parts.append('<tr>'+''.join('<td>'+text(row.get(key) if row.get(key) is not None else 'Unavailable')+'</td>' for key,_ in columns)+'</tr>')
        parts.append('</tbody></table></div>')
    parts.append('</details><details class="evidence-details"><summary>Forward dividend coverage details</summary><p class="muted">Known estimates for active stocks/funds; recently closed earned entitlements may also affect the canonical total. Not recognized income or interest. Unknown net estimates are unavailable, not zero.</p>')
    if model['dividend_evidence']:
        parts.append('<div class="table-scroll"><table><thead><tr><th>Holding</th><th>Verified ticker</th><th>Distribution status</th><th>Known net estimate</th><th>Evidence and limitation</th></tr></thead><tbody>')
        for row in model['dividend_evidence']:
            parts.append('<tr><td>'+text(row['name'])+'</td><td>'+text(row['ticker'])+'</td><td>'+text(row['status'])+'</td><td class="number">'+money(row['known_net_estimate'])+'</td><td>'+text(row['reason'])+'</td></tr>')
        parts.append('</tbody></table></div>')
    else:parts.append('<p>No eligible active stock/fund distribution evidence is available.</p>')
    return ''.join(parts)+'</details>'


def html_report(model):
    """Return session-local UTF-8 bytes, with a new non-personal export token."""
    snap=model['snapshot'];synthetic=bool(model['raw'].get('synthetic'))
    digest=base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
    csp="default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-"+digest+"'; connect-src 'none'; img-src 'none'; font-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
    parts=['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="'+text(csp)+'"><meta name="report-export" content="'+secrets.token_hex(16)+'"><title>FolioLens — Portfolio Report</title><style>'+STYLE+'.manual-derivatives td,.manual-derivatives th{white-space:nowrap}.manual-derivatives td:first-child{white-space:normal;min-width:16rem}</style></head><body><main><header><div><h1>FolioLens</h1><p class="muted">Your investments, in focus.</p><p class="muted">'+('SYNTHETIC DEMO · fabricated transactions and market prices' if synthetic else 'PRIVATE FINANCIAL ANALYSIS · keep this report confidential')+'</p></div><div class="report-meta"><p class="badge">'+text(model['report_status']['headline'])+'</p><p class="muted">Generated '+day(snap['report_date'])+'<br>Transactions to '+day(snap['latest_transaction_date'])+'<br>Stock/fund history to '+day(snap['valuation_date'])+'</p></div></header>']
    parts.append('<p class="scope-note">Analysis: '+text(model['scope_label'])+' · Full portfolio value: '+money(model['full_totals']['value'])+' · Full lifetime profit: '+money(model['full_totals']['profit'])+'</p><section class="metrics" aria-label="Eight headline metrics">')
    for index,m in enumerate(model['metrics']):
        if index==4:parts.append('</section><section class="metrics metrics-secondary" aria-label="Performance and income metrics">')
        value=number(m.value);attr=f' data-value="{value!r}"' if value is not None else ''
        parts.append('<article class="metric" data-metric="'+text(m.key)+'"'+attr+'><h2>'+text(m.label)+'</h2><div class="metric-value">'+text(m.display)+'</div><small>'+text(m.scope)+'</small>'+('<small>'+text(m.status.replace('_',' ').capitalize())+'</small>' if value is None else '')+'<details><summary>What this means</summary><p>'+text(m.explanation)+'</p></details></article>')
    parts.append('</section><section class="panel"><h2>Portfolio versus Benchmark</h2><p class="muted">'+text(model.get('returns_scope_label','Stocks & funds'))+' · cash and derivatives excluded</p><div class="period-controls" role="group" aria-label="Historical chart view"><button type="button" data-view="wealth" aria-controls="wealth-view" aria-pressed="true">Wealth (EUR)</button><button type="button" data-view="drawdown" aria-controls="drawdown-view" aria-pressed="false">Drawdown (%)</button></div><div id="wealth-view" aria-hidden="false">'+wealth_views(model)+'</div><div id="drawdown-view" hidden aria-hidden="true">'+drawdown_chart(model)+'</div>')
    parts.append('<div class="period-component"><div class="period-top"><h2>Cumulative Period Performance</h2><div class="period-controls">')
    for key in ('1M','3M','YTD','1Y','MAX'):
        r=next((r for r in model['periods'] if r['period_key']==key),None)
        display=f'{key} · {r["portfolio_twr_pct"]:.2f}%' if r else key+' · Unavailable'
        dates=day(r['effective_start_date'])+' to '+day(r['end_date']) if r else 'Reliable observations or accounting coverage unavailable; missing performance is not zero.'
        parts.append('<button type="button" data-period="'+key+'" data-display="'+text(display)+'" data-dates="'+text(dates)+'" aria-pressed="'+('true' if key=='MAX' else 'false')+'">'+key+'</button>')
    maximum=next((r for r in model['periods'] if r['period_key']=='MAX'),None)
    parts.append('</div></div><p id="period-value" class="period-value">'+(f'MAX · {maximum["portfolio_twr_pct"]:.2f}%' if maximum else 'MAX · Unavailable')+'</p><p id="period-dates" class="muted">'+(day(maximum['effective_start_date'])+' to '+day(maximum['end_date']) if maximum else 'Reliable performance unavailable.')+'</p><p class="muted">Period TWR is cumulative and adjusts for cash flows. MWR is annualized; headline TWR is since inception. Cash and derivatives are excluded. Period controls change TWR only; the wealth chart retains its full available history.</p></div></section>')
    parts.append('<section class="panel"><h2>Investment Income · '+str(snap['year'])+'</h2><p class="muted">Recognized net investment income · dividends + interest</p><div class="income-layout"><div>'+income_chart(snap['months'])+'</div><aside class="income-facts"><p><span class="muted">Net dividends YTD</span><br><span class="income-number">'+money(snap['ytd_dividends'])+'</span></p><p><span class="muted">Net interest YTD</span><br><span class="income-number">'+money(snap['ytd_interest'])+'</span></p><p><span class="muted">'+text(model['forward_dividends']['label'])+'</span><br><span class="income-number">'+text(forward_display(model['forward_dividends']))+'</span><br><small>'+text(model['forward_dividends']['status'])+'</small></p><p class="muted income-note">'+text(model['forward_dividends']['note'])+'</p><p class="muted income-note">Solid = covered month-end · outline = partial · dash = uncovered, not zero. Reinvested dividend income is recognized once.</p></aside></div></section><section class="panel"><h2>Portfolio Composition</h2>'+composition_panel(model['composition'])+' </section><h2>Five largest valued stock/fund holdings</h2><p class="holding-description">Weights are shares of valued stock/fund assets, without look-through. Thin bars show size relative to the largest holding; use the percentages for allocation. '+text(HOLDING_EXPLANATION)+'</p>'+holding_cards(snap['top_holdings']))
    parts.append('<details><summary>See Top 10 holdings</summary>'+table_html(snap['top_holdings'])+'</details><details><summary>All current stock/fund holdings</summary>'+table_html(model['holdings'],True)+'</details><p class="muted">Closed and derecognized positions are excluded. Unpriced positions are not assigned zero. Performance coverage qualifies each open-position return.</p>'+intelligence_html(model)+'<section class="observations"><h2>What Stands Out?</h2><ul>'+''.join('<li>'+text(s)+'</li>' for s in model['insights'])+'</ul></section><h2>Data health and coverage</h2>')

    parts.append('<div class="warning"><ul>'+''.join('<li>'+text(s)+'</li>' for s in model['issues'])+'</ul></div>' if model['issues'] else '<p>No blocking issues in the tracked analytical scope.</p>')
    parts.append('<p class="scope-note">'+text(model['report_status']['quality'])+' · '+text(model['report_status']['accounting'])+'<br>'+text(model['valuation_summary'])+'</p>')
    coverage=model['manual_derivatives']
    if coverage['active']:
        from pulse.derivative_display import manual_summary,table_rows
        rows=table_rows(coverage);columns=list(rows[0])
        parts.append('<details class="manual-derivatives"><summary>Derivative positions and manual valuations</summary><p>'+text(manual_summary(coverage))+'</p><p class="muted">Confirmed dated EUR-per-unit inputs, not a live price feed. Missing/expired evidence is not zero. Source and observation dates apply to each position.</p><div class="table-scroll" tabindex="0" role="region" aria-label="Derivative positions; scroll for valuation details"><table><thead><tr>'+''.join('<th>'+text(k)+'</th>' for k in columns)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+text(row[k])+'</td>' for k in columns)+'</tr>' for row in rows)+'</tbody></table></div></details>')
    parts.append(evidence_details(model))
    parts.append('<footer><div><h3>Financial scope</h3><p>Stocks/funds lifetime profit excludes derivatives and cash interest. Full-portfolio lifetime profit includes net income. Capital committed and recovery cover the full ecosystem; recovery is not withdrawable cash. Missing data remains unavailable.</p></div><div><h3>Benchmark and sources</h3><p>Benchmark: '+text(model['raw'].get('benchmark_name','No comparison'))+' · EUR reporting. Comparisons require validated adjusted prices, EUR conversion and matched cash-flow dates. Historical estimates, stale quotes and incomplete inputs are disclosed above.</p></div><div class="disclaimer"><p>Offline report: no provider requests, external scripts, fonts or login are required. This file contains financial information; clearing the application does not delete files you download.</p><p>Unofficial independent analysis. Not affiliated with Trade Republic. Not a tax certificate or investment recommendation.</p></div></footer></main><script>'+SCRIPT+'</script></body></html>')
    return ''.join(parts).encode('utf-8')
