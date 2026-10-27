# FolioLens Personal v1.0.13

Production promotion of the owner-approved PR #6, reviewed head
`0cf17504cf590818b10ebb118e62cebfef79cb8d`. Only stable version metadata changes
after integration; the validated financial and application implementation is reused.

- Batch dated EUR-per-unit derivative observations with shared defaults, row
  overrides, one confirmation and optional atomic encrypted persistence.
- Same-session canonical recomputation from bound, dated market inputs without
  external requests. Existing freshness, evidence and fallback safeguards remain.
- Current valuation completeness is displayed separately from retained historical,
  source-evidence and accounting warnings. Scope changes regenerate both reports
  without another engine run.
- Aggregate-only performance diagnostics and batched existing OpenFIGI identity
  requests. No automatic derivative quotation collectors or new providers.

The verified development evidence is documented in
[Batch manual valuations and performance diagnosis](BATCH_MANUAL_PERFORMANCE.md):
356 tests, 126 protected definitions unchanged, nine synthetic canonical scenarios,
36 matching controlled real-data fields, actual Windows batch/report/profile tests,
and passing Linux/Windows CI. Observed repeat analysis fell from 205.78 to 26.62
seconds, with zero external requests; these are measurements, not a runtime SLA.

Production release checks verify stable metadata, frozen worker/TLS/report startup,
package integrity/privacy, final CI, side-by-side installation and the actual desktop
shortcut. The previous v1.0.12 application, shortcut and profile backup are retained
for rollback. Installation does not reset private evidence or saved observations.

Extract the entire Windows ZIP and open **Open FolioLens Personal.exe**. Python is
not required. The executable is unsigned; normal Windows security warnings may
appear. Public CSV uploads remain disabled. Holy Grail, experimental PR #4 and all
protected accounting definitions remain unchanged.
