import csv
import hashlib
import json
from io import StringIO
import os
from pathlib import Path
import socket
from types import SimpleNamespace
import pytest
from desktop.personal import free_port,personal_environment,server_command
from pulse.private_config import personal_defaults,export_digest
from pulse.synthetic import fixture
from pulse.runner import run_analysis

def test_personal_launch_is_loopback_and_has_no_replay(monkeypatch):
    monkeypatch.setenv('FOLIOLENS_PERSONAL_REPLAY','fictional-path')
    monkeypatch.setenv('FOLIOLENS_PRIVATE_CONFIG','fictional-path')
    env=personal_environment()
    assert env['FOLIOLENS_MODE']=='personal'
    assert 'FOLIOLENS_PERSONAL_REPLAY' not in env and 'FOLIOLENS_PRIVATE_CONFIG' not in env
    port=free_port()
    with socket.socket() as sock:sock.bind(('127.0.0.1',port))
    assert server_command(port)[-2:]==['--server',str(port)]

def test_exact_private_registry_revalidates_future_export(tmp_path,monkeypatch):
    data,prices=fixture('unsupported_action')
    row=next(r for r in csv.DictReader(StringIO(data.decode())) if r['type']=='FREE_RECEIPT')
    spec=dict(event_id='FICTIONAL_CONFIRMED_EVENT',transaction_id_sha256=hashlib.sha256(row['transaction_id'].encode()).hexdigest(),event_date=row['date'],isin=row['symbol'],security_name=row['name'],category=row['category'],asset_class=row['asset_class'],broker_type=row['type'],quantity=float(row['shares']),description=row['description'])
    (tmp_path/'verified-events.json').write_text(json.dumps([spec]))
    monkeypatch.setenv('FOLIOLENS_CONFIG_DIRECTORY',str(tmp_path))
    defaults=personal_defaults(data)
    assert defaults['export_sha256']==export_digest(data)
    result=run_analysis(data,prices=prices,private_config=defaults)
    assert result['accounting_status']=='COMPLETE' and len(result['worthless_derecognition_events'])==1
    changed=data.replace(row['transaction_id'].encode(),b'UNRECOGNIZED_TEST_ID')
    result=run_analysis(changed,prices=prices,private_config=personal_defaults(changed))
    assert result['accounting_status']=='BLOCKING' and not result['worthless_derecognition_events']
    changed=data.replace(row['description'].encode(),b'Changed event evidence')
    assert run_analysis(changed,prices=prices,private_config=personal_defaults(changed))['accounting_status']=='BLOCKING'

def test_export_configuration_does_not_transfer(tmp_path,monkeypatch):
    (tmp_path/'exports').mkdir();data,_=fixture('stocks');other,_=fixture('etf_only')
    config={'export_sha256':export_digest(data),'worthless_confirmations':[]}
    (tmp_path/'exports'/(export_digest(data)+'.json')).write_text(json.dumps(config))
    monkeypatch.setenv('FOLIOLENS_CONFIG_DIRECTORY',str(tmp_path))
    assert personal_defaults(data)==config
    assert personal_defaults(other)=={'export_sha256':export_digest(other)}

def test_frozen_worker_failure_does_not_expose_exception(monkeypatch,capsys):
    import sys,runpy
    from desktop.personal import main
    monkeypatch.setattr(sys,'argv',['launcher','--worker','fictional_engine.py'])
    def failure(*args,**kwargs):raise ValueError('FICTIONAL_PRIVATE_PAYLOAD')
    monkeypatch.setattr(runpy,'run_path',failure)
    with pytest.raises(SystemExit) as error:main()
    assert error.value.code==1
    captured=capsys.readouterr()
    assert not captured.out and not captured.err

def test_hot_deployment_refreshes_old_private_helper(monkeypatch):
    import pulse.private_config as module
    from streamlit.testing.v1 import AppTest
    monkeypatch.delattr(module,'personal_defaults')
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py'),default_timeout=30).run()
    assert not app.exception
    assert hasattr(module,'personal_defaults')
