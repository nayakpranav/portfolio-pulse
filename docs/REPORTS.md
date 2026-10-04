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
