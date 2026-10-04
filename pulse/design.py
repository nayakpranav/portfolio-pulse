"""Shared presentation tokens; no accounting or user data."""
COLORS = dict(background='#081727',surface='#112238',border='#24415e',text='#f6f9fd',
              muted='#91a7bc',actual='#42cbea',benchmark='#397df5',mint='#a8ebbc',warning='#f8bc62')
TYPE = dict(product=40,section=21,primary=32,secondary=26,label=14,body=16,small=13)
SPACE = (4,8,12,16,24,32)
RADIUS = 12
CSS_TOKENS = ':root{'+''.join('--fl-'+key+':'+value+';' for key,value in COLORS.items())+'--fl-radius:12px;--fl-gap:16px}'
KPI_CSS = '''.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin:24px 0 0}
.metric{min-width:0;padding:22px;background:var(--fl-surface);border:1px solid var(--fl-border);border-radius:var(--fl-radius)}
.metric h2{font-size:.875rem;line-height:1.45;color:var(--fl-muted);min-height:2.9em;margin:0 0 10px;font-weight:600}
.metric-value{font-size:2rem;line-height:1.25;font-weight:750;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.metric small{display:block;margin-top:10px;line-height:1.5}.metric details{font-size:.85rem;margin-top:12px}
.metrics-secondary{margin-top:16px;margin-bottom:32px;border-top:1px solid var(--fl-border);padding-top:8px}
.metrics-secondary .metric{background:transparent;border:0;border-radius:0;padding:16px 22px}
.metrics-secondary .metric-value{font-size:1.625rem;color:var(--fl-mint)}
@media(max-width:1000px){.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:560px){.metrics{grid-template-columns:1fr}.metric{padding:20px}.metrics-secondary .metric{padding:16px 20px}.metric h2{min-height:0}}'''

STREAMLIT_CSS = CSS_TOKENS+'''
.block-container{max-width:1300px;padding-top:2rem;padding-bottom:2rem}
h1{letter-spacing:-1px}h3{font-size:1.35rem;letter-spacing:-.2px}
[data-testid="stSidebar"]{border-right:1px solid var(--fl-border)}
[data-testid="stMetricValue"]{font-variant-numeric:tabular-nums;font-size:2rem;line-height:1.25;font-weight:700}
[data-testid="stMetricValue"]>div{white-space:normal;overflow-wrap:anywhere}
[data-testid="stMetricLabel"]{font-size:.875rem;min-height:2.9em;white-space:normal;color:var(--fl-muted)}
[data-testid="stMetricLabel"] p{white-space:normal;overflow-wrap:anywhere}
.st-key-primary_kpis [data-testid="stMetric"]{background:var(--fl-surface);border:1px solid var(--fl-border);border-radius:12px;padding:22px;min-height:160px}
.st-key-secondary_kpis{border-top:1px solid var(--fl-border);padding-top:12px;margin:8px 0 20px}
.st-key-secondary_kpis [data-testid="stMetric"]{background:transparent;border:0;padding:12px 22px}
.st-key-secondary_kpis [data-testid="stMetricValue"]{font-size:1.625rem;color:var(--fl-mint)}
.period-performance{border-top:1px solid var(--fl-border);border-bottom:1px solid var(--fl-border);padding:18px 0;margin:10px 0}
.period-performance strong{display:block;font-size:2rem;color:var(--fl-actual);line-height:1.35;font-variant-numeric:tabular-nums}
@media(max-width:1100px){.st-key-primary_kpis [data-testid="stHorizontalBlock"],.st-key-secondary_kpis [data-testid="stHorizontalBlock"]{flex-wrap:wrap}
.st-key-primary_kpis [data-testid="stColumn"],.st-key-secondary_kpis [data-testid="stColumn"]{flex:1 1 calc(50% - 16px);min-width:0}}
@media(max-width:600px){.block-container{padding:1rem}.st-key-primary_kpis [data-testid="stColumn"],.st-key-secondary_kpis [data-testid="stColumn"]{flex-basis:100%}
[data-testid="stMetricLabel"]{min-height:0}.st-key-primary_kpis [data-testid="stMetric"]{min-height:0}}
'''
