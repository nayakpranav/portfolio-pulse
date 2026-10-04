"""Pulse composition using the validated V6.7.8 vector primitives and data builder."""
from io import BytesIO
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.lib.colors import HexColor
from pulse import core
from pulse.charts import wealth_range, benchmark_alias
from pulse.design import COLORS
from pulse.scopes import blocker_message
from pulse.reporting import warning_summary, MINT
from investment_snapshot_pdf import (_panel,_label,_truncate,_holding_name_lines,_kpi_icon,
    BG,WHITE,MUTED,CYAN,BLUE,GREEN,AMBER,GRID)

# Shared role-based palette; retain canonical vector/data primitives.
BG,WHITE,MUTED,CYAN,BLUE,GREEN,AMBER,GRID = [HexColor(COLORS[k]) for k in
    ('background','text','muted','actual','benchmark','mint','warning','border')]

def panel(c,x,y,width,height):
    c.setFillColor(HexColor(COLORS['surface']));c.setStrokeColor(GRID)
    c.setLineWidth(.4);c.roundRect(x,y,width,height,7,fill=1,stroke=1)

PDF_SCHEMA_VERSION = 7

def ascii_text(value):
    return str(value).replace('€','EUR ').replace('—','-').replace('–','-').encode('latin-1','replace').decode('latin-1')

def fit_size(value,width,preferred=16,font='Helvetica-Bold'):
    return min(preferred,preferred*width/max(stringWidth(value,font,preferred),1))

def lines(value,width,size=8):
    """Wrap footer prose within the page, including unusually long input words."""
    result=[];current=''
    for word in ascii_text(value).split():
        if current and stringWidth(current+' '+word,'Helvetica',size)>width:
            result.append(current);current=''
        while stringWidth(word,'Helvetica',size)>width:
            cut=len(word)
            while stringWidth(word[:cut],'Helvetica',size)>width:cut-=1
            result.append(word[:cut]);word=word[cut:]
        current=(current+' '+word).strip()
    if current:result.append(current)
    return result

def monthly_amount(month):
    from pulse.adapter import number
    return number(month.get('total')) if month.get('status') in {'complete','partial'} else None

def monthly_label(month):
    value=monthly_amount(month)
    return f'{value:,.2f}'+('*' if month.get('status')=='partial' else '') if value is not None else '-'

def income_label_layout(months,width):
    # Exact two-decimal labels only when all neighboring cells can hold them
    # at a readable size. Otherwise associate values with full month names.
    if all(stringWidth(monthly_label(m),'Helvetica',7)<=width/12-2 for m in months):return 'bars',12
    columns=3 if all(stringWidth(m['label']+': '+monthly_label(m),'Helvetica',7.5)<=width/3-8 for m in months) else 2
    return 'key',columns

def summary_pdf(model):
    data = model['snapshot']
    output = BytesIO()
    c = canvas.Canvas(output,pagesize=landscape(A4),pageCompression=1)
    c.setTitle('FolioLens | Portfolio Summary')
    c.setAuthor('FolioLens')
    c.setSubject(ascii_text('Private analytical summary; unofficial and not a tax statement. Benchmark: '+model['raw'].get('benchmark_name','No comparison')))
    w,h = landscape(A4)
    c.setFillColor(BG); c.rect(0,0,w,h,fill=1,stroke=0)
    _label(c,'FolioLens',28,h-37,25,WHITE,'Helvetica-Bold')
    _label(c,'Your investments, in focus.',29,h-53,9,MUTED)
    _label(c,f"{'SYNTHETIC DEMO | ' if model['raw']['synthetic'] else 'PRIVATE FINANCIAL ANALYSIS | '}{model['health'].upper()}",w-28,h-28,8,CYAN,align='right')
    tx = data['latest_transaction_date']; vd = data['valuation_date']
    _label(c,f"Generated {data['report_date']:%d %b %Y} | Transactions to {tx:%d %b %Y}" if tx else 'Transaction cutoff unavailable',w-28,h-44,7,MUTED,align='right')
    _label(c,f'Stock/fund history to {vd:%d %b %Y}' if vd else 'Historical valuation unavailable',w-28,h-57,7,MUTED,align='right')
    full=model['full_totals']
    value_text=f"EUR {full['value']:,.2f}" if full['value'] is not None else 'Unavailable'
    profit_text=f"EUR {full['profit']:,.2f}" if full['profit'] is not None else 'Unavailable'
    _label(c,f"Analysis: {model['scope_label']} | Full portfolio value: {value_text} | Full lifetime profit: {profit_text}",29,h-68,8,MUTED)
    card_w = (w-74)/4
    for i,metric in enumerate(model['metrics'][:4]):
        x = 28+i*(card_w+6); y=h-143
        panel(c,x,y,card_w,69)
        _label(c,metric.label.upper(),x+10,y+53,6.5,MUTED,'Helvetica-Bold')
        value=ascii_text(metric.display)
        _label(c,value,x+10,y+28,fit_size(value,card_w-20,21 if metric.value is not None else 12),WHITE,'Helvetica-Bold')
        _label(c,_truncate(ascii_text(metric.scope),card_w-20,6),x+10,y+10,6,MUTED)
    for i,metric in enumerate(model['metrics'][4:]):
        x=28+i*(card_w+6)
        _label(c,metric.label.upper(),x+2,h-165,6.7,MUTED,'Helvetica-Bold')
        _label(c,ascii_text(metric.display),x+2,h-187,16,GREEN if metric.value is not None else AMBER,'Helvetica-Bold')
        _label(c,'Cumulative' if metric.key=='twr' else f"{data['year']} recognized net investment income" if metric.key=='income' else 'Annualized',x+2,h-202,6 if metric.key=='income' else 7,MUTED)
        if metric.key=='benchmark':
            _label(c,_truncate(ascii_text(benchmark_alias(model)),card_w-4,7),x+2,h-213,7,MUTED)
    # Main matched-wealth panel, retaining gaps rather than drawing through missing values.
    px,py,pw,ph=28,220,478,149
    panel(c,px,py,pw,ph)
    _label(c,'UNAFFECTED STOCK/FUND WEALTH (PARTIAL)' if model.get('excluded_securities') else 'STOCK/FUND WEALTH VS MATCHED BENCHMARK',px+12,py+ph-20,9,WHITE,'Helvetica-Bold')
    estimated=model['raw']['historical_metrics'].get('historical_analytics_status')=='OK_WITH_LOW_CONFIDENCE_FALLBACK'
    _label(c,'Cash and derivatives excluded | EUR'+(' | History includes price estimates' if estimated else ''),px+12,py+ph-35,7,MUTED)
    nav=model['nav']
    series=[]
    for column,color,label in [('stockfund_value_eur',CYAN,'Actual'),('benchmark_pme_value_eur',BLUE,ascii_text(benchmark_alias(model)))]:
        if column in nav and (column!='benchmark_pme_value_eur' or model['by_key']['benchmark'].value is not None):
            series.append((list(nav[column]),color,label))
    values=[float(v) for seq,_,_ in series for v in seq if v is not None and v==v]
    if values:
        low,high=wealth_range(values)
        gx,gy,gw,gh=px+41,py+37,pw-58,ph-87
        for frac in (0,.5,1):
            y=gy+gh*frac;c.setStrokeColor(GRID);c.setLineWidth(.3);c.line(gx,y,gx+gw,y)
            _label(c,f'{low+(high-low)*frac:,.0f}',gx-5,y-2,6,MUTED,align='right')
        for j,(seq,color,label) in enumerate(series):
            c.setStrokeColor(color);c.setLineWidth(2.2 if j==0 else 1.6);c.setDash([] if j==0 else [5,3]);path=None
            for k,v in enumerate(seq):
                if v is None or v!=v:
                    if path:c.drawPath(path,stroke=1,fill=0)
                    path=None;continue
                point=(gx+gw*k/max(len(seq)-1,1),gy+gh*(float(v)-low)/(high-low))
                if path:path.lineTo(*point)
                else:
                    path=c.beginPath();path.moveTo(*point)
            if path:c.drawPath(path,stroke=1,fill=0)
            c.line(px+12+j*110,py+14,px+32+j*110,py+14)
            _label(c,_truncate(label,265 if j else 100,7),px+38+j*110,py+12,7,color)
            c.setDash([])
        _label(c,'Wealth (EUR) | Axis does not start at zero' if low != 0 else 'Wealth (EUR)',px+12,py+ph-46,6,MUTED)
        if not nav.empty:
            dates=__import__('pandas').to_datetime(nav['date'])
            _label(c,f'{dates.iloc[0]:%b %Y}',gx,gy-11,6,MUTED)
            _label(c,f'{dates.iloc[-1]:%b %Y}',gx+gw,gy-11,6,MUTED,align='right')
            endpoints=[f"{label if j==0 else 'Benchmark'}: EUR {float(seq[-1]):,.2f}" for j,(seq,_,label) in enumerate(series) if seq and seq[-1] is not None and seq[-1]==seq[-1]]
            _label(c,' | '.join(endpoints),px+pw-12,py+ph-46,5.5,MUTED,align='right')
    else:
        _label(c,'Unavailable - data requires review',px+30,py+78,10,AMBER)
    # Canonical monthly amounts and status markers; no forecast or uncovered zeros.
    ix,iy,iw,ih=514,220,w-542,149
    panel(c,ix,iy,iw,ih)
    _label(c,f"NET INVESTMENT INCOME | {data['year']}",ix+12,iy+ih-20,9,WHITE,'Helvetica-Bold')
    _label(c,'Recognized net income; outline = partial',ix+12,iy+ih-35,6.8,MUTED)
    months=data['months'];amounts=[monthly_amount(m) for m in months]
    scale=max([abs(v) for v in amounts if v is not None] or [1]) or 1
    gx,gw=ix+15,iw-30
    mode,columns=income_label_layout(months,gw)
    signed=any(v is not None and v<0 for v in amounts)
    gy,gh=(iy+45,50) if mode=='bars' else (iy+70,35) if columns==3 else (iy+86,20)
    baseline=gy+gh/2 if signed else gy
    c.setStrokeColor(GRID);c.setLineWidth(.4);c.line(gx,baseline,gx+gw,baseline)
    for j,(m,val) in enumerate(zip(months,amounts)):
        x=gx+j*gw/12
        if val is not None:
            height=abs(val)/scale*gh*(.5 if signed else 1)
            c.setFillColor(BLUE);c.setStrokeColor(AMBER if m['status']=='partial' else BLUE)
            c.rect(x+2,baseline if val>=0 else baseline-height,gw/12-4,max(height,.6),stroke=1,fill=m['status']=='complete')
            if mode=='bars':
                _label(c,monthly_label(m),x+gw/24,baseline+height+4 if val>=0 else baseline-height-9,7,WHITE,align='center')
        elif mode=='bars':_label(c,'-',x+gw/24,baseline+4,7,MUTED,align='center')
        _label(c,m['label'][0],x+gw/24,gy-19 if mode=='bars' else gy-10,6,MUTED,align='center')
    if mode=='key':
        for j,m in enumerate(months):
            row,col=divmod(j,columns);label=m['label']+': '+monthly_label(m)
            _label(c,label,gx+col*gw/columns,iy+(46 if columns==3 else 66)-row*10,7.5,WHITE if monthly_amount(m) is not None else MUTED)
    _label(c,'EUR | * partial / outline | - uncovered, not zero',ix+14,iy+3,6.5,MUTED)
    # Compact holdings strip with the canonical valued-stock/fund denominator.
    _label(c,'LARGEST VALUED STOCK/FUND HOLDINGS',28,204,9,WHITE,'Helvetica-Bold')
    _label(c,'Weights use valued stock/fund assets; Open = unrealized return on remaining acquisition basis.',28,191,8,MUTED)
    for i,r in enumerate(data['top_holdings'][:5]):
        x=28+i*(w-56)/5;tile_w=(w-56)/5-6
        panel(c,x,106,tile_w,77)
        c.setFillColor(CYAN);c.circle(x+18,163,9,fill=1,stroke=0)
        _label(c,f'{i+1:02d}',x+18,160,8.5,BG,'Helvetica-Bold',align='center')
        for j,line in enumerate(_holding_name_lines(ascii_text(r['name']),tile_w-43,8.8)):
            _label(c,line,x+36,165-j*11,8.8,WHITE)
        from pulse.adapter import number
        ret=number(r.get('return_pct'))
        badge='Open N/A' if ret is None else 'Open '+('0.00%' if ret==0 else f'{ret:+,.2f}%')
        if stringWidth(badge,'Helvetica-Bold',7)<=tile_w-43:
            _label(c,badge,x+tile_w-8,177,7,HexColor(MINT) if ret is not None and ret>0 else HexColor('#fca5a5') if ret is not None and ret<0 else MUTED,'Helvetica-Bold',align='right')
        value=f"EUR {r['value']:,.2f}"
        _label(c,value,x+10,134,fit_size(value,tile_w-20),HexColor(MINT),'Helvetica-Bold')
        _label(c,f"{r['weight_pct']:.1f}% of valued stocks/funds",x+10,118,8,MUTED)
    if not data['top_holdings']:
        _label(c,'No supported holding ranking available.',28,140,10,AMBER)
    _label(c,'INSIGHT',29,94,7,CYAN,'Helvetica-Bold')
    insight=ascii_text(model['insights'][0]).replace(ascii_text(model['raw'].get('benchmark_name','')),ascii_text(benchmark_alias(model)))
    _label(c,_truncate(insight,w-114,8),85,94,8,WHITE)
    health=warning_summary(model)
    derivative_detail=f"Missing derivative valuations: {model['dependencies']['missing_derivative']}; full portfolio value and profit unavailable." if model['dependencies']['missing_derivative'] else ''
    health_lines=lines(health.replace(derivative_detail,'').strip(),w-114)
    # The full diagnostic list is in the UI/HTML; summarize additional checks
    # explicitly rather than shrink prose or draw beyond the A4 page.
    limit=3 if derivative_detail else 4
    if len(health_lines)>limit:health_lines=health_lines[:limit-1]+['Additional checks require review; full diagnostics are in the application and HTML report.']
    if derivative_detail:health_lines.append(derivative_detail)
    _label(c,'HEALTH',29,80,7,AMBER if model['issues'] else MUTED,'Helvetica-Bold')
    y=80
    for line in health_lines:
        _label(c,line,85,y,8,MUTED);y-=10
    method='Stock/fund profit excludes derivatives and cash interest. Capital/recovery cover the full ecosystem; recovery is not withdrawable cash. '
    method+= 'Prices: fabricated synthetic illustrations.' if model['raw']['synthetic'] else 'Prices: Yahoo Finance where available; EUR reporting.'
    _label(c,'METHOD',29,y-2,7,MUTED,'Helvetica-Bold')
    for line in lines(method,w-114):
        _label(c,line,85,y-2,8,MUTED);y-=10
    comp=model.get('composition',{})
    if y>=46 and comp.get('top_pct') is not None:
        detail=f"Composition: {comp['active_count']} active stocks/funds; {comp['valued_count']} reliably valued; top {comp['top_count']}: {comp['top_pct']:.1f}%; stocks: {comp['stock_pct']:.1f}%, funds: {comp['fund_pct']:.1f}%"
        if comp['unclassified_pct']>0:detail+=f", unclassified: {comp['unclassified_pct']:.1f}%"
        if comp['partial']:detail+=' (unaffected partial scope)'
        _label(c,_truncate(detail,w-58,8),29,43,8,MUTED)
    _label(c,'Unofficial independent analysis. Not affiliated with Trade Republic. Not a tax certificate or investment recommendation.',29,min(y-4,29),8,MUTED)
    c.showPage();c.save()
    return output.getvalue()
