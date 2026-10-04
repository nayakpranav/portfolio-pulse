"""Private export-bound confirmations; no event registry is distributed."""
import csv
import hashlib
from io import StringIO
import json
import math
from pathlib import Path
from datetime import date
from pulse import core
from portfolio_core import canonical_column_mapping

def export_digest(data):
    return hashlib.sha256(data).hexdigest()

def event_candidates(data):
    reader = csv.DictReader(StringIO(data.decode('utf-8-sig')))
    mapping = canonical_column_mapping(reader.fieldnames or [])
    candidates = []
    for index, raw in enumerate(reader):
        row = {mapping.get(k, k): v for k, v in raw.items()}
        if str(row.get('type', '')).strip().upper() != 'FREE_RECEIPT':
            continue
        try:
            quantity = float(row.get('shares') or 'nan')
        except ValueError:
            continue
        if quantity < 0 and str(row.get('asset_class', '')).upper().strip() in {'STOCK','FUND'}:
            candidates.append(dict(source_row=index+2, name=row.get('name',''), isin=row.get('symbol',''),
                quantity=quantity, date=row.get('date') or row.get('datetime'),
                export_sha256=export_digest(data)))
    return candidates

def validate_private_config(config, data):
    if config is None:
        return {}
    if not isinstance(config,dict) or config.get('export_sha256') != export_digest(data):
        raise ValueError('Private confirmations must be bound to this exact export. Review them after changing the export.')
    if len(json.dumps(config).encode('utf-8'))>65536:
        raise ValueError('Private configuration exceeds the size limit.')
    from security_events import KnownWorthlessDerecognition
    known=config.get('known_events',[])
    if not isinstance(known,list) or len(known)>100:raise ValueError('Invalid private registry')
    for item in known:
        spec=KnownWorthlessDerecognition(**item)
        if len(spec.transaction_id_sha256)!=64 or any(c not in '0123456789abcdef' for c in spec.transaction_id_sha256) or not math.isfinite(float(spec.quantity)) or float(spec.quantity)>=0:
            raise ValueError('Invalid exact event evidence')
    allowed = {c['source_row'] for c in event_candidates(data)}
    confirmations = config.get('worthless_confirmations', [])
    if not isinstance(confirmations,list) or len(confirmations)>100 or any(type(i) is not int or i not in allowed for i in confirmations):
        raise ValueError('Only detected negative security-delivery rows can be confirmed.')
    quotes = config.get('derivative_quotes', {})
    if not isinstance(quotes,dict) or len(quotes)>100:
        raise ValueError('Invalid manual derivative valuation configuration.')
    reader=csv.DictReader(StringIO(data.decode('utf-8-sig')))
    mapping=canonical_column_mapping(reader.fieldnames or [])
    derivative_symbols=set()
    for raw in reader:
        row={mapping.get(k,k):v for k,v in raw.items()}
        if str(row.get('asset_class','')).strip().upper()=='DERIVATIVE':derivative_symbols.add(str(row.get('symbol','')).strip())
    for isin, quote in quotes.items():
        if not isinstance(isin,str) or len(isin)!=12 or not isin.isalnum() or isin not in derivative_symbols or not isinstance(quote,dict):
            raise ValueError('Manual valuations require an instrument ISIN.')
        price=float(quote.get('price_eur',float('nan')))
        quote_date=date.fromisoformat(str(quote.get('date','')))
        source=str(quote.get('source','')).strip()
        if not math.isfinite(price) or not 0<price<=100000 or quote_date>date.today() or not source or len(source)>160:
            raise ValueError('Manual prices must be positive finite EUR prices with a nonfuture date and source.')
    return config

def load_private_config(path, data):
    path=Path(path)
    if path.stat().st_size>65536:
        raise ValueError('Private configuration exceeds the size limit.')
    return validate_private_config(json.loads(path.read_text(encoding='utf-8')),data)

def personal_defaults(data):
    """Read generic runtime configuration from the user's private environment."""
    import os
    directory=os.environ.get('FOLIOLENS_CONFIG_DIRECTORY')
    if not directory:return {}
    root=Path(directory)
    exact=root/'exports'/(export_digest(data)+'.json')
    config=load_private_config(exact,data) if exact.is_file() else {'export_sha256':export_digest(data)}
    registry=root/'verified-events.json'
    if registry.is_file():
        if registry.stat().st_size>65536:raise ValueError('Private registry exceeds limit')
        config['known_events']=json.loads(registry.read_text(encoding='utf-8'))
    return validate_private_config(config,data)

def install_private_events(ns, config):
    if not config.get('worthless_confirmations') and not config.get('known_events'):
        return
    import security_events
    original=ns['match_known_worthless_derecognitions']
    def match(frame):
        specs=[security_events.KnownWorthlessDerecognition(**item) for item in config.get('known_events',[])]
        for index in config.get('worthless_confirmations',[]):
            # Canonical source_row is the input CSV line number (header is line 1).
            rows=frame[frame['source_row'].eq(index)]
            if len(rows)!=1:
                continue
            row=rows.iloc[0]
            specs.append(security_events.KnownWorthlessDerecognition(
                event_id=f'SESSION_CONFIRMED_ROW_{index}',
                transaction_id_sha256=hashlib.sha256(str(row.get('transaction_id','')).encode()).hexdigest(),
                event_date=str(row['event_date'].date()), isin=str(row['isin']), security_name=str(row['security_name']),
                category=str(row['category']).upper(), asset_class=str(row['asset_class_clean']).upper(),
                broker_type=str(row['type_norm']), quantity=float(row['shares']),description=str(row['description_clean'])))
        saved=security_events.KNOWN_WORTHLESS_DERECOGNITIONS
        try:
            security_events.KNOWN_WORTHLESS_DERECOGNITIONS=tuple(specs)
            return original(frame)  # All canonical cash/full-position/prior-activity guards still apply.
        finally:
            security_events.KNOWN_WORTHLESS_DERECOGNITIONS=saved
    ns['match_known_worthless_derecognitions']=match
