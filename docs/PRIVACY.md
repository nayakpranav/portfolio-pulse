# Privacy and data handling

Public source does not mean client-only processing. A CSV uploaded to a hosted
Streamlit app is transferred to and processed on that server. This application
does not claim that uploads stay on the user's computer.

**Public deployment defaults to synthetic demo only.** `PULSE_ENABLE_UPLOADS=1`
enables the production CSV widget. Do not enable it for a public deployment until
the operator has reviewed provider rights, hosting/privacy obligations and resource
limits. Local personal-use analysis should bind to `127.0.0.1`.

For an enabled upload:

- Each analysis uses a separate subprocess and unpredictable temporary directory.
- Private input, outputs and provider-cache overlays stay in that directory and
  are deleted on normal success, error or timeout. Operating-system/process crashes
  can leave temporary files; operators should clean stale directories securely.
- Worker stdout/stderr are discarded. Browser exceptions use generic messages;
  no raw export contents, broker identifiers or private diagnostics are logged.
- Analysis and PDF bytes are kept in the current Streamlit session only. There
  is no `st.cache_data`, private global cache or shared download directory.
- **Clear session results** deletes the model/PDF session keys and resets the
  upload widget with a new widget key. Streamlit/hosting connection lifetimes
  govern in-memory cleanup on disconnect; no secure RAM erasure is promised.
- Generated PDFs are private financial artifacts, even when source is public.
- No transaction files or generated private reports are committed or served from
  repository/static paths. `.gitignore` excludes outputs, uploads, secrets and caches.

No external price requests occur in synthetic mode. In local live analysis, the
inherited core may request public security ISINs/names/tickers, benchmark tickers
and currency pairs from Yahoo/yfinance and OpenFIGI. It does not send CSV histories,
account details, private row identifiers or generated reports. Public security
requests can still reveal which instruments are of interest to the user. Do not
put private text in security-name fields; use a verified broker export.

Open derivative scraping/probes, future dividend enrichment and additive sector
metadata fetching are disabled in Pulse. The original private metadata seed is
excluded. There are no required API credentials, model APIs or telemetry added.
Streamlit usage telemetry is disabled. The hosting platform has its own access
logs, processing policies and subprocess/runtime behavior.

Input limits: UTF-8 CSV, 5 MB, 10,000 rows, 100 securities, ten years, bounded text
fields and a 120-second worker timeout. Unsupported formats and malformed dates
fail before analysis. A server operator must add infrastructure resource/rate
limits appropriate to their hosting environment before enabling public uploads.

Local validation included concurrent independent worker processes with distinct
values/downloads, separate Streamlit sessions with different benchmark results,
clearing one session without affecting the other, temporary-file cleanup and
synthetic/malformed browser uploads. These tests do not establish a formal
security certification or approval of third-party market-data hosting rights.
