"""Presentation contracts: shared axes, identity, accessible hierarchy and offline cards."""
from copy import deepcopy
from html.parser import HTMLParser
from io import BytesIO
import math
import pytest
from pypdf import PdfReader
from pulse.charts import benchmark_alias, wealth_range
from pulse.reporting import holding_cards
from pulse.html_report import html_report
from pulse.pdf import summary_pdf

@pytest.mark.parametrize('values',[[0,100],[1,1.01],[0,0],[-5,100],[-100,-10],[None,float('nan'),float('inf')]])
def test_shared_wealth_axis_preserves_observations_without_negative_padding(values):
    low,high=wealth_range(values)
    valid=[v for v in values if v is not None and math.isfinite(v)]
    assert high>low
    assert all(low<=v<=high for v in valid)
    if valid and min(valid)>=0:assert low>=0
    if valid and min(valid)<0:assert low<0
    if valid and min(valid)>0:assert high-low>=max(valid)*.05

@pytest.mark.parametrize('ticker,name,alias',[
 ('IWDA.AS','iShares Core MSCI World UCITS ETF USD (Acc)','MSCI World ETF (IWDA.AS)'),
 ('SPY','SPDR S&P 500 ETF Trust','SPDR S&P 500 ETF Trust (SPY)'),
 ('^GSPC','S&P 500 price-only index','S&P 500 price-only index (^GSPC)'),
])
def test_chart_alias_keeps_exact_verified_ticker_and_full_metadata(ticker,name,alias):
    model={'raw':dict(benchmark_enabled=True,synthetic=False,benchmark_identity={'ticker':ticker,'name':name},benchmark_name=name+' ('+ticker+')')}
    original=deepcopy(model);assert benchmark_alias(model)==alias and model==original

class Cards(HTMLParser):
    def __init__(self):super().__init__();self.attrs=[];self.words=[]
    def handle_starttag(self,tag,attrs):self.attrs.append((tag,dict(attrs)))
    def handle_data(self,text):self.words.append(text)

def test_rankings_and_relative_bars_do_not_replace_portfolio_weights():
    rows=[dict(name='<script>unsafe</script>',value=100,weight_pct=10),dict(name='Other',value=50,weight_pct=5)]
    parser=Cards();parser.feed(holding_cards(rows));words=''.join(parser.words)
    assert '01' in words and '02' in words and '10.0%' in words and '5.0%' in words
    fills=[a['style'] for _,a in parser.attrs if a.get('class')=='holding-fill']
    assert fills==['width:100.000%','width:50.000%']
    assert not any(t=='script' for t,_ in parser.attrs)
    assert not any(k.startswith('on') for _,a in parser.attrs for k in a)

def test_empty_and_small_holding_sets_do_not_fabricate_cards():
    assert 'holding-rank' not in holding_cards([])
    p=Cards();p.feed(holding_cards([dict(name='Small',value=.01,weight_pct=.01)]))
    assert sum(a.get('class')=='holding-card' for _,a in p.attrs)==1
    assert '€0.01' in ''.join(p.words)


@pytest.fixture(scope='module')
def prepared_demo():
    from pulse.synthetic import fixture
    from pulse.runner import run_analysis
    from pulse.adapter import prepare
    data,prices=fixture('demo')
    return prepare(run_analysis(data,prices=prices))

@pytest.mark.parametrize('case',['negative_chart','no_income','empty_holdings','long_names_huge_values'])
def test_edge_report_layout_and_figures_remain_consistent(case,prepared_demo):
    import pymupdf
    from dataclasses import replace
    model=deepcopy(prepared_demo)
    if case=='negative_chart':
        model['nav'].loc[model['nav'].index[0],'stockfund_value_eur']=-10
        model['snapshot']['months'][0]['total']=-20
    elif case=='no_income':
        for row in model['snapshot']['months']:row['total']=0
    elif case=='empty_holdings':model['snapshot']['top_holdings']=[]
    else:
        for row in model['snapshot']['top_holdings']:
            row.update(name='Synthetic global exchange traded accumulating equity fund '+('LongName'*20),value=1234567890123.45)
        model['metrics'][0]=replace(model['metrics'][0],value=1234567890123.45)
    pdf=summary_pdf(model);html=html_report(model).decode();parser=Cards();parser.feed(html)
    doc=pymupdf.open(stream=pdf,filetype='pdf');assert len(doc)==1
    spans=[s for block in doc[0].get_text('dict')['blocks'] for line in block.get('lines',[]) for s in line['spans']]
    assert all(20<=s['bbox'][0] and s['bbox'][2]<=doc[0].rect.width-20 and s['bbox'][1]>=10 and s['bbox'][3]<=doc[0].rect.height-15 for s in spans)
    text=PdfReader(BytesIO(pdf)).pages[0].extract_text()
    for metric in model['metrics']:
        assert metric.display in ''.join(parser.words)
        assert metric.display.replace('€','EUR ') in text
    if case=='negative_chart':assert wealth_range(model['nav']['stockfund_value_eur'])[0]<-10
    if case=='empty_holdings':assert 'No supported holding ranking available' in text
