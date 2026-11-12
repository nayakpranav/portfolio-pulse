"""Deterministic no-action regression against the protected production commit.

Uses fabricated inputs only. The separate source tree is temporary; no checkout,
private profile, market request or production installation is modified.
"""
from datetime import date
import ast
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

from pulse.adapter import prepare
from pulse.runner import run_analysis
from pulse.synthetic import fixture

BASELINE = 'd631b7c50b22619023de9e820198e39f6b97e6de'
CASES = ('demo', 'etf_only', 'realized_sales', 'reinvestment', 'unsupported_action',
         'missing_price', 'short_history', 'incomplete_accounting', 'derivatives')
ROOT = Path(__file__).resolve().parents[1]


def common_values(old, new, path=''):
    if isinstance(old, dict):
        assert isinstance(new, dict), path
        assert not old.keys() - new.keys(), path
        for key in new.keys() - old.keys():
            assert key == 'predecessor_history_provenance' and new[key] == '', path
        for key in old:
            common_values(old[key], new[key], path + '/' + key)
    elif isinstance(old, list):
        assert isinstance(new, list) and len(old) == len(new), path
        for index, (a, b) in enumerate(zip(old, new)):
            common_values(a, b, path + '/' + str(index))
    else:
        assert old == new, path


def compare():
    command = ['git', '-c', 'safe.directory=' + ROOT.as_posix()]
    archive = subprocess.check_output(command + ['archive', BASELINE], cwd=ROOT)
    findings = []
    with tempfile.TemporaryDirectory(prefix='foliolens-baseline-') as directory:
        baseline = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(baseline, filter='data')
        for kind in CASES:
            data, prices = fixture(kind)
            old = run_analysis(data, prices=prices, engine_path=baseline / 'vendor/v678/analysis_engine.py')
            new = run_analysis(data, prices=prices)
            for key in old.keys() - {'runtime_seconds', 'performance'}:
                common_values(old[key], new[key], kind + '/' + key)
            a, b = (prepare(r, date(2026, 10, 3)) for r in (old, new))
            assert a['metrics'] == b['metrics'], kind
            assert a['snapshot'] == b['snapshot'], kind
            findings.append(dict(scenario=kind, status='PASS'))
        definitions = lambda path: {n.name: ast.dump(n, include_attributes=False)
            for n in ast.parse(path.read_text(encoding='utf-8')).body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        old = definitions(baseline / 'vendor/v678/analysis_engine.py')
        new = definitions(ROOT / 'vendor/v678/analysis_engine.py')
        changed = [name for name in old if old[name] != new.get(name)]
    return dict(baseline=BASELINE, scenarios=findings, retained_engine_definitions=len(old),
        unchanged_engine_definitions=len(old)-len(changed), compatibility_extensions=changed,
        allowed_addition='Empty predecessor-history provenance on no-action histories')


if __name__ == '__main__':
    print(json.dumps(compare(), indent=2))
