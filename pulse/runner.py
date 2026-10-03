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

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 10000

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
        reader = csv.DictReader(StringIO(text))
        from pulse.core import validate_export_columns
        from portfolio_core import canonical_column_mapping
        check = validate_export_columns(reader.fieldnames or [])
        if not check.valid:
            raise AnalysisError('Unsupported CSV columns. Use the documented Trade Republic transaction export format.')
        rows = list(reader)
        if not rows or len(rows) > MAX_ROWS or any(None in item or any(len(str(v)) > 2000 for v in item.values()) for item in rows):
            raise AnalysisError('The CSV is empty, malformed or exceeds the 10,000-row / field-size limits.')
        mapping=canonical_column_mapping(reader.fieldnames)
        rows=[{mapping.get(k,k):v for k,v in item.items()} for item in rows]
        if len({item.get('symbol') for item in rows if item.get('asset_class') in {'STOCK','FUND','DERIVATIVE'}}) > 100:
            raise AnalysisError('This export exceeds the 100-security processing limit.')
        import pandas as pd
        dates = pd.to_datetime([item.get('date') or item.get('datetime') for item in rows],errors='coerce',utc=True)
        if dates.isna().any() or (dates.max()-dates.min()).days > 3653:
            raise AnalysisError('Valid dates and at most ten years of history are required.')
    except (UnicodeError,csv.Error):
        raise AnalysisError('The file must be a valid UTF-8 CSV.') from None

def run_analysis(data, *, benchmark='IWDA.AS', prices=None, timeout=120, engine_path=None):
    if benchmark is not None:
        try:
            benchmark = validate_ticker(benchmark)
        except ValueError as exc:
            raise AnalysisError(str(exc)) from None
        if prices is not None and benchmark not in prices.get('benchmarks', {'IWDA.AS': prices.get('benchmark')}):
            raise AnalysisError('This benchmark has no explicitly defined synthetic data. Custom tickers are local personal-use only.')
        if prices is None and benchmark not in PRESETS and os.environ.get('PULSE_ENABLE_UPLOADS') != '1':
            raise AnalysisError('Custom benchmarks require the explicitly enabled local personal-use workflow.')
    validate_upload(data)
    started = time.perf_counter()
    with analysis_workspace() as work:
        work.joinpath('input.csv').write_bytes(data)
        env = os.environ.copy()
        env.update(PULSE_RESULT_PATH=str(work/'result.json'),PYTHONIOENCODING='utf-8',
                   TR_SECURITY_METADATA_CACHE_PATH=str(work/'metadata.json'),
                   PYTHONPATH=os.pathsep.join([str(ROOT),str(ROOT/'vendor'/'v678')]))
        env.pop('OPENFIGI_API_KEY',None)
        env.pop('PULSE_PRICE_INPUT',None)
        env['PULSE_BENCHMARK_DISABLED'] = '1' if benchmark is None else '0'
        if prices is not None:
            work.joinpath('prices.json').write_text(json.dumps(prices))
            env['PULSE_PRICE_INPUT'] = str(work/'prices.json')
        command = [sys.executable,str(engine_path or ROOT/'vendor'/'v678'/'analysis_engine.py'),
                   '--input-csv',str(work/'input.csv'),'--output-root',str(work/'engine'),
                   '--manifest',str(work/'manifest.json'),'--no-enable-dividend-growth',
                   '--no-enable-derivative-quotes','--benchmark-ticker',benchmark or 'IWDA.AS',
                   '--benchmark-name',benchmark_name(benchmark, prices is not None),
                   '--no-export-raw','--no-export-redacted']
        kwargs = {'creationflags':subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        try:
            completed = subprocess.run(command,cwd=work,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=timeout,**kwargs)
        except subprocess.TimeoutExpired:
            raise AnalysisError('Analysis reached the processing limit. Try a smaller export or retry when market data is available.') from None
        if completed.returncode or not (work/'result.json').exists():
            raise AnalysisError('Analysis could not validate this export. Check its format and accounting completeness.')
        result = json.loads(work.joinpath('result.json').read_text(encoding='utf-8'))
    result['runtime_seconds'] = time.perf_counter()-started
    result['synthetic'] = prices is not None
    result['benchmark_enabled'] = benchmark is not None
    result['benchmark_name'] = benchmark_name(benchmark, prices is not None)
    result['benchmark_ticker'] = benchmark
    return result
