# Source and market-data licensing

The owner explicitly authorized selection, reuse, publication and independent
deployment of the supplied V6.7.8 source for FolioLens in this assignment.
The supplied source package otherwise carries a private-use restriction. That
restriction must not be interpreted as permission to publish the original package,
its personal registry, data/artifacts or historical repository.

FolioLens has a separate source-available personal-use license in `LICENSE`. No broker
logos, downloaded financial datasets, private price caches, external artwork or
reference design assets are redistributed. The PDF uses original vector primitives
and built-in ReportLab fonts. Dependencies are installed separately under their
own licenses; their licenses do not grant rights to provider financial data.

yfinance's [official documentation](https://ranaroussi.github.io/yfinance/) states
that Yahoo Finance's API is intended for personal use. It refers users to Yahoo's
terms for rights to downloaded data. Public multi-user hosting/redistribution is
not presumed permitted by an open-source Python library license. Consequently,
the hosted CSV/live-provider workflow is disabled by default, while local
personal-use analysis remains available subject to the provider's terms.

The public demo is self-contained: fabricated securities, transactions and price
paths exercise the same canonical analytical pipeline without external requests.
Enabling public live analysis requires an approved provider arrangement and a
controlled regression comparison after any replacement of price-fetching inputs.
An investment-advice or data-distribution commercial service is outside this release.

## Deployment distinctions (reviewed 4 October 2026)

- **Private personal analysis:** the implemented loopback workflow follows the
  personal/research scope described by yfinance; the user remains responsible for
  applicable provider terms. This is not an unrestricted data redistribution grant.
- **Owner-only hosted analysis:** technical support exists, but authentication alone
  does not establish data-use rights. Require a verified owner gateway protecting
  HTTP, WebSocket and download/media endpoints, privacy/retention review and an
  applicable provider arrangement. This mode is not approved by this release.
- **Anonymous public multi-user analysis:** rights have not been established;
  uploads and live-provider requests remain disabled on the public deployment.

Yahoo's [official terms](https://legal.yahoo.com/ie/en/yahoo/terms/otos/index.html)
and [Finance terms help](https://help.yahoo.com/kb/finance/SLN7179.html) govern use
in addition to the library guidance. The full Yahoo legal pages returned access
errors during this environment's review, so no affirmative hosting authorization
is inferred from them. Noncommercial status does not establish unrestricted access.

[Onvista's official policy](https://hilfe.onvista.de/de/verstoss-gegen-nutzungsbedingungen)
requires written permission for automated requests, including scripts/tools.
[Finanzen.net terms](https://www.finanzen.net/nutzungsbedingungen),
[L&S conditions](https://www.ls-tc.de/en/disclaimer) and
[Börse Stuttgart data access](https://www.boerse-stuttgart.de/de-de/fuer-geschaeftspartner/zugang-zu-boersendaten/)
do not establish a blanket grant for this application's automated multi-user quote
retrieval/display. The canonical multi-provider code remains in the retained source
but its scraping is disabled. The active alternatives are Yahoo diagnostics and
explicit dated manual valuations. No paid data subscriptions are purchased.

Only Yahoo/yfinance and the canonical [OpenFIGI identification API](https://www.openfigi.com/api)
are permitted network destinations for personal enrichment. Yahoo also supplies FX.
Provider restrictions, rate limits and authorization failures stop requests;
there is no CAPTCHA, proxy, alternate-identity or access-control workaround.
