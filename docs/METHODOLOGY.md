# Methodology and source mapping

Baseline: V6.7.8, release commit `0b865ca8b3c301c77e6a09eba5fa6d0c7fed16f2`.
The source ZIP SHA-256 is
`83aed67dd4ba6d0c3dbbb782714c17de01f6fb165999d47dd3add5ef01ebb6d3`.
Per-file provenance is recorded in `vendor/v678/PROVENANCE.json`.

| Card | Canonical unrounded output | Scope |
| --- | --- | --- |
| Tracked Investment Value | `lifetime_current_tracked_open_value_eur` | Tracked stocks/funds and valued derivatives; excludes brokerage cash |
| Lifetime Economic Profit | `lifetime_economic_profit_eur` | Current tracked value + canonical recovery - gross acquisition outflows; reconciles to open/realized P/L and recognized net income |
| Net User Capital Committed | `lifetime_net_committed_user_ecosystem_eur` | User-funded investment outflows less ecosystem recovery, including interest; may be negative |
| Cash Recovered | `lifetime_ecosystem_recovery_eur` | Lifetime canonical sales, settlements and net-income recovery, including interest |
| Stock/Fund MWR | `stock_fund_mwr_acquisition_pct` | Annualized XIRR of actual dated acquisition-basis stock/fund flows; excludes derivatives and interest |
| Benchmark MWR | `benchmark_mwr_pct` | Annualized XIRR of the core PME replication of matched stock/fund cash flows |
| Stock/Fund TWR | `stockfund_twr_since_inception_pct` | Cumulative reconstructed stock/fund return; core end-of-day neutralizing-flow convention |
| Net Investment Income YTD | `build_snapshot_data`: `ytd_income` | Reporting-year recognized net dividends plus net interest through source cutoff |

The capital/recovery pair uses the **ecosystem**, interest-inclusive definition.
Recovery is an accounting total, not profit, account balance or withdrawable cash.
Internally reinvested dividend and non-cash disposition legs keep the original
core semantics. Promotional credits affect user funding/personal benefit, but
are not added to economic profit a second time. Deposits and transfers are not
investment income. Dividend-reinvestment income is recognized once.

The adapter reads canonical dictionaries, ledgers and daily-series outputs; it
never parses a generated HTML report or calculates an alternative FIFO/XIRR/TWR.
The inherited reporting-year income/holdings data builder remains unchanged.

## Charts and observations

- Wealth chart: `stockfund_value_eur` and `benchmark_pme_value_eur` from the
  canonical daily history. The benchmark is cash-flow matched; this is not a
  comparison to ordinary benchmark price appreciation. If disabled/unavailable,
  actual stock/fund wealth remains independently visible when supported.
- Monthly income: recognized net dividend and interest ledgers through the
  transaction/report cutoff. Complete months, partial months, uncovered months
  and covered zero-income months remain distinct. No forecasts are included.
- Holdings: active, positive-quantity, valued stock/fund positions, descending
  by canonical current value; weights divide by **valued stock/fund assets**.
  Closed/derecognized holdings are omitted. Missing prices restrict coverage.
- Period selector: all 1M/3M/YTD/1Y/MAX choices, with unavailable periods explicitly marked, using
  established observation/date-anchor rules; displayed return is cumulative TWR.
- At most five observations: comparable MWR difference in percentage points,
  positive contributor, negative contributor, actual YTD income and top-five
  weight. Concentration is described numerically without a risk threshold.
  No model API, predictions, inferred intentions or buy/sell recommendations.

## Availability

Core accounting blockers cannot be bypassed. Portfolio-wide accounting-dependent
cards, return observations and rankings are withheld when accounting is blocked.
Independent recognized income can remain visible when unaffected. Missing current
prices block value/profit without unnecessarily blocking valid capital/recovery.
Multiple-root XIRR is not shown as a confident return. Benchmark MWR requires
compatible actual/benchmark valuation dates. Insufficient or unresolved historical
data blocks TWR; canonical low-confidence fallback pricing is explicitly disclosed.
Optional sector-metadata warnings are omitted because sector analytics are not
part of FolioLens's displayed scope.

Personal mode retains Yahoo derivative identity diagnostics and supports explicit
export-bound, dated manual EUR valuations. Low-confidence generic probes are not
accepted as current prices. Other derivative scrapers and forward-dividend
enrichment remain disabled. Closed derivative accounting remains included. An
open unquoted derivative makes dependent totals unavailable; it is not zero. Net
reinvestment income and basis are still obtained from the canonical CSV-leg
matcher; missing externally reconciled gross tax detail is not invented.

Trade/account amounts are treated as EUR under the original parser's verified
export semantics. This release does not add multi-account or alternative export
conversion support. Unknown `FREE_RECEIPT` events remain review-required because
the public event registry is empty. Export-bound local/session confirmations
retain the canonical exact-event, cash and full-position safeguards; there is no
blanket worthless-security rule.

## Dates and pricing

Transaction cutoff comes from the export. Reporting year/date uses Europe/Berlin.
Historical/current valuations retain canonical price timestamps; historical
series can extend beyond the last transaction when quotes are newer. Coverage
does not guarantee an executable/current quote. Demo quotes are explicitly
fabricated and do not represent real securities or current market conditions.

Personal Yahoo pricing supports verified native currencies with validated EUR FX
coverage, including USD and INR; availability depends on provider history. GBp
minor units retain canonical conversion. ETF adjusted close is used only for
the benchmark total-return proxy; stock/fund reconstruction retains the original
corporate-action and dividend conventions. Price-only/unverified total-return
indices do not produce a dividend-inclusive PME comparison.
