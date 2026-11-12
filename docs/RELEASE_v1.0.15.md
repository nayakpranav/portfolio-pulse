# FolioLens Personal v1.0.15 — validated corporate-action lineage

Production release of the validated generic non-cash corporate-action compatibility
from [PR #8](https://github.com/nayakpranav/portfolio-pulse/pull/8).
The v1.0.14 Portfolio Intelligence features and all eight headline definitions
remain available and unchanged.

## What changed

- Validated one-for-one, zero-cash EXCHANGE pairs can carry the full stock/fund
  FIFO lot lineage to a new ISIN, preserving original acquisition dates, gross
  acquisition basis and user-funded basis without a sale, new investment flow,
  or realized gain. The successor's quote is resolved independently.
- Paired reverse-split and forward-split migrations, same-ISIN zero-quantity
  renames and consecutive actions execute in chronological broker source order.
- Matching requires compatible timestamps, effective dates, account/class,
  complete source-quantity closure and no unexplained cash, fee or tax.
  Ambiguous, partial, cash-bearing and unmatched actions stay blocking.
- A successor's missing current quote is never substituted with zero; historic
  predecessor quotes are used only in evidenced compatible share-unit intervals.
  Existing lower-confidence historical price warnings remain explicit.

## Validation evidence

Development reference: `4b63bba04bd92d10438153e4bf24cd2e31206846`.

- 429 synthetic tests passed, including 26 focused corporate-action tests.
- Nine deterministic baseline comparisons retained common financial outputs.
- Linux/independent-return and Windows portable build/smoke CI passed.
- Private acceptance on the supplied broker export reconciled 37 unaffected
  holdings, income, mapped economic cash flows and the complete eight-metric
  report; it confirmed the successor's fractional position and preserved
  original basis. Private inputs, captures and outputs remain outside Git.
- Offline HTML, one-page A4 PDF, Portfolio Intelligence, both analysis scopes,
  frozen executable launch/shutdown, privacy and ZIP/hash integrity were checked.

## Install and rollback

Download `FolioLens-Personal-Windows.zip` from this release, verify its SHA-256
against `SHA256SUMS.txt`, and **extract the entire ZIP into a new versioned
application folder**. Double-click `Open FolioLens Personal.exe`. This is a
portable app; do not replace a live executable in place.

Keep the existing v1.0.14 installation, working desktop shortcut and rollback
backup untouched until v1.0.15 passes a local end-to-end launch, local CSV
analysis and report export. The package contains no personal evidence. The
private configuration remains under `%USERPROFILE%\FolioLensPersonal`.

The GitHub release provides a generic tested Windows package, not an automatic
installation. The unsigned executable may display Windows security warnings.

## Scope

No financial formula was replaced. Corporate-action compatibility extensions
affect the retained V6.7.8 canonical core; original provenance is the baseline
reference, not a claim that all changed source functions remain hash-identical.
Neither Holy Grail nor the experimental PR #4 is modified.
