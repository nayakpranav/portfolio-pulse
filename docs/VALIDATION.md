# Validation evidence - 3 October 2026

## Verified locally

- The complete master specification was read. The outer package and both nested
  archives were safely extracted outside the new repository. All manifest artifact
  hashes matched. Release/audit documents, 88 private canonical CSV schemas,
  the supplied HTML's canonical data structure and the one-page PDF were inspected.
  No private values or reference artifacts were imported into public source.
- A clean repository was created with only the authorized new Git remote. The
  original repository/deployment was never mutated; no original history was imported.
- Eight source modules selected: `analysis_engine`, `portfolio_core`,
  `security_events`, `dividend_reinvestment`, `dividend_engine`,
  `automated_investing`, `exposure_engine`, `investment_snapshot_pdf`.
- **126 retained canonical function/class definitions are AST-identical** to V6.7.8.
  Changes are documented in the provenance manifest: removing legacy exports,
  removing the personal event registry, restricting metadata cache paths and adding
  price-input/output-serialization hooks. No FIFO, income, return or PME definition changed.
- Exact same-input comparisons passed for **nine controlled scenarios**: demo,
  ETF-only, realized sales, dividend reinvestment, unsupported action, missing
  price, short history, incomplete accounting and derivatives. Unrounded metric
  dictionaries, ledgers/quantities, historical series, diagnostics, status/provenance,
  monthly income, holdings and narrative inputs match the original pipeline.
  Synthetic prices, benchmark settings and valuation cutoff were identical.
- The synthetic test suite covers **15 portfolio scenarios**, including ordinary
  stocks, dividends, interest, no-dividend/no-SaveBack/no-derivative portfolios,
  realized sales and the incomplete cases above. Numeric expectations independently
  check the demo's capital, recovery, profit and recognized income; reinvestment
  recognition/basis, covered zero versus uncovered income and missing-value handling
  are checked explicitly.
- Final pinned-dependency suite: **31 passed**; dependency consistency check passed.
  GitHub Actions also passed all 30 tests on Linux/Python 3.12 for the initial
  public commit. Final commit status is reported separately in the handoff.
- Concurrent worker processes produced distinct holdings, values and PDFs, with
  temporary directories cleaned. Two independent Streamlit sessions retained
  different benchmark results; clearing one preserved the other's result/PDF.
  A transient Windows sync/indexing lock found in the final run is handled by
  bounded cleanup retries; persistent cleanup failures are surfaced rather than ignored.
- Local Chrome validation: eight metrics and three charts rendered at 1440px and
  390px. No page errors or horizontal overflow were observed. PDF downloads worked
  on desktop/mobile; periods, insights, holdings and session clearing were exercised.
- Upload-enabled **loopback-only** instance: synthetic CSV upload, analysis, PDF
  download and malformed-file rejection were exercised. The clear-widget defect
  identified during this test was fixed by rotating the uploader's session key.
- PDF: one A4 landscape page; canonical values/dates and unavailable states checked
  by tests; vector report rendered locally and visually reviewed for text, spacing,
  labels, chart/holdings clearance and footer. Generated files remain ignored.
- Synthetic profile: **2.75 s** analysis; **3.87 s** total process wall time;
  **240.3 MiB** peak combined parent/worker RSS; **358,240 bytes** canonical JSON.
  These are small-fixture observations on the local machine, not a hosted SLA or
  maximum-input capacity guarantee. Legacy export work is removed; otherwise the
  canonical build pipeline is retained to avoid unsafe accounting optimizations.

## Release limits

Public source/privacy scanning is run against the actual tracked files before
commit/push. Private references, outputs, broker UUIDs, credentials, local paths,
personal fixtures/registries and metadata caches are excluded. The public registry
is empty. Unknown real events remain blocked.

The hosted CSV workflow is disabled by default because Yahoo/yfinance's documented
personal-use data scope does not establish public multi-user hosting rights.
The default public demo uses fabricated prices and makes no provider requests.
Local CSV analysis retains live-provider failure/coverage states. Open derivative
quotes, forecasts and optional sector fetching are disabled. Additional server
resource/rate limits and licensed provider review are required before public uploads.

## First public-demo refinement (4 October 2026)

FolioLens branding, original self-contained SVG/PNG mark, demo-first landing,
three explicit synthetic benchmark illustrations and no comparison, local-only
custom ticker validation, and shared padded wealth-chart ranges are implemented.
Income wording describes recognized net investment income, including reinvestment.

The complete safe suite covers 48 checks, including all existing synthetic
scenarios, four benchmark modes, invalid/custom ticker boundaries, adjusted-price
and historical-coverage failures, provider failure without substitution, missing
currency, reliable/missing FX, local custom input and the public landing action.
All 126 retained canonical definitions match V6.7.8. Nine synthetic reference
scenarios match exactly at unrounded canonical JSON/status/provenance level.
No vendor files or original repository/deployment were changed.

Local browser checks at 1440px and 390px cover eight metrics, three charts, all
public benchmark modes, one-page PDF download and clearing session results.
Independent-session isolation remains covered by the full suite. The final PDF
was rendered and visually reviewed for axis/date labels, benchmark identification,
income terminology and clipping. Artifacts and screenshots remain Git-ignored.

Public deployment verification is performed after pushing this release; its
record is added below once the deployed revision has been exercised.
