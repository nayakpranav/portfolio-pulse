# Derivative quotation evidence — development assessment

Baseline: FolioLens Personal v1.0.11 (`01060eb`). Read-only quotation reference:
Holy Grail V6.7.10 commit `1a557d24e0a7c1f224743a659c9de3620e01ebba`.
The protected accounting reference remains V6.7.8, not the later collector.
This branch is an unpromoted development build, not an approved stable release.

## Source audit and access decisions (26 October 2026)

| Candidate | Evidence and decision |
| --- | --- |
| HSBC issuer website | [Terms](https://hsbc-zertifikate.de/emittent/rechtlichehinweise.html) require prior consent for reproduction. No documented anonymous structured-product bid API/access grant was established. Not enabled. |
| Societe Generale issuer website | The [website's legal notice](https://www.sg-zertifikate.de/contentmgmt/media/13ihwnzp/legal_information_de-de.pdf) restricts electronic reproduction/caching without written permission. Not enabled. |
| SG Markets SP API | [Documented API](https://shared.sgmarkets.com/sp-help-center-content/external-api-doc.html) requires SG Connect client credentials and serves bespoke products; it is not an established anonymous listed-warrant quote service. No credentials requested or execution API used. |
| Vontobel German product documents | [Terms](https://markets.vontobel.com/de-de/nutzungsbedingungen), rights section, explicitly permit private noncommercial downloads/copies. The bounded personal adapter reads only the published document and exact FinancialProduct ISIN. This narrow private-copy basis does not establish hosted or streaming-API rights. The inspected product document has identity/underlying metadata but quote placeholders, without bid/ask/time evidence. Internal streaming APIs and browser scraping are not used. |
| Stuttgart official delayed pre-trade files | [Official service](https://www.boerse-stuttgart.de/de-de/fuer-geschaeftspartner/reports/mifir-ii-delayed-data/) and its [download documentation](https://www.boerse-stuttgart.de/media/lfpd4xcs/verffentlichung-und-abruf-von-vor-und-nachhandelsdaten.pdf) explicitly provide free delayed pre-trade downloads. A real published file redirects to the exchange-issued CDN. An initial small-budget/line-by-line approach was inadequate. Buffered constant-memory streaming subsequently completed an approximately 252 MiB compressed / 2.68 GiB expanded file, including gzip CRC, in about ten seconds and found all seven requested ISINs. The observed JSON contract supplies exact ISIN, Bid/Ask, sizes, EUR currency and full UTC TransactionTime, but does not independently establish per-unit convention/current trading status. Its known 15-minute delay cannot become a current executable bid. |
| Frankfurt / Deutsche Boerse delayed service | [Official service](https://www.mds.deutsche-boerse.com/mds-en/real-time-data/Delayed-data) grants free noncommercial use of delayed files. Its separately listed Certificates and Warrants service is post-trade, which cannot substitute for bid prices. No validated current-bid adapter enabled. |
| Stuttgart retail website | [Terms](https://www.boerse-stuttgart.de/de-de/legal/agb-und-disclaimer/) constrain ordinary retrieval and automated requests. Only the separately documented delayed file offering is integrated; retail price/search scraping is not. |
| onvista | [Explicit written-permission requirement](https://hilfe.onvista.de/de/verstoss-gegen-nutzungsbedingungen) for automated requests. Rejected without permission. |
| finanzen.net | [Terms section 6(3)](https://www.finanzen.net/nutzungsbedingungen) explicitly prohibit automated retrieval without express permission. Rejected without permission. |
| comdirect | [Terms](https://www.comdirect.de/cms/hilfe-service-nutzungsbedingungen.html) permit private noncommercial copying, but no explicit automated quotation API permission/contract was established. Not enabled under this assignment's stronger explicit-automation requirement for aggregator sources. |
| Consorsbank / ARIVA | No explicit automated current-bid grant and supported interface established. [ARIVA terms](https://www.ariva.de/nutzungsbedingungen/) alone do not establish that grant. Not enabled. |

The inaccessible Stuttgart `robots.txt` resource and the actual public download
are distinct: a robots-resource 4xx is not a Disallow rule under
[RFC 9309 section 2.3.1.3](https://www.rfc-editor.org/rfc/rfc9309.html#section-2.3.1.3).
The documented public file was checked without identity/proxy/CAPTCHA changes.
Its original redirect was followed only to the specific exchange-issued CDN.
Actual source access denials/rate limits stop requests; no alternate identity,
endpoint substitution or safety bypass is implemented. Robots permission alone
is never interpreted as a market-data licence.

## Architecture and strict valuation boundary

`pulse/derivative_quotes.py` supplies immutable observations, explicit validation,
private document transport and worker-local collection. `pulse/derivative_stuttgart.py`
handles the official delayed file contract with bounded gzip processing. There
are no new dependencies. Dynamic open ISINs come from the canonical derivative
FIFO position frame; no portfolio identifiers are hardcoded or sent with quantities,
transaction rows, costs, account information or event identifiers.

The current private-copy adapters **do not produce verified current bids**.
The valuation boundary is fully implemented/tested for future verified provider
evidence, but the present Vontobel document parser intentionally cannot promote
an undocumented streaming response into an accepted quotation.

An eligible current observation requires an exact valid ISIN, reviewed HTTPS
source, explicit EUR/per-unit convention, actual bid, full date/time/UTC offset,
no future observation, at most 180 seconds of age, independently OPEN market
state, zero declared delay, valid optional sizes and a noninverted spread.
Spreads exceeding 50% require review. Weekends, closures, expiry and knockout
states cannot become current quotes. Ask-only, last-only, delayed and unknown-time
evidence never complete canonical valuation. HTTP retrieval time is not quote time.

Automatic accepted bids precede the existing optional manual observations. The
manual seven-day rule, graphical form, exact-activity binding and DPAPI store
remain unchanged. Missing/stale/unsupported automatic data remains unavailable.
No cost, old execution or zero fallback is introduced. The canonical engine's
126 protected definitions and every FIFO/return/cash-flow definition are unchanged.

Requests are serial, transparent, HTTPS-only and fixed-domain/path constrained.
Private documents have a 25-second / 20-request worker-local budget, 5-second
request bounds, response limits and 250ms spacing. The shared delayed-file read
has a 40-second / five-request budget, 512 MiB compressed / 8 GiB expanded limits
and 8192-byte requested-record/carry limits. The complete universe is streamed
through small buffers, never retained in memory or written into a portfolio cache.
Incomplete scans or failed gzip CRC are discarded. Restricted sources
are not retried. Automatic observations are cached only within one worker;
the valuation boundary revalidates freshness before use. Captured controlled
replays retain the recorded observation and evaluation time, never a fresh date.

Only personal, non-synthetic analyses receive the worker collection gate. Owner-
hosted/public modes cannot inherit it or private saved quotations. Synthetic
inputs and replay comparisons make no quote requests. Source raw snippets stay
in private run/capture evidence; HTML/PDF exports use explicit display fields and
contain no raw provider pages, signed download capabilities or transaction IDs.

## Labels and reporting

Underlying names come from explicit same-ISIN transaction descriptions or exact-
ISIN verified product metadata. Conflicting/missing names retain the original
label. Strike formatting is display-only. Historical turbo financing levels are
omitted; Open End is used only when the transaction/product evidence says so.
Missing expiry is explicitly unavailable, never copied from illustrative owner
examples. Canonical ISIN, quantities, values, positions and rankings do not change.

The expandable Quote Center shows active/automatic/indicative/missing coverage,
instrument labels, separate bid/ask, currency/unit qualification, source/time,
quantity/value and sanitized failures. The manual dropdown shows full descriptive
labels plus a wrapping selected-label caption. Offline HTML shares this prepared
model and escaping/CSP; the single-page PDF adds only a compact qualification.

## Production decision and remaining prerequisites

Do not merge, tag, publish or promote this branch merely because mock-provider
tests pass. v1.0.11 remains the installed production application. A stable v1.0.12
requires a genuinely supported permitted source that supplies exact-ISIN current
bid timestamps, currency, quotation convention and relevant market status within
the worker's resource budget, followed by controlled real-data and installed
Windows acceptance. An authorized targeted issuer feed is preferable to bulk
downloads. Accepting delayed/unknown-unit data as current liquidation prices,
reusing Holy Grail's ask/last fallback or increasing budgets without evidence is
not a safe resolution.

## Validation recorded on this development branch

The complete local suite passes **403 tests**, retaining the 328-test production
baseline. A final targeted quote/profile/UI suite passes 109 tests. Nine original
synthetic V6.7.8 comparisons match exactly. Controlled real-export comparison
matches **36 unrounded result fields**, all eight metrics, holdings, income and
diagnostics at identical captured market/FX/event inputs. A separate replay of
the v1.0.11 capture preserves the established financial dictionaries and figures.

Live collection under the application's worker HTTP controls obtained seven
exact-ISIN indicative exchange observations and zero acceptable current bids.
The source's private-copy/robots distinction and bounded listing timeout were
tested without changing client identity or bypassing actual data-resource denials.
The existing private profile is preserved. The supplied broker observations are
historical and have no precise comparable timestamp; no contemporaneous accuracy
claim is made. Private per-instrument captures and comparisons remain outside Git.

Generic development Windows packaging passes frozen worker/module, SSL, PDF/HTML,
CRC, file-manifest, SHA-256 and private-record scans without Python on PATH. This
is development validation, not a production installation or stable release.
