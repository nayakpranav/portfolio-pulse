"""Display-only composition of canonical active stock/fund positions."""
from datetime import date
from math import fsum,isfinite
import pandas as pd
from html import escape

def finite(value):
    try:
        value=float(value)
        return value if isfinite(value) else None
    except (ValueError,TypeError):return None

def composition(result,as_of,accounting_valid=True,partial=False):
    out=dict(active_count=None,valued_count=0,top_count=0,total_value=None,top_pct=None,remaining_pct=None,
             stock_pct=None,fund_pct=None,unclassified_pct=None,partial=partial,as_of=str(as_of))
    if not accounting_valid:
        return dict(out,note='Active positions and allocations require resolved stock/fund accounting.')
    diagnostics={}
    for row in result.get('valuation_diagnostics',[]):
        if row.get('instrument_type')=='DERIVATIVE':continue
        for key in ('canonical_instrument_id','canonical_current_isin','isin'):
            if row.get(key):diagnostics[str(row[key])]=row
    grouped={}
    for index,row in enumerate(result.get('holdings',[])):
        if row.get('position_status')!='ACTIVE' or str(row.get('asset_class','')).upper()=='DERIVATIVE':continue
        quantity=finite(row.get('current_quantity'))
        if quantity is None or quantity<=1e-8:continue
        identity=str(row.get('canonical_instrument_id') or row.get('current_isin') or row.get('isin') or 'unidentified-row-'+str(index))
        grouped.setdefault(identity,[]).append(row)
    out['active_count']=len(grouped);values=[];types={'STOCK':[],'FUND':[],'UNCLASSIFIED':[]}
    for identity,rows in grouped.items():
        row=rows[0]
        # An ambiguous duplicate cannot contribute a silently doubled valuation.
        signature=lambda r:(r.get('current_quantity'),r.get('live_current_value_eur'),r.get('asset_class'),r.get('live_price_date'))
        if any(signature(r)!=signature(row) for r in rows):continue
        value=finite(row.get('live_current_value_eur'))
        diag=diagnostics.get(identity) or diagnostics.get(str(row.get('current_isin') or row.get('isin') or ''))
        quote=pd.to_datetime(row.get('live_price_date'),errors='coerce',utc=True)
        if value is None or value<0 or not diag or diag.get('valuation_status')!='VALUED' or pd.isna(quote):continue
        if not 0<=(as_of-quote.date()).days<=7:continue
        values.append(value)
        kind=str(row.get('asset_class','')).strip().upper()
        types[kind if kind in {'STOCK','FUND'} else 'UNCLASSIFIED'].append(value)
    out['valued_count']=len(values);out['top_count']=min(5,len(values));total=fsum(values)
    if total>0:
        out.update(total_value=total,top_pct=fsum(sorted(values,reverse=True)[:5])/total*100)
        out['remaining_pct']=100-out['top_pct']
        for kind,key in [('STOCK','stock_pct'),('FUND','fund_pct'),('UNCLASSIFIED','unclassified_pct')]:out[key]=fsum(types[kind])/total*100
    out['note']=f"{out['valued_count']} of {out['active_count']} active positions have reliable dated valuations."
    if out['active_count']==0:out['note']='No active stock/fund positions. Allocation unavailable.'
    elif total<=0:out['note']+=' Allocation unavailable: no positive reliably valued denominator.'
    elif out['valued_count']<out['active_count']:out['note']+=' Unpriced, stale or unverified values are excluded from percentages.'
    if partial:out['note']='Partial scope: unaffected securities only. '+out['note']
    return out

COMPOSITION_SCHEMA_VERSION=1

COMPOSITION_CSS='''.composition{min-width:0}.composition-numbers{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:14px 0 20px}.composition-number{font-size:1.75rem;font-weight:700;color:#a8ebbc;font-variant-numeric:tabular-nums}.composition-label{color:#91a7bc;font-size:.875rem}.composition-bar{height:10px;border-radius:5px;overflow:hidden;display:flex;background:#24415e;margin:10px 0}.composition-segment{height:100%;flex-shrink:0}.composition-legend{display:flex;gap:12px;flex-wrap:wrap;font-size:.875rem;margin-bottom:22px;color:#f6f9fd}.composition-note{font-size:.85rem;color:#91a7bc;line-height:1.5;overflow-wrap:anywhere}.composition-key{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:5px}@media(max-width:390px){.composition-numbers{gap:12px}.composition-number{font-size:1.55rem}}'''

def composition_panel(data):
    percent=lambda v:f'{v:.1f}%' if v is not None else 'Unavailable'
    count='Unavailable' if data['active_count'] is None else str(data['active_count'])
    top='Top '+str(data['top_count']) if data['top_count'] else 'Top five'
    parts=['<div class="composition"><div class="composition-numbers"><div><div class="composition-label">Active stock/fund holdings</div><div class="composition-number" data-composition="active">'+count+'</div></div><div><div class="composition-label">'+top+' concentration</div><div class="composition-number" data-composition="concentration">'+percent(data['top_pct'])+'</div></div></div>']
    def bar(items,label):
        if data['top_pct'] is None:return '<p class="composition-note">'+escape(label)+': unavailable.</p>'
        segments=''.join('<span class="composition-segment" style="width:'+f'{value:.8f}'+'%;background:'+color+'"></span>' for title,value,color in items)
        legend=''.join('<span><i class="composition-key" style="background:'+color+'" aria-hidden="true"></i>'+escape(title)+': '+percent(value)+'</span>' for title,value,color in items)
        return '<div class="composition-label">'+escape(label)+'</div><div class="composition-bar" role="img" aria-label="'+escape('; '.join(title+': '+percent(value) for title,value,color in items),quote=True)+'">'+segments+'</div><div class="composition-legend">'+legend+'</div>'
    parts.append(bar([(top,data['top_pct'],'#42cbea'),('Remaining',data['remaining_pct'],'#24415e')],'Concentration of reliably valued assets'))
    items=[('Stocks',data['stock_pct'],'#42cbea'),('ETFs/funds',data['fund_pct'],'#a78bfa')]
    if data['unclassified_pct'] is not None and data['unclassified_pct']>0:items.append(('Unclassified',data['unclassified_pct'],'#91a7bc'))
    parts.append(bar(items,'Instrument types · valued stock/fund assets'))
    parts.append('<p class="composition-note">'+escape(data['note'])+'</p><p class="composition-note">Stock/fund instruments only; derivatives excluded. No fund constituent look-through, sector or risk exposure inference. Reliable coverage requires canonical valued status and a verified quote within seven days of '+escape(data['as_of'])+'.</p></div>')
    return ''.join(parts)
