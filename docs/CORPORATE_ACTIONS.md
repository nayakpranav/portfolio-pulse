# Canonical transaction and corporate-action coverage

FolioLens uses the retained V6.7.8 parser, support matrix, stock/fund FIFO,
derivative FIFO and cash-flow definitions. The engine is not replaced by a
name-based or generic delivery classifier.

| Export evidence | Automatic treatment | Safeguards |
| --- | --- | --- |
| Stock/fund BUY and SELL, including savings-plan buys | Canonical FIFO, fees/taxes and dated cash flows | Required quantities, supported acquisition lots and reconciliation |
| DIVIDEND / DISTRIBUTION and INTEREST_PAYMENT | Recognized net income ledgers | Canonical deduplication, tax and reinvestment reconciliation |
| STOCKPERK / BENEFITS_SAVEBACK | Existing promotional allocation and user-basis rules | Matched credit/acquisition conditions; no invented acquisition funding |
| Positive same-ISIN SPLIT quantity delta | Basis-conserving forward split | Exactly supported normalization and FIFO validation |
| Paired REVERSE_SPLIT at the same timestamp | Same-ISIN reverse split or identifier migration | Exactly one negative and one positive leg, conserved basis and lineage |
| DIVIDEND_REINVESTMENT with matching income/tax/funding legs | One economic-income recognition and FIFO acquisition | Canonical matching and complete-leg reconciliation |
| Derivative BUY / SELL / TILG / WARRANT_EXERCISE | Retained derivative FIFO and settlements | Supported quantities, acquisitions and settlement rules; current valuation independent |
| Previously verified exact zero-consideration full-position removal | Canonical worthless derecognition | Exact transaction hash, instrument/date/type/description/quantity, zero amount/price/fee/tax, supported prior activity and full-position removal |

IPO subscription cash is allocated only under the existing matched IPO-buy rule;
unallocated or unsupported evidence remains reviewable. Corporate actions outside
this coverage (including an arbitrary FREE_RECEIPT, custody transfer, merger or
spin-off) are not guessed. An unpaired reverse split or incomplete reinvestment
remains blocking. Standalone tax/fee labels are not generically treated as
supported security movements; observed combinations follow the support matrix.

Confirmed exceptional removals are private, per Windows user, encrypted with
DPAPI, and bound to exact broker transaction evidence plus the prior security
activity fingerprint. The original CSV digest still binds temporary row-based
confirmation and manual quote inputs to the current upload; it does not define
the identity of a remembered transaction. New exports containing unchanged
events automatically revalidate the canonical conditions. Similar names or
quantities are insufficient. Unsupported prior activity, partial removal,
nonzero consideration or missing transaction identity cannot be remembered as
an approved worthless loss.

Accounting for the complete export always runs first and remains in the result.
For safely localized failures only, a separate projection runs unchanged
canonical functions over **entire unaffected security histories**. It is labelled
partial, identifies excluded lineages, and cash-flow-matches the benchmark to
that same scope. It never removes just the inconvenient transaction, claims
complete holdings, or clears the original accounting status. Full-ecosystem
capital/recovery and totals stay blocked. Nonlocalizable failures remain
fail-closed. Recognized income remains independent only where its ledger and
reconciliation are valid.
