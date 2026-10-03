"""Profile synthetic execution only; never accepts private transaction data."""
import json
from pathlib import Path
import subprocess
import sys
import time
import psutil

if '--child' in sys.argv:
    from pulse.synthetic import fixture
    from pulse.runner import run_analysis
    data,prices=fixture()
    result=run_analysis(data,prices=prices)
    print(json.dumps({'analysis_seconds':result['runtime_seconds'],'canonical_output_bytes':len(json.dumps(result))}))
else:
    process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--child'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    peak=0
    started=time.perf_counter()
    while process.poll() is None:
        try:
            parent=psutil.Process(process.pid)
            peak=max(peak,sum(p.memory_info().rss for p in [parent,*parent.children(recursive=True)] if p.is_running()))
        except psutil.Error:pass
        time.sleep(.05)
    assert process.returncode==0
    data=json.loads(process.stdout.read())
    data.update(total_wall_seconds=time.perf_counter()-started,peak_combined_rss_mib=peak/1024**2)
    Path('.private/profile.json').write_text(json.dumps(data,indent=2))
    print(json.dumps(data,indent=2))
