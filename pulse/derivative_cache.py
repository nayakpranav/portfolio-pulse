"""Private per-OS-user daily public-quote cache; no transactions or quantities.

Windows uses the existing DPAPI boundary. Cache failures never authorize a quote
or block portfolio accounting. Every usable cached observation is revalidated.
"""
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
from pulse.derivative_quotes import (Observation,isin_valid,timestamp,previous_weekday,
    validate_observation,validate_indicative,VONTOBEL_HOST)

SCHEMA=1
MAX_BYTES=512*1024


def refresh_day(now):
    from zoneinfo import ZoneInfo
    day=now.astimezone(ZoneInfo('Europe/Berlin')).date()
    return previous_weekday(day+timedelta(days=1)) if day.weekday()>=5 else day


class QuoteCache:
    def __init__(self,directory):
        self.directory=Path(directory);self.path=self.directory/'public-derivative-quotes.dat';self.warning=''

    def _read(self):
        from pulse.profile import _protect
        try:
            if not self.path.exists():return {}
            if self.path.stat().st_size>MAX_BYTES:raise ValueError('limit')
            envelope=json.loads(_protect(self.path.read_bytes(),decrypt=True))
            if envelope.get('schema')!=SCHEMA or not isinstance(envelope.get('records'),dict) or len(envelope['records'])>200:raise ValueError('schema')
            for isin,record in envelope['records'].items():
                if not isin_valid(isin) or not isinstance(record,dict) or not isinstance(record.get('observations'),list) or len(record['observations'])>4 or not isinstance(record.get('metadata'),dict) or not isinstance(record.get('diagnostic'),str):raise ValueError('record')
            return envelope['records']
        except (OSError,ValueError,TypeError,AttributeError):
            self.warning='CACHE_UNAVAILABLE';return {}

    def get(self,isin,now,*,allow_previous=False):
        from pulse.derivative_reporting import _text
        record=self._read().get(isin)
        if not isinstance(record,dict):return None
        evaluated=timestamp(record.get('evaluated_at'))
        if evaluated is None or evaluated>now or not allow_previous and refresh_day(evaluated)!=refresh_day(now):return None
        result=dict(attempts=[],selected=None,indicative=None,metadata={},evaluated_at=now.isoformat(),cache='HIT',
                    network=dict(requests=0,received_bytes=0,elapsed_seconds=0),status='CACHED_NO_USABLE_QUOTE')
        for item in record.get('observations',[])[:4]:
            try:observation=Observation(**item)
            except (TypeError,ValueError):continue
            if observation.requested_isin!=isin:continue
            current=validate_observation(observation,now=now,allowed_hosts=(VONTOBEL_HOST,))
            dated=validate_indicative(observation,now=now,allowed_hosts=(VONTOBEL_HOST,))
            result['attempts'].append(current)
            if current['accepted']:result['selected']=current
            if dated['eligible_indicative']:result['indicative']=dated
        metadata=record.get('metadata',{})
        if isinstance(metadata,dict) and metadata.get('isin')==isin and metadata.get('identity')=='EXACT_ISIN':
            result['metadata']={k:_text(metadata.get(k),200) for k in ('isin','identity','underlying','product_type','maturity','direction','name')}
        result['status']='CACHED_VALIDATED_BID' if result['indicative'] else 'CACHED_NO_USABLE_QUOTE'
        if allow_previous and not result['indicative']:return None
        if not result['attempts']:
            result['attempts']=[dict(provider='Vontobel private document',requested_isin=isin,accepted=False,
                reason=record.get('diagnostic') if record.get('diagnostic') in DIAGNOSTICS else 'NO_USABLE_QUOTE')]
        return result

    def put(self,isin,result):
        if not isin_valid(isin):return
        from pulse.profile import _protect,_atomic
        observations=[]
        for attempt in result.get('attempts',[])[:4]:
            try:observation=Observation(**{k:attempt[k] for k in Observation.__dataclass_fields__})
            except (KeyError,ValueError,TypeError):continue
            if observation.requested_isin==isin and observation.verified_isin==isin:
                # Raw documents are private per-analysis audit only, never cached.
                observations.append({k:v for k,v in attempt.items() if k in Observation.__dataclass_fields__ and k!='raw_evidence'}|{'raw_evidence':''})
        record=dict(evaluated_at=result.get('evaluated_at'),observations=observations,metadata=result.get('metadata',{}),
                    diagnostic=result.get('status') if result.get('status') in DIAGNOSTICS else 'NO_USABLE_QUOTE')
        lock=self.directory/'write.lock';handle=None
        try:
            self.directory.mkdir(parents=True,exist_ok=True)
            handle=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            records=self._read();records[isin]=record
            records=dict(sorted(records.items(),key=lambda pair:pair[1].get('evaluated_at',''),reverse=True)[:200])
            content=_protect(json.dumps(dict(schema=SCHEMA,records=records),allow_nan=False).encode())
            if len(content)>MAX_BYTES:raise ValueError('limit')
            _atomic(self.path,content)
        except (OSError,ValueError,TypeError):self.warning='CACHE_WRITE_UNAVAILABLE'
        finally:
            if handle is not None:
                os.close(handle)
                try:lock.unlink()
                except OSError:self.warning='CACHE_LOCK_CLEANUP_REQUIRED'


DIAGNOSTICS={'PROVIDER_RESTRICTED','PROVIDER_NETWORK_FAILURE','QUOTE_REQUEST_BUDGET',
    'INSTRUMENT_NOT_FOUND','ROBOTS_DISALLOWS','ROBOTS_UNVERIFIED','PROVIDER_RESPONSE_TOO_LARGE',
    'NO_QUOTE_IN_PUBLIC_DOCUMENT','EXACT_ISIN_NOT_VERIFIED','FULL_QUOTE_TIMESTAMP_REQUIRED',
    'REDIRECT_REQUIRES_REVIEW','TOO_MANY_REDIRECTS','PROVIDER_UNAVAILABLE','MALFORMED_PROVIDER_DOCUMENT'}
