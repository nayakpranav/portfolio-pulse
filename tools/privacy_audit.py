"""Scan only Git-tracked public content. No reference data is read or printed."""
import re
from pathlib import Path
import subprocess
import sys
import json

ROOT=Path(__file__).resolve().parents[1]
def audit():
    files=subprocess.check_output(['git','-c','safe.directory='+ROOT.as_posix(),'ls-files','-z'],cwd=ROOT).decode().split('\0')
    denied_extensions={'.pdf','.html','.xlsx','.zip','.pem','.key','.pickle','.pkl'}
    rules={
        'broker_uuid':re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b',re.I),
        'credential':re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16}|-----BEGIN .*PRIVATE KEY-----)'),
        'personal_local_path':re.compile(r'(?:[A-Z]:[\\/](?:Users|Dropbox)|/Users/|/home/[^/]+/)',re.I),
    }
    findings=[]
    scanned=0
    for name in filter(None,files):
        if name=='tools/privacy_audit.py':continue  # Scanner rules themselves contain deny-list literals.
        path=ROOT/name
        if path.suffix.lower() in denied_extensions or any(part in {'.private','.venv','output','tmp'} for part in path.parts):
            findings.append({'file':name,'reason':'private/artifact file type or directory'})
        if name=='assets/favicon.png':
            continue  # Original generated branding asset, no user input.
        text=path.read_text(encoding='utf-8');scanned+=1
        for label,pattern in rules.items():
            if pattern.search(text):findings.append({'file':name,'reason':label})
    import ast
    tree=ast.parse((ROOT/'vendor/v678/security_events.py').read_text(encoding='utf-8'))
    registry=next(n.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='KNOWN_WORTHLESS_DERECOGNITIONS' for t in n.targets))
    if ast.literal_eval(registry)!=():findings.append({'file':'vendor/v678/security_events.py','reason':'nonempty event registry'})
    result={'status':'PASS' if not findings else 'FAIL','tracked_text_files_scanned':scanned,'findings':findings}
    print(json.dumps(result,indent=2))
    return not findings

if __name__=='__main__':sys.exit(0 if audit() else 1)
