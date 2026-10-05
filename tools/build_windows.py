"""Build only explicitly selected public source and dependency resources."""
from pathlib import Path
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]

def build():
    output=ROOT/'output/windows';output.mkdir(parents=True,exist_ok=True)
    stage_file=output/'build-stage.txt'
    stage=Path(stage_file.read_text().strip()) if stage_file.is_file() else Path(tempfile.gettempdir())/'absent'
    if stage.parent.resolve()!=Path(tempfile.gettempdir()).resolve() or not stage.is_dir():
        stage=Path(tempfile.mkdtemp(prefix='foliolens-generic-build-'))
    stage_file.write_text(str(stage))
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--log-level','WARN','--windowed','--onedir',
        '--name','Open FolioLens Personal','--icon',str(ROOT/'assets/favicon.ico'),
        '--distpath',str(stage/'portable'),'--workpath',str(stage/'build'),'--specpath',str(output),
        '--paths',str(ROOT),'--paths',str(ROOT/'vendor/v678')]
    for name in ('streamlit','yfinance','curl_cffi','altair'):
        command+=['--collect-all',name]
    for name in ('pulse.runner','pulse.adapter','pulse.pdf','pulse.html_report','pulse.reporting','pulse.design','pulse.composition','pulse.holding_performance','pulse.analytics','pulse.forward','pulse.synthetic','pulse.profile','pulse.event_review','pulse.partial','worker_hooks','psutil','bs4','requests','reportlab','pyarrow','uvicorn','websockets'):
        command+=['--hidden-import',name]
    for name in ('pytest','playwright','pymupdf','pip_audit','IPython','matplotlib','scipy'):
        command+=['--exclude-module',name]
    for source,target in [('streamlit_app.py','.'),('vendor/v678','vendor/v678'),('assets','assets'),('.streamlit/config.toml','.streamlit')]:
        command+=['--add-data',str(ROOT/source)+';'+target]
    command+=[str(ROOT/'desktop/personal.py')]
    subprocess.run(command,cwd=ROOT,check=True)
    folder=output/'portable/Open FolioLens Personal'
    shutil.copytree(stage/'portable/Open FolioLens Personal',folder,dirs_exist_ok=True)
    for unwanted in folder.rglob('__pycache__'):shutil.rmtree(unwanted)
    (folder/'READ ME.txt').write_text('FolioLens Personal\n\nDouble-click Open FolioLens Personal.exe. Your browser opens automatically.\nUpload your CSV, select a benchmark and click Analyze Portfolio.\nClose FolioLens in the small launcher window when finished.\nKeep this entire extracted folder together. No Python installation is required.\nThe unsigned executable may trigger a standard Windows security warning.\nPrivate configuration stays in your local user profile, never in this package.\nCurrent derivative quotes may be unavailable; valid performance/income still display.\n',encoding='utf-8')
    shutil.copyfile(ROOT/'LICENSE',folder/'LICENSE.txt')
    notices=folder/'Third-party licences';notices.mkdir(exist_ok=True)
    for dist in importlib.metadata.distributions():
        for file in dist.files or []:
            if any(part.lower() in {'licenses','license','licences'} for part in file.parts) or file.name.upper().startswith(('LICENSE','COPYING','NOTICE')):
                source=Path(dist.locate_file(file))
                if source.is_file() and source.stat().st_size<2*1024*1024:
                    destination=notices/dist.metadata['Name']/file.name
                    destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,destination)
    python_licence=Path(sys.base_prefix)/'LICENSE.txt'
    if python_licence.is_file():shutil.copyfile(python_licence,notices/'Python-LICENSE.txt')
    # Reject private/source-workspace paths before packaging; no reference inputs.
    assert not any(p.suffix.lower() in {'.csv','.pdf','.html','.log'} or any(part in {'.private','.venv','provider-cache'} for part in p.relative_to(folder).parts) for p in folder.rglob('*') if p.is_file() and '_internal/streamlit/static' not in p.as_posix()),'Unexpected private artifact'
    manifest={p.relative_to(folder).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob('*') if p.is_file()}
    (output/'package-manifest.json').write_text(json.dumps(manifest,indent=2))
    archive=output/'FolioLens-Personal-Windows.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as zipped:
        for p in folder.rglob('*'):
            if p.is_file():zipped.write(p,p.relative_to(folder.parent))
    (output/'SHA256SUMS.txt').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
    print(json.dumps({'package':str(archive),'files':len(manifest),'private_inputs_included':False}))

if __name__=='__main__':build()
