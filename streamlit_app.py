"""One-page Portfolio Pulse UI. Session data is never placed in shared caches."""
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import altair as alt
import pandas as pd
import streamlit as st
from pulse.runner import run_analysis,AnalysisError
from pulse.synthetic import fixture
from pulse.adapter import prepare
from pulse.pdf import summary_pdf

st.set_page_config(page_title='Portfolio Pulse',page_icon='◉',layout='wide')
st.markdown('''<style>
.block-container{max-width:1300px;padding-top:2rem;padding-bottom:2rem}
h1{letter-spacing:-1.5px} [data-testid="stMetric"]{background:#112238;border:1px solid #24415e;border-radius:12px;padding:16px}
[data-testid="stMetricValue"]{font-size:1.7rem} [data-testid="stMetricLabel"]{font-size:.9rem}
[data-testid="stSidebar"]{border-right:1px solid #24415e}
@media(max-width:600px){.block-container{padding:1rem}[data-testid="stMetricValue"]{font-size:1.4rem}}
</style>''',unsafe_allow_html=True)

upload_enabled = os.environ.get('PULSE_ENABLE_UPLOADS') == '1'
with st.sidebar:
    st.markdown('### PORTFOLIO PULSE')
    st.caption('A clear view of your investments.')
    st.divider()
    st.markdown('**1 · Transaction export**')
    if upload_enabled:
        st.caption('Files are processed by this Streamlit server. Public security identifiers/tickers may be sent to price providers. Results are session-specific and temporary files are deleted after processing.')
    else:
        st.caption('This public release uses synthetic data. Private uploads are disabled pending approval of hosted market-data use.')
    upload_epoch = st.session_state.get('upload_epoch',0)
    uploaded = st.file_uploader('Trade Republic transaction CSV',type=['csv'],disabled=not upload_enabled,key=f'upload_{upload_epoch}')
    st.caption('UTF-8 CSV · up to 5 MB / 10,000 rows')
    st.markdown('**2 · Benchmark**')
    selection = st.selectbox('Compare with',['Global developed equities','Global all-country equities','No comparison'])
    benchmark = {'Global developed equities':'IWDA.AS','Global all-country equities':'VWCE.DE','No comparison':None}[selection]
    analyze = st.button('Analyze Portfolio',type='primary',use_container_width=True,disabled=not upload_enabled or uploaded is None)
    demo = st.button('Try with Demo Portfolio',use_container_width=True)
    if st.button('Clear session results',use_container_width=True):
        for key in ('model','pdf'):
            st.session_state.pop(key,None)
        st.session_state.pop(f'upload_{upload_epoch}',None)
        st.session_state['upload_epoch'] = upload_epoch+1
        st.rerun()
    st.divider()
    st.caption('Independent, unofficial project. Not affiliated with Trade Republic. Analytical information, without buy/sell recommendations.')

st.title('Portfolio Pulse')
st.markdown('Your investments, performance and income at a glance.')
if demo or analyze:
    # Remove stale results before a new run so a failed upload cannot show an old PDF.
    st.session_state.pop('model',None);st.session_state.pop('pdf',None)
    data,prices = fixture() if demo else (uploaded.getvalue(),None)
    try:
        with st.spinner('Reconstructing investments, income and matched performance…'):
            result = run_analysis(data,benchmark=benchmark,prices=prices)
            model = prepare(result,report_date=datetime.now(ZoneInfo('Europe/Berlin')).date())
            pdf = summary_pdf(model)
            st.session_state['model'] = model;st.session_state['pdf'] = pdf
    except AnalysisError as exc:
        st.error(str(exc))
    except Exception:
        st.error('The analysis could not finish safely. Check the export or try the synthetic demo.')

if 'model' not in st.session_state:
    st.info('Start with a transaction export or explore the synthetic demo portfolio.')
    st.markdown('### Less noise. More understanding.')
    st.write('See invested value, lifetime outcomes, returns and income together. Every figure has a scope explanation, and missing data stays visible.')
    st.stop()

model = st.session_state['model']; snap = model['snapshot']
heading,download = st.columns([3,1])
with heading:
    st.markdown(f'**Data health · {model["health"]}**')
    tx = snap['latest_transaction_date']
    st.caption(f"{'SYNTHETIC DEMO · fabricated transactions and market prices · ' if model['raw']['synthetic'] else ''}Transactions to {tx:%d %b %Y}" if tx else 'Transaction cutoff unavailable')
with download:
    st.download_button('Download Portfolio Summary (PDF)',st.session_state['pdf'],file_name='Portfolio_Pulse_Summary.pdf',mime='application/pdf',use_container_width=True)
with st.expander('Data health and coverage'):
    if model['issues']:
        for issue in model['issues']:st.write('• '+issue)
    else:st.write('No blocking issues in the tracked analytical scope.')
    st.caption('Tracked lifetime results may include derivatives and income. Historical returns and benchmark comparison cover only stocks/funds. Brokerage cash is excluded from tracked value. Open derivative quotes are disabled in this MVP, so derivative-dependent totals can be unavailable.')
    if model['valuation_missing']:st.warning('Holdings rankings cover only valued stocks/funds; unpriced positions are omitted.')

for offset in (0,4):
    columns = st.columns(4)
    for column,metric in zip(columns,model['metrics'][offset:offset+4]):
        with column:
            st.metric(metric.label,metric.display,help=metric.explanation)
            st.caption(metric.scope)
            if metric.value is None:
                st.caption('Unavailable — data requires review' if metric.status!='DISABLED' else 'Comparison disabled')

st.markdown('### Portfolio versus Benchmark')
st.caption('Stock/fund wealth · EUR · cash-flow-matched benchmark · excludes cash and derivatives')
nav = model['nav']
if not nav.empty:
    columns = {'stockfund_value_eur':'Actual stock/fund wealth'}
    if model['by_key']['benchmark'].value is not None:columns['benchmark_pme_value_eur']='Matched benchmark wealth'
    chart_data = nav[['date',*columns]].rename(columns=columns).melt('date',var_name='Series',value_name='Value')
    chart = alt.Chart(chart_data).mark_line(strokeWidth=2.5).encode(x=alt.X('date:T',title=None),
        y=alt.Y('Value:Q',title='EUR',scale=alt.Scale(zero=False)),color=alt.Color('Series:N',scale=alt.Scale(range=['#42cbea','#397df5']),legend=alt.Legend(orient='top',labelLimit=300)),
        tooltip=['date:T','Series:N',alt.Tooltip('Value:Q',format=',.2f')]).properties(height=245)
    st.altair_chart(chart,use_container_width=True)
    if model['periods']:
        period = st.segmented_control('Cumulative period TWR',[r['period_key'] for r in model['periods']],default=model['periods'][-1]['period_key'])
        selected = next((r for r in model['periods'] if r['period_key']==period),None)
        if selected:st.caption(f"{period} cumulative stock/fund TWR: {selected['portfolio_twr_pct']:.2f}% · established observation/date anchors")
else:st.info('Unavailable — data requires review. Historical stock/fund wealth cannot be presented reliably.')

income_col,holdings_col = st.columns([1.15,1])
with income_col:
    st.markdown('### Investment Income')
    st.caption(f"{snap['year']} actual net dividends + interest · outline/opacity marks partial months")
    monthly = pd.DataFrame(snap['months'])
    chart = alt.Chart(monthly).mark_bar().encode(x=alt.X('label:N',sort=[m['label'] for m in snap['months']],title=None),
        y=alt.Y('total:Q',title='EUR'),color=alt.value('#397df5'),opacity=alt.Opacity('status:N',scale=alt.Scale(domain=['complete','partial','unavailable'],range=[1,.45,0]),legend=None),
        tooltip=['label:N','status:N',alt.Tooltip('dividends:Q',format=',.2f'),alt.Tooltip('interest:Q',format=',.2f'),alt.Tooltip('total:Q',format=',.2f')]).properties(height=200)
    st.altair_chart(chart,use_container_width=True)
    st.caption('Complete = covered month-end. Partial = incomplete coverage. Uncovered months have no value; a covered zero is actual zero income.')
with holdings_col:
    st.markdown('### Largest Holdings')
    st.caption('Top 5 · share of valued stock/fund assets · no look-through')
    top=pd.DataFrame(snap['top_holdings'][:5])
    if not top.empty:
        chart=alt.Chart(top).mark_bar(cornerRadiusEnd=3,color='#42cbea').encode(y=alt.Y('name:N',sort='-x',title=None,axis=alt.Axis(labelLimit=145,labelFontSize=10)),x=alt.X('value:Q',title='EUR'),
            tooltip=['name:N',alt.Tooltip('value:Q',format=',.2f'),alt.Tooltip('weight_pct:Q',format='.1f')]).properties(height=200)
        st.altair_chart(chart,use_container_width=True)
    else:st.info('No supported valued holding ranking available.')
    with st.expander('See Top 10 holdings'):
        st.dataframe(pd.DataFrame(snap['top_holdings']).rename(columns={'name':'Holding','value':'Value EUR','weight_pct':'Weight %'}),hide_index=True,use_container_width=True)
        st.caption('Closed and derecognized positions are excluded. Unpriced holdings are not assigned zero.')

st.markdown('### What Stands Out?')
for insight in model['insights']:st.write('• '+insight)
with st.expander('Understanding Your Capital'):
    life=model['raw']['lifetime_metrics']
    for label,key in [('User-funded investment outflows','lifetime_user_funded_investment_outflows_eur'),('Gross acquisition outflows','lifetime_gross_investment_outflows_eur')]:
        value=life.get(key)
        st.write(f'{label}: €{value:,.2f}' if value is not None and model['raw']['accounting_status']=='COMPLETE' else f'{label}: Unavailable — data requires review')
    st.write('Net user capital committed subtracts canonical recovery, including interest, from user-funded investment outflows. Recovery is a lifetime accounting measure, not freely withdrawable cash. Promotional funding changes user capital separately from economic profit.')
st.caption('Portfolio Pulse · Independent analytical software · Missing values are never substituted with zero.')
