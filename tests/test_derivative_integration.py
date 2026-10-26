"""Synthetic quotation integration; no financial inputs from the owner."""
import json
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timezone,date
import pandas as pd
import pytest
from pulse.derivative_quotes import Observation,validate_observation
from pulse.market import install_derivatives,allowed_host
from test_derivative_quotes import ISIN,OTHER,URL,NOW,observation,validate
LISTING='https://www.boerse-stuttgart.de/de-de/fuer-geschaeftspartner/reports/mifir-ii-delayed-data/xtsu-pre-trade-vhp/'


def test_automatic_bid_precedes_optional_manual_and_missing_remains_nan(monkeypatch):
    import pulse.derivative_quotes as quotes
    class Fixed(datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    monkeypatch.setattr(quotes,'datetime',Fixed)
    monkeypatch.setenv('FOLIOLENS_AUTO_DERIVATIVE_DOCUMENTS','1');monkeypatch.delenv('FOLIOLENS_REPLAY_PATH',raising=False)
    selected=validate(observation());results={ISIN:dict(selected=selected,attempts=[selected],metadata={},status='AUTO_VERIFIED_BID')}
    class Fake:
        def __init__(self,*,isins,cache=None):assert isins==[ISIN,OTHER]
        def quote(self,isin):return results.get(isin,dict(selected=None,attempts=[],metadata={},status='MISSING'))
    monkeypatch.setattr(quotes,'Collector',Fake)
    monkeypatch.setattr('pulse.valuations.today',lambda:date(2026,10,26))
    ns=dict(derivative_positions=pd.DataFrame([dict(isin=ISIN,position_status='ACTIVE'),dict(isin=OTHER,position_status='ACTIVE')]))
    config={'derivative_quotes':{ISIN:dict(price_eur=9,date='2026-10-26',source='Fictional manual fallback')}}
    install_derivatives(ns,config)
    assert ns['multi_source_derivative_quote_probe'](ISIN)['live_price_eur']==1.25
    missing=ns['multi_source_derivative_quote_probe'](OTHER);assert pd.isna(missing['live_price_eur'])
    results.clear();install_derivatives(ns,config)
    assert ns['multi_source_derivative_quote_probe'](ISIN)['live_price_eur']==9


def test_no_unreviewed_destinations_or_default_hosted_access():
    assert not allowed_host(URL) and allowed_host(URL,True)
    for url in [LISTING,'https://ddl.service.boerse-stuttgart.de/s/example','https://d3s5kssjdy247b.cloudfront.net/file.gz','https://www.boerse-stuttgart.de.evil.invalid/file']:
        assert not allowed_host(url,True)


@pytest.mark.parametrize('deployment,synthetic,enabled',[
    ('personal',False,'1'),('personal',True,'0'),('owner_hosted',False,'0'),('public_demo',True,'0')])
def test_worker_gate_is_derived_not_inherited(tmp_path,monkeypatch,deployment,synthetic,enabled):
    from pulse.runner import run_analysis
    from pulse.synthetic import fixture
    from pulse.private_config import export_digest
    monkeypatch.setenv('FOLIOLENS_MODE',deployment);monkeypatch.setenv('FOLIOLENS_OWNER_GATE_VERIFIED','1');monkeypatch.setenv('FOLIOLENS_DATA_RIGHTS_VERIFIED','1')
    monkeypatch.setenv('FOLIOLENS_AUTO_DERIVATIVE_DOCUMENTS','1');monkeypatch.delenv('STREAMLIT_SHARING_MODE',raising=False);monkeypatch.delenv('IS_STREAMLIT_CLOUD',raising=False)
    data,prices=fixture()
    def execute(command,work,env,timeout):
        assert env['FOLIOLENS_AUTO_DERIVATIVE_DOCUMENTS']==enabled
        (work/'result.json').write_text('{}');return 0
    monkeypatch.setattr('pulse.runner.execute_worker',execute)
    run_analysis(data,prices=prices if synthetic else None,private_config=dict(export_sha256=export_digest(data)))


def test_report_model_excludes_raw_quotes_and_escapes_labels():
    from pulse.synthetic import fixture
    from pulse.runner import run_analysis
    from pulse.adapter import prepare
    from pulse.html_report import html_report
    from pulse.pdf import summary_pdf
    from pypdf import PdfReader
    from io import BytesIO
    data,prices=fixture('open_derivatives');raw=run_analysis(data,prices=prices)
    raw['synthetic']=False;target=raw['active_derivatives'][0]['isin']
    raw['derivative_quote_evidence']={target:dict(attempts=[dict(raw_evidence='PRIVATE_PROVIDER_BODY')],metadata=dict(isin=target,identity='EXACT_ISIN',underlying='<img src=x onerror=alert(1)>'),status='NO_QUOTE')}
    model=prepare(raw,date(2026,10,26));before=deepcopy(raw)
    assert any('&lt;img' not in issue and '<img' in issue for issue in model['issues'] if 'require derivative valuations' in issue)
    text=html_report(model).decode();pdf=PdfReader(BytesIO(summary_pdf(model)))
    assert 'Derivative Quote Center' in text and 'PRIVATE_PROVIDER_BODY' not in text and '<img src=x' not in text
    assert '&lt;img' in text and len(pdf.pages)==1 and raw==before
    assert 'class="derivative-quotes-table"' in text and 'tabindex="0" role="region"' in text
    assert '.derivative-quotes-table td{white-space:nowrap}' in text
    assert all(m.display in text and m.display.replace('€','EUR ') in pdf.pages[0].extract_text() for m in model['metrics'])
