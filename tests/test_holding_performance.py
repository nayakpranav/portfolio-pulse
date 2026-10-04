"""Holding reporting uses invented exports and existing canonical sources only."""
from copy import deepcopy
from datetime import date
from io import StringIO,BytesIO
from pathlib import Path
import ast,csv,math
import numpy as np
import pandas as pd
import pytest
from pypdf import PdfReader
from pulse.synthetic import fixture,row,COLUMNS
from pulse.runner import run_analysis
from pulse.adapter import prepare
from pulse.holding_performance import project_holdings
from pulse.reporting import (holding_cards,table_rows,table_html,table_frame,FINANCIAL_COLUMNS,OPERATIONAL_COLUMNS,signed_percent)
from pulse.html_report import html_report
from pulse.pdf import summary_pdf
DAY=date(2026,10,4)


def encoded(rows):
    out=StringIO();writer=csv.DictWriter(out,fieldnames=COLUMNS);writer.writeheader();writer.writerows(rows);return out.getvalue().encode()


@pytest.fixture(scope='module')
def canonical():
    outputs={}
    for kind in ['demo','stocks','realized_sales','reinvestment','missing_price','unsupported_action','open_derivatives']:
        data,prices=fixture(kind);outputs[kind]=run_analysis(data,prices=prices)
    data,prices=fixture('stocks');base=list(csv.DictReader(StringIO(data.decode())))
    extras={
        'lots':[row('2025-02-01','BUY',shares=7,price=40,amount=-280,fee=-2),row('2026-01-12','SELL',shares=13,price=50,amount=650,fee=-1)],
        'closed':[row('2026-01-12','SELL',shares=11,price=50,amount=550,fee=-1)],
        'split':[row('2026-01-12','SPLIT',shares=11,category='CORPORATE_ACTION')],
        'reverse':[row('2026-01-12','REVERSE_SPLIT',shares=-11,category='CORPORATE_ACTION'),row('2026-01-12','REVERSE_SPLIT',i=2,shares=1.1,category='CORPORATE_ACTION')],
    }
    for kind,extra in extras.items():
        for i,r in enumerate(extra):r['transaction_id']=f'invented-holding-{kind}-{i}'
        scenario=deepcopy(prices)
        if kind=='reverse':scenario['securities']['ZZ0000000002']={'start':200,'end':230}
        outputs[kind]=run_analysis(encoded(base+extra),prices=scenario)
    for kind,price in [('gain',25),('loss',15),('flat',221/11)]:
        scenario=deepcopy(prices);scenario['securities']['ZZ0000000001']['end']=price
        outputs[kind]=run_analysis(data,prices=scenario)
    return outputs


@pytest.mark.parametrize('kind',['demo','stocks','realized_sales','reinvestment','lots','split','reverse','gain','loss','flat'])
def test_authoritative_remaining_basis_and_returns_reconcile(kind,canonical):
    raw=canonical[kind];before=deepcopy(raw);model=prepare(raw,DAY)
    by_isin={r['isin']:r for r in raw['holdings'] if r['position_status']=='ACTIVE'}
    for r in model['holdings']:
        source=by_isin[r['isin']]
        assert r['basis']==source['remaining_acquisition_cost_basis_eur']
        assert r['open_pl']==source['live_unrealized_pl_acquisition_basis_eur']
        assert r['return_pct']==source['live_simple_return_acquisition_basis_pct']
        assert r['value']-r['basis']==pytest.approx(r['open_pl'],abs=1e-6)
        assert r['open_pl']/r['basis']*100==pytest.approx(r['return_pct'],abs=1e-6)
    for r in model['snapshot']['top_holdings']:
        assert r['return_pct']==by_isin[r['isin']]['live_simple_return_acquisition_basis_pct']
    assert raw==before


@pytest.mark.parametrize('kind,state',[('gain','positive'),('loss','negative'),('flat','neutral')])
def test_canonical_positive_negative_and_exact_zero_badges(kind,state,canonical):
    model=prepare(canonical[kind],DAY);r=model['holdings'][0]
    html=holding_cards(model['snapshot']['top_holdings'])
    assert 'return-'+state in html
    assert signed_percent(r['return_pct']) in html
    if state=='positive':assert '↑ +' in html
    if state=='negative':assert '↓ -' in html
    if state=='neutral':assert r['return_pct']==0 and '0.00%' in html and 'Unavailable' not in html


def test_partial_sales_reinvestment_and_corporate_actions_use_adjusted_basis(canonical):
    lots=canonical['lots']['holdings'][0]
    assert lots['current_quantity']==5
    assert lots['remaining_acquisition_cost_basis_eur']==pytest.approx(282*5/7)
    assert lots['remaining_acquisition_cost_basis_eur']!=lots['total_acquisition_cost_basis_original_eur']
    reinvestment=prepare(canonical['reinvestment'],DAY)
    assert reinvestment['holdings'][0]['basis']==226 and reinvestment['by_key']['income'].value==4
    for kind in ['split','reverse']:
        model=prepare(canonical[kind],DAY)
        assert len(model['holdings'])==1 and model['holdings'][0]['basis']==221
        assert all(a['validation_status']=='PASS' for a in canonical[kind]['corporate_action_audit'])
    closed=prepare(canonical['closed'],DAY)
    assert not closed['holdings'] and not closed['snapshot']['top_holdings']


@pytest.mark.parametrize('change',['missing_basis','negative_basis','missing_price','stale','no_quote_date','uncovered','missing_pl','pl_mismatch','return_mismatch','infinite_return','duplicate_identity','missing_fx'])
def test_unreliable_or_inconsistent_sources_do_not_manufacture_returns(change,canonical):
    raw=deepcopy(canonical['stocks']);r=raw['holdings'][0]
    if change=='missing_basis':r['remaining_acquisition_cost_basis_eur']=None
    elif change=='negative_basis':r['remaining_acquisition_cost_basis_eur']=-1
    elif change=='missing_price':r['live_current_value_eur']=None
    elif change=='stale':r['live_price_date']='2026-08-01'
    elif change=='no_quote_date':r['live_price_date']=''
    elif change=='uncovered':raw['valuation_diagnostics']=[]
    elif change=='missing_pl':r['live_unrealized_pl_acquisition_basis_eur']=None
    elif change=='pl_mismatch':r['live_unrealized_pl_acquisition_basis_eur']+=1
    elif change=='return_mismatch':r['live_simple_return_acquisition_basis_pct']+=1
    elif change=='infinite_return':r['live_simple_return_acquisition_basis_pct']=float('inf')
    elif change=='duplicate_identity':raw['holdings'].append(deepcopy(r))
    else:r.update(live_price_currency='USD',fx_to_eur=None)
    model=prepare(raw,DAY);rows,issues=project_holdings(raw,model['snapshot'],DAY)
    assert all(r['return_pct'] is None for r in rows)
    if model['snapshot']['top_holdings']:assert 'return-unavailable' in holding_cards(model['snapshot']['top_holdings'])
    else:assert change=='missing_price'
    if change in {'pl_mismatch','return_mismatch','missing_basis','duplicate_identity'}:assert issues


def test_zero_basis_retains_canonical_pl_but_percentage_is_unavailable(canonical):
    raw=deepcopy(canonical['stocks']);r=raw['holdings'][0]
    r.update(remaining_acquisition_cost_basis_eur=0,live_unrealized_pl_acquisition_basis_eur=r['live_current_value_eur'],live_simple_return_acquisition_basis_pct=None)
    m=prepare(raw,DAY);r=m['holdings'][0]
    assert r['basis']==0 and r['open_pl']==r['value'] and r['return_pct'] is None
    assert 'zero or unusable' in r['performance_status']


def test_missing_one_valuation_does_not_hide_another_holding_return(canonical):
    raw=deepcopy(canonical['demo']);raw['holdings'][0]['live_current_value_eur']=None
    raw['valuation_diagnostics'][0]['valuation_status']='BLOCKING'
    m=prepare(raw,DAY);assert m['by_key']['value'].value is None
    assert m['holdings'][0]['return_pct'] is None and any(r['return_pct'] is not None for r in m['holdings'][1:])
    other=prepare(canonical['open_derivatives'],DAY)
    assert len(other['holdings'])==1 and other['holdings'][0]['return_pct'] is not None


def test_canonical_currency_conversion_and_acquisition_basis_not_user_basis():
    tree=ast.parse(Path('vendor/v678/analysis_engine.py').read_text(encoding='utf-8'))
    definitions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {'safe_float','enrich_holdings_with_dynamic_live_prices'}]
    ns=dict(pd=pd,np=np,ENABLE_STOCK_FUND_LIVE_PRICES=True,SHOW_DETAILED_PROGRESS=False,prefetch_openfigi_identities=lambda ids:None,
        find_best_yahoo_ticker=lambda **kw:dict(ticker='INVENTED',match_status='EXACT',match_score=100,matched_name='Invented',matched_exchange='TEST',matched_currency='USD',matched_quote_type='EQUITY'),
        fetch_latest_yahoo_price=lambda ticker:dict(currency='USD',price_native=100,price_date='2026-09-30',status='OK'),get_fx_to_eur=lambda currency:.9)
    exec(compile(ast.Module(body=definitions,type_ignores=[]),'canonical-function-test','exec'),ns)
    frame=pd.DataFrame([dict(isin='ZZ0000000001',security_name='Invented foreign stock',asset_class='STOCK',position_status='ACTIVE',current_quantity=2,remaining_acquisition_cost_basis_eur=100,remaining_user_funded_basis_eur=90)])
    r=ns['enrich_holdings_with_dynamic_live_prices'](frame).to_dict('records')[0]
    source=dict(holdings=[r],valuation_diagnostics=[dict(isin=r['isin'],valuation_status='VALUED',instrument_type='STOCK_FUND')])
    snap=dict(valued_stockfund_total=180,top_holdings=[dict(isin=r['isin'],name=r['security_name'],value=180,weight_pct=100)])
    holding=project_holdings(source,snap,DAY)[0][0]
    assert (holding['value'],holding['basis'],holding['open_pl'],holding['return_pct'])==(180,100,80,80)
    assert r['live_simple_return_user_basis_pct']==100


def test_shared_tables_order_identifiers_rank_and_exact_financial_formats(canonical):
    m=prepare(canonical['demo'],DAY);top=m['snapshot']['top_holdings'];all_rows=m['holdings']
    table=table_rows(top);assert list(table[0])==FINANCIAL_COLUMNS
    assert 'ISIN' not in table[0] and all(r['isin'] for r in top)
    assert [r['value'] for r in top]==sorted([r['value'] for r in top],reverse=True)
    assert list(table_rows(all_rows,True)[0])==FINANCIAL_COLUMNS+OPERATIONAL_COLUMNS
    assert list(table_frame(top).data)==FINANCIAL_COLUMNS
    assert table_rows(top)[0]['Return']==signed_percent(top[0]['return_pct'])
    html=html_report(m).decode()
    assert 'See Top 10 holdings' in html and '<th>ISIN</th>' in html
    assert all('<th class="number">'+c+'</th>' in html for c in FINANCIAL_COLUMNS[1:])
    for r in top:
        assert f'€{r["basis"]:,.2f}' in html and signed_percent(r['return_pct']) in html
    assert '↑ +' in html and '↓ -' in html and 'not lifetime' in html


def test_long_names_huge_returns_and_html_injection_remain_text(canonical):
    m=prepare(canonical['demo'],DAY);r=m['snapshot']['top_holdings'][0]
    r.update(name='</script><img src="https://invalid.example" onerror="alert(1)">',return_pct=1234567890123.45)
    html=holding_cards(m['snapshot']['top_holdings']);assert '<img' not in html and '+1,234,567,890,123.45%' in html
    r['performance_status']='"><img src=x onerror=alert(1)>'
    assert '<img' not in holding_cards([r]) and 'aria-label="Unrealized return:' in holding_cards([r])
    assert 'class="holding-value"' in html and 'class="holding-fill"' in html
    assert len(PdfReader(BytesIO(summary_pdf(m))).pages)==1


def test_unreliable_returns_are_not_lifetime_profit_or_income(canonical):
    raw=deepcopy(canonical['demo']);m=prepare(raw,DAY);before=deepcopy(m['metrics'])
    raw['holdings'][0]['live_simple_return_acquisition_basis_pct']+=1
    changed=prepare(raw,DAY)
    assert changed['metrics']==before and changed['by_key']['income'].value==m['by_key']['income'].value
    assert changed['holdings'][0]['return_pct'] is None and any('holding-performance' in i for i in changed['issues'])



def test_independent_models_scope_toggle_and_rounding_keep_sources(canonical):
    stock=prepare(canonical['open_derivatives'],DAY,'stocks_funds')
    full=prepare(canonical['open_derivatives'],DAY,'full_portfolio')
    assert stock['holdings']==full['holdings'] and stock['snapshot']['top_holdings']==full['snapshot']['top_holdings']
    first=prepare(canonical['gain'],DAY);second=prepare(canonical['loss'],DAY)
    wanted=second['holdings'][0]['return_pct'];first['holdings'][0]['return_pct']=123
    assert second['holdings'][0]['return_pct']==wanted
    r=second['snapshot']['top_holdings'][0]
    assert signed_percent(r['return_pct'])==f'{r["return_pct"]:+,.2f}%'
    assert r['return_pct']==canonical['loss']['holdings'][0]['live_simple_return_acquisition_basis_pct']


def test_localized_ambiguous_event_preserves_other_canonical_holding_returns():
    data,prices=fixture('demo');rows=list(csv.DictReader(StringIO(data.decode())))
    event=row('2026-06-01','FREE_RECEIPT',asset='FUND',shares=-8,category='DELIVERY');event['transaction_id']='invented-unresolved-fund-removal';rows.append(event)
    raw=run_analysis(encoded(rows),prices=prices);model=prepare(raw,DAY)
    assert raw['accounting_status']=='BLOCKING' and model['excluded_securities']
    assert 'partial' in model['scope_label'] and model['holdings']
    assert all(r['isin']!='ZZ0000000001' and r['return_pct'] is not None for r in model['holdings'])
    assert model['full_totals']['value'] is None and model['full_totals']['profit'] is None
