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
- Final pinned-dependency suite: **30 passed**; dependency consistency check passed.
- Concurrent worker processes produced distinct holdings, values and PDFs, with
  temporary directories cleaned. Two independent Streamlit sessions retained
  different benchmark results; clearing one preserved the other's result/PDF.
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

**No public deployment is claimed or verified.** The browser-control helper failed
before opening the authenticated Community Cloud console. See `DEPLOYMENT.md` for
the exact account authorization, deployment and post-deployment test checklist.
This remaining boundary prevents declaring the user's entire project complete.
