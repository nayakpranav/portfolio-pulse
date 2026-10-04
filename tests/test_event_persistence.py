"""Independent fabricated portfolios only; no owner-specific event evidence."""
import csv
from dataclasses import asdict
from io import StringIO
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from pulse.synthetic import fixture,row
from pulse.runner import run_analysis
from pulse.adapter import prepare
from pulse.private_config import export_digest,personal_defaults
from pulse.event_review import normalized,event_spec,review,remember,canonical_match
from pulse.profile import profile_root,read_records


def encoded(rows):
    out=StringIO();writer=csv.DictWriter(out,fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
    return out.getvalue().encode()


@pytest.fixture
def personal(tmp_path,monkeypatch):
    monkeypatch.setenv('FOLIOLENS_MODE','personal')
    monkeypatch.setenv('FOLIOLENS_CONFIG_DIRECTORY',str(tmp_path))
    monkeypatch.delenv('IS_STREAMLIT_CLOUD',raising=False)
    monkeypatch.delenv('STREAMLIT_SHARING_MODE',raising=False)
    return tmp_path


def confirmed(data,prices):
    candidate=review(data,{})[0]
    config=dict(export_sha256=export_digest(data),worthless_confirmations=[candidate['source_row']])
    result=run_analysis(data,prices=prices,private_config=config)
    assert remember(data,config,result)==1
    return result


def test_confirmation_survives_restart_new_export_and_row_reordering(personal):
    data,prices=fixture('unsupported_action');result=confirmed(data,prices)
    assert result['accounting_status']=='COMPLETE'
    assert review(data,personal_defaults(data))[0]['verified']
    rows=list(csv.DictReader(StringIO(data.decode())))
    extra=row('2026-09-30','INTEREST_PAYMENT',asset='',amount=3,category='CASH')
    extra['transaction_id']='other-investor-later-income'
    rows.insert(0,extra);new=encoded(rows)
    assert export_digest(new)!=export_digest(data)
    defaults=personal_defaults(new)
    assert review(new,defaults)[0]['verified']
    assert run_analysis(new,prices=prices,private_config=defaults)['accounting_status']=='COMPLETE'
    assert read_records(personal) and (personal/'verified-events.dat').is_file()
    if __import__('os').name=='nt':
        assert b'FICTIONAL' not in (personal/'verified-events.dat').read_bytes()


def test_prior_anchor_is_stable_when_new_rows_change_dataframe_dtypes():
    from pulse.event_review import prior_activity_anchor
    data,_=fixture('unsupported_action');rows=list(csv.DictReader(StringIO(data.decode())))
    rows=[r for r in rows if r['asset_class']=='STOCK']
    first=normalized(encoded(rows));event=first[first.type_norm.eq('FREE_RECEIPT')].iloc[0]
    before=prior_activity_anchor(first,event)
    added=row('2026-09-30','CUSTOMER_INBOUND',asset='',amount=1,category='CASH');added['transaction_id']='fictional-new-cash'
    rows.append(added);second=normalized(encoded(rows));event=second[second.type_norm.eq('FREE_RECEIPT')].iloc[0]
    assert before==prior_activity_anchor(second,event)


@pytest.mark.parametrize('field,value',[('transaction_id','different-owner-event'),('symbol','ZZ0000000088'),('description','Custody transfer'),('shares','-1'),('amount','1'),('fee','-1')])
def test_changed_event_does_not_inherit_classification(personal,field,value):
    data,prices=fixture('unsupported_action');confirmed(data,prices)
    rows=list(csv.DictReader(StringIO(data.decode())))
    next(r for r in rows if r['type']=='FREE_RECEIPT')[field]=value
    changed=encoded(rows)
    result=run_analysis(changed,prices=prices,private_config=personal_defaults(changed))
    assert result['accounting_status']=='BLOCKING' and not result['worthless_derecognition_events']


def test_prior_history_and_different_profile_are_isolated(personal,monkeypatch,tmp_path):
    data,prices=fixture('unsupported_action');confirmed(data,prices)
    rows=list(csv.DictReader(StringIO(data.decode())))
    next(r for r in rows if r['type']=='BUY')['transaction_id']='different-acquisition'
    changed=encoded(rows)
    assert not personal_defaults(changed).get('known_events')
    monkeypatch.setenv('FOLIOLENS_CONFIG_DIRECTORY',str(tmp_path/'second-person'))
    assert not personal_defaults(data).get('known_events')
    assert not review(data,personal_defaults(data))[0]['verified']


def test_profile_recovers_corruption_from_protected_backup(personal):
    from pulse.profile import _atomic
    data,prices=fixture('unsupported_action');confirmed(data,prices)
    _atomic(personal/'verified-events.dat',b'corrupt')
    assert review(data,personal_defaults(data))[0]['verified']
    _atomic(personal/'verified-events.dat',b'corrupt')
    _atomic(personal/'verified-events.backup.dat',b'corrupt')
    with pytest.raises(ValueError):personal_defaults(data)


def test_no_global_registry_mutation_during_concurrent_reviews():
    data,_=fixture('unsupported_action');frame=normalized(data)
    spec=event_spec(frame[frame.type_norm.eq('FREE_RECEIPT')].iloc[0])
    def check(i):return len(canonical_match(frame,[spec] if i%2 else [])['events'])
    with ThreadPoolExecutor(4) as pool:
        assert list(pool.map(check,range(20)))==[i%2 for i in range(20)]
    import security_events
    assert security_events.KNOWN_WORTHLESS_DERECOGNITIONS==()


def test_profile_does_not_depend_on_virtualized_appdata(monkeypatch,tmp_path):
    monkeypatch.setenv('USERPROFILE',str(tmp_path/'actual-user'))
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'virtual-cache'))
    assert profile_root()==tmp_path/'actual-user/FolioLensPersonal'
    from desktop.personal import personal_environment
    assert personal_environment()['FOLIOLENS_CONFIG_DIRECTORY']==str(profile_root())


@pytest.mark.parametrize('kind',['stocks','etf_only','realized_sales','dividends','interest','reinvestment','derivatives','open_derivatives','demo'])
def test_independent_supported_investors_need_no_event_confirmation(kind):
    data,prices=fixture(kind)
    assert not review(data,{})
    result=run_analysis(data,prices=prices)
    assert result['accounting_status']=='COMPLETE'
    model=prepare(result,analysis_scope='stocks_funds')
    assert model['holdings'] and model['by_key']['mwr'].value is not None


@pytest.mark.parametrize('split',['forward','reverse'])
def test_supported_splits_are_automatic_and_basis_conserved(split):
    data,prices=fixture('stocks');rows=list(csv.DictReader(StringIO(data.decode())))
    if split=='forward':
        additions=[row('2026-01-12','SPLIT',shares=11,category='CORPORATE_ACTION')]
    else:
        additions=[row('2026-01-12','REVERSE_SPLIT',shares=-11,category='CORPORATE_ACTION'),row('2026-01-12','REVERSE_SPLIT',i=2,shares=1.1,category='CORPORATE_ACTION')]
        prices['securities']['ZZ0000000002']={'start':200,'end':230}
    for index,item in enumerate(additions):item['transaction_id']=f'fictional-{split}-{index}'
    rows.extend(additions);data=encoded(rows)
    assert not review(data,{})
    result=run_analysis(data,prices=prices)
    assert result['accounting_status']=='COMPLETE'
    for action in result['corporate_action_audit']:
        assert action['validation_status']=='PASS'
        assert action['acquisition_basis_before_eur']==pytest.approx(action['acquisition_basis_after_eur'])
        assert action['investment_cash_flow_caused_eur']==0


def test_ambiguous_removal_preserves_unrelated_security_scope():
    data,prices=fixture('demo');rows=list(csv.DictReader(StringIO(data.decode())))
    action=row('2026-06-01','FREE_RECEIPT',i=3,shares=-13,category='DELIVERY')
    action['transaction_id']='fictional-ambiguous-other-investor'
    rows.append(action);data=encoded(rows)
    result=run_analysis(data,prices=prices)
    assert result['accounting_status']=='BLOCKING' and not result['worthless_derecognition_events']
    model=prepare(result,analysis_scope='stocks_funds')
    assert all(model['by_key'][k].value is not None for k in ('value','profit','mwr','benchmark','twr','income'))
    assert all(model['by_key'][k].value is None for k in ('capital','recovery'))
    assert model['nav'].shape[0]>0 and model['periods'] and len(model['holdings'])==5
    assert 'partial' in model['scope_label']
    assert model['full_totals']=={'value':None,'profit':None}
    assert any(r['isin']=='ZZ0000000003' for r in model['excluded_securities'])
    # Independent controlled portfolio containing only the unaffected histories.
    clean=encoded([r for r in rows if r['symbol']!='ZZ0000000003'])
    baseline=prepare(run_analysis(clean,prices=prices),analysis_scope='stocks_funds')
    for key in ('value','profit','mwr','benchmark','twr','income'):
        assert model['by_key'][key].value==pytest.approx(baseline['by_key'][key].value)
    assert model['raw'] is result and len(result['holdings'])==6


def test_missing_identity_and_nonzero_cash_cannot_be_persisted(personal):
    data,prices=fixture('unsupported_action');rows=list(csv.DictReader(StringIO(data.decode())))
    event=next(r for r in rows if r['type']=='FREE_RECEIPT');event['amount']='5'
    data=encoded(rows)
    assert not review(data,{})[0]['eligible']
    config=dict(export_sha256=export_digest(data),worthless_confirmations=[review(data,{})[0]['source_row']])
    result=run_analysis(data,prices=prices,private_config=config)
    assert remember(data,config,result)==0 and not read_records(personal)


@pytest.mark.parametrize('kind',['STOCKPERK','BENEFITS_SAVEBACK'])
def test_supported_promotional_funding_needs_no_review(kind):
    data,prices=fixture('stocks');rows=list(csv.DictReader(StringIO(data.decode())))
    promo=row('2025-01-06',kind,amount=2,category='CASH')
    promo['transaction_id']='fictional-investor-promo-'+kind
    rows.insert(0,promo);data=encoded(rows)
    assert not review(data,{})
    result=run_analysis(data,prices=prices)
    assert result['accounting_status']=='COMPLETE'
    life=result['lifetime_metrics']
    assert life['lifetime_gross_investment_outflows_eur']==pytest.approx(221)
    assert life['lifetime_user_funded_investment_outflows_eur']==pytest.approx(219)


def test_derivative_settlement_and_realized_loss_remain_canonical():
    data,prices=fixture('derivatives');rows=list(csv.DictReader(StringIO(data.decode())))
    next(r for r in rows if r['type']=='SELL')['amount']='4'
    next(r for r in rows if r['type']=='SELL')['price']='2'
    settlement=row('2026-03-01','TILG',i=7,asset='DERIVATIVE',amount=3,category='CORPORATE_ACTION')
    settlement['transaction_id']='fictional-investor-settlement'
    rows.append(settlement);data=encoded(rows)
    assert not review(data,{})
    result=run_analysis(data,prices=prices)
    assert result['accounting_status']=='COMPLETE' and not result['active_derivatives']
    assert result['lifetime_metrics']['lifetime_derivative_realized_pl_all_positions_eur']==pytest.approx(-5)
    assert len(result['derivative_ledger'])==3


def test_malformed_protected_records_fail_closed(personal):
    from pulse.profile import store_records
    store_records(personal,[{'bad':'fixture'}])
    with pytest.raises(ValueError):read_records(personal)


def test_hosted_confirmation_never_creates_shared_registry(tmp_path,monkeypatch):
    monkeypatch.setenv('FOLIOLENS_MODE','owner_hosted')
    monkeypatch.setenv('FOLIOLENS_OWNER_GATE_VERIFIED','1')
    monkeypatch.setenv('FOLIOLENS_DATA_RIGHTS_VERIFIED','1')
    monkeypatch.setenv('FOLIOLENS_CONFIG_DIRECTORY',str(tmp_path))
    data,prices=fixture('unsupported_action')
    config=dict(export_sha256=export_digest(data),worthless_confirmations=[review(data,{})[0]['source_row']])
    result=run_analysis(data,prices=prices,private_config=config)
    assert remember(data,config,result)==0 and not list(tmp_path.iterdir())
