# FolioLens Personal v1.0.12 - manual valuation maintenance

Based directly on stable `01060ebfe3c120ef537705c8af4b87e057cb9f67`.
PR #4 is a read-only label reference; its quotation system is not integrated.

## Changes

- Exact-ISIN derivative descriptions identify the underlying company in the
  existing manual dropdown. Explicit verified metadata can supply an expiry;
  missing expiry remains unavailable and historical turbo levels are omitted.
- Small applied manual-price coverage indicator, with current valuation
  completeness distinguished from historical/data-health and accounting warnings.
- Offline HTML includes descriptive instrument name, ISIN, quantity, confirmed
  EUR-per-unit price, actual date/source, canonical position value and status.
- PDF retains one landscape A4 page, adding only concise manual-coverage text.
- Stocks/funds and full-portfolio scopes retain the same canonical raw result;
  changing scope regenerates both downloads without running the engine again.
  Download clicks themselves are frontend-only and do not request a page rerun.

The existing manual form, seven-calendar-day freshness, exact-activity-bound
Windows-user encryption and profile location are unchanged. With valid manual
evidence for all open derivatives and complete accounting/other prices, canonical
full-portfolio tracked value and lifetime profit become available. Historical
estimates or other warnings remain visible and can keep overall health Partial.
No unavailable input is replaced with zero or acquisition cost.

No automatic quotation adapter, new provider, download/cache infrastructure,
runtime dependency or protected V6.7.8 definition is changed. The public synthetic
deployment stays restricted. Holy Grail and unmerged PR #4 remain unchanged.

## Acceptance and rollback

Synthetic fixtures cover missing/mixed/complete/expired manual prices and exact
numerical valuation increments. Private acceptance uses the authorized real export
with captured stock/FX/benchmark inputs and explicitly fabricated software-test
manual observations in an isolated profile, never as current broker prices.
Those test records and reports are not copied into the production profile.
Complete coverage is a calculation/workflow validation, not verification of the
owner's current market quotations. Genuine observations remain the user's input.

Release gates include full regression/security tests, 126-definition/nine-scenario
canonical comparison, controlled private comparison, real graphical manual-entry
and scope-switch tests, offline HTML/PDF consistency, frozen Windows worker/TLS
smoke tests, source/package privacy scans, checksums and CI. Preserve the previous
installation/profile/shortcut backup and install side by side before promotion.

## Future optional risk-adjusted performance (not implemented)

The existing canonical `daily_twr_return_pct` series is structurally suitable as
an input to independent Sharpe/Sortino research when its linked NAV reconciles
and continuous coverage is reliable. Current historical transaction/filled-price
estimates can materially affect such statistics. A future feature must establish
eligible observation dates/frequency, annualization, an aligned EUR risk-free
series or explicit hurdle, minimum history and downside/zero-variance handling.
Calendar-day carry rows must not be blindly treated as independent trading-day
returns. The available series alone does not establish those assumptions. This
maintenance release adds no Sharpe, Sortino or risk-adjusted-performance panel.
