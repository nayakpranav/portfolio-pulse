"""Local personal evidence store, separate from application binaries and CSVs."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time

_lock = threading.RLock()  # Synchronizes writes only; never stores portfolio data.


def profile_root():
    # AppData can be virtualized when setup runs from a packaged Windows app.
    # USERPROFILE is shared by setup, Explorer launches and frozen workers.
    return Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'FolioLensPersonal'


def configured_root():
    from pulse.mode import mode, uploads_enabled
    if mode() != 'personal' or not uploads_enabled():
        return None
    return Path(os.environ['FOLIOLENS_CONFIG_DIRECTORY']) if os.environ.get('FOLIOLENS_CONFIG_DIRECTORY') else profile_root()


def _protect(data, decrypt=False):
    if os.name != 'nt':
        return data
    import ctypes
    from ctypes import wintypes as w
    class Blob(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,w.DWORD,ctypes.POINTER(Blob)]
    operation.restype=w.BOOL
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ValueError('Private profile protection is unavailable.')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel=ctypes.WinDLL('kernel32')
        kernel.LocalFree.argtypes=[ctypes.c_void_p]
        kernel.LocalFree.restype=ctypes.c_void_p
        kernel.LocalFree(result.data)


def read_records(root):
    root = Path(root)
    primary, backup = root/'verified-events.dat', root/'verified-events.backup.dat'
    for path in (primary, backup):
        if not path.exists():
            continue
        try:
            if path.stat().st_size > 131072:
                raise ValueError('Private registry exceeds limit.')
            records = json.loads(_protect(path.read_bytes(), decrypt=True))
            if not isinstance(records, list) or len(records) > 100:
                raise ValueError('Invalid private registry.')
            from pulse import core
            from security_events import KnownWorthlessDerecognition
            import math
            for record in records:
                if not isinstance(record,dict) or set(record)!={'evidence','prior_activity_sha256'}:
                    raise ValueError('Invalid private registry.')
                evidence=KnownWorthlessDerecognition(**record['evidence'])
                for digest in (record['prior_activity_sha256'],evidence.transaction_id_sha256):
                    if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
                        raise ValueError('Invalid private registry.')
                if not math.isfinite(float(evidence.quantity)) or float(evidence.quantity)>=0:
                    raise ValueError('Invalid private registry.')
            if path == backup:
                _atomic(primary, path.read_bytes())
            return records
        except (OSError, ValueError, TypeError):
            continue
    if primary.exists() or backup.exists():
        raise ValueError('Private verified evidence could not be recovered. Accounting remains subject to review.')
    return []


def _atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.evidence-', dir=path.parent)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                break
            except OSError as error:
                if attempt==7 or error.errno not in {13,22}:
                    raise
                time.sleep(min(.05*2**attempt,.5))
    finally:
        Path(temporary).unlink(missing_ok=True)


def store_records(root, records):
    root = Path(root)
    if len(records) > 100:
        raise ValueError('Private registry exceeds limit.')
    protected = _protect(json.dumps(records, allow_nan=False).encode())
    with _lock:
        _atomic(root/'verified-events.backup.dat', protected)
        _atomic(root/'verified-events.dat', protected)


def merge_records(root, additions):
    with _lock:
        records = read_records(root)
        for record in additions:
            if record not in records:
                records.append(record)
        store_records(root, records)
