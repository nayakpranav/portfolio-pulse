# FolioLens Personal for Windows

Extract the entire **FolioLens-Personal-Windows.zip** package. Double-click
**Open FolioLens Personal.exe**. No Python installation, terminal commands or
package setup are required. Your default browser opens automatically.

Select the Trade Republic CSV, choose a benchmark and click **Analyze Portfolio**.
Download your private report and use **Clear session results** between exports.
The small launcher window provides **Open in browser** and **Close FolioLens**.
Closing that window stops the backend and removes its temporary workspace.
Keep the extracted folder and its `_internal` resources together.

The generic executable contains no portfolio data or personal event registry.
Previously verified event evidence is stored privately in your Windows user
profile and loaded automatically. Exact event identity, quantity, consideration
and prior-position checks still run in the V6.7.8 matcher for every export.
Changed or unrelated transactions are never automatically reclassified.

The portable launcher uses `%USERPROFILE%\FolioLensPersonal` for its private
profile, avoiding AppData redirection by packaged setup tools. Verified event
records are encrypted for the Windows user with DPAPI and have an atomic recovery
copy. Application updates do not replace these records. The launcher and sidebar
show the release version. No private event evidence is shipped in the ZIP.

Ordinary supported transactions need no confirmation. A genuinely ambiguous
delivery appears in **Private event / valuation review**, with its instrument,
date and quantity. Confirm a worthless write-off only with broker evidence;
transfers and exchanges are different events. Successful local confirmation is
remembered automatically for the exact transaction and complete prior security
activity. Later additions to an export do not invalidate that evidence. Changed
identity, consideration, quantities or prior history still require review.
Each Windows user has a separate encrypted store, and exact event plus prior
history binding prevents one portfolio's evidence applying to another. Hosted
confirmations remain session-only; no shared hosted event registry is created.

Missing current derivative quotes do not block independently valid stock/fund
returns, capital/recovery and recognized income. Tracked value and lifetime
profit remain unavailable when required valuations are missing. Dated manual
inputs remain optional inside the advanced private-review expander.

When derivative quotes are missing, **Stocks & funds** becomes the default scope.
Its value and reconciled lifetime profit exclude derivatives and cash interest.
Capital and recovery retain their full-ecosystem definition. Switch to **Full
portfolio** to see its dependent totals marked unavailable. Holdings, history and
the benchmark remain available when their own accounting/data validates. Specific
accounting blockers are displayed separately from valuation warnings.
See [scope definitions](ANALYSIS_SCOPES.md).

When a localized unresolved event affects only some instruments, an explicitly
labelled **Unaffected stocks & funds (partial)** scope can show the other security
histories and their matched performance. It excludes the affected instrument's
entire lineage and lists those exclusions. Full capital/recovery and portfolio
totals retain their accounting blockers. Unlocalized errors, duplicates and
missing accounting inputs remain fail-closed.

The application binds only to `127.0.0.1`, makes bounded Yahoo/OpenFIGI requests,
and retains the existing input, worker, session and PDF security controls.
It does not expose a public tunnel. The public Streamlit deployment stays synthetic.
Internet access is needed for live quotes; provider failures remain explicit.

This free portable release is unsigned. Windows may display its normal security
warning. No operating-system security controls are disabled or bypassed.
The launcher does not alter firewall rules, require administrator installation,
purchase services or install a background startup service.

Maintainers build with `tools/build_windows.py` or the Windows GitHub workflow.
Build inputs are an explicit list of public source/resources and dependencies.
Artifacts include package SHA-256 and a file manifest. CI uses synthetic data only;
private acceptance inputs, configurations, reports and captures are excluded.
