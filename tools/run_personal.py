"""Launch the full personal workflow on loopback, never on a public interface."""
import os
from pathlib import Path
import subprocess
import sys

if __name__=='__main__':
    env=os.environ.copy()
    env['FOLIOLENS_MODE']='personal'
    root=Path(__file__).resolve().parents[1]
    raise SystemExit(subprocess.call([sys.executable,'-m','streamlit','run',str(root/'streamlit_app.py'),
        '--server.address','127.0.0.1','--server.headless','true'],cwd=root,env=env))
