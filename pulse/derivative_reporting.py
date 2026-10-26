"""Derivative labels and quote-center display rows; no position arithmetic changes."""
from datetime import date
import math
import re
from pulse.derivative_quotes import finite_positive,Observation,timestamp,validate_indicative,VONTOBEL_HOST


def _text(value,limit=200):
    if not isinstance(value,str):return ''
    return ' '.join(value.split())[:limit]


def derivative_label(position,ledger=(),metadata=None):
    isin=_text(position.get('isin'),12);name=_text(position.get('security_name')) or 'Derivative'
    metadata=metadata or {}
    verified=metadata.get('isin')==isin and metadata.get('identity')=='EXACT_ISIN'
    # Use only explicit "auf <underlying>" statements from the same instrument's
    # canonical transaction descriptions. A guess from a similar name is not evidence.
    rows=[r for r in ledger if r.get('isin')==isin]
    underlyings=set()
    for row in rows:
        description=_text(row.get('description_clean'),2000)
        match=re.search(r'\bauf\s+(.+?)(?:,?\s+quantity\b|$)',description,re.I)
        if match:
            underlying=match.group(1).strip(' ,.;')
            # Remove the share's nominal denomination, never rename its issuer.
            underlying=re.sub(r'\s+(?:DL|EO|SF|LS)\s*[-\d,.]+$','',underlying,flags=re.I).strip()
            if underlying and not re.search(r'\b(?:amount|price|fee|tax|ISIN)\b',underlying,re.I):underlyings.add(underlying)
    underlying=_text(metadata.get('underlying')) if verified else ''
    if not underlying and len(underlyings)==1:underlying=next(iter(underlyings))
    if not underlying:return name+' · '+isin
    descriptions=' '.join(_text(r.get('description_clean'),2000) for r in rows)
    kind=_text(metadata.get('product_type')) if verified else _text(position.get('derivative_kind'))
    direction=_text(metadata.get('direction')) if verified else _text(position.get('direction'))
    maturity=_text(metadata.get('maturity'),40) if verified else ''
    turbo='turbo' in (kind+' '+descriptions).lower() or 'mini future' in kind.lower()
    open_end=bool(re.search(r'open[ -]?end',kind+' '+maturity+' '+descriptions,re.I))
    if turbo:
        direction={'CALL':'Long','PUT':'Short','LONG':'Long','SHORT':'Short'}.get(direction.upper(),'')
        product=(direction+' '+('Open End' if open_end else 'Turbo')).strip()
        # Historical turbo financing levels are deliberately never a current strike.
    else:
        match=re.fullmatch(r'(Call|Put)\s+([\d.,]+)\s*([$€])',name,re.I)
        if match:
            amount=match.group(2)
            # These are display-only broker strike strings, not price calculations.
            amount=amount.replace('.','').replace(',','.') if ',' in amount or re.fullmatch(r'\d{1,3}(?:\.\d{3})+',amount) else amount
            try:amount=f'{float(amount):,g}'
            except ValueError:pass
            product=match.group(1).title()+' '+match.group(3)+amount
        else:product=name
    if maturity and not open_end:
        try:ending=date.fromisoformat(maturity).strftime('%d %b %Y')
        except ValueError:ending='Expiry unavailable'
    else:ending='' if open_end else 'Expiry unavailable'
    return ' — '.join(p for p in (underlying,product,ending,isin) if p)


def quote_center(raw):
    """Explicit display contract: raw response snippets/transaction data excluded."""
    audits=raw.get('derivative_quote_evidence',{});rows=[]
    for position in raw.get('active_derivatives',[]):
        if position.get('position_status')!='ACTIVE':continue
        isin=position['isin'];audit=audits.get(isin,{})
        attempts=audit.get('attempts',[]);selected=audit.get('selected') or {}
        exact=[a for a in attempts if a.get('identity')=='EXACT_ISIN']
        indicative=None
        candidate=audit.get('indicative')
        if isinstance(candidate,dict):
            try:
                dated=Observation(**{k:candidate[k] for k in Observation.__dataclass_fields__})
                checked=validate_indicative(dated,now=timestamp(audit.get('evaluated_at')),allowed_hosts=(VONTOBEL_HOST,))
                if checked['eligible_indicative'] and dated.requested_isin==isin:indicative=checked
            except (KeyError,ValueError,TypeError):pass
        observation=selected or indicative or next((a for a in exact if a.get('bid') is not None or a.get('ask') is not None),{}) or next(iter(exact),{})
        quantity=position.get('current_quantity');price=position.get('live_price_eur')
        if position.get('quote_status') not in {'MANUAL_CONFIRMED','AUTO_VERIFIED_BID'}:price=None
        try:value=float(quantity)*float(price);value=value if math.isfinite(value) else None
        except (ValueError,TypeError):value=None
        indicative_value=None
        try:
            if indicative and not isinstance(quantity,bool) and math.isfinite(float(quantity)) and float(quantity)>0:
                indicative_value=float(quantity)*indicative['bid']
                if not math.isfinite(indicative_value):indicative_value=None
        except (TypeError,ValueError,OverflowError):pass
        rows.append(dict(isin=isin,label=derivative_label(position,raw.get('derivative_ledger',[]),audit.get('metadata')),
                         quantity=quantity,bid=finite_positive(observation.get('bid')),ask=finite_positive(observation.get('ask')),source=observation.get('provider',''),
                         observed_at=observation.get('observed_at',''),freshness=observation.get('freshness','UNKNOWN'),price_type=observation.get('price_type','NONE'),
                         currency=observation.get('currency',''),denomination=observation.get('denomination',''),
                         status=position.get('quote_status','UNAVAILABLE'),value=value,indicative_value=indicative_value,
                         reason=audit.get('status','No captured automatic quote evidence'),cache=audit.get('cache_warning') or audit.get('cache','Not used'),
                         automatic=bool(selected and selected.get('accepted')),indicative=bool(indicative_value is not None and not selected)))
    automatic=sum(r['automatic'] for r in rows);indicative=sum(r['indicative'] for r in rows);missing=sum(r['value'] is None for r in rows)
    estimates=[r['value'] if r['value'] is not None else r['indicative_value'] for r in rows]
    covered=sum(v is not None for v in estimates)
    return dict(rows=rows,active=len(rows),automatic=automatic,indicative=indicative,missing=missing,
                indicative_coverage=covered,indicative_known_subtotal=sum(v for v in estimates if v is not None) if covered else None,
                indicative_total=sum(estimates) if rows and covered==len(rows) else None,full_portfolio_estimate=None,
                note='Indicative estimates accept exact-ISIN EUR-per-unit bids observed today or the previous weekday, including delayed/closing observations. No ask is required. Holidays do not extend this conservative date window. Source and actual timestamps remain visible. Indicative values never complete canonical valuation. Local cache refreshes once per weekday; unknown or expired observations remain unavailable. Manual observations remain optional and separately labelled.')


def quote_summary(center):
    return f"Derivative quotes: {center['automatic']}/{center['active']} automatic bids; {center['indicative']} indicative only; {center['missing']} unvalued."


def indicative_summary(center):
    subtotal=center.get('indicative_known_subtotal')
    if subtotal is None:return 'Indicative derivative valuation unavailable: no validated dated bids or optional manual values.'
    if center.get('indicative_total') is None:
        return f"Indicative derivative known subtotal: €{subtotal:,.2f} ({center['indicative_coverage']}/{center['active']} positions); incomplete, full-portfolio estimate unavailable."
    text=f"Indicative derivative total: €{subtotal:,.2f}."
    full=center.get('full_portfolio_estimate')
    text+=f' Indicative full-portfolio estimate (cash excluded): €{full:,.2f}.' if full is not None else ' Indicative full-portfolio estimate unavailable: accounting or stock/fund coverage incomplete.'
    return text+' Dated bid/manual evidence; not a live liquidation value or canonical complete-portfolio figure.'


def quote_table_rows(center):
    def amount(value):
        if value is None:return 'Unavailable'
        try:return f'€{float(value):,.6f}'.rstrip('0').rstrip('.')
        except (ValueError,TypeError):return 'Unavailable'
    return [{'Instrument':r['label'],'ISIN':r['isin'],'Units':r['quantity'],
             'Bid':amount(r['bid']) if r['currency']=='EUR' else 'Unavailable','Ask':amount(r['ask']) if r['currency']=='EUR' else 'Unavailable',
             'Quote convention':r['denomination'] or 'Unverified',
             'Position value (EUR)':amount(r['value']),'Indicative position estimate (EUR)':amount(r['indicative_value']),
             'Source':r['source'] or 'Unavailable','Cache':r['cache'],
             'Observed at':r['observed_at'] or 'Unavailable','Freshness':r['freshness'].replace('_',' ').lower(),
             'Valuation':r['status'].replace('_',' ').lower(),'Diagnostic':r['reason'].replace('_',' ').lower()} for r in center['rows']]
