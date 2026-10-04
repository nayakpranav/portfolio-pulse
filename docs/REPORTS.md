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
