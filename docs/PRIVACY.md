# Privacy, security and actual retention

A hosted upload is transmitted to the Streamlit backend to calculate financial
results. Public securities/names/tickers and FX pairs may be sent to Yahoo Finance
and OpenFIGI, revealing instruments of interest. Reports contain private financial
information. This is not a claim that hosted financial data stays on the computer.

Public deployment remains `public_demo`, with no CSV input or live market requests.
The full local workflow uses `tools/run_personal.py` bound to `127.0.0.1`. Legacy
`PULSE_ENABLE_UPLOADS=1` remains a personal compatibility option, not an approved
public release. Explicit `owner_hosted` requires both provider-rights and owner-gate
verification flags. An external gateway must protect HTTP, WebSocket and media/
download endpoints; a flag alone is not authentication or a legal authorization.

## Input, execution and requests

UTF-8 CSV content is parsed and validated with canonical column mapping. Limits:
5 MB, 10,000 rows, 150 historical securities, ten years, 64 columns and bounded
fields. Private review JSON is capped at 64 KiB; backend messages at 16 MiB.
Workers receive only an allowlist of runtime environment paths, excluding arbitrary
application credentials. Binary NULs, malformed CSV/rows, duplicate headers, invalid dates and
nonfinite/extreme financial values are rejected. No input is evaluated/executed.
Canonical supported/unknown-event diagnostics determine which outputs remain valid.

Each analysis has its own subprocess and unpredictable temporary workspace. Two
workers can be admitted per application process. Workers are supervised for up
to 300 seconds and 1 GiB RSS, including child processes. Failure to monitor resources
stops analysis. Timeout/memory failure kills the worker before cleanup. These are
application-process controls, not infrastructure-wide DDoS/rate/abuse protection.
A hosted service still needs gateway quotas, aggregate resource budgets and
operational review. Public uploads remain off while those prerequisites are unmet.

Provider HTTP requests are HTTPS-only to Yahoo and OpenFIGI, with a ten-second
request ceiling, at most one transient timeout retry and an 800-request worker
budget. Restricted/denied/rate-limited providers stop receiving requests. Unapproved
redirects are refused. No CAPTCHA or authorization/rate-limit bypass is used.
Only public identification/price inputs are sent; transaction exports, account
identifiers, private transaction UUIDs and reports are never request payloads.
Unapproved non-Yahoo derivative scrapers remain disabled.

## Isolation and cleanup

Private input/config/results and all worker/provider caches stay in the analysis
temporary directory, which is deleted after success, errors and timeouts. Cleanup
retries transient filesystem locks; persistent failure is reported without exposing
paths. A hard process/OS crash can leave orphaned files. Operators must restrict
backend storage access and remove stale workspaces; no secure RAM/disk erasure is
promised. The original personal registry/metadata seed is not distributed.

There are no shared portfolio objects or private `st.cache_data` entries. Provider
objects/observations are cached within a single worker only. Models and PDF bytes
remain in the current Streamlit session. Clear resets upload/model/PDF and event/
manual-review state. Changing the uploaded export removes stale analysis and
download state. Framework connection expiry/orphan-media cleanup controls eventual
in-memory retention. A deliberately copied PDF or external private config is not
deleted by clearing the session. Avoid persistent shared volumes for private output.

Worker stdout/stderr and provider logging are suppressed. User errors are sanitized;
raw exception payloads, financial rows, private UUIDs and reports are not logged.
Only safe diagnostic statuses/counts are used for acceptance summaries. Streamlit
usage telemetry is disabled; the hosting provider has its own access/log policies.

Application private reports are never written into repository/static paths. Local
controlled acceptance deliberately stores confidential results/config and captured
identifiers in a private temporary directory outside the checkout. These contain
financial information and must not be uploaded to the public app or published.
The public audit covers staged/tracked source; Git-history hygiene is reviewed
separately. Synthetic fixtures alone are committed.

Tests cover independent workers/sessions, session-specific PDF bytes, clearing,
export-bound confirmations, malformed/oversized input, provider failures and
restrictions, admission/memory/time limits and transient cleanup failures. They
do not establish a formal security certification or unlimited hosting rights.
