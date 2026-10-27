"""Short-lived, session-owned market capture for manual-only canonical reruns.

No exports, reports or private confirmations belong in this public-input capture.
The owning Streamlit session retains it in memory and deletes it on clear/change.
"""
from datetime import datetime, timezone
import hashlib
import json
from zoneinfo import ZoneInfo

MAX_AGE_SECONDS = 4 * 60 * 60
SCHEMA = 1


def _binding(data, benchmark, config):
    accounting = {k: (config or {}).get(k, []) for k in ('known_events', 'worthless_confirmations')}
    return {'export': hashlib.sha256(data).hexdigest(), 'benchmark': benchmark,
            'accounting': hashlib.sha256(json.dumps(accounting,sort_keys=True).encode()).hexdigest()}


def retain(data, benchmark, config, raw, now=None):
    capture=raw.get('market_capture')
    if raw.get('synthetic') or not isinstance(capture,dict) or not isinstance(capture.get('calls'),dict) or not capture['calls']:
        return None
    now=now or datetime.now(timezone.utc)
    return dict(schema=SCHEMA, binding=_binding(data,benchmark,config),created_at=now.isoformat(),capture=capture)


def eligible(envelope, data, benchmark, config, now=None):
    """Return original inputs only; never advance their observation timestamps."""
    now=now or datetime.now(timezone.utc)
    try:
        created=datetime.fromisoformat(envelope['created_at'])
        age=(now-created).total_seconds()
        if envelope['schema']!=SCHEMA or envelope['binding']!=_binding(data,benchmark,config):
            return None,'The export, benchmark or accounting evidence changed.'
        if not 0<=age<=MAX_AGE_SECONDS or now.astimezone(ZoneInfo('Europe/Berlin')).date()!=created.astimezone(ZoneInfo('Europe/Berlin')).date():
            return None,'The session market capture expired; original quote dates are never refreshed by reuse.'
        capture=envelope['capture']
        if not isinstance(capture,dict) or not isinstance(capture.get('calls'),dict) or len(json.dumps(capture).encode())>64*1024*1024:
            raise ValueError
        return capture,''
    except (KeyError,ValueError,TypeError,OverflowError,AttributeError):
        return None,'No validated same-session market capture is available.'


def recompute(data, *, benchmark, config, envelope, run):
    """Canonical rerun, never a patch to prior financial results.

    Only a missing/expired binding falls back to live processing. A failed
    offline run is visible rather than silently issuing unexpected requests.
    """
    capture,reason=eligible(envelope,data,benchmark,config)
    raw=run(data,benchmark=benchmark,private_config=config,replay=capture,capture=True)
    if capture is not None and raw.get('replay_missing_inputs',0):
        raise ValueError('Captured market inputs were incomplete. No external requests were made; use Analyze Portfolio for a fresh analysis.')
    return raw,reason
