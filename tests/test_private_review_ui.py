"""GUI controls exercised using fabricated exports and isolated private profiles."""
from copy import deepcopy
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import pytest
from streamlit.testing.v1 import AppTest
from pulse.adapter import prepare
from pulse.runner import run_analysis
from pulse.synthetic import fixture
from pulse.private_config import export_digest
from pulse.valuations import saved_quotes
from pulse.html_report import html_report,HTML_SCHEMA_VERSION
from pulse.pdf import PDF_SCHEMA_VERSION


@pytest.fixture(scope='module')
def example():
    data,prices=fixture('open_derivatives')
    return data,run_analysis(data,prices=prices)


def app_for(example,monkeypatch,tmp_path):
    data,raw=example
    monkeypatch.setenv('FOLIOLENS_MODE','personal');monkeypatch.setenv('FOLIOLENS_CONFIG_DIRECTORY',str(tmp_path))
    monkeypatch.delenv('IS_STREAMLIT_CLOUD',raising=False);monkeypatch.delenv('STREAMLIT_SHARING_MODE',raising=False)
    monkeypatch.setattr('pulse.valuations.today',lambda:date(2026,9,30))
    import streamlit as st
    monkeypatch.setattr(st,'file_uploader',lambda *args,**kwargs:SimpleNamespace(getvalue=lambda:data))
    current=deepcopy(raw);current['synthetic']=False
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py'),default_timeout=30)
    for key,value in dict(model=prepare(current,date(2026,9,30)),input_digest=export_digest(data),pdf=b'initial',html=b'initial',export_schema=(PDF_SCHEMA_VERSION,HTML_SCHEMA_VERSION)).items():app.session_state[key]=value
    app.run();assert not app.exception
    return app


def button(app,label):
    return next(b for b in app.button if b.label==label)


def test_quote_form_is_optional_no_json_and_does_not_apply_without_confirmation(example,monkeypatch,tmp_path):
    app=app_for(example,monkeypatch,tmp_path)
    assert len(app.metric)==8 and all(m.value!='Unavailable' for m in app.metric)
    assert not app.text_area and app.session_state['model']['full_totals']['value'] is None
    button(app,'Save and update portfolio').click().run()
    assert not app.exception and app.error
    assert not app.session_state.to_dict().get('manual_quote_values')


def test_batch_updates_all_observations_reports_and_clear_removes_session_only(example,monkeypatch,tmp_path):
    import streamlit as st
    from pulse.replay import retain
    def editor(frame,**kwargs):
        frame['EUR per unit']=2.75
        return frame
    monkeypatch.setattr(st,'data_editor',editor)
    app=app_for(example,monkeypatch,tmp_path)
    raw=deepcopy(app.session_state['model']['raw']);raw['market_capture']={'calls':{'fictional':'unit test only'}}
    config={'export_sha256':export_digest(example[0]),'known_events':[],'worthless_confirmations':[]}
    app.session_state['market_replay']=retain(example[0],'IWDA.AS',config,raw)
    calls=[]
    def engine(data,**kwargs):
        calls.append(kwargs)
        result=run_analysis(data,prices=fixture('open_derivatives')[1],private_config=kwargs['private_config'])
        result['synthetic']=False;result['performance']['mode']='offline_replay'
        return result
    monkeypatch.setattr('pulse.runner.run_analysis',engine)
    app.checkbox(key='manual_batch_confirm').check()
    app.checkbox(key='manual_batch_remember').check()
    button(app,'Save and update portfolio').click().run()
    assert not app.exception and not app.error
    staged=app.session_state['manual_quote_values']
    assert len(staged)==7 and all(q==dict(price_eur=2.75,date='2026-09-30',source='Trade Republic — displayed bid') for q in staged.values())
    assert saved_quotes(example[0])==staged
    assert len(calls)==1 and calls[0]['replay']==raw['market_capture']
    assert app.session_state['model']['full_totals']['value'] is not None and app.session_state['pdf']!=b'initial'
    assert 'Valuation complete' in app.session_state['html'].decode()
    # Editor remains available with complete coverage, and scopes need no engine.
    assert any(b.label=='Save and update portfolio' for b in app.button)
    app.radio[0].set_value('Full portfolio').run();assert len(calls)==1
    assert 'Analysis: Full portfolio' in app.session_state['html'].decode()
    # A later explicit fresh analysis must not retain the old offline-run notice.
    button(app,'Analyze Portfolio').click().run()
    assert not app.exception and len(calls)==2 and 'manual_batch_notice' not in app.session_state
    button(app,'Clear session results').click().run()
    assert not app.exception and 'model' not in app.session_state
    assert not any(k.startswith('manual_') for k in app.session_state)
    assert 'market_replay' not in app.session_state and saved_quotes(example[0])==staged


def test_new_evidence_is_escaped_and_private_worker_fields_are_excluded(example):
    raw=deepcopy(example[1]);payload='<img src="https://invalid.example/leak" onerror="alert(1)">'
    raw['historical_price_audit'][0].update(security_name=payload,error='PRIVATE_EVIDENCE_TOKEN',transaction_id='PRIVATE_EVENT_TOKEN')
    raw['holdings'][0]['security_name']=payload
    model=prepare(raw,date(2026,9,30));before=deepcopy(raw);html=html_report(model).decode()
    assert html.count('class="evidence-details"')==2 and '&lt;img' in html
    assert '<img ' not in html and 'PRIVATE_EVIDENCE_TOKEN' not in html and 'PRIVATE_EVENT_TOKEN' not in html
    assert raw==before and len(model['metrics'])==8


def test_invalid_custom_benchmark_blocks_batch_without_erasing_drafts(example,monkeypatch,tmp_path):
    import streamlit as st
    def editor(frame,**kwargs):frame['EUR per unit']=2.75;return frame
    monkeypatch.setattr(st,'data_editor',editor)
    app=app_for(example,monkeypatch,tmp_path)
    def forbidden(*a,**k):raise AssertionError('Invalid selection must never reach the engine')
    monkeypatch.setattr('pulse.runner.run_analysis',forbidden)
    next(s for s in app.selectbox if s.label=='Compare with').set_value('Custom Yahoo Finance ticker').run()
    app.checkbox(key='manual_batch_confirm').check();button(app,'Save and update portfolio').click().run()
    assert not app.exception and any('No benchmark was substituted' in e.value for e in app.error)
    assert app.session_state['pdf']==b'initial' and 'manual_quote_values' not in app.session_state
