# PDF and offline HTML reports

**Download PDF Report** and **Download HTML Report** export the same prepared
analysis, selected scope, benchmark, valuation dates and eight financial figures.
Export generation does not rerun accounting or market requests. Changing scope,
reanalyzing or migrating an old display model regenerates both downloads together.
Changing the CSV, a failed new analysis or clearing the session removes both.
There is no shared report cache.

The PDF is one A4 landscape page with navy panels, prominent mint holding values,
white holding names, weights and larger lower disclosures. Very long holding
labels are explicitly abbreviated; extreme values shrink to fit their card.
Full instrument names and all diagnostic messages are available in HTML and
the application. The PDF preserves the applicable scope, missing valuations,
accounting blockers, price estimates, stale/currency/benchmark limitations,
income semantics and independent-analysis disclaimer.

HTML uses inline CSS, SVG charts and a small fixed script for five cumulative
TWR periods. Chart titles show values on hover. No external scripts, fonts,
images, CDNs, providers, login or backend are needed. All uploaded text is
escaped; it cannot become markup, executable code or a link. A restrictive
content security policy denies network connections and permits only the exact
bundled script. Data is explicitly selected for display; raw transactions,
broker identifiers, private event registries, provider captures, credentials
and unrelated session state are excluded.

Real reports contain personal financial figures and holdings. Keep them private.
Clearing the application removes its session state, but does not erase files
already downloaded. The generic Windows ZIP contains no generated reports.

## Missing trade amount diagnostics

V6.7.8 warns on a raw BUY/SELL amount that is absent. Its existing trade ledger
may infer a stock/fund trade value from quantity and price; a specific IPO rule
can instead allocate same-instrument subscription cash and fees when dates and
amounts reconcile within canonical tolerances. These are distinct from a
zero-consideration security removal, which is not a BUY/SELL missing-amount row.

The presentation now reports counts of canonical IPO reconciliations, quantity
and price inferences, and other missing amounts. The original warning remains
in canonical diagnostics. It does not label arbitrary missing amounts as zero
consideration or completed IPO allocations, change acquisition basis, suppress
warnings, or alter metric availability. Source evidence should still be checked
where only an inference, rather than reconciled broker cash, is available.

Complete stock/ETF-only portfolios default to full-portfolio results when their
own accounting and prices validate. Historical closed derivatives do not create
a current quotation warning. Open unquoted derivatives retain incomplete full
value and profit while independently valid stock/fund results remain available.


## v1.0.5 visual identity

Dashboard, PDF and HTML share role-based navy, cyan, blue, mint and amber tokens.
Four primary monetary cards lead a lighter secondary performance/income row.
Holdings use circular 01–05 rankings, prominent mint values and authoritative
allocation percentages. Dashboard/HTML thin bars compare each holding with the
largest displayed holding; they do not represent total-portfolio allocation.
HTML consolidates holdings into those cards and keeps the full holdings table.

All wealth charts use one padded-range rule: nonnegative observations do not
receive negative padding; genuine negatives stay visible. A minimum five-percent
span prevents exaggerating near-flat series, and nonzero origins are disclosed.
The verified benchmark gets a concise chart alias, such as MSCI World ETF
(IWDA.AS), while full identity remains in details and PDF metadata. Synthetic
comparisons stay explicitly labelled. Five HTML TWR controls sit adjacent to
wealth and change only cumulative TWR, never its full-history chart.

The PDF retains the full-width five-card strip after comparing rendered split
and strip arrangements. This preserves larger values and readable names rather
than compressing holdings into a narrow sidebar. Footer labels distinguish
insight, health, methodology and the independent-analysis disclaimer. Material
missing valuations and historical price-estimate qualifications remain visible.


## v1.0.6 portfolio composition and monthly income

The dashboard replaces the redundant holdings chart with Portfolio Composition
beside Investment Income. The two detailed holdings expanders now follow the
five unchanged ranked cards. HTML presents the same prepared composition between
income and holdings, without external assets or a second holdings chart.

The active count deduplicates canonical active stock/fund instruments, including
unpriced positions and excluding closed positions and derivatives. Percentages
use only nonnegative, finite current values with canonical VALUED diagnostics
and verified quotes within seven days of the displayed coverage date, matching
the existing freshness qualification. Synthetic scenarios use their explicit
historical valuation date. Missing, stale, future-dated or unverified values
remain in the active count but do not contribute invented values.

Top-five concentration is the sum of the largest up to five reliable values
divided by all reliable stock/fund values. Fewer than five are labelled Top N.
The remaining share is complementary. Instrument allocation uses canonical STOCK
and FUND classifications; uncertain classifications receive an explicit neutral
share. No security-name guessing, constituent look-through, sector or risk
analysis is performed. Zero or missing denominators are unavailable. Localized
accounting blockers retain the established unaffected-securities scope and its
partial qualification; a blocked position count is unavailable, never zero.

PDF layout and charts are retained. Composition appears only as an additional
footer observation when space permits, preserving benchmark and health text.
Monthly income has exact two-decimal EUR labels above bars when readable, or a
compact full-month value key when labels would collide. Negative and genuine
zero amounts remain exact. Partial months retain outlines and an asterisk;
uncovered/unavailable months show a dash, never an invented zero. HTML adds a
visible exact monthly key while preserving hover values; Streamlit retains its
exact tooltips to avoid overcrowding its compact responsive chart.


## v1.0.8 individual holding performance

Cards and both detailed tables expose the canonical remaining acquisition basis,
open-position unrealized P/L and simple unrealized return. The worker already
provides these EUR fields: `remaining_acquisition_cost_basis_eur`,
`live_unrealized_pl_acquisition_basis_eur`, and
`live_simple_return_acquisition_basis_pct`. No FIFO, prices or FX are reconstructed
in a report. Open P/L reconciles to current value minus remaining acquisition
basis; return reconciles to that P/L divided by positive usable basis, times 100.
Dividends, interest and realized sales are not added again. These returns are
neither lifetime returns nor annualized MWR or cumulative TWR.

Canonical identity, source presence, valuation diagnostics, existing quote
freshness and EUR-conversion evidence are checked before displaying performance.
Missing/unusable basis or prices, ambiguous identity and source discrepancies
produce an explicit unavailable state; material reconciliation issues remain
visible in data health and Performance coverage. A zero basis can retain valid
canonical open P/L, but has no percentage return. Existing market values,
allocation denominators, ranking and headline financial calculations are unchanged.

The five cards retain mint valuations with compact upper-right signed return
badges: green positive, red negative, neutral exact zero and muted unavailable.
Top 10 uses Holding, Value, Basis, Weight, Open P/L and Return; public security
identifiers remain internal to ranking. All Holdings adds quantity, quote date,
valuation, ISIN and performance coverage. Its intended security identifiers are
not private broker transaction identifiers. Shared formatting ensures the same
two-decimal signed figures in the app and offline HTML. Wide tables scroll inside
their container on mobile.

The one-page PDF retains its executive layout and monthly keys, with small Open
return indicators above holding names where they fit. Extreme indicators that
cannot fit are omitted rather than clipped; complete performance and operational
details remain in HTML and the application. No detailed PDF table is added.


## v1.0.9 holdings-card hotfix

The shared app/HTML card header pairs the rank badge with a padded right-aligned
return and a decorative inline SVG trend icon. Gains use mint; losses use soft
red. Neutral/unavailable states have no directional icon. The parent retains
its descriptive accessible label, while the SVG is hidden from assistive tools.
The PDF uses equivalent vector strokes on the rank row, ten-point right padding,
and a compact relative-size bar matching the existing card convention. Holding
names sit beneath the header; long PDF names are abbreviated and remain complete
in HTML and tables. Individual PDF labels no longer repeat Open; section text
explains unrealized return. One-page geometry, financial values and tables stay
unchanged. No external assets or new financial calculations are introduced.


## v1.0.10 risk and forward-income reporting

The wealth panel defaults to Wealth (EUR). Drawdown (%) displays stocks/funds
only, from the canonical daily `stockfund_nav_index` and
`stockfund_drawdown_pct`, not EUR wealth or cash-flow-matched benchmark wealth.
The initial 100 index baseline is included in running peaks. The reporting
adapter verifies linked daily returns, canonical cumulative TWR, maximum/current
drawdown, calendar continuity and price coverage. An interior missing return or
inconsistent input makes risk unavailable; no gap is bridged. A supported
unaffected scope stays explicitly partial. Historical price estimates retain
their warning. Unpriced derivatives do not invalidate stock/fund risk.

Forward 12M Net Dividends (Known Est.) is independent of recognized YTD income.
A reporting-only invocation of the retained V6.7.8 `build_dividend_analytics`
uses an explicit **zero-growth** scenario; accounting and recognized income are
not rerun or replaced. It maps the unrounded canonical
`forward_12m_estimated_net_known_subtotal_eur`. The engine keeps declared-event
entitlements, distribution seasonality, issuer-specific observed gross/net
retention, corporate-action unit adjustments, EUR/minor-unit FX and receipt
reconciliation. It includes earned entitlements for recently closed securities
within the canonical 180-day discovery window; interest is excluded.

The adapter distinguishes quantified-event coverage from active-holding
coverage. Unknown distribution amounts, payment dates, net retention, currency,
receipt reviews or uncovered securities qualify the known subtotal as partial.
A complete quantified-event subtotal alone cannot certify the whole portfolio.
Canonical broker-receipt seasonal fallback is disclosed as low confidence and
does not infer current quantities. Apparent accumulating-fund/no-history states
retain the canonical treatment; no known distribution guarantees future zero.
Missing inputs are unavailable rather than invented zeros. The forecast window
starts at the canonical transaction reference date; a reference more than 31
days old is unavailable until a current export is analyzed.

Personal mode uses the existing authorized Yahoo-only worker transport,
10-second request limits, rate-limit circuit breaker and global request budget.
Optional dividend retrieval also has a 25-second budget and respects the
remaining worker time reserved for output/cleanup. Explicit fabricated dividend
snapshots drive the public demo; no additional public provider requests occur.
Captures contain only public ticker distribution/FX inputs and stay private.

The offline HTML toggle uses native keyboard-accessible buttons, `aria-pressed`,
controlled hidden regions and a recalculated script SHA-256 CSP. Five-period TWR
controls remain independent. PDF retains its single-page wealth/income charts,
adds a compact drawdown summary and a qualified forward-income note. Extremely
large card returns use measured font width: signed two decimals, then rounded
whole percent, then `See HTML*` with an explicit explanation. Precise return
values remain in app/HTML tables. The unrealized-return definition now precedes
holding cards in both interactive presentations.
