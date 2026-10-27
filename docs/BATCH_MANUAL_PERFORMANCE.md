# Batch manual valuations and performance diagnosis

Development baseline: stable `e7eb7d488daf4f51fe839cf3639f09061a545240`
(v1.0.12). Development version: **1.0.13-dev**. This work does not integrate
experimental PR #4 or promote a stable installation.

## Measured Windows evidence

Local acceptance used the actual export in the frozen Windows executable, a
separate copied profile and the owner's existing dated report observations.
No broker price was invented or refreshed. Detailed captures/reports remain
outside Git. The following are aggregate measurements, not financial figures.

| Measurement | Stable executable | Development executable |
|---|---:|---:|
| First live `run_analysis` elapsed | 179.46 s | 168.87 s |
| First live provider requests | 488 | 467 |
| Repeat manual-update `run_analysis` elapsed | 205.78 s | 26.62 s |
| Repeat provider requests | 484 | 0 |

The development first live button-to-render measurement was **172.16 s**.
Submitting seven observations together, including encrypted persistence and both
reports, took **30.04 s** from Save to rendered confirmation. The stable repeat
analysis plus exports took 206.45 s; its browser rendering/entry time was not
measured. The comparable `run_analysis` boundary improved about **87%** for the
manual-only update. These are individual observations on this Windows machine,
not a service-level guarantee or a synthetic benchmark. Live runs occurred at
different times and returned some different market observations; numerical
reconciliation therefore uses identical captured inputs, separately.

The initial stable worker used 33.45 s CPU and recorded 149.84 s aggregate transport
elapsed across 488 requests. Identity resolution was the largest measured stage
(120.49 s, inclusive); FIFO was 0.46 s and PDF/HTML generation combined 0.36 s.
The stable repeat used 34.25 s CPU and 166.54 s aggregate transport elapsed. The
development live run used 30.17 s CPU and 147.15 s aggregate transport elapsed;
the offline manual update used 24.89 s CPU. Nested/concurrent timings must not be
added: transport elapsed includes transport CPU and overlapping work, rather than
being a claim of pure socket wait.

## Root causes and changes

- The old UI staged each quote separately and then requested an entire live
  analysis. Every such rerun discarded worker-local market inputs.
- A new session-owned capture retains public identification, stock/FX/history,
  dividend and benchmark inputs. Manual-only changes rerun the original canonical
  pipeline offline; no lifetime-profit, FIFO, cost-basis or metric patch is used.
- The capture binds the exact CSV digest, benchmark and accounting evidence.
  It expires after four hours or a Berlin calendar-date change. It never updates
  a market observation's date. Clear/upload change deletes it. An explicit fresh
  Analyze discards it and clears the previous update notice.
- Missing/changed/expired captures use a disclosed full-analysis fallback.
  An incomplete offline capture fails safely without an automatic live retry.
  Both requests and yfinance's curl transport are blocked during replay.
- First-run OpenFIGI identification batches the exact active and historical
  positions already needed by the canonical pipeline. This avoids later single-
  instrument requests; prefetch elapsed fell from 27.58 s to 10.08 s in the observed
  runs. Identity/type/exchange rules, provider throttles and failure states remain.
- No persistent/shared market cache is introduced. Existing worker-local caching
  plus bounded same-session reuse provides the main gain without adding provider
  rights, retention or stale-evidence assumptions. A fresh analysis still needs
  substantial Yahoo identification/history work; this remains a limitation.

## User workflow and reporting

The optional editor lists every active derivative with immutable identity/units,
descriptive name, EUR/unit price, date, source and status. A common date/source
supports row overrides. One confirmation and one optional encrypted-persistence
choice apply the batch using **Save and update portfolio**. Invalid rows prevent
application while retaining drafts; blank prices do not delete existing evidence.
The editor remains available at complete coverage. Unchanged encrypted evidence
is not rewritten; its schema, activity binding and seven-calendar-day validity
are unchanged. A failed persistence attempt is disclosed separately.

Current valuation availability is separated from historical/source/accounting
quality. When canonical full value and profit are available, the full scope says
**Full portfolio — Valuation complete**. Historical estimates, the IPO source
warning, manual provenance and genuine accounting blockers remain disclosed.
Stock/fund and full scopes regenerate both reports without an engine run. The
offline HTML and one-page landscape PDF use the same prepared model.

Aggregate diagnostics cover validation, evidence, worker startup, identity and
OpenFIGI work, stock/FX quotes, history, benchmark, dividends, canonical analytics,
serialization, report generation and server presentation construction. Request,
retry and repetition counts contain no identifiers, prices, URLs or response
bodies. Browser paint/latency is measured separately by graphical acceptance.

## Validation and readiness

- Full local suite: **356 passed**. Four focused UI tests also passed after the
  final notice-clearing guard. CI independently checks the final committed tree.
- All **126** retained definitions remain AST-identical to read-only V6.7.8.
  Nine canonical synthetic comparisons passed. The real controlled comparison
  matched **36 unrounded result fields**, eight metrics, holdings, income and
  diagnostics using identical captured prices/FX/dates and event/manual evidence.
  Only nondeterministic performance telemetry is excluded from comparisons.
- Actual frozen UI: seven exact original observations in one submission, zero
  update requests, eight available full-scope metrics, scope changes without an
  engine call, actual PDF/HTML downloads, encrypted restore after clear, and
  byte/mtime-idempotent persistence passed. Desktop/mobile and offline HTML at
  1440/1024/390 px passed. The private PDF remains one A4 landscape page.
- Frozen worker, TLS verification, startup without Python on PATH, package CRC,
  manifest/source agreement and private-marker scanning passed. No automatic
  derivative collector/cache/provider modules are included.
- The original stable profile, shortcut, active-installation record and input CSV
  were checked unchanged. The public deployment remains synthetic-only. No Holy
  Grail code, protected definition, production deployment or PR #4 was modified.

This is a reviewable development build, not a new stable release. Retain v1.0.12
and its rollback. GitHub checks, final package checksum and development location
are reported with the branch handoff rather than embedded private machine paths.
