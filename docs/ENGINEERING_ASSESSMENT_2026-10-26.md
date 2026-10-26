# Personal engineering assessment — 26 October 2026

## Baseline and boundaries

Baseline: v1.0.10, `c0c45d190a38c0e84a14a3073b52ff2231a576e4`.
This is a development branch, not a merge, deployment, installation or release.
The installed production package and public synthetic demonstration stay intact.
Holy Grail is read-only. The pinned engine, isolated worker, availability-aware
prepared model and Streamlit/HTML/PDF renderers remain the architecture. All 126
retained canonical definitions and original reference hashes are unchanged.

Baseline tests: **283 passed, one failed**. A fixed September FX fixture is now
stale under the existing seven-day rule. The test incorrectly compares unavailable
rates as `NaN == NaN`. Its current FX observation is now separate from historical
benchmark observations. No coverage rule or financial expectation is relaxed.

The authorized confidential CSV and October report are accessible. The installed
package matches the baseline manifest. Differently numbered filenames are not
assumed to exist. Real figures retain their precision in private reports and UI;
private inputs, captures, identifiers and financial outcomes are excluded from
public source, tests, logs and this document.

## Implemented corrections

**Freshness:** old manual derivative observations could previously make full
valuation appear complete. Live stock observations lacked the seven-day adapter
gate already used by holdings/composition presentation. Future FX dates passed a
one-sided date check. Live stock and optional manual observations now require a
valid nonfuture date within seven calendar days. Future FX is unavailable. Missing
evidence never becomes zero, cost or an old execution. This is an input-evidence
guard; canonical arithmetic is unchanged. A seven-day-old observation is not
certified as an executable bid. Controlled historical replays retain their actual
recorded evidence. Synthetic observations keep their explicit historical context.
Live results are captured after validation so replay cannot revive rejected prices.

**Optional graphical valuation:** an instrument selector, EUR-per-unit amount,
date, source and confirmation replace JSON editing. Reanalysis regenerates both
reports together. Stock/fund analysis does not require it. File-based configuration
remains supported. Opt-in local remembering uses Windows-user DPAPI and atomic
files outside the app/package, matched by exact derivative activity through the
observation date. Same names/ISINs alone cannot transfer evidence between investors.
Reordering and unrelated later rows preserve unchanged evidence; changed identity,
quantities, costs or prior history invalidate it. Expired records are never reused.
Hosted reviews stay session-only. Clear removes staged values/reports, retaining
only explicitly remembered private evidence. Corrupt optional evidence cannot
authorize a quote or block unrelated stock/fund accounting. Writes are synchronized
within one app process; independent simultaneous launcher writers are not a new
persistence guarantee.

**Truthful provider evidence:** the generic Yahoo derivative lookup could not
prove instrument identity and its values were always rejected. Those requests are
removed. Reporting no longer claims disabled scrapers were attempted, and actual
manual `quote_date` replaces an absent `quote_timestamp` mapping. No derivative
price model, additional scraper or changed FIFO/settlement calculation is added.

**Inspectable coverage:** the canonical historical ticker audit is exposed without
provider error payloads. Expandable details distinguish observed market dates,
carried market dates, transaction/filled estimates and unresolved history. Aggregate
shares retain canonical active-instrument-day denominators, not value weights;
per-security counts cover their own history windows. Forward details show active
holdings' verified ticker, canonical distribution status, known net estimate and
limitations. Missing net amounts stay unavailable; unscheduled payments, absent
observed retention/FX and broker-seasonality fallback remain qualified. Closed
earned entitlements can separately contribute to the canonical forecast total.
No extra tax assumption, forecast model or recognized-income change is introduced.

The private missing-trade-amount warning reconciles through canonical IPO cash/fee
allocation and is retained. Verified exceptional events still match exact private
transaction/prior-activity evidence across newer exports. Supported ordinary
exports require no additional confirmation.

## External libraries and compatibility

Public metadata reviewed 26 October 2026. Python declarations are not execution
tests. Only QuantStats is installed/run, in a separate developer environment.
The pinned application dependency file is unchanged.

| Project/version | License / requirements | Decision |
| --- | --- | --- |
| [QuantStats 0.0.86](https://pypi.org/project/quantstats/0.0.86/) | Apache-2.0; Python >=3.10; SciPy/matplotlib/seaborn | Accept for optional offline validation. Python 3.12 with pinned pandas/NumPy/yfinance is exercised. No graphing stack enters the portable app. |
| [pytr 0.4.10](https://github.com/pytr-org/pytr) | MIT; Python >=3.10; cryptography/curl/WebSocket stack, optional browser | Defer acquisition. Undocumented endpoints, changing authentication, credential custody and export completeness need a separately authorized adapter. No credentials, login or broker requests used. |
| [EdgarTools 5.62.1](https://pypi.org/project/edgartools/5.62.1/) | MIT; Python >=3.10; HTTPX/Pydantic/Arrow/rate limiter | Defer issuer research. Filings cannot establish broker withholding, entitlements or derivative quotes. Not installed/executed. |
| [skfolio 1.12.1](https://pypi.org/project/skfolio/1.12.1/) | BSD-3-Clause; Python >=3.10; solvers/scikit-learn/Plotly | Reject runtime integration. Optimization/factor models expand the product without resolving a demonstrated defect. Not installed/executed. |
| [Repomix 1.18.1](https://github.com/yamadashy/repomix) | MIT; Node >=22 | Defer development utility. Tracked-source allowlisting suffices; whole-workspace packing risks ignored private artifacts. No repository or portfolio content sent to another AI provider. Not installed/run. |

`tools/validate_returns.py` checks aligned canonical decimal daily returns and TWR
NAV: compounding, dates, every drawdown, maximum and current drawdown. It rejects
interior gaps before QuantStats can fill them. Passing explicit NAV avoids the
library's return/price magnitude heuristic. EUR wealth, matched benchmark wealth
and annualized MWR are not treated as daily TWR. No tear sheet, Sharpe/Sortino/alpha,
optimization or trading signal enters the UI. Disagreement fails validation without
changing a displayed figure. `requirements-validation.txt` is developer-only.
Use `python tools/validate_returns.py --synthetic`, or `--input` for a private
canonical result JSON. Only safe checks/counts are printed; no provider requests
are made. Independent synthetic validation runs in Linux CI. Windows packaging
explicitly excludes all experimental libraries.

## Providers and remaining limits

[pytr's authentication documentation](https://github.com/pytr-org/pytr#authentication)
describes changing login/frontend requirements and optional browser-mediated WAF
tokens. Source availability is not broker authorization. A future acquisition tool
must remain separate, avoid access-control workarounds, verify export completeness
and provide user-controlled authentication/credential custody.
[SEC developer rules](https://www.sec.gov/about/developer-resources) require
identified moderated access, currently at most ten requests/second across machines.
Issuer research requires exact issuer/CIK mapping, never substituted broker facts.

[yfinance guidance](https://ranaroussi.github.io/yfinance/) distinguishes software
licensing from Yahoo data rights and describes personal use. Hosted rights remain
unestablished. [Onvista requires prior written consent for automated access](https://hilfe.onvista.de/de/verstoss-gegen-nutzungsbedingungen).
[Börse Stuttgart describes free private website display and separate distribution
arrangements](https://www.boerse-stuttgart.de/de-de/fuer-geschaeftspartner/zugang-zu-boersendaten/),
which does not itself grant an automated feed. No new provider, paid service,
login or scraper is enabled. Derivative quotes need exact identity, EUR/unit
semantics, timestamp, price type and an authorized source. Underlying quotes,
KIDs and old executions cannot establish missing current values.

## Validation evidence

Actual graphical packaging validation discovers an additional local-runtime
dependency: the managed build interpreter's SSL module requires an adjacent
public trust-policy PEM resource. A worker-only smoke test did not import Altair
or exercise the launcher's HTTPS-context initialization, so it missed the fault.
The builder conditionally includes that exact resource without private key data,
certificate-verification changes or OS trust-store changes. The frozen smoke test
now constructs a verified SSL context and imports the application chart library.
Official Python CI does not require this additional runtime-specific file.

Nine synthetic canonical scenarios pass exact comparison. Latest-export acceptance
matches V6.7.8 across **35 unrounded fields**, with identical captured prices, FX,
dates, benchmark and private classifications. All eight metrics and existing
captured financial fields remain unchanged; provider/date text changes intentionally.
QuantStats independently agrees with synthetic and private complete daily series.

The PDF retains one landscape A4 page, its design and real figures. HTML retains
CSP/script hashing, escaping, offline operation, five TWR controls, wealth/drawdown,
rankings and tables. Added evidence is expandable; no headline card or navigation
is added. Session isolation/clearing, worker limits, private event matching and
public-upload restrictions remain. Final full regression, rendering, package and
CI evidence accompanies the branch/PR.

No authorized automatic feed is established for unpriced derivatives, so complete
value/profit remain unavailable without recent evidence. Historical estimates and
missing net-distribution evidence remain qualified. These are input limitations,
not reasons to weaken accounting or manufacture results.
