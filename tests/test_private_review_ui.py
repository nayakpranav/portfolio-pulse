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
from pulse.html_report import html_report
from pulse.html_report import HTML_SCHEMA_VERSION
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
    button(app,'Use this observation').click().run()
    assert not app.exception and app.error
    assert not app.session_state.to_dict().get('manual_quote_values')


def test_quote_form_stages_recent_source_identified_value_and_clear_removes_it(example,monkeypatch,tmp_path):
    app=app_for(example,monkeypatch,tmp_path);isin=app.session_state['manual_instrument']
    app.number_input(key='manual_price_'+isin).set_value(2.75)
    app.text_input(key='manual_source_'+isin).set_value('Fictional broker bid')
    app.checkbox(key='manual_confirm_'+isin).check()
    app.checkbox(key='manual_remember_'+isin).check()
    button(app,'Use this observation').click().run()
    assert not app.exception and app.success
    staged=app.session_state['manual_quote_values'][isin]
    assert staged==dict(price_eur=2.75,date='2026-09-30',source='Fictional broker bid')
    assert saved_quotes(example[0])=={isin:staged}
    # No displayed result or download is silently recalculated before Analyze.
    assert app.session_state['model']['full_totals']['value'] is None and app.session_state['pdf']==b'initial'
    button(app,'Clear session results').click().run()
    assert not app.exception and 'model' not in app.session_state
    assert not any(k.startswith('manual_') for k in app.session_state)
    assert saved_quotes(example[0])=={isin:staged}


def test_new_evidence_is_escaped_and_private_worker_fields_are_excluded(example):
    raw=deepcopy(example[1]);payload='<img src="https://invalid.example/leak" onerror="alert(1)">'
    raw['historical_price_audit'][0].update(security_name=payload,error='PRIVATE_EVIDENCE_TOKEN',transaction_id='PRIVATE_EVENT_TOKEN')
    raw['holdings'][0]['security_name']=payload
    model=prepare(raw,date(2026,9,30));before=deepcopy(raw);html=html_report(model).decode()
    assert html.count('class="evidence-details"')==2 and '&lt;img' in html
    assert '<img ' not in html and 'PRIVATE_EVIDENCE_TOKEN' not in html and 'PRIVATE_EVENT_TOKEN' not in html
    assert raw==before and len(model['metrics'])==8
