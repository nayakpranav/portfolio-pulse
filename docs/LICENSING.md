# Source and market-data licensing

The owner explicitly authorized selection, reuse, publication and independent
deployment of the supplied V6.7.8 source for Portfolio Pulse in this assignment.
The supplied source package otherwise carries a private-use restriction. That
restriction must not be interpreted as permission to publish the original package,
its personal registry, data/artifacts or historical repository.

Pulse has a separate source-available personal-use license in `LICENSE`. No broker
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
