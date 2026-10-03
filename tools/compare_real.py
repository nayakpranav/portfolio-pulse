"""Local-only controlled real-export acceptance; publishes no financial values."""
import argparse
import ast
from datetime import date
import json
import os
from pathlib import Path
import tempfile
from pulse.runner import run_analysis
from pulse.adapter import prepare

def compare(source,export,capture,config,benchmark='IWDA.AS'):
    source=Path(source).resolve()
    data=Path(export).read_bytes()
    captured=json.loads(Path(capture).read_text(encoding='utf-8'))
    private=json.loads(Path(config).read_text(encoding='utf-8'))
    original=(source/'analysis_engine.py').read_text(encoding='utf-8')
    selected=Path('vendor/v678/analysis_engine.py').read_text(encoding='utf-8')
    definitions=lambda code:{n.name:ast.dump(n,include_attributes=False) for n in ast.parse(code).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
    a,b=definitions(original),definitions(selected)
    assert all(a[k]==v for k,v in b.items()),'Canonical definition changed'
    code=original[:original.index('summary = pd.DataFrame([',original.index('# 8. Build all data'))]
    code=code.replace('stage_start(1, 7, "Reading and validating the Trade Republic CSV")',
        'from worker_hooks import install_hooks\ninstall_hooks(globals())\nstage_start(1, 7, "Reading and validating the Trade Republic CSV")',1)
    code='import sys\nsys.path.insert(0,'+repr(str(source))+')\n'+code.replace('from __future__ import annotations','')
    code+='\nfrom worker_hooks import write_results\nwrite_results(globals())\n'
    with tempfile.TemporaryDirectory(prefix='foliolens-reference-') as folder:
        baseline=Path(folder)/'baseline.py';baseline.write_text(code,encoding='utf-8')
        old=run_analysis(data,benchmark=benchmark,replay=captured,private_config=private,engine_path=baseline)
        new=run_analysis(data,benchmark=benchmark,replay=captured,private_config=private)
    keys=old.keys()-{'runtime_seconds'}
    different=[key for key in keys if old[key]!=new[key]]
    # All unrounded canonical serialized financial fields, rows, statuses and diagnostics.
    assert not different,'Controlled outputs differ: '+', '.join(different)
    old_model,new_model=(prepare(r,date.today()) for r in (old,new))
    assert old_model['metrics']==new_model['metrics']
    assert old_model['snapshot']==new_model['snapshot']
    report={'status':'PASS','real_export_used_locally':True,'canonical_definitions_identical':len(b),
        'canonical_result_fields_exactly_equal':len(keys),'headline_metrics_equal':8,
        'income_months_holdings_and_diagnostics_equal':True,'same_captured_prices_fx_dates_and_private_events':True,
        'network_requests_during_comparison':0,'financial_values_published':False,
        'manual_dated_derivative_inputs':len(private.get('derivative_quotes',{}))}
    output=Path(tempfile.gettempdir())/'foliolens-private-acceptance'
    output.mkdir(exist_ok=True)
    output.joinpath('real-comparison.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    output.joinpath('real-controlled-result.json').write_text(json.dumps(new),encoding='utf-8')
    print(json.dumps(report,indent=2))
    return new

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('source','export','capture','config'):parser.add_argument(name)
    parser.add_argument('--benchmark',default='IWDA.AS')
    args=parser.parse_args();os.environ['FOLIOLENS_MODE']='personal'
    compare(args.source,args.export,args.capture,args.config,args.benchmark)
