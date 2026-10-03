"""One-time audited extraction; reference material never becomes repository data."""
import ast
import hashlib
import json
from pathlib import Path
import sys

source = Path(sys.argv[1]).resolve()
target = Path(__file__).resolve().parents[1] / "vendor" / "v678"
target.mkdir(parents=True, exist_ok=True)
manifest = {"version": "V6.7.8", "commit": "0b865ca8b3c301c77e6a09eba5fa6d0c7fed16f2", "files": {}}
for name in ("portfolio_core.py", "automated_investing.py", "dividend_engine.py",
             "dividend_reinvestment.py", "exposure_engine.py", "security_events.py",
             "investment_snapshot_pdf.py", "analysis_engine.py"):
    original = (source / name).read_text(encoding="utf-8")
    text = original
    changes = []
    if name == "security_events.py":
        tree = ast.parse(text)
        node = next(n for n in tree.body if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "KNOWN_WORTHLESS_DERECOGNITIONS" for t in n.targets))
        lines = text.splitlines(keepends=True)
        lines[node.lineno-1:node.end_lineno] = ["KNOWN_WORTHLESS_DERECOGNITIONS = ()  # No personal event registry.\n"]
        text = "".join(lines)
        changes.append("Personal known-event entries removed; all matching safeguards retained")
    if name == "analysis_engine.py":
        # Keep the validated definitions and pipeline; omit summary rounding and exports.
        text = text[:text.index("summary = pd.DataFrame([", text.index("# 8. Build all data"))]
        start = text.index("metadata_overlay_path = ")
        end = text.index('print(f"Loaded:', start)
        text = text[:start] + 'SECURITY_METADATA_LKG_CACHE = LastKnownGoodMetadataCache(cache_path=OUTPUT_ROOT / "metadata.json")\n' + text[end:]
        # Entry into the adapter happens only in a fresh isolated worker process.
        marker = 'stage_start(1, 7, "Reading and validating the Trade Republic CSV")'
        text = text.replace(marker, 'from worker_hooks import install_hooks\ninstall_hooks(globals())\n\n' + marker, 1)
        text += '\nfrom worker_hooks import write_results\nwrite_results(globals())\n'
        changes += ["Old HTML/Excel/ZIP and rounded summary exports removed", "Metadata cache confined to worker temporary directory; private seed excluded", "Price injection and canonical result serialization hooks added"]
    (target / name).write_text(text, encoding="utf-8")
    manifest["files"][name] = {"original_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "selected_sha256": hashlib.sha256(text.encode()).hexdigest(), "changes": changes}
(target / "PROVENANCE.json").write_text(json.dumps(manifest, indent=2)+"\n")
print("Selected eight source modules; no artifacts, caches, notebooks or original history copied.")
