"""UI draft preparation and row errors, separate from valuation accounting."""
from datetime import date
from pulse.valuations import quote_status

DEFAULT_SOURCE = 'Trade Republic — displayed bid'


def drafts(coverage, quotes):
    return [dict(Instrument=r['name'],ISIN=r['isin'],Quantity=r['quantity'],
        **{'EUR per unit':quotes.get(r['isin'],{}).get('price_eur'),
           'Observation date':date.fromisoformat(quotes[r['isin']]['date']) if quotes.get(r['isin'],{}).get('date') else None,
           'Source':quotes.get(r['isin'],{}).get('source',''),
           'Status':r['status'].replace('_',' ').lower()}) for r in coverage['rows']]


def validate(rows, active_isins, default_date, default_source, reference_date=None):
    accepted,errors={},[]
    seen=set()
    for index,row in enumerate(rows,1):
        isin=row.get('ISIN')
        if isin not in active_isins or isin in seen or (isinstance(active_isins,list) and (index>len(active_isins) or active_isins[index-1]!=isin)):
            errors.append((index,'Instrument identity changed or is duplicated. Reload the current positions.'));continue
        seen.add(isin)
        price=row.get('EUR per unit')
        # Empty rows mean no new observation. Existing evidence is not deleted.
        if price is None or str(price).strip() in {'','nan','<NA>'}:continue
        observed=row.get('Observation date')
        observed=default_date if observed is None or str(observed) in {'','NaT','nan'} else observed
        if hasattr(observed,'date'):observed=observed.date()
        source=str(row.get('Source') or '').strip() or default_source.strip()
        quote=dict(price_eur=price,date=observed.isoformat() if hasattr(observed,'isoformat') else str(observed),source=source)
        status=quote_status(quote,reference_date)
        if status!='MANUAL_CONFIRMED':
            errors.append((index,{'MANUAL_FUTURE':'Observation date is in the future.',
                'MANUAL_STALE':'Observation is older than seven calendar days.'}.get(status,
                'Enter a positive finite EUR-per-unit price (up to 100,000), a valid date and a source (up to 160 characters).')))
        else:accepted[isin]=quote
    if seen!=set(active_isins):errors.append((0,'The editor no longer matches the complete set of active instruments. Reload the current positions.'))
    return accepted,errors
