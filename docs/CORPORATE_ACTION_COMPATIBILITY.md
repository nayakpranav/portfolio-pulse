# Non-cash corporate-action compatibility: PR #8

Production baseline: `d631b7c50b22619023de9e820198e39f6b97e6de` (v1.0.14).
Development only. No release, installation or shortcut promotion is authorized.

## Original eleven failures

The port omitted EXCHANGE from portfolio_core.CORPORATE_ACTION_TYPES.
Both the new ledger allowlist and central normalizer depend on that constant.
Consequently EXCHANGE legs never entered the normalizer or its audit, and the
support matrix correctly classified them as unknown security activity.

- Three positive tests stopped at BLOCKING instead of COMPLETE: consecutive
  conversion, economic-equivalence control and missing-successor-quote handling.
- Eight adversarial tests (unmatched leg, cash, fee, changed ratio, partial
  removal, different asset class, timestamp and category) retained BLOCKING but
  failed because no EXCHANGE audit record existed. They had not tested the new
  matcher at all.

The fixture also contained a latent error: an ordinary purchase's user-funded
basis was asserted without its fee, although no promotional funding existed.
Both bases include that fee under the existing methodology. This assertion was
previously hidden behind the first failure. A separate FIFO test deliberately
sets different acquisition/user-funded bases and proves both are carried intact.
Two other vacuous assertions were strengthened: realized profit is checked in
the actual canonical contract and recovery uses an existing required field.

## Implementation and safeguards

EXCHANGE is now registered in the shared action-type set. It is not a generic
sale or acquisition. Unique negative/positive stock/fund legs require matching
timestamps, effective dates, account/class and exact instrument identities;
source quantity must close fully, destination must not already hold units, and
cyclic/obsolete paths, nonfinite ratios and nonzero cash/fees/tax are rejected.
Only one-for-one EXCHANGE is supported without further economic evidence.
Explicit paired SPLIT and REVERSE_SPLIT use their validated quantity factors.

The existing FIFO operation retains lot acquisition dates, source trade IDs,
gross acquisition and user-funded bases. Its additional source-quantity guard
rejects mutation if actual open lots disagree with the normalized event.
Zero-quantity same-ISIN EXCHANGE can update metadata only for an active holding;
missing quantity is not evidence of an explicit zero. Raw normalized shares
therefore accompany the existing ledger validation fields.

At equal timestamps the normalizer uses original source order, rather than
alphabetical action-type order. A reverse split followed by an exchange at the
same instant must not be processed in reverse. Historical scaling likewise
distinguishes purchases before and after an action on the same date.

Successor quotation completeness remains independent. Earlier predecessor
history can fill only evidenced equal-unit intervals with recorded provenance;
quantity-changing predecessor prices are not rescaled without adjustment
evidence. Historical estimates retain their existing quality disclosure.
No new provider, personal registry or exceptional-ISIN rule is added.

## Canonical provenance and controlled comparisons

All 126 retained analysis-engine definitions remain present. **121 are AST
identical** to v1.0.14. Five existing definitions have the reviewed compatibility
extensions: classify_for_diagnostics, build_trade_ledger,
fifo_realized_and_positions, build_valuation_diagnostics and
build_historical_analytics. Three portfolio_core definitions are extended for
support classification, normalization and chronological historical scaling.
The original V6.7.8 provenance remains the original source identifier; it must
not be misrepresented as a hash of the modified implementation.

No MWR, XIRR, TWR, PME, income, tax, capital, recovery or economic-profit formula
is replaced. tools/compare_baseline.py uses the exact production tree and nine
fabricated scenarios, identical market inputs and unrounded canonical results.
Every common result and headline metric must match. The only permitted output
addition is empty predecessor-history provenance on unchanged histories.

An economically identical current-unit purchase also matches conversion history
for lifetime totals, daily NAV/TWR, period returns, benchmark balances, MWR and
cash-flow amounts/dates. One expected operational difference is explicitly
tested: canonical average fee per recorded investment event includes non-cash
action events in its existing denominator. This is not an investment return.

## Private acceptance and reporting

The authorized local export was processed with a separate temporary profile and
recorded live market inputs. Corrected accounting is COMPLETE, all eight headline
metrics are available and no current valuation blocker remains. The validated
action chain closes the predecessor and carries fractional successor quantity
and basis without realized profit or fictitious cash movement.

The production-baseline replay uses those same observations for unaffected
securities. Baseline-only obsolete identities/current quotes absent from the
capture are explicitly unavailable, not fabricated or assigned successor prices.
All 36 unaffected holdings match exactly on their complete existing fields;
recognized dividend and interest ledgers match exactly. The baseline remains
accounting-blocked as expected. Previously unavailable totals are not asserted
equal to corrected totals. Live historical estimates remain disclosed; the
private historical replay is not relabelled as wholly observed history.

The earlier partial HTML/PDF were inspected privately. Corrected HTML/PDF share
the prepared model and all eight displayed figures; one landscape A4 page and
text bounds pass. Offline HTML at desktop/mobile widths preserves CSP, chart and
period controls with no external requests. Files, snapshots, transaction IDs,
prices and captures stay outside Git; no private numbers accompany this document.

## Release boundary

PR #8 remains draft and unmerged. Development packaging and CI do not install a
new executable. The v1.0.14 application, desktop shortcut, private profiles,
rollback, public synthetic-only configuration and Holy Grail remain untouched.
Production promotion requires the owner's explicit approval.
