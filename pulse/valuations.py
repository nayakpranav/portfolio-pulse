"""Optional, dated EUR-per-unit evidence; never a derivative pricing model."""
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

MAX_QUOTE_AGE_DAYS = 7


def today():
    return datetime.now(ZoneInfo('Europe/Berlin')).date()


def quote_status(quote, reference_date=None):
    """A missing/stale/manual observation must not become a current quote."""
    reference_date = reference_date or today()
    try:
        observed = date.fromisoformat(str(quote['date']))
        if isinstance(quote['price_eur'], bool):
            return 'MANUAL_INVALID'
        price = float(quote['price_eur'])
        source = quote['source']
        if not math.isfinite(price) or not 0 < price <= 100000 or not isinstance(source, str) or not source.strip() or len(source) > 160:
            return 'MANUAL_INVALID'
        age = (reference_date - observed).days
        if age < 0:
            return 'MANUAL_FUTURE'
        if age > MAX_QUOTE_AGE_DAYS:
            return 'MANUAL_STALE'
        return 'MANUAL_CONFIRMED'
    except (KeyError, TypeError, ValueError, OverflowError):
        return 'MANUAL_INVALID'


def activity_anchor(data, isin, observed):
    """Bind local evidence to exact broker activity, not an ISIN/name alone.

    Only activity through the observation date is relevant. Later unrelated
    transactions and CSV row order cannot change that historical evidence.
    """
    from pulse.event_review import normalized
    import pandas as pd
    frame = normalized(data)
    rows = frame[frame['isin'].eq(isin) & frame['asset_class_clean'].eq('DERIVATIVE') &
                 frame['event_date'].le(pd.Timestamp(observed))]
    identifiers = rows['transaction_id'].fillna('').astype(str).str.strip()
    if rows.empty or identifiers.eq('').any() or identifiers.str.lower().eq('nan').any() or identifiers.duplicated().any():
        raise ValueError('Exact derivative activity is required to remember a quote.')
    fields = ('transaction_id', 'event_datetime', 'type_norm', 'isin', 'security_name',
              'shares', 'price', 'amount', 'fee', 'tax', 'description_clean')
    numeric = {'shares', 'price', 'amount', 'fee', 'tax'}
    def stable(row, key):
        value = row.get(key)
        if pd.isna(value):
            return '<missing>'
        return float(value).hex() if key in numeric else str(value).strip()
    evidence = sorted(tuple(stable(row, key) for key in fields) for _, row in rows.iterrows())
    return hashlib.sha256(repr(evidence).encode()).hexdigest()


def _read(root):
    from pulse.profile import _protect
    path = Path(root)/'derivative-valuations.dat'
    if not path.exists():
        return []
    if path.stat().st_size > 131072:
        raise ValueError('Saved valuation evidence exceeds limit.')
    records = json.loads(_protect(path.read_bytes(), decrypt=True))
    if not isinstance(records, list) or len(records) > 100:
        raise ValueError('Invalid saved valuation evidence.')
    for row in records:
        if not isinstance(row, dict) or set(row) != {'isin', 'activity_sha256', 'quote'}:
            raise ValueError('Invalid saved valuation evidence.')
        if not isinstance(row['isin'], str) or len(row['isin']) != 12 or not row['isin'].isalnum():
            raise ValueError('Invalid saved valuation evidence.')
        digest = row['activity_sha256']
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Invalid saved valuation evidence.')
        if quote_status(row['quote']) == 'MANUAL_INVALID':
            raise ValueError('Invalid saved valuation evidence.')
    return records


def saved_quotes(data, root=None):
    from pulse.profile import configured_root
    root = configured_root() if root is None else Path(root)
    if root is None:
        return {}
    quotes = {}
    for row in _read(root):
        if quote_status(row['quote']) != 'MANUAL_CONFIRMED':
            continue  # Expired records remain private; never reapply them as current.
        try:
            if activity_anchor(data, row['isin'], row['quote']['date']) == row['activity_sha256']:
                quotes[row['isin']] = dict(row['quote'])
        except ValueError:
            continue
    return quotes


def remember_quote(data, isin, quote):
    from pulse.profile import configured_root, _protect, _atomic, _lock
    from pulse.private_config import validate_private_config, export_digest
    root = configured_root()
    if root is None:
        return False  # Hosted evidence is session-only, never a global registry.
    validate_private_config({'export_sha256': export_digest(data), 'derivative_quotes': {isin: quote}}, data)
    if quote_status(quote) != 'MANUAL_CONFIRMED':
        raise ValueError('Only a recent, dated EUR-per-unit observation can be remembered.')
    record = dict(isin=isin, activity_sha256=activity_anchor(data, isin, quote['date']), quote=dict(quote))
    with _lock:
        rows = [r for r in _read(root) if (r['isin'], r['activity_sha256']) != (isin, record['activity_sha256'])]
        if len(rows) >= 100:
            raise ValueError('Saved valuation evidence exceeds limit.')
        rows.append(record)
        _atomic(root/'derivative-valuations.dat', _protect(json.dumps(rows, allow_nan=False).encode()))
    return True


def diagnostic_rows(rows, positions):
    """Correct report metadata without changing canonical valuation arithmetic."""
    by_isin = {r['isin']: r for r in positions}
    output = []
    for original in rows:
        row = dict(original)
        if row.get('instrument_type') == 'DERIVATIVE':
            quote = by_isin.get(row.get('isin'), {})
            row['quote_date'] = quote.get('quote_date', '')
            row['attempted_sources'] = 'USER_SUPPLIED_EUR_OBSERVATION' if quote.get('quote_status', '').startswith('MANUAL_') else quote.get('quote_source','') if quote.get('quote_status')=='AUTO_VERIFIED_BID' else 'NONE: no verified automatic bid; see derivative quote evidence'
        output.append(row)
    return output
