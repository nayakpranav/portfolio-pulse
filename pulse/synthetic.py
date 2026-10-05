"""Entirely fabricated transactions and prices, never based on a personal export."""
import csv
from io import StringIO

COLUMNS = ['datetime','date','account_type','category','type','asset_class','name','symbol','shares','price','amount','fee','tax','currency','original_amount','original_currency','fx_rate','description','transaction_id','counterparty_name','counterparty_iban','payment_reference','mcc_code']

def row(day, typ, *, i=1, asset='STOCK', shares='', price='', amount='', tax='', fee='', category='TRADING', description=''):
    data = dict.fromkeys(COLUMNS, '')
    data.update(datetime=day+'T10:00:00Z', date=day, account_type='SECURITIES', category=category,
                type=typ, asset_class=asset, name=f'Synthetic {"Global Fund" if asset == "FUND" else "Company"} {i}',
                symbol=f'ZZ00000000{i:02d}', shares=shares, price=price, amount=amount, fee=fee,
                tax=tax, currency='EUR', description=description or f'{typ} synthetic security')
    return data

def fixture(kind='demo'):
    rows = []
    count = 6 if kind == 'demo' else 1
    for i in range(1, count+1):
        rows.append(row('2025-01-06', 'BUY', i=i, asset='FUND' if (kind=='demo' and i == 1) or kind == 'etf_only' else 'STOCK', shares=10+i, price=20*i, amount=-(10+i)*20*i, fee=-1))
    if kind in {'demo','realized_sales'}:
        rows.append(row('2026-02-10','SELL',shares=3,price=24,amount=72,fee=-1,asset='FUND' if kind=='demo' else 'STOCK'))
    if kind in {'demo','dividends'}:
        for month in (2,4,6,8):
            rows.append(row(f'2026-{month:02d}-16','DIVIDEND',i=2 if kind=='demo' else 1,shares=12,amount=8+month,tax=-2,category='CASH'))
    if kind in {'demo','interest'}:
        for month in range(1,10):
            rows.append(row(f'2026-{month:02d}-20','INTEREST_PAYMENT',asset='',amount=2.5,tax=-0.5,category='CASH'))
    if kind == 'reinvestment':
        rows += [row('2026-04-15','DIVIDEND_REINVESTMENT',shares=.2,category='CORPORATE_ACTION',description='DIVIDEND_REINVESTMENT ZZ0000000001'),
                 row('2026-04-16','DIVIDEND',shares=11,amount=5,tax=-1,category='CASH',description='Dividend Reinvestment for ISIN ZZ0000000001'),
                 row('2026-04-16','DIVIDEND',shares=11,amount=-5,category='CASH',description='Dividend Reinvestment for ISIN ZZ0000000001')]
    if kind == 'unsupported_action':
        rows.append(row('2026-06-01','FREE_RECEIPT',shares=-11,category='DELIVERY'))
    if kind == 'incomplete_accounting':
        rows.append(row('2026-06-01','SELL',shares=30,amount=900,price=30))
    if kind == 'derivatives':
        rows += [row('2026-01-12','BUY',i=7,asset='DERIVATIVE',shares=2,price=5,amount=-10,fee=-1),row('2026-02-12','SELL',i=7,asset='DERIVATIVE',shares=2,price=7,amount=14,fee=-1)]
    if kind == 'open_derivatives':
        rows += [row('2026-01-12','BUY',i=i,asset='DERIVATIVE',shares=2,price=5,amount=-10,fee=-1) for i in range(7,14)]
        rows += [row('2026-02-12','SELL',i=7,asset='DERIVATIVE',shares=1,price=7,amount=7,fee=-1),
                 row('2026-02-16','DIVIDEND',amount=4,tax=-1,category='CASH'),
                 row('2026-03-20','INTEREST_PAYMENT',asset='',amount=2.5,tax=-.5,category='CASH')]
    # A source evidence cutoff is a fabricated cash movement, not invented income.
    rows.append(row('2026-09-30','CUSTOMER_INBOUND',asset='',amount=1,category='CASH'))
    if kind == 'short_history':
        rows = [row('2026-09-29','BUY',shares=11,price=20,amount=-220,fee=-1),rows[-1]]
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=COLUMNS)
    writer.writeheader()
    for index, item in enumerate(rows):
        item['transaction_id'] = f'synthetic-{kind}-{index}'
        writer.writerow(item)
    securities = {f'ZZ00000000{i:02d}': {'start':20*i,'end':20*i*(1.15 if i % 2 else .94)} for i in range(1,count+1)}
    if kind == 'missing_price':
        securities['ZZ0000000001']['missing'] = True
    snapshots={isin:dict(ticker=isin,history=[],calendar={},currency='EUR',history_status='OK',calendar_status='EMPTY_OR_UNAVAILABLE',diagnostic='Explicit fabricated distribution data',retrieved_at='2026-09-30T00:00:00+00:00') for isin in securities}
    if kind in {'demo','dividends'}:
        snapshots['ZZ0000000002' if kind=='demo' else 'ZZ0000000001']['history']=[dict(ex_date=f'2026-{month:02d}-16',dps=(8+month)/12) for month in (2,4,6,8)]
    return output.getvalue().encode(), {'asof':'2026-09-30','securities':securities,'dividend_snapshots':snapshots,'benchmark':{'start':100,'end':109},'benchmarks':{'IWDA.AS':{'start':100,'end':109},'VWCE.DE':{'start':100,'end':108},'SXR8.DE':{'start':100,'end':112}}}
