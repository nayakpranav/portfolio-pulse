"""Opt-in local comparison to supplied V6.7.8. Never stores reference data in Git."""
import ast
from datetime import date
import json
import os
from pathlib import Path
import sys
import tempfile
from pulse.runner import run_analysis
from pulse.synthetic import fixture
from pulse.adapter import prepare

def compare(source):
    source=Path(source).resolve()
    original=(source/'analysis_engine.py').read_text(encoding='utf-8')
    selected=Path('vendor/v678/analysis_engine.py').read_text(encoding='utf-8')
    a={n.name:ast.dump(n,include_attributes=False) for n in ast.parse(original).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
    b={n.name:ast.dump(n,include_attributes=False) for n in ast.parse(selected).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
    assert all(a[k]==v for k,v in b.items()),'A retained canonical function differs'
    code=original[:original.index('summary = pd.DataFrame([',original.index('# 8. Build all data'))]
    # Baseline functions and full canonical pipeline are unmodified. Only public
    # price inputs, output serialization and run-local cache paths are injected.
    code=code.replace('stage_start(1, 7, "Reading and validating the Trade Republic CSV")',
        'from worker_hooks import install_hooks\ninstall_hooks(globals())\nstage_start(1, 7, "Reading and validating the Trade Republic CSV")',1)
    code='import sys\nsys.path.insert(0,'+repr(str(source))+')\n'+code.replace('from __future__ import annotations','')
    code+='\nfrom worker_hooks import write_results\nwrite_results(globals())\n'
    findings=[]
    with tempfile.TemporaryDirectory(prefix='pulse-reference-compare-') as folder:
        baseline=Path(folder)/'baseline.py';baseline.write_text(code,encoding='utf-8')
        for kind in ('demo','etf_only','realized_sales','reinvestment','unsupported_action','missing_price','short_history','incomplete_accounting','derivatives'):
            data,prices=fixture(kind)
            old=run_analysis(data,prices=prices,engine_path=baseline)
            new=run_analysis(data,prices=prices)
            for key in old.keys()-{'runtime_seconds'}:
                assert old[key]==new[key],f'{kind}: {key} differs'
            old_model,new_model=(prepare(r,date(2026,10,3)) for r in (old,new))
            assert old_model['metrics']==new_model['metrics']
            assert old_model['snapshot']==new_model['snapshot']
            assert old_model['insights']==new_model['insights']
            findings.append({'fixture':kind,'status':'PASS','comparison':'Exact unrounded canonical JSON values, all exported status/provenance fields and presentation inputs'})
    report={'canonical_definitions_identical':len(b),'baseline':'V6.7.8','scenarios':findings,
            'private_data_used':False,'baseline_functions_modified':False}
    Path('.private').mkdir(exist_ok=True)
    Path('.private/comparison.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))

if __name__=='__main__':compare(sys.argv[1])
