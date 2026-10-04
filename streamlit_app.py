"""One-page FolioLens UI. Session data is never placed in shared caches."""
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import re
from pathlib import Path
from pulse.benchmarks import PRESETS, validate_ticker
from pulse.charts import wealth_range
from pulse.mode import uploads_enabled, mode
# A hot deployment can rerun this file before refreshing imported helper modules.
import importlib
import pulse.private_config as _private_config
if not hasattr(_private_config,'personal_defaults') or getattr(_private_config,'CONFIG_SCHEMA_VERSION',0)!=2:
    importlib.reload(_private_config)
from pulse.private_config import event_candidates,export_digest,load_private_config,personal_defaults
from pulse.runner import validate_upload
import json
import altair as alt
import pandas as pd
import streamlit as st
from pulse.runner import run_analysis,AnalysisError
from pulse.synthetic import fixture
import pulse.adapter as _adapter
import pulse.pdf as _pdf
if getattr(_adapter,'MODEL_SCHEMA_VERSION',0)!=2:importlib.reload(_adapter)
if getattr(_pdf,'PDF_SCHEMA_VERSION',0)!=2:importlib.reload(_pdf)
from pulse.adapter import prepare
from pulse.pdf import summary_pdf

st.set_page_config(page_title='FolioLens',page_icon=str(Path(__file__).parent/'assets/favicon.png'),layout='wide')
st.markdown('''<style>
.block-container{max-width:1300px;padding-top:2rem;padding-bottom:2rem}
h1{letter-spacing:-1.5px} [data-testid="stMetric"]{background:#112238;border:1px solid #24415e;border-radius:12px;padding:16px}
[data-testid="stMetricValue"]{font-size:1.7rem} [data-testid="stMetricLabel"]{font-size:.9rem}
[data-testid="stSidebar"]{border-right:1px solid #24415e}
.period-performance{border:1px solid #24415e;background:#112238;border-radius:12px;padding:18px;margin:10px 0}
.period-performance strong{display:block;font-size:2rem;color:#42cbea;line-height:1.35}
@media(max-width:600px){.block-container{padding:1rem}[data-testid="stMetricValue"]{font-size:1.4rem}}
</style>''',unsafe_allow_html=True)

upload_enabled = uploads_enabled()
session_config=None
with st.sidebar:
    mark,brand=st.columns([1,4])
    with mark:st.image(str(Path(__file__).parent/'assets/foliolens.svg'),width=32)
    with brand:
        st.markdown('**FolioLens**')
        st.caption('Your investments, in focus.')
    upload_epoch = st.session_state.get('upload_epoch',0)
    uploaded = None
    if upload_enabled:
        st.markdown('**Transaction export**')
        uploaded = st.file_uploader('Trade Republic transaction CSV',type=['csv'],key=f'upload_{upload_epoch}')
        st.caption('UTF-8 CSV · up to 5 MB / 10,000 rows')
    else:
        st.caption('Public demo · real CSV analysis is available only in the documented local personal-use workflow.')
        st.link_button('Get FolioLens Personal for Windows','https://github.com/nayakpranav/portfolio-pulse/releases/latest')
    st.markdown('**Benchmark**')
    options = ['MSCI World ETF (default)','Global All-Country ETF','S&P 500 ETF']
    if upload_enabled: options.append('Custom Yahoo Finance ticker')
    options.append('No comparison')
    selection = st.selectbox('Compare with',options)
    benchmark = dict(zip(options[:3],PRESETS)).get(selection)
    custom_valid = True
    if selection == 'Custom Yahoo Finance ticker':
        custom = st.text_input('Provider-compatible ticker',placeholder='SPY, IWDA.AS or ^GSPC',max_chars=32)
        try:
            benchmark = validate_ticker(custom)
        except ValueError as exc:
            custom_valid = False
            if custom: st.error(str(exc))
        st.caption('Exact identity, adjusted prices and EUR conversion must validate. Price-only indices cannot provide the dividend-inclusive comparison.')
    if not upload_enabled:
        st.caption('Fabricated benchmark illustrations · custom tickers are local-only.')
    analyze = st.button('Analyze Portfolio',type='primary',use_container_width=True,disabled=uploaded is None) if upload_enabled else False
    if analyze and not custom_valid:
        st.session_state.pop('model',None);st.session_state.pop('pdf',None)
        st.error('Enter a valid Yahoo Finance ticker before analyzing.')
        analyze=False
    demo = st.button('Try with Demo Portfolio',type='secondary' if upload_enabled else 'primary',use_container_width=True,disabled=selection=='Custom Yahoo Finance ticker')
    if st.button('Clear session results',use_container_width=True):
        for key in list(st.session_state):
            if key in {'model','pdf','input_digest','twr_period','start_demo','analysis_scope'} or key.startswith(('event_','manual_')):
                st.session_state.pop(key,None)
        st.session_state.pop(f'upload_{upload_epoch}',None)
        st.session_state['upload_epoch'] = upload_epoch+1
        st.rerun()
    if upload_enabled:
        with st.expander('Processing and privacy'):
            st.write('Your CSV is transmitted to this Streamlit backend to calculate results. Public security identifiers/names and tickers may be requested from Yahoo Finance and OpenFIGI, which can reveal your holdings to them. Reports contain private financial information.')
            st.write('Worker files and caches are deleted after processing. Results and PDF downloads stay in this session until cleared or the session expires; no permanent portfolio storage or private shared cache is used. Clear removes the selected upload and analysis/download state. Use an authenticated backend for hosted personal analysis.')
    if uploaded is not None:
        content=uploaded.getvalue();digest=export_digest(content)
        if st.session_state.get('input_digest')!=digest:
            st.session_state.pop('model',None);st.session_state.pop('pdf',None)
            st.session_state.pop('analysis_scope',None)
            for key in list(st.session_state):
                if key.startswith(('event_','manual_')):st.session_state.pop(key,None)
            st.session_state['input_digest']=digest
        try:
            validate_upload(content)
            local_defaults=load_private_config(os.environ['FOLIOLENS_PRIVATE_CONFIG'],content) if os.environ.get('FOLIOLENS_PRIVATE_CONFIG') else personal_defaults(content)
            candidates=event_candidates(content)
            confirmations=[]
            prior=st.session_state.get('model',{}).get('raw',{})
            derivatives=prior.get('active_derivatives',[]) if not prior.get('synthetic') else []
            if candidates or derivatives:
                with st.expander('Private event / valuation review'):
                    for candidate in candidates:
                        st.text(f"Row {candidate['source_row']} · {candidate['date']}\n{candidate['name']} · {candidate['isin']}\nQuantity removed: {abs(candidate['quantity']):g}")
                        established=any(item.get('isin')==candidate['isin'] and item.get('security_name')==candidate['name'] and float(item.get('quantity',0))==candidate['quantity'] for item in local_defaults.get('known_events',[]))
                        if established:
                            st.caption('Previously verified private event evidence is loaded automatically. Canonical identity, zero-consideration and full-position checks still apply.')
                            continue
                        if st.checkbox('I confirm a full-position worthless write-off with no proceeds.',value=candidate['source_row'] in local_defaults.get('worthless_confirmations',[]),key=f"event_{digest}_{candidate['source_row']}"):
                            confirmations.append(candidate['source_row'])
                    st.caption('Confirmation does not waive canonical cash, full-position or prior-activity checks. Reanalyze after reviewing.')
                    for derivative in derivatives:
                        st.text(f"{derivative['security_name']} · {derivative['isin']} · quantity {derivative['current_quantity']:g}")
                    raw_quotes=st.text_area('Dated manual derivative EUR prices (JSON)',value=json.dumps(local_defaults.get('derivative_quotes',{})) if local_defaults.get('derivative_quotes') else '',key='manual_quotes',placeholder='{"ISIN": {"price_eur": 1.25, "date": "YYYY-MM-DD", "source": "Broker bid"}}') if derivatives else ''
            else:raw_quotes=''
            if not raw_quotes and local_defaults.get('derivative_quotes'):raw_quotes=json.dumps(local_defaults['derivative_quotes'])
            if len(raw_quotes.encode('utf-8'))>65536:raise ValueError('Private valuation input exceeds limit')
            session_config={'export_sha256':digest,'worthless_confirmations':confirmations,'known_events':local_defaults.get('known_events',[]),'derivative_quotes':json.loads(raw_quotes) if raw_quotes else {}}
        except (AnalysisError,ValueError,TypeError,OSError):
            st.error('Review the CSV or private valuation JSON before analyzing. No private input is logged.')
            analyze=False
    st.caption('Independent, unofficial project. Not affiliated with Trade Republic. Analytical information, without buy/sell recommendations.')

st.title('FolioLens')
st.markdown('Your investments, in focus.')
start_demo = st.session_state.pop('start_demo',False)
if demo or analyze or start_demo:
    # Remove stale results before a new run so a failed upload cannot show an old PDF.
    st.session_state.pop('model',None);st.session_state.pop('pdf',None)
    st.session_state.pop('analysis_scope',None)
    data,prices = fixture() if demo or start_demo else (uploaded.getvalue(),None)
    try:
        with st.spinner('Reconstructing investments, income and matched performance…'):
            result = run_analysis(data,benchmark=benchmark,prices=prices,private_config=session_config if analyze else None)
            model = prepare(result,report_date=datetime.now(ZoneInfo('Europe/Berlin')).date())
            pdf = summary_pdf(model)
            st.session_state['model'] = model;st.session_state['pdf'] = pdf
    except AnalysisError as exc:
        st.error(str(exc))
    except Exception:
        st.error('The analysis could not finish safely. Check the export or try the synthetic demo.')

if 'model' not in st.session_state:
    st.markdown('### See the story behind your investments.')
    st.write('Explore performance, recognized investment income and a downloadable portfolio report in one clear view. Every figure includes its scope, and missing data stays visible.')
    if not upload_enabled:
        if st.button('Try with Demo Portfolio',type='primary',key='landing_demo'):
            st.session_state['start_demo'] = True
            st.rerun()
        st.caption('The public demo uses fabricated transactions and prices. Real CSV analysis is available through the documented local personal-use workflow.')
        st.link_button('Download the one-click Windows application','https://github.com/nayakpranav/portfolio-pulse/releases/latest')
        st.caption('Extract the ZIP and double-click Open FolioLens Personal.exe. No Python installation or terminal commands are needed.')
    st.stop()

model = st.session_state['model']
if model.get('model_schema_version')!=_adapter.MODEL_SCHEMA_VERSION:
    model=prepare(model['raw'],report_date=model['snapshot']['report_date'])
    st.session_state['model']=model;st.session_state['pdf']=summary_pdf(model)
if upload_enabled:
    scope = st.radio('Analysis scope',['Stocks & funds','Full portfolio'],horizontal=True,
                     index=0 if model['analysis_scope']=='stocks_funds' else 1,key='analysis_scope')
    scope_key='stocks_funds' if scope=='Stocks & funds' else 'full_portfolio'
    if scope_key!=model['analysis_scope']:
        model=prepare(model['raw'],report_date=model['snapshot']['report_date'],analysis_scope=scope_key)
        st.session_state['model']=model;st.session_state['pdf']=summary_pdf(model)
st.caption(f"Analysis scope: {model['scope_label']}. Returns, benchmark and holdings always cover stocks/funds. Capital committed and recovery cover the full investment ecosystem.")
if any(v is None for v in model['full_totals'].values()):
    if model['dependencies']['ecosystem_accounting'] and model['dependencies']['missing_derivative']:
        st.warning('Full portfolio value and lifetime profit are unavailable: current derivative quotations are missing. Independently valid stock/fund results remain available.')
    else:
        st.warning('Full portfolio results are incomplete. Accounting and valuation causes are listed under Data health and coverage.')
if model['dependencies']['accounting_checks'] or model['dependencies']['unexplained_accounting']:
    from pulse.scopes import blocker_message
    for check in model['dependencies']['accounting_checks']:st.error(blocker_message(check))
snap = model['snapshot']
heading,download = st.columns([3,1])
with heading:
    st.markdown(f'**Data health · {model["health"]}**')
    tx = snap['latest_transaction_date']
    st.caption(f"{'SYNTHETIC DEMO · fabricated transactions and market prices · ' if model['raw']['synthetic'] else ''}Transactions to {tx:%d %b %Y}" if tx else 'Transaction cutoff unavailable')
with download:
    st.download_button('Download Portfolio Summary (PDF)',st.session_state['pdf'],file_name='FolioLens_Summary.pdf',mime='application/pdf',use_container_width=True)
with st.expander('Data health and coverage'):
    if model['issues']:
        for issue in model['issues']:st.text('• '+issue)
    else:st.write('No blocking issues in the tracked analytical scope.')
    st.caption('Tracked lifetime results may include derivatives and income. Historical returns and benchmark comparison cover stocks/funds, excluding brokerage cash. Derivatives require a verified quote or explicit dated manual valuation; missing valuations block dependent totals.')
    if model['dependencies']['missing_stock']:st.warning('Holdings rankings cover only valued stocks/funds; unpriced stock/fund positions are omitted. See all current holdings below.')

for offset in (0,4):
    columns = st.columns(4)
    for column,metric in zip(columns,model['metrics'][offset:offset+4]):
        with column:
            st.metric(metric.label,metric.display,help=metric.explanation)
            st.caption(metric.scope)
            if metric.value is None:
                st.caption(metric.status.replace('_',' ').capitalize() if metric.status!='DISABLED' else 'Comparison disabled')

st.markdown('### Portfolio versus Benchmark')
st.caption('Stock/fund wealth · EUR · cash-flow-matched benchmark · excludes cash and derivatives')
nav = model['nav']
if not nav.empty:
    columns = {'stockfund_value_eur':'Actual stock/fund wealth'}
    if model['by_key']['benchmark'].value is not None:columns['benchmark_pme_value_eur']=model['raw']['benchmark_name']
    chart_data = nav[['date',*columns]].rename(columns=columns).melt('date',var_name='Series',value_name='Value')
    low, high = wealth_range(chart_data['Value'])
    st.caption('Wealth in EUR · padded vertical range' + (' · axis does not start at zero' if low != 0 else ''))
    names=list(columns.values())
    chart = alt.Chart(chart_data).mark_line(strokeWidth=2.8).encode(x=alt.X('date:T',title='Date',axis=alt.Axis(format='%b %Y',tickCount=6)),
        y=alt.Y('Value:Q',title='Wealth (EUR)',scale=alt.Scale(domain=[low,high],zero=False,nice=False)),color=alt.Color('Series:N',title=None,scale=alt.Scale(domain=names,range=['#42cbea','#397df5']),legend=alt.Legend(orient='top',direction='vertical',columns=1,labelLimit=320)),
        strokeDash=alt.StrokeDash('Series:N',scale=alt.Scale(domain=names,range=[[1,0],[7,4]]),legend=None),
        tooltip=['date:T','Series:N',alt.Tooltip('Value:Q',format=',.2f')]).properties(height=330)
    st.altair_chart(chart,use_container_width=True)
    last=nav.iloc[-1]
    st.caption('Endpoint wealth · '+ ' · '.join(f"{name}: €{float(last[col]):,.2f}" for col,name in columns.items() if pd.notna(last[col])))
else:st.info('Unavailable — data requires review. Historical stock/fund wealth cannot be presented reliably.')

st.markdown('### Cumulative Period Performance')
period = st.segmented_control('Cumulative period TWR',['1M','3M','YTD','1Y','MAX'],default='MAX',key='twr_period')
selected = next((r for r in model['periods'] if r['period_key']==period),None)
value=f"{selected['portfolio_twr_pct']:.2f}%" if selected else 'Unavailable'
st.markdown(f'<div class="period-performance">{period or "Select a period"} · Cumulative stock/fund TWR<strong>{value}</strong></div>',unsafe_allow_html=True)
if selected:
    st.caption(f"{period} cumulative stock/fund TWR: {selected['portfolio_twr_pct']:.2f}% · {pd.Timestamp(selected['effective_start_date']):%d %b %Y} to {pd.Timestamp(selected['end_date']):%d %b %Y}")
else:st.caption('This period lacks reliable observations or accounting coverage. Missing performance is not zero.')
st.caption('Cumulative TWR adjusts for cash flows across the selected period. MWR is annualized; the headline TWR covers the full available history. Cash and derivatives are excluded.')

income_col,holdings_col = st.columns([1.15,1])
with income_col:
    st.markdown('### Investment Income')
    st.caption(f"{snap['year']} recognized net investment income (dividends + interest) · outline/opacity marks partial months")
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
    with st.expander('All current stock/fund holdings'):
        st.dataframe(pd.DataFrame(model['holdings']).rename(columns={'name':'Holding','quantity':'Quantity','value':'Value EUR','quote_date':'Quote date','status':'Valuation'}),hide_index=True,use_container_width=True)

st.markdown('### What Stands Out?')
for insight in model['insights']:
    # Uploaded security names are data; prevent Markdown links/images from rendering.
    st.markdown('• '+re.sub(r'([\\`*_{}\[\]()#+.!|<>])',r'\\\1',insight))
with st.expander('Understanding Your Capital'):
    life=model['raw']['lifetime_metrics']
    for label,key in [('User-funded investment outflows','lifetime_user_funded_investment_outflows_eur'),('Gross acquisition outflows','lifetime_gross_investment_outflows_eur')]:
        value=life.get(key)
        st.write(f'{label}: €{value:,.2f}' if value is not None and model['dependencies']['ecosystem_accounting'] else f'{label}: Unavailable — data requires review')
    st.write('Net user capital committed subtracts canonical recovery, including interest, from user-funded investment outflows. Recovery is a lifetime accounting measure, not freely withdrawable cash. Promotional funding changes user capital separately from economic profit.')
st.caption('FolioLens · Independent analytical software · Missing values are never substituted with zero.')
