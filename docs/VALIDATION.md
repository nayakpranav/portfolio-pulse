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

### Deployed release verification

Release `e6818117d6f4cdf7cfda6baadccf3132d152c9a9` was pushed to the independent
repository. [GitHub CI passed](https://github.com/nayakpranav/portfolio-pulse/actions/runs/37158086658).
The deployed https://foliolens-tr.streamlit.app/ application was exercised in
its Streamlit hosting frame using fresh browser contexts at 1440px and 390px.
Both desktop and mobile workflows passed: demo primary action, eight metrics,
three charts, all four public comparison modes, coverage/holdings expansion,
period selection, PDF download and clearing results. No browser page errors.
The hosted interface has no upload input or custom-ticker choice.

The deployed default-benchmark PDF was downloaded and its eight displayed values,
synthetic benchmark name, FolioLens branding, recognized-income terminology and
nonzero-axis notice were checked against the validated synthetic expectations.
It remains one landscape A4 page. Public demos used only fabricated transactions
and prices. Privacy/source audit passed across 63 tracked text files, with no
findings; the sole binary is the original generated favicon. Working vendor files
remain unchanged. The original Holy Grail repository and deployment were untouched.


## Real Portfolio Analysis release (4 October 2026)

The retained V6.7.8 accounting definitions remain unchanged. Personal-mode Yahoo
identity, current/history pricing, adjusted-price benchmark resolution and EUR FX
are integrated with bounded transport, explicit provider restrictions and offline
captured-input replay. Unknown events remain blocking; export-bound confirmations
reuse exact canonical worthless-event classification. Dated manual derivative EUR
quotes are explicit review inputs, not verified live exchange quotes.

A complete authorized private export was processed locally with Yahoo inputs.
Its financial artifacts and capture/configuration files remain outside the Git
checkout. A controlled V6.7.8/FolioLens comparison using identical export,
captured public prices/FX, valuation inputs, benchmark and private classification
passed all 27 shared result fields, eight metrics, holdings, monthly income,
corporate-action outcomes and diagnostics exactly at unrounded numeric level.
All 126 retained engine definitions are AST-identical. Manual derivative inputs
from the supplied reference snapshot carry capture-date-only provenance where
exchange calendar dates were unavailable; data health remains review-required.

Production dependency vulnerability and consistency audits passed. Worker tests
cover malformed/oversized inputs, security count bounds, admission, memory/time
limits, temporary cleanup, exact private event binding, partial-removal blocking,
manual valuation gaps, custom symbol syntax/identity, price indices, non-EUR FX,
missing history/currency, stale quotes, provider timeout and rate-limit stopping.
The public release remains synthetic: no unrestricted hosted Yahoo rights have
been established, and no private export is used in public deployment checks.

Final test/browser/deployment evidence is recorded in the release handoff after
publication. The original repository and deployment are read-only and untouched.


Final local safe suite: **78 passed**. All nine original synthetic comparison
scenarios passed exactly. Live personal checks with fabricated transaction rows
verified a USD SPY benchmark and EUR conversion, explicit price-index rejection
for ^GSPC, and an invalid ticker remaining unavailable without substitution.
The complete private export passed loopback browser acceptance with eight metrics,
three charts, all five TWR periods, private PDF download and clearing the upload,
results and PDF. A second independent session retained its own synthetic results
and download after the private session cleared. No private financial values were
logged. Fresh public-preview Chrome checks passed at 1440px and 390px, including
all four public benchmark modes and PDF/clear workflows, with no page errors.

Synthetic and private PDFs rendered as one A4 landscape page; text and vector
bounds passed. The synthetic render was visually reviewed. Real artifacts were
inspected privately for geometry and expected values. Production dependency audit
reported no known vulnerabilities; dependency consistency passed. Git-tracked
source and all reachable history were scanned for private record markers,
credentials and artifacts. Original extracted source hashes remain unchanged;
retained vendor files equal the previous committed core. The provenance manifest
now also records Git's LF-normalized hashes alongside original export byte hashes.


## Windows Personal continuation

The generic portable application bundles its own runtime, uses a console-free Tk
launcher, selects an available loopback port, opens the default browser and stops
its backend/process tree on closing. Per-launch temporary storage is separate
from persistent private event evidence in the local user profile. Private exports,
registries, captures and reports are not build inputs or CI artifacts.

The exact supplied transaction export was processed locally, without alteration,
with live IWDA.AS and SPY benchmarks.
The established private event matched V6.7.8 identity and canonical accounting
safeguards. No manual derivative prices were used. Six metrics remained valid;
tracked value and lifetime profit remained unavailable for missing valuations.
The same captured-input comparison again passed all 27 shared fields exactly.

The safe suite passed 81 tests, including automatic private-registry revalidation,
changed-event blocking, export binding and launcher mode/loopback controls.
Final graphical/package/release verification is recorded in the handoff.

## Scoped reporting repair (issue 1)

A controlled missing-private-evidence run reproduced seven suppressed non-income
cards despite valid raw stock/fund MWR, benchmark MWR and historical statuses.
The actual accounting blocker was an unmatched security-removal classification;
the old PDF masked it with a derivative-only warning. A fresh personal run using
the private established evidence completed accounting. Profile lookup now also
works without an explicit directory override; event matching guards are unchanged.

Preset and custom real-export controlled acceptance produced eight available
Stocks & funds cards, complete current stock/fund holdings, the three principal
charts and five TWR periods. Full-portfolio value/profit stayed unavailable for
unpriced derivatives. Stock/fund profit reconciled across canonical FIFO, ledger
cash flows and per-security contribution rows, without derivative/cash-interest
aggregation. No manual derivative quotations were used.

All 30 serialized canonical fields (including the previous 27 and three new audit
sources), eight scoped metrics, holdings, monthly income and diagnostics matched
read-only V6.7.8 exactly at identical captured market/FX inputs. All 126 retained
definitions remain unchanged. The nine original synthetic comparisons passed.

Focused cases cover seven missing derivative quotes, derivative-only basis gaps,
unmatched stock events, missing current stock quotes with valid history, failed
benchmarks, incomplete history, independent income, profit reconciliation, scope
switching, session-specific PDFs/clearing and safe hot-release display migration.
Actual private preset/custom PDFs rendered as one-page A4 landscape with explicit
selected/full scopes and passed text bounds and visual inspection. Final safe-suite,
Windows packaging, installed workflow and public deployment results are recorded
in the release handoff. Private acceptance artifacts remain outside Git and CI.

## Personal installation and general corporate-action repair

The v1.0.2 checks did not establish that the Explorer-launched application read
the same physical private profile as the packaged engineering environment.
Windows MSIX AppData virtualization redirected provisioned files into the setup
tool's package cache. Filesystem-handle inspection identified that physical
location; the actual running installed server showed no loaded verified evidence
and requested confirmation for the unchanged current export. Earlier acceptance
claims therefore did not prove the owner's installed workflow.

The launcher now uses the non-virtualized Windows user directory, independently
reports its version/profile readiness from inside the frozen executable, and the
existing shortcut points to that installation. Private exact-event records use
per-user DPAPI protection and atomic recovery. Persistent identity includes the
broker transaction, canonical event fields and complete prior security-activity
fingerprint; a CSV digest binds only transient inputs. Numeric fingerprints are
stable across dataframe dtype changes. No user-specific registry is bundled.

Supported canonical corporate actions run automatically. The local graphical
review remembers successful exceptional confirmations after canonical FIFO
validation; invalid cash/identity/quantity/prior history still blocks them.
Concurrent preflights use function-local registry bindings without mutating a
global registry. Alternate profiles cannot inherit the owner's legacy evidence.
Hosted confirmations remain session-only. Corrupt protected evidence must recover
from a valid private backup or fail closed.

Localized accounting failures can supply a separately labelled unaffected-security
projection using unchanged canonical functions. Complete histories of affected
lineages are excluded, with matching scoped benchmark cash flows. Complete-export
accounting and audits remain intact; full capital/recovery and totals retain their
blockers. Duplicate, unlocalized or missing core inputs do not receive that escape.

Independent fabricated portfolios cover ordinary stocks/funds, dividends/interest,
reinvestment, promotional funding/SaveBack, realized loss and derivative settlement,
forward/reverse splits, exceptional-event persistence, newer exports, altered
evidence, isolated profiles and concurrent preflights. The retained nine synthetic
reference scenarios pass. Controlled real-export comparison matches all 32 fields
exactly, including the preserved 30-field contract, eight metrics, holdings, income
and diagnostics. All 126 retained canonical definitions are unchanged. Installed
live preset/custom browser tests produced eight valid stock/fund cards, three
charts, five TWR periods, private PDF downloads and cleared sessions. Full value
and lifetime profit remain unavailable for seven unquoted derivatives. Final
restart, package, safe-suite and CI results are recorded in the release handoff.
