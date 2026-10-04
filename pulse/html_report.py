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
from pulse.charts import wealth_range
from pulse.reporting import HOLDING_CSS, holding_cards, money

HTML_SCHEMA_VERSION = 1
SCRIPT = '''document.querySelectorAll('[data-period]').forEach(function(button){button.addEventListener('click',function(){document.querySelectorAll('[data-period]').forEach(function(other){other.setAttribute('aria-pressed','false');});button.setAttribute('aria-pressed','true');document.getElementById('period-value').textContent=button.dataset.display;document.getElementById('period-dates').textContent=button.dataset.dates;});});'''
STYLE = '''*{box-sizing:border-box}body{margin:0;background:#081727;color:#f6f9fd;font:16px/1.5 system-ui,sans-serif}main{max-width:1300px;margin:auto;padding:36px}h1{font-size:2.5rem;letter-spacing:-1px;margin:0}h2{font-size:1.25rem;margin:26px 0 12px}p{margin:8px 0}.muted,small{color:#91a7bc}.badge{display:inline-block;border:1px solid #24415e;border-radius:20px;padding:5px 12px;margin-top:14px;color:#42cbea}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:24px 0}.metric,.panel{background:#112238;border:1px solid #24415e;border-radius:12px;padding:18px;min-width:0}.metric h2{font-size:.9rem;color:#91a7bc;margin:0 0 12px}.metric-value{font-size:1.8rem;font-weight:750;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.metric small{display:block;margin-top:10px}.metric details{font-size:.85rem;margin-top:12px}.chart-row{display:grid;grid-template-columns:1.2fr 1fr;gap:16px}.chart{width:100%;height:auto;display:block}.period-value{font-size:2.2rem;color:#42cbea;font-weight:700}.period-controls{display:flex;gap:8px;flex-wrap:wrap}button{font:inherit;border:1px solid #24415e;background:#081727;color:#fff;padding:8px 18px;border-radius:8px;cursor:pointer}button[aria-pressed=true]{border-color:#42cbea;background:#19354a}button:focus-visible{outline:2px solid #a8ebbc}.warning{border-left:3px solid #f8bc62;padding:8px 14px;margin:14px 0;background:#112238;overflow-wrap:anywhere}li{margin:8px 0;overflow-wrap:anywhere}.table-scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:.9rem}th,td{text-align:left;padding:12px;border-bottom:1px solid #24415e;overflow-wrap:anywhere}td.number,th.number{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}footer{border-top:1px solid #24415e;margin-top:28px;padding-top:18px;font-size:.85rem}.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:.9rem}.actual{color:#42cbea}.benchmark{color:#397df5}.panel>h2{margin-top:0}@media(max-width:900px){.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.chart-row{grid-template-columns:1fr}main{padding:24px}}@media(max-width:480px){main{padding:16px}.metrics{grid-template-columns:1fr}h1{font-size:2rem}.metric-value{font-size:1.9rem}.panel{padding:14px}}'''+HOLDING_CSS

def text(value):
    return escape(str(value),quote=True)

def day(value):
    parsed=pd.to_datetime(value,errors='coerce')
    return parsed.strftime('%d %b %Y') if pd.notna(parsed) else 'Unavailable'

def svg_text(value,x,y,**attrs):
    attributes=' '.join(f'{key.replace("_","-")}="{text(val)}"' for key,val in attrs.items())
    return f'<text x="{x}" y="{y}" {attributes}>{text(value)}</text>'

def wealth_chart(model):
    nav=model['nav'];series=[]
    if nav.empty:return '<p>Historical wealth unavailable — data requires review.</p>'
    for col,color,label in [('stockfund_value_eur','#42cbea','Actual stock/fund wealth'),('benchmark_pme_value_eur','#397df5',model['raw'].get('benchmark_name','Selected benchmark'))]:
        if col in nav and (col!='benchmark_pme_value_eur' or model['by_key']['benchmark'].value is not None):series.append((list(nav[col]),color,label))
    values=[number(v) for seq,_,_ in series for v in seq if number(v) is not None]
    if not values:return '<p>Historical wealth unavailable — data requires review.</p>'
    low,high=wealth_range(values);w,h=1000,350;gx,gy,gw,gh=88,26,886,265
    svg=['<svg class="chart" role="img" aria-label="Stock/fund wealth and cash-flow-matched benchmark, EUR" viewBox="0 0 1000 350" xmlns="http://www.w3.org/2000/svg">']
    for frac in (0,.25,.5,.75,1):
        y=gy+gh*(1-frac);svg.append(f'<path d="M{gx},{y}H{gx+gw}" stroke="#24415e"/>')
        svg.append(svg_text(f'{low+(high-low)*frac:,.0f}',gx-10,y+4,fill='#91a7bc',font_size=13,text_anchor='end'))
    for j,(seq,color,label) in enumerate(series):
        path=[];connected=False
        for k,value in enumerate(seq):
            value=number(value)
            if value is None:connected=False;continue
            x=gx+gw*k/max(len(seq)-1,1);y=gy+gh*(high-value)/(high-low)
            path.append(f'{"L" if connected else "M"}{x:.3f},{y:.3f}');connected=True
        dash=' stroke-dasharray="8 5"' if j else ''
        svg.append(f'<path d="{" ".join(path)}" fill="none" stroke="{color}" stroke-width="3"{dash}><title>{text(label)}</title></path>')
        last=number(seq[-1])
        if last is not None:
            y=gy+gh*(high-last)/(high-low)
            svg.append(f'<circle cx="{gx+gw}" cy="{y:.3f}" r="4" fill="{color}"><title>{text(label+": "+money(last))}</title></circle>')
    for frac in (0,.25,.5,.75,1):
        index=round((len(nav)-1)*frac);svg.append(svg_text(day(nav.iloc[index]['date']),gx+gw*frac,320,fill='#91a7bc',font_size=13,text_anchor='start' if frac==0 else 'end' if frac==1 else 'middle'))
    svg.append('</svg>')
    legend=''.join('<span style="color:'+color+'">'+text(label)+'</span>' for _,color,label in series)
    endpoints=' · '.join(label+': '+money(seq[-1]) for seq,_,label in series)
    return '<div class="legend">'+legend+'</div>'+''.join(svg)+'<p class="muted">Wealth (EUR) · padded axis'+(' · axis does not start at zero' if low!=0 else '')+'</p><p>'+text(endpoints)+'</p>'

def income_chart(months):
    scale=max([abs(number(m.get('total')) or 0) for m in months] or [1]) or 1
    signed=any((number(m.get('total')) or 0)<0 for m in months);baseline=130 if signed else 235;gh=100 if signed else 205
    svg=['<svg class="chart" role="img" aria-label="Monthly recognized net investment income, EUR" viewBox="0 0 600 300" xmlns="http://www.w3.org/2000/svg">',f'<path d="M45,{baseline}H590" stroke="#24415e"/>']
    for i,m in enumerate(months):
        x=48+i*45;value=number(m.get('total'))
        if value is not None:
            height=abs(value)/scale*gh;y=baseline-height if value>=0 else baseline
            svg.append(f'<rect x="{x}" y="{y:.3f}" width="29" height="{max(height,.6):.3f}" fill="{"#397df5" if m["status"]=="complete" else "none"}" stroke="{"#f8bc62" if m["status"]=="partial" else "#397df5"}"><title>{text(m["label"]+": "+money(value)+" · "+m["status"])}</title></rect>')
        else:svg.append(svg_text('—',x+14,baseline-8,fill='#91a7bc',font_size=14,text_anchor='middle'))
        svg.append(svg_text(m['label'],x+14,264,fill='#91a7bc',font_size=12,text_anchor='middle'))
    svg.extend([svg_text('EUR · hover bars for amounts',48,290,fill='#91a7bc',font_size=13),'</svg>'])
    return ''.join(svg)

def holdings_chart(rows):
    rows=rows[:5]
    if not rows:return '<p>No supported valued holding ranking available.</p>'
    maximum=max(number(r['value']) or 0 for r in rows) or 1
    svg=['<svg class="chart" role="img" aria-label="Five largest valued stock/fund holdings, EUR" viewBox="0 0 600 300" xmlns="http://www.w3.org/2000/svg">']
    for i,r in enumerate(rows):
        y=25+i*51;name=str(r['name']);short=name if len(name)<=35 else name[:32]+'…'
        svg.append(svg_text(short,12,y,fill='#f6f9fd',font_size=13))
        value=number(r['value'])
        if value is not None:svg.append(f'<rect x="12" y="{y+8}" width="{value/maximum*576:.3f}" height="15" rx="3" fill="#42cbea"><title>{text(name+": "+money(value))}</title></rect>')
        svg.append(svg_text(money(value),590,y,fill='#a8ebbc',font_size=13,text_anchor='end'))
    svg.append('</svg>');return ''.join(svg)

def html_report(model):
    """Return session-local UTF-8 bytes, with a new non-personal export token."""
    snap=model['snapshot'];synthetic=bool(model['raw'].get('synthetic'))
    digest=base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
    csp="default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-"+digest+"'; connect-src 'none'; img-src 'none'; font-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
    parts=['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="'+text(csp)+'"><meta name="report-export" content="'+secrets.token_hex(16)+'"><title>FolioLens — Portfolio Report</title><style>'+STYLE+'</style></head><body><main><header><h1>FolioLens</h1><p class="muted">Your investments, in focus.</p><p>'+('SYNTHETIC DEMO · fabricated transactions and market prices' if synthetic else 'PRIVATE FINANCIAL ANALYSIS · keep this report confidential')+'</p><p class="badge">'+text(model['health'])+' · '+text(model['scope_label'])+'</p><p class="muted">Generated '+day(snap['report_date'])+' · Transactions to '+day(snap['latest_transaction_date'])+' · Stock/fund history to '+day(snap['valuation_date'])+'</p></header>']
    parts.append('<p>Analysis: '+text(model['scope_label'])+' · Full portfolio value: '+money(model['full_totals']['value'])+' · Full lifetime profit: '+money(model['full_totals']['profit'])+'</p><section class="metrics" aria-label="Eight headline metrics">')
    for m in model['metrics']:
        value=number(m.value);attr=f' data-value="{value!r}"' if value is not None else ''
        parts.append('<article class="metric" data-metric="'+text(m.key)+'"'+attr+'><h2>'+text(m.label)+'</h2><div class="metric-value">'+text(m.display)+'</div><small>'+text(m.scope)+'</small>'+('<small>'+text(m.status.replace('_',' ').capitalize())+'</small>' if value is None else '')+'<details><summary>What this means</summary><p>'+text(m.explanation)+'</p></details></article>')
    parts.append('</section><section class="panel"><h2>Portfolio versus Benchmark</h2><p class="muted">'+text(model.get('returns_scope_label','Stocks & funds'))+' wealth · EUR · cash-flow-matched benchmark · cash and derivatives excluded</p>'+wealth_chart(model)+'</section>')
    parts.append('<section class="panel" style="margin-top:16px"><h2>Cumulative Period Performance</h2><div class="period-controls">')
    for key in ('1M','3M','YTD','1Y','MAX'):
        r=next((r for r in model['periods'] if r['period_key']==key),None)
        display=f'{key} · {r["portfolio_twr_pct"]:.2f}%' if r else key+' · Unavailable'
        dates=day(r['effective_start_date'])+' to '+day(r['end_date']) if r else 'Reliable observations or accounting coverage unavailable; missing performance is not zero.'
        parts.append('<button type="button" data-period="'+key+'" data-display="'+text(display)+'" data-dates="'+text(dates)+'" aria-pressed="'+('true' if key=='MAX' else 'false')+'">'+key+'</button>')
    maximum=next((r for r in model['periods'] if r['period_key']=='MAX'),None)
    parts.append('</div><p id="period-value" class="period-value">'+(f'MAX · {maximum["portfolio_twr_pct"]:.2f}%' if maximum else 'MAX · Unavailable')+'</p><p id="period-dates" class="muted">'+(day(maximum['effective_start_date'])+' to '+day(maximum['end_date']) if maximum else 'Reliable performance unavailable.')+'</p><p class="muted">Period TWR is cumulative and adjusts for cash flows. MWR is annualized; headline TWR is since inception. Cash and derivatives are excluded.</p></section>')
    parts.append('<div class="chart-row" style="margin-top:16px"><section class="panel"><h2>Investment Income · '+str(snap['year'])+'</h2><p class="muted">Recognized net investment income · dividends + interest</p>'+income_chart(snap['months'])+'<p class="muted">Solid = covered month-end · outline = partial · dash = uncovered, not zero. Reinvested dividend income is recognized once.</p></section><section class="panel"><h2>Largest Holdings</h2><p class="muted">Top 5 · share of valued stock/fund assets · no look-through</p>'+holdings_chart(snap['top_holdings'])+'</section></div><h2>Five largest valued stock/fund holdings</h2>'+holding_cards(snap['top_holdings']))
    parts.append('<details><summary>All current stock/fund holdings</summary><div class="table-scroll"><table><thead><tr><th>Holding</th><th class="number">Quantity</th><th class="number">Value EUR</th><th>Quote date</th><th>Valuation</th></tr></thead><tbody>')
    for r in model['holdings']:
        qty=number(r.get('quantity'))
        parts.append('<tr><td>'+text(r['name'])+'</td><td class="number">'+(f'{qty:g}' if qty is not None else 'Unavailable')+'</td><td class="number">'+money(r.get('value'))+'</td><td>'+day(r.get('quote_date'))+'</td><td>'+text(r['status'])+'</td></tr>')
    parts.append('</tbody></table></div><p class="muted">Closed and derecognized positions are excluded. Unpriced positions are not assigned zero.</p></details><h2>What Stands Out?</h2><ul>'+''.join('<li>'+text(s)+'</li>' for s in model['insights'])+'</ul><h2>Data health and coverage</h2>')
    parts.append('<div class="warning"><ul>'+''.join('<li>'+text(s)+'</li>' for s in model['issues'])+'</ul></div>' if model['issues'] else '<p>No blocking issues in the tracked analytical scope.</p>')
    parts.append('<footer><p>Stocks/funds lifetime profit excludes derivatives and cash interest. Full-portfolio lifetime profit includes net income. Capital committed and recovery cover the full ecosystem; recovery is not withdrawable cash. Missing data remains unavailable.</p><p>Benchmark: '+text(model['raw'].get('benchmark_name','No comparison'))+' · EUR reporting. Comparisons require validated adjusted prices, EUR conversion and matched cash-flow dates. Historical estimates, stale quotes and incomplete inputs are disclosed above.</p><p>Offline report: no provider requests, external scripts, fonts or login are required. This file contains financial information; clearing the application does not delete files you download.</p><p>Unofficial independent analysis. Not affiliated with Trade Republic. Not a tax certificate or investment recommendation.</p></footer></main><script>'+SCRIPT+'</script></body></html>')
    return ''.join(parts).encode('utf-8')
