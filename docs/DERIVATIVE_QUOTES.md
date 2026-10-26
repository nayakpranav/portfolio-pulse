# Lightweight derivative quotation evidence - development assessment

Production remains FolioLens Personal v1.0.11 (`01060eb`). This is draft PR #4,
not a promoted stable release. Holy Grail V6.7.10 (`1a557d24`) was inspected read-only
for quotation architecture; accounting remains the protected V6.7.8 baseline.

## Revised product policy

Delayed or previous-weekday **bids** may support a separately labelled indicative
estimate. Live executability is not required for that estimate. Exact ISIN, actual
full timestamp/timezone, reviewed source, EUR currency and per-unit convention
remain mandatory. A bid does **not** require an ask. Ask/last observations are
never relabelled bids. Optional dated manual entry is unchanged.

The Stuttgart MiFIR VHP collector is removed, not merely hidden behind a setting.
Its worker destinations and Windows module import are removed too. No substitute
exchange-wide dataset is downloaded. Old bulk captures are private historical
evidence only and are not reused as a current source.

## Source audit and access decisions

| Candidate | Evidence and decision |
| --- | --- |
| HSBC issuer website | [Terms](https://hsbc-zertifikate.de/emittent/rechtlichehinweise.html) require prior consent for reproduction. No documented anonymous structured-product bid API/access grant was established. Not enabled. |
| Societe Generale issuer website | The [website's legal notice](https://www.sg-zertifikate.de/contentmgmt/media/13ihwnzp/legal_information_de-de.pdf) restricts electronic reproduction/caching without written permission. Not enabled. |
| SG Markets SP API | [Documented API](https://shared.sgmarkets.com/sp-help-center-content/external-api-doc.html) requires SG Connect client credentials and serves bespoke products; it is not an established anonymous listed-warrant quote service. No credentials requested or execution API used. |
| Vontobel German product documents | [Terms](https://markets.vontobel.com/de-de/nutzungsbedingungen), rights section, explicitly permit private noncommercial downloads/copies. The bounded personal adapter reads only the published document and exact FinancialProduct ISIN. This narrow private-copy basis does not establish hosted or streaming-API rights. The inspected product document has identity/underlying metadata but quote placeholders, without bid/ask/time evidence. Internal streaming APIs and browser scraping are not used. |
| Stuttgart official delayed pre-trade files | Previously investigated official MiFIR download. **Removed**: exchange-wide compressed/expanded payload is unsuitable for this application. No normal-run adapter, allowed worker destination or packaged bulk module remains. |
| Frankfurt / Deutsche Boerse delayed service | [Official service](https://www.mds.deutsche-boerse.com/mds-en/real-time-data/Delayed-data) grants free noncommercial use of delayed files. Its separately listed Certificates and Warrants service is post-trade, which cannot substitute for bid prices. No validated current-bid adapter enabled. |
| Stuttgart retail website | [Terms](https://www.boerse-stuttgart.de/de-de/legal/agb-und-disclaimer/) constrain ordinary retrieval and automated requests. Only the separately documented delayed file offering is integrated; retail price/search scraping is not. |
| onvista | [Explicit written-permission requirement](https://hilfe.onvista.de/de/verstoss-gegen-nutzungsbedingungen) for automated requests. Rejected without permission. |
| finanzen.net | [Terms section 6(3)](https://www.finanzen.net/nutzungsbedingungen) explicitly prohibit automated retrieval without express permission. Rejected without permission. |
| comdirect | [Terms](https://www.comdirect.de/cms/hilfe-service-nutzungsbedingungen.html) permit private noncommercial copying, but no explicit automated quotation API permission/contract was established. Not enabled under this assignment's stronger explicit-automation requirement for aggregator sources. |
| Consorsbank / ARIVA | No explicit automated current-bid grant and supported interface established. [ARIVA terms](https://www.ariva.de/nutzungsbedingungen/) alone do not establish that grant. Not enabled. |


Additional lightweight services investigated:

- [Vontobel Instrument Market Data API](https://api-docs.deritrade.com/api/instrument-market-data/)
  documents per-ISIN intraday/daily requests, including LastBidAsk. It requires
  3ScaleExternal authorization; anonymous personal access and credentials were
  not established. Its reference API independently requires Bearer authorization.
  No guessed/internal website REST or streaming endpoint is used as a substitute.
- One separately inspected public website JSON response (the same issuer's
  `/api/v1/prices/{isin}/latest` route, under its private-copy terms and Allow
  robots rule) returned a small price/time/size object. It did **not** supply
  response ISIN, currency or quotation convention. This is an exploratory access
  observation, not a supported API contract or validation of EUR-per-unit bids.
  It is not enabled in normal analysis; no missing fields are inferred.
- [Euronext Web Services](https://www.euronext.com/en/data/how-access-market-data/web-services)
  offers selected quotes/reference data through a client service. No existing
  personal subscription/access or exact-product coverage was established. Nothing
  was purchased, registered or enabled.
- [Stuttgart market data](https://www.boerse-stuttgart.de/de-de/fuer-geschaeftspartner/zugang-zu-boersendaten/marktdaten/)
  advertises real-time/delayed/EOD packages through its data team, not an established
  anonymous ISIN-level contract available to this application.
- [gettex delayed data](https://www.gettex.de/handel/delayed-data/) permits natural
  persons' private use but exposes MIC-wide files. That is not a lightweight
  replacement. Personal vendor services require a separately arranged service.

No reviewed anonymous lightweight source currently supplies a usable dated
EUR-per-unit bid for the controlled seven-position acceptance. Published Vontobel
HTML may supply exact instrument metadata but its observed quote box contains
placeholders without full price timestamps. Missing evidence remains unavailable.
No claims of impossibility or blanket prohibition are made about providers that
could supply an authorized client contract later.

## Boundaries, budget and cache

`pulse/derivative_quotes.py` retains the provider-independent contract and strict
current-bid validator. `validate_indicative` separately reuses structural checks,
accepts dated bids observed today or no earlier than the previous weekday in
Europe/Berlin, and permits delayed/closed-market evidence. Weekends use Friday;
there is no unverified holiday extension. Older observations are explicitly
expired. Actual quote time is never replaced by download/cache time. Explicit
expired/knocked-out/suspended states, invalid prices, unverified units/currency,
wrong ISINs and inverted/implausible spreads are rejected.

The serial lightweight transport has an **8-second collection budget**, 12-request
ceiling, at most 1 second per connect/read interval, 250 ms spacing, **384 KiB per
document** and **1.5 MiB total consumed-body** limits. Oversized Content-Length
is refused before reading the body. Timeouts are not retried by the enclosing
worker for this provider. Deadlines/denials/rate limits stop collection; no proxy,
client-identity switch, browser bypass, credentials or new dependencies are used.
The target is approximately ten seconds or less of additional normal-run work.

`pulse/derivative_cache.py` stores small exact-ISIN observations, source, quote
type/time, scoped metadata and sanitized attempt diagnostics under the local
configured personal profile's `quote-cache` directory. Windows uses existing
user-bound DPAPI encryption and atomic writes with a nonblocking cross-process
write lock. Raw documents, transactions, costs, quantities, account/event IDs and
portfolio results are excluded. Cache failures do not block accounting. A cache
lock left by a hard crash disables writes safely and surfaces a diagnostic.

One attempt per weekday is reused, including unsuccessful attempts, to avoid
repeated requests during normal analyses. Cached usable quotes are revalidated
on every use; fresh download time never resets their age. At a new weekday the
provider is attempted again. A previous still-valid observation can survive that
attempt's failure with its original timestamp and explicit cache provenance.
Weekend reads do not repeatedly refresh Friday's record. No cache is shared
across OS users or hosted sessions, and hosted/synthetic workers cannot inherit
the cache path or collection gate. Clear removes analysis/download state but
intentionally retains this disclosed local market cache and prior private profile.

## Presentation and accounting

The prepared model exposes validated indicative position estimates, coverage,
known subtotal, complete derivative indicative total (only with all active
positions covered), and a separate full-portfolio estimate with cash excluded.
The latter additionally requires complete canonical accounting and stock/fund
valuations. Missing positions produce a labelled partial subtotal, never a full
estimate. Current/manual values and validated dated automatic bids can coexist.
These supplementary figures feed no headline metric, return, FIFO, ranking,
canonical profit or availability status. The eight existing headline metrics and
126 retained canonical definitions remain unchanged.

Streamlit, offline HTML and the one-page PDF use this same prepared estimate.
Source, actual timestamps, bid/ask types, cache status and limitations remain
visible in the quote center. The optional manual form and descriptive labels are
preserved. Generic Windows development packages contain no private cache/profile
or acceptance input. Production v1.0.11, its shortcut and rollback remain untouched.

## Validation and promotion

Tests cover bid-only delayed/closing observations, wrong instruments/types/units,
future/expired dates, conservative weekend handling, daily and negative caching,
cache corruption/isolation/expiry, valid prior-day fallback, blocked bulk hosts,
small response limits, mixed/partial estimates and unchanged canonical results.
Private development acceptance records live lightweight request time, consumed
HTTP body bytes, per-ISIN diagnostics, and a second cached analysis. Controlled
stock/FX inputs remain separate from live derivative requests.

Keep PR #4 draft. Do not merge, publish v1.0.12 or change the installed shortcut
until the owner reviews the development results and a usable permitted source is
actually established. Mock-provider tests validate interfaces, not live coverage.

Recorded validation on this revision: 421 full-suite tests, 125 focused tests,
nine exact canonical synthetic scenarios, and 36 exact unrounded private-export
comparison fields. The controlled seven-instrument lightweight collection took
2.860 seconds, consumed 845,684 HTTP response-body bytes in 12 requests, and
obtained zero usable bids. Its cached repetition took 0.015 seconds and made no
HTTP requests. These are measured development observations, not performance
guarantees or a claim of usable live coverage. TLS/headers are not counted as body
bytes. Private price/FX/event inputs and per-instrument evidence are not published.
