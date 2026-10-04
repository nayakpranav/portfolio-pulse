# FolioLens

Your investments, in focus.

An independent, unofficial Streamlit application for Trade Republic transaction
exports. A pinned V6.7.8 analytical core powers eight essential metrics, three
charts, deterministic observations, contextual explanations and a private,
one-page A4 landscape PDF.

**Release status:** real-data personal analysis implemented; public synthetic demo. The public release defaults
to a synthetic demo. Public CSV uploads remain disabled pending an approved
hosted market-data arrangement. Public demo: https://foliolens-tr.streamlit.app/.
See [release evidence](docs/VALIDATION.md) and [deployment](docs/DEPLOYMENT.md).

## Windows one-click personal application

Download the generic Windows portable package from [Releases](https://github.com/nayakpranav/portfolio-pulse/releases), extract it and double-click **Open FolioLens Personal.exe**. No Python installation or terminal commands are needed. The browser opens automatically; use the small launcher window to close the local application. [Windows instructions](docs/WINDOWS_PERSONAL.md).

## Run

Personal analysis defaults to **Stocks & funds** when derivative quotes are missing.
Valid value, reconciled profit, holdings, history and matched benchmark results
remain visible; full-portfolio totals remain incomplete. Capital/recovery retain
their full-ecosystem scope. [Metric availability](docs/ANALYSIS_SCOPES.md).
Owner-only website uploads remain independently gated; see the
[hosting assessment and remaining prerequisites](docs/OWNER_HOSTING_ASSESSMENT.md).
Supported transactions and corporate actions are automatic. Confirmed exceptional
events are remembered privately across newer exports using exact transaction and
prior-activity evidence, rather than the whole CSV digest.
See [corporate-action coverage and safeguards](docs/CORPORATE_ACTIONS.md).

Use Python 3.12. Create a virtual environment, install the pinned dependencies,
then start the application:

```sh
python -m venv .venv
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py --server.address 127.0.0.1
```

Activate the virtual environment before installing or running. On Windows use
`.venv\Scripts\Activate.ps1`; on macOS/Linux use `source .venv/bin/activate`.

Choose **Try with Demo Portfolio**, optionally change the benchmark, inspect the
summary and download **Portfolio Summary (PDF)**. **Clear session results**
clears both analysis/download state and the selected upload.

For the full local personal-use workflow, use the loopback launcher:

```powershell
python tools/run_personal.py
```

The upload accepts the documented UTF-8 Trade Republic transaction CSV schema,
at most 5 MB, 10,000 rows, 150 securities and ten years of dated history. Other
file formats are not converted. Unknown events and unresolved basis stay blocked.
Live market-data calls require network access and can fail or reach a timeout.

## Structure

- `vendor/v678/`: selected canonical modules and original/selected source hashes.
- `worker_hooks.py`: controlled synthetic price injection and canonical output serialization.
- `pulse/runner.py`: validated inputs, separate worker process and temporary-file cleanup.
- `pulse/adapter.py`: metric/source mapping, scope guards, chart and narrative inputs.
- `streamlit_app.py`: a single responsive dashboard; no private shared caches.
- `pulse/pdf.py`: composition using V6.7.8's ReportLab vector components.
- `pulse/synthetic.py`: fabricated fixtures and prices, without personal data.
- `tests/`: scenario, numeric, PDF, input-safety and session-isolation checks.

The accounting engine is not rewritten. All 126 retained canonical function/class
definitions match V6.7.8. Its old HTML, workbook and ZIP export layer is excluded.
The personal known-event registry and private metadata cache are not distributed.
Corporate-action matching, FIFO, income recognition, cash-flow recovery, XIRR,
historical TWR and benchmark PME retain their established definitions.

## Verify

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
python tools/privacy_audit.py
```

For the optional local-only reference comparison, set `PYTHONPATH` to the project
root and run `python tools/compare_reference.py <extracted-V6.7.8-source-directory>`.
Reference source and results are never committed. The provided release is synthetic;
no private transaction exports, portfolio reports or historical Git commits are included.

Read [methodology](docs/METHODOLOGY.md), [privacy](docs/PRIVACY.md) and
[source/licensing notes](docs/LICENSING.md) before enabling uploads on a server.
This project is not affiliated with or endorsed by Trade Republic. It is not a
broker statement, tax certificate or personalized investment recommendation.

## Benchmarks

MSCI World ETF (`IWDA.AS`, default), Global All-Country ETF (`VWCE.DE`),
S&P 500 ETF (`SXR8.DE`), or no comparison. Demo selections use separate
explicit synthetic EUR curves (100 to 109, 108, and 112 respectively,
6 January 2025 to 30 September 2026, business-day linear interpolation).
They illustrate matched-flow PME and do not represent real ETF performance.
No public demo market-data requests are made.

The enabled local workflow accepts a provider-compatible custom ticker.
Syntax is checked before processing; provider history, adjusted prices, currency
and FX coverage must validate. Failure leaves the benchmark unavailable without
substituting a ticker. Wealth charts use a padded EUR axis, minimum 5% span,
and a visible nonzero-axis notice. Recognized net investment income includes
reinvested dividends once and is not necessarily cash received in the account.

## Real portfolio analysis release

Yahoo-powered identification, current/historical quotes, EUR conversion and custom
benchmark metadata are integrated with the retained canonical engine. Exact-export
temporary confirmations and dated manual derivative valuations complete the
personal workflow without editing legitimate transaction rows. Price-only indices
are identified and warned; unavailable comparisons never substitute another ticker.

See [personal setup and private review](docs/PERSONAL_USE.md),
[component audit](docs/ARCHITECTURE_AUDIT.md), [security](docs/PRIVACY.md) and
[provider/deployment rights](docs/LICENSING.md). Full real-export acceptance uses
controlled captured prices/FX and the original read-only pipeline. Real financial
artifacts stay outside the checkout; public fixtures remain entirely fabricated.

Public CSV uploads remain disabled. `FOLIOLENS_MODE=public_demo` is the safe default;
`personal` uses the provided loopback launcher. `owner_hosted` additionally requires
verified external access controls and provider rights, with explicit operator
configuration. Configuration flags are not permission grants or authentication.
