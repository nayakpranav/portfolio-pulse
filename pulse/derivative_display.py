"""Presentation of canonical derivative positions and existing manual evidence.

No network access, quote acquisition, cache or financial-accounting implementation.
"""
from datetime import date
import math
import re


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


def manual_positions(raw,reference_date):
    """Project canonical values only; date/source validation is unchanged."""
    from pulse.valuations import quote_status
    positions=[];metadata=raw.get('derivative_metadata',{})
    for row in raw.get('active_derivatives',[]):
        if row.get('position_status')!='ACTIVE':continue
        quote=dict(price_eur=row.get('live_price_eur'),date=row.get('quote_date'),source=row.get('quote_source'))
        status=quote_status(quote,reference_date)
        valid=(row.get('quote_status')=='MANUAL_CONFIRMED' and status=='MANUAL_CONFIRMED' and row.get('quote_currency')=='EUR'
               and row.get('quote_price_type')=='MANUAL_EUR_PER_UNIT')
        value=row.get('estimated_live_value_eur')
        try:value=float(value);valid=valid and math.isfinite(value)
        except (TypeError,ValueError,OverflowError):valid=False
        positions.append(dict(isin=row['isin'],name=derivative_label(row,raw.get('derivative_ledger',[]),metadata.get(row['isin'])),
            quantity=row.get('current_quantity'),price=float(quote['price_eur']) if valid else None,
            observed=_text(row.get('quote_date'),40),source=_text(row.get('quote_source'),160),value=value if valid else None,
            confirmed=valid,status='MANUAL_CONFIRMED' if valid else status if row.get('quote_status','').startswith('MANUAL_') else 'UNAVAILABLE'))
    return dict(rows=positions,active=len(positions),confirmed=sum(r['confirmed'] for r in positions),asof=reference_date)


def manual_summary(coverage):
    return f"Manual derivative valuations: {coverage['confirmed']}/{coverage['active']} valid and applied (dated EUR/unit evidence)."


def valuation_summary(dependencies,coverage):
    if not dependencies['ecosystem_accounting']:return 'Current valuation completeness requires accounting review.'
    complete=dependencies['full_valued'] and coverage['confirmed']==coverage['active']
    text='Current valuations: '+('complete' if complete else 'incomplete')
    if coverage['active']:text+=f"; derivatives {coverage['confirmed']}/{coverage['active']} confirmed manually"
    return text+'. Historical and accounting disclosures are reported separately.'


def table_rows(coverage):
    return [{'Derivative':r['name'],'ISIN':r['isin'],'Quantity':r['quantity'],
        'Confirmed EUR/unit':f"€{r['price']:,.6f}".rstrip('0').rstrip('.') if r['price'] is not None else 'Unavailable',
        'Observed':r['observed'] or 'Unavailable','Source':r['source'] or 'Unavailable',
        'Position value':f"€{r['value']:,.2f}" if r['value'] is not None else 'Unavailable',
        'Status':r['status'].replace('_',' ').lower()} for r in coverage['rows']]

