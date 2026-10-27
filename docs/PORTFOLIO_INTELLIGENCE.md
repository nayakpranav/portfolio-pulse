# Portfolio Intelligence — v1.0.14

Baseline: stable v1.0.13, commit `50c14df7a1037b860692e0a4304df441bcfb837a`.
This is an optional research presentation over existing canonical outputs. It does
not replace the eight headline metrics, initiate provider requests, or change the
126 retained V6.7.8 definitions. The owner has approved production promotion.

## Profit attribution

The full-ecosystem bridge reads the six canonical lifetime components: stock/fund
open P/L, stock/fund realized P/L, recognized net dividends, derivative open P/L,
derivative realized P/L including settlements, and recognized net interest.
Their unrounded sum and the separately exported historical security contributions
must both agree with authoritative lifetime economic profit (floating-point
tolerance EUR 0.000001). A residual is disclosed, never reassigned to an invented
category. Acquisition fees, taxes, reinvestment and settlements are already
included by the engine. Promotional personal benefit is not added to profit.

Open and fully closed security groups, known winners/detractors and a sortable
history retain realized/unrealized/income distinctions. Missing current quotes
disable the affected open contribution; they are not converted to zero. Known
historical realized contributions can remain visible. Localized accounting
projections are explicitly scoped and cannot certify full-portfolio attribution.

## Diversification and underlying allocation

Effective holdings is `1 / sum((value / total_value) ** 2)` over unrounded canonical
stock/fund market values. It requires all active positions to have reliable dated
valuations, and a positive total. It is an HHI equal-weight equivalent of nominal
concentration, not independent bets, correlation-adjusted diversification or risk.
Funds count as individual securities. Existing top-five percentages continue to
describe the reliably valued subset when coverage is incomplete.

Allocated Market Value by Underlying combines direct stock values with confirmed
manual derivative instrument values. Derivative descriptions must come from the
exact derivative ISIN. Direct-stock linkage requires a verified underlying ISIN
or exact normalized issuer/transaction alias text tied to a verified stock identity.
Similar names are not fuzzy-matched; spelling differences may remain separate.
Each row discloses its evidence. Missing values stay unavailable. Percentages use
complete tracked portfolio value only and need not sum to 100%: funds and unmapped
instruments are outside the map. This is not delta exposure, leveraged notional,
economic sensitivity, ETF look-through, country or currency exposure.

## Capital deployment

The optional third wealth series uses canonical daily external stock/fund flows,
independently reconciled by date against the lifetime cash-flow ledger: ordinary
BUY acquisition outflows minus SELL net recovery. Internal dividend reinvestments
are excluded and separately reconciled, as are recognized net dividends and TWR
neutralizing flows. No flow is shifted across a missing date. Continuous valued
history, a supported opening acquisition and resolved stock accounting are required.

Calendar-year wealth gain = ending wealth minus beginning wealth minus net
external flows. This measures the change in security wealth and excludes dividends
paid outside holdings. Investment result including dividends = wealth gain plus
net dividends minus reinvestment flows. The corresponding net distribution outside
the sleeve = net dividends minus reinvestments; both bridge identities reconcile.
First-year opening wealth is zero only for supported history from its recorded
acquisitions; later years begin at the prior observed year-end. A partial year is
labelled with its latest observation. These are monetary bridges, not vintage IRRs,
deposits, user capital committed or remaining acquisition basis. MWR/TWR differences
do not prove skill, causal market timing or a timing advantage.

## Benchmark risk

Existing portfolio TWR drawdown is unchanged. Comparative benchmark drawdown uses
`benchmark_price_total_return_eur` and reconciles its linked daily returns on the
same dates as portfolio risk. It never uses the cash-flow-matched PME balance.
Invalid identity/currency/price-only benchmarks, gaps or inconsistent returns make
the comparison unavailable. Both series share one negative axis. Benchmark current
time under water is calendar days since its last observed peak. Canonical carried
closes, asynchronous closing times and historical price estimates remain caveats;
no historical derivative performance is inferred.

## Income intelligence

Eligible-cohort current yield divides canonical zero-growth forward net estimates
by current reliably valued assets for the **same matched current-position cohort**.
Yield on cost uses that cohort's positive remaining acquisition basis. Quantity
mismatches, stale evidence, unresolved distribution status, past earned entitlements,
broker-receipt seasonality and missing valuation/FX/net inputs are excluded rather
than treated as zero. Known canonical zero distributions remain zero. The existing
forward known subtotal is unchanged and may include other earned entitlements, so
it can differ from this narrower yield numerator. Coverage counts and excluded-row
reasons accompany each yield; complete stock/fund yield is unavailable unless every
required position and forecast is covered. Estimates exclude interest and are not
guaranteed future payments.

Observed net/gross retention uses paired reconciled historical gross/net dividend
receipts. Gross minus net includes all recorded tax/refund effects, not foreign tax
alone. Foreign withholding is shown only as a separately identified known subtotal
with its row coverage. Retention can exceed 100% after refunds. This is observation,
not reclaim eligibility, legal advice or a tax-optimization recommendation.

## Interface, exports and isolation

Enable **Show Portfolio Intelligence** below the holdings tables. Five expanders
provide the research views. Wealth remains the default; cumulative capital is
opt-in. Scope changes and these controls use the same prepared results without
rerunning accounting or market requests. The batch editor, encrypted evidence,
seven-day manual observation validity and same-session market replay are unchanged.

Offline HTML contains optional expandable research, keyboard-operable chart toggles
and sortable security contributions. It exports only selected analytical fields,
escapes external text, and hashes its fixed script in the existing CSP. No raw
transactions, event evidence, market captures, credentials or external assets are
embedded. Tables scroll within their container on narrow screens. PDF retains the
existing single landscape A4 page and financial content; no research panels are
forced into it. Research results are session-owned and clear with the analysis.

## Later Risk Lab

Observed, aligned stock/fund TWR returns may support independent weekly volatility,
relative-return or Sharpe/Sortino validation later, with explicit risk-free-rate,
sample-length and sampling conventions. Current transaction-price estimates,
carried/stale closes and asynchronous markets must be assessed first. Holding-level
covariance and correlation-adjusted contributions need complete overlapping observed
histories and cannot be inferred from this aggregate index. No new risk scores,
optimization, Greeks, VaR or trading signals are included here.

## Validation

- Full local regression: **401 passed** (stable coverage plus 45 new cases).
- Nine canonical synthetic comparisons pass; all **126** retained definitions are
  identical to the read-only V6.7.8 reference.
- Current-export local comparison: **36** shared unrounded canonical result fields,
  eight metrics, holdings, monthly income, events and diagnostics match with the
  same captured market/FX inputs and private classifications. No private figures,
  identifiers or observations are included in this document or repository.
- Stable-versus-development prepared financial models and PDF text match under
  identical inputs in both scopes. Profit attribution and dated cash-flow bridges
  independently reconcile. Historical estimates and coverage caveats remain.
- Recorded real-data post-processing median: **0.071 seconds** across ten warmed
  samples. Stable adapter median 0.240 seconds; enhanced adapter 0.312 seconds.
  This measures local reporting work, not a claim about fresh provider latency or
  a runtime SLA. New intelligence makes **zero** additional provider requests.
- Offline HTML browser checks at 1440, 1024 and 390 px verify keyboard chart
  controls, five TWR periods, sorting, escaping/CSP and no external requests.
  Detailed tables scroll within their containers rather than overflowing the page.
- The unchanged landscape A4 PDF is rendered and inspected; one-page and text-bound
  checks pass. Generic frozen Windows packaging and side-by-side acceptance are
  final development gates; the working v1.0.13 installation is not promoted here.
