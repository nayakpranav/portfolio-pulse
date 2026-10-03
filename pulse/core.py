"""Load pure canonical modules under their original names; no accounting duplication."""
import sys
from pathlib import Path
VENDOR = Path(__file__).resolve().parents[1] / 'vendor' / 'v678'
if str(VENDOR) not in sys.path:
    sys.path.insert(0,str(VENDOR))
from portfolio_core import validate_export_columns
from investment_snapshot_pdf import build_snapshot_data
