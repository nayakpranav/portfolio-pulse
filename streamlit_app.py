"""One-page FolioLens UI. Session data is never placed in shared caches."""
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import time
import re
from pathlib import Path
import importlib
import pulse.charts as _charts
if getattr(_charts,'CHART_SCHEMA_VERSION',0)!=2:importlib.reload(_charts)
import pulse.reporting as _reporting
if getattr(_reporting,'PRESENTATION_SCHEMA_VERSION',0)!=4:importlib.reload(_reporting)
import pulse.composition as _composition
if getattr(_composition,'COMPOSITION_SCHEMA_VERSION',0)!=2:importlib.reload(_composition)
from pulse.benchmarks import PRESETS, validate_ticker
from pulse.charts import wealth_range, benchmark_alias
from pulse.design import STREAMLIT_CSS
from pulse.mode import uploads_enabled, mode
# A hot deployment can rerun this file before refreshing imported helper modules.
import pulse.private_config as _private_config
if not hasattr(_private_config,'personal_defaults') or getattr(_private_config,'CONFIG_SCHEMA_VERSION',0)!=4:
    importlib.reload(_private_config)
from pulse.private_config import event_candidates,export_digest,load_private_config,personal_defaults
from pulse.event_review import review as event_review,remember
from pulse.runner import validate_upload
import json
import altair as alt
import pandas as pd
import streamlit as st
from pulse.runner import run_analysis,AnalysisError
import pulse.synthetic as _synthetic
if getattr(_synthetic,'SYNTHETIC_SCHEMA_VERSION',0)!=2:importlib.reload(_synthetic)
from pulse.synthetic import fixture
import pulse.adapter as _adapter
import pulse.pdf as _pdf
if getattr(_adapter,'MODEL_SCHEMA_VERSION',0)!=10:importlib.reload(_adapter)
if getattr(_pdf,'PDF_SCHEMA_VERSION',0)!=11:importlib.reload(_pdf)
from pulse.adapter import prepare
from pulse.pdf import summary_pdf
import pulse.html_report as _html_report
if getattr(_html_report,'HTML_SCHEMA_VERSION',0)!=11:importlib.reload(_html_report)
from pulse.html_report import html_report, HTML_SCHEMA_VERSION
from pulse.reporting import HOLDING_CSS, holding_cards, table_frame, HOLDING_EXPLANATION
from pulse.composition import COMPOSITION_CSS, composition_panel

def store_reports(model):
    # Compute both first; a failure must not leave downloads from different runs.
    mark=time.perf_counter();pdf=summary_pdf(model);pdf_seconds=time.perf_counter()-mark
    mark=time.perf_counter();html=html_report(model);html_seconds=time.perf_counter()-mark
    st.session_state.setdefault('workflow_timings',{}).update(pdf_seconds=pdf_seconds,html_seconds=html_seconds)
    st.session_state.update(model=model,pdf=pdf,html=html,
                            export_schema=(_pdf.PDF_SCHEMA_VERSION,HTML_SCHEMA_VERSION))

st.set_page_config(page_title='FolioLens',page_icon=str(Path(__file__).parent/'assets/favicon.png'),layout='wide')
presentation_started=time.perf_counter()
st.markdown('<style>'+STREAMLIT_CSS+'</style>',unsafe_allow_html=True)
st.markdown('<style>'+HOLDING_CSS+'</style>',unsafe_allow_html=True)

upload_enabled = uploads_enabled()
session_config=None
with st.sidebar:
    mark,brand=st.columns([1,4])
    with mark:st.image(str(Path(__file__).parent/'assets/foliolens.svg'),width=32)
    with brand:
        st.markdown('**FolioLens**')
        st.caption('Your investments, in focus.')
    if mode()=='personal':st.caption('Personal · '+os.environ.get('FOLIOLENS_VERSION','development'))
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
        for key in ('model','pdf','html','export_schema'):st.session_state.pop(key,None)
        st.error('Enter a valid Yahoo Finance ticker before analyzing.')
        analyze=False
    demo = st.button('Try with Demo Portfolio',type='secondary' if upload_enabled else 'primary',use_container_width=True,disabled=selection=='Custom Yahoo Finance ticker')
    if st.button('Clear session results',use_container_width=True):
        for key in list(st.session_state):
            if key in {'model','pdf','html','export_schema','input_digest','twr_period','performance_view','start_demo','analysis_scope','market_replay','workflow_timings'} or key.startswith(('event_','manual_','intelligence_')):
                st.session_state.pop(key,None)
        st.session_state.pop(f'upload_{upload_epoch}',None)
        st.session_state['upload_epoch'] = upload_epoch+1
        st.rerun()
    if upload_enabled:
        with st.expander('Processing and privacy'):
            st.write('Your CSV is transmitted to this Streamlit backend to calculate results. Public security identifiers/names and tickers may be requested from Yahoo Finance and OpenFIGI, which can reveal your holdings to them. Reports contain private financial information.')
            st.write('Worker files and caches are deleted after processing. Results and PDF/HTML downloads stay in this session until cleared or the session expires; no permanent portfolio storage or private shared cache is used. Clear removes the upload and both downloads. Files you download remain on your device. Use an authenticated backend for hosted personal analysis.')
            if mode()=='personal':st.write('Confirmed exceptional-event evidence is encrypted in your local Windows profile and retained for future exports. Clear session results keeps this evidence; it does not retain your full export or report.')
    if uploaded is not None:
        content=uploaded.getvalue();digest=export_digest(content)
        if st.session_state.get('input_digest')!=digest:
            for key in ('model','pdf','html','export_schema','market_replay','workflow_timings'):st.session_state.pop(key,None)
            st.session_state.pop('analysis_scope',None)
            for key in list(st.session_state):
                if key.startswith(('event_','manual_')):st.session_state.pop(key,None)
            st.session_state['input_digest']=digest
        try:
            evidence_started=time.perf_counter()
            validate_upload(content)
            local_defaults=load_private_config(os.environ['FOLIOLENS_PRIVATE_CONFIG'],content) if os.environ.get('FOLIOLENS_PRIVATE_CONFIG') else personal_defaults(content)
            candidates=[c for c in event_review(content,local_defaults) if not c['verified']]
            confirmations=[]
            if candidates:
                with st.expander('Action required: review transaction'):
                    for candidate in candidates:
                        st.text(f"Row {candidate['source_row']} · {candidate['date']}\n{candidate['name']} · {candidate['isin']}\nQuantity removed: {abs(candidate['quantity']):g}")
                        st.caption('This delivery has no established economic classification. A transfer, exchange or custody removal is not automatically a worthless loss.')
                        if not candidate['eligible']:
                            st.warning('Worthless-loss confirmation is unavailable: '+candidate['reason'].replace('_',' ').lower()+'. Provide complete broker evidence for this event; it remains under review.')
                            continue
                        if st.checkbox('The broker evidence confirms a full-position worthless write-off with no proceeds. Remember this exact event privately.',value=candidate['source_row'] in local_defaults.get('worthless_confirmations',[]),key=f"event_{digest}_{candidate['source_row']}"):
                            confirmations.append(candidate['source_row'])
                    st.caption('Canonical identity, cash, full-position and prior-activity checks apply. Successful local confirmations are remembered for the same transaction and prior history in future exports; hosted confirmations stay in this session.')
            quotes=dict(local_defaults.get('derivative_quotes',{}),**st.session_state.get('manual_quote_values',{}))
            if len(json.dumps(quotes).encode('utf-8'))>65536:raise ValueError('Private valuation input exceeds limit')
            session_config={'export_sha256':digest,'worthless_confirmations':confirmations,'known_events':local_defaults.get('known_events',[]),'derivative_quotes':quotes}
            st.session_state.setdefault('workflow_timings',{})['sidebar_evidence_seconds']=time.perf_counter()-evidence_started
        except (AnalysisError,ValueError,TypeError,OSError):
            st.error('The CSV or private profile could not validate. Protected evidence recovery was attempted; no event was approved automatically. Restore the private profile if needed, or review the CSV and optional valuations. No private input is logged.')
            analyze=False
    st.caption('Independent, unofficial project. Not affiliated with Trade Republic. Analytical information, without buy/sell recommendations.')

st.title('FolioLens')
st.markdown('Your investments, in focus.')
start_demo = st.session_state.pop('start_demo',False)
if demo or analyze or start_demo:
    workflow_started=time.perf_counter()
    # Remove stale results before a new run so a failed upload cannot show an old PDF.
    for key in ('model','pdf','html','export_schema','market_replay','manual_batch_notice','manual_batch_warning'):st.session_state.pop(key,None)
    st.session_state.pop('analysis_scope',None)
    data,prices = fixture() if demo or start_demo else (uploaded.getvalue(),None)
    try:
        with st.spinner('Reconstructing investments, income and matched performance…'):
            result = run_analysis(data,benchmark=benchmark,prices=prices,private_config=session_config if analyze else None,capture=bool(analyze))
            if analyze:
                try:remember(data,session_config,result)
                except (OSError,ValueError,TypeError):
                    st.warning('The analysis finished, but verified evidence could not be saved privately. This event may need review after restarting; no private paths or data are logged.')
            mark=time.perf_counter();model = prepare(result,report_date=datetime.now(ZoneInfo('Europe/Berlin')).date())
            st.session_state.setdefault('workflow_timings',{})['prepare_seconds']=time.perf_counter()-mark
            store_reports(model)
            if analyze:
                from pulse.replay import retain
                st.session_state['market_replay']=retain(data,benchmark,session_config,result)
            st.session_state['workflow_timings']['analyze_to_reports_seconds']=time.perf_counter()-workflow_started
            if analyze and session_config.get('worthless_confirmations'):st.rerun()
    except AnalysisError as exc:
        st.error(str(exc))
    except Exception:
        st.error('The analysis could not finish safely. Check the export or try the synthetic demo.')

presentation_started=time.perf_counter()
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
    model=prepare(model['raw'],report_date=model['snapshot']['report_date'],analysis_scope=model.get('analysis_scope'))
    store_reports(model)
if st.session_state.get('export_schema')!=(_pdf.PDF_SCHEMA_VERSION,HTML_SCHEMA_VERSION) or 'html' not in st.session_state:
    store_reports(model)
if upload_enabled:
    scope = st.radio('Analysis scope',['Stocks & funds','Full portfolio'],horizontal=True,
                     index=0 if model['analysis_scope']=='stocks_funds' else 1,key='analysis_scope')
    scope_key='stocks_funds' if scope=='Stocks & funds' else 'full_portfolio'
    if scope_key!=model['analysis_scope']:
        model=prepare(model['raw'],report_date=model['snapshot']['report_date'],analysis_scope=scope_key)
        store_reports(model)
st.caption(f"Analysis scope: {model['scope_label']}. Returns, benchmark and holdings always cover stocks/funds. Capital committed and recovery cover the full investment ecosystem.")
if model.get('excluded_securities'):
    st.warning('Partial analysis: entire histories of securities requiring review are excluded from the displayed stock/fund metrics, chart and matched benchmark. Complete portfolio figures remain unavailable.')
    for excluded in model['excluded_securities']:st.text('Requires review: '+excluded['name']+' · '+excluded['isin'])
if any(v is None for v in model['full_totals'].values()):
    if model['dependencies']['ecosystem_accounting'] and model['dependencies']['missing_derivative']:
        st.warning('Full portfolio value and lifetime profit are unavailable: current derivative quotations are missing. Independently valid stock/fund results remain available.')
    else:
        st.warning('Full portfolio results are incomplete. Accounting and valuation causes are listed under Data health and coverage.')
if model['dependencies']['accounting_checks'] or model['dependencies']['unexplained_accounting']:
    from pulse.scopes import blocker_message
    for check in model['dependencies']['accounting_checks']:st.error(blocker_message(check))
snap = model['snapshot']
heading,download_pdf,download_html = st.columns([2,1,1])
with heading:
    st.markdown('**'+model['report_status']['headline']+'**')
    st.caption(model['report_status']['quality'])
    st.caption(model['valuation_summary'])
    if model['manual_derivatives']['active']:
        from pulse.derivative_display import manual_summary
        st.caption(manual_summary(model['manual_derivatives']))
    tx = snap['latest_transaction_date']
    st.caption(f"{'SYNTHETIC DEMO · fabricated transactions and market prices · ' if model['raw']['synthetic'] else ''}Transactions to {tx:%d %b %Y}" if tx else 'Transaction cutoff unavailable')
with download_pdf:
    st.download_button('Download PDF Report',st.session_state['pdf'],file_name='FolioLens_Summary.pdf',mime='application/pdf',use_container_width=True,on_click='ignore')
with download_html:
    st.download_button('Download HTML Report',st.session_state['html'],file_name='FolioLens_Report.html',mime='text/html',use_container_width=True,on_click='ignore')
with st.expander('Data health and coverage'):
    st.write(model['report_status']['accounting'])
    st.caption('Historical/source quality · '+model['health']+' · current valuation completeness is reported separately.')
    if model['issues']:
        for issue in model['issues']:st.text('• '+issue)
    else:st.write('No blocking issues in the tracked analytical scope.')
    for transaction in model['raw'].get('review_transactions',[]):
        st.text(f"Review row {transaction['source_row']} · {transaction['date']} · {transaction['type']}\n{transaction['name']} · {transaction['isin']} · quantity {transaction['quantity']}\n{transaction['reason']}")
    for action in model['raw'].get('corporate_action_audit',[]):
        if action.get('validation_status')!='PASS':
            st.text(f"Corporate-action review · {action.get('corporate_action_type','')} · {action.get('old_name','')}\n{action.get('warning_error','Incomplete action evidence; FIFO mutation was rejected.')}")
    st.caption('Tracked lifetime results include the applicable investments and income. Historical returns and benchmark comparison cover stocks/funds, excluding brokerage cash. Missing inputs block only dependent totals.')
    from pulse.evidence import history_summary
    st.write(history_summary(model['history_evidence']))
    st.caption(model['history_evidence']['note'])
    if model['history_evidence']['rows']:
        st.dataframe(pd.DataFrame(model['history_evidence']['rows']),hide_index=True,use_container_width=True)
    if model['dependencies']['missing_stock']:st.warning('Holdings rankings cover only valued stocks/funds; unpriced stock/fund positions are omitted. See all current holdings below.')
if upload_enabled and not model['raw']['synthetic'] and model['manual_derivatives']['active']:
    with st.expander('Advanced: optional dated derivative valuations'):
        from pulse.valuations import today, remember_quotes, MAX_QUOTE_AGE_DAYS
        from pulse.manual_batch import drafts, validate, DEFAULT_SOURCE
        st.caption(f'Optional only. Valid stock/fund results do not require this. No automatic derivative scraper is enabled. Observations must be positive EUR per unit, dated within {MAX_QUOTE_AGE_DAYS} calendar days; never enter total position value, acquisition cost or an old execution price.')
        if uploaded is not None:
            st.caption('Edit all prices together. Empty date/source cells use the batch defaults. Existing dated evidence keeps its original date/source unless you explicitly replace it. Empty price cells do not remove saved evidence.')
            from pulse.derivative_display import manual_summary
            st.write(manual_summary(model['manual_derivatives']))
            previous=(session_config or {}).get('derivative_quotes',{})
            rows=drafts(model['manual_derivatives'],previous)
            with st.form('manual_batch_form'):
                defaults_date=st.date_input('Common observation date',value=today(),max_value=today(),key='manual_common_date')
                defaults_source=st.text_input('Common observation source',value=DEFAULT_SOURCE,max_chars=160,key='manual_common_source')
                edited=st.data_editor(pd.DataFrame(rows),hide_index=True,num_rows='fixed',use_container_width=True,key='manual_batch_editor',
                    disabled=['Instrument','ISIN','Quantity','Status'],
                    column_config={'Instrument':st.column_config.TextColumn(width='large'),
                        'EUR per unit':st.column_config.NumberColumn(format='%.6f',min_value=0.000001,max_value=100000.),
                        'Observation date':st.column_config.DateColumn(format='YYYY-MM-DD'),
                        'Source':st.column_config.TextColumn(width='medium',max_chars=160)})
                common_all=st.checkbox('Replace every priced row’s date and source with the batch defaults',key='manual_common_all')
                confirmed=st.checkbox('I checked every entered instrument, EUR-per-unit price, observation date and source in this batch.',key='manual_batch_confirm')
                persist=st.checkbox('Remember privately on this computer until the observations expire',disabled=mode()!='personal',key='manual_batch_remember')
                save=st.form_submit_button('Save and update portfolio',type='primary')
            if save:
                edited_rows=edited.to_dict('records')
                if common_all:
                    for row in edited_rows:row.update({'Observation date':defaults_date,'Source':defaults_source})
                accepted,errors=validate(edited_rows,[r['ISIN'] for r in rows],defaults_date,defaults_source)
                if not custom_valid:errors.append((0,'Enter a valid selected benchmark ticker before updating the portfolio. No benchmark was substituted.'))
                for row,reason in errors:st.error((f'Row {row}: ' if row else '')+reason+' Draft inputs are retained; no batch was applied.')
                if not confirmed:
                    st.error('Confirm the complete batch before applying observations. Your draft inputs are retained.')
                elif not errors and not accepted:
                    st.info('Enter at least one observed price. No values were invented or changed.')
                elif not errors:
                    changed=dict(session_config or {});changed['derivative_quotes']=dict(previous,**accepted)
                    started=time.perf_counter()
                    try:
                        from pulse.replay import recompute, retain
                        with st.spinner('Updating canonical accounting and both reports…'):
                            result,reason=recompute(uploaded.getvalue(),benchmark=benchmark,config=changed,envelope=st.session_state.get('market_replay'),run=run_analysis)
                            scope=model['analysis_scope']
                            updated=prepare(result,report_date=today(),analysis_scope=scope)
                            store_reports(updated)
                            st.session_state['market_replay']=retain(uploaded.getvalue(),benchmark,changed,result) if reason else st.session_state.get('market_replay')
                            values=dict(st.session_state.get('manual_quote_values',{}));values.update(accepted)
                            st.session_state['manual_quote_values']=values
                            st.session_state['workflow_timings']['manual_update_to_reports_seconds']=time.perf_counter()-started
                            st.session_state['workflow_timings']['manual_update_mode']=result.get('performance',{}).get('mode','full_analysis')
                        if persist:
                            try:saved=remember_quotes(uploaded.getvalue(),accepted)
                            except (OSError,ValueError,TypeError):saved=False
                            if not saved:st.session_state['manual_batch_warning']='Updated in this session; encrypted persistence could not finish. No private input was logged.'
                            else:st.session_state.pop('manual_batch_warning',None)
                        if reason:st.session_state['manual_batch_notice']='Full-analysis fallback: '+reason
                        else:st.session_state['manual_batch_notice']='Batch applied through canonical offline replay. No external market-data requests were made.'
                        st.rerun()
                    except (AnalysisError,ValueError,OSError,TypeError):
                        st.error('The batch could not update safely. Previous reports and draft inputs are retained. No observations were saved. Use Analyze Portfolio if the captured inputs are incomplete.')

if upload_enabled and st.session_state.get('manual_batch_notice'):
    st.info(st.session_state['manual_batch_notice'])
if upload_enabled and st.session_state.get('manual_batch_warning'):
    st.warning(st.session_state['manual_batch_warning'])

for offset in (0,4):
    with st.container(key='primary_kpis' if offset==0 else 'secondary_kpis'):
        columns = st.columns(4)
        for column,metric in zip(columns,model['metrics'][offset:offset+4]):
            with column:
                st.metric(metric.label,metric.display,help=metric.explanation)
                st.caption(metric.scope)
                if metric.value is None:
                    st.caption(metric.status.replace('_',' ').capitalize() if metric.status!='DISABLED' else 'Comparison disabled')

st.markdown('### Portfolio versus Benchmark')
st.caption(model.get('returns_scope_label','Stocks & funds')+' wealth · EUR · cash-flow-matched benchmark · excludes cash and derivatives')
view=st.segmented_control('Historical chart view',['Wealth (EUR)','Drawdown (%)'],default='Wealth (EUR)',key='performance_view')
nav = model['nav']
if view=='Drawdown (%)':
    from pulse.analytics import risk_summary
    risk=model['drawdown']
    if risk['available']:
        risk_data=pd.DataFrame(risk['series'])
        comparison=model['intelligence']['benchmark_risk']
        lower=min(-1,risk['maximum']*1.15,(comparison['maximum'] or 0)*1.15)
        base=alt.Chart(risk_data).encode(x=alt.X('date:T',title='Date',axis=alt.Axis(format='%b %Y',tickCount=6)),y=alt.Y('drawdown_pct:Q',title='TWR drawdown (%)',scale=alt.Scale(domain=[lower,0],nice=False)),tooltip=[alt.Tooltip('date:T',title='Date'),alt.Tooltip('drawdown_pct:Q',title='Drawdown (%)',format='.2f')])
        chart=base.mark_area(color='#42cbea',opacity=.18)+base.mark_line(color='#42cbea',strokeWidth=2.5)+alt.Chart(pd.DataFrame({'zero':[0]})).mark_rule(color='#91a7bc').encode(y='zero:Q')
        if comparison['available']:
            benchmark_risk_data=pd.DataFrame(comparison['series'])
            chart+=alt.Chart(benchmark_risk_data).mark_line(color='#397df5',strokeDash=[7,4],strokeWidth=2).encode(x='date:T',y='benchmark_drawdown_pct:Q',tooltip=[alt.Tooltip('date:T'),alt.Tooltip('benchmark_drawdown_pct:Q',title='Benchmark drawdown (%)',format='.2f')])
        st.altair_chart(chart.properties(height=330),use_container_width=True)
        st.write(risk_summary(risk))
        if comparison['available']:
            st.caption(f"Cyan: stock/fund TWR · dashed blue: benchmark EUR adjusted returns. Benchmark max: {comparison['maximum']:.2f}% · current: {comparison['current']:.2f}%")
        st.caption(comparison['note'])
        st.caption(f"Latest observation: {risk['date']:%d %b %Y} · {risk['scope']}")
    else:st.info(risk_summary(risk))
    st.caption(risk['note'])
elif not nav.empty:
    columns = {'stockfund_value_eur':'Actual stock/fund wealth'}
    if model['by_key']['benchmark'].value is not None:columns['benchmark_pme_value_eur']=benchmark_alias(model)
    capital=model['intelligence']['capital'];wealth_data=nav.copy()
    if capital['available'] and st.checkbox('Cumulative net stock/fund capital invested',key='intelligence_capital'):
        wealth_data['capital_eur']=[r['capital_eur'] for r in capital['series']]
        columns['capital_eur']='Cumulative net stock/fund capital invested'
        st.caption(capital['note'])
    chart_data = wealth_data[['date',*columns]].rename(columns=columns).melt('date',var_name='Series',value_name='Value')
    low, high = wealth_range(chart_data['Value'])
    st.caption('Wealth in EUR · padded vertical range' + (' · axis does not start at zero' if low != 0 else ''))
    names=list(columns.values())
    styles={'stockfund_value_eur':('#42cbea',[1,0],3),'benchmark_pme_value_eur':('#397df5',[7,4],2.3),'capital_eur':('#91a7bc',[3,4],1.6)}
    chart = alt.Chart(chart_data).mark_line().encode(x=alt.X('date:T',title='Date',axis=alt.Axis(format='%b %Y',tickCount=6)),
        y=alt.Y('Value:Q',title='Wealth (EUR)',scale=alt.Scale(domain=[low,high],zero=False,nice=False)),color=alt.Color('Series:N',title=None,scale=alt.Scale(domain=names,range=[styles[c][0] for c in columns]),legend=alt.Legend(orient='top',direction='vertical',columns=1,labelLimit=320)),
        strokeDash=alt.StrokeDash('Series:N',scale=alt.Scale(domain=names,range=[styles[c][1] for c in columns]),legend=None),
        size=alt.Size('Series:N',scale=alt.Scale(domain=names,range=[styles[c][2] for c in columns]),legend=None),
        tooltip=['date:T','Series:N',alt.Tooltip('Value:Q',format=',.2f')]).properties(height=330)
    st.altair_chart(chart,use_container_width=True)
    last=wealth_data.iloc[-1]
    st.caption('Endpoint wealth · '+ ' · '.join(f"{name}: €{float(last[col]):,.2f}" for col,name in columns.items() if pd.notna(last[col])))
else:st.info('Unavailable — data requires review. Historical stock/fund wealth cannot be presented reliably.')
with st.expander('Benchmark identity and comparison'):
    st.text(model['raw'].get('benchmark_name','No comparison'))
    st.caption('Exact selected instrument · EUR conversion · cash-flow-matched benchmark MWR. The chart legend uses a concise alias; the instrument and calculations are unchanged.')

st.markdown('### Cumulative Period Performance')
period = st.segmented_control('Cumulative period TWR',['1M','3M','YTD','1Y','MAX'],default='MAX',key='twr_period')
selected = next((r for r in model['periods'] if r['period_key']==period),None)
value=f"{selected['portfolio_twr_pct']:.2f}%" if selected else 'Unavailable'
st.markdown(f'<div class="period-performance">{period or "Select a period"} · Cumulative stock/fund TWR<strong>{value}</strong></div>',unsafe_allow_html=True)
if selected:
    st.caption(f"{period} cumulative stock/fund TWR: {selected['portfolio_twr_pct']:.2f}% · {pd.Timestamp(selected['effective_start_date']):%d %b %Y} to {pd.Timestamp(selected['end_date']):%d %b %Y}")
else:st.caption('This period lacks reliable observations or accounting coverage. Missing performance is not zero.')
st.caption('Cumulative TWR adjusts for cash flows across the selected period. MWR is annualized; the headline TWR covers the full available history. Cash and derivatives are excluded. Period controls change TWR only; the wealth chart retains its full available history.')

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
    from pulse.analytics import forward_display
    forecast=model['forward_dividends']
    st.markdown('**'+forecast['label']+'** · '+forward_display(forecast))
    st.caption(forecast['status']+' · '+forecast['note'])
    with st.expander('Forward dividend coverage details'):
        st.caption('Estimated future dividends only; not recognized income, interest or a guaranteed payment schedule. Missing net estimates remain unavailable.')
        if model['dividend_evidence']:
            coverage=pd.DataFrame(model['dividend_evidence']).rename(columns={'name':'Holding','isin':'ISIN','ticker':'Verified ticker','status':'Canonical distribution status','known_net_estimate':'Known net estimate (EUR)','reason':'Evidence and limitation'})
            st.dataframe(coverage,hide_index=True,use_container_width=True)
        else:st.write('No eligible active stock/fund distribution evidence is available.')
with holdings_col:
    st.markdown('### Portfolio Composition')
    st.markdown('<style>'+COMPOSITION_CSS+'</style>'+composition_panel(model['composition']),unsafe_allow_html=True)

st.markdown('### Your Five Largest Holdings')
st.caption('Valued stocks/funds · no constituent look-through. Thin bars show size relative to the largest holding; percentages show allocation. '+HOLDING_EXPLANATION)
st.markdown(holding_cards(snap['top_holdings']),unsafe_allow_html=True)
with st.expander('See Top 10 holdings'):
    st.dataframe(table_frame(snap['top_holdings']),hide_index=True,use_container_width=True)
    st.caption('Closed and derecognized positions are excluded. Unpriced holdings are not assigned zero.')
with st.expander('All current stock/fund holdings'):
    st.dataframe(table_frame(model['holdings'],True),hide_index=True,use_container_width=True)

if st.checkbox('Show Portfolio Intelligence',key='intelligence_show'):
    from pulse.intelligence_view import streamlit_intelligence
    streamlit_intelligence(model)

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
if upload_enabled:
    # Aggregate-only, session-owned evidence. Includes server presentation
    # construction, not an unverifiable claim about browser paint/network time.
    timings=st.session_state.setdefault('workflow_timings',{})
    timings['presentation_build_seconds']=time.perf_counter()-presentation_started
    with st.expander('Analysis timing diagnostics'):
        st.caption('Monotonic wall-clock seconds; function stages are inclusive and must not be added together. CPU is process time; transport elapsed includes network wait. No instruments, prices or transaction values are recorded here.')
        st.json(dict(workflow=timings,analysis=model['raw'].get('performance',{}),providers=model['raw'].get('provider_diagnostics',{})))
