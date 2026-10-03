# FolioLens deployment

Public application: https://foliolens-tr.streamlit.app/
Repository: https://github.com/nayakpranav/portfolio-pulse

Community Cloud tracks `main`, entry point `streamlit_app.py`, Python 3.12 and
`requirements.txt`. Keep `PULSE_ENABLE_UPLOADS` unset on the public deployment.
Public portfolios and benchmark illustrations are synthetic; no market-data
requests are made. Custom tickers and real CSV processing are local-only.

After release validate eight metrics, three charts, three synthetic benchmark
illustrations and no comparison, period selection, income coverage, PDF download
and clearing results at desktop and 390px mobile widths, in independent sessions.
Never upload private reference data. See VALIDATION.md for recorded checks.
