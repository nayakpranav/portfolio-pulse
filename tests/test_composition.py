"""Synthetic presentation aggregation; no private investors or event evidence."""
from datetime import date
from copy import deepcopy
from io import BytesIO
import pytest
from pypdf import PdfReader
from pulse.composition import composition,composition_panel
from pulse.pdf import income_label_layout,monthly_label,summary_pdf
DAY=date(2026,10,4)

def result(values,kinds=None):
    kinds=kinds or ['STOCK']*len(values)
    rows=[dict(canonical_instrument_id=f'fictional-{i}',isin=f'ZZ{i:010}',asset_class=kinds[i],position_status='ACTIVE',current_quantity=1,
               live_current_value_eur=v,live_price_date='2026-09-30',security_name='Invented security') for i,v in enumerate(values)]
    diagnostics=[dict(canonical_instrument_id=r['canonical_instrument_id'],instrument_type='STOCK_FUND',valuation_status='VALUED') for r in rows]
    return dict(holdings=rows,valuation_diagnostics=diagnostics)

@pytest.mark.parametrize('kind,stocks,funds',[('STOCK',100,0),('FUND',0,100),('STOCK/FUND',0,0)])
def test_canonical_classification_without_name_guesses(kind,stocks,funds):
    r=result([100,200],[kind,kind]);r['holdings'][0]['security_name']='ETF-looking name is not evidence'
    c=composition(r,DAY);assert c['active_count']==2 and c['top_pct']==100
    assert c['stock_pct']==stocks and c['fund_pct']==funds
    assert c['unclassified_pct']==100-stocks-funds

@pytest.mark.parametrize('values,top,expected',[([],0,None),([.01],1,100),([20,30],2,100),([1,2,3,4,5],5,100),([10,20,30,40,50,50,100],5,90),([999,1],2,100)])
def test_concentration_exact_denominator_and_real_number_of_positions(values,top,expected):
    c=composition(result(values),DAY);assert c['active_count']==len(values) and c['top_count']==top
    assert c['top_pct']==expected
    if expected is not None:assert c['top_pct']+c['remaining_pct']==pytest.approx(100)
    else:assert 'composition-segment' not in composition_panel(c)

@pytest.mark.parametrize('change',['unpriced','stale','no_date','blocked_quote','future_date'])
def test_unreliable_positions_counted_but_excluded_from_denominator(change):
    r=result([100,200],['STOCK','FUND']);row=r['holdings'][1]
    if change=='unpriced':row['live_current_value_eur']=None
    if change=='stale':row['live_price_date']='2026-09-01'
    if change=='no_date':row['live_price_date']=''
    if change=='future_date':row['live_price_date']='2026-10-05'
    if change=='blocked_quote':r['valuation_diagnostics'][1]['valuation_status']='BLOCKING'
    c=composition(r,DAY);assert c['active_count']==2 and c['valued_count']==1 and c['total_value']==100
    assert c['stock_pct']==100 and c['fund_pct']==0 and 'excluded' in c['note']


def test_mixed_types_closed_derivatives_duplicates_and_neutral_category():
    r=result([100,200,100],['STOCK','FUND',None]);old=deepcopy(r)
    c=composition(r,DAY);assert (c['stock_pct'],c['fund_pct'],c['unclassified_pct'])==(25,50,25)
    assert r==old
    r['holdings'] += [dict(r['holdings'][0]),dict(r['holdings'][0],position_status='CLOSED'),dict(r['holdings'][0],asset_class='DERIVATIVE',canonical_instrument_id='derivative')]
    assert composition(r,DAY)==c
    r['holdings'].append(dict(r['holdings'][0],live_current_value_eur=101))
    assert composition(r,DAY)['valued_count']==2


def test_unknown_accounting_and_zero_denominator_are_unavailable_not_zero():
    c=composition(result([100]),DAY,False);assert c['active_count'] is None and c['top_pct'] is None
    c=composition(result([0]),DAY);assert c['active_count']==1 and c['top_pct'] is None
    assert 'composition-segment' not in composition_panel(c)


def test_independent_results_and_partial_scope_labels():
    one=composition(result([10,90],['STOCK','FUND']),DAY)
    two=composition(result([100]),DAY,partial=True)
    assert one['fund_pct']==90 and two['fund_pct']==0
    assert 'unaffected' in two['note'] and not one['partial']
    one['fund_pct']=0;assert composition(result([10,90],['STOCK','FUND']),DAY)['fund_pct']==90

@pytest.mark.parametrize('value,status,label',[(0,'complete','0.00'),(-12.34,'complete','-12.34'),(12.34,'partial','12.34*'),(None,'uncovered','-'),(0,'unavailable','-')])
def test_exact_monthly_label_semantics(value,status,label):
    assert monthly_label(dict(total=value,status=status))==label


def test_readable_exact_labels_or_monthly_key_no_silent_rounding():
    months=[dict(label='Jan',total=1.23,status='complete')]*12
    assert income_label_layout(months,270)==('bars',12)
    months[0]=dict(label='Jan',total=1234567890123.45,status='partial')
    assert income_label_layout(months,270)==('key',2)
    assert monthly_label(months[0])=='1,234,567,890,123.45*'
