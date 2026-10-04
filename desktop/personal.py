"""Generic Windows GUI; user configurations and portfolios are never bundled."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import tempfile
import time
import urllib.request
import webbrowser

ROOT=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]

def personal_environment():
    env=os.environ.copy()
    env['FOLIOLENS_MODE']='personal'
    env['FOLIOLENS_CONFIG_DIRECTORY']=str(Path(os.environ.get('LOCALAPPDATA',Path.home()))/'FolioLensPersonal')
    # Ordinary startup never reuses acceptance captures or old manual inputs.
    for key in ('PULSE_ENABLE_UPLOADS','FOLIOLENS_PRIVATE_CONFIG','FOLIOLENS_PERSONAL_REPLAY'):
        env.pop(key,None)
    return env

def server_command(port):
    prefix=[sys.executable,'--server'] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).resolve()),'--server']
    return prefix+[str(port)]

def stop_server(process):
    import psutil
    if process and process.poll() is None:
        try:
            parent=psutil.Process(process.pid)
            for child in parent.children(recursive=True):child.kill()
            parent.kill()
        except (psutil.NoSuchProcess,psutil.AccessDenied):pass
        process.wait(timeout=10)

def graphical_main():
    import tkinter as tk
    from tkinter import ttk
    gui=tk.Tk();gui.title('FolioLens Personal');gui.geometry('420x220');gui.resizable(False,False)
    try:gui.iconbitmap(str(ROOT/'assets/favicon.ico'))
    except tk.TclError:pass
    frame=ttk.Frame(gui,padding=22);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='FolioLens Personal',font=('Segoe UI',18,'bold')).pack(anchor='w')
    ttk.Label(frame,text='Your investments, in focus.').pack(anchor='w',pady=(2,12))
    status=tk.StringVar(value='Starting your private application…')
    ttk.Label(frame,textvariable=status,wraplength=370).pack(anchor='w')
    holder={'process':None,'url':None,'closing':False,'workspace':None}
    def open_browser():
        if holder['url']:webbrowser.open(holder['url'])
    def close():
        holder['closing']=True;stop_server(holder['process'])
        if holder['workspace']:
            try:holder['workspace'].cleanup()
            except OSError:
                from tkinter import messagebox
                messagebox.showwarning('FolioLens Personal','Temporary cleanup could not finish. Reopen the application and contact the operator before further analysis.')
        gui.destroy()
    buttons=ttk.Frame(frame);buttons.pack(fill='x',pady=14)
    open_button=ttk.Button(buttons,text='Open in browser',command=open_browser,state='disabled');open_button.pack(side='left')
    ttk.Button(buttons,text='Close FolioLens',command=close).pack(side='right')
    gui.protocol('WM_DELETE_WINDOW',close)
    def ready(url):
        if holder['closing']:return
        holder['url']=url;status.set('Ready. Upload your CSV in the browser. Close here when finished.');open_button.configure(state='normal');open_browser()
    def start():
        try:
            port=free_port();url=f'http://127.0.0.1:{port}'
            env=personal_environment()
            runtime=Path(env['FOLIOLENS_CONFIG_DIRECTORY'])/'runtime';runtime.mkdir(parents=True,exist_ok=True)
            workspace=tempfile.TemporaryDirectory(prefix='session-',dir=runtime);holder['workspace']=workspace
            env.update(TEMP=workspace.name,TMP=workspace.name,TMPDIR=workspace.name)
            process=subprocess.Popen(server_command(port),cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            holder['process']=process
            for _ in range(120):
                if holder['closing']:stop_server(process);return
                if process.poll() is not None:raise RuntimeError('Startup stopped')
                try:
                    with urllib.request.urlopen(url+'/_stcore/health',timeout=1) as response:
                        if response.status==200:gui.after(0,ready,url);return
                except OSError:time.sleep(.5)
            raise RuntimeError('Startup timeout')
        except Exception:
            stop_server(holder['process'])
            if not holder['closing']:gui.after(0,status.set,'Startup could not finish. Close and reopen the application. Keep the extracted package together.')
    threading.Thread(target=start,daemon=True).start()
    gui.mainloop()

def main():
    # Windowed Python has no console streams; providers never log private payloads.
    for name in ('stdin','stdout','stderr'):
        if getattr(sys,name) is None:setattr(sys,name,open(os.devnull,'r' if name=='stdin' else 'w'))
    if len(sys.argv)>1 and sys.argv[1]=='--worker':
        import runpy
        engine=sys.argv[2];sys.argv=sys.argv[2:]
        sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'vendor/v678'))
        try:runpy.run_path(engine,run_name='__main__')
        except Exception:raise SystemExit(1) from None
        return
    if len(sys.argv)>1 and sys.argv[1]=='--server':
        from streamlit.web import cli
        sys.argv=['streamlit','run',str(ROOT/'streamlit_app.py'),'--global.developmentMode','false','--server.address','127.0.0.1','--server.port',sys.argv[2],'--server.headless','true','--browser.gatherUsageStats','false']
        try:cli.main()
        except Exception:raise SystemExit(1) from None
        return
    if len(sys.argv)>1 and sys.argv[1]=='--self-test':
        import json
        from pulse.runner import run_analysis
        from pulse.synthetic import fixture
        from pulse.adapter import prepare
        from pulse.pdf import summary_pdf
        data,prices=fixture();model=prepare(run_analysis(data,prices=prices))
        report={'synthetic_worker':True,'metrics':len(model['metrics']),'pdf':summary_pdf(model).startswith(b'%PDF'),'personal_mode':personal_environment()['FOLIOLENS_MODE']=='personal'}
        data,prices=fixture('open_derivatives')
        scoped=prepare(run_analysis(data,prices=prices),analysis_scope='stocks_funds')
        report.update(stock_fund_scope_metrics=sum(m.value is not None for m in scoped['metrics']),
                      unpriced_derivatives=scoped['dependencies']['missing_derivative'],
                      full_portfolio_partial=all(v is None for v in scoped['full_totals'].values()),
                      scoped_pdf=summary_pdf(scoped).startswith(b'%PDF'))
        assert report['stock_fund_scope_metrics']==8 and report['unpriced_derivatives']==7 and report['full_portfolio_partial'] and report['scoped_pdf']
        Path(sys.argv[2]).write_text(json.dumps(report));return
    graphical_main()

if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()
