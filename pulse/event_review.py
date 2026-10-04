"""Preflight uses the unchanged canonical normalizer and event matcher.

Only generic function code is cached. Private frames/evidence are call-local.
"""
import ast
from dataclasses import asdict
from functools import lru_cache
import hashlib
from io import BytesIO
import re
from types import FunctionType
import numpy as np
import pandas as pd
from pulse.core import VENDOR
from portfolio_core import canonical_column_mapping, validate_export_columns
import security_events


@lru_cache(maxsize=1)
def _normalizer():
    tree = ast.parse((VENDOR/'analysis_engine.py').read_text(encoding='utf-8'))
    names = {'parse_euro_number', 'clean_str', 'normalize_raw_dataframe'}
    selected = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[])
    namespace = dict(pd=pd, np=np, re=re, canonical_column_mapping=canonical_column_mapping,
                     validate_export_columns=validate_export_columns)
    exec(compile(selected, str(VENDOR/'analysis_engine.py'), 'exec'), namespace)
    return namespace['normalize_raw_dataframe']


def normalized(data):
    return _normalizer()(pd.read_csv(BytesIO(data)))


def canonical_match(frame, specs):
    original = security_events.match_known_worthless_derecognitions
    namespace = dict(original.__globals__, KNOWN_WORTHLESS_DERECOGNITIONS=tuple(specs))
    # Avoid mutating a global registry across sessions or concurrent preflights.
    return FunctionType(original.__code__, namespace)(frame)


def event_spec(row):
    transaction_id = str(row.get('transaction_id', '')).strip()
    if not transaction_id or transaction_id.lower() == 'nan':
        raise ValueError('A persistent event requires an exact broker transaction identifier.')
    digest = hashlib.sha256(transaction_id.encode()).hexdigest()
    return security_events.KnownWorthlessDerecognition(
        event_id='VERIFIED:'+digest[:20], transaction_id_sha256=digest,
        event_date=str(row['event_date'].date()), isin=str(row['isin']),
        security_name=str(row['security_name']), category=str(row['category']).upper().strip(),
        asset_class=str(row['asset_class_clean']).upper(), broker_type=str(row['type_norm']),
        quantity=float(row['shares']), description=str(row['description_clean']))


def prior_activity_anchor(frame, row):
    prior = frame[frame['isin'].eq(row['isin']) & (
        frame['event_datetime'].lt(row['event_datetime']) |
        (frame['event_datetime'].eq(row['event_datetime']) & frame['source_row'].lt(row['source_row'])))]
    prior = prior[prior['asset_class_clean'].isin({'STOCK','FUND'}) & prior['shares'].notna()]
    # Bind the proof to the full prior security activity, not a file digest or name.
    fields = ['transaction_id','type_norm','shares','amount','fee','tax','event_datetime','isin']
    def stable(row,key):
        value=row.get(key,'')
        if key in {'shares','amount','fee','tax'}:
            return float(value).hex() if pd.notna(value) else '<missing>'
        return str(value).strip()
    rows = sorted(tuple(stable(r,k) for k in fields) for _, r in prior.iterrows())
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def records_for(frame, specs):
    matched = canonical_match(frame, specs)
    records = []
    for source in matched['matched_source_rows']:
        row = frame[frame['source_row'].eq(source)].iloc[0]
        digest = event_spec(row).transaction_id_sha256
        spec = next(s for s in specs if s.transaction_id_sha256 == digest)
        records.append({'evidence':asdict(spec), 'prior_activity_sha256':prior_activity_anchor(frame,row)})
    return records


def applicable_specs(frame, records):
    specs = []
    for record in records:
        spec = security_events.KnownWorthlessDerecognition(**record['evidence'])
        matching = frame[frame['transaction_id'].fillna('').astype(str).str.strip().map(
            lambda s: hashlib.sha256(s.encode()).hexdigest()).eq(spec.transaction_id_sha256)]
        if len(matching)==1 and prior_activity_anchor(frame, matching.iloc[0])==record['prior_activity_sha256']:
            specs.append(spec)
    return specs


def review(data, config):
    frame = normalized(data)
    specs = [security_events.KnownWorthlessDerecognition(**s) for s in config.get('known_events',[])]
    matched = canonical_match(frame, specs)
    rows = []
    for _, diagnostic in matched['diagnostics'].iterrows():
        if float(diagnostic.get('quantity',0) or 0) >= 0:
            continue
        source = int(diagnostic['source_row'])
        candidate = frame[frame['source_row'].eq(source)].iloc[0]
        try:
            possible = canonical_match(frame, [event_spec(candidate)])
            eligible = source in possible['matched_source_rows']
        except ValueError:
            eligible = False
        rows.append(dict(source_row=source, name=candidate['security_name'], isin=candidate['isin'],
                         quantity=float(candidate['shares']), date=str(candidate['event_date'].date()),
                         verified=source in matched['matched_source_rows'], eligible=eligible,
                         reason=str(diagnostic['diagnostic'])))
    return rows


def remember(data, config, result):
    from pulse.profile import configured_root, merge_records
    root = configured_root()
    if root is None:
        return 0  # Hosted confirmations are session-only; no shared registry.
    sources = {int(r['source_row']) for r in result.get('worthless_derecognition_events',[])
               if r.get('classification_status')=='SUPPORTED_KNOWN_EVENT'
               and r.get('post_event_quantity')==0 and r.get('cash_effect_eur')==0
               and r.get('fifo_quantity_closed')==r.get('quantity_removed')}
    frame = normalized(data)
    specs = [security_events.KnownWorthlessDerecognition(**s) for s in config.get('known_events',[])]
    for index in config.get('worthless_confirmations',[]):
        if index in sources:
            specs.append(event_spec(frame[frame['source_row'].eq(index)].iloc[0]))
    records = records_for(frame, specs)
    records = [r for r in records if r['evidence']['transaction_id_sha256'] in {
        event_spec(frame[frame['source_row'].eq(i)].iloc[0]).transaction_id_sha256 for i in sources}]
    if records:
        merge_records(root, records)
    return len(records)
