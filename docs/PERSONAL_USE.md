# Real portfolio analysis

For Windows, use the [one-click portable application](WINDOWS_PERSONAL.md).
The following source workflow is for developers: use Python 3.12, activate the project virtual environment and install
`requirements.txt`. Launch the full workflow on loopback:

```sh
python tools/run_personal.py
```

Open the displayed local URL. Upload the complete UTF-8 Trade Republic export,
select a preset or **Custom Yahoo Finance ticker**, and click **Analyze Portfolio**.
Download the private one-page PDF or offline HTML report. **Clear session results** resets the upload,
model, PDF, HTML and private review controls. Selecting a different file also removes
previous analysis/download state. There are eight headline metrics and three charts;
the prominent cumulative period TWR is a separate performance component.

Current prices, strict ISIN/exchange/type identification, historical close and
adjusted-close data, FIFO, cash-flow matching, EUR FX conversion and PME use the
retained V6.7.8 components. Missing identity/currency/price data is unavailable,
never zero. Historical transaction-price/fill estimates remain explicitly flagged.
There is no permanent portfolio storage in the application workflow.

## Private event review

Only unresolved negative security `FREE_RECEIPT` rows appear in
**Action required: review transaction**. Previously verified exact events are
recognized automatically, without displaying private evidence in the sidebar.
Review the displayed instrument, CSV line, date and quantity. Confirm only an
actually known full-position worthless write-off with no proceeds. Confirmation
is initially bound to the uploaded CSV and line. A successfully validated local
event is then remembered in the encrypted Windows profile using its exact
transaction identity and complete prior activity. Adding later transactions
does not invalidate the historical evidence; changing identity, consideration,
quantity or relevant prior history does.
The canonical matcher still requires the exact transaction, full prior position,
zero cash/fee/tax/price and supported prior activity. Partial, ambiguous or
unconfirmed removals remain blocking. This never modifies the public event registry.

An optional private JSON file outside the checkout can be loaded with
`FOLIOLENS_PRIVATE_CONFIG`. It has this structure (all placeholders below are
generic, not personal records):

```json
{
  "export_sha256": "SHA256_OF_THE_EXACT_CSV_BYTES",
  "worthless_confirmations": [2],
  "derivative_quotes": {
    "INSTRUMENT_ISIN": {
      "price_eur": 1.25,
      "date": "YYYY-MM-DD",
      "source": "Explicit broker bid or dated captured observation"
    }
  }
}
```

The example's placeholders must be replaced locally with reviewed evidence.
Obtain the digest with `hashlib.sha256(csv_bytes).hexdigest()`. Do not commit this
file. CSV line numbers start at 2. This optional export-specific advanced file
requires an updated digest after a change; the ordinary graphical workflow uses
persistent exact-event evidence and requires no manual JSON or hash calculation.

## Derivatives

Canonical derivative FIFO, settlements and valuation arithmetic are retained.
Yahoo's generic ISIN probe cannot establish a sufficiently reliable derivative
identity, so a low-confidence result is diagnostic, not an accepted valuation.
Unapproved non-Yahoo scrapers are disabled. After analysis, active positions appear
under **Advanced: optional dated derivative valuations**, including when all are
already valued. Edit prices together in the table. ISINs, descriptions and units
are read-only. Blank date/source cells use the common defaults; existing evidence
retains its original date/source unless explicitly replaced. Confirm the batch,
optionally remember it encrypted locally, and click **Save and update portfolio**.
Invalid rows retain their drafts and prevent the batch from being applied. Empty
price cells leave existing evidence intact. No JSON editing is needed.
Observations must be no more than seven calendar days old. Optional local
remembering is encrypted and bound to exact instrument activity; expired/changed
evidence is not reused. File-based private configuration remains supported.
A captured observation date
must not be represented as a verified exchange quote timestamp. Manual valuations
are disclosed separately from current valuation completeness. Without them, dependent tracked-value
and lifetime-profit totals remain unavailable; independently valid returns/income
remain available. No current derivative price is invented.

Successful same-session manual updates rerun the canonical engine with the original
captured market inputs and both HTTP transports blocked. A capture is bound to the
exact export, benchmark and accounting evidence; it expires after four hours or a
Berlin calendar-date change. Observation dates never advance on reuse. A missing,
changed or expired capture requires a disclosed full-analysis fallback. An
incomplete offline capture fails safely without silently requesting live data.
Scope changes regenerate both reports without an engine run. Batch updates keep
the selected scope; select **Full portfolio** to display its ecosystem KPIs.

**Analysis timing diagnostics** contains only aggregate stage durations, CPU time
and provider/request counts. Nested stages are inclusive and must not be summed.
Server presentation construction is distinct from browser rendering latency.

## Benchmarks and currencies

Presets are `IWDA.AS`, `VWCE.DE`, `SXR8.DE` and no comparison. Custom syntax supports
exchange suffixes and index symbols, including `SPY`, `VOO`, `^GSPC`, `^NSEI` and
`NIFTYBEES.NS`. Syntax is not proof of a suitable instrument. Yahoo must return
the exact symbol, resolved name, instrument type, currency, adjusted history and
matched-date coverage. ETFs, equities and mutual funds may provide the required
adjusted-price total-return proxy. Price-only or unverified total-return indices
are explicitly unavailable for dividend-inclusive comparison. Tickers are never
substituted. The comparison is canonical cash-flow-matched PME MWR, not price return.

Reporting is EUR. Non-EUR currencies use Yahoo's native-to-EUR pair where reliable
coverage exists. The canonical pence/agorot unit normalization is retained;
unknown currencies are refused. EUR/USD/GBP/INR and other provider currencies are
not assumed equivalent, and no universal FX-availability guarantee is made.
Failures, stale dates, history gaps and provider restrictions are visible.

## Controlled replay and acceptance

`run_analysis(..., capture=True)` can capture run-local public market inputs for
an explicitly controlled comparison. These identifiers can reveal holdings and
must stay private and outside the checkout. Replay performs no provider requests
and never fills absent observations with fresh live prices. The optional personal
test setting `FOLIOLENS_PERSONAL_REPLAY` points to such a captured JSON file;
unset it for normal live analysis. It preserves recorded dates rather than
pretending the observations are current. No durable shared last-known-good cache
is added. The core's run-local fallback estimates retain their diagnostics.

For local acceptance only:

```sh
python tools/compare_real.py EXTRACTED_SOURCE ORIGINAL_CSV CAPTURE_JSON PRIVATE_CONFIG_JSON
```

The comparison runs the read-only original pipeline and FolioLens with identical
captured prices, FX, benchmark, dates and private classifications. It compares
unrounded canonical records and all eight metrics. Financial results and the
safe pass/fail report are written to a private temporary acceptance directory
outside the checkout. The public source/tests contain no real portfolio fixtures.
