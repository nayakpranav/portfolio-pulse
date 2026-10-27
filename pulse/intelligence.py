"""Read-only intelligence over canonical results. No accounting or provider calls.

Every monetary bridge is checked against independent canonical evidence. Missing
inputs are never passed through the engine's permissive safe_float helper.
"""
from collections import defaultdict, Counter
import math
import re
import numpy as np
import pandas as pd
from pulse.analytics import finite


def total(values):
    values=[finite(v) for v in values]
    return math.fsum(values) if all(finite(v) is not None for v in values) else None


def agrees(a,b):
    return a is not None and b is not None and abs(a-b)<=max(1e-6,8*math.ulp(max(abs(a),abs(b))))


def attribution(model, scoped):
    raw=model['raw'];life=raw['lifetime_metrics'];deps=model['dependencies']
    income_ok=model['by_key']['income'].value is not None
    stock_ok=deps['stock_accounting'];ecosystem_ok=deps['ecosystem_accounting']
    derivative_ok=ecosystem_ok and model['manual_derivatives']['confirmed']==model['manual_derivatives']['active']
    specs=[('Unrealized stock/fund P/L','lifetime_stock_open_pl_eur',stock_ok and deps['stock_valued']),
        ('Realized stock/fund P/L','lifetime_stock_realized_pl_eur',stock_ok),
        ('Recognized net dividends','lifetime_net_dividend_recovery_eur',income_ok),
        ('Unrealized derivative P/L','lifetime_derivative_open_pl_eur',derivative_ok),
        ('Realized derivative P/L incl. settlements','lifetime_derivative_realized_pl_all_positions_eur',ecosystem_ok),
        ('Recognized net interest','lifetime_net_interest_recovery_eur',income_ok)]
    components=[dict(component=label,value=finite(life.get(key)) if valid else None) for label,key,valid in specs]
    # A localized projection must never lend its stock-valid flag to full totals.
    if model.get('excluded_securities'):
        for item in components:item['value']=None
    authoritative=model['full_totals']['profit'];component_sum=total(r['value'] for r in components)
    projected={r['isin']:r for r in model['holdings']}
    manual={r['isin']:r for r in model['manual_derivatives']['rows']}
    rows=[];source=scoped.get('wealth_contribution',[])
    counts=Counter((r.get('instrument_type'),r.get('isin')) for r in source)
    for r in source:
        kind=r.get('instrument_type');active=r.get('position_status')=='ACTIVE';isin=r.get('isin','')
        stock=kind in {'STOCK','FUND','STOCK/FUND'};derivative=kind=='DERIVATIVE'
        valid=stock_ok if stock else ecosystem_ok
        valid=valid and counts[(kind,isin)]==1
        opened=finite(r.get('open_pl_eur')) if valid else None
        if active and stock and projected.get(isin,{}).get('open_pl') is None:opened=None
        if active and derivative and not manual.get(isin,{}).get('confirmed'):opened=None
        realized=finite(r.get('realized_pl_eur')) if valid else None
        income=finite(r.get('net_income_eur')) if valid and income_ok else None
        contribution=finite(r.get('economic_contribution_eur')) if valid else None
        if not agrees(total([opened,realized,income]),contribution):contribution=None
        rows.append(dict(name=r.get('security_name','Security'),isin=isin,instrument_type=kind,
            position_status=r.get('position_status','Unknown'),unrealized=opened,realized=realized,
            net_income=income,contribution=contribution,
            status='Reconciled canonical components' if contribution is not None else 'Incomplete component evidence'))
    row_sum=total(r['contribution'] for r in rows)
    full_rows=not model.get('excluded_securities') and agrees(row_sum,authoritative)
    available=agrees(component_sum,authoritative) and full_rows
    securities=[r for r in rows if r['instrument_type'] in {'STOCK','FUND','STOCK/FUND','DERIVATIVE'}]
    known=[r for r in securities if r['contribution'] is not None]
    groups=[]
    for label,selected in [('Currently open securities',[r for r in securities if r['position_status']=='ACTIVE']),
                            ('Fully closed securities',[r for r in securities if r['position_status']=='CLOSED']),
                            ('Other / income-only securities',[r for r in securities if r['position_status'] not in {'ACTIVE','CLOSED'}])]:
        groups.append(dict(group=label,contribution=total(r['contribution'] for r in selected),securities=len(selected)))
    return dict(available=available,authoritative=authoritative,components=components,
        component_sum=component_sum,difference=authoritative-component_sum if authoritative is not None and component_sum is not None else None,
        rows=rows,groups=groups,rankings_partial=len(known)!=len(securities),
        winners=sorted([r for r in known if r['contribution']>0],key=lambda r:r['contribution'],reverse=True)[:5],
        detractors=sorted([r for r in known if r['contribution']<0],key=lambda r:r['contribution'])[:5],
        note='Full investment ecosystem: canonical FIFO open/realized P/L plus recognized net dividends and interest. Fees, taxes and settlements are already included; promotional personal benefit is not added again. '
             +('Components and historical-security contributions reconcile to authoritative lifetime economic profit.' if available else 'Complete attribution unavailable: missing valuations, accounting/income evidence or a reconciliation difference. Known components remain separately qualified.')
             +(' Historical-security rows cover the explicitly unaffected stock/fund scope only.' if model.get('excluded_securities') else ''))


def dated_history(nav, columns):
    if nav.empty or not {'date',*columns}.issubset(nav.columns):return None
    frame=nav[['date',*columns]].copy();frame['date']=pd.to_datetime(frame['date'],errors='coerce',utc=True).dt.tz_localize(None).dt.normalize()
    if frame.date.isna().any() or frame.date.duplicated().any() or not frame.date.is_monotonic_increasing:return None
    if not set(pd.bdate_range(frame.date.iloc[0],frame.date.iloc[-1])).issubset(set(frame.date)):return None
    for col in columns:
        frame[col]=pd.to_numeric(frame[col],errors='coerce')
        if not np.isfinite(frame[col]).all():return None
    return frame


def capital_bridge(model, scoped):
    out=dict(available=False,series=[],years=[],scope=model['returns_scope_label'],
        note='Unavailable: continuous valued stock/fund history and independently matched dated cash flows are required.')
    cols=['stockfund_value_eur','external_flow_to_stockfund_eur','internal_dividend_reinvestment_flow_eur','twr_neutralizing_flow_eur','net_dividend_return_eur','price_coverage_pct']
    frame=dated_history(model['nav'],cols)
    if frame is None or (frame.price_coverage_pct<99.999).any() or not model['dependencies']['stock_accounting']:return out
    ledger=scoped.get('lifetime_cashflow_ledger')
    if ledger is None:return out
    expected={c:defaultdict(float) for c in ['external','reinvested','dividends']}
    dates=set(frame.date);acquisition_dates=[]
    for row in ledger:
        sleeve,kind=row.get('asset_scope'),row.get('cashflow_type')
        if sleeve=='STOCK_FUND' and kind in {'BUY','SELL','DIVIDEND_REINVESTMENT'}:
            field='core_recovery_eur' if kind=='SELL' else 'acquisition_outflow_eur'
            value=finite(row.get(field));bucket='reinvested' if kind=='DIVIDEND_REINVESTMENT' else 'external'
            if kind=='SELL' and value is not None:value=-value
        elif kind=='DIVIDEND_NET':value=finite(row.get('core_recovery_eur'));bucket='dividends'
        elif sleeve=='STOCK_FUND':return out
        else:continue
        try:dt=pd.Timestamp(row.get('event_date'))
        except (ValueError,TypeError):return out
        if value is None or pd.isna(dt):return out
        dt=(dt.tz_convert('UTC').tz_localize(None) if dt.tzinfo else dt).normalize()
        if dt not in dates:return out  # Never shift weekend/missing flows.
        if sleeve=='STOCK_FUND' and kind=='BUY' and value>0:acquisition_dates.append(dt)
        expected[bucket][dt]+=value
    if not acquisition_dates or (frame.loc[frame.date<min(acquisition_dates),'stockfund_value_eur'].abs()>1e-8).any():return out
    for bucket,column in [('external','external_flow_to_stockfund_eur'),('reinvested','internal_dividend_reinvestment_flow_eur'),('dividends','net_dividend_return_eur')]:
        values=np.array([expected[bucket].get(d,0.) for d in frame.date])
        if not np.allclose(values,frame[column],rtol=0,atol=1e-6):return out
    if not np.allclose(frame.twr_neutralizing_flow_eur,frame.external_flow_to_stockfund_eur+frame.internal_dividend_reinvestment_flow_eur,rtol=0,atol=1e-6):return out
    frame['capital']=frame.external_flow_to_stockfund_eur.cumsum()
    previous=0.;years=[]
    for year,group in frame.groupby(frame.date.dt.year,sort=True):
        external=math.fsum(group.external_flow_to_stockfund_eur);end=float(group.stockfund_value_eur.iloc[-1])
        gain=end-previous-external;dividends=math.fsum(group.net_dividend_return_eur);reinvested=math.fsum(group.internal_dividend_reinvestment_flow_eur)
        years.append(dict(year=int(year),beginning_wealth=previous,net_external_invested=external,wealth_gain_loss=gain,
            ending_wealth=end,net_dividends=dividends,reinvested_dividends=reinvested,
            investment_result_including_dividends=gain+dividends-reinvested,
            net_distributions_outside_sleeve=dividends-reinvested,through=group.date.iloc[-1].date().isoformat()))
        previous=end
    return dict(out,available=True,series=[dict(date=d.isoformat(),capital_eur=float(v)) for d,v in zip(frame.date,frame.capital)],years=years,
        note='Stock/fund sleeve, from first recorded acquisition with zero opening wealth. Cumulative capital = ordinary acquisition outflows less net sales; internal dividend reinvestments are excluded. Acquisition fees/taxes and promotional funding follow canonical flows. Not deposits, remaining cost basis or net USER capital. '
             'Yearly wealth gain = ending minus beginning wealth minus external flows; it includes reinvested distributions, but excludes dividends paid outside security wealth. Total investment result adds net dividends and removes reinvestment flows; it is a monetary amount, not MWR/TWR. '
             'All dated flow/reinvestment/dividend series reconcile to the canonical lifetime ledger. Historical price-estimate qualifications still apply.')


def benchmark_risk(model):
    out=dict(available=False,series=[],maximum=None,current=None,date=None,underwater_days=None,
        note='Comparative drawdown unavailable: aligned, validated EUR adjusted benchmark history and reliable portfolio TWR are required.')
    if not model['drawdown']['available'] or model['by_key']['benchmark'].value is None or model['raw'].get('benchmark_identity',{}).get('warning'):return out
    frame=dated_history(model['nav'],['benchmark_price_total_return_eur'])
    nav=model['nav']
    if frame is None or len(frame)<2 or (frame.benchmark_price_total_return_eur<=0).any() or 'benchmark_daily_return_pct' not in nav:return out
    price=frame.benchmark_price_total_return_eur;returns=pd.to_numeric(nav.benchmark_daily_return_pct,errors='coerce')/100
    if not np.isfinite(returns.iloc[1:]).all() or not np.allclose(returns.iloc[1:],price.pct_change(fill_method=None).iloc[1:],rtol=0,atol=1e-9):return out
    if pd.notna(returns.iloc[0]) and abs(returns.iloc[0])>1e-12:return out
    index=price/price.iloc[0];linked=np.concatenate(([1.],np.cumprod(1+returns.iloc[1:].to_numpy())))
    if not np.allclose(index,linked,rtol=1e-9,atol=1e-9):return out
    risk=(index/index.cummax()-1)*100;portfolio=model['drawdown']['series']
    if len(portfolio)!=len(frame) or any(pd.Timestamp(r['date'])!=d for r,d in zip(portfolio,frame.date)):return out
    last_peak=frame.loc[risk.abs()<=1e-9,'date'].iloc[-1]
    return dict(out,available=True,maximum=min(0,float(risk.min())),current=min(0,float(risk.iloc[-1])),date=frame.date.iloc[-1].date(),
        underwater_days=int((frame.date.iloc[-1]-last_peak).days),
        series=[dict(date=d.isoformat(),portfolio_drawdown_pct=p['drawdown_pct'],benchmark_drawdown_pct=min(0,float(b))) for d,p,b in zip(frame.date,portfolio,risk)],
        note='Matched dates: stock/fund cash-flow-adjusted TWR versus the benchmark EUR adjusted total-return proxy. Benchmark risk uses prices/linked returns, never the cash-flow-matched PME balance. Canonical carried closes and disclosed portfolio historical estimates remain. No derivative historical returns are inferred.')


def company_key(value):
    # Exact normalized text only. Do not fuzzy-match companies or strip legal
    # suffixes, currencies, geographies or infer identity from an ISIN prefix.
    return re.sub(r'[^\w]+',' ',str(value or '').casefold()).strip()


def underlying_exposure(model, scoped):
    from pulse.derivative_display import derivative_label
    stock_rows=[r for r in scoped.get('holdings',[]) if r.get('position_status')=='ACTIVE' and r.get('asset_class')=='STOCK']
    projected={r['isin']:r for r in model['holdings']};groups={};aliases=defaultdict(set);isin_keys={};unmapped=[]
    def group(key,name):
        return groups.setdefault(key,dict(company=name,stocks=[],derivatives=[],instruments=[],sources=set(),missing=0))
    for r in stock_rows:
        isin=r.get('isin');identity=str(r.get('ticker_match_status',''))
        verified=(identity=='EXACT_ISIN' or identity.startswith('OPENFIGI_EXACT_ISIN_') or identity=='OPENFIGI_EXACT_ISIN'
                  or bool(model['raw'].get('synthetic') and identity=='SYNTHETIC_CONTROLLED_INPUT'))
        company=r.get('ticker_matched_name') if verified else None
        if not company and model['raw'].get('synthetic'):company=r.get('security_name')
        if not company:
            unmapped.append(dict(name=r.get('security_name','Stock'),isin=isin,reason='Verified issuer identity unavailable'));continue
        key=company_key(company);isin_keys[isin]=key
        for alias in [company,r.get('security_name')]:
            if company_key(alias):aliases[company_key(alias)].add(key)
        item=group(key,company);h=projected.get(isin,{})
        value=finite(h.get('value')) if h.get('open_pl') is not None and model['dependencies']['stock_accounting'] else None
        item['stocks'].append(value);item['instruments'].append(isin);item['sources'].add('Exact-ISIN canonical stock identity')
    metadata=model['raw'].get('derivative_metadata',{})
    for r in model['manual_derivatives']['rows']:
        isin=r['isin'];pos=next((p for p in model['raw'].get('active_derivatives',[]) if p.get('isin')==isin),{})
        meta=metadata.get(isin,{})
        exact=meta.get('identity')=='EXACT_ISIN' and meta.get('isin')==isin
        label=derivative_label(pos,model['raw'].get('derivative_ledger',[]),meta)
        name=label.split(' — ')[0] if label.endswith(' — '+str(isin)) else ''
        explicit_key=isin_keys.get(meta.get('underlying_isin')) if exact else None
        matches=aliases.get(company_key(name),set())
        if explicit_key:key=explicit_key;source='Verified exact underlying ISIN'
        elif name and len(matches)==1:key=next(iter(matches));source='Exact derivative ISIN; explicit underlying text matches verified stock alias'
        elif name and not matches:key='description:'+company_key(name);source='Exact derivative ISIN; explicit underlying description (stock linkage unavailable)'
        else:
            unmapped.append(dict(name=r['name'],isin=isin,reason='Ambiguous or missing explicit underlying identity'));continue
        item=group(key,name);item['derivatives'].append(r['value'] if r['confirmed'] and model['dependencies']['ecosystem_accounting'] else None)
        item['instruments'].append(isin);item['sources'].add(source)
    rows=[];denominator=model['full_totals']['value'] if model['report_status']['full_complete'] else None
    for item in groups.values():
        direct=total(item['stocks']);derivatives=total(item['derivatives']);combined=total([direct,derivatives])
        known=math.fsum(v for v in item['stocks']+item['derivatives'] if v is not None)
        rows.append(dict(company=item['company'],direct_stock_value=direct,confirmed_derivative_value=derivatives,
            allocated_market_value=combined,known_subtotal=known,
            full_portfolio_share_pct=combined/denominator*100 if combined is not None and denominator is not None and denominator>0 else None,
            instruments=', '.join(item['instruments']),source='; '.join(sorted(item['sources'])),
            status='Mapped valued instruments; not complete company sensitivity' if combined is not None else 'Partial: an associated instrument lacks a reliable valuation'))
    return dict(rows=sorted(rows,key=lambda r:r['known_subtotal'],reverse=True),unmapped=unmapped,full_denominator=denominator,
        funds_excluded=sum(r.get('position_status')=='ACTIVE' and r.get('asset_class')=='FUND' for r in scoped.get('holdings',[])),
        note='Allocated Market Value by Underlying, in EUR: direct stocks plus confirmed derivative instrument values. This is NOT delta exposure, leveraged notional or economic sensitivity. Stock linkage requires exact normalized issuer/alias text or a verified underlying ISIN; no fuzzy company matching. Different spellings can remain separate. '
             'Shares use complete tracked portfolio value only; they need not sum to 100% because funds/unmapped instruments are outside this map. No ETF look-through, currency/geographic inference, Greeks or financing-level estimates.')


def income_intelligence(model, scoped):
    source=model['raw'].get('forward_dividends',{});forecast=model['forward_dividends'];projections={r.get('isin'):r for r in source.get('projection',[])}
    holdings={r['isin']:r for r in model['holdings']};rows=[];eligible=[]
    zero_growth=finite(source.get('growth_assumption_pct'))==0
    for r in scoped.get('holdings',[]):
        if r.get('position_status')!='ACTIVE' or r.get('asset_class') not in {'STOCK','FUND'}:continue
        isin=r.get('isin');p=projections.get(isin,{});h=holdings.get(isin,{})
        events=[e for e in source.get('forecast',[]) if e.get('isin')==isin]
        valid_status=str(p.get('external_dividend_status','')).startswith(('EXTERNAL_HISTORY_FOUND','DECLARED_UPCOMING_FOUND','NO_EXTERNAL_DIVIDEND_EVIDENCE','APPARENTLY_ACCUMULATING_NAME_AND_NO_HISTORY'))
        net=finite(p.get('growth_adjusted_forward_12m_net_dividend_eur'))
        reason='Current-position canonical zero-growth distribution estimate'
        if not forecast['available'] or not zero_growth or not valid_status or 'UNSCHEDULED' in str(p.get('external_dividend_status','')) or net is None:reason='Incomplete or stale distribution/FX/net-income evidence'
        elif not agrees(finite(p.get('current_quantity')),finite(r.get('current_quantity'))):reason='Forecast/current position quantity cannot be matched'
        elif any(e.get('method')=='BROKER_RECEIPT_SEASONALITY_ESTIMATE' or pd.isna(pd.to_datetime(e.get('ex_dividend_date'),errors='coerce')) or pd.Timestamp(e['ex_dividend_date']).date()<=forecast['asof'] for e in events):reason='Prior earned entitlement or receipt-seasonality estimate is not a current-position run-rate'
        elif h.get('open_pl') is None or finite(h.get('value')) is None or h['value']<=0:reason='Reliable current EUR valuation unavailable'
        else:eligible.append(dict(net=net,value=h['value'],basis=h.get('basis')))
        covered=reason=='Current-position canonical zero-growth distribution estimate'
        rows.append(dict(name=r.get('security_name','Security'),isin=isin,eligible=covered,known_net=net if covered else None,reason=reason))
    net=total(r['net'] for r in eligible) if eligible else None
    value=total(r['value'] for r in eligible) if eligible else None
    basis=total(r['basis'] for r in eligible) if eligible and all(finite(r['basis']) is not None and r['basis']>0 for r in eligible) else None
    full_coverage=len(eligible)==len(rows) and bool(rows) and model['composition']['valued_count']==model['composition']['active_count'] and forecast['status']=='Known estimate'
    receipts=[];foreign=[]
    if model['by_key']['income'].value is not None:
        for r in scoped.get('dividends',[]):
            gross,received=finite(r.get('gross_dividend_eur')),finite(r.get('net_dividend_eur'))
            if gross is not None and gross>0 and received is not None and finite(r.get('reconciliation_error')) is not None and abs(r['reconciliation_error'])<=1e-6:receipts.append((gross,received))
            tax=finite(r.get('foreign_withholding_tax_eur'))
            if tax is not None and r.get('foreign_withholding_source') not in {None,'','UNAVAILABLE_CSV_ONLY','NOT_SEPARATELY_CLASSIFIED_FOR_ORDINARY_CASH_DIVIDEND'}:foreign.append(tax)
    gross=math.fsum(r[0] for r in receipts);received=math.fsum(r[1] for r in receipts)
    return dict(rows=rows,eligible_count=len(eligible),active_count=len(rows),known_net=net,eligible_value=value,eligible_basis=basis,
        eligible_current_yield_pct=net/value*100 if net is not None and value is not None and value>0 else None,
        eligible_yield_on_cost_pct=net/basis*100 if net is not None and basis is not None and basis>0 else None,
        complete_portfolio_yield_pct=net/value*100 if full_coverage and value is not None and value>0 else None,
        complete=full_coverage,receipt_count=len(receipts),total_receipt_count=len(scoped.get('dividends',[])),
        observed_net_retention_pct=received/gross*100 if gross>0 else None,
        observed_gross_minus_net=gross-received if receipts else None,
        separately_identified_foreign_withholding=math.fsum(foreign) if foreign else None,foreign_coverage_count=len(foreign),
        note='Stock/fund forward yield uses only matched current quantities, net-estimable zero-growth canonical distributions and reliably valued assets in the SAME eligible cohort. Yield on cost uses that cohort’s remaining acquisition basis; neither is MWR/TWR. '
             'A partial-cohort yield is not a complete portfolio forecast. Closed entitlements, prior earned payments and broker receipt seasonality are excluded, so this numerator can differ from the existing forward known subtotal. Known zero distributions remain zero; unknown amounts are not zero. '
             'Historical retention uses paired reconciled canonical gross/net receipts, excluding interest. Gross-minus-net includes all recorded tax/refund effects, not foreign withholding alone. Separately identified foreign tax is only a known subtotal; no reclaim eligibility or tax advice is implied.')


def build_intelligence(model, scoped):
    return dict(attribution=attribution(model,scoped),capital=capital_bridge(model,scoped),
        benchmark_risk=benchmark_risk(model),underlying=underlying_exposure(model,scoped),income=income_intelligence(model,scoped))
