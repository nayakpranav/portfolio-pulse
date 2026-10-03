# Deployment

Repository: https://github.com/nayakpranav/portfolio-pulse

The source, synthetic workflow and local PDF downloads are validated. No public
Streamlit URL is claimed here until that deployed instance has actually been tested.

The current browser-control runtime failed before opening Streamlit's deployment
console (`helper_unknown_error: setup refresh had errors`). The environment has no
authenticated Streamlit deployment API. Interactive authorization/deployment is
therefore an external boundary, not a failed application calculation.

## Community Cloud steps

1. Sign into https://share.streamlit.io/ with the GitHub account that can deploy
   `nayakpranav/portfolio-pulse`. Authorize the required account access yourself.
2. Select **Create app** / deploy from GitHub.
3. Repository: `nayakpranav/portfolio-pulse`; branch: `main`;
   main file: `streamlit_app.py`; Python: **3.12**.
4. Choose an available app subdomain and deploy. Install from `requirements.txt`.
   Do **not** set `PULSE_ENABLE_UPLOADS=1` for this public release.
5. Open the deployed app; choose **Try with Demo Portfolio**. Verify all eight
   metrics, three charts, deterministic observations, metric explanations,
   holdings expansion, period selection and the data-health explanation.
6. Download the PDF and verify it is one landscape page labelled synthetic.
   Disable benchmark comparison and reanalyze. Clear the session results.
7. Repeat with a 390px browser viewport and in a second independent session.
   Verify session results/downloads do not cross. Inspect runtime/browser errors.

Never upload private reference transactions to the public application for deployment
validation. If a deployment changes package/runtime versions, repeat the synthetic
suite and controlled reference comparison before claiming equivalence.

These steps follow the [official Community Cloud deployment guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy).
An untested deployment or merely opening an editor is not deployment verification.
