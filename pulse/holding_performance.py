"""Expose and validate existing canonical open-position EUR performance.

No lot reconstruction, price requests, FX calculation or alternative accounting.
"""
from collections import Counter
import math
import pandas as pd
from pulse.scopes import number


def project_holdings(result,snapshot,as_of):
    active=[r for r in result['holdings'] if r.get('position_status')=='ACTIVE' and r.get('asset_class')!='DERIVATIVE']
    identity=lambda r:str(r.get('canonical_instrument_id') or r.get('current_isin') or r.get('isin') or '')
    counts=Counter(identity(r) for r in active)
    diagnostics={}
    for d in result['valuation_diagnostics']:
        if d.get('instrument_type')=='DERIVATIVE':continue
        for key in ('canonical_instrument_id','canonical_current_isin','isin'):
            if d.get(key):diagnostics[str(d[key])]=d
    total=number(snapshot['valued_stockfund_total'])
    output=[];review=0
    for row in active:
        value=number(row.get('live_current_value_eur'));basis=number(row.get('remaining_acquisition_cost_basis_eur'))
        pl=number(row.get('live_unrealized_pl_acquisition_basis_eur'));ret=number(row.get('live_simple_return_acquisition_basis_pct'))
        holding=dict(name=row.get('security_name','Security'),isin=str(row.get('isin') or ''),canonical_instrument_id=identity(row),
            quantity=row.get('current_quantity'),value=value,basis=basis if basis is not None and basis>=0 else None,
            weight_pct=value/total*100 if value is not None and total is not None and total>0 else None,
            quote_date=row.get('live_price_date'),status='Valued' if value is not None else 'Unpriced',open_pl=None,return_pct=None)
        diag=diagnostics.get(identity(row)) or diagnostics.get(holding['isin'])
        quote=pd.to_datetime(row.get('live_price_date'),errors='coerce',utc=True)
        native=str(row.get('live_price_currency') or '').upper()
        fx=number(row.get('fx_to_eur'))
        status='Available'
        if not identity(row) or counts[identity(row)]!=1:
            status='Ambiguous canonical position';review+=1;holding['basis']=None
        elif basis is None or basis<0:
            status='Missing or invalid acquisition basis';review+=1
        elif value is None or value<0 or not diag or diag.get('valuation_status')!='VALUED':
            status='Reliable valuation unavailable'
        elif pd.isna(quote) or not 0<=(as_of-quote.date()).days<=7:
            status='Verified current quote unavailable or stale'
        elif native and native!='EUR' and (fx is None or fx<=0):
            status='Canonical EUR conversion unavailable';review+=1
        elif pl is None or not math.isclose(value-basis,pl,rel_tol=0,abs_tol=1e-6):
            status='Canonical open P/L requires reconciliation';review+=1
        else:
            holding['open_pl']=pl
            if basis<=1e-12:
                status='Return unavailable: zero or unusable basis'
            elif ret is None:
                status='Canonical return unavailable';review+=1
            elif not math.isclose(pl/basis*100,ret,rel_tol=0,abs_tol=1e-6):
                status='Canonical return requires reconciliation';review+=1
            else:holding['return_pct']=ret
        holding['performance_status']=status
        output.append(holding)
    by_isin={r['isin']:r for r in output if r['isin'] and sum(x['isin']==r['isin'] for x in output)==1}
    for top in snapshot['top_holdings']:
        source=by_isin.get(top['isin'])
        for key in ('basis','open_pl','return_pct','performance_status','canonical_instrument_id'):
            top[key]=source.get(key) if source else ('Canonical position mapping unavailable' if key=='performance_status' else None)
    issues=[f'{review} active stock/fund position(s) require holding-performance source review. Unreconciled P/L or returns are unavailable; inspect Performance coverage in All Holdings.'] if review else []
    return output,issues
