# Real-data integration audit

The MVP already retained the validated normalization, strict exact-ISIN discovery,
exchange/type checks, Yahoo latest/history, FX, stock/fund and derivative FIFO,
corporate actions, reinvestment, SaveBack/promo funding, recovery, XIRR, TWR and PME.
It removed the old multi-tab exports, personal event registry and metadata seed,
disabled derivative probing, and supplied controlled synthetic price hooks.

The real-data release adapts these extension points without changing retained
canonical definitions:

- `pulse/market.py`: worker-local provider bounds, exact benchmark metadata,
  adjusted/currency/coverage guards, public observation capture/replay and dated FX.
- `pulse/private_config.py`: export-bound event and manual valuation configuration.
  The original matcher/FIFO continue to make the accounting decisions.
- `pulse/resources.py`: admission, wall-time and RSS supervision with process cleanup.
- `pulse/mode.py`: public demo, personal loopback and explicitly verified owner modes.
- `worker_hooks.py`: install adapters before the original pipeline; return a compact
  canonical contract. No private configuration is a global application registry.
- `streamlit_app.py` / `pulse/pdf.py`: presentation, selected benchmark labels,
  data-health disclosures and session-specific reporting.

All 126 retained engine definitions are unchanged. The original repository,
deployment and supplied reference files are read-only. The synthetic fixture
inputs/trajectories are preserved; chart styling changes do not alter them.
