"""Session-isolated canonical engine execution. Private exceptions never reach the UI."""
import csv
from contextlib import contextmanager
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from pulse.benchmarks import PRESETS, validate_ticker, benchmark_name
from pulse.resources import execute_worker, WorkerLimitError
from pulse.private_config import validate_private_config, load_private_config, personal_defaults
from pulse.mode import uploads_enabled

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 10000
MAX_SECURITIES = 150

def worker_environment():
    """Pass runtime paths, not arbitrary application credentials, to workers."""
    allowed={'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','TMPDIR','COMSPEC','PATHEXT',
             'USERPROFILE','HOMEDRIVE','HOMEPATH','LOCALAPPDATA','APPDATA','PROGRAMDATA',
             'PROGRAMFILES','PROGRAMFILES(X86)','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS',
             'LANG','LC_ALL','TZ','SSL_CERT_FILE','SSL_CERT_DIR','REQUESTS_CA_BUNDLE'}
    return {key:value for key,value in os.environ.items() if key.upper() in allowed}

class AnalysisError(ValueError):
    pass

@contextmanager
def analysis_workspace():
    directory=tempfile.TemporaryDirectory(prefix='portfolio-pulse-')
    try:
        yield Path(directory.name)
    finally:
        # Windows indexing/sync tools can briefly hold a just-closed directory.
        # Retry cleanup; never ignore a persistent private-data cleanup failure.
        for attempt in range(8):
            try:
                directory.cleanup()
                break
            except PermissionError:
                if attempt==7:
                    raise AnalysisError('Temporary data cleanup failed. The server operator must review temporary storage before accepting more uploads.') from None
                time.sleep(min(.05*2**attempt,.5))

def validate_upload(data):
    if not data or len(data) > MAX_BYTES:
        raise AnalysisError('Choose a nonempty CSV no larger than 5 MB.')
    try:
        text = data.decode('utf-8-sig')
        if '\x00' in text:
            raise AnalysisError('The file contains unsupported binary content.')
        reader = csv.DictReader(StringIO(text),strict=True)
        from pulse.core import validate_export_columns
        from portfolio_core import canonical_column_mapping
        check = validate_export_columns(reader.fieldnames or [])
        if not check.valid or len(reader.fieldnames or [])>64 or len(set(reader.fieldnames or []))!=len(reader.fieldnames or []):
            raise AnalysisError('Unsupported CSV columns. Use the documented Trade Republic transaction export format.')
        rows=[]
        for item in reader:
            rows.append(item)
            if len(rows)>MAX_ROWS:
                raise AnalysisError('The export exceeds the 10,000-row limit.')
        if not rows or len(rows) > MAX_ROWS or any(None in item or any(v is None or len(str(v)) > 2000 for v in item.values()) for item in rows):
            raise AnalysisError('The CSV is empty, malformed or exceeds the 10,000-row / field-size limits.')
        mapping=canonical_column_mapping(reader.fieldnames)
        rows=[{mapping.get(k,k):v for k,v in item.items()} for item in rows]
        import math
        for item in rows:
            for key in ('amount','fee','tax','shares','price','fx_rate'):
                value=str(item.get(key,'') or '').strip()
                if not value or value.lower() in {'nan','none','null'}:continue
                try: numeric=float(value)
                except ValueError:continue  # Canonical normalization diagnoses unsupported numeric representations.
                if not math.isfinite(numeric) or abs(numeric)>1e15:
                    raise AnalysisError('The CSV contains a nonfinite or out-of-range financial number.')
        if len({str(item.get('symbol','')).strip().upper() for item in rows if str(item.get('asset_class','')).strip().upper() in {'STOCK','FUND','DERIVATIVE'}}) > MAX_SECURITIES:
            raise AnalysisError('This export exceeds the 150-security processing limit.')
        import pandas as pd
        dates = pd.to_datetime([item.get('date') or item.get('datetime') for item in rows],errors='coerce',utc=True)
        if dates.isna().any() or (dates.max()-dates.min()).days > 3653:
            raise AnalysisError('Valid dates and at most ten years of history are required.')
    except (UnicodeError,csv.Error):
        raise AnalysisError('The file must be a valid UTF-8 CSV.') from None

def run_analysis(data, *, benchmark='IWDA.AS', prices=None, timeout=300, engine_path=None, private_config=None, replay=None, capture=False):
    if prices is None and replay is None and not uploads_enabled():
        raise AnalysisError('Real market-data processing requires the explicitly enabled local personal-use workflow or verified owner deployment.')
    if benchmark is not None:
        try:
            benchmark = validate_ticker(benchmark)
        except ValueError as exc:
            raise AnalysisError(str(exc)) from None
        if prices is not None and benchmark not in prices.get('benchmarks', {'IWDA.AS': prices.get('benchmark')}):
            raise AnalysisError('This benchmark has no explicitly defined synthetic data. Custom tickers are local personal-use only.')
        if prices is None and benchmark not in PRESETS and not uploads_enabled():
            raise AnalysisError('Custom benchmarks require the explicitly enabled local personal-use workflow.')
    validate_upload(data)
    try:
        if replay is None and prices is None and os.environ.get('FOLIOLENS_PERSONAL_REPLAY'):
            profile=Path(os.environ['FOLIOLENS_PERSONAL_REPLAY'])
            if profile.stat().st_size>64*1024*1024:
                raise ValueError('Captured input exceeds limit')
            replay=json.loads(profile.read_text(encoding='utf-8'))
        if private_config is None and prices is None and os.environ.get('FOLIOLENS_PRIVATE_CONFIG'):
            private_config=load_private_config(os.environ['FOLIOLENS_PRIVATE_CONFIG'],data)
        if private_config is None and prices is None:
            private_config=personal_defaults(data) or None
        private_config=validate_private_config(private_config,data)
    except (ValueError,TypeError,OSError):
        raise AnalysisError('Private event/valuation configuration is invalid or belongs to another export. Review its binding, dates and sources.') from None
    started = time.perf_counter()
    with analysis_workspace() as work:
        work.joinpath('input.csv').write_bytes(data)
        env = worker_environment()
        env.update(PULSE_RESULT_PATH=str(work/'result.json'),PYTHONIOENCODING='utf-8',
                   TR_SECURITY_METADATA_CACHE_PATH=str(work/'metadata.json'),
                   PYTHONPATH=os.pathsep.join([str(ROOT),str(ROOT/'vendor'/'v678')]))
        env.pop('OPENFIGI_API_KEY',None)
        env.pop('PULSE_PRICE_INPUT',None)
        env.pop('FOLIOLENS_REPLAY_PATH',None)
        env.pop('FOLIOLENS_WORKER_CONFIG',None)
        # Workers inherit no parent private configuration path or application secrets.
        env.pop('FOLIOLENS_PRIVATE_CONFIG',None)
        env.pop('FOLIOLENS_PERSONAL_REPLAY',None)
        work.joinpath('private-config.json').write_text(json.dumps(private_config),encoding='utf-8')
        env['FOLIOLENS_WORKER_CONFIG']=str(work/'private-config.json')
        if replay is not None:
            work.joinpath('replay.json').write_text(json.dumps(replay),encoding='utf-8')
            env['FOLIOLENS_REPLAY_PATH']=str(work/'replay.json')
        env['FOLIOLENS_CAPTURE']='1' if capture else '0'
        env['PULSE_BENCHMARK_DISABLED'] = '1' if benchmark is None else '0'
        if prices is not None:
            work.joinpath('prices.json').write_text(json.dumps(prices))
            env['PULSE_PRICE_INPUT'] = str(work/'prices.json')
        command = [sys.executable,*(['--worker'] if getattr(sys,'frozen',False) else []),str(engine_path or ROOT/'vendor'/'v678'/'analysis_engine.py'),
                   '--input-csv',str(work/'input.csv'),'--output-root',str(work/'engine'),
                   '--manifest',str(work/'manifest.json'),'--no-enable-dividend-growth',
                   '--no-enable-derivative-quotes','--benchmark-ticker',benchmark or 'FOLIOLENS_DISABLED',
                   '--benchmark-name',benchmark_name(benchmark, prices is not None),
                   '--no-export-raw','--no-export-redacted']
        try:
            returncode=execute_worker(command,work,env,timeout)
        except WorkerLimitError as exc:
            raise AnalysisError(str(exc)) from None
        if returncode or not (work/'result.json').exists():
            raise AnalysisError('Analysis could not validate this export. Check its format and accounting completeness.')
        result = json.loads(work.joinpath('result.json').read_text(encoding='utf-8'))
    result['runtime_seconds'] = time.perf_counter()-started
    result['synthetic'] = prices is not None
    result['benchmark_enabled'] = benchmark is not None
    result['benchmark_name'] = result.get('benchmark_identity',{}).get('name') or benchmark_name(benchmark, prices is not None)
    result['benchmark_ticker'] = benchmark
    return result
