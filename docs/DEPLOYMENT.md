# FolioLens deployment

Public application: https://foliolens-tr.streamlit.app/
Repository: https://github.com/nayakpranav/portfolio-pulse

Community Cloud tracks `main`, entry point `streamlit_app.py`, Python 3.12 and
`requirements.txt`. Use `FOLIOLENS_MODE=public_demo` (the default). Keep legacy
`PULSE_ENABLE_UPLOADS` unset and all owner-verification flags unset on the public
deployment.
Public portfolios and benchmark illustrations are synthetic; no market-data
requests are made. Custom tickers and real CSV processing are available in the personal workflow
documented in [PERSONAL_USE.md](PERSONAL_USE.md). The launcher binds to loopback.
Do not expose personal mode to anonymous users.

Owner hosting requires `FOLIOLENS_MODE=owner_hosted`, verified provider rights and
a separately implemented authentication gateway protecting HTTP, WebSockets and
report downloads. Only after verifying those prerequisites may an operator set
`FOLIOLENS_OWNER_GATE_VERIFIED=1` and `FOLIOLENS_DATA_RIGHTS_VERIFIED=1`. These flags
are configuration attestations, not authentication or a data licence. Anonymous
public processing additionally requires operator quotas, abuse controls and
appropriate market-data rights; it is not enabled by this release.

Workers enforce two concurrent analyses per server process, a 300-second wall
limit, a monitored 1 GiB process-tree RSS limit and bounded provider requests.
This does not establish protection against abusive distributed traffic. Private
configuration/replay files belong outside the checkout and use deployment secrets
for paths; never commit their contents. No paid services are provisioned.

After release validate eight metrics, three charts, three synthetic benchmark
illustrations and no comparison, period selection, income coverage, PDF download
and clearing results at desktop and 390px mobile widths, in independent sessions.
Never upload private reference data. See VALIDATION.md for recorded checks.
