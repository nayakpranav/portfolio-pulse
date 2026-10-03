# ============================================================
# Trade Republic Holy Grail All-In-One Dashboard
# Version controlled by DASHBOARD_VERSION in Section 0.
# Stocks + Funds + Dividends + Interest + Derivatives + Diagnostics
#
# Integrated from:
#   - Holy Grail stock/fund FIFO dashboard V4
#   - Derivative Grail dashboard V3.0
#   - Audit fixes: ISO date parser, duplicate diagnostics, interest ledger,
#     active-only income proxy, redacted exports, unsupported activity summary,
#     safer price/quote labeling, unified diagnostics.
#
# Scope:
#   - Stocks/Funds: FIFO accounting, dividends/distributions, YoC, active-position dividend projection proxy, live Yahoo daily prices.
#   - Derivatives: FIFO cost-at-risk, realized P/L, TILG settlement, optional quote probes.
#   - Crypto: summarized as unsupported activity; not FIFO-accounted.
#   - Cash/card: cash ledger diagnostics plus a separate private expense analytics layer; excluded from investment P/L.
#
# Not official broker/tax reporting.
# ============================================================

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from automated_investing import build_saveback_analytics
from dividend_engine import YahooDividendProvider, build_dividend_analytics
from investment_snapshot_pdf import build_snapshot_data, render_snapshot_pdf
from dividend_reinvestment import (
    match_dividend_reinvestments,
    reconcile_reinvestment_gross,
)
from exposure_engine import (
    LastKnownGoodMetadataCache,
    build_security_sector_industry_metadata,
    build_sector_industry_analytics,
    fetch_yahoo_security_metadata as fetch_yahoo_security_metadata_core,
)
from security_events import (
    WORTHLESS_DERECOGNITION,
    match_known_worthless_derecognitions,
)

from portfolio_core import (
    APP_VERSION,
    MANIFEST_SCHEMA_VERSION,
    annotate_historical_scales,
    apply_split_factor_to_lots,
    build_transaction_support_matrix,
    canonical_column_mapping,
    normalize_corporate_actions,
    transaction_price_fallback_with_quality,
    validate_export_columns,
)

IN_COLAB = False
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the private Trade Republic portfolio dashboard from a CSV export."
    )
    parser.add_argument("--input-csv", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--enable-stock-prices", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--enable-derivative-quotes", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--enable-dividend-growth", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--enable-historical-analytics", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--benchmark-ticker", default="IWDA.AS")
    parser.add_argument("--benchmark-name", default="MSCI World UCITS ETF")
    parser.add_argument("--risk-free-rate-pct", type=float, default=0.0)
    parser.add_argument("--risk-free-rate-source", default="Manual")
    parser.add_argument("--risk-free-rate-date", default="")
    parser.add_argument("--show-detailed-progress", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--export-redacted", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--export-raw", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--openfigi-api-key", default=os.environ.get("OPENFIGI_API_KEY", ""))
    return parser


CLI_ARGS = _build_cli_parser().parse_args()


# ============================================================
# 0. User settings
# ============================================================

DASHBOARD_VERSION = APP_VERSION
OUTDIR_NAME = f"TR_Holy_Grail_All_In_One_{DASHBOARD_VERSION.replace('.', '_')}"


ENABLE_STOCK_FUND_LIVE_PRICES = bool(CLI_ARGS.enable_stock_prices)
ENABLE_DERIVATIVE_QUOTE_PROBE = bool(CLI_ARGS.enable_derivative_quotes)
ENABLE_DIVIDEND_GROWTH_PROJECTION = bool(CLI_ARGS.enable_dividend_growth)
ENABLE_HISTORICAL_ANALYTICS = bool(CLI_ARGS.enable_historical_analytics)
BENCHMARK_TICKER = str(CLI_ARGS.benchmark_ticker or "IWDA.AS").strip()
BENCHMARK_NAME = str(CLI_ARGS.benchmark_name or BENCHMARK_TICKER).strip()
RISK_FREE_RATE_PCT = float(CLI_ARGS.risk_free_rate_pct or 0.0)
RISK_FREE_RATE_SOURCE = str(CLI_ARGS.risk_free_rate_source or "Manual").strip()
RISK_FREE_RATE_DATE = str(CLI_ARGS.risk_free_rate_date or "").strip()
SHOW_DETAILED_PROGRESS = bool(CLI_ARGS.show_detailed_progress)

# Download controls. The files are always built; these switches control
# which standalone files Colab downloads automatically at the end.
AUTO_DOWNLOAD_HTML = False
AUTO_DOWNLOAD_EXCEL = False
AUTO_DOWNLOAD_ZIP = False

# Export raw clean transactions only if you explicitly accept PII risk.
EXPORT_RAW_CLEAN_TRANSACTIONS = bool(CLI_ARGS.export_raw)
EXPORT_REDACTED_CLEAN_TRANSACTIONS = bool(CLI_ARGS.export_redacted)

MAX_ROWS_IN_HTML = 2500
PROMO_MATCH_DAY_WINDOW = 2
CASH_PROMO_MATCH_DAY_WINDOW = 4
IPO_SUBSCRIPTION_MATCH_DAY_WINDOW = 30
DIVIDEND_AMOUNT_MODE = "AMOUNT_IS_GROSS"  # observed valid for current Trade Republic export structure

# Optional: use Yahoo dividend history to apply a conservative growth adjustment
# to the active-position TTM dividend proxy. If the history lookup fails,
# the dashboard safely falls back to the existing TTM proxy.
DIVIDEND_GROWTH_LOOKBACK_YEARS = 10
DIVIDEND_GROWTH_CLIP_LOW = -0.25
DIVIDEND_GROWTH_CLIP_HIGH = 0.20
DIVIDEND_GROWTH_FETCH_SLEEP_SECONDS = 0.15

ASSET_CLASSES_ALLOWED = {"STOCK", "FUND"}
TRADE_TYPES = {"BUY", "SELL", "SPLIT", "REVERSE_SPLIT", WORTHLESS_DERECOGNITION}
STOCK_ACQUISITION_TYPES = {"BUY", "DIVIDEND_REINVESTMENT"}
DIVIDEND_TYPES = {"DIVIDEND", "DISTRIBUTION"}
PROMO_TYPES_SECURITY_SPECIFIC = {"STOCKPERK", "BENEFITS_SAVEBACK"}
DERIVATIVE_ASSET_CLASS = "DERIVATIVE"
DERIVATIVE_TYPES = {"BUY", "SELL", "WARRANT_EXERCISE", "TILG"}

QUOTE_TIMEOUT_SECONDS = 15
QUOTE_MAX_WORKERS = 6
QUOTE_SOURCES_ENABLED = {
    "boerse_stuttgart": True,
    "onvista": True,
    "consors": True,
    "comdirect": True,
    "yahoo": True,
}

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
}

# ============================================================
# Generic Yahoo ticker discovery settings
# ============================================================
#
# Shared-code principle:
# - Do not hardcode personal holdings.
# - Use Trade Republic ISIN + security name to discover the Yahoo ticker.
# - Because this notebook is designed for Trade Republic Germany users,
#   prefer German/EUR listings first.
# - If no German/EUR listing is found, fall back to native EUR listings,
#   then non-EUR primary listings with FX conversion.
#
# Optional manual override dictionaries are intentionally empty.
# Keep them empty when sharing the notebook with others.
# Add entries only when a specific ticker is proven wrong.

YAHOO_GERMANY_PRIORITY_SUFFIXES = (
    ".DE",   # Xetra / Germany on Yahoo
    ".F",    # Frankfurt
    ".SG",   # Stuttgart
    ".HM",   # Hamburg
    ".MU",   # Munich
    ".DU",   # Düsseldorf
    ".BE",   # Berlin
)

YAHOO_EUR_NATIVE_PRIORITY_SUFFIXES = (
    ".AS",   # Amsterdam
    ".PA",   # Paris
    ".MI",   # Milan
    ".MC",   # Madrid
    ".VI",   # Vienna
    ".BR",   # Brussels
    ".LS",   # Lisbon
    ".IR",   # Ireland, when available
)

YAHOO_EUR_PRIORITY_SUFFIXES = (
    *YAHOO_GERMANY_PRIORITY_SUFFIXES,
    *YAHOO_EUR_NATIVE_PRIORITY_SUFFIXES,
)

# Backward-compatible name used elsewhere in the notebook.
EUR_PRIORITY_SUFFIXES = YAHOO_EUR_PRIORITY_SUFFIXES

YAHOO_NON_EUR_SUFFIXES = (
    ".L", ".SW", ".CO", ".ST", ".OL", ".HE",
    ".TO", ".V", ".AX", ".T", ".HK", ".SS", ".SZ",
)

GERMAN_EXCHANGES_YAHOO = {
    "GER", "FRA", "STU", "HAM", "MUN", "DUS", "BER",
    "XETRA", "FRANKFURT", "STUTTGART", "HAMBURG", "MUNICH",
}

EUR_NATIVE_EXCHANGES_YAHOO = {
    "AMS", "PAR", "MIL", "MCE", "VIE", "BRU", "LIS",
    "AEB", "XAMS", "XPAR", "XMIL", "XMAD", "XWBO", "XBRU", "XLIS",
}

YAHOO_ALLOWED_QUOTE_TYPES = {
    "EQUITY",
    "ETF",
    "MUTUALFUND",
}

YAHOO_BAD_SECURITY_TERMS = {
    "warrant",
    "turbo",
    "knock",
    "factor",
    "option",
    "call",
    "put",
    "certificate",
    "zertifikat",
    "mini future",
    "bonus cap",
    "discount certificate",
}

YAHOO_GENERIC_STOPWORDS = {
    "group", "holding", "holdings", "class", "stock", "shares",
    "registered", "ordinary", "common", "company", "corp", "corporation",
    "inc", "incorporated", "plc", "ag", "se", "sa", "nv", "ab", "asa",
    "etf", "fund", "usd", "eur", "acc", "dist", "distributing",
    "thesaurierend", "ausschüttend",
}

AUTO_TICKER_SEARCH_MAX_RESULTS = 25
AUTO_TICKER_PRICE_TEST_TOP_N = 15
AUTO_TICKER_SEARCH_SLEEP_SECONDS = 0.10

OPENFIGI_ISIN_RESOLUTION_ENABLED = True
OPENFIGI_API_URL = "https://api.openfigi.com/v3/mapping"
OPENFIGI_API_KEY = str(CLI_ARGS.openfigi_api_key or "").strip()
OPENFIGI_TIMEOUT_SECONDS = 25
OPENFIGI_BATCH_SIZE_WITHOUT_KEY = 5
OPENFIGI_BATCH_SIZE_WITH_KEY = 100
OPENFIGI_REQUEST_SLEEP_SECONDS = 0.35
OPENFIGI_MAX_RETRIES = 3
YAHOO_EXACT_ISIN_FALLBACK_ENABLED = True
ISIN_IDENTITY_CACHE = {}
SECURITY_METADATA_CACHE = {}
SECURITY_METADATA_LKG_CACHE = None

# Optional user-specific overrides.
# Keep empty for a generic shareable version.
YAHOO_TICKER_OVERRIDES = {}

# Optional user-specific dividend-history ticker overrides.
# Keep empty for a generic shareable version.
DIVIDEND_HISTORY_TICKER_OVERRIDES = {}

# Audit trail for automatic ticker selection.
# Written to TR_Auto_Ticker_Selection_Audit.csv in Section 9.
AUTO_TICKER_AUDIT_ROWS = []

# Automatic annual-DPS projection mode.
#
# V5.8 keeps the no-manual-override dividend projection workflow because maintaining
# one dividend-per-share value per company is annoying and error-prone.
# Latest annual DPS is now inferred automatically from yfinance dividend history:
#   - use the current calendar-year dividend sum if it already looks like a
#     substantially complete annual/semiannual payout; otherwise use the latest
#     completed year.
#   - convert ticker dividend currency to EUR through Yahoo FX.
#   - keep fallback to your active-position TTM cash received when history is missing.
# This is still an analytical estimate, not a declared dividend forecast.
LATEST_ANNUAL_DPS_OVERRIDES = {}

SENSITIVE_COLUMNS = [
    "counterparty_name",
    "counterparty_iban",
    "payment_reference",
    "description",
    "transaction_id",
    "mcc_code",
]

FX_CACHE = {"EUR": 1.0, "": 1.0, "UNKNOWN": 1.0}


# ============================================================
# Clean stage progress
# ============================================================

RUN_STARTED_AT = None
_STAGE_STARTED_AT = None
_STAGE_LABEL = ""
_STAGE_NUMBER = None
_STAGE_TOTAL = None
RUN_STAGE_TIMINGS = []


def stage_start(number, total, label):
    """Print one concise stage heading and start its timer."""
    global RUN_STARTED_AT, _STAGE_STARTED_AT, _STAGE_LABEL, _STAGE_NUMBER, _STAGE_TOTAL

    now = time.perf_counter()
    if RUN_STARTED_AT is None:
        RUN_STARTED_AT = now

    _STAGE_STARTED_AT = now
    _STAGE_LABEL = str(label)
    _STAGE_NUMBER = int(number)
    _STAGE_TOTAL = int(total)
    print(f"[{number}/{total}] {_STAGE_LABEL}")


def stage_end(detail=""):
    """Finish the current stage with elapsed seconds."""
    global _STAGE_STARTED_AT, _STAGE_NUMBER, _STAGE_TOTAL

    elapsed = (
        time.perf_counter() - _STAGE_STARTED_AT
        if _STAGE_STARTED_AT is not None
        else 0.0
    )
    detail_text = str(detail or "")
    suffix = f" — {detail_text}" if detail_text else ""
    print(f"      Done — {elapsed:.1f} s{suffix}")
    RUN_STAGE_TIMINGS.append({
        "stage_number": _STAGE_NUMBER,
        "stage_total": _STAGE_TOTAL,
        "stage": _STAGE_LABEL,
        "elapsed_seconds": round(elapsed, 3),
        "detail": detail_text,
    })
    _STAGE_STARTED_AT = None
    _STAGE_NUMBER = None
    _STAGE_TOTAL = None


# ============================================================
# 1. Upload/input
# ============================================================

INPUT_CSV = str(Path(CLI_ARGS.input_csv).expanduser().resolve())
INPUT_CSV_NAME = Path(INPUT_CSV).name
OUTPUT_ROOT = Path(CLI_ARGS.output_root).expanduser().resolve()
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
OUTDIR = OUTPUT_ROOT / OUTDIR_NAME

# Always start from a clean, run-specific output directory.
if OUTDIR.exists():
    shutil.rmtree(OUTDIR)

OUTDIR.mkdir(parents=True, exist_ok=True)
YFINANCE_CACHE_DIR = OUTPUT_ROOT / ".yfinance_cache"
YFINANCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
try:
    yf.set_tz_cache_location(str(YFINANCE_CACHE_DIR))
except Exception:
    # Older supported yfinance versions may not expose this helper. Cache
    # configuration is an enrichment concern and must never block accounting.
    pass
SECURITY_METADATA_LKG_CACHE = LastKnownGoodMetadataCache(cache_path=OUTPUT_ROOT / "metadata.json")
print(f"Loaded: {INPUT_CSV_NAME}", flush=True)

# ============================================================
# 2. Generic helpers
# ============================================================

def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_euro_number(s):
    if isinstance(s, pd.Series):
        if pd.api.types.is_numeric_dtype(s):
            return pd.to_numeric(s, errors="coerce")
        out = s.astype(str).str.strip()
        out = out.replace({"": np.nan, "nan": np.nan, "None": np.nan})
        out = (
            out.str.replace("€", "", regex=False)
               .str.replace("EUR", "", regex=False)
               .str.replace("\u00a0", "", regex=False)
               .str.replace(" ", "", regex=False)
        )
        german_mask = out.str.contains(r"^\-?\d{1,3}(\.\d{3})+,\d+$", regex=True, na=False)
        out.loc[german_mask] = out.loc[german_mask].str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
        comma_mask = out.str.contains(",", regex=False, na=False) & ~german_mask
        out.loc[comma_mask] = out.loc[comma_mask].str.replace(",", ".", regex=False)
        return pd.to_numeric(out, errors="coerce")
    return pd.to_numeric(s, errors="coerce")


def clean_str(x):
    if pd.isna(x):
        return ""
    x = str(x).strip()
    return re.sub(r"\s+", " ", x)


def safe_float(x, default=0.0):
    try:
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def safe_div(num, den):
    num = safe_float(num)
    den = safe_float(den)
    if abs(den) < 1e-12:
        return np.nan
    return num / den


def complete_numeric_sum(values):
    """Sum only when every constituent is numeric; explicit unknown stays unknown."""
    series = pd.to_numeric(pd.Series(values), errors="coerce")
    if series.empty:
        return 0.0
    return float(series.sum()) if series.notna().all() else np.nan


def money_print(x):
    return f"€{safe_float(x):,.2f}"


def pct_print(x):
    return f"{safe_float(x):.2f}%"


def to_jsonable_value(x):
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except Exception:
        pass
    if isinstance(x, (pd.Timestamp, datetime)):
        return x.strftime("%Y-%m-%d")
    if isinstance(x, np.datetime64):
        return pd.Timestamp(x).strftime("%Y-%m-%d")
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, (np.floating, float)):
        if not np.isfinite(x):
            return None
        return round(float(x), 8)
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    return x


def df_to_records(df):
    if df is None or df.empty:
        return []
    out = []
    for rec in df.to_dict(orient="records"):
        out.append({k: to_jsonable_value(v) for k, v in rec.items()})
    return out

def make_redacted_transactions(df):
    """
    Redact direct and derived PII fields.

    Security names for STOCK/FUND/DERIVATIVE/CRYPTO rows are kept because they
    are investment identifiers. Non-investment names are redacted because TR
    uses the same name/security_name fields for personal names, counterparties,
    and merchants.
    """
    out = df.copy()

    direct_sensitive = set(SENSITIVE_COLUMNS) | {
        "description_clean",
        "counterparty_name",
        "counterparty_iban",
        "payment_reference",
        "transaction_id",
        "mcc_code",
    }

    for c in direct_sensitive:
        if c in out.columns:
            out[c] = "[REDACTED]"

    if "asset_class_clean" in out.columns:
        asset = out["asset_class_clean"].fillna("").astype(str).str.upper().str.strip()
    elif "asset_class" in out.columns:
        asset = out["asset_class"].fillna("").astype(str).str.upper().str.strip()
    else:
        asset = pd.Series([""] * len(out), index=out.index)

    # Derived analytical tables often omit asset_class but retain an ISIN or an
    # explicit instrument/scope column. Treat those rows as investment rows so
    # security names remain visible in private analytical outputs.
    isin_mask = (
        out["isin"].fillna("").astype(str).str.strip().ne("")
        if "isin" in out.columns
        else pd.Series(False, index=out.index)
    )
    instrument_mask = (
        out["instrument_type"].fillna("").astype(str).str.upper().str.strip()
        .isin({"STOCK", "FUND", "ETF", "DERIVATIVE", "CRYPTO"})
        if "instrument_type" in out.columns
        else pd.Series(False, index=out.index)
    )
    scope_mask = (
        out["asset_scope"].fillna("").astype(str).str.upper().str.strip()
        .isin({"STOCK_FUND", "DERIVATIVE", "TRACKED_INVESTMENTS"})
        if "asset_scope" in out.columns
        else pd.Series(False, index=out.index)
    )
    investment_asset_mask = (
        asset.isin({"STOCK", "FUND", "DERIVATIVE", "CRYPTO"})
        | isin_mask
        | instrument_mask
        | scope_mask
    )

    for c in ["name", "security_name"]:
        if c in out.columns:
            out.loc[~investment_asset_mask, c] = "[REDACTED_NON_SECURITY_NAME]"

    return out


def make_safe_export(df):
    """
    Safer export wrapper for CSV outputs.

    Keeps accounting numbers but removes raw transaction identifiers and
    non-investment names.
    """
    out = make_redacted_transactions(df)

    id_like_cols = [
        c for c in out.columns
        if (
            "transaction_id" in c.lower()
            or c.lower().endswith("_trade_id")
            or c.lower() in {"trade_id", "dividend_id", "source_buy_trade_id", "sell_trade_id", "split_trade_id"}
        )
    ]

    for c in id_like_cols:
        out[c] = out[c].apply(
            lambda x: x if str(x) in {"", "nan", "NaT", "UNMATCHED_SELL", "SPLIT_UNMATCHED_REVIEW"} else "[REDACTED_ID]"
        )

    return out


def make_html_safe_dataframe(df):
    """Return a private analytical dataframe suitable for the HTML payload.

    Security and instrument names remain visible because the generated dashboard is
    intended for the owner's private use. Direct banking identifiers, free-text
    payment metadata and broker transaction identifiers are removed from the payload
    rather than replaced with visible redaction markers.
    """
    if df is None:
        return pd.DataFrame()
    out = df.copy()

    direct_sensitive = set(SENSITIVE_COLUMNS) | {
        "description_clean",
        "counterparty_name",
        "counterparty_iban",
        "payment_reference",
        "transaction_id",
        "mcc_code",
    }
    id_like_cols = {
        c for c in out.columns
        if (
            "transaction_id" in c.lower()
            or c.lower().endswith("_trade_id")
            or c.lower() in {
                "trade_id", "dividend_id", "source_buy_trade_id", "sell_trade_id",
                "split_trade_id", "close_event_id",
            }
        )
    }
    return out.drop(columns=sorted(direct_sensitive | id_like_cols), errors="ignore")


def escape_html_payload_value(value):
    """Neutralize markup before values reach legacy ``innerHTML`` consumers.

    Ampersands intentionally remain unchanged so ordinary labels such as
    ``S&P 500`` render naturally. Escaping angle brackets is sufficient to
    prevent a payload value from creating an HTML element; pre-encoded entities
    are decoded only as text when the containing HTML is parsed.
    """
    if isinstance(value, str):
        return value.replace("<", "&lt;").replace(">", "&gt;")
    if isinstance(value, list):
        return [escape_html_payload_value(item) for item in value]
    if isinstance(value, dict):
        return {key: escape_html_payload_value(item) for key, item in value.items()}
    return value


def df_to_html_records(df):
    return df_to_records(make_html_safe_dataframe(df))

# ============================================================
# 3. Shared parser and diagnostics
# ============================================================

def normalize_raw_dataframe(df):
    df = df.copy()
    validation = validate_export_columns(df.columns)
    if not validation.valid:
        raise ValueError(validation.message)
    df = df.rename(columns=canonical_column_mapping(df.columns))
    # If both an alias and its canonical name are present, prefer the first
    # canonical column deterministically and retain the extra source column only
    # in the immutable raw CSV.
    df = df.loc[:, ~df.columns.duplicated(keep="first")]
    optional_defaults = {
        "date": "", "datetime": "", "amount": np.nan, "name": "Unknown security",
        "symbol": "", "asset_class": "", "currency": "EUR", "description": "",
        "transaction_id": "", "category": "", "original_currency": "",
        "counterparty_name": "", "counterparty_iban": "", "payment_reference": "",
        "mcc_code": "", "account_type": "", "shares": np.nan, "price": np.nan,
        "fee": np.nan, "tax": np.nan, "original_amount": np.nan, "fx_rate": np.nan,
    }
    for column, default in optional_defaults.items():
        if column not in df.columns:
            df[column] = default

    df["type_norm"] = df["type"].fillna("").astype(str).str.upper().str.strip()
    df["asset_class_clean"] = df["asset_class"].fillna("").astype(str).str.upper().str.strip()
    df["security_name"] = df["name"].fillna("Unknown security").astype(str).apply(clean_str)
    df["isin"] = df["symbol"].fillna("").astype(str).apply(clean_str)
    df["currency_clean"] = df["currency"].fillna("EUR").astype(str).apply(clean_str)

    df["description_clean"] = df["description"].fillna("").astype(str).apply(clean_str)

    for col in ["transaction_id", "category", "original_currency", "counterparty_name", "counterparty_iban", "payment_reference", "mcc_code"]:
        if col not in df.columns:
            df[col] = ""

    for c in ["shares", "price", "amount", "fee", "tax", "original_amount", "fx_rate"]:
        if c in df.columns:
            df[c] = parse_euro_number(df[c])
        else:
            df[c] = np.nan

    # Corrected: TR CSV date observed as ISO YYYY-MM-DD. Keep broker/export calendar date.
    df["event_datetime"] = pd.to_datetime(df["datetime"], errors="coerce", utc=True).dt.tz_convert(None)
    parsed_date = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df["date_parse_failed"] = parsed_date.isna()
    df["event_date"] = parsed_date.fillna(df["event_datetime"].dt.normalize())

    df["datetime_date_utc"] = df["event_datetime"].dt.normalize()
    df["date_vs_datetime_day_mismatch"] = (
        df["event_date"].notna()
        & df["datetime_date_utc"].notna()
        & (df["event_date"] != df["datetime_date_utc"])
    )

    df["year"] = df["event_date"].dt.year.astype("Int64")
    df["month"] = df["event_date"].dt.month.astype("Int64")
    df["month_name"] = df["event_date"].dt.strftime("%b")
    df["quarter"] = "Q" + df["event_date"].dt.quarter.astype("Int64").astype(str)
    df["year_month"] = df["event_date"].dt.strftime("%Y-%m")
    df["source_row"] = np.arange(len(df)) + 2
    return df


def classify_for_diagnostics(df):
    df = df.copy()

    def normalized_category(t):
        t = str(t).upper().strip()
        if t == "BUY": return "Buy"
        if t == "SELL": return "Sell"
        if t == "DIVIDEND": return "Dividend"
        if t == "DISTRIBUTION": return "Distribution"
        if t == "SPLIT": return "Split"
        if t == "REVERSE_SPLIT": return "Reverse split / identifier migration"
        if t in {"STOCKPERK", "BENEFITS_SAVEBACK"}: return "Promo / saveback"
        if t == "INTEREST_PAYMENT": return "Interest"
        if "CARD" in t: return "Card payment"
        if t in {"CUSTOMER_INPAYMENT", "CUSTOMER_INBOUND", "TRANSFER_INBOUND", "TRANSFER_INSTANT_INBOUND"}: return "Cash deposit"
        if t in {"TRANSFER_INSTANT_OUTBOUND", "MANUAL_CASH_TRANSFER"}: return "Cash withdrawal"
        if t == "GIFT": return "Gift"
        if t == "REFERRAL": return "Referral"
        if t in {"TAX", "TAX REFUND", "TAX_OPTIMIZATION"}: return "Tax / tax refund"
        if t in {"WARRANT_EXERCISE", "TILG"}: return "Derivative / corporate action"
        if t == "IPO_SUBSCRIPTION": return "IPO subscription"
        return "Unknown / manual review"

    df["normalized_category"] = df["type_norm"].apply(normalized_category)
    df["include_in_portfolio_model"] = np.select(
        [
            df["asset_class_clean"].isin(ASSET_CLASSES_ALLOWED) & df["type_norm"].isin(TRADE_TYPES),
            df["type_norm"].isin(DIVIDEND_TYPES),
            df["type_norm"].isin(PROMO_TYPES_SECURITY_SPECIFIC),
            df["asset_class_clean"].eq(DERIVATIVE_ASSET_CLASS) & df["type_norm"].isin(DERIVATIVE_TYPES),
            df["type_norm"].isin({"WARRANT_EXERCISE", "TILG", "TAX_OPTIMIZATION", "IPO_SUBSCRIPTION"}),
        ],
        ["TRUE_STOCK_FUND", "TRUE_INCOME", "TRUE_PROMO", "TRUE_DERIVATIVE", "REVIEW"],
        default="FALSE",
    )
    trade_missing_core = (
        df["asset_class_clean"].isin(ASSET_CLASSES_ALLOWED | {DERIVATIVE_ASSET_CLASS})
        & df["type_norm"].isin({"BUY", "SELL"})
        & (df["amount"].isna() | df["shares"].isna())
    )

    df["manual_review_flag"] = np.where(
        df["include_in_portfolio_model"].eq("REVIEW")
        | df["event_date"].isna()
        | df["date_parse_failed"]
        | trade_missing_core
        | ((df["asset_class_clean"].isin(ASSET_CLASSES_ALLOWED | {DERIVATIVE_ASSET_CLASS})) & df["isin"].eq("")),
        "REVIEW",
        "OK",
    )
    return df


def build_pre_accounting_diagnostics(df):
    rows = []
    rows.append({"check": "raw_rows", "value": len(df), "severity": "INFO"})
    rows.append({"check": "date_parse_failed_rows", "value": int(df["date_parse_failed"].sum()), "severity": "BLOCKING" if int(df["date_parse_failed"].sum()) else "PASS"})
    rows.append({"check": "date_vs_utc_datetime_day_mismatch_rows", "value": int(df["date_vs_datetime_day_mismatch"].sum()), "severity": "INFO"})

    if "transaction_id" in df.columns:
        dup_count = int(df["transaction_id"].astype(str).replace("", np.nan).duplicated().sum())
    else:
        dup_count = 0
    rows.append({"check": "duplicate_transaction_id_rows", "value": dup_count, "severity": "BLOCKING" if dup_count else "PASS"})

    stock_fund_trades = df[df["asset_class_clean"].isin(ASSET_CLASSES_ALLOWED) & df["type_norm"].isin(["BUY", "SELL"])].copy()
    der_trades = df[df["asset_class_clean"].eq(DERIVATIVE_ASSET_CLASS) & df["type_norm"].isin(["BUY", "SELL"])].copy()
    all_trades = pd.concat([stock_fund_trades, der_trades], ignore_index=True)

    bad_buy_sign = int((all_trades["type_norm"].eq("BUY") & all_trades["amount"].notna() & (all_trades["amount"] >= 0)).sum())
    bad_sell_sign = int((all_trades["type_norm"].eq("SELL") & all_trades["amount"].notna() & (all_trades["amount"] <= 0)).sum())
    rows.append({"check": "buy_bad_amount_sign_rows", "value": bad_buy_sign, "severity": "WARNING" if bad_buy_sign else "PASS"})
    rows.append({"check": "sell_bad_amount_sign_rows", "value": bad_sell_sign, "severity": "WARNING" if bad_sell_sign else "PASS"})

    missing_trade_amount = int((all_trades["type_norm"].isin(["BUY", "SELL"]) & all_trades["amount"].isna()).sum())
    missing_trade_shares = int((all_trades["type_norm"].isin(["BUY", "SELL"]) & all_trades["shares"].isna()).sum())
    zero_cost_buy_rows = int((all_trades["type_norm"].eq("BUY") & (all_trades["amount"].isna() | (all_trades["amount"].fillna(0) == 0))).sum())
    rows.append({"check": "missing_trade_amount_rows", "value": missing_trade_amount, "severity": "WARNING" if missing_trade_amount else "PASS"})
    rows.append({"check": "missing_trade_shares_rows", "value": missing_trade_shares, "severity": "BLOCKING" if missing_trade_shares else "PASS"})
    rows.append({"check": "zero_or_missing_cost_buy_rows", "value": zero_cost_buy_rows, "severity": "WARNING" if zero_cost_buy_rows else "PASS"})

    amount_check = all_trades[all_trades["shares"].notna() & all_trades["price"].notna() & all_trades["amount"].notna()].copy()
    amount_check["amount_vs_qty_price_diff"] = amount_check["amount"].abs() - (amount_check["shares"].abs() * amount_check["price"])
    mismatch = int((amount_check["amount_vs_qty_price_diff"].abs() > 0.02).sum()) if not amount_check.empty else 0
    rows.append({"check": "trade_amount_qty_price_mismatch_gt_2ct", "value": mismatch, "severity": "WARNING" if mismatch else "PASS"})

    pii_cols_present = [c for c in SENSITIVE_COLUMNS if c in df.columns]
    rows.append({"check": "pii_columns_present_in_raw_dataframe", "value": len(pii_cols_present), "severity": "INFO" if pii_cols_present else "PASS"})
    return pd.DataFrame(rows)

# ============================================================
# 4. Stock/fund dividend, interest, promo, FIFO
# ============================================================

def build_clean_dividend_ledger(
    df,
    dividend_reinvestments=None,
    dividend_reinvestment_candidate_cash_rows=None,
):
    reinvestments = (
        dividend_reinvestments.copy()
        if isinstance(dividend_reinvestments, pd.DataFrame)
        else pd.DataFrame()
    )
    matched_cash_rows = set(dividend_reinvestment_candidate_cash_rows or set())
    if not reinvestments.empty:
        for column in ("positive_dividend_source_row", "funding_source_row"):
            matched_cash_rows.update(
                int(value) for value in pd.to_numeric(
                    reinvestments.get(column, pd.Series(dtype=float)), errors="coerce"
                ).dropna()
            )
    div = df[
        df["type_norm"].isin(DIVIDEND_TYPES)
        & ~df["source_row"].isin(matched_cash_rows)
    ].copy()
    tax_refunds = df[df["type_norm"].isin({"TAX", "TAX REFUND"}) & (df["amount"].fillna(0) > 0) & df["isin"].ne("")].copy()
    if not tax_refunds.empty:
        div = pd.concat([div, tax_refunds], ignore_index=True).drop_duplicates(subset=["transaction_id"], keep="first")
    if div.empty and reinvestments.empty:
        return pd.DataFrame(columns=["dividend_id", "payment_date", "isin", "gross_dividend_eur", "dividend_tax_withheld_eur", "dividend_tax_refund_eur", "net_dividend_eur"])

    if not div.empty:
        div["payment_date"] = div["event_date"]
        tax_raw = div["tax"].fillna(0)
        div["dividend_tax_withheld_eur"] = np.where(div["type_norm"].isin({"TAX", "TAX REFUND"}), 0, np.where(tax_raw < 0, -tax_raw, 0))
        div["dividend_tax_refund_eur"] = np.where(div["type_norm"].isin({"TAX", "TAX REFUND"}), div["amount"].fillna(0), np.where(tax_raw > 0, tax_raw, 0))

        if DIVIDEND_AMOUNT_MODE == "AMOUNT_IS_GROSS":
            div["gross_dividend_eur"] = np.where(div["type_norm"].isin({"TAX", "TAX REFUND"}), 0, div["amount"].fillna(0))
            div["net_dividend_eur"] = div["gross_dividend_eur"] - div["dividend_tax_withheld_eur"] + div["dividend_tax_refund_eur"]
        elif DIVIDEND_AMOUNT_MODE == "AMOUNT_IS_NET":
            div["net_dividend_eur"] = div["amount"].fillna(0)
            div["gross_dividend_eur"] = np.where(div["type_norm"].isin({"TAX", "TAX REFUND"}), 0, div["net_dividend_eur"] + div["dividend_tax_withheld_eur"] - div["dividend_tax_refund_eur"])
        else:
            raise ValueError("Invalid DIVIDEND_AMOUNT_MODE")

        div["reconciliation_error"] = (div["gross_dividend_eur"] - div["dividend_tax_withheld_eur"] + div["dividend_tax_refund_eur"] - div["net_dividend_eur"]).round(8)
        div["dividend_id"] = div["transaction_id"].fillna("").astype(str)
        div["dividend_id"] = div["dividend_id"].where(div["dividend_id"].ne(""), div.index.astype(str))
        div["dividend_per_share_gross_eur"] = np.where(div["shares"].abs() > 1e-12, div["gross_dividend_eur"] / div["shares"].abs(), np.nan)
        div["distribution_mode"] = "CASH"
        div["broker_reinvestment_base_eur"] = np.nan
        div["foreign_withholding_tax_eur"] = np.nan
        div["domestic_tax_and_solidarity_surcharge_eur"] = np.nan
        div["known_dividend_tax_subtotal_eur"] = div["dividend_tax_withheld_eur"]
        div["total_dividend_tax_eur"] = div["dividend_tax_withheld_eur"]
        div["net_dividend_income_eur"] = div["net_dividend_eur"]
        div["reinvested_amount_eur"] = np.nan
        div["reinvested_quantity"] = np.nan
        div["reinvested_acquisition_price_eur"] = np.nan
        div["cash_dividend_retained_eur"] = div["net_dividend_eur"]
        div["net_settlement_cash_effect_eur"] = div["net_dividend_eur"]
        div["gross_source"] = "TRADE_REPUBLIC_CSV_AMOUNT"
        div["foreign_withholding_source"] = "NOT_SEPARATELY_CLASSIFIED_FOR_ORDINARY_CASH_DIVIDEND"
        div["reinvestment_reconciliation_status"] = "NOT_APPLICABLE"
        div["reinvestment_event_id"] = ""
        div["source_row_ids"] = div["source_row"].astype("Int64").astype(str)

    if not reinvestments.empty:
        reinvested_rows = []
        for _, event in reinvestments[reinvestments["validation_status"].eq("PASS")].iterrows():
            payment_date = pd.to_datetime(event.get("payment_date"), errors="coerce")
            gross = pd.to_numeric(pd.Series([event.get("gross_dividend_eur")]), errors="coerce").iloc[0]
            total_tax = pd.to_numeric(pd.Series([event.get("total_dividend_tax_eur")]), errors="coerce").iloc[0]
            net_income = safe_float(event.get("net_dividend_income_eur"))
            source_row = safe_float(event.get("positive_dividend_source_row"), np.nan)
            reinvested_rows.append({
                "dividend_id": event.get("event_id", ""),
                "transaction_id": "",
                "payment_date": payment_date,
                "year": payment_date.year if pd.notna(payment_date) else pd.NA,
                "month": payment_date.month if pd.notna(payment_date) else pd.NA,
                "month_name": payment_date.strftime("%b") if pd.notna(payment_date) else "",
                "quarter": f"Q{payment_date.quarter}" if pd.notna(payment_date) else "",
                "year_month": payment_date.strftime("%Y-%m") if pd.notna(payment_date) else "",
                "security_name": event.get("security_name", ""),
                "isin": event.get("isin", ""),
                "asset_class_clean": "STOCK",
                "shares": event.get("entitlement_quantity"),
                "gross_dividend_eur": gross,
                "dividend_tax_withheld_eur": total_tax,
                "dividend_tax_refund_eur": 0.0,
                "net_dividend_eur": net_income,
                "dividend_per_share_gross_eur": event.get("gross_dividend_per_share_eur"),
                "currency_clean": "EUR",
                "original_amount": np.nan,
                "original_currency": "",
                "fx_rate": np.nan,
                "type_norm": "DIVIDEND_REINVESTMENT",
                "reconciliation_error": np.nan,
                "source_row": source_row,
                "distribution_mode": "REINVESTED",
                "broker_reinvestment_base_eur": event.get("broker_reinvestment_base_eur"),
                "foreign_withholding_tax_eur": event.get("foreign_withholding_tax_eur"),
                "domestic_tax_and_solidarity_surcharge_eur": event.get("domestic_tax_and_solidarity_surcharge_eur"),
                "known_dividend_tax_subtotal_eur": event.get("domestic_tax_and_solidarity_surcharge_eur"),
                "total_dividend_tax_eur": total_tax,
                "net_dividend_income_eur": net_income,
                "reinvested_amount_eur": event.get("reinvested_amount_eur"),
                "reinvested_quantity": event.get("reinvested_quantity"),
                "reinvested_acquisition_price_eur": event.get("reinvested_acquisition_price_eur"),
                "cash_dividend_retained_eur": np.nan,
                "net_settlement_cash_effect_eur": event.get("net_settlement_cash_effect_eur"),
                "gross_source": event.get("gross_source", "UNAVAILABLE_CSV_ONLY"),
                "foreign_withholding_source": event.get("foreign_withholding_source", "UNAVAILABLE_CSV_ONLY"),
                "reinvestment_reconciliation_status": event.get("reconciliation_status", ""),
                "reinvestment_event_id": event.get("event_id", ""),
                "source_row_ids": event.get("source_row_ids", ""),
            })
        if reinvested_rows:
            div = pd.concat([div, pd.DataFrame(reinvested_rows)], ignore_index=True, sort=False)

    keep = ["dividend_id", "transaction_id", "payment_date", "year", "month", "month_name", "quarter", "year_month", "security_name", "isin", "asset_class_clean", "shares", "gross_dividend_eur", "dividend_tax_withheld_eur", "dividend_tax_refund_eur", "net_dividend_eur", "dividend_per_share_gross_eur", "currency_clean", "original_amount", "original_currency", "fx_rate", "type_norm", "reconciliation_error", "source_row", "distribution_mode", "broker_reinvestment_base_eur", "foreign_withholding_tax_eur", "domestic_tax_and_solidarity_surcharge_eur", "known_dividend_tax_subtotal_eur", "total_dividend_tax_eur", "net_dividend_income_eur", "reinvested_amount_eur", "reinvested_quantity", "reinvested_acquisition_price_eur", "cash_dividend_retained_eur", "net_settlement_cash_effect_eur", "gross_source", "foreign_withholding_source", "reinvestment_reconciliation_status", "reinvestment_event_id", "source_row_ids"]
    return div[[c for c in keep if c in div.columns]].sort_values(["payment_date", "source_row"]).reset_index(drop=True)


def build_interest_ledger(df):
    interest = df[df["type_norm"].eq("INTEREST_PAYMENT")].copy()
    if interest.empty:
        return pd.DataFrame(columns=["payment_date", "year", "year_month", "gross_interest_eur", "interest_tax_withheld_eur", "net_interest_eur", "source_row"])
    tax_raw = interest["tax"].fillna(0)
    interest["payment_date"] = interest["event_date"]
    interest["gross_interest_eur"] = interest["amount"].fillna(0)
    interest["interest_tax_withheld_eur"] = np.where(tax_raw < 0, -tax_raw, 0)
    interest["interest_tax_refund_eur"] = np.where(tax_raw > 0, tax_raw, 0)
    interest["net_interest_eur"] = interest["gross_interest_eur"] - interest["interest_tax_withheld_eur"] + interest["interest_tax_refund_eur"]
    keep = ["payment_date", "year", "month", "year_month", "gross_interest_eur", "interest_tax_withheld_eur", "interest_tax_refund_eur", "net_interest_eur", "source_row"]
    return interest[[c for c in keep if c in interest.columns]].sort_values(["payment_date", "source_row"]).reset_index(drop=True)


def build_security_promo_table(df):
    """Build the complete promotional-funding table.

    Older Trade Republic exports identify the funded security directly. Newer
    Saveback rows can arrive as unscoped cash rewards without an ISIN. Those rows
    remain eligible for conservative amount-and-date matching to a subsequent
    security purchase.
    """
    promo = df[df["type_norm"].isin(PROMO_TYPES_SECURITY_SPECIFIC)].copy()
    if promo.empty:
        return pd.DataFrame(columns=[
            "event_date", "year", "year_month", "security_name", "isin",
            "asset_class_clean", "promo_type", "promo_amount_eur", "promo_scope",
            "transaction_id", "source_row",
        ])
    promo["promo_amount_eur"] = promo["amount"].fillna(0).clip(lower=0)
    specific = promo["asset_class_clean"].isin(ASSET_CLASSES_ALLOWED) & promo["isin"].ne("")
    promo["promo_scope"] = np.where(specific, "SECURITY_SPECIFIC", "UNSCOPED_CASH_REWARD")
    keep = [
        "event_date", "year", "year_month", "security_name", "isin",
        "asset_class_clean", "type_norm", "promo_amount_eur", "promo_scope",
        "transaction_id", "source_row",
    ]
    return (
        promo[[c for c in keep if c in promo.columns]]
        .rename(columns={"type_norm": "promo_type"})
        .sort_values(["event_date", "source_row"])
        .reset_index(drop=True)
    )


def attach_promo_credits_to_buys(
    trades,
    promo,
    day_window=PROMO_MATCH_DAY_WINDOW,
    cash_day_window=CASH_PROMO_MATCH_DAY_WINDOW,
):
    """Attach promotional funding to purchases without guessing broadly.

    Security-specific rewards retain the original ISIN-and-date matching. Unscoped
    cash-format Saveback rewards are matched only when a subsequent BUY within the
    short window has the same cash amount to the cent. This captures the newer TR
    export format while avoiding arbitrary attribution to ordinary purchases.
    """
    trades = trades.copy()
    trades["matched_promo_credit_eur"] = 0.0
    trades["funding_source"] = "USER_FUNDED"

    promo_pool = promo.copy()
    for col, default in {
        "remaining_promo_eur": 0.0,
        "matched_amount_eur": 0.0,
        "matched_buy_date": pd.NaT,
        "matched_security_name": "",
        "matched_isin": "",
        "matched_trade_id": "",
        "matched_trade_source_row": np.nan,
        "promo_match_status": "UNMATCHED",
        "promo_match_method": "",
    }.items():
        promo_pool[col] = default

    if promo_pool.empty:
        return trades, promo_pool

    promo_pool["remaining_promo_eur"] = pd.to_numeric(
        promo_pool["promo_amount_eur"], errors="coerce"
    ).fillna(0).clip(lower=0)

    def buy_total(idx):
        row = trades.loc[idx]
        return (
            safe_float(row.get("gross_buy_value_eur"))
            + safe_float(row.get("fee_paid_eur"))
            + safe_float(row.get("tax_paid_eur"))
        )

    def apply_match(pidx, bidx, amount, method):
        amount = max(0.0, safe_float(amount))
        if amount <= 1e-12:
            return
        total = buy_total(bidx)
        capacity = max(0.0, total - safe_float(trades.loc[bidx, "matched_promo_credit_eur"]))
        matched = min(amount, capacity)
        if matched <= 1e-12:
            return
        trades.loc[bidx, "matched_promo_credit_eur"] += matched
        current = safe_float(trades.loc[bidx, "matched_promo_credit_eur"])
        trades.loc[bidx, "funding_source"] = (
            "PROMO_MATCHED" if current >= total - 1e-8 else "PARTLY_PROMO_MATCHED"
        )
        promo_pool.loc[pidx, "remaining_promo_eur"] = max(
            0.0, safe_float(promo_pool.loc[pidx, "remaining_promo_eur"]) - matched
        )
        promo_pool.loc[pidx, "matched_amount_eur"] += matched
        promo_pool.loc[pidx, "matched_buy_date"] = trades.loc[bidx, "event_date"]
        promo_pool.loc[pidx, "matched_security_name"] = trades.loc[bidx, "security_name"]
        promo_pool.loc[pidx, "matched_isin"] = trades.loc[bidx, "isin"]
        promo_pool.loc[pidx, "matched_trade_id"] = trades.loc[bidx].get("trade_id", "")
        promo_pool.loc[pidx, "matched_trade_source_row"] = trades.loc[bidx].get("source_row", np.nan)
        promo_pool.loc[pidx, "promo_match_status"] = (
            "MATCHED" if safe_float(promo_pool.loc[pidx, "remaining_promo_eur"]) <= 1e-8
            else "PARTLY_MATCHED"
        )
        promo_pool.loc[pidx, "promo_match_method"] = method

    # Original security-specific matching.
    buy_idx = trades[trades["type_norm"].eq("BUY")].sort_values(
        ["event_date", "isin", "event_datetime", "source_row"]
    ).index
    for bidx in buy_idx:
        row = trades.loc[bidx]
        if pd.isna(row.get("event_date")):
            continue
        buy_date = pd.Timestamp(row["event_date"])
        isin = str(row.get("isin", ""))
        scoped = promo_pool[
            promo_pool["promo_scope"].eq("SECURITY_SPECIFIC")
            & promo_pool["isin"].eq(isin)
            & promo_pool["remaining_promo_eur"].gt(0)
        ].copy()
        if scoped.empty:
            continue
        scoped["date_diff_days"] = scoped["event_date"].apply(
            lambda d: abs((pd.Timestamp(d) - buy_date).days) if pd.notna(d) else 999999
        )
        scoped = scoped[scoped["date_diff_days"].le(day_window)].sort_values(
            ["date_diff_days", "event_date", "source_row"]
        )
        remaining_capacity = max(
            0.0, buy_total(bidx) - safe_float(trades.loc[bidx, "matched_promo_credit_eur"])
        )
        for pidx, prow in scoped.iterrows():
            if remaining_capacity <= 1e-8:
                break
            amount = min(safe_float(prow["remaining_promo_eur"]), remaining_capacity)
            apply_match(pidx, bidx, amount, "ISIN_AND_DATE")
            remaining_capacity -= amount

    # New cash-format Saveback matching. Require exact amount-to-cent and a
    # subsequent purchase, with a short window covering weekends/holidays.
    unscoped = promo_pool[
        promo_pool["promo_scope"].eq("UNSCOPED_CASH_REWARD")
        & promo_pool["remaining_promo_eur"].gt(0)
    ].sort_values(["event_date", "source_row"])
    for pidx, prow in unscoped.iterrows():
        pdate = pd.to_datetime(prow.get("event_date"), errors="coerce")
        reward = safe_float(prow.get("remaining_promo_eur"))
        if pd.isna(pdate) or reward <= 1e-12:
            continue
        candidates = []
        for bidx in buy_idx:
            bdate = pd.to_datetime(trades.loc[bidx, "event_date"], errors="coerce")
            if pd.isna(bdate):
                continue
            delta = (bdate.normalize() - pdate.normalize()).days
            if delta < 0 or delta > cash_day_window:
                continue
            capacity = max(
                0.0, buy_total(bidx) - safe_float(trades.loc[bidx, "matched_promo_credit_eur"])
            )
            amount_diff = abs(capacity - reward)
            if amount_diff > 0.02:
                continue
            description = str(trades.loc[bidx].get("description_clean", "")).lower()
            savings_plan_priority = 0 if "savings plan" in description else 1
            candidates.append((amount_diff, delta, savings_plan_priority, bdate, bidx))
        if not candidates:
            continue
        candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
        _, _, _, _, bidx = candidates[0]
        apply_match(pidx, bidx, reward, "UNSCOPED_EXACT_AMOUNT_AND_SUBSEQUENT_DATE")

    promo_pool["unmatched_promo_eur"] = promo_pool["remaining_promo_eur"]
    return trades, promo_pool


def attach_ipo_subscription_cashflows_to_buys(
    trades,
    df,
    day_window=IPO_SUBSCRIPTION_MATCH_DAY_WINDOW,
):
    """Allocate IPO prepayment/refund cash and subscription fees to a missing-amount BUY.

    Allocation is accepted only when the net IPO subscription cash for the same
    ISIN closely reconciles to shares multiplied by price. The source IPO rows can
    then be removed from manual review without hiding unresolved IPO activity.
    """
    trades = trades.copy()
    trades["ipo_allocated_fee_eur"] = 0.0
    trades["ipo_subscription_net_purchase_eur"] = np.nan
    trades["ipo_allocation_flag"] = ""
    allocated_source_rows = set()

    if df is None or df.empty:
        return trades, allocated_source_rows
    ipo = df[
        df["type_norm"].eq("IPO_SUBSCRIPTION")
        & df["asset_class_clean"].isin(ASSET_CLASSES_ALLOWED)
        & df["isin"].ne("")
    ].copy()
    if ipo.empty:
        return trades, allocated_source_rows

    missing_buy_idx = trades[
        trades["type_norm"].eq("BUY") & trades["amount"].isna()
    ].sort_values(["event_date", "event_datetime", "source_row"]).index

    for bidx in missing_buy_idx:
        row = trades.loc[bidx]
        buy_date = pd.to_datetime(row.get("event_date"), errors="coerce")
        if pd.isna(buy_date):
            continue
        candidates = ipo[
            ipo["isin"].eq(row.get("isin", ""))
            & ipo["event_date"].notna()
            & ipo["event_date"].le(buy_date)
            & ipo["event_date"].ge(buy_date - pd.Timedelta(days=day_window))
        ].copy()
        if candidates.empty:
            continue
        net_security_cash = max(0.0, -safe_float(candidates["amount"].fillna(0).sum()))
        allocated_fee = safe_float(candidates["fee"].abs().fillna(0).sum())
        inferred = safe_float(row.get("gross_buy_value_eur"))
        tolerance = max(0.05, inferred * 0.002)
        if net_security_cash <= 1e-12 or abs(net_security_cash - inferred) > tolerance:
            continue

        trades.loc[bidx, "gross_buy_value_eur"] = net_security_cash
        trades.loc[bidx, "fee_paid_eur"] = safe_float(trades.loc[bidx, "fee_paid_eur"]) + allocated_fee
        trades.loc[bidx, "ipo_allocated_fee_eur"] = allocated_fee
        trades.loc[bidx, "ipo_subscription_net_purchase_eur"] = net_security_cash
        trades.loc[bidx, "ipo_allocation_flag"] = "IPO_NET_CASH_AND_FEE_ALLOCATED"
        trades.loc[bidx, "trade_value_inference_flag"] = "IPO_SUBSCRIPTION_NET_CASH_AND_FEE_ALLOCATED"
        allocated_source_rows.update(int(x) for x in candidates["source_row"].dropna())
        if pd.notna(row.get("source_row")):
            allocated_source_rows.add(int(row.get("source_row")))

    return trades, allocated_source_rows

def build_trade_ledger(df, dividend_reinvestments=None):
    trades = df[df["asset_class_clean"].isin(ASSET_CLASSES_ALLOWED) & df["type_norm"].isin(TRADE_TYPES) & df["isin"].ne("")].copy()
    promo = build_security_promo_table(df)
    reinvestment_input = dividend_reinvestments if isinstance(dividend_reinvestments, pd.DataFrame) else pd.DataFrame()
    if trades.empty and reinvestment_input.empty:
        return pd.DataFrame(), promo, set()
    trades = trades.sort_values(["event_datetime", "event_date", "source_row"]).reset_index(drop=True)
    trades["quantity"] = trades["shares"].fillna(0)
    trades["trade_price_eur"] = trades["price"]

    trades["fee_paid_eur"] = np.where(
        trades["fee"].fillna(0) < 0,
        -trades["fee"].fillna(0),
        trades["fee"].fillna(0)
    )

    trades["tax_paid_eur"] = np.where(
        trades["tax"].fillna(0) < 0,
        -trades["tax"].fillna(0),
        0
    )

    trades["inferred_gross_trade_value_eur"] = (
        trades["quantity"].abs() * trades["trade_price_eur"]
    )

    trades["trade_value_inference_flag"] = ""

    buy_mask = trades["type_norm"].eq("BUY")
    sell_mask = trades["type_norm"].eq("SELL")
    amount_present = trades["amount"].notna()
    inferred_present = trades["inferred_gross_trade_value_eur"].notna() & (trades["inferred_gross_trade_value_eur"] > 0)

    trades["gross_buy_value_eur"] = 0.0
    trades["gross_sell_value_eur"] = 0.0

    trades.loc[buy_mask & amount_present, "gross_buy_value_eur"] = (
        trades.loc[buy_mask & amount_present, "amount"].abs()
    )

    trades.loc[sell_mask & amount_present, "gross_sell_value_eur"] = (
        trades.loc[sell_mask & amount_present, "amount"].abs()
    )

    trades.loc[buy_mask & ~amount_present & inferred_present, "gross_buy_value_eur"] = (
        trades.loc[buy_mask & ~amount_present & inferred_present, "inferred_gross_trade_value_eur"]
    )

    trades.loc[sell_mask & ~amount_present & inferred_present, "gross_sell_value_eur"] = (
        trades.loc[sell_mask & ~amount_present & inferred_present, "inferred_gross_trade_value_eur"]
    )

    trades.loc[buy_mask & ~amount_present & inferred_present, "trade_value_inference_flag"] = (
        "BUY_AMOUNT_MISSING_USED_ABS_SHARES_X_PRICE"
    )

    trades.loc[sell_mask & ~amount_present & inferred_present, "trade_value_inference_flag"] = (
        "SELL_AMOUNT_MISSING_USED_ABS_SHARES_X_PRICE"
    )

    trades.loc[buy_mask & amount_present & (trades["amount"] > 0), "trade_value_inference_flag"] = (
        "BUY_AMOUNT_POSITIVE_USED_ABS_AMOUNT_CHECK_EXPORT_SIGN"
    )
    trades, allocated_ipo_source_rows = attach_ipo_subscription_cashflows_to_buys(trades, df)
    trades["net_buy_cash_outflow_eur"] = np.where(trades["type_norm"].eq("BUY"), trades["gross_buy_value_eur"] + trades["fee_paid_eur"] + trades["tax_paid_eur"], 0)
    trades["net_sell_cash_inflow_eur"] = np.where(trades["type_norm"].eq("SELL"), trades["gross_sell_value_eur"] - trades["fee_paid_eur"] - trades["tax_paid_eur"], 0)
    trades["signed_quantity"] = trades["quantity"]
    trades.loc[trades["type_norm"].eq("BUY"), "signed_quantity"] = trades.loc[trades["type_norm"].eq("BUY"), "quantity"].abs()
    trades.loc[trades["type_norm"].eq("SELL"), "signed_quantity"] = -trades.loc[trades["type_norm"].eq("SELL"), "quantity"].abs()
    trades.loc[trades["type_norm"].eq(WORTHLESS_DERECOGNITION), "signed_quantity"] = -trades.loc[trades["type_norm"].eq(WORTHLESS_DERECOGNITION), "quantity"].abs()
    trades.loc[trades["type_norm"].eq("SPLIT"), "signed_quantity"] = trades.loc[trades["type_norm"].eq("SPLIT"), "quantity"].fillna(0)
    trades["acquisition_cost_basis_eur"] = np.where(trades["type_norm"].eq("BUY"), trades["gross_buy_value_eur"] + trades["fee_paid_eur"] + trades["tax_paid_eur"], 0)
    trades, promo = attach_promo_credits_to_buys(trades, promo)
    trades["user_funded_cost_basis_eur"] = np.where(trades["type_norm"].eq("BUY"), (trades["acquisition_cost_basis_eur"] - trades["matched_promo_credit_eur"]).clip(lower=0), 0)
    trades["trade_id"] = trades["transaction_id"].fillna("").astype(str)
    trades["trade_id"] = trades["trade_id"].where(trades["trade_id"].ne(""), trades.index.astype(str))
    reinvestments = reinvestment_input
    if not reinvestments.empty:
        reinvested_trades = []
        for _, event in reinvestments[reinvestments["validation_status"].eq("PASS")].iterrows():
            event_date = pd.to_datetime(event.get("action_date"), errors="coerce")
            basis = safe_float(event.get("reinvested_amount_eur"))
            quantity = safe_float(event.get("reinvested_quantity"))
            source_row = safe_float(event.get("action_source_row"), np.nan)
            reinvested_trades.append({
                "trade_id": event.get("event_id", ""), "transaction_id": "",
                "event_datetime": event_date, "event_date": event_date,
                "year": event_date.year if pd.notna(event_date) else pd.NA,
                "month": event_date.month if pd.notna(event_date) else pd.NA,
                "month_name": event_date.strftime("%b") if pd.notna(event_date) else "",
                "quarter": f"Q{event_date.quarter}" if pd.notna(event_date) else "",
                "year_month": event_date.strftime("%Y-%m") if pd.notna(event_date) else "",
                "type_norm": "DIVIDEND_REINVESTMENT", "asset_class_clean": "STOCK",
                "security_name": event.get("security_name", ""), "isin": event.get("isin", ""),
                "quantity": quantity, "signed_quantity": quantity,
                "trade_price_eur": event.get("reinvested_acquisition_price_eur"),
                "amount": np.nan, "gross_buy_value_eur": basis, "gross_sell_value_eur": 0.0,
                "fee_paid_eur": 0.0, "tax_paid_eur": 0.0,
                "net_buy_cash_outflow_eur": basis, "net_sell_cash_inflow_eur": 0.0,
                "matched_promo_credit_eur": 0.0, "funding_source": "DIVIDEND_PROCEEDS_INTERNAL",
                "acquisition_cost_basis_eur": basis, "user_funded_cost_basis_eur": basis,
                "currency_clean": "EUR", "description_clean": "Dividend reinvestment / Wahldividende",
                "source_row": source_row, "inferred_gross_trade_value_eur": basis,
                "trade_value_inference_flag": "BROKER_REINVESTMENT_BASE_LINKED",
                "ipo_allocated_fee_eur": 0.0, "ipo_subscription_net_purchase_eur": np.nan,
                "ipo_allocation_flag": "", "dividend_reinvestment_event_id": event.get("event_id", ""),
                "source_row_ids": event.get("source_row_ids", ""),
            })
        if reinvested_trades:
            trades = pd.concat([trades, pd.DataFrame(reinvested_trades)], ignore_index=True, sort=False)
    keep = ["trade_id", "transaction_id", "event_datetime", "event_date", "year", "month", "month_name", "quarter", "year_month", "type_norm", "broker_type_norm", "asset_class_clean", "security_name", "isin", "quantity", "signed_quantity", "trade_price_eur", "amount", "gross_buy_value_eur", "gross_sell_value_eur", "fee_paid_eur", "tax_paid_eur", "net_buy_cash_outflow_eur", "net_sell_cash_inflow_eur", "matched_promo_credit_eur", "funding_source", "acquisition_cost_basis_eur", "user_funded_cost_basis_eur", "currency_clean", "description_clean", "source_row", "inferred_gross_trade_value_eur", "trade_value_inference_flag", "ipo_allocated_fee_eur", "ipo_subscription_net_purchase_eur", "ipo_allocation_flag", "dividend_reinvestment_event_id", "source_row_ids", "known_security_event_id", "tax_treatment_status"]
    return trades[[c for c in keep if c in trades.columns]].sort_values(["event_datetime", "source_row"]).reset_index(drop=True), promo, allocated_ipo_source_rows


def fifo_realized_and_positions(trades, corporate_action_audit=None):
    realized_rows, open_lots_rows, holding_rows, split_allocation_rows = [], [], [], []
    corporate_action_audit = (
        corporate_action_audit.copy()
        if corporate_action_audit is not None
        else pd.DataFrame()
    )
    if trades.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), corporate_action_audit
    for isin, g in trades.groupby("isin", dropna=False):
        g = g.sort_values(["event_datetime", "source_row"]).copy()
        security_name = g["security_name"].dropna().iloc[-1]
        asset_class = g["asset_class_clean"].dropna().iloc[-1]
        canonical_instrument_id = str(g.get("canonical_instrument_id", pd.Series([f"ISIN:{isin}"])).dropna().iloc[-1])
        historical_isin_aliases = str(g.get("historical_isin_aliases", pd.Series([str(isin)])).dropna().iloc[-1])
        lots = []
        totals = dict(buy_qty=0.0, sell_qty=0.0, derecognition_qty=0.0, split_qty=0.0, buy_value=0.0, sell_value=0.0, fees=0.0, tax=0.0, promo=0.0, acq_orig=0.0, user_orig=0.0)
        for _, r in g.iterrows():
            typ = r["type_norm"]
            qty_signed = safe_float(r["signed_quantity"])
            qty_abs = abs(qty_signed)
            totals["fees"] += safe_float(r["fee_paid_eur"])
            totals["tax"] += safe_float(r["tax_paid_eur"])
            if typ in STOCK_ACQUISITION_TYPES:
                cost_basis = safe_float(r["acquisition_cost_basis_eur"])
                user_basis = safe_float(r["user_funded_cost_basis_eur"])
                totals["buy_qty"] += qty_abs
                totals["buy_value"] += safe_float(r["gross_buy_value_eur"])
                totals["promo"] += safe_float(r["matched_promo_credit_eur"])
                totals["acq_orig"] += cost_basis
                totals["user_orig"] += user_basis
                lots.append({"source_trade_id": r["trade_id"], "buy_date": r["event_date"], "quantity_remaining": qty_abs, "quantity_original": qty_abs, "cost_basis_remaining": cost_basis, "cost_basis_original": cost_basis, "user_basis_remaining": user_basis, "user_basis_original": user_basis, "buy_price": r["trade_price_eur"], "funding_source": r["funding_source"]})
            elif typ == "SPLIT":
                split_extra_qty = qty_signed
                quantity_before = sum(lot["quantity_remaining"] for lot in lots)
                basis_before = sum(lot["cost_basis_remaining"] for lot in lots)
                user_basis_before = sum(lot["user_basis_remaining"] for lot in lots)
                declared_factor = safe_float(r.get("corporate_action_factor"), np.nan)
                if not np.isfinite(declared_factor) and quantity_before > 1e-12:
                    declared_factor = (quantity_before + split_extra_qty) / quantity_before
                action_status = "PASS"
                warning_error = ""
                if not lots or not np.isfinite(declared_factor) or declared_factor <= 0:
                    action_status = "BLOCKING"
                    warning_error = "Corporate action has no matching pre-action FIFO lots or an invalid ratio."
                else:
                    quantities_before = {
                        lot["source_trade_id"]: lot["quantity_remaining"] for lot in lots
                    }
                    apply_split_factor_to_lots(lots, declared_factor)
                    quantity_after = sum(lot["quantity_remaining"] for lot in lots)
                    totals["split_qty"] += quantity_after - quantity_before
                    for lot in lots:
                        before = quantities_before.get(lot["source_trade_id"], 0.0)
                        split_allocation_rows.append({
                            "split_trade_id": r["trade_id"],
                            "corporate_action_id": r.get("corporate_action_id", ""),
                            "corporate_action_type": r.get("corporate_action_type", "SPLIT"),
                            "split_date": r["event_date"], "security_name": security_name,
                            "isin": isin, "canonical_instrument_id": canonical_instrument_id,
                            "historical_isin_aliases": historical_isin_aliases,
                            "old_isin": r.get("old_isin", isin), "new_isin": r.get("new_isin", isin),
                            "source_buy_trade_id": lot["source_trade_id"],
                            "quantity_before_split": before,
                            "added_quantity": lot["quantity_remaining"] - before,
                            "lot_quantity_after_split": lot["quantity_remaining"],
                            "split_ratio": declared_factor,
                            "cost_basis_remaining_after_split": lot["cost_basis_remaining"],
                            "user_basis_remaining_after_split": lot["user_basis_remaining"],
                            "source_row": r["source_row"],
                        })
                quantity_after = sum(lot["quantity_remaining"] for lot in lots)
                basis_after = sum(lot["cost_basis_remaining"] for lot in lots)
                user_basis_after = sum(lot["user_basis_remaining"] for lot in lots)
                action_id = str(r.get("corporate_action_id", ""))
                if action_id and not corporate_action_audit.empty:
                    mask = corporate_action_audit["action_id"].astype(str).eq(action_id)
                    corporate_action_audit.loc[mask, "quantity_before"] = quantity_before
                    corporate_action_audit.loc[mask, "quantity_after"] = quantity_after
                    corporate_action_audit.loc[mask, "destination_quantity"] = quantity_after
                    corporate_action_audit.loc[mask, "derived_or_declared_ratio"] = declared_factor
                    corporate_action_audit.loc[mask, "acquisition_basis_before_eur"] = basis_before
                    corporate_action_audit.loc[mask, "acquisition_basis_after_eur"] = basis_after
                    corporate_action_audit.loc[mask, "user_funded_basis_before_eur"] = user_basis_before
                    corporate_action_audit.loc[mask, "user_funded_basis_after_eur"] = user_basis_after
                    if action_status != "PASS":
                        corporate_action_audit.loc[mask, "validation_status"] = action_status
                        corporate_action_audit.loc[mask, "warning_error"] = warning_error
                    elif abs(basis_before - basis_after) > 0.005 or abs(user_basis_before - user_basis_after) > 0.005:
                        corporate_action_audit.loc[mask, "validation_status"] = "BLOCKING"
                        corporate_action_audit.loc[mask, "warning_error"] = "Basis conservation invariant failed."
            elif typ in {"SELL", WORTHLESS_DERECOGNITION}:
                is_derecognition = typ == WORTHLESS_DERECOGNITION
                if is_derecognition:
                    totals["derecognition_qty"] += qty_abs
                else:
                    totals["sell_qty"] += qty_abs
                    totals["sell_value"] += safe_float(r["gross_sell_value_eur"])
                sell_qty = qty_abs
                net_sell_value = 0.0 if is_derecognition else safe_float(r["net_sell_cash_inflow_eur"])
                if sell_qty <= 0:
                    continue
                proceeds_per_share = net_sell_value / sell_qty
                remaining_to_sell = sell_qty
                while remaining_to_sell > 1e-10 and lots:
                    lot = lots[0]
                    lot_qty = lot["quantity_remaining"]
                    matched_qty = min(remaining_to_sell, lot_qty)
                    frac = matched_qty / lot_qty if lot_qty else 0
                    allocated_cost = lot["cost_basis_remaining"] * frac
                    allocated_user_basis = lot["user_basis_remaining"] * frac
                    allocated_proceeds = proceeds_per_share * matched_qty
                    realized_rows.append({"sell_trade_id": r["trade_id"], "source_buy_trade_id": lot["source_trade_id"], "sell_date": r["event_date"], "buy_date": lot["buy_date"], "year": r["year"], "year_month": r["year_month"], "security_name": security_name, "isin": isin, "asset_class": asset_class, "quantity_sold": matched_qty, "allocated_net_sell_proceeds_eur": allocated_proceeds, "allocated_acquisition_cost_basis_eur": allocated_cost, "allocated_user_funded_basis_eur": allocated_user_basis, "realized_pl_acquisition_basis_eur": allocated_proceeds - allocated_cost, "realized_pl_user_basis_eur": allocated_proceeds - allocated_user_basis, "sell_price_eur": r["trade_price_eur"], "buy_price_eur": lot["buy_price"], "holding_days": (pd.Timestamp(r["event_date"]) - pd.Timestamp(lot["buy_date"])).days if pd.notna(r["event_date"]) and pd.notna(lot["buy_date"]) else np.nan, "source_row": r["source_row"], "close_type": typ, "is_discretionary_sale": not is_derecognition, "known_security_event_id": r.get("known_security_event_id", ""), "tax_treatment_status": r.get("tax_treatment_status", "")})
                    lot["quantity_remaining"] -= matched_qty
                    lot["cost_basis_remaining"] -= allocated_cost
                    lot["user_basis_remaining"] -= allocated_user_basis
                    remaining_to_sell -= matched_qty
                    if lot["quantity_remaining"] <= 1e-10:
                        lots.pop(0)
                if remaining_to_sell > 1e-8:
                    realized_rows.append({"sell_trade_id": r["trade_id"], "source_buy_trade_id": "UNMATCHED_DERECOGNITION" if is_derecognition else "UNMATCHED_SELL", "sell_date": r["event_date"], "buy_date": pd.NaT, "year": r["year"], "year_month": r["year_month"], "security_name": security_name, "isin": isin, "asset_class": asset_class, "quantity_sold": remaining_to_sell, "allocated_net_sell_proceeds_eur": proceeds_per_share * remaining_to_sell, "allocated_acquisition_cost_basis_eur": np.nan, "allocated_user_funded_basis_eur": np.nan, "realized_pl_acquisition_basis_eur": np.nan, "realized_pl_user_basis_eur": np.nan, "sell_price_eur": r["trade_price_eur"], "buy_price_eur": np.nan, "holding_days": np.nan, "source_row": r["source_row"], "close_type": typ, "is_discretionary_sale": not is_derecognition, "known_security_event_id": r.get("known_security_event_id", ""), "tax_treatment_status": r.get("tax_treatment_status", "")})
        for lot in lots:
            if lot["quantity_remaining"] > 1e-10:
                open_lots_rows.append({"security_name": security_name, "isin": isin, "canonical_instrument_id": canonical_instrument_id, "historical_isin_aliases": historical_isin_aliases, "asset_class": asset_class, "source_trade_id": lot["source_trade_id"], "buy_date": lot["buy_date"], "quantity_remaining": lot["quantity_remaining"], "cost_basis_remaining_eur": lot["cost_basis_remaining"], "user_basis_remaining_eur": lot["user_basis_remaining"], "buy_price_eur": lot["buy_price"], "funding_source": lot["funding_source"]})
        current_quantity = sum(lot["quantity_remaining"] for lot in lots)
        remaining_cost_basis = sum(lot["cost_basis_remaining"] for lot in lots)
        remaining_user_basis = sum(lot["user_basis_remaining"] for lot in lots)
        realized_for_isin = [rr for rr in realized_rows if rr["isin"] == isin]
        realized_pl_acq = sum(safe_float(rr["realized_pl_acquisition_basis_eur"]) for rr in realized_for_isin)
        realized_pl_user = sum(safe_float(rr["realized_pl_user_basis_eur"]) for rr in realized_for_isin)
        holding_rows.append({"security_name": security_name, "isin": isin, "current_isin": isin, "canonical_instrument_id": canonical_instrument_id, "historical_isin_aliases": historical_isin_aliases, "asset_class": asset_class, "position_status": "ACTIVE" if current_quantity > 1e-8 else "CLOSED", "closure_status": "DERECOGNIZED" if current_quantity <= 1e-8 and totals["derecognition_qty"] > 0 else ("CLOSED" if current_quantity <= 1e-8 else "OPEN"), "current_quantity": current_quantity, "total_buy_quantity": totals["buy_qty"], "total_sell_quantity": totals["sell_qty"], "total_derecognized_quantity": totals["derecognition_qty"], "total_split_quantity": totals["split_qty"], "total_gross_buy_value_eur": totals["buy_value"], "total_gross_sell_value_eur": totals["sell_value"], "total_fees_paid_eur": totals["fees"], "total_tax_paid_eur": totals["tax"], "matched_promo_credit_eur": totals["promo"], "total_acquisition_cost_basis_original_eur": totals["acq_orig"], "total_user_funded_cost_basis_original_eur": totals["user_orig"], "remaining_acquisition_cost_basis_eur": remaining_cost_basis, "remaining_user_funded_basis_eur": remaining_user_basis, "avg_cost_basis_per_share_eur": remaining_cost_basis / current_quantity if current_quantity > 1e-10 else np.nan, "avg_user_funded_basis_per_share_eur": remaining_user_basis / current_quantity if current_quantity > 1e-10 else np.nan, "fifo_realized_pl_acquisition_basis_eur": realized_pl_acq, "fifo_realized_pl_user_basis_eur": realized_pl_user, "trade_events": len(g), "buy_events": int((g["type_norm"] == "BUY").sum()), "dividend_reinvestment_events": int((g["type_norm"] == "DIVIDEND_REINVESTMENT").sum()), "sell_events": int((g["type_norm"] == "SELL").sum()), "derecognition_events": int((g["type_norm"] == WORTHLESS_DERECOGNITION).sum()), "split_events": int((g["type_norm"] == "SPLIT").sum()), "first_trade_date": g["event_date"].min(), "last_trade_date": g["event_date"].max()})
    holdings = pd.DataFrame(holding_rows)
    realized = pd.DataFrame(realized_rows)
    open_lots = pd.DataFrame(open_lots_rows)
    split_allocations = pd.DataFrame(split_allocation_rows)
    if not holdings.empty:
        holdings = holdings.sort_values(["position_status", "remaining_acquisition_cost_basis_eur"], ascending=[True, False], na_position="last").reset_index(drop=True)
    return holdings, realized, open_lots, split_allocations, corporate_action_audit


def build_income_holdings_combined(holdings, dividends, raw_df):
    if holdings.empty:
        holdings = pd.DataFrame(columns=["security_name", "isin", "asset_class", "position_status", "current_quantity", "remaining_acquisition_cost_basis_eur", "remaining_user_funded_basis_eur", "total_acquisition_cost_basis_original_eur", "total_user_funded_cost_basis_original_eur"])
    max_date = raw_df["event_date"].max()
    ttm_start = max_date - pd.DateOffset(months=12) if pd.notna(max_date) else pd.Timestamp.today() - pd.DateOffset(months=12)
    div_lifetime_columns = [
        "isin", "dividend_security_name", "total_gross_dividends",
        "total_dividend_tax_withheld", "total_dividend_tax_refund",
        "total_net_dividends", "dividend_events", "first_dividend_date",
        "last_dividend_date",
    ]
    div_lifetime = dividends.groupby("isin", dropna=False).agg(dividend_security_name=("security_name", "last"), total_gross_dividends=("gross_dividend_eur", complete_numeric_sum), total_dividend_tax_withheld=("dividend_tax_withheld_eur", complete_numeric_sum), total_dividend_tax_refund=("dividend_tax_refund_eur", "sum"), total_net_dividends=("net_dividend_eur", "sum"), dividend_events=("dividend_id", "count"), first_dividend_date=("payment_date", "min"), last_dividend_date=("payment_date", "max")).reset_index() if not dividends.empty else pd.DataFrame(columns=div_lifetime_columns)
    ttm_div = dividends[dividends["payment_date"] >= ttm_start].copy() if not dividends.empty else dividends.copy()
    div_ttm_columns = [
        "isin", "ttm_gross_dividends", "ttm_tax_withheld",
        "ttm_tax_refund", "ttm_net_dividends", "ttm_dividend_events",
    ]
    div_ttm = ttm_div.groupby("isin", dropna=False).agg(ttm_gross_dividends=("gross_dividend_eur", complete_numeric_sum), ttm_tax_withheld=("dividend_tax_withheld_eur", complete_numeric_sum), ttm_tax_refund=("dividend_tax_refund_eur", "sum"), ttm_net_dividends=("net_dividend_eur", "sum"), ttm_dividend_events=("dividend_id", "count")).reset_index() if not ttm_div.empty else pd.DataFrame(columns=div_ttm_columns)
    combined = holdings.merge(div_lifetime, on="isin", how="outer").merge(div_ttm, on="isin", how="left")
    combined["security_name"] = combined["security_name"].fillna(combined.get("dividend_security_name", "Unknown security"))
    combined["asset_class"] = combined["asset_class"].fillna("Unknown")
    for c in ["current_quantity", "total_buy_quantity", "total_sell_quantity", "total_split_quantity", "total_gross_buy_value_eur", "total_gross_sell_value_eur", "total_fees_paid_eur", "total_tax_paid_eur", "matched_promo_credit_eur", "total_acquisition_cost_basis_original_eur", "total_user_funded_cost_basis_original_eur", "remaining_acquisition_cost_basis_eur", "remaining_user_funded_basis_eur", "fifo_realized_pl_acquisition_basis_eur", "fifo_realized_pl_user_basis_eur", "total_dividend_tax_refund", "total_net_dividends", "dividend_events", "ttm_tax_refund", "ttm_net_dividends", "ttm_dividend_events"]:
        if c in combined.columns:
            combined[c] = combined[c].fillna(0)
    for value_column, count_column in [
        ("total_gross_dividends", "dividend_events"),
        ("total_dividend_tax_withheld", "dividend_events"),
        ("ttm_gross_dividends", "ttm_dividend_events"),
        ("ttm_tax_withheld", "ttm_dividend_events"),
    ]:
        if value_column in combined.columns:
            combined.loc[combined[count_column].fillna(0).eq(0), value_column] = 0.0
    combined["position_status"] = combined["position_status"].fillna("NO_TRADE_RECORD")
    combined["ttm_gross_yoc_acquisition_basis_pct"] = combined.apply(lambda r: safe_div(r["ttm_gross_dividends"], r["remaining_acquisition_cost_basis_eur"]) * 100, axis=1)
    combined["ttm_net_yoc_acquisition_basis_pct"] = combined.apply(lambda r: safe_div(r["ttm_net_dividends"], r["remaining_acquisition_cost_basis_eur"]) * 100, axis=1)
    combined["ttm_gross_yoc_user_funded_basis_pct"] = combined.apply(lambda r: safe_div(r["ttm_gross_dividends"], r["remaining_user_funded_basis_eur"]) * 100, axis=1)
    combined["ttm_net_yoc_user_funded_basis_pct"] = combined.apply(lambda r: safe_div(r["ttm_net_dividends"], r["remaining_user_funded_basis_eur"]) * 100, axis=1)
    combined["lifetime_net_dividend_recovery_acquisition_basis_pct"] = combined.apply(lambda r: safe_div(r["total_net_dividends"], r["total_acquisition_cost_basis_original_eur"]) * 100, axis=1)
    combined["dividend_tax_drag_pct"] = combined.apply(
        lambda r: safe_div(r["total_dividend_tax_withheld"], r["total_gross_dividends"]) * 100
        if pd.notna(r.get("total_dividend_tax_withheld")) and pd.notna(r.get("total_gross_dividends"))
        else np.nan,
        axis=1,
    )
    combined["yoc_warning"] = np.where(combined["ttm_net_yoc_acquisition_basis_pct"] > 25, "HIGH_YOC_CHECK_PARTIAL_SELL_OR_SMALL_BASIS", "")
    return combined.sort_values(["position_status", "remaining_acquisition_cost_basis_eur", "total_net_dividends"], ascending=[True, False, False]).reset_index(drop=True), ttm_start, max_date


def build_stock_fund_dividend_projection(combined, dividends, max_date, months=12):
    """
    Builds a conservative active-position dividend projection.

    Important interpretation:
    - This is NOT a true declared forward dividend forecast.
    - It is a TTM/seasonality proxy built only from dividends already observed in the CSV.
    - Closed/non-trade positions are excluded from the active projection.
    """
    holding_cols = [
        "security_name", "isin", "asset_class", "position_status", "current_quantity",
        "remaining_acquisition_cost_basis_eur", "remaining_user_funded_basis_eur",
        "live_current_value_eur", "yahoo_ticker", "ttm_gross_dividends", "ttm_net_dividends",
        "ttm_dividend_events", "total_net_dividends", "first_dividend_date", "last_dividend_date",
        "ttm_gross_yoc_acquisition_basis_pct", "ttm_net_yoc_acquisition_basis_pct",
        "ttm_gross_yoc_user_funded_basis_pct", "ttm_net_yoc_user_funded_basis_pct",
        "yoc_warning",
    ]

    if combined is None or combined.empty:
        empty_holding = pd.DataFrame(columns=holding_cols)
        empty_month = pd.DataFrame(columns=[
            "projection_month_index", "projection_year_month", "projected_gross_dividend_eur",
            "projected_net_dividend_eur", "historical_reference_year_month", "method",
            "active_dividend_rows_used"
        ])
        return empty_holding, empty_month

    active = combined[combined["position_status"].eq("ACTIVE")].copy()

    for c in holding_cols:
        if c not in active.columns:
            active[c] = np.nan if c not in {"security_name", "isin", "asset_class", "position_status", "yoc_warning", "yahoo_ticker"} else ""

    projection_by_holding = active[holding_cols].copy()
    projection_by_holding["forward_12m_gross_dividend_eur"] = projection_by_holding["ttm_gross_dividends"].fillna(0)
    projection_by_holding["forward_12m_net_dividend_eur"] = projection_by_holding["ttm_net_dividends"].fillna(0)
    projection_by_holding["estimated_monthly_net_dividend_eur"] = projection_by_holding["forward_12m_net_dividend_eur"] / 12.0

    projection_by_holding["forward_net_yoc_acquisition_basis_pct"] = np.where(
        projection_by_holding["remaining_acquisition_cost_basis_eur"].fillna(0).abs() > 1e-12,
        projection_by_holding["forward_12m_net_dividend_eur"] / projection_by_holding["remaining_acquisition_cost_basis_eur"] * 100.0,
        np.nan,
    )
    projection_by_holding["forward_net_yoc_user_funded_basis_pct"] = np.where(
        projection_by_holding["remaining_user_funded_basis_eur"].fillna(0).abs() > 1e-12,
        projection_by_holding["forward_12m_net_dividend_eur"] / projection_by_holding["remaining_user_funded_basis_eur"] * 100.0,
        np.nan,
    )
    projection_by_holding["forward_net_current_yield_pct"] = np.where(
        projection_by_holding["live_current_value_eur"].fillna(0).abs() > 1e-12,
        projection_by_holding["forward_12m_net_dividend_eur"] / projection_by_holding["live_current_value_eur"] * 100.0,
        np.nan,
    )
    projection_by_holding["projection_method"] = np.where(
        projection_by_holding["forward_12m_net_dividend_eur"].fillna(0) > 0,
        "active_position_ttm_proxy",
        "no_ttm_dividend_observed",
    )
    projection_by_holding["projection_warning"] = np.select(
        [
            projection_by_holding["position_status"].ne("ACTIVE"),
            projection_by_holding["forward_12m_net_dividend_eur"].fillna(0).le(0),
            projection_by_holding["yoc_warning"].fillna("").astype(str).ne(""),
        ],
        [
            "EXCLUDED_NOT_ACTIVE",
            "NO_TTM_DIVIDEND_FOR_ACTIVE_POSITION",
            "CHECK_YOC_DENOMINATOR_OR_PARTIAL_SELL",
        ],
        default="TTM_PROXY_NOT_DECLARED_FORWARD_DIVIDEND",
    )
    projection_by_holding = projection_by_holding.sort_values(
        ["forward_12m_net_dividend_eur", "remaining_acquisition_cost_basis_eur"],
        ascending=[False, False]
    ).reset_index(drop=True)

    active_isins = set(active["isin"].dropna().astype(str))
    if dividends is None or dividends.empty or pd.isna(max_date) or not active_isins:
        projection_by_month = pd.DataFrame(columns=[
            "projection_month_index", "projection_year_month", "projected_gross_dividend_eur",
            "projected_net_dividend_eur", "historical_reference_year_month", "method",
            "active_dividend_rows_used"
        ])
        return projection_by_holding, projection_by_month

    active_dividends = dividends[dividends["isin"].astype(str).isin(active_isins)].copy()
    max_ts = pd.Timestamp(max_date).normalize()
    month_rows = []

    for i in range(1, months + 1):
        future_month = (max_ts + pd.DateOffset(months=i)).to_period("M").to_timestamp()
        reference_month = (future_month - pd.DateOffset(years=1)).strftime("%Y-%m")
        rows = active_dividends[active_dividends["year_month"].astype(str).eq(reference_month)].copy()
        gross = safe_float(rows["gross_dividend_eur"].sum()) if not rows.empty else 0.0
        net = safe_float(rows["net_dividend_eur"].sum()) if not rows.empty else 0.0
        month_rows.append({
            "projection_month_index": i,
            "projection_year_month": future_month.strftime("%Y-%m"),
            "projected_gross_dividend_eur": gross,
            "projected_net_dividend_eur": net,
            "historical_reference_year_month": reference_month,
            "method": "same_month_prior_year_active_positions_only" if not rows.empty else "no_same_month_active_dividend_observed",
            "active_dividend_rows_used": len(rows),
        })

    projection_by_month = pd.DataFrame(month_rows)
    return projection_by_holding, projection_by_month


# ============================================================
# 4B. Growth-adjusted dividend projection
# ============================================================

def _empty_dividend_growth_history():
    return pd.DataFrame(columns=[
        "security_name", "isin", "yahoo_ticker", "dividend_year",
        "annual_dividend_per_share", "dividend_currency", "source", "error"
    ])


def _dedupe_keep_order(items):
    out = []
    seen = set()
    for x in items:
        x = str(x or "").strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out




def _native_dividend_ticker_from_openfigi(isin, selected_ticker=""):
    """
    Return at most one exact-ISIN home/primary-market Yahoo ticker.

    This is a fallback for dividend history only. It never performs a name
    search and never invents exchange suffixes unrelated to an exact
    OpenFIGI record.
    """
    isin = str(isin or "").strip().upper()
    selected_ticker = str(selected_ticker or "").strip().upper()

    identity = resolve_openfigi_identity(isin)
    if identity.get("status") != "OPENFIGI_EXACT_ISIN":
        return ""

    records = identity.get("records", []) or []
    country = isin[:2]

    home_exchanges = {
        "DE": OPENFIGI_GERMAN_EXCHANGES,
        "US": OPENFIGI_US_EXCHANGES,
        "NL": {"EURONEXT-AMSTER", "EURONEXT AMSTERDAM", "AMSTERDAM", "AS", "NA"},
        "FR": {"EURONEXT-PARIS", "EURONEXT PARIS", "PARIS", "FP"},
        "IT": {"EURONEXT-MILAN", "EURONEXT MILAN", "MILAN", "IM"},
        "ES": {"MADRID", "SM"},
        "AT": {"VIENNA", "AV"},
        "BE": {"EURONEXT-BRUSS", "EURONEXT BRUSSELS", "BRUSSELS", "BB"},
        "PT": {"EURONEXT-LISBON", "EURONEXT LISBON", "LISBON", "PL"},
        "GB": {"LONDON", "LSE", "LN"},
        "CH": {"SIX", "SIX SWISS", "SWITZERLAND", "SW"},
        "DK": {"NOMX COPENHAGEN", "COPENHAGEN", "DC"},
        "SE": {"NOMX STOCKHOLM", "STOCKHOLM", "SS"},
        "NO": {"OSLO", "NO"},
        "FI": {"NOMX HELSINKI", "HELSINKI", "FH"},
        "CA": {"TORONTO", "CANADA", "CN", "TSX VENTURE", "CV"},
        "AU": {"ASX", "AUSTRALIA", "AT"},
        "JP": {"TOKYO", "JAPAN", "JT"},
        "HK": {"HONG KONG", "HK"},
        "IL": {"TEL AVIV", "IT"},
    }

    preferred = home_exchanges.get(country, set())

    def rank_record(record):
        exchange = str(record.get("exchCode", "") or "").strip().upper()
        quote_type = _openfigi_quote_type(record)

        if quote_type not in YAHOO_ALLOWED_QUOTE_TYPES:
            return (9, exchange)

        if exchange in preferred:
            return (0, exchange)

        # Some non-US issuers have their liquid primary listing in the US.
        if exchange in OPENFIGI_US_EXCHANGES:
            return (1, exchange)

        # Prefer a real non-German home-market listing over another German
        # cross-listing when the country-specific exchange was not identified.
        if exchange not in OPENFIGI_GERMAN_EXCHANGES:
            return (2, exchange)

        return (3, exchange)

    for record in sorted(records, key=rank_record):
        if rank_record(record)[0] >= 9:
            continue

        for symbol in _openfigi_yahoo_symbols(record):
            symbol = str(symbol or "").strip().upper()
            if symbol and symbol != selected_ticker:
                return symbol

    return ""


def dividend_history_ticker_candidates(isin, yahoo_ticker="", security_name=""):
    """
    Return a deliberately tiny dividend-history candidate list.

    Order:
    1. The exact Yahoo ticker already selected by the live-price stage.
    2. One exact-ISIN home/primary-market ticker from OpenFIGI.

    No suffix spraying. No company-name search. No testing every exchange.
    """
    isin = str(isin or "").strip().upper()
    yahoo_ticker = str(yahoo_ticker or "").strip().upper()

    candidates = []

    if yahoo_ticker:
        candidates.append(yahoo_ticker)

    native_ticker = _native_dividend_ticker_from_openfigi(
        isin=isin,
        selected_ticker=yahoo_ticker,
    )
    if native_ticker:
        candidates.append(native_ticker)

    return _dedupe_keep_order(candidates)[:2]

def manual_latest_dps_override(isin):
    """Disabled in V5.8: future dividends are inferred automatically from dividend history."""
    return None



def _ticker_currency(yt):
    currency = ""
    try:
        fast = yt.fast_info
        currency = str(fast.get("currency", "") or "").upper()
    except Exception:
        pass
    if not currency:
        try:
            currency = str(yt.info.get("currency", "") or "").upper()
        except Exception:
            currency = ""
    return currency or "UNKNOWN"



def _currency_from_yahoo_symbol(ticker):
    """Infer the trading currency from a Yahoo symbol without another web call."""
    ticker = str(ticker or "").strip().upper()

    suffix_currency = {
        ".DE": "EUR", ".F": "EUR", ".SG": "EUR", ".HM": "EUR",
        ".MU": "EUR", ".DU": "EUR", ".BE": "EUR",
        ".AS": "EUR", ".PA": "EUR", ".MI": "EUR", ".MC": "EUR",
        ".VI": "EUR", ".BR": "EUR", ".LS": "EUR", ".HE": "EUR",
        ".L": "GBP", ".SW": "CHF", ".CO": "DKK", ".ST": "SEK",
        ".OL": "NOK", ".TO": "CAD", ".V": "CAD", ".AX": "AUD",
        ".T": "JPY", ".HK": "HKD", ".TA": "ILS",
    }

    for suffix, currency in suffix_currency.items():
        if ticker.endswith(suffix):
            return currency

    # Bare equity tickers produced by this resolver are US listings.
    if ticker and "." not in ticker:
        return "USD"

    return "UNKNOWN"


def _fetch_dividend_series_for_ticker(ticker):
    """
    Make one dividend-history request for one ticker.

    The older code queried dividends, get_dividends, actions, fast_info and
    info for every candidate. This version uses the documented get_dividends
    surface once and infers currency from the symbol where possible.
    """
    ticker = str(ticker or "").strip().upper()
    if not ticker:
        return pd.Series(dtype=float), "UNKNOWN"

    yt = yf.Ticker(ticker)

    try:
        divs = yt.get_dividends(period="max")
    except Exception:
        return pd.Series(dtype=float), _currency_from_yahoo_symbol(ticker)

    if divs is None or len(divs) == 0:
        return pd.Series(dtype=float), _currency_from_yahoo_symbol(ticker)

    divs = pd.Series(divs).dropna()
    divs = divs[divs > 0]

    try:
        divs.index = pd.to_datetime(divs.index, errors="coerce")
        divs = divs[~pd.isna(divs.index)]
        if getattr(divs.index, "tz", None) is not None:
            divs.index = divs.index.tz_convert(None)
    except Exception:
        return pd.Series(dtype=float), _currency_from_yahoo_symbol(ticker)

    currency = _currency_from_yahoo_symbol(ticker)
    if currency == "UNKNOWN":
        currency = _ticker_currency(yt)

    return divs, currency


def fetch_yahoo_annual_dividends(
    ticker,
    security_name="",
    isin="",
    lookback_years=DIVIDEND_GROWTH_LOOKBACK_YEARS,
):
    """
    Fetch annual dividend-per-share history with at most two ticker attempts.

    Attempt 1: the exact ticker already proven by the live-price stage.
    Attempt 2: one exact-ISIN home/primary-market ticker from OpenFIGI.

    The function stops immediately after the first valid history.
    """
    candidates = dividend_history_ticker_candidates(
        isin=isin,
        yahoo_ticker=ticker,
        security_name=security_name,
    )

    if not candidates:
        return _empty_dividend_growth_history()

    attempted_errors = []

    for cand in candidates:
        try:
            divs, currency = _fetch_dividend_series_for_ticker(cand)

            if divs is None or divs.empty:
                attempted_errors.append(f"{cand}: no dividends")
                continue

            min_year = (
                pd.Timestamp.today().year
                - int(lookback_years)
                - 2
            )

            divs = divs[divs.index.year >= min_year]

            if divs.empty:
                attempted_errors.append(
                    f"{cand}: no dividends inside lookback"
                )
                continue

            annual = (
                divs.groupby(divs.index.year)
                .sum()
                .reset_index()
            )
            annual.columns = [
                "dividend_year",
                "annual_dividend_per_share",
            ]
            annual = annual[
                annual["annual_dividend_per_share"] > 0
            ]

            if annual.empty:
                attempted_errors.append(
                    f"{cand}: empty annual history"
                )
                continue

            annual["security_name"] = security_name
            annual["isin"] = isin
            annual["yahoo_ticker"] = cand
            annual["dividend_currency"] = currency
            annual["source"] = (
                "yfinance_dividends_selected_then_native"
            )
            annual["error"] = ""

            return annual[[
                "security_name",
                "isin",
                "yahoo_ticker",
                "dividend_year",
                "annual_dividend_per_share",
                "dividend_currency",
                "source",
                "error",
            ]]

        except Exception as exc:
            attempted_errors.append(
                f"{cand}: {str(exc)[:120]}"
            )

    return pd.DataFrame([{
        "security_name": security_name,
        "isin": isin,
        "yahoo_ticker": ",".join(candidates),
        "dividend_year": np.nan,
        "annual_dividend_per_share": np.nan,
        "dividend_currency": "UNKNOWN",
        "source": "yfinance_dividends_selected_then_native",
        "error": " | ".join(attempted_errors)[:250],
    }])

def _window_cagr(annual_complete, years):
    if annual_complete is None or annual_complete.empty:
        return np.nan
    annual_complete = annual_complete.dropna(subset=["dividend_year", "annual_dividend_per_share"]).copy()
    annual_complete = annual_complete[annual_complete["annual_dividend_per_share"] > 0]
    if len(annual_complete) < 2:
        return np.nan
    latest_year = int(annual_complete["dividend_year"].max())
    latest = float(annual_complete.loc[annual_complete["dividend_year"].eq(latest_year), "annual_dividend_per_share"].iloc[-1])
    candidates = annual_complete[annual_complete["dividend_year"] <= latest_year - int(years)]
    if candidates.empty:
        return np.nan
    start_row = candidates.sort_values("dividend_year").iloc[-1]
    start_year = int(start_row["dividend_year"])
    start = float(start_row["annual_dividend_per_share"])
    elapsed = latest_year - start_year
    if start <= 0 or latest <= 0 or elapsed <= 0:
        return np.nan
    return (latest / start) ** (1.0 / elapsed) - 1.0


def dividend_growth_stats_from_history(annual_history):
    """Derive latest DPS and dividend-growth statistics from annual dividend history.

    V5.8 automatic latest-DPS rule:
    - A calendar year is normally considered incomplete until year-end.
    - However, many European stocks pay once or twice per year. If the current-year
      dividend sum is already at least 80% of the latest completed year, treat it as
      the latest paid annual DPS. This catches cases like Munich Re, Deutsche Telekom,
      and Novo Nordisk without manual overrides.
    - For quarterly payers, the current year will usually be below 80% until most of
      the year has passed, so the latest completed year remains the DPS base.

    This affects only analytical projections, not broker/accounting cash flows.
    """
    current_year = pd.Timestamp.today().year
    base = {
        "external_dividend_history_years": 0,
        "external_latest_complete_dividend_year": np.nan,
        "external_latest_annual_dps": np.nan,
        "external_dividend_ticker_used": "",
        "external_dividend_currency": "UNKNOWN",
        "growth_yoy_latest_pct": np.nan,
        "growth_cagr_3y_pct": np.nan,
        "growth_cagr_5y_pct": np.nan,
        "growth_cagr_10y_pct": np.nan,
        "growth_median_yoy_pct": np.nan,
        "growth_raw_blended_pct": 0.0,
        "growth_clipped_pct": 0.0,
        "growth_projection_confidence": "NO_EXTERNAL_HISTORY",
    }

    if annual_history is None or annual_history.empty:
        return base

    ah = annual_history.copy()
    ah = ah.dropna(subset=["dividend_year", "annual_dividend_per_share"])
    ah = ah[ah["annual_dividend_per_share"] > 0]
    if ah.empty:
        return base

    ah["dividend_year"] = ah["dividend_year"].astype(int)
    ah = ah.sort_values("dividend_year")

    completed = ah[ah["dividend_year"] < current_year].copy()
    current = ah[ah["dividend_year"] == current_year].copy()

    if completed.empty and current.empty:
        return base

    # Decide whether the current-year dividend sum is already a realistic annual DPS.
    # This avoids excluding annual European dividends paid in the current year while
    # avoiding half-year underestimation for quarterly US dividends.
    use_current_as_latest = False
    if not current.empty:
        current_dps = float(current["annual_dividend_per_share"].sum())
        if completed.empty:
            use_current_as_latest = True
        else:
            prev_latest_dps = float(completed.sort_values("dividend_year").iloc[-1]["annual_dividend_per_share"])
            if prev_latest_dps <= 0:
                use_current_as_latest = True
            elif current_dps >= 0.80 * prev_latest_dps:
                use_current_as_latest = True

    if use_current_as_latest:
        latest_year = current_year
        # Preserve source/ticker/currency from current-year row but use annual sum.
        latest_row = current.iloc[-1].copy()
        latest_row["annual_dividend_per_share"] = float(current["annual_dividend_per_share"].sum())
        growth_history = pd.concat([completed, pd.DataFrame([latest_row])], ignore_index=True).sort_values("dividend_year")
        latest_source_note = "AUTO_CURRENT_YEAR_SUBSTANTIALLY_COMPLETE"
    else:
        if completed.empty:
            return base
        latest_year = int(completed["dividend_year"].max())
        latest_row = completed.loc[completed["dividend_year"].eq(latest_year)].iloc[-1]
        growth_history = completed.sort_values("dividend_year")
        latest_source_note = "AUTO_LATEST_COMPLETED_YEAR"

    latest_dps = float(latest_row["annual_dividend_per_share"])
    base["external_dividend_history_years"] = int(growth_history["dividend_year"].nunique())
    base["external_latest_complete_dividend_year"] = latest_year
    base["external_latest_annual_dps"] = latest_dps
    base["external_dividend_ticker_used"] = str(latest_row.get("yahoo_ticker", "") or "")
    base["external_dividend_currency"] = str(latest_row.get("dividend_currency", "UNKNOWN") or "UNKNOWN").upper()

    yoy = growth_history.set_index("dividend_year")["annual_dividend_per_share"].pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    yoy_clean = yoy[(yoy > -0.75) & (yoy < 1.00)]
    if not yoy_clean.empty:
        base["growth_yoy_latest_pct"] = float(yoy_clean.iloc[-1] * 100.0)
        base["growth_median_yoy_pct"] = float(yoy_clean.median() * 100.0)

    for yrs, key in [(3, "growth_cagr_3y_pct"), (5, "growth_cagr_5y_pct"), (10, "growth_cagr_10y_pct")]:
        cagr = _window_cagr(growth_history, yrs)
        if np.isfinite(cagr) and -0.75 < cagr < 1.00:
            base[key] = float(cagr * 100.0)

    weighted_components = []
    for key, weight in [
        ("growth_yoy_latest_pct", 0.20),
        ("growth_cagr_3y_pct", 0.25),
        ("growth_cagr_5y_pct", 0.35),
        ("growth_cagr_10y_pct", 0.10),
        ("growth_median_yoy_pct", 0.10),
    ]:
        val = safe_float(base.get(key), np.nan)
        if np.isfinite(val):
            weighted_components.append((val / 100.0, weight))

    if weighted_components:
        total_w = sum(w for _, w in weighted_components)
        raw = sum(v * w for v, w in weighted_components) / total_w
        clipped = float(np.clip(raw, DIVIDEND_GROWTH_CLIP_LOW, DIVIDEND_GROWTH_CLIP_HIGH))
        base["growth_raw_blended_pct"] = raw * 100.0
        base["growth_clipped_pct"] = clipped * 100.0

    n = base["external_dividend_history_years"]
    if n >= 8 and np.isfinite(safe_float(base["growth_cagr_5y_pct"], np.nan)):
        base["growth_projection_confidence"] = "HIGH_HISTORY_BASED_" + latest_source_note
    elif n >= 4:
        base["growth_projection_confidence"] = "MEDIUM_HISTORY_BASED_" + latest_source_note
    elif n >= 2:
        base["growth_projection_confidence"] = "LOW_HISTORY_BASED_" + latest_source_note
    else:
        base["growth_projection_confidence"] = "INSUFFICIENT_HISTORY_TTM_FALLBACK"

    return base



def enrich_dividend_projection_with_external_growth(projection_by_holding, *, combined, trades, dividends, actions, asof):
    """External events/estimates only; actual accounting remains broker truth."""
    currencies = dict(zip(combined.get("yahoo_ticker", pd.Series(dtype=str)),
                          combined.get("live_price_currency", pd.Series(dtype=str))))
    provider = YahooDividendProvider(yf.Ticker, currencies)

    def dividend_fx(currency):
        # Preserve minor-unit denomination; never map UNKNOWN to EUR.
        if currency in {"GBp", "GBX"}:
            return get_fx_to_eur("GBP") / 100.0
        if currency in {"ZAc", "ZAC"}:
            return get_fx_to_eur("ZAR") / 100.0
        if currency in {"ILA", "ILa"}:
            return get_fx_to_eur("ILS") / 100.0
        return get_fx_to_eur(currency) if currency and currency != "UNKNOWN" else np.nan

    return build_dividend_analytics(
        projection_by_holding, combined, trades, dividends, actions, asof,
        provider=provider,
        candidate_resolver=lambda row: dividend_history_ticker_candidates(
            row.get("isin", ""), row.get("yahoo_ticker", ""), row.get("security_name", "")),
        fx_resolver=dividend_fx,
        growth_stats=dividend_growth_stats_from_history,
        enabled=ENABLE_DIVIDEND_GROWTH_PROJECTION,
    )


def apply_growth_to_monthly_projection(projection_by_month, projection_by_holding):
    monthly = projection_by_month.copy()
    if monthly.empty:
        return monthly
    base_net = safe_float(projection_by_holding.get("forward_12m_net_dividend_eur", pd.Series(dtype=float)).sum())
    growth_net = safe_float(projection_by_holding.get("growth_adjusted_forward_12m_net_dividend_eur", pd.Series(dtype=float)).sum())
    base_gross = safe_float(projection_by_holding.get("forward_12m_gross_dividend_eur", pd.Series(dtype=float)).sum())
    growth_gross = safe_float(projection_by_holding.get("growth_adjusted_forward_12m_gross_dividend_eur", pd.Series(dtype=float)).sum())
    net_factor = growth_net / base_net if base_net > 1e-12 else 1.0
    gross_factor = growth_gross / base_gross if base_gross > 1e-12 else 1.0
    monthly["growth_adjusted_projected_net_dividend_eur"] = monthly["projected_net_dividend_eur"].fillna(0) * net_factor
    monthly["growth_adjusted_projected_gross_dividend_eur"] = monthly["projected_gross_dividend_eur"].fillna(0) * gross_factor
    monthly["growth_adjustment_factor_net"] = net_factor
    monthly["growth_adjustment_factor_gross"] = gross_factor
    monthly["growth_adjusted_method"] = "seasonality_scaled_by_external_dividend_growth"
    return monthly


# ============================================================
# Actual observed dividend yield on cost
# Based only on shares that actually received the dividend
# ============================================================

def _fifo_lots_asof_stockfund_dividend(trades_for_isin, dividend_date, dividend_source_row):
    """
    Rebuild open FIFO lots for one stock/fund ISIN up to the dividend row.

    The dividend row's shares field is still the authority for how many
    shares actually received the dividend. This lot reconstruction is used
    only to estimate the cost basis of those entitled shares.
    """
    lots = []

    if trades_for_isin is None or trades_for_isin.empty or pd.isna(dividend_date):
        return lots

    g = trades_for_isin.copy()
    g["event_date"] = pd.to_datetime(g["event_date"], errors="coerce")
    d = pd.Timestamp(dividend_date).normalize()
    sr = safe_float(dividend_source_row, 10**12)

    eligible = g[
        (g["event_date"] < d)
        | (
            (g["event_date"] == d)
            & (g["source_row"].fillna(10**12) < sr)
        )
    ].sort_values(["event_date", "event_datetime", "source_row"])

    for _, r in eligible.iterrows():
        typ = str(r.get("type_norm", "")).upper().strip()
        qty_signed = safe_float(r.get("signed_quantity", r.get("quantity", 0)))
        qty_abs = abs(qty_signed)

        if typ in STOCK_ACQUISITION_TYPES:
            cost = safe_float(r.get("acquisition_cost_basis_eur", 0))
            if qty_abs > 1e-12 and cost >= 0:
                lots.append({
                    "quantity_remaining": qty_abs,
                    "cost_basis_remaining": cost,
                })

        elif typ == "SELL":
            remaining = qty_abs
            while remaining > 1e-10 and lots:
                lot = lots[0]
                lot_qty = safe_float(lot.get("quantity_remaining", 0))
                matched = min(remaining, lot_qty)
                frac = matched / lot_qty if lot_qty else 0

                lot["quantity_remaining"] -= matched
                lot["cost_basis_remaining"] -= lot["cost_basis_remaining"] * frac

                remaining -= matched

                if lot["quantity_remaining"] <= 1e-10:
                    lots.pop(0)

        elif typ == "SPLIT":
            split_extra_qty = qty_signed
            if split_extra_qty > 0 and lots:
                current_qty = sum(safe_float(lot.get("quantity_remaining", 0)) for lot in lots)
                if current_qty > 1e-12:
                    for lot in lots:
                        lot["quantity_remaining"] += split_extra_qty * lot["quantity_remaining"] / current_qty

    return lots


def _cost_basis_for_dividend_entitled_shares(lots, dividend_shares):
    """
    Take oldest open FIFO lots up to the number of shares in the dividend row.
    This avoids counting shares bought after the dividend payment.
    """
    if not lots or dividend_shares <= 1e-12:
        return np.nan, 0.0

    remaining = dividend_shares
    entitled_cost = 0.0
    matched_qty = 0.0

    for lot in lots:
        if remaining <= 1e-10:
            break

        lot_qty = safe_float(lot.get("quantity_remaining", 0))
        lot_cost = safe_float(lot.get("cost_basis_remaining", 0))

        if lot_qty <= 1e-12:
            continue

        take_qty = min(remaining, lot_qty)
        frac = take_qty / lot_qty

        entitled_cost += lot_cost * frac
        matched_qty += take_qty
        remaining -= take_qty

    return entitled_cost, matched_qty


def build_actual_observed_dividend_yoc_table(combined, dividends, trades, raw_df):
    """
    Income-tab metric:
    actual observed dividend yield on cost.

    This answers:
    'What dividend yield did I actually receive on the shares that were
    entitled to the dividend at that time?'

    It does NOT use today's current shares for past dividends.
    Later savings-plan purchases are only shown separately as next-cycle implied.
    """
    if combined is None or combined.empty or dividends is None or dividends.empty or trades is None or trades.empty:
        return pd.DataFrame()

    max_date = raw_df["event_date"].max() if "event_date" in raw_df.columns else pd.Timestamp.today()
    ttm_start = max_date - pd.DateOffset(months=12) if pd.notna(max_date) else pd.Timestamp.today() - pd.DateOffset(months=12)

    div = dividends.copy()
    div = div[
        div["isin"].notna()
        & div["isin"].astype(str).str.strip().ne("")
        & div["payment_date"].notna()
        & (div["shares"].fillna(0).abs() > 1e-12)
        & div["net_dividend_eur"].notna()
    ].copy()

    if div.empty:
        return pd.DataFrame()

    trades_by_isin = {
        str(isin).strip(): g.copy()
        for isin, g in trades.groupby("isin", dropna=False)
    }

    event_rows = []

    for _, r in div.sort_values(["payment_date", "source_row"]).iterrows():
        isin = str(r["isin"]).strip()
        payment_date = pd.Timestamp(r["payment_date"]).normalize()
        source_row = safe_float(r.get("source_row", 10**12), 10**12)

        dividend_shares = abs(safe_float(r.get("shares", 0)))
        gross_cash = pd.to_numeric(pd.Series([r.get("gross_dividend_eur")]), errors="coerce").iloc[0]
        net_cash = safe_float(r.get("net_dividend_eur", 0))
        tax_cash = pd.to_numeric(pd.Series([r.get("dividend_tax_withheld_eur")]), errors="coerce").iloc[0]

        if dividend_shares <= 1e-12:
            continue

        lots = _fifo_lots_asof_stockfund_dividend(
            trades_for_isin=trades_by_isin.get(isin, pd.DataFrame()),
            dividend_date=payment_date,
            dividend_source_row=source_row,
        )

        fifo_qty_available = sum(safe_float(lot.get("quantity_remaining", 0)) for lot in lots)

        entitled_cost_basis, matched_entitled_qty = _cost_basis_for_dividend_entitled_shares(
            lots=lots,
            dividend_shares=dividend_shares,
        )

        gross_dps = gross_cash / dividend_shares if dividend_shares > 1e-12 and pd.notna(gross_cash) else np.nan
        net_dps = net_cash / dividend_shares if dividend_shares > 1e-12 else np.nan

        avg_entitled_cost_per_share = (
            entitled_cost_basis / matched_entitled_qty
            if matched_entitled_qty > 1e-12 and np.isfinite(entitled_cost_basis)
            else np.nan
        )

        actual_gross_yoc_pct = (
            gross_cash / entitled_cost_basis * 100.0
            if pd.notna(gross_cash) and np.isfinite(entitled_cost_basis) and entitled_cost_basis > 1e-12
            else np.nan
        )

        actual_net_yoc_pct = (
            net_cash / entitled_cost_basis * 100.0
            if np.isfinite(entitled_cost_basis) and entitled_cost_basis > 1e-12
            else np.nan
        )

        event_rows.append({
            "isin": isin,
            "security_name": r.get("security_name", ""),
            "payment_date": payment_date,
            "source_row": r.get("source_row", np.nan),

            "dividend_shares_entitled": dividend_shares,
            "fifo_qty_available_asof_payment": fifo_qty_available,
            "matched_entitled_qty": matched_entitled_qty,
            "entitled_shares_cost_basis_eur": entitled_cost_basis,
            "avg_entitled_cost_per_share_eur": avg_entitled_cost_per_share,

            "gross_cash_received_eur": gross_cash,
            "net_cash_received_eur": net_cash,
            "tax_withheld_eur": tax_cash,
            "gross_dps_eur": gross_dps,
            "net_dps_eur": net_dps,

            "actual_gross_yoc_pct": actual_gross_yoc_pct,
            "actual_net_yoc_pct": actual_net_yoc_pct,
            "quantity_check_flag": (
                "DIVIDEND_SHARES_EXCEED_FIFO_QTY_CHECK"
                if dividend_shares > fifo_qty_available + 1e-6
                else ""
            ),
        })

    events = pd.DataFrame(event_rows)

    if events.empty:
        return pd.DataFrame()

    events["payment_date"] = pd.to_datetime(events["payment_date"], errors="coerce")
    ttm_events = events[events["payment_date"] >= ttm_start].copy()

    latest = (
        events.sort_values(["payment_date", "source_row"])
        .groupby("isin", as_index=False)
        .tail(1)
        [[
            "isin",
            "payment_date",
            "dividend_shares_entitled",
            "fifo_qty_available_asof_payment",
            "matched_entitled_qty",
            "entitled_shares_cost_basis_eur",
            "avg_entitled_cost_per_share_eur",
            "gross_cash_received_eur",
            "net_cash_received_eur",
            "tax_withheld_eur",
            "gross_dps_eur",
            "net_dps_eur",
            "actual_gross_yoc_pct",
            "actual_net_yoc_pct",
            "quantity_check_flag",
        ]]
        .rename(columns={
            "payment_date": "latest_dividend_date",
            "dividend_shares_entitled": "shares_that_received_latest_dividend",
            "fifo_qty_available_asof_payment": "fifo_qty_available_at_latest_dividend",
            "matched_entitled_qty": "matched_entitled_qty_latest_dividend",
            "entitled_shares_cost_basis_eur": "cost_basis_of_latest_entitled_shares_eur",
            "avg_entitled_cost_per_share_eur": "avg_cost_per_entitled_share_latest_dividend_eur",
            "gross_cash_received_eur": "latest_gross_cash_received_eur",
            "net_cash_received_eur": "latest_net_cash_received_eur",
            "tax_withheld_eur": "latest_tax_withheld_eur",
            "gross_dps_eur": "latest_gross_dps_eur",
            "net_dps_eur": "latest_net_dps_eur",
            "actual_gross_yoc_pct": "latest_actual_gross_yoc_pct",
            "actual_net_yoc_pct": "latest_actual_net_yoc_pct",
            "quantity_check_flag": "latest_quantity_check_flag",
        })
    )

    ttm = (
        ttm_events.groupby("isin", dropna=False)
        .agg(
            actual_ttm_dividend_events=("payment_date", "count"),
            actual_ttm_gross_cash_received_eur=("gross_cash_received_eur", complete_numeric_sum),
            actual_ttm_net_cash_received_eur=("net_cash_received_eur", "sum"),
            actual_ttm_tax_withheld_eur=("tax_withheld_eur", complete_numeric_sum),
            actual_ttm_gross_dps_eur=("gross_dps_eur", complete_numeric_sum),
            actual_ttm_net_dps_eur=("net_dps_eur", "sum"),
            actual_ttm_gross_yoc_on_entitled_shares_pct=("actual_gross_yoc_pct", complete_numeric_sum),
            actual_ttm_net_yoc_on_entitled_shares_pct=("actual_net_yoc_pct", "sum"),
            first_ttm_dividend_date=("payment_date", "min"),
            last_ttm_dividend_date=("payment_date", "max"),
        )
        .reset_index()
    )

    h = combined.copy()

    for c in [
        "isin",
        "security_name",
        "position_status",
        "current_quantity",
        "remaining_acquisition_cost_basis_eur",
        "remaining_user_funded_basis_eur",
        "avg_cost_basis_per_share_eur",
        "avg_user_funded_basis_per_share_eur",
        "live_price_eur",
        "live_current_value_eur",
    ]:
        if c not in h.columns:
            h[c] = np.nan

    h = h[h["isin"].notna() & h["isin"].astype(str).str.strip().ne("")].copy()

    out = h.merge(latest, on="isin", how="left")
    out = out.merge(ttm, on="isin", how="left")

    zero_cols = [
        "actual_ttm_dividend_events",
        "actual_ttm_net_cash_received_eur",
        "actual_ttm_net_dps_eur",
        "actual_ttm_net_yoc_on_entitled_shares_pct",
    ]

    for c in zero_cols:
        if c in out.columns:
            out[c] = out[c].fillna(0)
    for c in [
        "actual_ttm_gross_cash_received_eur", "actual_ttm_tax_withheld_eur",
        "actual_ttm_gross_dps_eur", "actual_ttm_gross_yoc_on_entitled_shares_pct",
    ]:
        if c in out.columns:
            out.loc[out["actual_ttm_dividend_events"].fillna(0).eq(0), c] = 0.0

    # Explicit next-cycle run-rate: useful, but NOT actual received income.
    out["next_cycle_implied_net_dividend_on_current_shares_eur"] = (
        out["current_quantity"].fillna(0)
        * out["actual_ttm_net_dps_eur"].fillna(0)
    )

    out["next_cycle_implied_net_yoc_on_current_basis_pct"] = np.where(
        out["remaining_acquisition_cost_basis_eur"].abs() > 1e-12,
        out["next_cycle_implied_net_dividend_on_current_shares_eur"]
        / out["remaining_acquisition_cost_basis_eur"] * 100.0,
        np.nan
    )

    out["actual_observed_dividend_yoc_note"] = np.select(
        [
            out["actual_ttm_dividend_events"].fillna(0).eq(0),
            out["actual_ttm_dividend_events"].fillna(0).eq(1),
            out["actual_ttm_dividend_events"].fillna(0).between(2, 3),
            out["actual_ttm_dividend_events"].fillna(0).ge(4),
        ],
        [
            "No TTM dividend observed in TR CSV",
            "Actual one-payment TTM yield; likely annual payer or incomplete history",
            "Actual two/three-payment TTM yield; likely semiannual or partial quarterly history",
            "Actual four-plus-payment TTM yield; likely quarterly/monthly payer",
        ],
        default="Check manually",
    )

    out = out.sort_values(
        [
            "actual_ttm_net_yoc_on_entitled_shares_pct",
            "actual_ttm_net_cash_received_eur",
        ],
        ascending=[False, False],
        na_position="last",
    ).reset_index(drop=True)

    return out

# ============================================================
# 5. Stock/fund Yahoo price enrichment
# ============================================================

def _isin_country_prefix(isin):
    isin = str(isin or "").strip().upper()
    return isin[:2] if len(isin) >= 2 else ""


def _ticker_suffix(symbol):
    symbol = str(symbol or "").strip().upper()
    if "." not in symbol:
        return ""
    return "." + symbol.rsplit(".", 1)[-1]


def _is_german_yahoo_symbol(symbol):
    symbol = str(symbol or "").strip().upper()
    return symbol.endswith(YAHOO_GERMANY_PRIORITY_SUFFIXES)


def _is_eur_native_yahoo_symbol(symbol):
    symbol = str(symbol or "").strip().upper()
    return symbol.endswith(YAHOO_EUR_NATIVE_PRIORITY_SUFFIXES)


def _is_probably_non_eur_symbol(symbol):
    symbol = str(symbol or "").strip().upper()
    return symbol.endswith(YAHOO_NON_EUR_SUFFIXES)


def _security_name_tokens(security_name):
    sec = str(security_name or "").lower()
    raw = re.split(r"[^a-zA-Z0-9]+", sec)
    return [
        w for w in raw
        if len(w) >= 4 and w not in YAHOO_GENERIC_STOPWORDS
    ]


def _candidate_name_text(candidate):
    return " ".join([
        str(candidate.get("shortname", "") or ""),
        str(candidate.get("longname", "") or ""),
        str(candidate.get("symbol", "") or ""),
    ]).lower()


def yahoo_search(query, max_results=AUTO_TICKER_SEARCH_MAX_RESULTS):
    query = str(query or "").strip()
    if not query:
        return []

    url = (
        "https://query1.finance.yahoo.com/v1/finance/search"
        f"?q={quote(query)}&quotesCount={int(max_results)}&newsCount=0"
    )

    try:
        r = requests.get(
            url,
            headers={"User-Agent": REQUEST_HEADERS["User-Agent"]},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        return r.json().get("quotes", []) or []
    except Exception:
        return []


def _append_candidate(by_symbol, candidate, source_query):
    symbol = str(candidate.get("symbol", "") or "").strip()
    if not symbol:
        return

    if symbol not in by_symbol:
        c = dict(candidate)
        c["_source_queries"] = [source_query]
        by_symbol[symbol] = c
    else:
        by_symbol[symbol].setdefault("_source_queries", [])
        by_symbol[symbol]["_source_queries"].append(source_query)


def _add_symbol_guess(by_symbol, symbol, source_query):
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        return

    if symbol not in by_symbol:
        by_symbol[symbol] = {
            "symbol": symbol,
            "shortname": "",
            "longname": "",
            "exchange": "",
            "currency": "",
            "quoteType": "",
            "_source_queries": [source_query],
            "_generated_guess": True,
        }

# ============================================================
# EXACT-ISIN / OPENFIGI IDENTITY-RESOLUTION HELPERS
# ============================================================

def is_valid_isin(isin):
    """Validate the 12-character ISIN structure and Luhn checksum."""
    isin = str(isin or "").strip().upper()

    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin):
        return False

    expanded = "".join(
        str(int(character, 36)) if character.isalpha() else character
        for character in isin
    )

    total = 0
    for position, character in enumerate(reversed(expanded)):
        number = int(character)
        if position % 2 == 1:
            number *= 2
        total += (number // 10) + (number % 10)

    return total % 10 == 0


OPENFIGI_GERMAN_EXCHANGES = {
    "ALL GERMAN SE",
    "BERLIN",
    "DUSSELDORF",
    "DÜSSELDORF",
    "EUWAX STUTTGART",
    "FRANKFURT",
    "GETTEX",
    "HAMBURG",
    "HANNOVER",
    "MUNICH",
    "QUOTRIX",
    "STUTTGART",
    "TRADEGATE",
    "XETRA",
    "DE", "GB", "GD", "GF", "GH", "GM", "GR", "GS", "GY",
}

OPENFIGI_US_EXCHANGES = {
    "US",
    "NASDAQ",
    "NASDAQ/NCM",
    "NASDAQ/NGM",
    "NASDAQ/NGS",
    "NEW YORK",
    "NYSE",
    "NYSE AMERICAN",
    "NYSE ARCA",
    "OTC US",
    "PINK SHEETS",
    "UN", "UA", "UC", "UF", "UM", "UP", "UQ", "UR", "US", "UW",
}

OPENFIGI_EXCHANGE_TO_YAHOO_SUFFIX = {
    # Germany
    "XETRA": ".DE",
    "GY": ".DE",
    "DE": ".DE",
    "FRANKFURT": ".F",
    "GF": ".F",
    "GR": ".F",
    "STUTTGART": ".SG",
    "EUWAX STUTTGART": ".SG",
    "GS": ".SG",
    "MUNICH": ".MU",
    "GM": ".MU",
    "HAMBURG": ".HM",
    "GH": ".HM",
    "DUSSELDORF": ".DU",
    "DÜSSELDORF": ".DU",
    "GD": ".DU",
    "BERLIN": ".BE",
    "GB": ".BE",

    # EUR-native exchanges
    "EURONEXT-AMSTER": ".AS",
    "EURONEXT AMSTERDAM": ".AS",
    "AMSTERDAM": ".AS",
    "AS": ".AS",
    "NA": ".AS",
    "EURONEXT-PARIS": ".PA",
    "EURONEXT PARIS": ".PA",
    "PARIS": ".PA",
    "FP": ".PA",
    "EURONEXT-MILAN": ".MI",
    "EURONEXT MILAN": ".MI",
    "MILAN": ".MI",
    "IM": ".MI",
    "MADRID": ".MC",
    "SM": ".MC",
    "VIENNA": ".VI",
    "AV": ".VI",
    "EURONEXT-BRUSS": ".BR",
    "EURONEXT BRUSSELS": ".BR",
    "BRUSSELS": ".BR",
    "BB": ".BR",
    "EURONEXT-LISBON": ".LS",
    "EURONEXT LISBON": ".LS",
    "LISBON": ".LS",
    "PL": ".LS",

    # Other major native exchanges
    "LONDON": ".L",
    "LSE": ".L",
    "LN": ".L",
    "SIX": ".SW",
    "SIX SWISS": ".SW",
    "SWITZERLAND": ".SW",
    "SW": ".SW",
    "NOMX COPENHAGEN": ".CO",
    "COPENHAGEN": ".CO",
    "DC": ".CO",
    "NOMX STOCKHOLM": ".ST",
    "STOCKHOLM": ".ST",
    "SS": ".ST",
    "OSLO": ".OL",
    "NO": ".OL",
    "NOMX HELSINKI": ".HE",
    "HELSINKI": ".HE",
    "FH": ".HE",
    "TORONTO": ".TO",
    "CANADA": ".TO",
    "CN": ".TO",
    "TSX VENTURE": ".V",
    "CV": ".V",
    "ASX": ".AX",
    "AUSTRALIA": ".AX",
    "AT": ".AX",
    "TOKYO": ".T",
    "JAPAN": ".T",
    "JT": ".T",
    "HONG KONG": ".HK",
    "HK": ".HK",
    "TEL AVIV": ".TA",
    "IT": ".TA",
}


def _openfigi_headers():
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": REQUEST_HEADERS.get("User-Agent", "Mozilla/5.0"),
    }

    api_key = str(OPENFIGI_API_KEY or "").strip()
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key

    return headers


def _openfigi_normalize_record(record):
    record = dict(record or {})

    ticker = str(record.get("ticker", "") or "").strip().upper()
    ticker = ticker.replace("/", "-")

    return {
        "figi": str(record.get("figi", "") or "").strip(),
        "compositeFIGI": str(record.get("compositeFIGI", "") or "").strip(),
        "shareClassFIGI": str(record.get("shareClassFIGI", "") or "").strip(),
        "ticker": ticker,
        "name": str(record.get("name", "") or "").strip(),
        "exchCode": str(record.get("exchCode", "") or "").strip().upper(),
        "securityType": str(record.get("securityType", "") or "").strip(),
        "securityType2": str(record.get("securityType2", "") or "").strip(),
        "marketSector": str(record.get("marketSector", "") or "").strip(),
        "securityDescription": str(
            record.get("securityDescription", "") or ""
        ).strip(),
    }


def _openfigi_quote_type(record):
    type_text = " ".join(
        [
            str(record.get("securityType", "") or ""),
            str(record.get("securityType2", "") or ""),
            str(record.get("marketSector", "") or ""),
            str(record.get("securityDescription", "") or ""),
        ]
    ).upper()

    if any(term in type_text for term in {
        "ETF",
        "ETP",
        "EXCHANGE TRADED FUND",
        "EXCHANGE-TRADED FUND",
        "EXCHANGE TRADED PRODUCT",
    }):
        return "ETF"

    if any(term in type_text for term in {
        "MUTUAL FUND",
        "OPEN-END FUND",
        "OPEN END FUND",
        "INVESTMENT FUND",
        "FUND",
    }):
        return "MUTUALFUND"

    if any(term in type_text for term in {
        "COMMON STOCK",
        "ORDINARY SHARE",
        "DEPOSITARY RECEIPT",
        "EQUITY",
        "REIT",
    }):
        return "EQUITY"

    return ""


def _openfigi_record_is_supported(record):
    ticker = str(record.get("ticker", "") or "").strip().upper()

    if not ticker:
        return False

    # Normal shares/funds have compact exchange symbols. This rejects option
    # descriptions and other long instrument strings.
    if not re.fullmatch(r"[A-Z0-9^=._\-]{1,40}", ticker):
        return False

    type_text = " ".join(
        [
            str(record.get("securityType", "") or ""),
            str(record.get("securityType2", "") or ""),
            str(record.get("marketSector", "") or ""),
            str(record.get("securityDescription", "") or ""),
        ]
    ).lower()

    forbidden_terms = {
        "option",
        "warrant",
        "future",
        "swap",
        "certificate",
        "bond",
        "note",
        "mortgage",
        "preferred security",
        "right",
    }

    if any(term in type_text for term in forbidden_terms):
        return False

    return _openfigi_quote_type(record) in YAHOO_ALLOWED_QUOTE_TYPES


def _openfigi_result_to_identity(isin, result_item):
    if not isinstance(result_item, dict):
        return {
            "status": "OPENFIGI_ERROR",
            "name": "",
            "records": [],
            "url": OPENFIGI_API_URL,
            "error": "OpenFIGI returned a non-object result",
        }

    if result_item.get("warning"):
        return {
            "status": "OPENFIGI_NOT_FOUND",
            "name": "",
            "records": [],
            "url": OPENFIGI_API_URL,
            "error": str(result_item.get("warning", "")),
        }

    if result_item.get("error"):
        return {
            "status": "OPENFIGI_ERROR",
            "name": "",
            "records": [],
            "url": OPENFIGI_API_URL,
            "error": str(result_item.get("error", "")),
        }

    records = []
    seen = set()

    for raw_record in result_item.get("data", []) or []:
        record = _openfigi_normalize_record(raw_record)

        if not _openfigi_record_is_supported(record):
            continue

        key = (
            record.get("ticker", ""),
            record.get("exchCode", ""),
            record.get("figi", ""),
        )

        if key in seen:
            continue

        seen.add(key)
        records.append(record)

    if not records:
        return {
            "status": "OPENFIGI_NO_SUPPORTED_RECORDS",
            "name": "",
            "records": [],
            "url": OPENFIGI_API_URL,
            "error": (
                "Exact ISIN mapping returned no supported stock/fund records"
            ),
        }

    records.sort(
        key=lambda record: (
            0
            if record.get("exchCode", "") in OPENFIGI_GERMAN_EXCHANGES
            else 1
            if record.get("exchCode", "")
            in OPENFIGI_EXCHANGE_TO_YAHOO_SUFFIX
            else 2
            if record.get("exchCode", "") in OPENFIGI_US_EXCHANGES
            else 3,
            record.get("exchCode", ""),
            record.get("ticker", ""),
        )
    )

    name = next(
        (
            str(record.get("name", "") or "").strip()
            for record in records
            if str(record.get("name", "") or "").strip()
        ),
        "",
    )

    return {
        "status": "OPENFIGI_EXACT_ISIN",
        "name": name,
        "records": records,
        "url": OPENFIGI_API_URL,
        "error": "",
    }


def prefetch_openfigi_identities(isins):
    """
    Batch exact-ISIN mapping through OpenFIGI v3.

    The public tier is intentionally batched at five jobs per request because
    that is the stricter limit currently documented for the mapping endpoint.
    """
    unique_isins = _dedupe_keep_order(
        str(isin or "").strip().upper()
        for isin in isins
    )

    pending = []

    for isin in unique_isins:
        if isin in ISIN_IDENTITY_CACHE:
            continue

        if not OPENFIGI_ISIN_RESOLUTION_ENABLED:
            ISIN_IDENTITY_CACHE[isin] = {
                "status": "OPENFIGI_DISABLED",
                "name": "",
                "records": [],
                "url": OPENFIGI_API_URL,
                "error": "",
            }
            continue

        if not is_valid_isin(isin):
            ISIN_IDENTITY_CACHE[isin] = {
                "status": "INVALID_ISIN",
                "name": "",
                "records": [],
                "url": OPENFIGI_API_URL,
                "error": "ISIN format or checksum validation failed",
            }
            continue

        pending.append(isin)

    if not pending:
        return

    batch_size = (
        OPENFIGI_BATCH_SIZE_WITH_KEY
        if str(OPENFIGI_API_KEY or "").strip()
        else OPENFIGI_BATCH_SIZE_WITHOUT_KEY
    )

    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        jobs = [
            {
                "idType": "ID_ISIN",
                "idValue": isin,
            }
            for isin in batch
        ]

        last_error = ""

        for attempt in range(1, OPENFIGI_MAX_RETRIES + 1):
            try:
                response = requests.post(
                    OPENFIGI_API_URL,
                    json=jobs,
                    headers=_openfigi_headers(),
                    timeout=OPENFIGI_TIMEOUT_SECONDS,
                )

                if response.status_code == 429:
                    reset_seconds = safe_float(
                        response.headers.get("ratelimit-reset", 3),
                        3.0,
                    )
                    wait_seconds = max(
                        1.0,
                        min(reset_seconds, 20.0),
                    )
                    time.sleep(wait_seconds)
                    last_error = "HTTP 429 rate limit"
                    continue

                if response.status_code in {500, 502, 503, 504}:
                    last_error = f"HTTP {response.status_code}"
                    time.sleep(min(2 ** attempt, 10))
                    continue

                response.raise_for_status()
                payload = response.json()

                if not isinstance(payload, list):
                    raise ValueError(
                        "OpenFIGI mapping response was not a list"
                    )

                if len(payload) != len(batch):
                    raise ValueError(
                        "OpenFIGI response length did not match request length"
                    )

                for isin, result_item in zip(batch, payload):
                    ISIN_IDENTITY_CACHE[isin] = (
                        _openfigi_result_to_identity(
                            isin,
                            result_item,
                        )
                    )

                last_error = ""
                break

            except Exception as exception:
                last_error = str(exception)
                if attempt < OPENFIGI_MAX_RETRIES:
                    time.sleep(min(2 ** attempt, 10))

        if last_error:
            for isin in batch:
                ISIN_IDENTITY_CACHE[isin] = {
                    "status": "OPENFIGI_ERROR",
                    "name": "",
                    "records": [],
                    "url": OPENFIGI_API_URL,
                    "error": last_error,
                }

        time.sleep(OPENFIGI_REQUEST_SLEEP_SECONDS)


def resolve_openfigi_identity(isin):
    isin = str(isin or "").strip().upper()

    if isin not in ISIN_IDENTITY_CACHE:
        prefetch_openfigi_identities([isin])

    return dict(
        ISIN_IDENTITY_CACHE.get(
            isin,
            {
                "status": "OPENFIGI_ERROR",
                "name": "",
                "records": [],
                "url": OPENFIGI_API_URL,
                "error": "Identity cache was not populated",
            },
        )
    )


def _openfigi_yahoo_symbols(record):
    ticker = str(record.get("ticker", "") or "").strip().upper()
    exchange = str(record.get("exchCode", "") or "").strip().upper()

    if not ticker:
        return []

    # Already looks like a Yahoo-formatted symbol.
    if "." in ticker or ticker.endswith("=X"):
        return [ticker]

    symbols = []

    if exchange in OPENFIGI_GERMAN_EXCHANGES:
        preferred_suffix = OPENFIGI_EXCHANGE_TO_YAHOO_SUFFIX.get(exchange)
        if preferred_suffix:
            symbols.append(ticker + preferred_suffix)

        for suffix in YAHOO_GERMANY_PRIORITY_SUFFIXES:
            symbols.append(ticker + suffix)

    elif exchange in OPENFIGI_EXCHANGE_TO_YAHOO_SUFFIX:
        symbols.append(
            ticker + OPENFIGI_EXCHANGE_TO_YAHOO_SUFFIX[exchange]
        )

    elif exchange in OPENFIGI_US_EXCHANGES:
        symbols.append(ticker)

    else:
        # Unknown exchange code: fail closed. A bare ticker can represent a
        # different company on another market.
        return []

    return _dedupe_keep_order(symbols)


def _exact_candidate(
    by_symbol,
    candidate,
    identity_source,
    identity_url="",
):
    symbol = str(
        candidate.get("symbol", "") or ""
    ).strip().upper()

    if not symbol:
        return

    if symbol not in by_symbol:
        by_symbol[symbol] = dict(candidate)
        by_symbol[symbol]["symbol"] = symbol

    by_symbol[symbol]["_identity_source"] = identity_source
    by_symbol[symbol]["_identity_url"] = identity_url


def _strict_name_match(candidate, security_name, identity_name=""):
    candidate_text = _candidate_name_text(candidate)
    reference_tokens = _dedupe_keep_order(
        _security_name_tokens(identity_name)
        + _security_name_tokens(security_name)
    )

    if not candidate_text or not reference_tokens:
        return False

    matched = sum(
        token in candidate_text
        for token in reference_tokens
    )

    if len(reference_tokens) <= 2:
        required = 1
    else:
        required = max(
            2,
            (len(reference_tokens) + 1) // 2,
        )

    return matched >= required


def _candidate_asset_type_is_compatible(candidate, asset_class):
    quote_type = str(
        candidate.get("quoteType", "") or ""
    ).strip().upper()

    asset_class = str(asset_class or "").strip().upper()

    if not quote_type:
        return True

    if asset_class == "STOCK":
        return quote_type == "EQUITY"

    if asset_class == "FUND":
        return quote_type in {
            "ETF",
            "MUTUALFUND",
        }

    return quote_type in YAHOO_ALLOWED_QUOTE_TYPES


def yahoo_candidate_pool(isin, security_name, asset_class=""):
    """
    Build Yahoo candidates only after OpenFIGI confirms the exact ISIN.

    Candidate sources:
    1. Deterministic Yahoo symbols generated from exact OpenFIGI ticker and
       exchange records.
    2. Yahoo's exact-ISIN search, but only when the returned symbol's base
       ticker exists in the exact OpenFIGI records.

    No company-name search is used. No candidate survives when OpenFIGI fails.
    """
    isin = str(isin or "").strip().upper()
    security_name = str(security_name or "").strip()
    asset_class = str(asset_class or "").strip().upper()

    identity = resolve_openfigi_identity(isin)
    by_symbol = {}

    if identity.get("status") != "OPENFIGI_EXACT_ISIN":
        return []

    identity_name = str(identity.get("name", "") or "").strip()
    identity_url = str(identity.get("url", "") or "").strip()
    exact_records = identity.get("records", []) or []

    allowed_base_tickers = {
        str(record.get("ticker", "") or "").strip().upper()
        for record in exact_records
        if str(record.get("ticker", "") or "").strip()
    }

    for record in exact_records:
        quote_type = _openfigi_quote_type(record)

        if asset_class == "STOCK" and quote_type != "EQUITY":
            continue

        if asset_class == "FUND" and quote_type not in {
            "ETF",
            "MUTUALFUND",
        }:
            continue

        record_name = str(
            record.get("name", "") or identity_name
        ).strip()

        exchange = str(
            record.get("exchCode", "") or ""
        ).strip().upper()

        for symbol in _openfigi_yahoo_symbols(record):
            currency_hint = (
                "EUR"
                if _is_german_yahoo_symbol(symbol)
                or _is_eur_native_yahoo_symbol(symbol)
                else ""
            )

            _exact_candidate(
                by_symbol,
                {
                    "symbol": symbol,
                    "shortname": record_name,
                    "longname": record_name,
                    "exchange": exchange,
                    "currency": currency_hint,
                    "quoteType": quote_type,
                },
                identity_source="OPENFIGI_EXACT_ISIN",
                identity_url=identity_url,
            )

    if YAHOO_EXACT_ISIN_FALLBACK_ENABLED:
        for candidate in yahoo_search(
            isin,
            AUTO_TICKER_SEARCH_MAX_RESULTS,
        ):
            symbol = str(
                candidate.get("symbol", "") or ""
            ).strip().upper()

            if not symbol:
                continue

            # Yahoo suffixes identify the venue. The base ticker must already
            # exist in OpenFIGI's exact-ISIN records.
            symbol_base = symbol.split(".", 1)[0]

            if symbol_base not in allowed_base_tickers:
                continue

            if not _candidate_asset_type_is_compatible(
                candidate,
                asset_class,
            ):
                continue

            candidate_text = _candidate_name_text(candidate)

            if any(
                re.search(
                    rf"\b{re.escape(term)}\b",
                    candidate_text,
                )
                for term in YAHOO_BAD_SECURITY_TERMS
            ):
                continue

            if not _strict_name_match(
                candidate,
                security_name,
                identity_name,
            ):
                continue

            _exact_candidate(
                by_symbol,
                candidate,
                identity_source=(
                    "OPENFIGI_EXACT_ISIN_PLUS_"
                    "YAHOO_EXACT_ISIN_TICKER_MATCH"
                ),
                identity_url=identity_url,
            )

    candidates = list(by_symbol.values())

    def candidate_priority(candidate):
        symbol = str(
            candidate.get("symbol", "") or ""
        ).strip().upper()

        if _is_german_yahoo_symbol(symbol):
            suffix_rank = next(
                (
                    index
                    for index, suffix
                    in enumerate(YAHOO_GERMANY_PRIORITY_SUFFIXES)
                    if symbol.endswith(suffix)
                ),
                len(YAHOO_GERMANY_PRIORITY_SUFFIXES),
            )
            return (0, suffix_rank, symbol)

        if (
            str(
                candidate.get("currency", "") or ""
            ).upper() == "EUR"
            or _is_eur_native_yahoo_symbol(symbol)
        ):
            suffix_rank = next(
                (
                    index
                    for index, suffix
                    in enumerate(YAHOO_EUR_NATIVE_PRIORITY_SUFFIXES)
                    if symbol.endswith(suffix)
                ),
                len(YAHOO_EUR_NATIVE_PRIORITY_SUFFIXES),
            )
            return (1, suffix_rank, symbol)

        return (2, 0, symbol)

    candidates.sort(key=candidate_priority)

    return candidates

def score_yahoo_candidate(candidate, security_name="", asset_class=""):
    """
    Score Yahoo search candidates for a Trade Republic Germany user.

    Highest priority:
    - Correct security type.
    - EUR listing.
    - German Yahoo listing / exchange.
    - Name similarity.
    - Avoid derivatives/certificates.
    """
    symbol = str(candidate.get("symbol", "") or "").strip()
    symbol_u = symbol.upper()

    exchange = str(candidate.get("exchange", "") or "").upper().strip()
    exch_disp = str(candidate.get("exchDisp", "") or "").upper().strip()
    quote_type = str(candidate.get("quoteType", "") or "").upper().strip()
    currency = str(candidate.get("currency", "") or "").upper().strip()
    asset_class = str(asset_class or "").upper().strip()

    combined_name = _candidate_name_text(candidate)
    sec_tokens = _security_name_tokens(security_name)

    score = 0

    # Security type.
    if quote_type in YAHOO_ALLOWED_QUOTE_TYPES:
        score += 250
    elif quote_type:
        score -= 500

    if asset_class == "FUND" and quote_type in {"ETF", "MUTUALFUND"}:
        score += 220

    if asset_class == "STOCK" and quote_type == "EQUITY":
        score += 180

    # German/EUR listing preference.
    if symbol_u.endswith(".DE"):
        score += 700
    elif symbol_u.endswith(".F"):
        score += 650
    elif symbol_u.endswith((".SG", ".HM", ".MU", ".DU", ".BE")):
        score += 480

    if exchange in GERMAN_EXCHANGES_YAHOO or exch_disp in GERMAN_EXCHANGES_YAHOO:
        score += 550

    if currency == "EUR":
        score += 350

    # EUR-native fallback listings.
    if _is_eur_native_yahoo_symbol(symbol_u):
        score += 230

    if exchange in EUR_NATIVE_EXCHANGES_YAHOO or exch_disp in EUR_NATIVE_EXCHANGES_YAHOO:
        score += 150

    # Penalize clearly non-EUR primary listings, but do not reject them.
    # They are useful fallback candidates when no German/EUR ticker works.
    if currency in {"USD", "JPY", "DKK", "CHF", "GBP", "GBX", "GBp", "CAD", "AUD", "HKD"}:
        score -= 120

    if _is_probably_non_eur_symbol(symbol_u):
        score -= 80

    if exchange in {"NMS", "NYQ", "ASE", "NAS", "PCX", "PNK", "OTC"}:
        score -= 160

    # Avoid warrants/certificates in stock/fund lookup.
    if any(t in combined_name for t in YAHOO_BAD_SECURITY_TERMS):
        score -= 1500

    # Name match.
    if sec_tokens:
        matched = sum(1 for w in sec_tokens if w in combined_name)
        score += min(matched * 45, 270)

    # Generated suffix guesses are plausible but less reliable than Yahoo search hits.
    if candidate.get("_generated_guess", False):
        score -= 60

    return score


def candidate_has_price(symbol):
    try:
        hist = yf.Ticker(symbol).history(period="10d", auto_adjust=False)
        if hist.empty:
            return False

        price_col = "Adj Close" if "Adj Close" in hist.columns else "Close"
        return not hist[price_col].dropna().empty
    except Exception:
        return False


def _ticker_selection_status(symbol, score, selected_has_price):
    symbol_u = str(symbol or "").strip().upper()

    if not selected_has_price:
        return "NO_PRICE_VALIDATED"

    if _is_german_yahoo_symbol(symbol_u):
        return "AUTO_GERMANY_EUR"

    if _is_eur_native_yahoo_symbol(symbol_u):
        return "AUTO_EUR_NATIVE"

    if score >= 250:
        return "AUTO_NON_EUR_FX_CONVERTED"

    return "LOW_CONFIDENCE"


def find_best_yahoo_ticker(
    isin,
    security_name,
    asset_class="",
):
    """
    Select the first identity-controlled Yahoo candidate that returns a price.

    The function fails closed. It never selects an unvalidated ticker and never
    converts an unresolved holding into a zero-valued position.
    """
    isin = str(isin or "").strip().upper()
    security_name = str(security_name or "").strip()
    asset_class = str(asset_class or "").strip().upper()

    if isin in YAHOO_TICKER_OVERRIDES:
        ticker = str(
            YAHOO_TICKER_OVERRIDES[isin] or ""
        ).strip().upper()

        return {
            "ticker": ticker,
            "match_status": "USER_OVERRIDE",
            "match_score": 9999.0,
            "matched_name": "User ISIN override",
            "matched_exchange": "USER",
            "matched_currency": "",
            "matched_quote_type": "EQUITY_OR_FUND",
        }

    identity = resolve_openfigi_identity(isin)

    candidates = yahoo_candidate_pool(
        isin,
        security_name,
        asset_class,
    )

    selected = None
    tested_candidates = []

    for candidate in candidates[
        :AUTO_TICKER_PRICE_TEST_TOP_N
    ]:
        symbol = str(
            candidate.get("symbol", "") or ""
        ).strip().upper()

        if not symbol:
            continue

        if not _candidate_asset_type_is_compatible(
            candidate,
            asset_class,
        ):
            tested_candidates.append(
                f"{symbol}|rejected_asset_type"
            )
            continue

        has_price = candidate_has_price(symbol)

        tested_candidates.append(
            f"{symbol}|"
            f"price={'yes' if has_price else 'no'}|"
            f"identity="
            f"{candidate.get('_identity_source', '')}|"
            f"currency={candidate.get('currency', '')}|"
            f"exchange={candidate.get('exchange', '')}|"
            f"type={candidate.get('quoteType', '')}"
        )

        if has_price:
            selected = candidate
            break

    if selected is None:
        if not candidates:
            status = str(
                identity.get(
                    "status",
                    "NO_SAFE_CANDIDATES",
                )
                or "NO_SAFE_CANDIDATES"
            )
        else:
            status = "NO_VERIFIED_YAHOO_PRICE"

        AUTO_TICKER_AUDIT_ROWS.append({
            "isin": isin,
            "security_name": security_name,
            "asset_class": asset_class,
            "selected_ticker": "",
            "match_status": status,
            "match_score": 0.0,
            "matched_name": str(
                identity.get("name", "") or ""
            ),
            "matched_exchange": "",
            "matched_currency": "",
            "matched_quote_type": "",
            "identity_source": str(
                identity.get("status", "") or ""
            ),
            "identity_url": str(
                identity.get("url", "") or ""
            ),
            "identity_error": str(
                identity.get("error", "") or ""
            ),
            "top_candidates": " || ".join(
                tested_candidates
            ),
        })

        return {
            "ticker": "",
            "match_status": status,
            "match_score": 0.0,
            "matched_name": str(
                identity.get("name", "") or ""
            ),
            "matched_exchange": "",
            "matched_currency": "",
            "matched_quote_type": "",
        }

    symbol = str(
        selected.get("symbol", "") or ""
    ).strip().upper()

    identity_source = str(
        selected.get("_identity_source", "") or ""
    )

    selected_currency = str(
        selected.get("currency", "") or ""
    ).strip().upper()

    if _is_german_yahoo_symbol(symbol):
        venue_status = "GERMANY_EUR"
    elif (
        selected_currency == "EUR"
        or _is_eur_native_yahoo_symbol(symbol)
    ):
        venue_status = "EUR_NATIVE"
    else:
        venue_status = "NATIVE_FX_CONVERTED"

    status = f"{identity_source}_{venue_status}"

    if identity_source == "OPENFIGI_EXACT_ISIN":
        confidence = (
            1000.0
            if venue_status == "GERMANY_EUR"
            else 950.0
        )
    else:
        confidence = 700.0

    matched_name = str(
        selected.get("shortname", "")
        or selected.get("longname", "")
        or identity.get("name", "")
        or ""
    )

    matched_exchange = str(
        selected.get("exchange", "") or ""
    )

    matched_quote_type = str(
        selected.get("quoteType", "") or ""
    )

    AUTO_TICKER_AUDIT_ROWS.append({
        "isin": isin,
        "security_name": security_name,
        "asset_class": asset_class,
        "selected_ticker": symbol,
        "match_status": status,
        "match_score": confidence,
        "matched_name": matched_name,
        "matched_exchange": matched_exchange,
        "matched_currency": selected_currency,
        "matched_quote_type": matched_quote_type,
        "identity_source": identity_source,
        "identity_url": str(
            selected.get("_identity_url", "") or ""
        ),
        "identity_error": "",
        "top_candidates": " || ".join(
            tested_candidates
        ),
    })

    return {
        "ticker": symbol,
        "match_status": status,
        "match_score": confidence,
        "matched_name": matched_name,
        "matched_exchange": matched_exchange,
        "matched_currency": selected_currency,
        "matched_quote_type": matched_quote_type,
    }

def get_fx_to_eur(currency):
    currency = str(currency or "").upper().strip()
    if currency in FX_CACHE:
        return FX_CACHE[currency]
    if currency in {"EUR", "", "UNKNOWN"}:
        FX_CACHE[currency] = 1.0
        return 1.0
    pair = f"{currency}EUR=X"
    try:
        hist = yf.Ticker(pair).history(period="5d")
        if hist.empty:
            FX_CACHE[currency] = np.nan
            return np.nan
        rate = float(hist["Close"].dropna().iloc[-1])
        FX_CACHE[currency] = rate
        return rate
    except Exception:
        FX_CACHE[currency] = np.nan
        return np.nan


def fetch_latest_yahoo_price(ticker):
    """Fetch a current Yahoo price while preserving non-fatal fallback diagnostics."""
    if not ticker:
        return {
            "price_native": np.nan, "currency": "", "price_date": "", "source_col": "",
            "status": "NO_TICKER", "error": "", "label": "no ticker",
            "price_failure_stage": "", "price_exception_type": "", "price_diagnostic_detail": "",
        }

    diagnostics = []

    def record_failure(stage, exc):
        diagnostics.append({
            "stage": str(stage),
            "exception_type": type(exc).__name__,
            "message": str(exc),
        })

    def diagnostic_fields():
        return {
            "price_failure_stage": " | ".join(dict.fromkeys(d["stage"] for d in diagnostics)),
            "price_exception_type": " | ".join(dict.fromkeys(d["exception_type"] for d in diagnostics)),
            "price_diagnostic_detail": " | ".join(
                f'{d["stage"]}: {d["exception_type"]}: {d["message"]}' for d in diagnostics
            ),
        }

    try:
        yt = yf.Ticker(ticker)
        currency = ""
        price = np.nan
        col = ""

        try:
            fi = yt.fast_info
            currency = str(fi.get("currency", "") or "")
            for k in ["lastPrice", "regularMarketPrice", "previousClose"]:
                v = fi.get(k, None)
                if v is not None and pd.notna(v) and float(v) > 0:
                    price = float(v)
                    col = f"fast_info.{k}"
                    break
        except Exception as exc:
            record_failure("fast_info", exc)

        if not np.isfinite(price):
            try:
                info = yt.info
                currency = currency or str(info.get("currency", "") or "")
                for k in ["regularMarketPrice", "currentPrice", "previousClose"]:
                    v = info.get(k, None)
                    if v is not None and pd.notna(v) and float(v) > 0:
                        price = float(v)
                        col = f"info.{k}"
                        break
            except Exception as exc:
                record_failure("info", exc)

        hist = pd.DataFrame()
        if not np.isfinite(price):
            try:
                hist = yt.history(period="10d", auto_adjust=False)
                if not hist.empty:
                    price_col = "Adj Close" if "Adj Close" in hist.columns else "Close"
                    s = hist[price_col].dropna()
                    if not s.empty:
                        price = float(s.iloc[-1])
                        col = price_col
            except Exception as exc:
                record_failure("history_fallback", exc)
                fields = diagnostic_fields()
                return {
                    "price_native": np.nan, "currency": currency.upper() if currency else "UNKNOWN",
                    "price_date": "", "source_col": "", "status": "ERROR", "error": str(exc),
                    "label": "price lookup failed", **fields,
                }
        else:
            try:
                hist = yt.history(period="10d", auto_adjust=False)
            except Exception as exc:
                record_failure("history_date_lookup", exc)
                hist = pd.DataFrame()

        price_date = ""
        if hist is not None and not hist.empty:
            price_date = str(pd.Timestamp(hist.index[-1]).date())

        if not currency:
            try:
                currency = str(yt.fast_info.get("currency", "") or "")
            except Exception as exc:
                record_failure("currency_lookup", exc)
                currency = ""

        fields = diagnostic_fields()
        return {
            "price_native": price,
            "currency": currency.upper() if currency else "UNKNOWN",
            "price_date": price_date,
            "source_col": col,
            "status": "OK" if np.isfinite(price) else "NO_PRICE",
            "error": "",
            "label": "best available Yahoo latest price; fallback daily close",
            **fields,
        }
    except Exception as exc:
        record_failure("ticker_initialization", exc)
        fields = diagnostic_fields()
        return {
            "price_native": np.nan, "currency": "", "price_date": "", "source_col": "",
            "status": "ERROR", "error": str(exc), "label": "price lookup failed", **fields,
        }


def fetch_yahoo_security_metadata(ticker):
    """Fetch additive descriptive metadata for an already-resolved Yahoo ticker."""

    return fetch_yahoo_security_metadata_core(
        ticker=ticker,
        ticker_factory=yf.Ticker,
        cache=SECURITY_METADATA_CACHE,
    )


def enrich_holdings_with_dynamic_live_prices(holdings):
    out = holdings.copy()
    text_cols = [
        "yahoo_ticker",
        "ticker_match_status",
        "ticker_matched_name",
        "ticker_matched_exchange",
        "ticker_matched_currency",
        "ticker_matched_quote_type",
        "live_price_currency",
        "live_price_date",
        "live_price_source",
        "live_price_col",
        "price_status",
        "price_error",
        "price_label",
        "price_failure_stage",
        "price_exception_type",
        "price_diagnostic_detail",
    ]

    numeric_cols = [
        "ticker_match_score",
        "live_price_native",
        "fx_to_eur",
        "live_price_eur",
        "live_current_value_eur",
        "live_unrealized_pl_acquisition_basis_eur",
        "live_unrealized_pl_user_basis_eur",
        "live_simple_return_acquisition_basis_pct",
        "live_simple_return_user_basis_pct",
    ]

    for c in text_cols:
        if c not in out.columns:
            out[c] = pd.Series("", index=out.index, dtype="object")

    for c in numeric_cols:
        if c not in out.columns:
            out[c] = np.nan

    if not ENABLE_STOCK_FUND_LIVE_PRICES or out.empty:
        return out

    active_rows = out[out["position_status"].eq("ACTIVE")].copy()

    # Resolve every active ISIN in small OpenFIGI batches before the price loop.
    prefetch_openfigi_identities(
        active_rows["isin"].astype(str).tolist()
    )

    ticker_cache = {}
    price_cache = {}

    for counter, (idx, row) in enumerate(active_rows.iterrows(), start=1):
        isin, name = str(row["isin"]), str(row["security_name"])
        if SHOW_DETAILED_PROGRESS:
            print(f"[{counter}/{len(active_rows)}] {name} | {isin}")

        asset_class = str(row.get("asset_class", "") or row.get("asset_class_clean", "") or "").strip().upper()
        match = ticker_cache.get((isin, name, asset_class)) or find_best_yahoo_ticker(
            isin=isin,
            security_name=name,
            asset_class=asset_class,
        )
        ticker_cache[(isin, name, asset_class)] = match

        ticker = match["ticker"]
        price = price_cache.get(ticker) or fetch_latest_yahoo_price(ticker)
        price_cache[ticker] = price
        currency = str(price.get("currency", "") or "").strip().upper()
        price_native = safe_float(price.get("price_native", np.nan), np.nan)
        fx = safe_float(get_fx_to_eur(currency), np.nan)

        price_eur = (
            price_native * fx
            if np.isfinite(price_native) and np.isfinite(fx)
            else np.nan
        )

        qty = safe_float(row.get("current_quantity"), 0.0)
        value = qty * price_eur if np.isfinite(price_eur) else np.nan

        acq_basis = safe_float(
            row.get("remaining_acquisition_cost_basis_eur"),
            0.0,
        )
        user_basis = safe_float(
            row.get("remaining_user_funded_basis_eur"),
            0.0,
        )

        pl_acq = value - acq_basis if np.isfinite(value) else np.nan
        pl_user = value - user_basis if np.isfinite(value) else np.nan

        return_acq_pct = (
            (pl_acq / acq_basis) * 100.0
            if np.isfinite(pl_acq)
            and np.isfinite(acq_basis)
            and abs(acq_basis) > 1e-12
            else np.nan
        )

        return_user_pct = (
            (pl_user / user_basis) * 100.0
            if np.isfinite(pl_user)
            and np.isfinite(user_basis)
            and abs(user_basis) > 1e-12
            else np.nan
        )

        out.loc[idx, "yahoo_ticker"] = ticker
        out.loc[idx, "ticker_match_status"] = match["match_status"]
        out.loc[idx, "ticker_match_score"] = match["match_score"]
        out.loc[idx, "ticker_matched_name"] = match["matched_name"]
        out.loc[idx, "ticker_matched_exchange"] = match["matched_exchange"]
        out.loc[idx, "ticker_matched_currency"] = match["matched_currency"]
        out.loc[idx, "ticker_matched_quote_type"] = match["matched_quote_type"]
        out.loc[idx, "live_price_native"] = price_native
        out.loc[idx, "live_price_currency"] = currency
        out.loc[idx, "fx_to_eur"] = fx
        out.loc[idx, "live_price_eur"] = price_eur
        out.loc[idx, "live_price_date"] = price.get("price_date", "")
        out.loc[idx, "live_price_source"] = ticker
        out.loc[idx, "live_price_col"] = price.get("source_col", "")
        out.loc[idx, "price_status"] = price.get("status", "")
        out.loc[idx, "price_error"] = price.get("error", "")
        out.loc[idx, "price_label"] = price.get("label", "")
        out.loc[idx, "price_failure_stage"] = price.get("price_failure_stage", "")
        out.loc[idx, "price_exception_type"] = price.get("price_exception_type", "")
        out.loc[idx, "price_diagnostic_detail"] = price.get("price_diagnostic_detail", "")
        out.loc[idx, "live_current_value_eur"] = value
        out.loc[idx, "live_unrealized_pl_acquisition_basis_eur"] = pl_acq
        out.loc[idx, "live_unrealized_pl_user_basis_eur"] = pl_user
        out.loc[idx, "live_simple_return_acquisition_basis_pct"] = return_acq_pct
        out.loc[idx, "live_simple_return_user_basis_pct"] = return_user_pct

    return out

# ============================================================
# 6. Derivative module
# ============================================================

def classify_derivative_kind(name, description):
    text = f"{name} {description}".lower()
    if "open end turbo" in text: return "Open End Turbo"
    if "best turbo" in text: return "Best Turbo"
    if "turbo" in text: return "Turbo"
    if "optionsschein" in text or "call" in text or "put" in text: return "Warrant / Option certificate"
    if "knock" in text: return "Knock-out"
    return "Derivative"


def infer_direction(name, description):
    text = f"{name} {description}".lower()
    if "short" in text: return "SHORT"
    if "put" in text: return "PUT"
    if "long" in text: return "LONG"
    if "call" in text: return "CALL"
    return "UNKNOWN"


def extract_underlying(description):
    desc = str(description or "")
    for pat in [r"auf\s+(.+?),\s*quantity", r"auf\s+(.+?)\s+quantity", r"auf\s+(.+)$"]:
        m = re.search(pat, desc, flags=re.IGNORECASE)
        if m:
            out = clean_str(m.group(1))
            return re.sub(r"\s+, quantity.*$", "", out, flags=re.IGNORECASE)
    return ""


def build_quote_links(isin):
    isin_q = quote(str(isin or "").strip())
    return {"onvista_search": f"https://www.onvista.de/suche/?searchValue={isin_q}", "consors_search": f"https://www.consorsbank.de/web/Wertpapier/Suche?searchString={isin_q}", "comdirect_search": f"https://www.comdirect.de/inf/search/all.html?SEARCH_VALUE={isin_q}", "boerse_stuttgart_search": f"https://www.boerse-stuttgart.de/en/search/?query={isin_q}", "boerse_frankfurt_search": f"https://www.boerse-frankfurt.de/suche?q={isin_q}", "google_search": f"https://www.google.com/search?q={isin_q}+derivative+quote"}


def build_derivative_ledger(df):
    der = df[df["asset_class_clean"].eq(DERIVATIVE_ASSET_CLASS) & df["type_norm"].isin(DERIVATIVE_TYPES) & df["isin"].ne("")].copy()
    if der.empty:
        return pd.DataFrame()
    der = der.sort_values(["event_datetime", "source_row"]).reset_index(drop=True)
    der["derivative_kind"] = der.apply(lambda r: classify_derivative_kind(r["security_name"], r["description_clean"]), axis=1)
    der["direction"] = der.apply(lambda r: infer_direction(r["security_name"], r["description_clean"]), axis=1)
    der["underlying_guess"] = der["description_clean"].apply(extract_underlying)
    der["quantity"] = der["shares"].fillna(0)
    der["signed_quantity"] = 0.0
    der.loc[der["type_norm"].eq("BUY"), "signed_quantity"] = der.loc[der["type_norm"].eq("BUY"), "quantity"].abs()
    der.loc[der["type_norm"].isin(["SELL", "WARRANT_EXERCISE"]), "signed_quantity"] = -der.loc[der["type_norm"].isin(["SELL", "WARRANT_EXERCISE"]), "quantity"].abs()
    der.loc[der["type_norm"].eq("TILG"), "signed_quantity"] = 0.0
    der["fee_paid_eur"] = np.where(der["fee"].fillna(0) < 0, -der["fee"].fillna(0), der["fee"].fillna(0))
    der["buy_value_eur"] = np.where(der["type_norm"].eq("BUY"), -der["amount"].fillna(0), 0)
    der["sell_value_eur"] = np.where(der["type_norm"].eq("SELL"), der["amount"].fillna(0), 0)
    der["tilg_settlement_eur"] = np.where(der["type_norm"].eq("TILG"), der["amount"].fillna(0), 0)
    der["cashflow_eur"] = der["amount"].fillna(0) - der["fee_paid_eur"]
    der["trade_id"] = der["transaction_id"].fillna("").astype(str)
    der["trade_id"] = der["trade_id"].where(der["trade_id"].ne(""), der.index.astype(str))
    keep = ["trade_id", "transaction_id", "event_datetime", "event_date", "year", "month", "year_month", "type_norm", "asset_class_clean", "security_name", "isin", "derivative_kind", "direction", "underlying_guess", "quantity", "signed_quantity", "price", "amount", "fee_paid_eur", "buy_value_eur", "sell_value_eur", "tilg_settlement_eur", "cashflow_eur", "currency_clean", "description_clean", "source_row"]
    return der[[c for c in keep if c in der.columns]].sort_values(["event_datetime", "source_row"]).reset_index(drop=True)


def fifo_derivative_positions(derivative_ledger):
    realized_rows, position_rows, open_lots_rows = [], [], []
    if derivative_ledger.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    for isin, g in derivative_ledger.groupby("isin", dropna=False):
        g = g.sort_values(["event_datetime", "source_row"]).copy()
        security_name = g["security_name"].dropna().iloc[-1]
        derivative_kind = g["derivative_kind"].dropna().iloc[-1]
        direction = g["direction"].dropna().iloc[-1]
        und = g["underlying_guess"].dropna()
        und = und[und.astype(str).str.strip().ne("")]
        underlying_guess = und.iloc[-1] if len(und) else ""
        lots = []
        totals = dict(buy_qty=0.0, closed_qty=0.0, buy_value=0.0, sell_value=0.0, tilg=0.0, fees=0.0, cashflow=0.0)
        for _, r in g.iterrows():
            typ = r["type_norm"]
            totals["fees"] += safe_float(r["fee_paid_eur"])
            totals["cashflow"] += safe_float(r["cashflow_eur"])
            if typ == "BUY":
                qty = abs(safe_float(r["signed_quantity"]))
                buy_value = safe_float(r["buy_value_eur"])
                fee = safe_float(r["fee_paid_eur"])
                totals["buy_qty"] += qty
                totals["buy_value"] += buy_value
                cost_basis = buy_value + fee
                lots.append({"source_trade_id": r["trade_id"], "buy_date": r["event_date"], "quantity_remaining": qty, "quantity_original": qty, "cost_basis_remaining": cost_basis, "cost_basis_original": cost_basis, "buy_price": r["price"], "source_row": r["source_row"]})
            elif typ == "SELL":
                qty_to_close = abs(safe_float(r["signed_quantity"]))
                totals["closed_qty"] += qty_to_close
                totals["sell_value"] += safe_float(r["sell_value_eur"])
                net_proceeds = safe_float(r["sell_value_eur"]) - safe_float(r["fee_paid_eur"])
                proceeds_per_unit = net_proceeds / qty_to_close if qty_to_close > 0 else 0
                remaining = qty_to_close
                while remaining > 1e-10 and lots:
                    lot = lots[0]
                    lot_qty = lot["quantity_remaining"]
                    matched_qty = min(remaining, lot_qty)
                    frac = matched_qty / lot_qty if lot_qty else 0
                    allocated_cost = lot["cost_basis_remaining"] * frac
                    allocated_proceeds = proceeds_per_unit * matched_qty
                    realized_rows.append({"isin": isin, "security_name": security_name, "derivative_kind": derivative_kind, "direction": direction, "underlying_guess": underlying_guess, "close_type": "SELL", "sell_trade_id": r["trade_id"], "source_buy_trade_id": lot["source_trade_id"], "buy_date": lot["buy_date"], "sell_date": r["event_date"], "year": r["year"], "year_month": r["year_month"], "quantity_closed": matched_qty, "allocated_cost_basis_eur": allocated_cost, "allocated_net_proceeds_eur": allocated_proceeds, "realized_pl_eur": allocated_proceeds - allocated_cost, "holding_days": (pd.Timestamp(r["event_date"]) - pd.Timestamp(lot["buy_date"])).days if pd.notna(r["event_date"]) and pd.notna(lot["buy_date"]) else np.nan, "source_row": r["source_row"]})
                    lot["quantity_remaining"] -= matched_qty
                    lot["cost_basis_remaining"] -= allocated_cost
                    remaining -= matched_qty
                    if lot["quantity_remaining"] <= 1e-10:
                        lots.pop(0)
                if remaining > 1e-8:
                    realized_rows.append({"isin": isin, "security_name": security_name, "derivative_kind": derivative_kind, "direction": direction, "underlying_guess": underlying_guess, "close_type": "SELL_UNMATCHED", "sell_trade_id": r["trade_id"], "source_buy_trade_id": "UNMATCHED_SELL", "buy_date": pd.NaT, "sell_date": r["event_date"], "year": r["year"], "year_month": r["year_month"], "quantity_closed": remaining, "allocated_cost_basis_eur": np.nan, "allocated_net_proceeds_eur": proceeds_per_unit * remaining, "realized_pl_eur": np.nan, "holding_days": np.nan, "source_row": r["source_row"]})
            elif typ == "WARRANT_EXERCISE":
                qty_to_close = abs(safe_float(r["signed_quantity"]))
                totals["closed_qty"] += qty_to_close
                remaining = qty_to_close
                while remaining > 1e-10 and lots:
                    lot = lots[0]
                    lot_qty = lot["quantity_remaining"]
                    matched_qty = min(remaining, lot_qty)
                    frac = matched_qty / lot_qty if lot_qty else 0
                    allocated_cost = lot["cost_basis_remaining"] * frac
                    realized_rows.append({"isin": isin, "security_name": security_name, "derivative_kind": derivative_kind, "direction": direction, "underlying_guess": underlying_guess, "close_type": "WARRANT_EXERCISE", "sell_trade_id": r["trade_id"], "source_buy_trade_id": lot["source_trade_id"], "buy_date": lot["buy_date"], "sell_date": r["event_date"], "year": r["year"], "year_month": r["year_month"], "quantity_closed": matched_qty, "allocated_cost_basis_eur": allocated_cost, "allocated_net_proceeds_eur": 0, "realized_pl_eur": -allocated_cost, "holding_days": (pd.Timestamp(r["event_date"]) - pd.Timestamp(lot["buy_date"])).days if pd.notna(r["event_date"]) and pd.notna(lot["buy_date"]) else np.nan, "source_row": r["source_row"]})
                    lot["quantity_remaining"] -= matched_qty
                    lot["cost_basis_remaining"] -= allocated_cost
                    remaining -= matched_qty
                    if lot["quantity_remaining"] <= 1e-10:
                        lots.pop(0)
            elif typ == "TILG":
                totals["tilg"] += safe_float(r["tilg_settlement_eur"])
        open_qty = sum(lot["quantity_remaining"] for lot in lots)
        open_cost_basis = sum(lot["cost_basis_remaining"] for lot in lots)
        for lot in lots:
            if lot["quantity_remaining"] > 1e-10:
                open_lots_rows.append({"isin": isin, "security_name": security_name, "derivative_kind": derivative_kind, "direction": direction, "underlying_guess": underlying_guess, "source_buy_trade_id": lot["source_trade_id"], "buy_date": lot["buy_date"], "quantity_remaining": lot["quantity_remaining"], "cost_basis_remaining_eur": lot["cost_basis_remaining"], "buy_price": lot["buy_price"], "source_row": lot["source_row"]})
        realized_for_isin = [rr for rr in realized_rows if rr["isin"] == isin]
        realized_pl_before_tilg = sum(safe_float(rr["realized_pl_eur"]) for rr in realized_for_isin)
        realized_pl_with_tilg = realized_pl_before_tilg + totals["tilg"]
        position_rows.append({"isin": isin, "security_name": security_name, "derivative_kind": derivative_kind, "direction": direction, "underlying_guess": underlying_guess, "position_status": "ACTIVE" if open_qty > 1e-8 else "CLOSED", "current_quantity": open_qty, "total_buy_quantity": totals["buy_qty"], "total_closed_quantity": totals["closed_qty"], "total_buy_value_eur": totals["buy_value"], "total_sell_value_eur": totals["sell_value"], "total_tilg_settlement_eur": totals["tilg"], "total_fees_paid_eur": totals["fees"], "net_cashflow_eur": totals["cashflow"], "open_cost_basis_eur": open_cost_basis, "realized_pl_before_tilg_eur": realized_pl_before_tilg, "realized_pl_with_tilg_eur": realized_pl_with_tilg, "trade_events": len(g), "buy_events": int((g["type_norm"] == "BUY").sum()), "sell_events": int((g["type_norm"] == "SELL").sum()), "warrant_exercise_events": int((g["type_norm"] == "WARRANT_EXERCISE").sum()), "tilg_events": int((g["type_norm"] == "TILG").sum()), "first_trade_date": g["event_date"].min(), "last_trade_date": g["event_date"].max()})
    positions = pd.DataFrame(position_rows)
    realized = pd.DataFrame(realized_rows)
    open_lots = pd.DataFrame(open_lots_rows)
    if not positions.empty:
        positions = positions.sort_values(["position_status", "open_cost_basis_eur", "realized_pl_with_tilg_eur"], ascending=[True, False, False]).reset_index(drop=True)
    return positions, realized, open_lots


def normalize_price_text_to_float(x):
    if x is None:
        return np.nan
    s = str(x).strip().replace("\u00a0", " ")
    s = re.sub(r"(EUR|€|G|B|Brief|Geld|Bid|Ask|Last|Kurs|Price)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[^0-9,\.\-]", "", s)
    if not s:
        return np.nan
    if re.match(r"^-?\d{1,3}(\.\d{3})+,\d+$", s):
        s = s.replace(".", "").replace(",", ".")
    elif "," in s and "." not in s:
        s = s.replace(",", ".")
    elif "," in s and "." in s and s.rfind(",") > s.rfind("."):
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return np.nan


def fetch_html(url):
    try:
        r = requests.get(url, headers=REQUEST_HEADERS, timeout=QUOTE_TIMEOUT_SECONDS, allow_redirects=True)
        if r.status_code != 200:
            return "", f"HTTP_{r.status_code}", str(r.url)
        return r.text or "", "", str(r.url)
    except Exception as e:
        return "", f"REQUEST_ERROR: {e}", url


def extract_quote_from_visible_text(html):
    if not html:
        return {}
    soup = BeautifulSoup(html, "html.parser")
    for bad in soup(["script", "style", "noscript", "svg"]):
        bad.decompose()
    text = soup.get_text("\n")
    text = re.sub(r"\n+", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    result = {"last": np.nan, "bid": np.nan, "ask": np.nan, "currency": "EUR", "timestamp": "", "raw_match": ""}
    for pat in [r"(?:Bid|Geld)\s*[:\n ]+\s*([0-9][0-9\.,]*)", r"([0-9][0-9\.,]*)\s*(?:Geld|Bid)"]:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            val = normalize_price_text_to_float(m.group(1))
            if np.isfinite(val):
                result["bid"] = val
                break
    for pat in [r"(?:Ask|Brief)\s*[:\n ]+\s*([0-9][0-9\.,]*)", r"([0-9][0-9\.,]*)\s*(?:Brief|Ask)"]:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            val = normalize_price_text_to_float(m.group(1))
            if np.isfinite(val):
                result["ask"] = val
                break
    for pat in [r"(?:LAST PRICE|Last Price|Letzter Preis|Letzter Kurs|aktueller Kurs|Kurs)\s*[:\n ]+\s*([0-9][0-9\.,]*)", r"([0-9][0-9\.,]*)\s*(?:EUR|€)\s*(?:G|B)?"]:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            val = normalize_price_text_to_float(m.group(1))
            if np.isfinite(val):
                result["last"] = val
                result["raw_match"] = m.group(0)[:250]
                break
    for pat in [r"(?:PRICE DETERMINATION TIME|Preisfeststellung|Datum/Zeit|Zeit)\s*[:\n ]+\s*([0-9]{1,2}[./][0-9]{1,2}[./][0-9]{2,4}[^,\n]{0,30})", r"([0-9]{1,2}[./][0-9]{1,2}[./][0-9]{2,4}\s*/?\s*[0-9]{1,2}:[0-9]{2}:[0-9]{2})", r"([0-9]{1,2}:[0-9]{2}:[0-9]{2})"]:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            result["timestamp"] = clean_str(m.group(1))
            break
    return result

# ============================================================
# Derivative quote probe: safer multi-source replacement
# Replace from def build_derivative_source_urls(...) through
# def multi_source_derivative_quote_probe(...), but keep
# enrich_derivatives_with_quote_attempts(...) unchanged.
# ============================================================

def is_valid_derivative_quote_number(x, min_price=1e-6, max_price=100000):
    """
    Reject NaN, zero, negative, and absurd parsed prices.
    This prevents pages like Comdirect/search pages from accidentally
    producing 0.00 as the selected derivative price.
    """
    try:
        v = float(x)
        return np.isfinite(v) and (v > min_price) and (v < max_price)
    except Exception:
        return False


def clean_derivative_quote_number(x):
    return float(x) if is_valid_derivative_quote_number(x) else np.nan


def build_derivative_source_urls(isin):
    isin_q = quote(str(isin or "").strip())

    return {
        # Existing sources
        "boerse_stuttgart_search": f"https://www.boerse-stuttgart.de/en/search/?query={isin_q}",
        "boerse_stuttgart_de_search": f"https://www.boerse-stuttgart.de/de-de/suche/?query={isin_q}",
        "onvista_search": f"https://www.onvista.de/suche/?searchValue={isin_q}",
        "consors_search": f"https://www.consorsbank.de/web/Wertpapier/Suche?searchString={isin_q}",
        "comdirect_search": f"https://www.comdirect.de/inf/search/all.html?SEARCH_VALUE={isin_q}",
        "boerse_frankfurt_search": f"https://www.boerse-frankfurt.de/suche?q={isin_q}",

        # Extra fallback search sources.
        # These are best-effort. They may not always expose parsable quote text.
        "ariva_search": f"https://www.ariva.de/suche/?searchname={isin_q}",
        "finanzen_net_search": f"https://www.finanzen.net/suchergebnis.asp?_search={isin_q}",

        # Manual sanity fallback link, not scraped directly.
        "google_search": f"https://www.google.com/search?q={isin_q}+derivate+kurs+geld+brief",
    }


def probe_url_for_quote(url, source_name):
    html, err, final_url = fetch_html(url)

    if err:
        return {
            "source": source_name,
            "source_url": final_url,
            "status": err,
            "last": np.nan,
            "bid": np.nan,
            "ask": np.nan,
            "selected_price": np.nan,
            "selected_price_type": "NONE",
            "currency": "",
            "timestamp": "",
            "raw_match": "",
            "confidence": "NONE",
        }

    parsed = extract_quote_from_visible_text(html)

    bid = clean_derivative_quote_number(parsed.get("bid", np.nan))
    ask = clean_derivative_quote_number(parsed.get("ask", np.nan))
    last = clean_derivative_quote_number(parsed.get("last", np.nan))

    has_bid = is_valid_derivative_quote_number(bid)
    has_ask = is_valid_derivative_quote_number(ask)
    has_last = is_valid_derivative_quote_number(last)

    if not (has_bid or has_ask or has_last):
        return {
            "source": source_name,
            "source_url": final_url,
            "status": "NO_USABLE_QUOTE_PARSED",
            "last": last,
            "bid": bid,
            "ask": ask,
            "selected_price": np.nan,
            "selected_price_type": "NONE",
            "currency": "",
            "timestamp": parsed.get("timestamp", ""),
            "raw_match": parsed.get("raw_match", ""),
            "confidence": "NONE",
        }

    selected = np.nan
    selected_type = "NONE"
    confidence = "NONE"

    # Finanzen.net and Comdirect search pages frequently expose a reliable
    # ask/last pair while unrelated page numbers are incorrectly parsed as bid.
    # Accept the ask only when the same page's last price corroborates it.
    if source_name in {"Finanzen.net", "Comdirect"} and has_ask and has_last:
        ask_last_gap = abs(ask - last) / max(abs(last), 0.01)

        if ask_last_gap <= 0.10:
            selected = ask
            selected_type = "ASK_VALIDATED_BY_LAST"
            confidence = "HIGH" if source_name == "Finanzen.net" else "MEDIUM_HIGH"

    # Generic fallback for all other source/field combinations.
    if not is_valid_derivative_quote_number(selected):
        if has_bid and has_ask:
            if bid <= ask:
                selected = bid
                selected_type = "BID"
                confidence = (
                    "HIGH"
                    if source_name in {
                        "Onvista",
                        "Comdirect",
                        "Börse Stuttgart DE",
                        "Börse Stuttgart EN",
                    }
                    else "MEDIUM"
                )
            else:
                # Never convert an inverted/garbled spread into a quote by
                # choosing its lower side. Reject it and let another source win.
                selected = np.nan
                selected_type = "REJECTED_INCONSISTENT_SPREAD"
                confidence = "NONE"
        elif has_bid:
            selected = bid
            selected_type = "BID"
            confidence = (
                "MEDIUM_HIGH"
                if source_name in {"Onvista", "Comdirect"}
                else "MEDIUM"
            )
        elif has_last:
            selected = last
            selected_type = "LAST"
            confidence = "MEDIUM"
        elif has_ask:
            selected = ask
            selected_type = "ASK_ONLY"
            confidence = "LOW"

    if not is_valid_derivative_quote_number(selected):
        return {
            "source": source_name,
            "source_url": final_url,
            "status": (
                "REJECTED_INCONSISTENT_SPREAD"
                if selected_type == "REJECTED_INCONSISTENT_SPREAD"
                else "NO_VALID_SELECTED_PRICE"
            ),
            "last": last,
            "bid": bid,
            "ask": ask,
            "selected_price": np.nan,
            "selected_price_type": selected_type,
            "currency": "",
            "timestamp": parsed.get("timestamp", ""),
            "raw_match": parsed.get("raw_match", ""),
            "confidence": "NONE",
        }

    return {
        "source": source_name,
        "source_url": final_url,
        "status": "QUOTE_PARSED",
        "last": last,
        "bid": bid,
        "ask": ask,
        "selected_price": selected,
        "selected_price_type": selected_type,
        "currency": parsed.get("currency", "EUR") or "EUR",
        "timestamp": parsed.get("timestamp", ""),
        "raw_match": parsed.get("raw_match", ""),
        "confidence": confidence,
    }


def yahoo_derivative_probe(isin):
    isin = str(isin or "").strip()

    if not isin:
        return {
            "source": "Yahoo",
            "source_url": "",
            "status": "NO_ISIN",
            "last": np.nan,
            "bid": np.nan,
            "ask": np.nan,
            "selected_price": np.nan,
            "selected_price_type": "NONE",
            "currency": "",
            "timestamp": "",
            "raw_match": "",
            "confidence": "NONE",
        }

    try:
        yt = yf.Ticker(isin)
        hist = yt.history(period="10d", auto_adjust=False)

        if hist.empty:
            return {
                "source": "Yahoo",
                "source_url": f"https://finance.yahoo.com/quote/{isin}",
                "status": "YAHOO_NO_HISTORY",
                "last": np.nan,
                "bid": np.nan,
                "ask": np.nan,
                "selected_price": np.nan,
                "selected_price_type": "NONE",
                "currency": "",
                "timestamp": "",
                "raw_match": "",
                "confidence": "NONE",
            }

        price_col = "Adj Close" if "Adj Close" in hist.columns else "Close"
        clean_price = hist[price_col].dropna()

        if clean_price.empty:
            return {
                "source": "Yahoo",
                "source_url": f"https://finance.yahoo.com/quote/{isin}",
                "status": "YAHOO_NO_PRICE",
                "last": np.nan,
                "bid": np.nan,
                "ask": np.nan,
                "selected_price": np.nan,
                "selected_price_type": "NONE",
                "currency": "",
                "timestamp": "",
                "raw_match": "",
                "confidence": "NONE",
            }

        price = clean_derivative_quote_number(clean_price.iloc[-1])

        if not is_valid_derivative_quote_number(price):
            return {
                "source": "Yahoo",
                "source_url": f"https://finance.yahoo.com/quote/{isin}",
                "status": "YAHOO_INVALID_PRICE",
                "last": np.nan,
                "bid": np.nan,
                "ask": np.nan,
                "selected_price": np.nan,
                "selected_price_type": "NONE",
                "currency": "",
                "timestamp": "",
                "raw_match": "",
                "confidence": "NONE",
            }

        currency = ""

        try:
            currency = str(yt.fast_info.get("currency", "") or "").upper()
        except Exception:
            pass

        return {
            "source": "Yahoo",
            "source_url": f"https://finance.yahoo.com/quote/{isin}",
            "status": "YAHOO_FOUND_LOW_CONFIDENCE",
            "last": price,
            "bid": np.nan,
            "ask": np.nan,
            "selected_price": price,
            "selected_price_type": "LAST",
            "currency": currency if currency else "UNKNOWN",
            "timestamp": clean_price.index[-1].strftime("%Y-%m-%d"),
            "raw_match": price_col,
            "confidence": "LOW",
        }

    except Exception as e:
        return {
            "source": "Yahoo",
            "source_url": f"https://finance.yahoo.com/quote/{isin}",
            "status": "YAHOO_ERROR",
            "last": np.nan,
            "bid": np.nan,
            "ask": np.nan,
            "selected_price": np.nan,
            "selected_price_type": "NONE",
            "currency": "",
            "timestamp": "",
            "raw_match": str(e)[:250],
            "confidence": "NONE",
        }


def choose_best_quote_result(results):
    usable = [
        r for r in results
        if is_valid_derivative_quote_number(r.get("selected_price", np.nan))
        and str(r.get("confidence", "NONE")).upper() != "NONE"
        and str(r.get("selected_price_type", "")).upper()
        != "REJECTED_INCONSISTENT_SPREAD"
    ]

    if not usable:
        return {
            "quote_status": "QUOTE_FAILED",
            "live_price_eur": np.nan,
            "quote_currency": "",
            "quote_date": "",
            "quote_source": "",
            "quote_source_url": "",
            "quote_price_type": "",
            "quote_bid": np.nan,
            "quote_ask": np.nan,
            "quote_last": np.nan,
            "quote_error": "No usable nonzero quote found",
            "quote_confidence": "NONE",
            "quote_raw_match": "",
        }

    # Primary rule:
    # use Finanzen.net's ask only when its own last price corroborates it.
    validated_finanzen = [
        r for r in usable
        if str(r.get("source", "")) == "Finanzen.net"
        and str(r.get("selected_price_type", "")).upper()
        == "ASK_VALIDATED_BY_LAST"
        and str(r.get("confidence", "")).upper() in {"HIGH", "MEDIUM_HIGH"}
    ]

    if validated_finanzen:
        best = validated_finanzen[0]
    else:
        # Fallback hierarchy when no validated Finanzen.net ask exists.
        source_priority = {
            "Börse Stuttgart DE": 1,
            "Börse Stuttgart EN": 2,
            "Onvista": 3,
            "Comdirect": 4,
            "Börse Frankfurt": 5,
            "Consors": 6,
            "Finanzen.net": 7,
            "ARIVA": 8,
            "Yahoo": 99,
        }

        type_priority = {
            "ASK_VALIDATED_BY_LAST": 1,
            "BID": 2,
            "LAST": 3,
            "ASK_ONLY": 4,
            "NONE": 99,
        }

        confidence_priority = {
            "HIGH": 1,
            "MEDIUM_HIGH": 2,
            "MEDIUM": 3,
            "LOW": 4,
            "NONE": 99,
        }

        usable.sort(
            key=lambda r: (
                source_priority.get(r.get("source", ""), 50),
                type_priority.get(
                    str(r.get("selected_price_type", "NONE")).upper(),
                    50,
                ),
                confidence_priority.get(
                    str(r.get("confidence", "NONE")).upper(),
                    50,
                ),
            )
        )

        best = usable[0]

    raw_price = clean_derivative_quote_number(
        best.get("selected_price", np.nan)
    )
    curr = str(
        best.get("currency", "EUR") or "EUR"
    ).upper().strip()

    if (
        curr not in ["EUR", "UNKNOWN", ""]
        and is_valid_derivative_quote_number(raw_price)
    ):
        fx = get_fx_to_eur(curr)

        if is_valid_derivative_quote_number(fx):
            converted_price = raw_price * fx
            final_curr = f"EUR_FROM_{curr}"
        else:
            converted_price = np.nan
            final_curr = f"FX_FAILED_{curr}"
    else:
        converted_price = raw_price
        final_curr = (
            "EUR"
            if curr in ["", "UNKNOWN"]
            else curr
        )

    if not is_valid_derivative_quote_number(converted_price):
        return {
            "quote_status": "QUOTE_FAILED",
            "live_price_eur": np.nan,
            "quote_currency": final_curr,
            "quote_date": best.get("timestamp", ""),
            "quote_source": best.get("source", ""),
            "quote_source_url": best.get("source_url", ""),
            "quote_price_type": best.get(
                "selected_price_type",
                "",
            ),
            "quote_bid": best.get("bid", np.nan),
            "quote_ask": best.get("ask", np.nan),
            "quote_last": best.get("last", np.nan),
            "quote_error": (
                "Selected quote failed FX/validity check"
            ),
            "quote_confidence": best.get(
                "confidence",
                "NONE",
            ),
            "quote_raw_match": best.get(
                "raw_match",
                "",
            ),
        }

    quote_status = "QUOTE_OK"

    if (
        best.get("source") in {"ARIVA", "Yahoo"}
        or str(best.get("confidence", "")).upper() == "LOW"
    ):
        quote_status = "QUOTE_OK_LOW_CONFIDENCE"

    return {
        "quote_status": quote_status,
        "live_price_eur": converted_price,
        "quote_currency": final_curr,
        "quote_date": best.get("timestamp", ""),
        "quote_source": best.get("source", ""),
        "quote_source_url": best.get("source_url", ""),
        "quote_price_type": best.get(
            "selected_price_type",
            "",
        ),
        "quote_bid": best.get("bid", np.nan),
        "quote_ask": best.get("ask", np.nan),
        "quote_last": best.get("last", np.nan),
        "quote_error": best.get("status", ""),
        "quote_confidence": best.get(
            "confidence",
            "NONE",
        ),
        "quote_raw_match": best.get(
            "raw_match",
            "",
        ),
    }

def multi_source_derivative_quote_probe(isin):
    isin = str(isin or "").strip()
    urls = build_derivative_source_urls(isin)
    results = []

    max_workers = QUOTE_MAX_WORKERS if "QUOTE_MAX_WORKERS" in globals() else 8

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []

        if QUOTE_SOURCES_ENABLED.get("boerse_stuttgart", True):
            futures.append(executor.submit(probe_url_for_quote, urls["boerse_stuttgart_de_search"], "Börse Stuttgart DE"))
            futures.append(executor.submit(probe_url_for_quote, urls["boerse_stuttgart_search"], "Börse Stuttgart EN"))

        if QUOTE_SOURCES_ENABLED.get("onvista", True):
            futures.append(executor.submit(probe_url_for_quote, urls["onvista_search"], "Onvista"))

        if QUOTE_SOURCES_ENABLED.get("comdirect", True):
            futures.append(executor.submit(probe_url_for_quote, urls["comdirect_search"], "Comdirect"))

        if QUOTE_SOURCES_ENABLED.get("consors", True):
            futures.append(executor.submit(probe_url_for_quote, urls["consors_search"], "Consors"))

        futures.append(executor.submit(probe_url_for_quote, urls["boerse_frankfurt_search"], "Börse Frankfurt"))
        futures.append(executor.submit(probe_url_for_quote, urls["ariva_search"], "ARIVA"))
        futures.append(executor.submit(probe_url_for_quote, urls["finanzen_net_search"], "Finanzen.net"))

        if QUOTE_SOURCES_ENABLED.get("yahoo", True):
            futures.append(executor.submit(yahoo_derivative_probe, isin))

        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                results.append({
                    "source": "UNKNOWN",
                    "source_url": "",
                    "status": f"THREAD_ERROR: {str(e)[:180]}",
                    "selected_price": np.nan,
                    "selected_price_type": "NONE",
                    "bid": np.nan,
                    "ask": np.nan,
                    "last": np.nan,
                    "currency": "",
                    "timestamp": "",
                    "confidence": "NONE",
                    "raw_match": "",
                })

    best = choose_best_quote_result(results)
    best["all_quote_attempts"] = results
    return best

def enrich_derivatives_with_quote_attempts(positions):
    positions = positions.copy()
    for col, default in {"quote_status": "", "live_price_eur": np.nan, "quote_currency": "", "quote_date": "", "quote_source": "", "quote_source_url": "", "quote_price_type": "", "quote_bid": np.nan, "quote_ask": np.nan, "quote_last": np.nan, "quote_error": "", "quote_confidence": "", "quote_raw_match": "", "estimated_live_value_eur": np.nan, "estimated_unrealized_pl_eur": np.nan, "estimated_unrealized_return_pct": np.nan, "avg_open_cost_per_unit_eur": np.nan, "annualized_return_pct": np.nan, "distance_to_breakeven_pct": np.nan, "live_quote_found": False, "days_open": np.nan, "onvista_search": "", "consors_search": "", "comdirect_search": "", "boerse_stuttgart_search": "", "boerse_frankfurt_search": "", "google_search": ""}.items():
        positions[col] = default
    quote_attempt_rows = []
    if positions.empty:
        return positions, pd.DataFrame()
    active_mask = positions["position_status"].eq("ACTIVE")
    active_count = int(active_mask.sum())
    if SHOW_DETAILED_PROGRESS:
        print(f"Derivative quote probe for {active_count} active positions...")
    if not ENABLE_DERIVATIVE_QUOTE_PROBE:
        for idx, r in positions[active_mask].iterrows():
            for k, v in build_quote_links(str(r["isin"])).items():
                positions.loc[idx, k] = v
            positions.loc[idx, "quote_status"] = "QUOTE_PROBE_DISABLED"
        return positions, pd.DataFrame()
    for counter, (idx, r) in enumerate(positions[active_mask].iterrows(), start=1):
        isin, name = str(r["isin"]), str(r["security_name"])
        if SHOW_DETAILED_PROGRESS:
            print(f"[{counter}/{active_count}] Derivative quote: {isin} | {name}")
        for k, v in build_quote_links(isin).items():
            positions.loc[idx, k] = v
        result = multi_source_derivative_quote_probe(isin)
        for k in ["quote_status", "live_price_eur", "quote_currency", "quote_date", "quote_source", "quote_source_url", "quote_price_type", "quote_bid", "quote_ask", "quote_last", "quote_error", "quote_confidence", "quote_raw_match"]:
            positions.loc[idx, k] = result.get(k, "")
        for attempt in result.get("all_quote_attempts", []):
            quote_attempt_rows.append({"isin": isin, "security_name": name, "source": attempt.get("source", ""), "source_url": attempt.get("source_url", ""), "status": attempt.get("status", ""), "selected_price": attempt.get("selected_price", np.nan), "selected_price_type": attempt.get("selected_price_type", ""), "bid": attempt.get("bid", np.nan), "ask": attempt.get("ask", np.nan), "last": attempt.get("last", np.nan), "currency": attempt.get("currency", ""), "timestamp": attempt.get("timestamp", ""), "confidence": attempt.get("confidence", ""), "raw_match": attempt.get("raw_match", "")})
    positions["estimated_live_value_eur"] = positions["current_quantity"] * positions["live_price_eur"]
    positions["estimated_unrealized_pl_eur"] = positions["estimated_live_value_eur"] - positions["open_cost_basis_eur"]
    positions["estimated_unrealized_return_pct"] = np.where(positions["open_cost_basis_eur"].abs() > 1e-12, positions["estimated_unrealized_pl_eur"] / positions["open_cost_basis_eur"] * 100.0, np.nan)
    positions["avg_open_cost_per_unit_eur"] = np.where(positions["current_quantity"].abs() > 1e-12, positions["open_cost_basis_eur"] / positions["current_quantity"], np.nan)
    positions["live_quote_found"] = positions["live_price_eur"].notna()
    first_dates = pd.to_datetime(positions["first_trade_date"], errors="coerce")
    positions["days_open"] = (pd.Timestamp.today().normalize() - first_dates).dt.days
    years_open = np.where((positions["days_open"] / 365.0) < (1/365.0), 1/365.0, positions["days_open"] / 365.0)
    positions["annualized_return_pct"] = np.where((positions["open_cost_basis_eur"] > 1e-12) & positions["estimated_live_value_eur"].notna() & (positions["estimated_live_value_eur"] >= 0), ((positions["estimated_live_value_eur"] / positions["open_cost_basis_eur"]) ** (1 / years_open) - 1) * 100.0, np.nan)
    positions["distance_to_breakeven_pct"] = np.where(positions["live_price_eur"].notna() & (positions["live_price_eur"] > 1e-12) & (positions["avg_open_cost_per_unit_eur"] > 1e-12), ((positions["avg_open_cost_per_unit_eur"] / positions["live_price_eur"]) - 1) * 100.0, np.nan)
    return positions, pd.DataFrame(quote_attempt_rows)

# ============================================================
# 7. Cash / unsupported activity summaries
# ============================================================


CARD_EXPENSE_TYPES = {"CARD_TRANSACTION", "CARD_TRANSACTION_INTERNATIONAL", "CARD_ORDERING_FEE"}
EXPENSE_CASHLIKE_CATEGORY = "Cash-like & financial"
EXPENSE_REFUND_CATEGORY = "Refund / unidentified"
EXPENSE_CATEGORY_ORDER = [
    "Groceries & food retail", "Restaurants & cafés", "Shopping & retail",
    "Travel & accommodation", "Digital & subscriptions", "Health & pharmacy",
    "Alcohol & tobacco", "Education", "Transport", "Fuel & automotive",
    "Home & household", "Entertainment", "Personal care", "Telecom & utilities",
    "Government & fees", "Other / uncategorized", EXPENSE_REFUND_CATEGORY,
    EXPENSE_CASHLIKE_CATEGORY,
]

_EXPENSE_MERCHANT_PATTERNS = [
    (r"AMZN|AMAZON", "Amazon"),
    (r"ALIEXPRESS", "AliExpress"),
    (r"OPENAI|CHATGPT", "OpenAI / ChatGPT"),
    (r"GOOGLE\*?GOOGLE ONE|GOOGLE ONE", "Google One"),
    (r"NETFLIX", "Netflix"),
    (r"DROPBOX", "Dropbox"),
    (r"NETTO", "Netto Marken-Discount"),
    (r"ALDI", "Aldi"),
    (r"LIDL", "Lidl"),
    (r"REWE", "Rewe"),
    (r"GO ASIA", "Go Asia"),
    (r"DM[- ]?DROGERIE|DM DROGERIE", "dm"),
    (r"ROSSMANN", "Rossmann"),
    (r"ALLDRINK", "alldrink"),
    (r"CAFE UNIQUE", "Cafe Unique"),
    (r"BACKWERK|BACK\. GILLEN|BACKEREI GILLEN|BACK GILLEN", "BackWerk / Gillen"),
    (r"HOOKAH SHOP", "Hookah Shop"),
    (r"STUDIERENDENWERK|STW SAARLAND", "Studierendenwerk Saar"),
    (r"DB VERTRIEB|DEUTSCHE BAHN", "Deutsche Bahn"),
    (r"TK MAXX", "TK Maxx"),
]


def _canonicalize_expense_merchant(value):
    text = clean_str(value)
    text = re.sub(r"\bnull\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" -_/\\")
    if not text or text.upper() in {"TR CARD TRANSACTION", "UNKNOWN SECURITY", "NAN", "NONE"}:
        return ""
    upper = text.upper()
    for pattern, label in _EXPENSE_MERCHANT_PATTERNS:
        if re.search(pattern, upper):
            return label
    text = re.sub(r"\s+#?\d{2,}$", "", text).strip()
    return text.title() if text.isupper() else text


def _expense_category_from_mcc(mcc_value, merchant_name=""):
    try:
        mcc = int(float(mcc_value))
    except Exception:
        mcc = None

    merchant_upper = str(merchant_name or "").upper()
    if any(term in merchant_upper for term in ["OPENAI", "CHATGPT", "GOOGLE ONE", "NETFLIX", "DROPBOX", "GEMINI"]):
        return "Digital & subscriptions"
    if mcc in {6010, 6011, 6012, 6051, 6211, 6300, 6513} or (mcc is not None and 6536 <= mcc <= 6540):
        return EXPENSE_CASHLIKE_CATEGORY
    if mcc in {5411, 5422, 5441, 5451, 5462, 5499}:
        return "Groceries & food retail"
    if mcc in {5811, 5812, 5813, 5814}:
        return "Restaurants & cafés"
    if mcc in {5921, 5993}:
        return "Alcohol & tobacco"
    if mcc in {5912, 5122, 5975, 5976} or (mcc is not None and 8011 <= mcc <= 8099):
        return "Health & pharmacy"
    if mcc in {7230, 7297, 7298, 7299, 5977}:
        return "Personal care"
    if mcc in {8211, 8220, 8241, 8244, 8249, 8299}:
        return "Education"
    if mcc in {4111, 4112, 4121, 4131, 4784, 4789, 7523}:
        return "Transport"
    if mcc in {5541, 5542, 5551}:
        return "Fuel & automotive"
    if mcc in {4511, 4582, 4722, 7011, 7012, 7032, 7033} or (
        mcc is not None and (3000 <= mcc <= 3299 or 3351 <= mcc <= 3441 or 3501 <= mcc <= 3999)
    ):
        return "Travel & accommodation"
    if mcc in {5732, 5734, 5735, 5815, 5816, 5817, 5818, 4899}:
        return "Digital & subscriptions"
    if mcc in {7832, 7841, 7911, 7922, 7929, 7932, 7933, 7941} or (mcc is not None and 7991 <= mcc <= 7999):
        return "Entertainment"
    if mcc in {4812, 4814, 4816, 4821, 4900}:
        return "Telecom & utilities"
    if mcc in {5200, 5211, 5231, 5251, 5261, 5262, 5712, 5713, 5714, 5718, 5719, 5722}:
        return "Home & household"
    if mcc in {9211, 9222, 9311, 9399, 9402}:
        return "Government & fees"

    retail_codes = {
        5094, 5137, 5139, 5300, 5309, 5310, 5311, 5331, 5399,
        5611, 5621, 5631, 5641, 5651, 5655, 5661, 5681,
        5691, 5697, 5698, 5699, 5931, 5932, 5933, 5935,
        5940, 5941, 5942, 5943, 5944, 5945, 5946, 5947, 5948, 5949,
        5950, 5964, 5965, 5966, 5967, 5968, 5969,
        5970, 5971, 5972, 5973, 5978, 5979,
        5992, 5994, 5995, 5996, 5997, 5998, 5999,
    }
    if mcc in retail_codes:
        return "Shopping & retail"
    return "Other / uncategorized"


def _empty_expense_analytics():
    return {
        "metrics": {},
        "ledger": pd.DataFrame(),
        "monthly": pd.DataFrame(),
        "categories": pd.DataFrame(),
        "merchants": pd.DataFrame(),
        "weekdays": pd.DataFrame(),
        "daily": pd.DataFrame(),
        "recurring_candidates": pd.DataFrame(),
        "narrative": pd.DataFrame(),
    }


def build_expense_analytics(df):
    """Build a private card-expense analytics layer.

    This function never feeds investment accounting. It uses only card cash effects,
    separates refunds, and excludes cash-like/financial card outflows from the main
    consumer-spending KPIs while preserving them in a separate audit category.
    """
    if df is None or df.empty:
        return _empty_expense_analytics()

    card = df[df["type_norm"].isin(CARD_EXPENSE_TYPES)].copy()
    if card.empty:
        return _empty_expense_analytics()

    card = card.sort_values(["event_date", "event_datetime", "source_row"]).reset_index(drop=True)
    card["cash_effect_eur"] = (
        card["amount"].fillna(0.0)
        + card["fee"].fillna(0.0)
        + card["tax"].fillna(0.0)
    )
    card["gross_card_outflow_eur"] = (-card["cash_effect_eur"]).clip(lower=0.0)
    card["refund_eur"] = card["cash_effect_eur"].clip(lower=0.0)
    card["net_card_outflow_eur"] = card["gross_card_outflow_eur"] - card["refund_eur"]

    merchant_values = []
    for _, row in card.iterrows():
        merchant = _canonicalize_expense_merchant(row.get("security_name", ""))
        if not merchant:
            merchant = _canonicalize_expense_merchant(row.get("description_clean", ""))
        if not merchant:
            if safe_float(row.get("refund_eur")) > 0:
                merchant = "Unidentified refund"
            elif str(row.get("type_norm", "")) == "CARD_ORDERING_FEE":
                merchant = "Trade Republic card fee"
            else:
                merchant = "Unidentified card transaction"
        merchant_values.append(merchant)
    card["merchant_name"] = merchant_values
    card["mcc_code_clean"] = card["mcc_code"].apply(
        lambda value: str(int(float(value))) if pd.notna(value) and str(value).strip() not in {"", "nan"} else ""
    )
    card["expense_category"] = [
        _expense_category_from_mcc(mcc, merchant)
        for mcc, merchant in zip(card["mcc_code"], card["merchant_name"])
    ]
    card.loc[card["type_norm"].eq("CARD_ORDERING_FEE"), "expense_category"] = "Government & fees"
    card["refund_match_confidence"] = ""
    card["refund_matched_purchase_date"] = pd.NaT

    purchases = card[card["gross_card_outflow_eur"].gt(0)].copy()
    used_purchase_indices = set()
    for refund_idx, refund_row in card[card["refund_eur"].gt(0)].iterrows():
        refund_date = pd.to_datetime(refund_row.get("event_date"), errors="coerce")
        if pd.isna(refund_date):
            continue
        merchant_hint = str(refund_row.get("merchant_name", "") or "")
        prior = purchases[
            purchases["event_date"].le(refund_date)
            & (refund_date - purchases["event_date"]).dt.days.between(0, 180)
            & ~purchases.index.isin(used_purchase_indices)
        ].copy()
        selected = None
        confidence = "UNMATCHED"

        if merchant_hint not in {"", "Unidentified refund"}:
            same_merchant = prior[prior["merchant_name"].eq(merchant_hint)].copy()
            if not same_merchant.empty:
                same_merchant["amount_distance"] = (
                    same_merchant["gross_card_outflow_eur"] - safe_float(refund_row.get("refund_eur"))
                ).abs()
                selected = same_merchant.sort_values(
                    ["amount_distance", "event_date", "source_row"],
                    ascending=[True, False, False],
                ).iloc[0]
                confidence = "HIGH" if safe_float(selected["amount_distance"]) <= 0.02 else "MEDIUM"
        else:
            exact = prior[
                prior["gross_card_outflow_eur"].round(2).eq(round(safe_float(refund_row.get("refund_eur")), 2))
                & (refund_date - prior["event_date"]).dt.days.le(120)
            ].copy()
            if len(exact) == 1:
                selected = exact.iloc[0]
                confidence = "MEDIUM"

        if selected is not None:
            used_purchase_indices.add(selected.name)
            card.loc[refund_idx, "merchant_name"] = selected["merchant_name"]
            card.loc[refund_idx, "expense_category"] = selected["expense_category"]
            card.loc[refund_idx, "refund_match_confidence"] = confidence
            card.loc[refund_idx, "refund_matched_purchase_date"] = selected["event_date"]
        else:
            card.loc[refund_idx, "refund_match_confidence"] = "UNMATCHED"
            if merchant_hint in {"", "Unidentified refund"}:
                card.loc[refund_idx, "expense_category"] = EXPENSE_REFUND_CATEGORY

    card["is_cashlike_or_financial"] = card["expense_category"].eq(EXPENSE_CASHLIKE_CATEGORY)
    card["consumer_gross_outflow_eur"] = np.where(
        card["is_cashlike_or_financial"], 0.0, card["gross_card_outflow_eur"]
    )
    card["consumer_refund_eur"] = np.where(
        card["is_cashlike_or_financial"], 0.0, card["refund_eur"]
    )
    card["net_consumer_spend_eur"] = np.where(
        card["is_cashlike_or_financial"], 0.0, card["net_card_outflow_eur"]
    )
    card["is_international"] = (
        card["type_norm"].eq("CARD_TRANSACTION_INTERNATIONAL")
        | ~card["original_currency"].fillna("EUR").astype(str).str.upper().eq("EUR")
    )
    card["transaction_kind"] = np.select(
        [card["gross_card_outflow_eur"].gt(0), card["refund_eur"].gt(0)],
        ["PURCHASE", "REFUND"],
        default="ZERO_VALUE",
    )
    card["year_month"] = card["event_date"].dt.strftime("%Y-%m")
    card["year"] = card["event_date"].dt.year.astype("Int64")
    card["month"] = card["event_date"].dt.month.astype("Int64")
    card["month_name"] = card["event_date"].dt.strftime("%b")
    card["weekday"] = card["event_date"].dt.day_name()
    card["day_of_month"] = card["event_date"].dt.day.astype("Int64")

    saveback = df[df["type_norm"].eq("BENEFITS_SAVEBACK")].copy()
    saveback["saveback_credit_eur"] = saveback["amount"].fillna(0.0).clip(lower=0.0)
    saveback_monthly = (
        saveback.groupby("year_month", dropna=False)["saveback_credit_eur"].sum().reset_index()
        if not saveback.empty
        else pd.DataFrame(columns=["year_month", "saveback_credit_eur"])
    )

    monthly = card.groupby("year_month", dropna=False).agg(
        gross_card_outflow_eur=("gross_card_outflow_eur", "sum"),
        refunds_eur=("refund_eur", "sum"),
        net_card_outflow_eur=("net_card_outflow_eur", "sum"),
        gross_consumer_spend_eur=("consumer_gross_outflow_eur", "sum"),
        consumer_refunds_eur=("consumer_refund_eur", "sum"),
        net_consumer_spend_eur=("net_consumer_spend_eur", "sum"),
        purchase_count=("gross_card_outflow_eur", lambda values: int(pd.Series(values).gt(0).sum())),
        refund_count=("refund_eur", lambda values: int(pd.Series(values).gt(0).sum())),
        spending_days=("event_date", lambda values: int(pd.Series(values).nunique())),
        largest_purchase_eur=("gross_card_outflow_eur", "max"),
    ).reset_index()

    cashlike_monthly = (
        card[card["is_cashlike_or_financial"]]
        .groupby("year_month", dropna=False)["net_card_outflow_eur"]
        .sum().rename("cashlike_net_outflow_eur").reset_index()
    )
    international_monthly = (
        card[card["is_international"] & card["gross_card_outflow_eur"].gt(0)]
        .groupby("year_month", dropna=False)["gross_card_outflow_eur"]
        .sum().rename("international_gross_outflow_eur").reset_index()
    )
    purchase_amounts_monthly = (
        card[card["gross_card_outflow_eur"].gt(0)]
        .groupby("year_month", dropna=False)["gross_card_outflow_eur"]
        .agg(average_purchase_eur="mean", median_purchase_eur="median")
        .reset_index()
    )
    monthly = monthly.merge(cashlike_monthly, on="year_month", how="left")
    monthly = monthly.merge(international_monthly, on="year_month", how="left")
    monthly = monthly.merge(purchase_amounts_monthly, on="year_month", how="left")
    monthly = monthly.merge(saveback_monthly, on="year_month", how="left")
    monthly["month_start"] = pd.to_datetime(monthly["year_month"] + "-01", errors="coerce")
    monthly = monthly.sort_values("month_start").reset_index(drop=True)

    first_month = monthly["month_start"].min()
    last_month = monthly["month_start"].max()
    if pd.notna(first_month) and pd.notna(last_month):
        full_months = pd.DataFrame({"month_start": pd.date_range(first_month, last_month, freq="MS")})
        full_months["year_month"] = full_months["month_start"].dt.strftime("%Y-%m")
        monthly = full_months.merge(monthly.drop(columns=["month_start"]), on="year_month", how="left")
        monthly["month_start"] = pd.to_datetime(monthly["year_month"] + "-01")
    numeric_month_cols = [
        "gross_card_outflow_eur", "refunds_eur", "net_card_outflow_eur",
        "gross_consumer_spend_eur", "consumer_refunds_eur", "net_consumer_spend_eur",
        "purchase_count", "refund_count", "spending_days", "largest_purchase_eur",
        "cashlike_net_outflow_eur", "international_gross_outflow_eur",
        "average_purchase_eur", "median_purchase_eur", "saveback_credit_eur",
    ]
    for column in numeric_month_cols:
        if column not in monthly.columns:
            monthly[column] = 0.0
        monthly[column] = pd.to_numeric(monthly[column], errors="coerce").fillna(0.0)
    monthly["year"] = monthly["month_start"].dt.year.astype("Int64")
    monthly["month"] = monthly["month_start"].dt.month.astype("Int64")
    monthly["month_name"] = monthly["month_start"].dt.strftime("%b")
    monthly["rolling_3m_average_eur"] = monthly["net_consumer_spend_eur"].rolling(3, min_periods=1).mean()
    monthly["prior_month_change_pct"] = monthly["net_consumer_spend_eur"].pct_change().replace([np.inf, -np.inf], np.nan) * 100.0
    monthly["year_over_year_change_pct"] = monthly["net_consumer_spend_eur"].pct_change(12).replace([np.inf, -np.inf], np.nan) * 100.0

    first_expense_date = pd.to_datetime(card["event_date"].min(), errors="coerce")
    last_expense_date = pd.to_datetime(card["event_date"].max(), errors="coerce")
    monthly["is_complete_month"] = False
    if pd.notna(first_expense_date) and pd.notna(last_expense_date):
        monthly["is_complete_month"] = (
            monthly["month_start"].gt(first_expense_date.to_period("M").to_timestamp())
            & monthly["month_start"].lt(last_expense_date.to_period("M").to_timestamp())
        )

    categories = card.groupby("expense_category", dropna=False).agg(
        gross_outflow_eur=("gross_card_outflow_eur", "sum"),
        refunds_eur=("refund_eur", "sum"),
        net_spend_eur=("net_card_outflow_eur", "sum"),
        purchase_count=("gross_card_outflow_eur", lambda values: int(pd.Series(values).gt(0).sum())),
        refund_count=("refund_eur", lambda values: int(pd.Series(values).gt(0).sum())),
        merchant_count=("merchant_name", "nunique"),
    ).reset_index()
    gross_consumer_total = safe_float(
        categories.loc[
            ~categories["expense_category"].isin([EXPENSE_CASHLIKE_CATEGORY, EXPENSE_REFUND_CATEGORY]),
            "gross_outflow_eur",
        ].sum()
    )
    categories["gross_consumer_share_pct"] = np.where(
        categories["expense_category"].isin([EXPENSE_CASHLIKE_CATEGORY, EXPENSE_REFUND_CATEGORY]),
        np.nan,
        np.where(gross_consumer_total > 1e-12, categories["gross_outflow_eur"] / gross_consumer_total * 100.0, np.nan),
    )
    category_rank = {category: index for index, category in enumerate(EXPENSE_CATEGORY_ORDER)}
    categories["category_order"] = categories["expense_category"].map(category_rank).fillna(999)
    categories = categories.sort_values(["gross_outflow_eur", "category_order"], ascending=[False, True]).reset_index(drop=True)

    merchants = card.groupby(["merchant_name", "expense_category"], dropna=False).agg(
        gross_outflow_eur=("gross_card_outflow_eur", "sum"),
        refunds_eur=("refund_eur", "sum"),
        net_spend_eur=("net_card_outflow_eur", "sum"),
        purchase_count=("gross_card_outflow_eur", lambda values: int(pd.Series(values).gt(0).sum())),
        refund_count=("refund_eur", lambda values: int(pd.Series(values).gt(0).sum())),
        first_date=("event_date", "min"),
        last_date=("event_date", "max"),
    ).reset_index()
    merchant_purchase_means = (
        card[card["gross_card_outflow_eur"].gt(0)]
        .groupby(["merchant_name", "expense_category"], dropna=False)["gross_card_outflow_eur"]
        .agg(average_purchase_eur="mean", median_purchase_eur="median", largest_purchase_eur="max")
        .reset_index()
    )
    merchants = merchants.merge(merchant_purchase_means, on=["merchant_name", "expense_category"], how="left")
    consumer_merchant_total = safe_float(
        merchants.loc[~merchants["expense_category"].eq(EXPENSE_CASHLIKE_CATEGORY), "net_spend_eur"].sum()
    )
    merchants["consumer_net_spend_share_pct"] = np.where(
        merchants["expense_category"].eq(EXPENSE_CASHLIKE_CATEGORY),
        np.nan,
        np.where(consumer_merchant_total > 1e-12, merchants["net_spend_eur"] / consumer_merchant_total * 100.0, np.nan),
    )
    merchants = merchants.sort_values(["net_spend_eur", "gross_outflow_eur"], ascending=[False, False]).reset_index(drop=True)

    consumer_purchases = card[
        card["gross_card_outflow_eur"].gt(0) & ~card["is_cashlike_or_financial"]
    ].copy()
    weekdays = consumer_purchases.groupby("weekday", dropna=False).agg(
        gross_spend_eur=("gross_card_outflow_eur", "sum"),
        purchase_count=("source_row", "count"),
        average_purchase_eur=("gross_card_outflow_eur", "mean"),
        median_purchase_eur=("gross_card_outflow_eur", "median"),
    ).reset_index()
    weekday_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    weekdays["weekday_order"] = weekdays["weekday"].map({name: index for index, name in enumerate(weekday_order)})
    weekdays = weekdays.sort_values("weekday_order").reset_index(drop=True)

    daily = card.groupby("event_date", dropna=False).agg(
        net_consumer_spend_eur=("net_consumer_spend_eur", "sum"),
        gross_consumer_spend_eur=("consumer_gross_outflow_eur", "sum"),
        refunds_eur=("consumer_refund_eur", "sum"),
        purchase_count=("gross_card_outflow_eur", lambda values: int(pd.Series(values).gt(0).sum())),
    ).reset_index().sort_values("event_date")

    recurring_rows = []
    for (merchant_name, expense_category), group in consumer_purchases.groupby(["merchant_name", "expense_category"]):
        group = group.sort_values("event_date")
        distinct_months = int(group["year_month"].nunique())
        if len(group) < 3 or distinct_months < 3:
            continue
        dates = pd.to_datetime(group["event_date"], errors="coerce").dropna().sort_values()
        gaps = dates.diff().dt.days.dropna()
        median_gap = safe_float(gaps.median(), np.nan) if not gaps.empty else np.nan
        amounts = pd.to_numeric(group["gross_card_outflow_eur"], errors="coerce").dropna()
        average_amount = safe_float(amounts.mean(), np.nan)
        median_amount = safe_float(amounts.median(), np.nan)
        amount_cv = safe_div(safe_float(amounts.std(ddof=0), np.nan), average_amount) if average_amount > 1e-12 else np.nan
        frequency_ratio = safe_div(len(group), distinct_months)
        if (
            np.isfinite(median_gap) and 20 <= median_gap <= 40
            and np.isfinite(amount_cv) and amount_cv <= 0.20
            and frequency_ratio <= 1.6
        ):
            recurring_rows.append({
                "merchant_name": merchant_name,
                "expense_category": expense_category,
                "transactions": int(len(group)),
                "distinct_months": distinct_months,
                "first_date": dates.min(),
                "last_date": dates.max(),
                "median_gap_days": median_gap,
                "average_amount_eur": average_amount,
                "median_amount_eur": median_amount,
                "amount_coefficient_of_variation": amount_cv,
                "estimated_annualized_eur": median_amount * 12.0,
                "classification": "Recurring-payment candidate",
            })
    recurring_candidates = pd.DataFrame(recurring_rows)
    if not recurring_candidates.empty:
        recurring_candidates = recurring_candidates.sort_values(
            ["estimated_annualized_eur", "transactions"], ascending=[False, False]
        ).reset_index(drop=True)

    complete_months = monthly[monthly["is_complete_month"]].copy()
    average_complete_month = safe_float(complete_months["net_consumer_spend_eur"].mean(), np.nan) if not complete_months.empty else np.nan
    median_complete_month = safe_float(complete_months["net_consumer_spend_eur"].median(), np.nan) if not complete_months.empty else np.nan
    monthly_std = safe_float(complete_months["net_consumer_spend_eur"].std(ddof=0), np.nan) if len(complete_months) > 1 else np.nan
    monthly_cv = safe_div(monthly_std, average_complete_month) if average_complete_month > 1e-12 else np.nan
    latest_month_row = monthly.iloc[-1] if not monthly.empty else pd.Series(dtype=object)
    highest_month_row = (
        complete_months.sort_values("net_consumer_spend_eur", ascending=False).iloc[0]
        if not complete_months.empty
        else (monthly.sort_values("net_consumer_spend_eur", ascending=False).iloc[0] if not monthly.empty else pd.Series(dtype=object))
    )

    max_event_date = pd.to_datetime(card["event_date"].max(), errors="coerce")
    trailing_start = max_event_date - pd.Timedelta(days=29) if pd.notna(max_event_date) else pd.NaT
    trailing_30d_spend = safe_float(
        card.loc[card["event_date"].between(trailing_start, max_event_date), "net_consumer_spend_eur"].sum()
    ) if pd.notna(trailing_start) else np.nan

    consumer_merchant_rows = merchants[
        ~merchants["expense_category"].eq(EXPENSE_CASHLIKE_CATEGORY)
        & merchants["net_spend_eur"].gt(0)
    ].copy()
    merchant_weights = (
        consumer_merchant_rows["net_spend_eur"] / consumer_merchant_rows["net_spend_eur"].sum()
        if not consumer_merchant_rows.empty and consumer_merchant_rows["net_spend_eur"].sum() > 1e-12
        else pd.Series(dtype=float)
    )
    effective_merchants = safe_div(1.0, safe_float((merchant_weights ** 2).sum(), np.nan)) if not merchant_weights.empty else np.nan
    top5_merchant_share = safe_float(merchant_weights.nlargest(5).sum() * 100.0, np.nan) if not merchant_weights.empty else np.nan
    top_merchant = consumer_merchant_rows.iloc[0] if not consumer_merchant_rows.empty else pd.Series(dtype=object)
    category_chart_rows = categories[
        ~categories["expense_category"].isin([EXPENSE_CASHLIKE_CATEGORY, EXPENSE_REFUND_CATEGORY])
        & categories["gross_outflow_eur"].gt(0)
    ].copy()
    top_category = category_chart_rows.iloc[0] if not category_chart_rows.empty else pd.Series(dtype=object)

    total_gross_card = safe_float(card["gross_card_outflow_eur"].sum())
    total_refunds = safe_float(card["refund_eur"].sum())
    total_net_card = safe_float(card["net_card_outflow_eur"].sum())
    total_gross_consumer = safe_float(card["consumer_gross_outflow_eur"].sum())
    total_net_consumer = safe_float(card["net_consumer_spend_eur"].sum())
    total_cashlike = safe_float(card.loc[card["is_cashlike_or_financial"], "net_card_outflow_eur"].sum())
    saveback_total = safe_float(saveback["saveback_credit_eur"].sum()) if not saveback.empty else 0.0
    purchase_values = consumer_purchases["gross_card_outflow_eur"]
    largest_purchase_row = (
        consumer_purchases.sort_values("gross_card_outflow_eur", ascending=False).iloc[0]
        if not consumer_purchases.empty else pd.Series(dtype=object)
    )
    international_spend = safe_float(
        consumer_purchases.loc[consumer_purchases["is_international"], "gross_card_outflow_eur"].sum()
    )
    foreign_currency_spend = safe_float(
        consumer_purchases.loc[
            ~consumer_purchases["original_currency"].fillna("EUR").astype(str).str.upper().eq("EUR"),
            "gross_card_outflow_eur",
        ].sum()
    )

    metrics = {
        "expense_status": "OK",
        "expense_first_date": first_expense_date,
        "expense_last_date": last_expense_date,
        "expense_card_rows": int(len(card)),
        "expense_purchase_count": int(consumer_purchases.shape[0]),
        "expense_refund_count": int(card["refund_eur"].gt(0).sum()),
        "expense_gross_card_outflow_eur": total_gross_card,
        "expense_refunds_received_eur": total_refunds,
        "expense_net_card_outflow_eur": total_net_card,
        "expense_gross_consumer_spend_eur": total_gross_consumer,
        "expense_net_consumer_spend_eur": total_net_consumer,
        "expense_cashlike_financial_outflow_eur": total_cashlike,
        "expense_recorded_saveback_eur": saveback_total,
        "expense_saveback_to_gross_consumer_spend_pct": safe_div(saveback_total, total_gross_consumer) * 100.0,
        "expense_complete_months": int(len(complete_months)),
        "expense_average_complete_month_eur": average_complete_month,
        "expense_median_complete_month_eur": median_complete_month,
        "expense_monthly_spend_cv": monthly_cv,
        "expense_latest_month": latest_month_row.get("year_month", ""),
        "expense_latest_month_net_consumer_spend_eur": safe_float(latest_month_row.get("net_consumer_spend_eur"), np.nan),
        "expense_trailing_30d_net_consumer_spend_eur": trailing_30d_spend,
        "expense_highest_month": highest_month_row.get("year_month", ""),
        "expense_highest_month_net_consumer_spend_eur": safe_float(highest_month_row.get("net_consumer_spend_eur"), np.nan),
        "expense_average_purchase_eur": safe_float(purchase_values.mean(), np.nan) if not purchase_values.empty else np.nan,
        "expense_median_purchase_eur": safe_float(purchase_values.median(), np.nan) if not purchase_values.empty else np.nan,
        "expense_largest_purchase_eur": safe_float(largest_purchase_row.get("gross_card_outflow_eur"), np.nan),
        "expense_largest_purchase_merchant": largest_purchase_row.get("merchant_name", ""),
        "expense_international_gross_consumer_spend_eur": international_spend,
        "expense_foreign_currency_gross_consumer_spend_eur": foreign_currency_spend,
        "expense_top_category": top_category.get("expense_category", ""),
        "expense_top_category_gross_spend_eur": safe_float(top_category.get("gross_outflow_eur"), np.nan),
        "expense_top_category_share_pct": safe_float(top_category.get("gross_consumer_share_pct"), np.nan),
        "expense_top_merchant": top_merchant.get("merchant_name", ""),
        "expense_top_merchant_net_spend_eur": safe_float(top_merchant.get("net_spend_eur"), np.nan),
        "expense_top_merchant_share_pct": safe_float(top_merchant.get("consumer_net_spend_share_pct"), np.nan),
        "expense_top5_merchant_share_pct": top5_merchant_share,
        "expense_effective_merchant_count": effective_merchants,
        "expense_recurring_candidate_count": int(len(recurring_candidates)),
        "expense_scope_note": "Card purchases and card fees only; deposits, withdrawals, securities, dividends, interest and bank transfers excluded. Cash-like/financial card outflows are separated from consumer spending.",
    }

    narrative_rows = []
    if np.isfinite(average_complete_month):
        latest_value = safe_float(metrics["expense_latest_month_net_consumer_spend_eur"], np.nan)
        latest_change = safe_div(latest_value - average_complete_month, average_complete_month) * 100.0 if average_complete_month > 1e-12 else np.nan
        narrative_rows.append({
            "category": "Monthly pace",
            "severity": "WATCH" if np.isfinite(latest_change) and latest_change > 20 else "INFO",
            "title": f"Latest month: {metrics['expense_latest_month']}",
            "message": f"Net consumer spending is €{latest_value:,.2f}; the completed-month average is €{average_complete_month:,.2f}. The latest month may be partial.",
        })
    if str(metrics.get("expense_top_category", "")):
        narrative_rows.append({
            "category": "Category concentration",
            "severity": "INFO",
            "title": f"Largest purchase category: {metrics['expense_top_category']}",
            "message": f"It represents {safe_float(metrics.get('expense_top_category_share_pct')):.1f}% of gross consumer purchases (€{safe_float(metrics.get('expense_top_category_gross_spend_eur')):,.2f}).",
        })
    if str(metrics.get("expense_top_merchant", "")):
        narrative_rows.append({
            "category": "Merchant concentration",
            "severity": "INFO",
            "title": f"Largest merchant: {metrics['expense_top_merchant']}",
            "message": f"Net spending at this merchant is €{safe_float(metrics.get('expense_top_merchant_net_spend_eur')):,.2f}; the top five merchants account for {safe_float(metrics.get('expense_top5_merchant_share_pct')):.1f}% of consumer spending.",
        })
    if total_cashlike > 0:
        narrative_rows.append({
            "category": "Scope separation",
            "severity": "WATCH",
            "title": "Cash-like and financial card outflows are excluded from consumer spending",
            "message": f"€{total_cashlike:,.2f} is shown separately because card-funded deposits, cash access or financial services are not ordinary consumption.",
        })
    if saveback_total > 0:
        narrative_rows.append({
            "category": "Saveback",
            "severity": "GOOD",
            "title": "Recorded Saveback benefit",
            "message": f"The CSV contains €{saveback_total:,.2f} of Saveback credits, equivalent to {safe_float(metrics.get('expense_saveback_to_gross_consumer_spend_pct')):.2f}% of gross consumer card spending. It is an investment benefit, not a cash refund.",
        })
    if len(recurring_candidates):
        narrative_rows.append({
            "category": "Recurring payments",
            "severity": "INFO",
            "title": f"{len(recurring_candidates)} recurring-payment candidates detected",
            "message": "Candidates require at least three months, a roughly monthly interval and stable amounts. They are statistical candidates, not confirmed subscriptions.",
        })

    ledger_columns = [
        "event_date", "year_month", "weekday", "merchant_name", "expense_category",
        "transaction_kind", "type_norm", "gross_card_outflow_eur", "refund_eur",
        "net_card_outflow_eur", "net_consumer_spend_eur", "mcc_code_clean",
        "is_international", "original_amount", "original_currency", "fx_rate",
        "refund_match_confidence", "refund_matched_purchase_date", "source_row",
    ]
    ledger = card[[column for column in ledger_columns if column in card.columns]].copy()

    return {
        "metrics": metrics,
        "ledger": ledger,
        "monthly": monthly,
        "categories": categories,
        "merchants": merchants,
        "weekdays": weekdays,
        "daily": daily,
        "recurring_candidates": recurring_candidates,
        "narrative": pd.DataFrame(narrative_rows),
    }


def build_cash_ledger(df):
    cash = df.copy()
    cash["fee_paid_abs_eur"] = cash["fee"].abs().fillna(0)
    cash["tax_cash_effect_eur"] = cash["tax"].fillna(0)
    cash["net_cash_effect_proxy_eur"] = cash["amount"].fillna(0) + cash["fee"].fillna(0) + cash["tax"].fillna(0)
    return cash.groupby(["normalized_category", "type_norm"], dropna=False).agg(rows=("source_row", "count"), amount_sum_eur=("amount", "sum"), fee_abs_sum_eur=("fee_paid_abs_eur", "sum"), tax_sum_eur=("tax_cash_effect_eur", "sum"), net_cash_effect_proxy_eur=("net_cash_effect_proxy_eur", "sum")).reset_index().sort_values("amount_sum_eur", ascending=False)


def build_unsupported_activity_summary(df):
    unsupported = df[
        df["asset_class_clean"].isin({"CRYPTO"})
        | df["type_norm"].isin({"IPO_SUBSCRIPTION", "TAX_OPTIMIZATION", "GIFT", "REFERRAL"})
    ].copy()
    if unsupported.empty:
        return pd.DataFrame(columns=["asset_class_clean", "type_norm", "rows", "amount_sum_eur", "fee_sum_eur", "tax_sum_eur"])
    return unsupported.groupby(["asset_class_clean", "type_norm"], dropna=False).agg(rows=("source_row", "count"), amount_sum_eur=("amount", "sum"), fee_sum_eur=("fee", "sum"), tax_sum_eur=("tax", "sum")).reset_index().sort_values("rows", ascending=False)


def build_stockfund_concentration_table(combined, active_derivatives=None):
    """Company/security concentration table for active stock/fund holdings.

    Value weights use latest tracked stock/fund value. Cost weights use remaining FIFO acquisition basis.
    This is analytics only; it does not change accounting.
    """
    if combined is None or combined.empty:
        return pd.DataFrame(columns=[
            "security_name", "isin", "asset_class", "live_current_value_eur", "remaining_acquisition_cost_basis_eur",
            "value_weight_stockfund_pct", "value_weight_tracked_ex_cash_pct", "cost_weight_stockfund_pct",
            "cost_weight_total_open_risk_pct", "live_unrealized_pl_acquisition_basis_eur", "live_simple_return_acquisition_basis_pct"
        ])

    active = combined[combined["position_status"].eq("ACTIVE")].copy()
    if active.empty:
        return pd.DataFrame()

    for c in ["live_current_value_eur", "remaining_acquisition_cost_basis_eur", "live_unrealized_pl_acquisition_basis_eur", "live_simple_return_acquisition_basis_pct"]:
        if c not in active.columns:
            active[c] = np.nan

    stock_value_total = safe_float(active["live_current_value_eur"].sum())
    stock_cost_total = safe_float(active["remaining_acquisition_cost_basis_eur"].sum())

    derivative_value_total = 0.0
    derivative_cost_total = 0.0
    if active_derivatives is not None and not active_derivatives.empty:
        if "estimated_live_value_eur" in active_derivatives.columns:
            derivative_value_total = safe_float(active_derivatives["estimated_live_value_eur"].sum())
        if "open_cost_basis_eur" in active_derivatives.columns:
            derivative_cost_total = safe_float(active_derivatives["open_cost_basis_eur"].sum())

    tracked_value_total = stock_value_total + derivative_value_total
    total_open_risk = stock_cost_total + derivative_cost_total

    out = active[[
        "security_name", "isin", "asset_class", "current_quantity", "live_current_value_eur",
        "remaining_acquisition_cost_basis_eur", "remaining_user_funded_basis_eur",
        "live_unrealized_pl_acquisition_basis_eur", "live_simple_return_acquisition_basis_pct"
    ]].copy()

    out["value_weight_stockfund_pct"] = np.where(stock_value_total > 1e-12, out["live_current_value_eur"] / stock_value_total * 100.0, np.nan)
    out["value_weight_tracked_ex_cash_pct"] = np.where(tracked_value_total > 1e-12, out["live_current_value_eur"] / tracked_value_total * 100.0, np.nan)
    out["cost_weight_stockfund_pct"] = np.where(stock_cost_total > 1e-12, out["remaining_acquisition_cost_basis_eur"] / stock_cost_total * 100.0, np.nan)
    out["cost_weight_total_open_risk_pct"] = np.where(total_open_risk > 1e-12, out["remaining_acquisition_cost_basis_eur"] / total_open_risk * 100.0, np.nan)

    return out.sort_values("value_weight_tracked_ex_cash_pct", ascending=False).reset_index(drop=True)


# ============================================================
# 7B. Advanced portfolio insights
# ============================================================


def _finite_or_nan(value):
    try:
        out = float(value)
        return out if np.isfinite(out) else np.nan
    except Exception:
        return np.nan


def _weighted_average(values, weights):
    values = pd.to_numeric(pd.Series(values), errors="coerce")
    weights = pd.to_numeric(pd.Series(weights), errors="coerce").abs()
    valid = values.notna() & weights.notna() & (weights > 0)
    if not valid.any():
        return np.nan
    return float(np.average(values[valid], weights=weights[valid]))


def _weighted_median(values, weights):
    values = pd.to_numeric(pd.Series(values), errors="coerce")
    weights = pd.to_numeric(pd.Series(weights), errors="coerce").abs()
    valid = values.notna() & weights.notna() & (weights > 0)
    if not valid.any():
        return np.nan
    ordered = pd.DataFrame({"value": values[valid], "weight": weights[valid]}).sort_values("value")
    cutoff = safe_float(ordered["weight"].sum()) / 2.0
    cumulative = ordered["weight"].cumsum()
    return float(ordered.loc[cumulative.ge(cutoff), "value"].iloc[0])


def _xnpv(rate, values, dates):
    if rate <= -1:
        return np.nan
    base = pd.Timestamp(min(dates)).normalize()
    total = 0.0
    for value, dt in zip(values, dates):
        years = (pd.Timestamp(dt).normalize() - base).days / 365.0
        try:
            total += float(value) / ((1.0 + float(rate)) ** years)
        except (OverflowError, ZeroDivisionError, ValueError):
            return np.nan
    return float(total)


def _solve_xirr(cashflows):
    result = {
        "annualized_return_pct": np.nan,
        "status": "NO_CASHFLOWS",
        "root_count": 0,
        "selected_root": np.nan,
        "npv_residual_eur": np.nan,
        "cashflow_count": 0,
        "start_date": "",
        "end_date": "",
        "period_years": np.nan,
    }
    if cashflows is None or cashflows.empty:
        return result

    work = cashflows[["cashflow_date", "cashflow_eur"]].copy()
    work["cashflow_date"] = pd.to_datetime(work["cashflow_date"], errors="coerce").dt.normalize()
    work["cashflow_eur"] = pd.to_numeric(work["cashflow_eur"], errors="coerce")
    work = work.dropna().groupby("cashflow_date", as_index=False)["cashflow_eur"].sum()
    work = work[work["cashflow_eur"].abs() > 1e-10].sort_values("cashflow_date")
    if work.empty:
        return result

    values = work["cashflow_eur"].astype(float).tolist()
    dates = work["cashflow_date"].tolist()
    result.update({
        "cashflow_count": len(work),
        "start_date": str(work["cashflow_date"].min().date()),
        "end_date": str(work["cashflow_date"].max().date()),
        "period_years": round((work["cashflow_date"].max() - work["cashflow_date"].min()).days / 365.0, 4),
    })
    if not (any(v < 0 for v in values) and any(v > 0 for v in values)):
        result["status"] = "REQUIRES_POSITIVE_AND_NEGATIVE_CASHFLOW"
        return result

    # Search in log(1+r) space. This covers rates from approximately -99.99%
    # through +10,000% without requiring scipy or another dependency.
    x_grid = np.linspace(math.log(0.0001), math.log(101.0), 1800)
    rate_grid = np.exp(x_grid) - 1.0
    npvs = [_xnpv(rate, values, dates) for rate in rate_grid]
    roots = []

    def add_root(rate):
        if not np.isfinite(rate) or rate <= -1:
            return
        if all(abs(rate - existing) > 1e-7 * max(1.0, abs(existing)) for existing in roots):
            roots.append(float(rate))

    for idx in range(len(rate_grid) - 1):
        left_rate, right_rate = float(rate_grid[idx]), float(rate_grid[idx + 1])
        left_npv, right_npv = npvs[idx], npvs[idx + 1]
        if np.isfinite(left_npv) and abs(left_npv) < 1e-8:
            add_root(left_rate)
        if not (np.isfinite(left_npv) and np.isfinite(right_npv)):
            continue
        if left_npv * right_npv > 0:
            continue
        lo_x, hi_x = float(x_grid[idx]), float(x_grid[idx + 1])
        lo_val = left_npv
        for _ in range(160):
            mid_x = (lo_x + hi_x) / 2.0
            mid_rate = math.exp(mid_x) - 1.0
            mid_val = _xnpv(mid_rate, values, dates)
            if not np.isfinite(mid_val):
                break
            if abs(mid_val) <= 1e-10:
                lo_x = hi_x = mid_x
                break
            if lo_val * mid_val <= 0:
                hi_x = mid_x
            else:
                lo_x = mid_x
                lo_val = mid_val
        add_root(math.exp((lo_x + hi_x) / 2.0) - 1.0)

    roots.sort()
    result["root_count"] = len(roots)
    if not roots:
        result["status"] = "NO_VALID_XIRR_ROOT"
        return result

    # A 10% guess is conventional. When multiple roots exist, retain the root
    # closest to that guess but report MULTIPLE_ROOTS clearly.
    selected = min(roots, key=lambda r: abs(r - 0.10))
    residual = _xnpv(selected, values, dates)
    tolerance = max(0.01, sum(abs(v) for v in values) * 1e-9)
    if not np.isfinite(residual) or abs(residual) > tolerance:
        result["status"] = "XIRR_RESIDUAL_CHECK_FAILED"
        return result

    result.update({
        "annualized_return_pct": selected * 100.0,
        "selected_root": selected,
        "npv_residual_eur": residual,
        "status": "MULTIPLE_ROOTS" if len(roots) > 1 else "OK",
    })
    return result


def _build_mwr_cashflows(
    trades,
    dividends,
    derivative_ledger,
    stock_active,
    active_derivatives,
    max_date,
    include_derivatives=False,
    user_funded=False,
):
    rows = []
    scope = "TRACKED_INVESTMENTS" if include_derivatives else "STOCK_FUND"
    basis_mode = "USER_FUNDED" if user_funded else "ACQUISITION"

    if trades is not None and not trades.empty:
        for _, row in trades.iterrows():
            typ = str(row.get("type_norm", ""))
            dt = row.get("event_date")
            if typ in STOCK_ACQUISITION_TYPES:
                outflow = safe_float(row.get("net_buy_cash_outflow_eur"))
                if user_funded:
                    outflow = max(0.0, outflow - safe_float(row.get("matched_promo_credit_eur")))
                amount = -outflow
                label = "Stock/fund purchase"
            elif typ == "SELL":
                amount = safe_float(row.get("net_sell_cash_inflow_eur"))
                label = "Stock/fund sale"
            else:
                continue
            rows.append({
                "scope": scope,
                "basis_mode": basis_mode,
                "cashflow_date": dt,
                "cashflow_eur": amount,
                "cashflow_type": typ,
                "security_name": row.get("security_name", ""),
                "isin": row.get("isin", ""),
                "note": label,
            })

    if dividends is not None and not dividends.empty:
        for _, row in dividends.iterrows():
            amount = safe_float(row.get("net_dividend_eur"))
            if abs(amount) <= 1e-12:
                continue
            rows.append({
                "scope": scope,
                "basis_mode": basis_mode,
                "cashflow_date": row.get("payment_date"),
                "cashflow_eur": amount,
                "cashflow_type": "DIVIDEND_NET",
                "security_name": row.get("security_name", ""),
                "isin": row.get("isin", ""),
                "note": "Net dividend/distribution",
            })

    stock_complete = True
    stock_terminal = 0.0
    if stock_active is not None and not stock_active.empty:
        live = pd.to_numeric(stock_active.get("live_current_value_eur"), errors="coerce")
        stock_complete = bool(live.notna().all())
        stock_terminal = safe_float(live.sum()) if stock_complete else np.nan

    derivative_complete = True
    derivative_terminal = 0.0
    if include_derivatives:
        if derivative_ledger is not None and not derivative_ledger.empty:
            for _, row in derivative_ledger.iterrows():
                amount = safe_float(row.get("cashflow_eur"))
                if abs(amount) <= 1e-12:
                    continue
                rows.append({
                    "scope": scope,
                    "basis_mode": basis_mode,
                    "cashflow_date": row.get("event_date"),
                    "cashflow_eur": amount,
                    "cashflow_type": str(row.get("type_norm", "DERIVATIVE")),
                    "security_name": row.get("security_name", ""),
                    "isin": row.get("isin", ""),
                    "note": "Derivative cash flow",
                })
        if active_derivatives is not None and not active_derivatives.empty:
            live = pd.to_numeric(active_derivatives.get("estimated_live_value_eur"), errors="coerce")
            derivative_complete = bool(live.notna().all())
            derivative_terminal = safe_float(live.sum()) if derivative_complete else np.nan

    terminal_complete = stock_complete and (derivative_complete if include_derivatives else True)
    terminal_value = stock_terminal + derivative_terminal if terminal_complete else np.nan
    if terminal_complete and pd.notna(max_date):
        rows.append({
            "scope": scope,
            "basis_mode": basis_mode,
            "cashflow_date": pd.Timestamp(max_date).normalize(),
            "cashflow_eur": terminal_value,
            "cashflow_type": "TERMINAL_VALUE",
            "security_name": "All active tracked positions",
            "isin": "",
            "note": "Period-end live/indicative value",
        })

    cashflows = pd.DataFrame(rows, columns=[
        "scope", "basis_mode", "cashflow_date", "cashflow_eur",
        "cashflow_type", "security_name", "isin", "note",
    ])
    if not terminal_complete:
        result = {
            "annualized_return_pct": np.nan,
            "status": "INCOMPLETE_LIVE_VALUATION",
            "root_count": 0,
            "selected_root": np.nan,
            "npv_residual_eur": np.nan,
            "cashflow_count": len(cashflows),
            "start_date": str(pd.to_datetime(cashflows["cashflow_date"], errors="coerce").min().date()) if not cashflows.empty else "",
            "end_date": str(pd.Timestamp(max_date).date()) if pd.notna(max_date) else "",
            "period_years": np.nan,
        }
    else:
        result = _solve_xirr(cashflows)
    result.update({
        "scope": scope,
        "basis_mode": basis_mode,
        "terminal_value_eur": terminal_value,
        "valuation_complete": terminal_complete,
    })
    return result, cashflows



def build_lifetime_performance(
    df,
    trades,
    dividends,
    interest,
    combined,
    derivative_ledger,
    derivative_positions,
    active_derivatives,
    advanced_metrics,
):
    """Build an auditable since-inception capital and profit bridge.

    Bank deposits, withdrawals, card activity and transfers are deliberately
    excluded. Only investment cash flows are modelled: stock/fund and derivative
    outflows, sales/settlements, net dividends, net interest and matched security
    promotional credits.
    """
    rows = []
    event_order = 0

    def add_event(
        event_date,
        asset_scope,
        cashflow_type,
        security_name="",
        isin="",
        acquisition_outflow_eur=0.0,
        user_funded_outflow_eur=0.0,
        core_recovery_eur=0.0,
        interest_recovery_eur=0.0,
        matched_promo_credit_eur=0.0,
        note="",
    ):
        nonlocal event_order
        event_order += 1
        dt = pd.to_datetime(event_date, errors="coerce")
        rows.append({
            "event_order": event_order,
            "event_date": dt.normalize() if pd.notna(dt) else pd.NaT,
            "asset_scope": asset_scope,
            "cashflow_type": cashflow_type,
            "security_name": clean_str(security_name),
            "isin": clean_str(isin),
            "acquisition_outflow_eur": max(0.0, safe_float(acquisition_outflow_eur)),
            "user_funded_outflow_eur": max(0.0, safe_float(user_funded_outflow_eur)),
            "core_recovery_eur": safe_float(core_recovery_eur),
            "interest_recovery_eur": safe_float(interest_recovery_eur),
            "matched_promo_credit_eur": max(0.0, safe_float(matched_promo_credit_eur)),
            "note": note,
        })

    if trades is not None and not trades.empty:
        for _, row in trades.iterrows():
            typ = str(row.get("type_norm", "")).upper()
            if typ in STOCK_ACQUISITION_TYPES:
                acquisition_outflow = max(0.0, safe_float(row.get("net_buy_cash_outflow_eur")))
                promo_credit = min(
                    acquisition_outflow,
                    max(0.0, safe_float(row.get("matched_promo_credit_eur"))),
                )
                user_outflow = max(0.0, acquisition_outflow - promo_credit)
                add_event(
                    row.get("event_date"),
                    "STOCK_FUND",
                    typ,
                    row.get("security_name", ""),
                    row.get("isin", ""),
                    acquisition_outflow_eur=acquisition_outflow,
                    user_funded_outflow_eur=user_outflow,
                    matched_promo_credit_eur=promo_credit,
                    note=(
                        "Dividend proceeds reinvested into a new FIFO acquisition lot"
                        if typ == "DIVIDEND_REINVESTMENT"
                        else "Stock/fund purchase including recorded fees and buy-side taxes"
                    ),
                )
            elif typ == "SELL":
                add_event(
                    row.get("event_date"),
                    "STOCK_FUND",
                    "SELL",
                    row.get("security_name", ""),
                    row.get("isin", ""),
                    core_recovery_eur=safe_float(row.get("net_sell_cash_inflow_eur")),
                    note="Net stock/fund sale proceeds after recorded fees and taxes",
                )

    if derivative_ledger is not None and not derivative_ledger.empty:
        for _, row in derivative_ledger.iterrows():
            typ = str(row.get("type_norm", "")).upper()
            cashflow = safe_float(row.get("cashflow_eur"))
            outflow = 0.0
            recovery = 0.0
            if typ == "BUY":
                outflow = max(0.0, -cashflow)
                if outflow <= 1e-12:
                    # Defensive fallback for a malformed positive BUY amount.
                    outflow = max(
                        0.0,
                        abs(safe_float(row.get("amount"))) + safe_float(row.get("fee_paid_eur")),
                    )
            elif cashflow >= 0:
                recovery = cashflow
            else:
                # Exercise fees or other derivative cash outflows remain capital deployed.
                outflow = -cashflow

            note_map = {
                "BUY": "Derivative purchase including recorded fee",
                "SELL": "Net derivative sale proceeds after recorded fee",
                "TILG": "Derivative cash settlement / redemption",
                "WARRANT_EXERCISE": "Derivative exercise or expiry cash adjustment",
            }
            add_event(
                row.get("event_date"),
                "DERIVATIVE",
                typ or "DERIVATIVE_EVENT",
                row.get("security_name", ""),
                row.get("isin", ""),
                acquisition_outflow_eur=outflow,
                user_funded_outflow_eur=outflow,
                core_recovery_eur=recovery,
                note=note_map.get(typ, "Derivative investment cash flow"),
            )

    if dividends is not None and not dividends.empty:
        for _, row in dividends.iterrows():
            add_event(
                row.get("payment_date"),
                "INCOME",
                "DIVIDEND_NET",
                row.get("security_name", ""),
                row.get("isin", ""),
                core_recovery_eur=safe_float(row.get("net_dividend_eur")),
                note="Net dividend/distribution after withholding and refunds",
            )

    if interest is not None and not interest.empty:
        for _, row in interest.iterrows():
            add_event(
                row.get("payment_date"),
                "INTEREST",
                "INTEREST_NET",
                "Trade Republic cash interest",
                "",
                interest_recovery_eur=safe_float(row.get("net_interest_eur")),
                note="Net interest; shown separately because it is generated by cash, not securities",
            )

    ledger_columns = [
        "event_order", "event_date", "year", "year_month", "asset_scope",
        "cashflow_type", "security_name", "isin", "acquisition_outflow_eur",
        "user_funded_outflow_eur", "core_recovery_eur", "interest_recovery_eur",
        "ecosystem_recovery_eur", "matched_promo_credit_eur",
        "net_commitment_change_acquisition_core_eur",
        "net_commitment_change_acquisition_ecosystem_eur",
        "net_commitment_change_user_core_eur",
        "net_commitment_change_user_ecosystem_eur",
        "cumulative_net_committed_acquisition_core_eur",
        "cumulative_net_committed_acquisition_ecosystem_eur",
        "cumulative_net_committed_user_core_eur",
        "cumulative_net_committed_user_ecosystem_eur", "note",
    ]
    ledger = pd.DataFrame(rows)
    if ledger.empty:
        ledger = pd.DataFrame(columns=ledger_columns)
    else:
        ledger = ledger.sort_values(["event_date", "event_order"], na_position="last").reset_index(drop=True)
        ledger["year"] = ledger["event_date"].dt.year.astype("Int64")
        ledger["year_month"] = ledger["event_date"].dt.strftime("%Y-%m")
        ledger["ecosystem_recovery_eur"] = ledger["core_recovery_eur"] + ledger["interest_recovery_eur"]
        ledger["net_commitment_change_acquisition_core_eur"] = ledger["acquisition_outflow_eur"] - ledger["core_recovery_eur"]
        ledger["net_commitment_change_acquisition_ecosystem_eur"] = ledger["acquisition_outflow_eur"] - ledger["ecosystem_recovery_eur"]
        ledger["net_commitment_change_user_core_eur"] = ledger["user_funded_outflow_eur"] - ledger["core_recovery_eur"]
        ledger["net_commitment_change_user_ecosystem_eur"] = ledger["user_funded_outflow_eur"] - ledger["ecosystem_recovery_eur"]
        ledger["cumulative_net_committed_acquisition_core_eur"] = ledger["net_commitment_change_acquisition_core_eur"].cumsum()
        ledger["cumulative_net_committed_acquisition_ecosystem_eur"] = ledger["net_commitment_change_acquisition_ecosystem_eur"].cumsum()
        ledger["cumulative_net_committed_user_core_eur"] = ledger["net_commitment_change_user_core_eur"].cumsum()
        ledger["cumulative_net_committed_user_ecosystem_eur"] = ledger["net_commitment_change_user_ecosystem_eur"].cumsum()
        ledger = ledger[[c for c in ledger_columns if c in ledger.columns]]

    def ledger_sum(mask, column):
        if ledger.empty or column not in ledger.columns:
            return 0.0
        return safe_float(ledger.loc[mask, column].sum())

    stock_buy_mask = ledger.get("asset_scope", pd.Series(dtype=str)).eq("STOCK_FUND") & ledger.get("cashflow_type", pd.Series(dtype=str)).eq("BUY")
    derivative_buy_mask = ledger.get("asset_scope", pd.Series(dtype=str)).eq("DERIVATIVE") & ledger.get("cashflow_type", pd.Series(dtype=str)).eq("BUY")
    stock_sell_mask = ledger.get("asset_scope", pd.Series(dtype=str)).eq("STOCK_FUND") & ledger.get("cashflow_type", pd.Series(dtype=str)).eq("SELL")
    derivative_sell_mask = ledger.get("asset_scope", pd.Series(dtype=str)).eq("DERIVATIVE") & ledger.get("cashflow_type", pd.Series(dtype=str)).eq("SELL")
    derivative_tilg_mask = ledger.get("asset_scope", pd.Series(dtype=str)).eq("DERIVATIVE") & ledger.get("cashflow_type", pd.Series(dtype=str)).eq("TILG")
    derivative_other_mask = ledger.get("asset_scope", pd.Series(dtype=str)).eq("DERIVATIVE") & ~ledger.get("cashflow_type", pd.Series(dtype=str)).isin(["BUY", "SELL", "TILG"])
    dividend_mask = ledger.get("cashflow_type", pd.Series(dtype=str)).eq("DIVIDEND_NET")
    interest_mask = ledger.get("cashflow_type", pd.Series(dtype=str)).eq("INTEREST_NET")

    stock_purchase_outflows = ledger_sum(stock_buy_mask, "acquisition_outflow_eur")
    stock_user_purchase_outflows = ledger_sum(stock_buy_mask, "user_funded_outflow_eur")
    derivative_purchase_outflows = ledger_sum(derivative_buy_mask, "acquisition_outflow_eur")
    derivative_other_outflows = ledger_sum(derivative_other_mask, "acquisition_outflow_eur")
    gross_investment_outflows = safe_float(ledger.get("acquisition_outflow_eur", pd.Series(dtype=float)).sum())
    user_funded_investment_outflows = safe_float(ledger.get("user_funded_outflow_eur", pd.Series(dtype=float)).sum())
    matched_promo_credits = safe_float(ledger.get("matched_promo_credit_eur", pd.Series(dtype=float)).sum())

    stock_sale_recovery = ledger_sum(stock_sell_mask, "core_recovery_eur")
    derivative_sale_recovery = ledger_sum(derivative_sell_mask, "core_recovery_eur")
    derivative_tilg_recovery = ledger_sum(derivative_tilg_mask, "core_recovery_eur")
    derivative_other_recovery = ledger_sum(derivative_other_mask, "core_recovery_eur")
    net_dividend_recovery = ledger_sum(dividend_mask, "core_recovery_eur")
    net_interest_recovery = ledger_sum(interest_mask, "interest_recovery_eur")
    core_recovery = safe_float(ledger.get("core_recovery_eur", pd.Series(dtype=float)).sum())
    ecosystem_recovery = safe_float(ledger.get("ecosystem_recovery_eur", pd.Series(dtype=float)).sum())

    net_committed_acquisition_core = gross_investment_outflows - core_recovery
    net_committed_acquisition_ecosystem = gross_investment_outflows - ecosystem_recovery
    net_committed_user_core = user_funded_investment_outflows - core_recovery
    net_committed_user_ecosystem = user_funded_investment_outflows - ecosystem_recovery

    def series_max(column):
        if ledger.empty or column not in ledger.columns:
            return 0.0
        values = pd.to_numeric(ledger[column], errors="coerce").dropna()
        return max(0.0, safe_float(values.max())) if not values.empty else 0.0

    peak_acquisition_core = series_max("cumulative_net_committed_acquisition_core_eur")
    peak_acquisition_ecosystem = series_max("cumulative_net_committed_acquisition_ecosystem_eur")
    peak_user_core = series_max("cumulative_net_committed_user_core_eur")
    peak_user_ecosystem = series_max("cumulative_net_committed_user_ecosystem_eur")

    stock_active = combined[combined["position_status"].eq("ACTIVE")].copy() if combined is not None and not combined.empty else pd.DataFrame()
    stock_live = pd.to_numeric(stock_active.get("live_current_value_eur"), errors="coerce") if not stock_active.empty else pd.Series(dtype=float)
    stock_valuation_complete = bool(stock_active.empty or stock_live.notna().all())
    stock_partial_value = safe_float(stock_live.sum())

    derivative_live = pd.to_numeric(active_derivatives.get("estimated_live_value_eur"), errors="coerce") if active_derivatives is not None and not active_derivatives.empty else pd.Series(dtype=float)
    derivative_valuation_complete = bool(active_derivatives is None or active_derivatives.empty or derivative_live.notna().all())
    derivative_partial_value = safe_float(derivative_live.sum())
    tracked_valuation_complete = stock_valuation_complete and derivative_valuation_complete
    partial_tracked_value = stock_partial_value + derivative_partial_value
    tracked_value = partial_tracked_value if tracked_valuation_complete else np.nan

    stock_open_pl = safe_float(stock_active.get("live_unrealized_pl_acquisition_basis_eur", pd.Series(dtype=float)).sum()) if stock_valuation_complete else np.nan
    derivative_open_pl = safe_float(active_derivatives.get("estimated_unrealized_pl_eur", pd.Series(dtype=float)).sum()) if derivative_valuation_complete and active_derivatives is not None else np.nan
    stock_realized_pl = safe_float(combined.get("fifo_realized_pl_acquisition_basis_eur", pd.Series(dtype=float)).sum()) if combined is not None and not combined.empty else 0.0
    derivative_realized_pl = safe_float(derivative_positions.get("realized_pl_with_tilg_eur", pd.Series(dtype=float)).sum()) if derivative_positions is not None and not derivative_positions.empty else 0.0

    cashflow_profit_ex_interest = tracked_value + core_recovery - gross_investment_outflows if tracked_valuation_complete else np.nan
    lifetime_economic_profit = tracked_value + ecosystem_recovery - gross_investment_outflows if tracked_valuation_complete else np.nan
    personal_lifetime_benefit = tracked_value + ecosystem_recovery - user_funded_investment_outflows if tracked_valuation_complete else np.nan
    component_profit = (
        stock_open_pl + derivative_open_pl + stock_realized_pl + derivative_realized_pl
        + net_dividend_recovery + net_interest_recovery
    ) if tracked_valuation_complete else np.nan
    reconciliation_difference = lifetime_economic_profit - component_profit if tracked_valuation_complete else np.nan

    simple_return_gross = safe_div(lifetime_economic_profit, gross_investment_outflows) * 100.0 if gross_investment_outflows > 1e-12 and np.isfinite(_finite_or_nan(lifetime_economic_profit)) else np.nan
    simple_return_user = safe_div(personal_lifetime_benefit, user_funded_investment_outflows) * 100.0 if user_funded_investment_outflows > 1e-12 and np.isfinite(_finite_or_nan(personal_lifetime_benefit)) else np.nan
    return_on_net_committed = safe_div(lifetime_economic_profit, net_committed_acquisition_ecosystem) * 100.0 if net_committed_acquisition_ecosystem > 1e-12 and np.isfinite(_finite_or_nan(lifetime_economic_profit)) else np.nan
    personal_return_on_net_committed = safe_div(personal_lifetime_benefit, net_committed_user_ecosystem) * 100.0 if net_committed_user_ecosystem > 1e-12 and np.isfinite(_finite_or_nan(personal_lifetime_benefit)) else np.nan
    capital_efficiency_peak = safe_div(personal_lifetime_benefit, peak_user_ecosystem) * 100.0 if peak_user_ecosystem > 1e-12 and np.isfinite(_finite_or_nan(personal_lifetime_benefit)) else np.nan
    recovery_ratio_user = safe_div(ecosystem_recovery, user_funded_investment_outflows) * 100.0 if user_funded_investment_outflows > 1e-12 else np.nan

    if tracked_valuation_complete:
        valuation_status = "COMPLETE"
    elif not stock_valuation_complete and not derivative_valuation_complete:
        valuation_status = "INCOMPLETE_STOCK_AND_DERIVATIVE_VALUATION"
    elif not stock_valuation_complete:
        valuation_status = "INCOMPLETE_STOCK_VALUATION"
    else:
        valuation_status = "INCOMPLETE_DERIVATIVE_VALUATION"

    if not tracked_valuation_complete:
        profit_status = valuation_status
    elif abs(safe_float(reconciliation_difference)) > 0.05:
        profit_status = "RECONCILIATION_WARNING"
    else:
        profit_status = "OK"

    excluded_cash_rows = 0
    excluded_cash_net = 0.0
    if df is not None and not df.empty and "normalized_category" in df.columns:
        excluded_mask = df["normalized_category"].isin(["Cash deposit", "Cash withdrawal", "Card payment"])
        excluded_cash_rows = int(excluded_mask.sum())
        excluded_cash_net = safe_float(df.loc[excluded_mask, "amount"].fillna(0).sum())

    metrics = {
        "lifetime_valuation_status": valuation_status,
        "lifetime_profit_status": profit_status,
        "lifetime_stock_purchase_outflows_eur": stock_purchase_outflows,
        "lifetime_stock_user_funded_purchase_outflows_eur": stock_user_purchase_outflows,
        "lifetime_derivative_purchase_outflows_eur": derivative_purchase_outflows,
        "lifetime_other_investment_outflows_eur": derivative_other_outflows,
        "lifetime_gross_investment_outflows_eur": gross_investment_outflows,
        "lifetime_user_funded_investment_outflows_eur": user_funded_investment_outflows,
        "lifetime_matched_promo_credits_eur": matched_promo_credits,
        "lifetime_stock_sale_recovery_eur": stock_sale_recovery,
        "lifetime_derivative_sale_recovery_eur": derivative_sale_recovery,
        "lifetime_derivative_tilg_recovery_eur": derivative_tilg_recovery,
        "lifetime_derivative_other_recovery_eur": derivative_other_recovery,
        "lifetime_net_dividend_recovery_eur": net_dividend_recovery,
        "lifetime_net_interest_recovery_eur": net_interest_recovery,
        "lifetime_core_recovery_eur": core_recovery,
        "lifetime_ecosystem_recovery_eur": ecosystem_recovery,
        "lifetime_net_committed_acquisition_core_eur": net_committed_acquisition_core,
        "lifetime_net_committed_acquisition_ecosystem_eur": net_committed_acquisition_ecosystem,
        "lifetime_net_committed_user_core_eur": net_committed_user_core,
        "lifetime_net_committed_user_ecosystem_eur": net_committed_user_ecosystem,
        "lifetime_peak_net_committed_acquisition_core_eur": peak_acquisition_core,
        "lifetime_peak_net_committed_acquisition_ecosystem_eur": peak_acquisition_ecosystem,
        "lifetime_peak_net_committed_user_core_eur": peak_user_core,
        "lifetime_peak_net_committed_user_ecosystem_eur": peak_user_ecosystem,
        "lifetime_partial_tracked_open_value_eur": partial_tracked_value,
        "lifetime_current_tracked_open_value_eur": tracked_value,
        "lifetime_stock_open_pl_eur": stock_open_pl,
        "lifetime_derivative_open_pl_eur": derivative_open_pl,
        "lifetime_stock_realized_pl_eur": stock_realized_pl,
        "lifetime_derivative_realized_pl_all_positions_eur": derivative_realized_pl,
        "lifetime_profit_ex_interest_eur": cashflow_profit_ex_interest,
        "lifetime_economic_profit_eur": lifetime_economic_profit,
        "lifetime_personal_benefit_including_promos_eur": personal_lifetime_benefit,
        "lifetime_component_profit_eur": component_profit,
        "lifetime_profit_reconciliation_difference_eur": reconciliation_difference,
        "lifetime_simple_return_on_gross_outflows_pct": simple_return_gross,
        "lifetime_simple_return_on_user_funded_outflows_pct": simple_return_user,
        "lifetime_return_on_net_committed_acquisition_pct": return_on_net_committed,
        "lifetime_return_on_net_committed_user_pct": personal_return_on_net_committed,
        "lifetime_capital_efficiency_on_peak_user_commitment_pct": capital_efficiency_peak,
        "lifetime_recovery_ratio_on_user_funded_outflows_pct": recovery_ratio_user,
        "lifetime_tracked_investments_mwr_pct": advanced_metrics.get("tracked_investments_mwr_pct", np.nan),
        "lifetime_tracked_investments_mwr_status": advanced_metrics.get("tracked_investments_mwr_status", ""),
        "lifetime_excluded_cash_card_transfer_rows": excluded_cash_rows,
        "lifetime_excluded_cash_card_transfer_net_eur": excluded_cash_net,
        "lifetime_cashflow_start_date": str(ledger["event_date"].min().date()) if not ledger.empty and ledger["event_date"].notna().any() else "",
        "lifetime_cashflow_end_date": str(ledger["event_date"].max().date()) if not ledger.empty and ledger["event_date"].notna().any() else "",
    }

    bridge_rows = [
        (1, "Open stock/fund gain or loss", stock_open_pl, "OPEN_P_L", True, "Live value minus remaining FIFO acquisition basis"),
        (2, "Open derivative gain or loss", derivative_open_pl, "OPEN_P_L", True, "Indicative value minus open derivative FIFO cost basis"),
        (3, "Realized stock/fund P/L", stock_realized_pl, "REALIZED_P_L", True, "FIFO realized result across all stock/fund positions"),
        (4, "Realized derivative P/L incl. TILG", derivative_realized_pl, "REALIZED_P_L", True, "Includes realized results from active and fully closed derivative positions"),
        (5, "Net dividends", net_dividend_recovery, "INCOME", True, "After recorded withholding and refunds"),
        (6, "Net interest", net_interest_recovery, "INTEREST", True, "Cash-balance income; separate from security performance"),
        (7, "Cash-flow/accounting reconciliation", reconciliation_difference, "RECONCILIATION", True, "Normally approximately zero; captures unallocated cash-flow differences"),
        (8, "Lifetime economic profit", lifetime_economic_profit, "TOTAL", False, "Current tracked value + recovered cash - all investment outflows"),
        (9, "Matched promotional credits", matched_promo_credits, "PROMOTION", False, "Reduces personal cash funded but is excluded from investment P/L"),
        (10, "Personal lifetime benefit incl. promos", personal_lifetime_benefit, "TOTAL_PERSONAL", False, "Lifetime economic profit plus matched promotional funding"),
    ]
    profit_bridge = pd.DataFrame(bridge_rows, columns=[
        "sort_order", "component", "amount_eur", "category", "included_in_lifetime_profit", "note",
    ])

    return_rows = [
        ("Lifetime economic profit", lifetime_economic_profit, "EUR", "Absolute wealth created by tracked investments, dividends and interest; excludes bank transfers", profit_status),
        ("Personal lifetime benefit incl. promos", personal_lifetime_benefit, "EUR", "Economic profit plus matched promotional purchase funding", profit_status),
        ("Simple return on gross investment outflows", simple_return_gross, "%", "Lifetime economic profit divided by every euro of investment outflow; recycled capital can be counted repeatedly", profit_status),
        ("Simple return on user-funded outflows", simple_return_user, "%", "Personal lifetime benefit divided by purchase/outflow cash funded by the user after matched promos", profit_status),
        ("Return on current net committed capital", return_on_net_committed, "%", "Secondary ratio only; becomes unstable when recovered cash makes the denominator small or non-positive", "N/A_IF_NON_POSITIVE_DENOMINATOR" if not np.isfinite(_finite_or_nan(return_on_net_committed)) else "OK"),
        ("Capital efficiency on peak user commitment", capital_efficiency_peak, "%", "Personal lifetime benefit divided by the highest cumulative user capital ever tied up", profit_status),
        ("Tracked-investment MWR / XIRR", advanced_metrics.get("tracked_investments_mwr_pct", np.nan), "% annualized", "Timing-aware annualized return for stocks/funds and derivatives; excludes cash interest because cash principal is not modelled", advanced_metrics.get("tracked_investments_mwr_status", "")),
    ]
    return_definitions = pd.DataFrame(return_rows, columns=[
        "metric", "value", "unit", "interpretation", "status",
    ])

    return {
        "metrics": metrics,
        "cashflow_ledger": ledger,
        "profit_bridge": profit_bridge,
        "return_definitions": return_definitions,
    }

def _aggregate_realized_events(realized, derivative_realized):
    rows = []
    if realized is not None and not realized.empty:
        valid = realized[
            realized.get("source_buy_trade_id", "").astype(str).ne("UNMATCHED_SELL")
            & pd.to_numeric(realized.get("realized_pl_acquisition_basis_eur"), errors="coerce").notna()
        ].copy()
        # Administrative/write-off closes belong in canonical realized FIFO,
        # not in metrics that purport to describe discretionary sell decisions.
        if "is_discretionary_sale" in valid.columns:
            valid = valid[valid["is_discretionary_sale"].fillna(True).eq(True)].copy()
        for trade_id, group in valid.groupby("sell_trade_id", dropna=False):
            cost = safe_float(group["allocated_acquisition_cost_basis_eur"].sum())
            proceeds = safe_float(group["allocated_net_sell_proceeds_eur"].sum())
            pl = safe_float(group["realized_pl_acquisition_basis_eur"].sum())
            rows.append({
                "close_event_id": str(trade_id),
                "event_date": group["sell_date"].max(),
                "security_name": group["security_name"].dropna().iloc[-1] if group["security_name"].notna().any() else "",
                "isin": group["isin"].dropna().iloc[-1] if group["isin"].notna().any() else "",
                "instrument_type": str(group["asset_class"].dropna().iloc[-1]) if "asset_class" in group and group["asset_class"].notna().any() else "STOCK/FUND",
                "close_type": str(group["close_type"].dropna().iloc[-1]) if "close_type" in group and group["close_type"].notna().any() else "SELL",
                "quantity_closed": safe_float(group["quantity_sold"].sum()),
                "net_proceeds_eur": proceeds,
                "cost_basis_eur": cost,
                "realized_pl_eur": pl,
                "realized_return_pct": safe_div(pl, cost) * 100.0 if cost > 1e-12 else np.nan,
                "weighted_holding_days": _weighted_average(group["holding_days"], group["allocated_acquisition_cost_basis_eur"]),
            })

    if derivative_realized is not None and not derivative_realized.empty:
        valid = derivative_realized[
            derivative_realized.get("source_buy_trade_id", "").astype(str).ne("UNMATCHED_SELL")
            & pd.to_numeric(derivative_realized.get("realized_pl_eur"), errors="coerce").notna()
        ].copy()
        for trade_id, group in valid.groupby("sell_trade_id", dropna=False):
            cost = safe_float(group["allocated_cost_basis_eur"].sum())
            proceeds = safe_float(group["allocated_net_proceeds_eur"].sum())
            pl = safe_float(group["realized_pl_eur"].sum())
            rows.append({
                "close_event_id": str(trade_id),
                "event_date": group["sell_date"].max(),
                "security_name": group["security_name"].dropna().iloc[-1] if group["security_name"].notna().any() else "",
                "isin": group["isin"].dropna().iloc[-1] if group["isin"].notna().any() else "",
                "instrument_type": "DERIVATIVE",
                "close_type": str(group["close_type"].dropna().iloc[-1]) if "close_type" in group and group["close_type"].notna().any() else "DERIVATIVE_CLOSE",
                "quantity_closed": safe_float(group["quantity_closed"].sum()),
                "net_proceeds_eur": proceeds,
                "cost_basis_eur": cost,
                "realized_pl_eur": pl,
                "realized_return_pct": safe_div(pl, cost) * 100.0 if cost > 1e-12 else np.nan,
                "weighted_holding_days": _weighted_average(group["holding_days"], group["allocated_cost_basis_eur"]),
            })

    events = pd.DataFrame(rows)
    if events.empty:
        return pd.DataFrame(columns=[
            "close_event_id", "event_date", "security_name", "isin", "instrument_type",
            "close_type", "quantity_closed", "net_proceeds_eur", "cost_basis_eur",
            "realized_pl_eur", "realized_return_pct", "weighted_holding_days", "outcome",
        ])
    tolerance = 0.01
    events["outcome"] = np.select(
        [events["realized_pl_eur"] > tolerance, events["realized_pl_eur"] < -tolerance],
        ["WIN", "LOSS"],
        default="FLAT",
    )
    return events.sort_values(["event_date", "realized_pl_eur"], ascending=[False, False]).reset_index(drop=True)


def _build_position_snapshot(stock_active, active_derivatives, dividend_projection_by_holding):
    projection_map = {}
    if dividend_projection_by_holding is not None and not dividend_projection_by_holding.empty:
        for _, row in dividend_projection_by_holding.iterrows():
            projection_map[str(row.get("isin", ""))] = _finite_or_nan(
                row.get("growth_adjusted_forward_12m_net_dividend_eur", row.get("forward_12m_net_dividend_eur", 0))
            )
    rows = []
    if stock_active is not None and not stock_active.empty:
        for _, row in stock_active.iterrows():
            live = _finite_or_nan(row.get("live_current_value_eur"))
            basis = max(0.0, safe_float(row.get("remaining_acquisition_cost_basis_eur")))
            if np.isfinite(live) and live >= 0:
                value, source = live, "LIVE_VALUE"
            else:
                value, source = basis, "OPEN_BASIS_FALLBACK"
            rows.append({
                "security_name": row.get("security_name", ""),
                "isin": row.get("isin", ""),
                "instrument_type": row.get("asset_class", "STOCK/FUND"),
                "base_value_eur": value,
                "open_basis_eur": basis,
                "forward_12m_net_income_eur": projection_map.get(str(row.get("isin", "")), np.nan),
                "valuation_source": source,
            })
    if active_derivatives is not None and not active_derivatives.empty:
        for _, row in active_derivatives.iterrows():
            live = _finite_or_nan(row.get("estimated_live_value_eur"))
            basis = max(0.0, safe_float(row.get("open_cost_basis_eur")))
            if np.isfinite(live) and live >= 0:
                value, source = live, "INDICATIVE_LIVE_VALUE"
            else:
                value, source = basis, "OPEN_BASIS_FALLBACK"
            rows.append({
                "security_name": row.get("security_name", ""),
                "isin": row.get("isin", ""),
                "instrument_type": "DERIVATIVE",
                "base_value_eur": value,
                "open_basis_eur": basis,
                "forward_12m_net_income_eur": 0.0,
                "valuation_source": source,
            })
    snapshot = pd.DataFrame(rows)
    if snapshot.empty:
        return pd.DataFrame(columns=[
            "security_name", "isin", "instrument_type", "base_value_eur", "open_basis_eur",
            "forward_12m_net_income_eur", "valuation_source", "weight_pct",
        ])
    total = safe_float(snapshot["base_value_eur"].sum())
    snapshot["weight_pct"] = np.where(total > 1e-12, snapshot["base_value_eur"] / total * 100.0, np.nan)
    return snapshot.sort_values("weight_pct", ascending=False).reset_index(drop=True)


def build_advanced_portfolio_insights(
    df,
    trades,
    dividends,
    interest,
    combined,
    open_lots,
    realized,
    derivative_ledger,
    derivative_realized,
    derivative_open_lots,
    active_derivatives,
    dividend_projection_by_holding,
    dividend_projection_by_month,
    max_date,
    ttm_start,
):
    stock_active = combined[combined["position_status"].eq("ACTIVE")].copy() if combined is not None and not combined.empty else pd.DataFrame()

    stock_valuation_dates = [pd.Timestamp(max_date).normalize()] if pd.notna(max_date) else []
    if stock_active is not None and not stock_active.empty and "live_price_date" in stock_active.columns:
        stock_valuation_dates.extend(pd.to_datetime(stock_active["live_price_date"], errors="coerce").dropna().dt.normalize().tolist())
    stock_valuation_date = max(stock_valuation_dates) if stock_valuation_dates else pd.Timestamp.today().normalize()
    tracked_valuation_dates = list(stock_valuation_dates)
    if active_derivatives is not None and not active_derivatives.empty and "quote_date" in active_derivatives.columns:
        tracked_valuation_dates.extend(pd.to_datetime(active_derivatives["quote_date"], format="mixed", dayfirst=True, errors="coerce").dropna().dt.normalize().tolist())
    tracked_valuation_date = max(tracked_valuation_dates) if tracked_valuation_dates else stock_valuation_date

    stock_mwr, stock_mwr_cashflows = _build_mwr_cashflows(
        trades, dividends, derivative_ledger, stock_active, active_derivatives, stock_valuation_date,
        include_derivatives=False, user_funded=False,
    )
    stock_user_mwr, stock_user_cashflows = _build_mwr_cashflows(
        trades, dividends, derivative_ledger, stock_active, active_derivatives, stock_valuation_date,
        include_derivatives=False, user_funded=True,
    )
    tracked_mwr, tracked_cashflows = _build_mwr_cashflows(
        trades, dividends, derivative_ledger, stock_active, active_derivatives, tracked_valuation_date,
        include_derivatives=True, user_funded=False,
    )
    mwr_cashflows = pd.concat(
        [stock_mwr_cashflows, stock_user_cashflows, tracked_cashflows],
        ignore_index=True,
    ).sort_values(["scope", "basis_mode", "cashflow_date"], na_position="last")

    snapshot = _build_position_snapshot(stock_active, active_derivatives, dividend_projection_by_holding)
    weights = pd.to_numeric(snapshot.get("weight_pct"), errors="coerce").dropna() / 100.0 if not snapshot.empty else pd.Series(dtype=float)
    actual_positions = int((pd.to_numeric(snapshot.get("base_value_eur"), errors="coerce").fillna(0) > 1e-12).sum()) if not snapshot.empty else 0
    sum_sq = float((weights ** 2).sum()) if len(weights) else np.nan
    effective_holdings = 1.0 / sum_sq if np.isfinite(sum_sq) and sum_sq > 1e-12 else np.nan
    hhi = sum_sq * 10000.0 if np.isfinite(sum_sq) else np.nan
    top1 = safe_float(snapshot.head(1)["weight_pct"].sum()) if not snapshot.empty else 0.0
    top3 = safe_float(snapshot.head(3)["weight_pct"].sum()) if not snapshot.empty else 0.0
    top5 = safe_float(snapshot.head(5)["weight_pct"].sum()) if not snapshot.empty else 0.0
    effective_ratio = safe_div(effective_holdings, actual_positions) if actual_positions else np.nan
    if not np.isfinite(effective_ratio):
        concentration_label = "Not available"
    elif effective_ratio >= 0.65 and top3 < 40:
        concentration_label = "Broadly distributed"
    elif effective_ratio >= 0.35 and top3 < 60:
        concentration_label = "Moderately concentrated"
    else:
        concentration_label = "Highly concentrated"
    valuation_sources = set(snapshot.get("valuation_source", pd.Series(dtype=str)).dropna().astype(str))
    if valuation_sources == {"LIVE_VALUE"} or valuation_sources.issubset({"LIVE_VALUE", "INDICATIVE_LIVE_VALUE"}):
        concentration_basis = "LIVE_OR_INDICATIVE_VALUE"
    elif len(valuation_sources) == 1 and "OPEN_BASIS_FALLBACK" in valuation_sources:
        concentration_basis = "OPEN_BASIS_FALLBACK"
    else:
        concentration_basis = "MIXED_VALUE_AND_BASIS_FALLBACK"

    sale_events = _aggregate_realized_events(realized, derivative_realized)
    wins = sale_events[sale_events["outcome"].eq("WIN")] if not sale_events.empty else pd.DataFrame()
    losses = sale_events[sale_events["outcome"].eq("LOSS")] if not sale_events.empty else pd.DataFrame()
    flats = sale_events[sale_events["outcome"].eq("FLAT")] if not sale_events.empty else pd.DataFrame()
    event_count = len(sale_events)
    win_rate = safe_div(len(wins), event_count) * 100.0 if event_count else np.nan
    gross_wins = safe_float(wins["realized_pl_eur"].sum()) if not wins.empty else 0.0
    gross_losses = abs(safe_float(losses["realized_pl_eur"].sum())) if not losses.empty else 0.0
    profit_factor = safe_div(gross_wins, gross_losses) if gross_losses > 1e-12 else np.nan
    avg_win = safe_float(wins["realized_pl_eur"].mean()) if not wins.empty else np.nan
    avg_loss = safe_float(losses["realized_pl_eur"].mean()) if not losses.empty else np.nan
    payoff_ratio = safe_div(avg_win, abs(avg_loss)) if np.isfinite(avg_win) and np.isfinite(avg_loss) and abs(avg_loss) > 1e-12 else np.nan
    median_holding = safe_float(sale_events["weighted_holding_days"].median()) if not sale_events.empty else np.nan
    weighted_holding = _weighted_average(sale_events.get("weighted_holding_days"), sale_events.get("cost_basis_eur")) if not sale_events.empty else np.nan

    open_basis_older_365 = 0.0
    open_basis_total = 0.0
    longest_open_days = np.nan
    longest_open_name = ""
    if open_lots is not None and not open_lots.empty and pd.notna(max_date):
        ol = open_lots.copy()
        ol["buy_date"] = pd.to_datetime(ol["buy_date"], errors="coerce")
        ol["holding_days"] = (pd.Timestamp(max_date).normalize() - ol["buy_date"]).dt.days
        ol["basis"] = pd.to_numeric(ol.get("cost_basis_remaining_eur"), errors="coerce").fillna(0)
        open_basis_total = safe_float(ol["basis"].sum())
        open_basis_older_365 = safe_float(ol.loc[ol["holding_days"] >= 365, "basis"].sum())
        valid_ol = ol[ol["holding_days"].notna()]
        if not valid_ol.empty:
            longest = valid_ol.sort_values("holding_days", ascending=False).iloc[0]
            longest_open_days = safe_float(longest["holding_days"])
            longest_open_name = str(longest.get("security_name", ""))

    stock_trade_fees = safe_float(trades["fee_paid_eur"].sum()) if trades is not None and not trades.empty else 0.0
    derivative_trade_fees = safe_float(derivative_ledger["fee_paid_eur"].sum()) if derivative_ledger is not None and not derivative_ledger.empty else 0.0
    total_all_fees = safe_float(df["fee"].abs().fillna(0).sum()) if df is not None and not df.empty else stock_trade_fees + derivative_trade_fees
    stock_notional = safe_float(trades["gross_buy_value_eur"].sum() + trades["gross_sell_value_eur"].sum()) if trades is not None and not trades.empty else 0.0
    derivative_notional = safe_float(derivative_ledger["buy_value_eur"].sum() + derivative_ledger["sell_value_eur"].sum()) if derivative_ledger is not None and not derivative_ledger.empty else 0.0
    total_notional = stock_notional + derivative_notional
    fee_drag_pct = safe_div(stock_trade_fees + derivative_trade_fees, total_notional) * 100.0 if total_notional > 1e-12 else np.nan
    trade_event_count = int(len(trades) + len(derivative_ledger))
    avg_fee_per_trade = safe_div(stock_trade_fees + derivative_trade_fees, trade_event_count) if trade_event_count else np.nan

    gross_dividends = complete_numeric_sum(dividends["gross_dividend_eur"]) if dividends is not None and not dividends.empty else 0.0
    net_dividends = safe_float(dividends["net_dividend_eur"].sum()) if dividends is not None and not dividends.empty else 0.0
    div_tax = (complete_numeric_sum(dividends["dividend_tax_withheld_eur"]) - safe_float(dividends["dividend_tax_refund_eur"].sum())) if dividends is not None and not dividends.empty else 0.0
    gross_interest = safe_float(interest["gross_interest_eur"].sum()) if interest is not None and not interest.empty else 0.0
    net_interest = safe_float(interest["net_interest_eur"].sum()) if interest is not None and not interest.empty else 0.0
    interest_tax = safe_float(interest["interest_tax_withheld_eur"].sum() - interest.get("interest_tax_refund_eur", pd.Series(dtype=float)).sum()) if interest is not None and not interest.empty else 0.0
    gross_income = gross_dividends + gross_interest if np.isfinite(gross_dividends) else np.nan
    net_income = net_dividends + net_interest
    net_income_retention = safe_div(net_income, gross_income) * 100.0 if np.isfinite(gross_income) and gross_income > 1e-12 else np.nan
    income_tax_leakage = safe_div(div_tax + interest_tax, gross_income) * 100.0 if np.isfinite(gross_income) and np.isfinite(div_tax) and gross_income > 1e-12 else np.nan

    income_rows = []
    if stock_active is not None and not stock_active.empty:
        for _, row in stock_active.iterrows():
            ttm_net = max(0.0, safe_float(row.get("ttm_net_dividends")))
            if ttm_net <= 1e-12:
                continue
            income_rows.append({
                "security_name": row.get("security_name", ""),
                "isin": row.get("isin", ""),
                "ttm_net_dividend_eur": ttm_net,
            })
    income_by_company = pd.DataFrame(income_rows)
    if not income_by_company.empty:
        income_total = safe_float(income_by_company["ttm_net_dividend_eur"].sum())
        income_by_company["income_share_pct"] = np.where(
            income_total > 1e-12,
            income_by_company["ttm_net_dividend_eur"] / income_total * 100.0,
            np.nan,
        )
        income_by_company = income_by_company.sort_values("income_share_pct", ascending=False).reset_index(drop=True)
        income_weights = income_by_company["income_share_pct"] / 100.0
        income_sum_sq = float((income_weights ** 2).sum())
        effective_income_sources = 1.0 / income_sum_sq if income_sum_sq > 1e-12 else np.nan
        top_income_1 = safe_float(income_by_company.head(1)["income_share_pct"].sum())
        top_income_3 = safe_float(income_by_company.head(3)["income_share_pct"].sum())
    else:
        income_total = 0.0
        effective_income_sources = np.nan
        top_income_1 = 0.0
        top_income_3 = 0.0

    active_isins = set(stock_active.get("isin", pd.Series(dtype=str)).astype(str)) if stock_active is not None and not stock_active.empty else set()
    dividend_months_ttm = 0
    if dividends is not None and not dividends.empty and pd.notna(max_date):
        div_dates = pd.to_datetime(dividends["payment_date"], errors="coerce")
        mask = (div_dates > pd.Timestamp(ttm_start)) & (div_dates <= pd.Timestamp(max_date))
        if active_isins:
            mask &= dividends["isin"].astype(str).isin(active_isins)
        mask &= pd.to_numeric(dividends["net_dividend_eur"], errors="coerce").fillna(0) > 0
        dividend_months_ttm = int(div_dates[mask].dt.to_period("M").nunique())

    current_ttm = 0.0
    previous_ttm = 0.0
    dividend_growth_pct = np.nan
    if dividends is not None and not dividends.empty and pd.notna(max_date):
        div_dates = pd.to_datetime(dividends["payment_date"], errors="coerce")
        net_series = pd.to_numeric(dividends["net_dividend_eur"], errors="coerce").fillna(0)
        current_start = pd.Timestamp(max_date) - pd.DateOffset(months=12)
        previous_start = pd.Timestamp(max_date) - pd.DateOffset(months=24)
        current_ttm = safe_float(net_series[(div_dates > current_start) & (div_dates <= pd.Timestamp(max_date))].sum())
        previous_ttm = safe_float(net_series[(div_dates > previous_start) & (div_dates <= current_start)].sum())
        dividend_growth_pct = safe_div(current_ttm - previous_ttm, previous_ttm) * 100.0 if abs(previous_ttm) > 1e-12 else np.nan

    largest_projected_month = ""
    largest_projected_month_net = np.nan
    if dividend_projection_by_month is not None and not dividend_projection_by_month.empty:
        col = "growth_adjusted_projected_net_dividend_eur" if "growth_adjusted_projected_net_dividend_eur" in dividend_projection_by_month.columns else "projected_net_dividend_eur"
        month_work = dividend_projection_by_month.copy()
        month_work[col] = pd.to_numeric(month_work[col], errors="coerce")
        if month_work[col].notna().all():
            largest = month_work.sort_values(col, ascending=False).iloc[0]
            largest_projected_month = str(largest.get("projection_year_month", ""))
            largest_projected_month_net = safe_float(largest.get(col))

    base_value = safe_float(snapshot["base_value_eur"].sum()) if not snapshot.empty else 0.0
    base_basis = safe_float(snapshot["open_basis_eur"].sum()) if not snapshot.empty else 0.0
    base_income = (float(snapshot["forward_12m_net_income_eur"].sum()) if snapshot["forward_12m_net_income_eur"].notna().all() else np.nan) if not snapshot.empty else 0.0
    largest_names = snapshot.head(1)["security_name"].astype(str).tolist() if not snapshot.empty else []
    top3_names = snapshot.head(3)["security_name"].astype(str).tolist() if not snapshot.empty else []
    top_income_name = income_by_company.head(1)["security_name"].astype(str).tolist() if not income_by_company.empty else []

    scenario_specs = [
        ("Broad decline -10%", -0.10, -0.20, -0.05, set(), "Stocks/funds -10%; derivatives -20%; dividends -5%"),
        ("Broad decline -20%", -0.20, -0.40, -0.15, set(), "Stocks/funds -20%; derivatives -40%; dividends -15%"),
        ("Broad decline -30%", -0.30, -0.60, -0.25, set(), "Stocks/funds -30%; derivatives -60%; dividends -25%"),
        ("Largest position -25%", 0.0, 0.0, 0.0, set(largest_names), "Largest current position -25%"),
        ("Top 3 positions -20%", 0.0, 0.0, 0.0, set(top3_names), "Three largest current positions -20%"),
        ("Derivatives -50%", 0.0, -0.50, 0.0, set(), "All active derivatives -50%"),
        ("Forward dividends -25%", 0.0, 0.0, -0.25, set(), "Forward net dividends -25%; values unchanged"),
        ("Largest dividend payer -50%", 0.0, 0.0, 0.0, set(), "Largest TTM dividend payer income -50%"),
    ]
    stress_rows = []
    for scenario_name, stock_shock, deriv_shock, income_shock, selected_names, assumptions in scenario_specs:
        stressed_value = 0.0
        stressed_income = 0.0
        for _, row in snapshot.iterrows():
            name = str(row.get("security_name", ""))
            instrument_type = str(row.get("instrument_type", ""))
            value = safe_float(row.get("base_value_eur"))
            income_value = _finite_or_nan(row.get("forward_12m_net_income_eur"))
            if selected_names and name in selected_names:
                shock = -0.25 if scenario_name.startswith("Largest") else -0.20
            elif instrument_type == "DERIVATIVE":
                shock = deriv_shock
            else:
                shock = stock_shock
            stressed_value += value * (1.0 + shock)
            row_income_shock = income_shock
            if scenario_name == "Largest position -25%" and name in selected_names:
                row_income_shock = -0.25
            if scenario_name == "Top 3 positions -20%" and name in selected_names:
                row_income_shock = -0.20
            if scenario_name == "Largest dividend payer -50%" and top_income_name and name == top_income_name[0]:
                row_income_shock = -0.50
            stressed_income += income_value * (1.0 + row_income_shock)
        value_change = stressed_value - base_value
        stress_rows.append({
            "scenario": scenario_name,
            "assumptions": assumptions,
            "base_value_eur": base_value,
            "stressed_value_eur": stressed_value,
            "value_change_eur": value_change,
            "value_change_pct": safe_div(value_change, base_value) * 100.0 if base_value > 1e-12 else np.nan,
            "stressed_open_pl_eur": stressed_value - base_basis,
            "base_forward_12m_net_income_eur": base_income,
            "stressed_forward_12m_net_income_eur": stressed_income,
            "income_change_eur": stressed_income - base_income,
            "income_change_pct": safe_div(stressed_income - base_income, base_income) * 100.0 if base_income > 1e-12 else np.nan,
            "status": "OK" if base_value > 1e-12 else "NO_VALUATION",
        })
    stress_scenarios = pd.DataFrame(stress_rows)

    broad20 = stress_scenarios[stress_scenarios["scenario"].eq("Broad decline -20%")].iloc[0] if not stress_scenarios.empty else None
    messages = []
    if stock_mwr["status"] in {"OK", "MULTIPLE_ROOTS"}:
        severity = "GOOD" if safe_float(stock_mwr["annualized_return_pct"]) >= 0 else "WATCH"
        messages.append({
            "severity": severity,
            "category": "Return",
            "title": "Since-inception stock/fund MWR",
            "message": f"Annualized money-weighted return is {safe_float(stock_mwr['annualized_return_pct']):.2f}% ({stock_mwr['status']}).",
        })
    else:
        messages.append({
            "severity": "INFO",
            "category": "Return",
            "title": "MWR not available",
            "message": f"Stock/fund MWR status: {stock_mwr['status']}. Complete live valuation is required.",
        })
    messages.append({
        "severity": "WATCH" if concentration_label == "Highly concentrated" else "INFO",
        "category": "Concentration",
        "title": concentration_label,
        "message": f"Top three positions represent {top3:.2f}% of tracked value; {actual_positions} positions behave like {effective_holdings:.1f} equally weighted positions." if np.isfinite(effective_holdings) else f"Top three positions represent {top3:.2f}% of tracked value.",
    })
    if event_count:
        messages.append({
            "severity": "GOOD" if win_rate >= 60 and (not np.isfinite(profit_factor) or profit_factor >= 1.5) else "WATCH" if win_rate < 45 else "INFO",
            "category": "Realized behaviour",
            "title": "Closed-event quality",
            "message": f"{event_count} close events: {win_rate:.1f}% win rate, profit factor {profit_factor:.2f}." if np.isfinite(profit_factor) else f"{event_count} close events: {win_rate:.1f}% win rate; no realized loss denominator for profit factor.",
        })
    else:
        messages.append({"severity": "INFO", "category": "Realized behaviour", "title": "No matched close events", "message": "There are no matched realized close events to score yet."})
    messages.append({
        "severity": "WATCH" if top_income_3 >= 65 else "INFO",
        "category": "Income",
        "title": "Dividend-source concentration",
        "message": f"The top three active dividend sources produced {top_income_3:.1f}% of active-position TTM net dividends across {len(income_by_company)} payers.",
    })
    if np.isfinite(fee_drag_pct):
        messages.append({
            "severity": "WATCH" if fee_drag_pct > 0.50 else "GOOD" if fee_drag_pct < 0.15 else "INFO",
            "category": "Friction",
            "title": "Trading fee drag",
            "message": f"Stock/fund and derivative trading fees equal {fee_drag_pct:.3f}% of gross traded notional.",
        })
    if broad20 is not None:
        messages.append({
            "severity": "INFO",
            "category": "Stress test",
            "title": "Mechanical broad-decline scenario",
            "message": f"Under the -20% stock/fund and -40% derivative scenario, tracked value changes by {safe_float(broad20['value_change_eur']):,.2f} EUR ({safe_float(broad20['value_change_pct']):.1f}%). This is not a forecast.",
        })
    insight_messages = pd.DataFrame(messages)

    metrics = {
        "stock_fund_mwr_acquisition_pct": stock_mwr["annualized_return_pct"],
        "stock_fund_mwr_acquisition_status": stock_mwr["status"],
        "stock_fund_mwr_user_funded_pct": stock_user_mwr["annualized_return_pct"],
        "stock_fund_mwr_user_funded_status": stock_user_mwr["status"],
        "tracked_investments_mwr_pct": tracked_mwr["annualized_return_pct"],
        "tracked_investments_mwr_status": tracked_mwr["status"],
        "mwr_period_years": stock_mwr.get("period_years", np.nan),
        "stock_fund_mwr_valuation_date": str(pd.Timestamp(stock_valuation_date).date()),
        "tracked_mwr_valuation_date": str(pd.Timestamp(tracked_valuation_date).date()),
        "largest_position_weight_pct": top1,
        "top3_position_weight_pct": top3,
        "top5_position_weight_pct": top5,
        "effective_number_of_holdings": effective_holdings,
        "actual_weighted_positions": actual_positions,
        "portfolio_hhi": hhi,
        "concentration_label": concentration_label,
        "concentration_valuation_basis": concentration_basis,
        "realized_close_events": event_count,
        "realized_win_events": len(wins),
        "realized_loss_events": len(losses),
        "realized_flat_events": len(flats),
        "realized_win_rate_pct": win_rate,
        "realized_profit_factor": profit_factor,
        "realized_payoff_ratio": payoff_ratio,
        "average_realized_win_eur": avg_win,
        "average_realized_loss_eur": avg_loss,
        "median_realized_holding_days": median_holding,
        "weighted_realized_holding_days": weighted_holding,
        "open_stock_basis_held_over_365d_pct": safe_div(open_basis_older_365, open_basis_total) * 100.0 if open_basis_total > 1e-12 else np.nan,
        "longest_open_stock_holding_days": longest_open_days,
        "longest_open_stock_holding_name": longest_open_name,
        "total_all_fees_eur": total_all_fees,
        "investment_trade_fees_eur": stock_trade_fees + derivative_trade_fees,
        "gross_traded_notional_eur": total_notional,
        "fee_drag_traded_notional_pct": fee_drag_pct,
        "average_fee_per_investment_trade_eur": avg_fee_per_trade,
        "gross_investment_income_eur": gross_income,
        "net_investment_income_eur": net_income,
        "net_income_retention_pct": net_income_retention,
        "income_tax_leakage_pct": income_tax_leakage,
        "active_ttm_dividend_payers": len(income_by_company),
        "effective_dividend_sources": effective_income_sources,
        "largest_dividend_source_share_pct": top_income_1,
        "top3_dividend_source_share_pct": top_income_3,
        "dividend_paying_months_ttm": dividend_months_ttm,
        "dividend_month_coverage_pct": dividend_months_ttm / 12.0 * 100.0,
        "portfolio_net_dividends_current_ttm_eur": current_ttm,
        "portfolio_net_dividends_previous_ttm_eur": previous_ttm,
        "portfolio_net_dividend_growth_pct": dividend_growth_pct,
        "largest_projected_dividend_month": largest_projected_month,
        "largest_projected_dividend_month_net_eur": largest_projected_month_net,
        "stress_base_value_eur": base_value,
        "stress_base_open_basis_eur": base_basis,
        "stress_base_forward_income_eur": base_income,
        "stress_test_valuation_basis": concentration_basis,
        "derivative_tilg_not_allocated_to_close_events_eur": safe_float(derivative_ledger.get("tilg_settlement_eur", pd.Series(dtype=float)).sum()) if derivative_ledger is not None and not derivative_ledger.empty else 0.0,
    }
    return {
        "metrics": metrics,
        "mwr_cashflows": mwr_cashflows,
        "realized_events": sale_events,
        "position_snapshot": snapshot,
        "income_by_company": income_by_company,
        "stress_scenarios": stress_scenarios,
        "insight_messages": insight_messages,
    }


# ============================================================
# 7C. Historical performance, benchmark, attribution and cash intelligence
# ============================================================

HISTORICAL_ANALYTICS_MIN_OBSERVATIONS = 30
HISTORICAL_PRICE_MAX_WORKERS = 6
HISTORICAL_PRICE_FALLBACK_LABEL = "TRANSACTION_PRICE_FALLBACK"
EXTERNAL_CASH_DEPOSIT_TYPES = {
    "CUSTOMER_INPAYMENT", "CUSTOMER_INBOUND", "TRANSFER_INBOUND",
    "TRANSFER_INSTANT_INBOUND",
}
COUNTRY_BY_ISIN_PREFIX = {
    "AT": "Austria", "AU": "Australia", "BE": "Belgium", "CA": "Canada",
    "CH": "Switzerland", "CN": "China", "DE": "Germany", "DK": "Denmark",
    "ES": "Spain", "FI": "Finland", "FR": "France", "GB": "United Kingdom",
    "HK": "Hong Kong", "IE": "Ireland", "IN": "India", "IT": "Italy",
    "JP": "Japan", "LU": "Luxembourg", "NL": "Netherlands", "NO": "Norway",
    "PT": "Portugal", "SE": "Sweden", "SG": "Singapore", "US": "United States",
}


def _history_empty_frame():
    return pd.DataFrame(columns=["close_native", "adjusted_close_native"])


def _normalize_yahoo_history(frame):
    if frame is None or frame.empty:
        return _history_empty_frame()
    out = frame.copy()
    index = pd.to_datetime(out.index, errors="coerce")
    try:
        index = index.tz_localize(None)
    except (TypeError, AttributeError):
        try:
            index = index.tz_convert(None)
        except (TypeError, AttributeError):
            pass
    out.index = pd.DatetimeIndex(index).normalize()
    out = out[~out.index.isna()]
    close_col = "Close" if "Close" in out.columns else None
    adjusted_col = "Adj Close" if "Adj Close" in out.columns else close_col
    if close_col is None:
        return _history_empty_frame()
    result = pd.DataFrame(index=out.index)
    result["close_native"] = pd.to_numeric(out[close_col], errors="coerce")
    result["adjusted_close_native"] = pd.to_numeric(out[adjusted_col], errors="coerce")
    result = result.groupby(level=0).last().sort_index()
    return result.dropna(how="all")


def _fetch_yahoo_history(ticker, start_date, end_date):
    ticker = str(ticker or "").strip()
    if not ticker:
        return _history_empty_frame(), "", "NO_TICKER", ""
    try:
        yt = yf.Ticker(ticker)
        frame = yt.history(
            start=str(pd.Timestamp(start_date).date()),
            end=str((pd.Timestamp(end_date) + pd.Timedelta(days=2)).date()),
            auto_adjust=False,
            actions=False,
        )
        currency = ""
        try:
            currency = str(yt.fast_info.get("currency", "") or "").upper().strip()
        except Exception:
            currency = ""
        normalized = _normalize_yahoo_history(frame)
        status = "OK" if not normalized.empty else "NO_HISTORY"
        return normalized, currency, status, ""
    except Exception as exc:
        return _history_empty_frame(), "", "ERROR", str(exc)


def _normalize_currency_for_history(currency):
    raw = str(currency or "").strip()
    upper = raw.upper()
    if upper in {"GBP", "GBX", "GBPENCE", "GBP PENCE", "GBP."} or raw == "GBp":
        return "GBP", 0.01 if upper in {"GBX", "GBPENCE", "GBP PENCE"} or raw == "GBp" else 1.0
    if upper in {"ILA", "AGOROT"}:
        return "ILS", 0.01
    return upper or "UNKNOWN", 1.0


def _transaction_price_fallback(trades_for_isin, calendar, valuation_date, live_price_eur=np.nan, live_price_date=None):
    points = trades_for_isin.copy() if trades_for_isin is not None else pd.DataFrame()
    if np.isfinite(safe_float(live_price_eur, np.nan)):
        live_dt = pd.to_datetime(live_price_date, errors="coerce")
        if pd.isna(live_dt):
            live_dt = pd.Timestamp(valuation_date)
        live_dt = pd.Timestamp(live_dt).normalize()
        eligible = calendar[calendar <= live_dt]
        if len(eligible):
            live_row = {
                "event_date": eligible[-1], "historical_trade_price_eur": float(live_price_eur),
                "trade_price_eur": float(live_price_eur), "quantity": 1.0,
            }
            points = pd.concat([points, pd.DataFrame([live_row])], ignore_index=True)
    series, quality = transaction_price_fallback_with_quality(points, calendar)
    if np.isfinite(safe_float(live_price_eur, np.nan)) and len(calendar):
        live_dt = pd.to_datetime(live_price_date, errors="coerce")
        if pd.isna(live_dt):
            live_dt = pd.Timestamp(valuation_date)
        eligible = calendar[calendar <= pd.Timestamp(live_dt).normalize()]
        if len(eligible):
            quality.loc[eligible[-1]] = "EXTERNAL_LIVE_PRICE"
    return series, quality


def _drawdown_episode_table(nav_series):
    nav = pd.to_numeric(nav_series, errors="coerce").dropna()
    if nav.empty:
        return pd.DataFrame(columns=[
            "episode", "peak_date", "trough_date", "recovery_date", "max_drawdown_pct",
            "days_to_trough", "days_to_recovery", "total_underwater_days", "status",
        ])
    running_peak = nav.cummax()
    drawdown = nav / running_peak - 1.0
    rows = []
    in_episode = False
    start_pos = None
    episode_number = 0
    for pos, (dt, value) in enumerate(drawdown.items()):
        if value < -1e-12 and not in_episode:
            in_episode = True
            start_pos = max(0, pos - 1)
        recovered = in_episode and value >= -1e-12
        last_item = pos == len(drawdown) - 1
        if in_episode and (recovered or last_item):
            end_pos = pos if recovered else pos
            segment = drawdown.iloc[start_pos:end_pos + 1]
            trough_date = segment.idxmin()
            peak_date = nav.iloc[:start_pos + 1].idxmax()
            recovery_date = dt if recovered else pd.NaT
            episode_number += 1
            rows.append({
                "episode": episode_number,
                "peak_date": peak_date,
                "trough_date": trough_date,
                "recovery_date": recovery_date,
                "max_drawdown_pct": safe_float(segment.min()) * 100.0,
                "days_to_trough": (pd.Timestamp(trough_date) - pd.Timestamp(peak_date)).days,
                "days_to_recovery": (pd.Timestamp(recovery_date) - pd.Timestamp(trough_date)).days if pd.notna(recovery_date) else np.nan,
                "total_underwater_days": (pd.Timestamp(recovery_date) - pd.Timestamp(peak_date)).days if pd.notna(recovery_date) else (pd.Timestamp(dt) - pd.Timestamp(peak_date)).days,
                "status": "RECOVERED" if pd.notna(recovery_date) else "CURRENT",
            })
            in_episode = False
            start_pos = None
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=[
            "episode", "peak_date", "trough_date", "recovery_date", "max_drawdown_pct",
            "days_to_trough", "days_to_recovery", "total_underwater_days", "status",
        ])
    return out.sort_values("max_drawdown_pct").reset_index(drop=True)


def build_valuation_diagnostics(holdings, active_derivatives):
    """Return one actionable valuation record for every active tracked position."""
    rows = []
    stock = holdings[holdings["position_status"].eq("ACTIVE")].copy() if holdings is not None and not holdings.empty else pd.DataFrame()
    total_stock_basis = safe_float(stock.get("remaining_acquisition_cost_basis_eur", pd.Series(dtype=float)).sum())
    for _, row in stock.iterrows():
        value = safe_float(row.get("live_current_value_eur"), np.nan)
        basis = safe_float(row.get("remaining_acquisition_cost_basis_eur"), np.nan)
        resolved = np.isfinite(value)
        quote_currency = str(row.get("live_price_currency", "") or "")
        rows.append({
            "instrument_type": "STOCK_FUND",
            "security_name": row.get("security_name", ""),
            "requested_isin": row.get("historical_isin_aliases", row.get("isin", "")),
            "isin": row.get("isin", ""),
            "canonical_instrument_id": row.get("canonical_instrument_id", ""),
            "canonical_current_isin": row.get("current_isin", row.get("isin", "")),
            "historical_isin_aliases": row.get("historical_isin_aliases", row.get("isin", "")),
            "quantity": row.get("current_quantity", np.nan),
            "acquisition_basis_eur": basis,
            "basis_share_of_stock_portfolio_pct": basis / total_stock_basis * 100.0 if np.isfinite(basis) and total_stock_basis > 1e-12 else np.nan,
            "valuation_status": "VALUED" if resolved else "BLOCKING",
            "ticker_resolution_status": row.get("ticker_match_status", ""),
            "live_price_status": row.get("price_status", ""),
            "failure_reason": "" if resolved else (row.get("price_diagnostic_detail", "") or row.get("price_error", "") or row.get("price_status", "NO_RELIABLE_CURRENT_PRICE")),
            "attempted_sources": "OPENFIGI;YAHOO_EXACT_ISIN;YAHOO_SEARCH;YAHOO_PRICE_HISTORY",
            "selected_ticker": row.get("yahoo_ticker", ""),
            "exchange": row.get("ticker_matched_exchange", ""),
            "quote_currency": quote_currency,
            "price_source": row.get("live_price_source", ""),
            "fx_source": "IDENTITY_EUR" if quote_currency in {"", "EUR"} else "YAHOO_FX_TO_EUR",
            "quote_date": row.get("live_price_date", ""),
            "resolution_method": row.get("ticker_match_status", ""),
            "confidence_status": row.get("ticker_match_status", ""),
            "current_value_eur": value,
        })
    derivative = active_derivatives.copy() if active_derivatives is not None else pd.DataFrame()
    total_derivative_basis = safe_float(derivative.get("open_cost_basis_eur", pd.Series(dtype=float)).sum()) if not derivative.empty else 0.0
    for _, row in derivative.iterrows():
        value = safe_float(row.get("estimated_live_value_eur"), np.nan)
        basis = safe_float(row.get("open_cost_basis_eur"), np.nan)
        resolved = np.isfinite(value)
        rows.append({
            "instrument_type": "DERIVATIVE", "security_name": row.get("security_name", ""),
            "requested_isin": row.get("isin", ""), "isin": row.get("isin", ""),
            "canonical_instrument_id": f"ISIN:{row.get('isin', '')}",
            "canonical_current_isin": row.get("isin", ""), "historical_isin_aliases": row.get("isin", ""),
            "quantity": row.get("current_quantity", np.nan), "acquisition_basis_eur": basis,
            "basis_share_of_stock_portfolio_pct": basis / total_derivative_basis * 100.0 if np.isfinite(basis) and total_derivative_basis > 1e-12 else np.nan,
            "valuation_status": "VALUED" if resolved else "BLOCKING",
            "ticker_resolution_status": row.get("quote_status", ""), "live_price_status": row.get("quote_status", ""),
            "failure_reason": "" if resolved else (row.get("quote_error", "") or row.get("quote_status", "NO_RELIABLE_CURRENT_QUOTE")),
            "attempted_sources": "BOERSE_STUTTGART;ONVISTA;CONSORS;COMDIRECT;YAHOO",
            "selected_ticker": row.get("yahoo_ticker", ""), "exchange": "",
            "quote_currency": row.get("quote_currency", "EUR"), "price_source": row.get("quote_source", ""),
            "fx_source": "IDENTITY_EUR", "quote_date": row.get("quote_timestamp", ""),
            "resolution_method": row.get("quote_source", ""), "confidence_status": row.get("quote_status", ""),
            "current_value_eur": value,
        })
    return pd.DataFrame(rows)


PERFORMANCE_PERIOD_KEYS = ("1D", "1W", "1M", "3M", "YTD", "1Y", "3Y", "5Y", "MAX")
PERFORMANCE_ANCHOR_MAX_LAG_DAYS = 7


def build_period_performance(nav_index, benchmark_total_return, daily_returns, benchmark_name="Benchmark"):
    """Extract broker-style cumulative TWR periods from canonical daily history.

    Fixed calendar lookbacks resolve to the latest valid portfolio observation on
    or before the requested anchor. Anchors more than seven calendar days stale
    are rejected instead of manufacturing a period. YTD requires a valid
    observation immediately before the current calendar year; MAX starts from
    the canonical inception level. Benchmark returns use the exact same resolved
    start and end dates. All values are cumulative, never annualized.
    """
    columns = [
        "period_key", "requested_start_date", "effective_start_date", "end_date",
        "elapsed_days", "portfolio_twr_pct", "benchmark_return_pct",
        "excess_return_pct_points", "period_max_drawdown_pct", "available",
        "unavailable_reason", "benchmark_available", "benchmark_unavailable_reason",
        "benchmark_name", "anchor_convention",
    ]
    path_columns = [
        "period_key", "date", "portfolio_rebased", "benchmark_rebased",
        "effective_start_date", "end_date",
    ]

    def clean_series(values):
        series = pd.Series(values).copy()
        series.index = pd.to_datetime(series.index, errors="coerce")
        series = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
        series = series[~series.index.isna()].groupby(level=0).last().sort_index()
        return series

    nav = clean_series(nav_index).dropna()
    benchmark = clean_series(benchmark_total_return)
    returns = clean_series(daily_returns).dropna()
    valid_dates = pd.DatetimeIndex(returns.index.intersection(nav.index)).sort_values()
    rows = []
    path_rows = []
    end_date = valid_dates[-1] if len(valid_dates) else pd.NaT

    def unavailable_row(period_key, requested_start, reason):
        return {
            "period_key": period_key,
            "requested_start_date": requested_start,
            "effective_start_date": pd.NaT,
            "end_date": end_date,
            "elapsed_days": np.nan,
            "portfolio_twr_pct": np.nan,
            "benchmark_return_pct": np.nan,
            "excess_return_pct_points": np.nan,
            "period_max_drawdown_pct": np.nan,
            "available": False,
            "unavailable_reason": reason,
            "benchmark_available": False,
            "benchmark_unavailable_reason": "PORTFOLIO_PERIOD_UNAVAILABLE",
            "benchmark_name": benchmark_name,
            "anchor_convention": "LATEST_VALID_OBSERVATION_ON_OR_BEFORE_TARGET",
        }

    for period_key in PERFORMANCE_PERIOD_KEYS:
        requested_start = pd.NaT
        start_date = pd.NaT
        reason = ""
        if pd.isna(end_date):
            reason = "NO_VALID_TWR_OBSERVATIONS"
        elif period_key == "1D":
            prior = valid_dates[valid_dates < end_date]
            requested_start = prior[-1] if len(prior) else pd.NaT
            start_date = requested_start
            if pd.isna(start_date):
                reason = "INSUFFICIENT_HISTORY"
            elif (end_date - start_date).days > PERFORMANCE_ANCHOR_MAX_LAG_DAYS:
                reason = "PRIOR_OBSERVATION_TOO_STALE"
        elif period_key == "YTD":
            year_start = pd.Timestamp(year=end_date.year, month=1, day=1)
            requested_start = year_start - pd.Timedelta(days=1)
            candidates = valid_dates[valid_dates < year_start]
            if len(candidates):
                start_date = candidates[-1]
                if (requested_start - start_date).days > PERFORMANCE_ANCHOR_MAX_LAG_DAYS:
                    reason = "PRIOR_YEAR_END_OBSERVATION_TOO_STALE"
            else:
                reason = "INSUFFICIENT_PRE_YEAR_HISTORY"
        elif period_key == "MAX":
            requested_start = nav.index.min() if not nav.empty else pd.NaT
            start_date = requested_start
            if pd.isna(start_date) or start_date >= end_date:
                reason = "INSUFFICIENT_HISTORY"
        else:
            offsets = {
                "1W": pd.DateOffset(days=7),
                "1M": pd.DateOffset(months=1),
                "3M": pd.DateOffset(months=3),
                "1Y": pd.DateOffset(years=1),
                "3Y": pd.DateOffset(years=3),
                "5Y": pd.DateOffset(years=5),
            }
            requested_start = end_date - offsets[period_key]
            candidates = valid_dates[valid_dates <= requested_start]
            if len(candidates):
                start_date = candidates[-1]
                if (requested_start - start_date).days > PERFORMANCE_ANCHOR_MAX_LAG_DAYS:
                    reason = "START_OBSERVATION_TOO_STALE"
            else:
                reason = "INSUFFICIENT_HISTORY"

        if reason:
            rows.append(unavailable_row(period_key, requested_start, reason))
            continue

        start_value = safe_float(nav.get(start_date), np.nan)
        end_value = safe_float(nav.get(end_date), np.nan)
        if not np.isfinite(start_value) or not np.isfinite(end_value) or start_value <= 1e-12:
            rows.append(unavailable_row(period_key, requested_start, "MISSING_START_OR_END_TWR_INDEX"))
            continue
        window = nav.loc[(nav.index >= start_date) & (nav.index <= end_date)].dropna()
        if len(window) < 2:
            rows.append(unavailable_row(period_key, requested_start, "INSUFFICIENT_WINDOW_OBSERVATIONS"))
            continue
        portfolio_return = (end_value / start_value - 1.0) * 100.0
        rebased_portfolio = window / start_value * 100.0
        period_drawdown = rebased_portfolio / rebased_portfolio.cummax() - 1.0
        period_max_drawdown = safe_float(period_drawdown.min(), np.nan) * 100.0

        benchmark_start = safe_float(benchmark.get(start_date), np.nan)
        benchmark_end = safe_float(benchmark.get(end_date), np.nan)
        benchmark_available = bool(
            np.isfinite(benchmark_start) and np.isfinite(benchmark_end) and benchmark_start > 1e-12
        )
        benchmark_return = (
            (benchmark_end / benchmark_start - 1.0) * 100.0 if benchmark_available else np.nan
        )
        benchmark_window = benchmark.reindex(window.index)
        rebased_benchmark = (
            benchmark_window / benchmark_start * 100.0 if benchmark_available else pd.Series(np.nan, index=window.index)
        )
        rows.append({
            "period_key": period_key,
            "requested_start_date": requested_start,
            "effective_start_date": start_date,
            "end_date": end_date,
            "elapsed_days": int((end_date - start_date).days),
            "portfolio_twr_pct": portfolio_return,
            "benchmark_return_pct": benchmark_return,
            "excess_return_pct_points": portfolio_return - benchmark_return if benchmark_available else np.nan,
            "period_max_drawdown_pct": period_max_drawdown,
            "available": True,
            "unavailable_reason": "",
            "benchmark_available": benchmark_available,
            "benchmark_unavailable_reason": "" if benchmark_available else "BENCHMARK_START_OR_END_UNAVAILABLE",
            "benchmark_name": benchmark_name,
            "anchor_convention": "LATEST_VALID_OBSERVATION_ON_OR_BEFORE_TARGET",
        })
        for dt in window.index:
            path_rows.append({
                "period_key": period_key,
                "date": dt,
                "portfolio_rebased": safe_float(rebased_portfolio.get(dt), np.nan),
                "benchmark_rebased": safe_float(rebased_benchmark.get(dt), np.nan),
                "effective_start_date": start_date,
                "end_date": end_date,
            })

    return pd.DataFrame(rows, columns=columns), pd.DataFrame(path_rows, columns=path_columns)


def _risk_metrics_from_returns(daily_returns, nav_series, benchmark_returns=None, annual_risk_free_rate_pct=0.0):
    returns = pd.to_numeric(daily_returns, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    nav = pd.to_numeric(nav_series, errors="coerce").dropna()
    metrics = {
        "history_observations": len(returns),
        "history_status": "INSUFFICIENT_HISTORY" if len(returns) < HISTORICAL_ANALYTICS_MIN_OBSERVATIONS else "OK",
    }
    if returns.empty or nav.empty:
        return metrics
    periods = max(len(returns), 1)
    cumulative = float((1.0 + returns).prod() - 1.0)
    annualized_return = float((1.0 + cumulative) ** (252.0 / periods) - 1.0) if cumulative > -1 else np.nan
    annualized_vol = float(returns.std(ddof=1) * math.sqrt(252.0)) if len(returns) > 1 else np.nan
    rf_daily = (1.0 + safe_float(annual_risk_free_rate_pct) / 100.0) ** (1.0 / 252.0) - 1.0
    excess = returns - rf_daily
    downside = np.minimum(excess, 0.0)
    downside_deviation = float(np.sqrt(np.mean(np.square(downside))) * math.sqrt(252.0)) if len(downside) else np.nan
    sharpe = float(excess.mean() / returns.std(ddof=1) * math.sqrt(252.0)) if len(returns) > 1 and returns.std(ddof=1) > 1e-12 else np.nan
    sortino = float(excess.mean() * 252.0 / downside_deviation) if np.isfinite(downside_deviation) and downside_deviation > 1e-12 else np.nan
    running_peak = nav.cummax()
    drawdown = nav / running_peak - 1.0
    max_drawdown = safe_float(drawdown.min(), np.nan)
    current_drawdown = safe_float(drawdown.iloc[-1], np.nan)
    calmar = annualized_return / abs(max_drawdown) if np.isfinite(annualized_return) and np.isfinite(max_drawdown) and max_drawdown < -1e-12 else np.nan
    q05 = safe_float(returns.quantile(0.05), np.nan)
    cvar = safe_float(returns[returns <= q05].mean(), np.nan) if np.isfinite(q05) else np.nan
    weekly = (1.0 + returns).resample("W-FRI").prod() - 1.0
    monthly = (1.0 + returns).resample("ME").prod() - 1.0
    metrics.update({
        "stockfund_twr_since_inception_pct": cumulative * 100.0,
        "stockfund_twr_annualized_pct": annualized_return * 100.0 if np.isfinite(annualized_return) else np.nan,
        "annualized_volatility_pct": annualized_vol * 100.0 if np.isfinite(annualized_vol) else np.nan,
        "downside_deviation_pct": downside_deviation * 100.0 if np.isfinite(downside_deviation) else np.nan,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        "maximum_drawdown_pct": max_drawdown * 100.0 if np.isfinite(max_drawdown) else np.nan,
        "current_drawdown_pct": current_drawdown * 100.0 if np.isfinite(current_drawdown) else np.nan,
        "historical_var_95_daily_pct": -q05 * 100.0 if np.isfinite(q05) else np.nan,
        "historical_cvar_95_daily_pct": -cvar * 100.0 if np.isfinite(cvar) else np.nan,
        "daily_return_skewness": safe_float(returns.skew(), np.nan),
        "daily_return_excess_kurtosis": safe_float(returns.kurt(), np.nan),
        "positive_day_pct": float((returns > 0).mean() * 100.0),
        "positive_month_pct": float((monthly > 0).mean() * 100.0) if len(monthly) else np.nan,
        "worst_day_pct": safe_float(returns.min(), np.nan) * 100.0,
        "best_day_pct": safe_float(returns.max(), np.nan) * 100.0,
        "worst_week_pct": safe_float(weekly.min(), np.nan) * 100.0 if len(weekly) else np.nan,
        "worst_month_pct": safe_float(monthly.min(), np.nan) * 100.0 if len(monthly) else np.nan,
    })
    if benchmark_returns is not None:
        bench = pd.to_numeric(benchmark_returns, errors="coerce").dropna()
        aligned = pd.concat([returns.rename("portfolio"), bench.rename("benchmark")], axis=1).dropna()
        if len(aligned) >= HISTORICAL_ANALYTICS_MIN_OBSERVATIONS and aligned["benchmark"].var(ddof=1) > 1e-12:
            beta = aligned["portfolio"].cov(aligned["benchmark"]) / aligned["benchmark"].var(ddof=1)
            # Jensen-style alpha must use excess returns when a non-zero
            # risk-free assumption is supplied. The previous implementation
            # omitted this adjustment and understated/overstated alpha when
            # beta differed from one.
            alpha = (
                (aligned["portfolio"] - rf_daily).mean()
                - beta * (aligned["benchmark"] - rf_daily).mean()
            ) * 252.0
            active = aligned["portfolio"] - aligned["benchmark"]
            tracking_error = active.std(ddof=1) * math.sqrt(252.0)
            information_ratio = active.mean() * 252.0 / tracking_error if tracking_error > 1e-12 else np.nan
            metrics.update({
                "benchmark_beta": beta,
                "benchmark_alpha_annualized_pct": alpha * 100.0,
                "benchmark_correlation": aligned["portfolio"].corr(aligned["benchmark"]),
                "tracking_error_annualized_pct": tracking_error * 100.0,
                "information_ratio": information_ratio,
            })
    return metrics


def _build_cash_deployment_events(df):
    if df is None or df.empty:
        return pd.DataFrame(), {}
    work = df.sort_values(["event_datetime", "source_row"]).copy()
    lots = []
    deployment_rows = []
    external_total = 0.0
    investment_types = {"BUY"}
    for _, row in work.iterrows():
        amount = safe_float(row.get("amount")) + safe_float(row.get("fee")) + safe_float(row.get("tax"))
        typ = str(row.get("type_norm", ""))
        dt = row.get("event_date")
        if pd.isna(dt) or abs(amount) <= 1e-12:
            continue
        if amount > 0:
            source_kind = "EXTERNAL_DEPOSIT" if typ in EXTERNAL_CASH_DEPOSIT_TYPES else "INTERNAL_CASH"
            lots.append({
                "source_kind": source_kind,
                "source_type": typ,
                "source_date": pd.Timestamp(dt).normalize(),
                "remaining_eur": amount,
                "original_eur": amount,
                "source_row": row.get("source_row"),
            })
            if source_kind == "EXTERNAL_DEPOSIT":
                external_total += amount
            continue
        need = -amount
        is_investment_buy = typ in investment_types and str(row.get("asset_class_clean", "")) in (ASSET_CLASSES_ALLOWED | {DERIVATIVE_ASSET_CLASS})
        while need > 1e-10 and lots:
            lot = lots[0]
            used = min(need, safe_float(lot.get("remaining_eur")))
            if is_investment_buy and lot.get("source_kind") == "EXTERNAL_DEPOSIT":
                delay = (pd.Timestamp(dt).normalize() - pd.Timestamp(lot["source_date"])).days
                deployment_rows.append({
                    "deposit_date": lot["source_date"],
                    "deployment_date": pd.Timestamp(dt).normalize(),
                    "delay_days": delay,
                    "deployed_eur": used,
                    "deposit_type": lot.get("source_type", ""),
                    "investment_name": row.get("security_name", ""),
                    "investment_isin": row.get("isin", ""),
                    "investment_asset_class": row.get("asset_class_clean", ""),
                })
            lot["remaining_eur"] -= used
            need -= used
            if lot["remaining_eur"] <= 1e-10:
                lots.pop(0)
        # Negative balances or omitted pre-export cash are represented as an
        # internal synthetic source so later deposits are not back-allocated.
        if need > 1e-10:
            need = 0.0
    events = pd.DataFrame(deployment_rows)
    metrics = {"external_cash_deposits_eur": external_total}
    if not events.empty and external_total > 1e-12:
        deployed_total = safe_float(events["deployed_eur"].sum())
        metrics["external_deposit_cash_matched_to_investments_eur"] = deployed_total
        metrics["external_deposit_cash_not_matched_to_investment_buys_eur"] = max(0.0, external_total - deployed_total)
        for days in [7, 30, 90]:
            deployed_within = safe_float(events.loc[events["delay_days"] <= days, "deployed_eur"].sum())
            # Denominator 1: every identified external deposit.
            metrics[f"external_deposits_invested_within_{days}d_pct"] = deployed_within / external_total * 100.0
            # Denominator 2: only external-deposit cash that was eventually
            # matched to an investment purchase.
            metrics[f"matched_deposit_cash_invested_within_{days}d_pct"] = deployed_within / deployed_total * 100.0 if deployed_total > 1e-12 else np.nan
        metrics["cash_weighted_average_deployment_days"] = _weighted_average(events["delay_days"], events["deployed_eur"])
        metrics["cash_weighted_median_deployment_days"] = _weighted_median(events["delay_days"], events["deployed_eur"])
        # Backward-compatible alias retained for older Streamlit/HTML readers.
        metrics["median_deposit_to_investment_days"] = metrics["cash_weighted_average_deployment_days"]
        metrics["external_deposits_eventually_invested_pct"] = deployed_total / external_total * 100.0
    return events, metrics


def build_wealth_contribution_table(combined, derivative_positions, interest=None):
    rows = []
    if combined is not None and not combined.empty:
        for _, row in combined.iterrows():
            open_pl = safe_float(row.get("live_unrealized_pl_acquisition_basis_eur")) if str(row.get("position_status", "")) == "ACTIVE" else 0.0
            realized_pl = safe_float(row.get("fifo_realized_pl_acquisition_basis_eur"))
            net_dividends = safe_float(row.get("total_net_dividends"))
            promo = safe_float(row.get("matched_promo_credit_eur"))
            economic = open_pl + realized_pl + net_dividends
            rows.append({
                "security_name": row.get("security_name", ""),
                "isin": row.get("isin", ""),
                "instrument_type": row.get("asset_class", "STOCK/FUND"),
                "position_status": row.get("position_status", ""),
                "current_value_eur": safe_float(row.get("live_current_value_eur")) if str(row.get("position_status", "")) == "ACTIVE" else 0.0,
                "open_pl_eur": open_pl,
                "realized_pl_eur": realized_pl,
                "net_income_eur": net_dividends,
                "economic_contribution_eur": economic,
                "matched_promo_eur": promo,
                "personal_benefit_contribution_eur": economic + promo,
                "fees_eur": safe_float(row.get("total_fees_paid_eur")),
                "trade_tax_eur": safe_float(row.get("total_tax_paid_eur")),
                "income_tax_eur": safe_float(row.get("total_dividend_tax_withheld")),
                "country": COUNTRY_BY_ISIN_PREFIX.get(str(row.get("isin", ""))[:2].upper(), str(row.get("isin", ""))[:2].upper() or "Unknown"),
                "currency": str(row.get("live_price_currency", "") or row.get("ticker_matched_currency", "") or "Unknown"),
            })
    if derivative_positions is not None and not derivative_positions.empty:
        for _, row in derivative_positions.iterrows():
            open_pl = safe_float(row.get("estimated_unrealized_pl_eur")) if str(row.get("position_status", "")) == "ACTIVE" else 0.0
            realized_pl = safe_float(row.get("realized_pl_with_tilg_eur"))
            economic = open_pl + realized_pl
            rows.append({
                "security_name": row.get("security_name", ""),
                "isin": row.get("isin", ""),
                "instrument_type": "DERIVATIVE",
                "position_status": row.get("position_status", ""),
                "current_value_eur": safe_float(row.get("estimated_live_value_eur")) if str(row.get("position_status", "")) == "ACTIVE" else 0.0,
                "open_pl_eur": open_pl,
                "realized_pl_eur": realized_pl,
                "net_income_eur": 0.0,
                "economic_contribution_eur": economic,
                "matched_promo_eur": 0.0,
                "personal_benefit_contribution_eur": economic,
                "fees_eur": safe_float(row.get("total_fees_paid_eur")),
                "trade_tax_eur": 0.0,
                "income_tax_eur": 0.0,
                "country": "Derivative",
                "currency": "EUR",
            })
    if interest is not None and not interest.empty:
        net_interest = safe_float(interest.get("net_interest_eur", pd.Series(dtype=float)).sum())
        gross_interest = safe_float(interest.get("gross_interest_eur", pd.Series(dtype=float)).sum())
        interest_tax = max(0.0, gross_interest - net_interest)
        if abs(net_interest) > 1e-12 or abs(gross_interest) > 1e-12:
            rows.append({
                "security_name": "Trade Republic cash interest",
                "isin": "",
                "instrument_type": "CASH",
                "position_status": "INCOME",
                "current_value_eur": 0.0,
                "open_pl_eur": 0.0,
                "realized_pl_eur": 0.0,
                "net_income_eur": net_interest,
                "economic_contribution_eur": net_interest,
                "matched_promo_eur": 0.0,
                "personal_benefit_contribution_eur": net_interest,
                "fees_eur": 0.0,
                "trade_tax_eur": 0.0,
                "income_tax_eur": interest_tax,
                "country": "Cash",
                "currency": "EUR",
            })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    total_profit = safe_float(out["economic_contribution_eur"].sum())
    out["share_of_lifetime_profit_pct"] = np.where(abs(total_profit) > 1e-12, out["economic_contribution_eur"] / total_profit * 100.0, np.nan)
    return out.sort_values("economic_contribution_eur", ascending=False).reset_index(drop=True)


def build_allocation_breakdown(wealth_contribution):
    if wealth_contribution is None or wealth_contribution.empty:
        return pd.DataFrame(columns=["dimension", "category", "current_value_eur", "weight_pct", "economic_contribution_eur"])
    active = wealth_contribution[wealth_contribution["current_value_eur"] > 1e-10].copy()
    rows = []
    for dimension, column in [("Asset class", "instrument_type"), ("Country / ISIN domicile", "country"), ("Trading currency", "currency")]:
        grouped = active.groupby(column, dropna=False).agg(
            current_value_eur=("current_value_eur", "sum"),
            economic_contribution_eur=("economic_contribution_eur", "sum"),
        ).reset_index().rename(columns={column: "category"})
        total = safe_float(grouped["current_value_eur"].sum())
        grouped["weight_pct"] = np.where(total > 1e-12, grouped["current_value_eur"] / total * 100.0, np.nan)
        grouped["dimension"] = dimension
        rows.append(grouped[["dimension", "category", "current_value_eur", "weight_pct", "economic_contribution_eur"]])
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def _benchmark_pme(cashflows, benchmark_price_eur, valuation_date):
    if cashflows is None or cashflows.empty or benchmark_price_eur is None or benchmark_price_eur.dropna().empty:
        return {}, pd.DataFrame(), pd.Series(dtype=float)
    price = pd.to_numeric(benchmark_price_eur, errors="coerce").dropna().sort_index()
    flows = cashflows.copy()
    flows["cashflow_date"] = pd.to_datetime(flows["cashflow_date"], errors="coerce").dt.normalize()
    flows["cashflow_eur"] = pd.to_numeric(flows["cashflow_eur"], errors="coerce")
    flows = flows.dropna().groupby("cashflow_date", as_index=False)["cashflow_eur"].sum().sort_values("cashflow_date")
    calendar = price.index.union(pd.DatetimeIndex(flows["cashflow_date"])).sort_values()
    price = price.reindex(calendar).ffill().bfill()
    flow_map = flows.set_index("cashflow_date")["cashflow_eur"].to_dict()
    units = 0.0
    rows = []
    values = []
    for dt, px in price.items():
        flow = safe_float(flow_map.get(pd.Timestamp(dt).normalize(), 0.0))
        units_before = units
        if np.isfinite(px) and px > 1e-12 and abs(flow) > 1e-12:
            units += -flow / px
        value = units * px if np.isfinite(px) else np.nan
        values.append((dt, value))
        if abs(flow) > 1e-12:
            rows.append({
                "cashflow_date": dt,
                "actual_cashflow_eur": flow,
                "benchmark_price_eur": px,
                "units_before": units_before,
                "units_change": -flow / px if np.isfinite(px) and px > 1e-12 else np.nan,
                "units_after": units,
                "benchmark_value_after_eur": value,
            })
    value_series = pd.Series({pd.Timestamp(dt): value for dt, value in values}, name="benchmark_value_eur").sort_index()
    terminal_date = pd.Timestamp(valuation_date).normalize()
    eligible = value_series.index[value_series.index <= terminal_date]
    terminal_value = safe_float(value_series.loc[eligible[-1]], np.nan) if len(eligible) else np.nan
    xirr_rows = flows.rename(columns={"cashflow_date": "cashflow_date", "cashflow_eur": "cashflow_eur"}).copy()
    if np.isfinite(terminal_value):
        xirr_rows = pd.concat([xirr_rows, pd.DataFrame([{"cashflow_date": terminal_date, "cashflow_eur": terminal_value}])], ignore_index=True)
    xirr = _solve_xirr(xirr_rows)
    metrics = {
        "benchmark_terminal_value_eur": terminal_value,
        "benchmark_mwr_pct": xirr.get("annualized_return_pct", np.nan),
        "benchmark_mwr_status": xirr.get("status", ""),
        "benchmark_units_terminal": units,
    }
    return metrics, pd.DataFrame(rows), value_series


def build_historical_analytics(
    df,
    trades,
    dividends,
    interest,
    holdings,
    combined,
    derivative_positions,
    realized_behavior_events,
    advanced_metrics,
    max_date,
    benchmark_ticker,
    benchmark_name,
    annual_risk_free_rate_pct=0.0,
):
    empty = {
        "metrics": {"historical_analytics_status": "DISABLED_OR_UNAVAILABLE"},
        "daily_nav": pd.DataFrame(), "monthly_returns": pd.DataFrame(),
        "calendar_returns": pd.DataFrame(), "rolling_returns": pd.DataFrame(),
        "period_performance": pd.DataFrame(), "period_performance_history": pd.DataFrame(),
        "drawdown_episodes": pd.DataFrame(), "benchmark_cashflows": pd.DataFrame(),
        "historical_ticker_audit": pd.DataFrame(), "cash_history": pd.DataFrame(),
        "cash_deployment_events": pd.DataFrame(), "wealth_contribution": pd.DataFrame(),
        "allocation_breakdown": pd.DataFrame(), "sold_hindsight": pd.DataFrame(),
        "story_messages": pd.DataFrame(),
    }
    if not ENABLE_HISTORICAL_ANALYTICS or trades is None or trades.empty:
        return empty

    start_date = pd.to_datetime(trades["event_date"], errors="coerce").min()
    valuation_candidates = [pd.Timestamp(max_date) if pd.notna(max_date) else pd.NaT]
    if holdings is not None and not holdings.empty and "live_price_date" in holdings.columns:
        valuation_candidates.extend(pd.to_datetime(holdings["live_price_date"], errors="coerce").dropna().tolist())
    valuation_date = max([d for d in valuation_candidates if pd.notna(d)], default=pd.Timestamp(max_date)).normalize()
    start_date = pd.Timestamp(start_date).normalize()
    event_dates = pd.to_datetime(trades["event_date"], errors="coerce").dropna().dt.normalize()
    calendar = pd.date_range(start_date, valuation_date, freq="B").union(
        pd.DatetimeIndex(event_dates)
    ).drop_duplicates().sort_values()

    holding_map = {}
    if holdings is not None and not holdings.empty:
        for _, row in holdings.iterrows():
            holding_map[str(row.get("isin", ""))] = row

    security_rows = []
    for isin, group in trades.groupby("isin", dropna=False):
        isin = str(isin)
        hrow = holding_map.get(isin, {})
        ticker = str(hrow.get("yahoo_ticker", "") if hasattr(hrow, "get") else "").strip()
        currency = str(hrow.get("live_price_currency", "") if hasattr(hrow, "get") else "").strip()
        match_status = str(hrow.get("ticker_match_status", "") if hasattr(hrow, "get") else "")
        # Derecognized holdings are no longer live-valued, so enrichment leaves
        # their ticker fields null.  Historical TWR must still resolve their
        # pre-derecognition market history rather than treating the string
        # representation of NaN as a ticker.
        if hasattr(hrow, "get") and str(hrow.get("closure_status", "")) == "DERECOGNIZED":
            if ticker.lower() in {"", "nan", "none", "<na>"}:
                ticker = ""
            if currency.lower() in {"", "nan", "none", "<na>"}:
                currency = ""
        name = str(group["security_name"].dropna().iloc[-1]) if group["security_name"].notna().any() else isin
        if not ticker and ENABLE_STOCK_FUND_LIVE_PRICES:
            try:
                match = find_best_yahoo_ticker(isin, name, str(group["asset_class_clean"].dropna().iloc[-1]))
                ticker = str(match.get("ticker", "") or "")
                currency = str(match.get("matched_currency", "") or currency)
                match_status = str(match.get("match_status", "") or match_status)
            except Exception as exc:
                match_status = f"HISTORY_TICKER_LOOKUP_FAILED: {exc}"
        security_rows.append({
            "isin": isin, "security_name": name, "ticker": ticker,
            "currency": currency, "ticker_match_status": match_status,
            "live_price_eur": safe_float(hrow.get("live_price_eur", np.nan), np.nan) if hasattr(hrow, "get") else np.nan,
            "live_price_date": hrow.get("live_price_date", "") if hasattr(hrow, "get") else "",
        })
    security_map = pd.DataFrame(security_rows)

    tickers = sorted({str(x).strip() for x in security_map["ticker"] if str(x).strip()})
    history_results = {}
    if tickers:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(HISTORICAL_PRICE_MAX_WORKERS, len(tickers))) as pool:
            futures = {pool.submit(_fetch_yahoo_history, ticker, start_date, valuation_date): ticker for ticker in tickers}
            for future, ticker in [(future, ticker) for future, ticker in futures.items()]:
                try:
                    history_results[ticker] = future.result()
                except Exception as exc:
                    history_results[ticker] = (_history_empty_frame(), "", "ERROR", str(exc))

    currencies = set()
    for _, row in security_map.iterrows():
        ticker = str(row.get("ticker", ""))
        result_currency = history_results.get(ticker, (_history_empty_frame(), "", "", ""))[1]
        norm_currency, _ = _normalize_currency_for_history(result_currency or row.get("currency", ""))
        if norm_currency not in {"EUR", "UNKNOWN", ""}:
            currencies.add(norm_currency)
    fx_histories = {}
    for currency in sorted(currencies):
        fx_frame, _, fx_status, fx_error = _fetch_yahoo_history(f"{currency}EUR=X", start_date, valuation_date)
        fx_histories[currency] = (fx_frame["close_native"] if not fx_frame.empty else pd.Series(dtype=float), fx_status, fx_error)

    price_by_isin = {}
    adjusted_by_isin = {}
    quality_by_isin = {}
    audit_rows = []
    for _, row in security_map.iterrows():
        isin = str(row["isin"])
        ticker = str(row.get("ticker", ""))
        native_frame, fetched_currency, status, error = history_results.get(ticker, (_history_empty_frame(), "", "NO_TICKER", ""))
        currency, unit_factor = _normalize_currency_for_history(fetched_currency or row.get("currency", ""))
        market_close = pd.Series(dtype=float)
        market_adjusted = pd.Series(dtype=float)
        if not native_frame.empty:
            market_close = native_frame["close_native"] * unit_factor
            market_adjusted = native_frame["adjusted_close_native"] * unit_factor
            if currency not in {"EUR", "UNKNOWN", ""}:
                fx_series = fx_histories.get(currency, (pd.Series(dtype=float), "", ""))[0]
                fx_series = pd.to_numeric(fx_series, errors="coerce").reindex(market_close.index.union(fx_series.index)).sort_index().ffill().bfill()
                market_close = market_close.reindex(fx_series.index) * fx_series
                market_adjusted = market_adjusted.reindex(fx_series.index) * fx_series
        external_observed_dates = set(pd.DatetimeIndex(market_close.dropna().index).normalize()) if not market_close.empty else set()
        market_close = market_close.reindex(calendar).ffill() if not market_close.empty else pd.Series(index=calendar, dtype=float)
        market_adjusted = market_adjusted.reindex(calendar).ffill() if not market_adjusted.empty else pd.Series(index=calendar, dtype=float)
        market_quality = pd.Series("UNRESOLVED", index=calendar, dtype=object)
        if market_close.notna().any():
            market_quality.loc[market_close.notna()] = "FORWARD_FILLED_MARKET_PRICE"
            observed_mask = market_quality.index.isin(external_observed_dates) & market_close.notna()
            market_quality.loc[observed_mask] = "EXTERNALLY_OBSERVED_MARKET_PRICE"
        fallback, fallback_quality = _transaction_price_fallback(
            trades[trades["isin"].astype(str).eq(isin)], calendar, valuation_date,
            row.get("live_price_eur", np.nan), row.get("live_price_date", ""),
        )
        final_close = market_close.combine_first(fallback)
        final_adjusted = market_adjusted.combine_first(final_close)
        final_quality = market_quality.where(market_close.notna(), fallback_quality)
        source = "YAHOO_CLOSE" if market_close.notna().any() else HISTORICAL_PRICE_FALLBACK_LABEL if fallback.notna().any() else "NO_HISTORY"
        price_by_isin[isin] = final_close
        adjusted_by_isin[isin] = final_adjusted
        quality_by_isin[isin] = final_quality
        observations = max(int(final_close.notna().sum()), 1)
        external_observed_count = int(final_quality.eq("EXTERNALLY_OBSERVED_MARKET_PRICE").sum())
        external_effective_count = int(final_quality.isin(["EXTERNALLY_OBSERVED_MARKET_PRICE", "FORWARD_FILLED_MARKET_PRICE"]).sum())
        fallback_count = int(final_quality.isin([
            "TRANSACTION_OBSERVED_PRICE", "EXTERNAL_LIVE_PRICE", "INTERPOLATED_PRICE",
            "FORWARD_FILLED_PRICE", "BACKWARD_FILLED_PRICE",
        ]).sum())
        audit_rows.append({
            "isin": isin, "security_name": row.get("security_name", ""), "yahoo_ticker": ticker,
            "currency": currency, "history_source": source, "history_status": status,
            "history_start": final_close.first_valid_index(), "history_end": final_close.last_valid_index(),
            "history_observations": int(final_close.notna().sum()),
            "externally_observed_market_price_observations": external_observed_count,
            "external_market_effective_observations": external_effective_count,
            "transaction_or_filled_fallback_observations": fallback_count,
            "unresolved_observations": int(final_close.isna().sum()),
            "externally_observed_market_price_coverage_pct": external_observed_count / observations * 100.0,
            "external_market_price_coverage_pct": external_effective_count / observations * 100.0,
            "fallback_price_coverage_pct": fallback_count / observations * 100.0,
            "unresolved_price_coverage_pct": int(final_close.isna().sum()) / len(final_close) * 100.0 if len(final_close) else np.nan,
            "error": error,
        })

    quantity_matrix = pd.DataFrame(index=calendar)
    value_matrix = pd.DataFrame(index=calendar)
    quality_matrix = pd.DataFrame(index=calendar)
    for isin, group in trades.groupby("isin", dropna=False):
        isin = str(isin)
        quantity_column = "historical_signed_quantity" if "historical_signed_quantity" in group.columns else "signed_quantity"
        changes = group.groupby(pd.to_datetime(group["event_date"], errors="coerce").dt.normalize())[quantity_column].sum()
        quantity = changes.reindex(calendar, fill_value=0.0).cumsum()
        price = price_by_isin.get(isin, pd.Series(index=calendar, dtype=float)).reindex(calendar)
        quantity_matrix[isin] = quantity
        value_matrix[isin] = quantity * price
        quality_matrix[isin] = quality_by_isin.get(isin, pd.Series("UNRESOLVED", index=calendar)).reindex(calendar).fillna("UNRESOLVED")

    active_mask = quantity_matrix.abs() > 1e-8
    priced_mask = value_matrix.notna() | ~active_mask
    coverage_pct = priced_mask.sum(axis=1) / max(len(priced_mask.columns), 1) * 100.0
    active_counts = active_mask.sum(axis=1)
    active_priced_counts = (active_mask & value_matrix.notna()).sum(axis=1)
    active_coverage_pct = np.where(active_counts > 0, active_priced_counts / active_counts * 100.0, 100.0)
    partial_portfolio_value = value_matrix.fillna(0.0).sum(axis=1)
    portfolio_value = partial_portfolio_value.where(pd.Series(active_coverage_pct, index=calendar) >= 99.999)
    external_effective = quality_matrix.isin(["EXTERNALLY_OBSERVED_MARKET_PRICE", "FORWARD_FILLED_MARKET_PRICE"])
    external_observed = quality_matrix.eq("EXTERNALLY_OBSERVED_MARKET_PRICE")
    fallback_effective = quality_matrix.isin([
        "TRANSACTION_OBSERVED_PRICE", "EXTERNAL_LIVE_PRICE", "INTERPOLATED_PRICE",
        "FORWARD_FILLED_PRICE", "BACKWARD_FILLED_PRICE",
    ])
    active_instrument_days = int(active_mask.sum().sum())
    external_effective_days = int((active_mask & external_effective).sum().sum())
    external_observed_days = int((active_mask & external_observed).sum().sum())
    fallback_days = int((active_mask & fallback_effective).sum().sum())
    unresolved_days = int((active_mask & quality_matrix.eq("UNRESOLVED")).sum().sum())

    external_flow = pd.Series(0.0, index=calendar)
    internal_reinvestment_flow = pd.Series(0.0, index=calendar)
    ordinary_buy_flow = trades[trades["type_norm"].eq("BUY")].groupby("event_date")["net_buy_cash_outflow_eur"].sum()
    reinvestment_flow = trades[trades["type_norm"].eq("DIVIDEND_REINVESTMENT")].groupby("event_date")["net_buy_cash_outflow_eur"].sum()
    sell_flow = trades[trades["type_norm"].eq("SELL")].groupby("event_date")["net_sell_cash_inflow_eur"].sum()
    for dt, amount in ordinary_buy_flow.items():
        dt = pd.Timestamp(dt).normalize()
        if dt in external_flow.index:
            external_flow.loc[dt] += safe_float(amount)
    for dt, amount in reinvestment_flow.items():
        dt = pd.Timestamp(dt).normalize()
        if dt in internal_reinvestment_flow.index:
            internal_reinvestment_flow.loc[dt] += safe_float(amount)
    for dt, amount in sell_flow.items():
        dt = pd.Timestamp(dt).normalize()
        if dt in external_flow.index:
            external_flow.loc[dt] -= safe_float(amount)
    twr_neutralizing_flow = external_flow + internal_reinvestment_flow
    dividend_income = pd.Series(0.0, index=calendar)
    if dividends is not None and not dividends.empty:
        grouped_div = dividends.groupby("payment_date")["net_dividend_eur"].sum()
        for dt, amount in grouped_div.items():
            dt = pd.Timestamp(dt).normalize()
            if dt in dividend_income.index:
                dividend_income.loc[dt] += safe_float(amount)

    previous_value = portfolio_value.shift(1)
    # Daily TWR convention: security cash flows are neutralized as end-of-day
    # flows. This avoids Modified-Dietz distortions on large purchases or full
    # liquidations when intraday portfolio valuations are unavailable.
    denominator = previous_value
    daily_return = (portfolio_value - previous_value - twr_neutralizing_flow + dividend_income) / denominator
    valid = (denominator > 1e-8) & (pd.Series(active_coverage_pct, index=calendar) >= 99.999)
    daily_return = daily_return.where(valid).replace([np.inf, -np.inf], np.nan)
    # Defensive normalization: event-date unions can retain duplicate labels on
    # some pandas versions. Historical analytics require one scalar observation
    # per calendar date.
    if isinstance(daily_return, pd.DataFrame):
        daily_return = daily_return.iloc[:, 0]
    daily_return = pd.Series(
        pd.to_numeric(daily_return, errors="coerce").to_numpy(dtype=float),
        index=pd.DatetimeIndex(calendar),
        dtype=float,
    ).groupby(level=0).last().reindex(calendar)
    nav_index = pd.Series(index=calendar, dtype=float)
    nav_level = 100.0
    for dt in calendar:
        r = daily_return.loc[dt]
        if np.isfinite(r):
            nav_level *= 1.0 + r
        nav_index.loc[dt] = nav_level
    running_peak = nav_index.cummax()
    drawdown = nav_index / running_peak - 1.0

    # Benchmark total-return proxy and PME.
    benchmark_frame, benchmark_currency_raw, benchmark_status, benchmark_error = _fetch_yahoo_history(benchmark_ticker, start_date, valuation_date)
    benchmark_currency, benchmark_factor = _normalize_currency_for_history(benchmark_currency_raw)
    benchmark_adjusted_eur = pd.Series(dtype=float)
    benchmark_close_eur = pd.Series(dtype=float)
    if not benchmark_frame.empty:
        benchmark_adjusted_eur = benchmark_frame["adjusted_close_native"] * benchmark_factor
        benchmark_close_eur = benchmark_frame["close_native"] * benchmark_factor
        if benchmark_currency not in {"EUR", "UNKNOWN", ""}:
            fx_series, _, _ = fx_histories.get(benchmark_currency, (pd.Series(dtype=float), "", ""))
            if fx_series.empty:
                fx_frame, _, _, _ = _fetch_yahoo_history(f"{benchmark_currency}EUR=X", start_date, valuation_date)
                fx_series = fx_frame["close_native"] if not fx_frame.empty else pd.Series(dtype=float)
            union = benchmark_adjusted_eur.index.union(fx_series.index)
            fx_series = fx_series.reindex(union).sort_index().ffill().bfill()
            benchmark_adjusted_eur = benchmark_adjusted_eur.reindex(union) * fx_series
            benchmark_close_eur = benchmark_close_eur.reindex(union) * fx_series
    benchmark_adjusted_eur = benchmark_adjusted_eur.reindex(calendar).ffill().bfill() if not benchmark_adjusted_eur.empty else pd.Series(index=calendar, dtype=float)
    benchmark_return = benchmark_adjusted_eur.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)

    pme_cashflows = []
    for _, row in trades.iterrows():
        typ = str(row.get("type_norm", ""))
        if typ in STOCK_ACQUISITION_TYPES:
            amount = -safe_float(row.get("net_buy_cash_outflow_eur"))
        elif typ == "SELL":
            amount = safe_float(row.get("net_sell_cash_inflow_eur"))
        else:
            continue
        pme_cashflows.append({"cashflow_date": row.get("event_date"), "cashflow_eur": amount})
    if dividends is not None and not dividends.empty:
        pme_cashflows.extend({"cashflow_date": r.get("payment_date"), "cashflow_eur": safe_float(r.get("net_dividend_eur"))} for _, r in dividends.iterrows())
    pme_cashflows_df = pd.DataFrame(pme_cashflows)
    pme_metrics, benchmark_cashflows, benchmark_value = _benchmark_pme(pme_cashflows_df, benchmark_adjusted_eur, valuation_date)

    external_daily_pct = np.where(active_counts > 0, (active_mask & external_effective).sum(axis=1) / active_counts * 100.0, 100.0)
    fallback_daily_pct = np.where(active_counts > 0, (active_mask & fallback_effective).sum(axis=1) / active_counts * 100.0, 0.0)
    unresolved_daily_pct = np.where(active_counts > 0, (active_mask & quality_matrix.eq("UNRESOLVED")).sum(axis=1) / active_counts * 100.0, 0.0)
    daily_nav = pd.DataFrame({
        "date": calendar,
        "stockfund_value_eur": portfolio_value.values,
        "partial_known_stockfund_value_eur": partial_portfolio_value.values,
        "external_flow_to_stockfund_eur": external_flow.values,
        "internal_dividend_reinvestment_flow_eur": internal_reinvestment_flow.values,
        "twr_neutralizing_flow_eur": twr_neutralizing_flow.values,
        "net_dividend_return_eur": dividend_income.values,
        "daily_twr_return_pct": daily_return.values * 100.0,
        "stockfund_nav_index": nav_index.values,
        "stockfund_drawdown_pct": drawdown.values * 100.0,
        "price_coverage_pct": active_coverage_pct,
        "external_market_price_coverage_pct": external_daily_pct,
        "fallback_price_coverage_pct": fallback_daily_pct,
        "unresolved_price_coverage_pct": unresolved_daily_pct,
        "benchmark_price_total_return_eur": benchmark_adjusted_eur.reindex(calendar).values,
        "benchmark_daily_return_pct": benchmark_return.reindex(calendar).values * 100.0,
        "benchmark_pme_value_eur": benchmark_value.reindex(calendar).values if not benchmark_value.empty else np.nan,
    })

    monthly_nav = nav_index.resample("ME").last()
    monthly_return = monthly_nav.pct_change()
    if len(monthly_nav):
        monthly_return.iloc[0] = monthly_nav.iloc[0] / 100.0 - 1.0
    monthly_returns = pd.DataFrame({
        "month_end": monthly_return.index,
        "year": monthly_return.index.year,
        "month": monthly_return.index.month,
        "month_name": monthly_return.index.strftime("%b"),
        "return_pct": monthly_return.values * 100.0,
    })
    calendar_returns = monthly_returns.groupby("year", as_index=False).agg(
        return_pct=("return_pct", lambda x: ((1.0 + pd.to_numeric(x, errors="coerce") / 100.0).prod() - 1.0) * 100.0),
        positive_months=("return_pct", lambda x: int((pd.to_numeric(x, errors="coerce") > 0).sum())),
        months=("return_pct", "count"),
    ) if not monthly_returns.empty else pd.DataFrame()
    rolling = pd.DataFrame({"date": calendar})
    for label, periods_count in [("rolling_3m_pct", 63), ("rolling_6m_pct", 126), ("rolling_12m_pct", 252), ("rolling_24m_pct", 504)]:
        rolling[label] = (nav_index / nav_index.shift(periods_count) - 1.0).values * 100.0
    period_performance, period_performance_history = build_period_performance(
        nav_index,
        benchmark_adjusted_eur,
        daily_return,
        benchmark_name,
    )
    drawdown_episodes = _drawdown_episode_table(nav_index)

    risk_metrics = _risk_metrics_from_returns(daily_return, nav_index, benchmark_return, annual_risk_free_rate_pct)
    actual_terminal = safe_float(portfolio_value.iloc[-1], np.nan)
    benchmark_terminal = safe_float(pme_metrics.get("benchmark_terminal_value_eur"), np.nan)
    enough_history = len(daily_return.dropna()) >= HISTORICAL_ANALYTICS_MIN_OBSERVATIONS
    if not enough_history:
        historical_status = "LIMITED_HISTORY"
    elif unresolved_days:
        historical_status = "INCOMPLETE_PRICE_HISTORY"
    elif fallback_days:
        historical_status = "OK_WITH_LOW_CONFIDENCE_FALLBACK"
    else:
        historical_status = "OK"
    risk_metrics.update({
        "historical_analytics_status": historical_status,
        "daily_twr_cashflow_convention": "END_OF_DAY",
        "historical_derivative_scope": "EXCLUDED_FROM_DAILY_TWR_AND_DRAWDOWN",
        "historical_start_date": start_date,
        "historical_valuation_date": valuation_date,
        "historical_price_coverage_avg_pct": safe_float(pd.Series(active_coverage_pct).mean(), np.nan),
        "historical_price_coverage_min_pct": safe_float(pd.Series(active_coverage_pct).min(), np.nan),
        "externally_observed_market_price_coverage_pct": external_observed_days / active_instrument_days * 100.0 if active_instrument_days else np.nan,
        "external_market_price_coverage_pct": external_effective_days / active_instrument_days * 100.0 if active_instrument_days else np.nan,
        "fallback_price_coverage_pct": fallback_days / active_instrument_days * 100.0 if active_instrument_days else np.nan,
        "unresolved_price_coverage_pct": unresolved_days / active_instrument_days * 100.0 if active_instrument_days else np.nan,
        "historical_price_coverage_semantics": "External market, fallback, and unresolved instrument-days are reported separately; legacy coverage includes all usable prices.",
        "benchmark_ticker": benchmark_ticker,
        "benchmark_name": benchmark_name,
        "benchmark_history_status": benchmark_status,
        "benchmark_history_error": benchmark_error,
        "benchmark_terminal_value_eur": benchmark_terminal,
        "benchmark_mwr_pct": pme_metrics.get("benchmark_mwr_pct", np.nan),
        "benchmark_mwr_status": pme_metrics.get("benchmark_mwr_status", ""),
        "active_wealth_vs_benchmark_eur": actual_terminal - benchmark_terminal if np.isfinite(actual_terminal) and np.isfinite(benchmark_terminal) else np.nan,
        "public_market_equivalent_ratio": actual_terminal / benchmark_terminal if np.isfinite(actual_terminal) and np.isfinite(benchmark_terminal) and benchmark_terminal > 1e-12 else np.nan,
        "excess_mwr_vs_benchmark_pct_points": safe_float(advanced_metrics.get("stock_fund_mwr_acquisition_pct"), np.nan) - safe_float(pme_metrics.get("benchmark_mwr_pct"), np.nan) if np.isfinite(safe_float(pme_metrics.get("benchmark_mwr_pct"), np.nan)) else np.nan,
    })

    # Cash history and deployment analytics. Use every transaction date, not
    # only security-trade dates, otherwise deposits and withdrawals that occur
    # on non-trading days disappear from the reconstructed cash balance.
    raw_cash = df.copy()
    raw_cash["event_date"] = pd.to_datetime(raw_cash["event_date"], errors="coerce").dt.normalize()
    raw_cash["cash_effect_eur"] = raw_cash["amount"].fillna(0) + raw_cash["fee"].fillna(0) + raw_cash["tax"].fillna(0)
    all_cash_dates = pd.DatetimeIndex(raw_cash["event_date"].dropna())
    cash_start_date = all_cash_dates.min() if len(all_cash_dates) else start_date
    cash_calendar = pd.date_range(cash_start_date, valuation_date, freq="B").union(
        all_cash_dates
    ).drop_duplicates().sort_values()
    daily_cash_change = raw_cash.groupby("event_date")["cash_effect_eur"].sum()
    cash_balance = daily_cash_change.reindex(cash_calendar, fill_value=0.0).cumsum()
    stockfund_value_on_cash_calendar = portfolio_value.reindex(cash_calendar).ffill().fillna(0.0)
    total_wealth_proxy = cash_balance + stockfund_value_on_cash_calendar
    cash_allocation = np.where(
        total_wealth_proxy > 1e-8,
        cash_balance / total_wealth_proxy * 100.0,
        np.nan,
    )
    cash_history = pd.DataFrame({
        "date": cash_calendar,
        "cash_balance_proxy_eur": cash_balance.values,
        "stockfund_value_eur": stockfund_value_on_cash_calendar.values,
        "cash_plus_stockfund_wealth_eur": total_wealth_proxy.values,
        "cash_allocation_pct": cash_allocation,
    })
    positive_cash = cash_balance.clip(lower=0)
    years = max((valuation_date - cash_start_date).days / 365.0, 1 / 365.0)
    net_interest = safe_float(interest["net_interest_eur"].sum()) if interest is not None and not interest.empty else 0.0
    benchmark_return_cash_calendar = benchmark_return.reindex(cash_calendar).ffill().fillna(0.0)
    gross_benchmark_opportunity_difference = np.nansum(
        positive_cash.shift(1).fillna(0).values * benchmark_return_cash_calendar.values
    )
    net_benchmark_opportunity_difference = gross_benchmark_opportunity_difference - net_interest
    deployment_events, deployment_metrics = _build_cash_deployment_events(df)
    risk_metrics.update({
        "average_cash_balance_eur": safe_float(positive_cash.mean()),
        "peak_cash_balance_eur": safe_float(cash_balance.max()),
        "minimum_cash_balance_eur": safe_float(cash_balance.min()),
        "average_cash_allocation_pct": safe_float(pd.Series(cash_allocation).dropna().mean(), np.nan),
        "cash_days_above_500_eur": int((positive_cash > 500).sum()),
        "cash_interest_yield_annualized_pct": net_interest / safe_float(positive_cash.mean()) / years * 100.0 if safe_float(positive_cash.mean()) > 1e-12 else np.nan,
        "gross_benchmark_opportunity_difference_eur": gross_benchmark_opportunity_difference,
        "net_benchmark_opportunity_difference_after_interest_eur": net_benchmark_opportunity_difference,
        # Compatibility alias retained for older HTML/CSV consumers.
        "cash_drag_vs_benchmark_proxy_eur": gross_benchmark_opportunity_difference,
        **deployment_metrics,
    })

    wealth_contribution = build_wealth_contribution_table(combined, derivative_positions, interest)
    allocation_breakdown = build_allocation_breakdown(wealth_contribution)

    # Sold-position hindsight; derivatives are excluded because historical derivative quotes are not reliable.
    hindsight_rows = []
    if realized_behavior_events is not None and not realized_behavior_events.empty:
        for _, event in realized_behavior_events.iterrows():
            if str(event.get("instrument_type", "")).upper() == "DERIVATIVE":
                continue
            isin = str(event.get("isin", ""))
            prices = price_by_isin.get(isin, pd.Series(dtype=float)).dropna()
            event_date = pd.to_datetime(event.get("event_date"), errors="coerce")
            qty = safe_float(event.get("quantity_closed"))
            current_price = safe_float(prices.iloc[-1], np.nan) if not prices.empty else np.nan
            after = prices[prices.index >= pd.Timestamp(event_date).normalize()] if pd.notna(event_date) and not prices.empty else pd.Series(dtype=float)
            max_after = safe_float(after.max(), np.nan) if not after.empty else np.nan
            proceeds = safe_float(event.get("net_proceeds_eur"))
            hypothetical = qty * current_price if qty > 0 and np.isfinite(current_price) else np.nan
            opportunity = hypothetical - proceeds if np.isfinite(hypothetical) else np.nan
            hindsight_rows.append({
                "event_date": event_date,
                "security_name": event.get("security_name", ""),
                "isin": isin,
                "quantity_sold": qty,
                "actual_net_proceeds_eur": proceeds,
                "realized_pl_eur": safe_float(event.get("realized_pl_eur")),
                "sale_price_net_per_unit_eur": proceeds / qty if qty > 1e-12 else np.nan,
                "current_price_eur": current_price,
                "hypothetical_current_value_eur": hypothetical,
                "post_sale_opportunity_eur": opportunity,
                "current_price_vs_sale_pct": (current_price / (proceeds / qty) - 1.0) * 100.0 if qty > 1e-12 and proceeds > 1e-12 and np.isfinite(current_price) else np.nan,
                "maximum_price_after_sale_eur": max_after,
                "days_since_sale": (valuation_date - pd.Timestamp(event_date).normalize()).days if pd.notna(event_date) else np.nan,
                "hindsight_status": "AVAILABLE" if np.isfinite(current_price) else "NO_CURRENT_PRICE",
                "note": "Excludes dividends after sale and returns earned by reinvested proceeds.",
            })
    sold_hindsight = pd.DataFrame(hindsight_rows)
    if not sold_hindsight.empty:
        sold_hindsight = sold_hindsight.sort_values("post_sale_opportunity_eur", ascending=False).reset_index(drop=True)
        risk_metrics.update({
            "sold_hindsight_events_available": int(sold_hindsight["hindsight_status"].eq("AVAILABLE").sum()),
            "sold_hindsight_net_opportunity_eur": safe_float(sold_hindsight["post_sale_opportunity_eur"].sum()),
            "largest_post_sale_opportunity_name": str(sold_hindsight.iloc[0]["security_name"]),
            "largest_post_sale_opportunity_eur": safe_float(sold_hindsight.iloc[0]["post_sale_opportunity_eur"]),
        })

    story_messages = []
    if np.isfinite(safe_float(risk_metrics.get("active_wealth_vs_benchmark_eur"), np.nan)):
        active = safe_float(risk_metrics["active_wealth_vs_benchmark_eur"])
        story_messages.append({
            "severity": "GOOD" if active >= 0 else "WATCH", "category": "Benchmark",
            "title": "Cash-flow-matched benchmark comparison",
            "message": f"The stock/fund sleeve is {abs(active):,.2f} EUR {'ahead of' if active >= 0 else 'behind'} the {benchmark_name} cash-flow-matched benchmark at the valuation date.",
        })
    if np.isfinite(safe_float(risk_metrics.get("maximum_drawdown_pct"), np.nan)):
        story_messages.append({
            "severity": "WATCH" if safe_float(risk_metrics["maximum_drawdown_pct"]) <= -20 else "INFO", "category": "Risk",
            "title": "Historical drawdown",
            "message": f"Maximum reconstructed stock/fund drawdown was {safe_float(risk_metrics['maximum_drawdown_pct']):.2f}%; current drawdown is {safe_float(risk_metrics.get('current_drawdown_pct')):.2f}%.",
        })
    if wealth_contribution is not None and not wealth_contribution.empty:
        top = wealth_contribution.iloc[0]
        story_messages.append({
            "severity": "INFO", "category": "Attribution", "title": "Largest lifetime contributor",
            "message": f"{top['security_name']} contributed {safe_float(top['economic_contribution_eur']):,.2f} EUR, equal to {safe_float(top['share_of_lifetime_profit_pct']):.1f}% of attributed investment profit.",
        })
    if np.isfinite(safe_float(risk_metrics.get("external_deposits_invested_within_30d_pct"), np.nan)):
        story_messages.append({
            "severity": "GOOD" if safe_float(risk_metrics["external_deposits_invested_within_30d_pct"]) >= 75 else "INFO",
            "category": "Cash", "title": "Deposit deployment speed",
            "message": f"{safe_float(risk_metrics['external_deposits_invested_within_30d_pct']):.1f}% of external deposits were matched to investment purchases within 30 days under FIFO cash-source attribution.",
        })
    story_messages = pd.DataFrame(story_messages)

    return {
        "metrics": risk_metrics,
        "daily_nav": daily_nav,
        "monthly_returns": monthly_returns,
        "calendar_returns": calendar_returns,
        "rolling_returns": rolling,
        "period_performance": period_performance,
        "period_performance_history": period_performance_history,
        "drawdown_episodes": drawdown_episodes,
        "benchmark_cashflows": benchmark_cashflows,
        "historical_ticker_audit": pd.DataFrame(audit_rows),
        "cash_history": cash_history,
        "cash_deployment_events": deployment_events,
        "wealth_contribution": wealth_contribution,
        "allocation_breakdown": allocation_breakdown,
        "sold_hindsight": sold_hindsight,
        "story_messages": story_messages,
    }


DIVIDEND_CALENDAR_COLUMNS = [
    "calendar_event_id", "security_name", "isin", "calendar_date", "calendar_year",
    "calendar_month", "date_role", "event_class", "underlying_category", "status_label",
    "payment_date", "expected_payment_date", "ex_dividend_date", "gross_dividend_eur",
    "estimated_net_dividend_eur", "net_status", "entitlement_quantity", "gross_dps",
    "dividend_currency", "source_provider", "source_ticker", "confidence", "warning",
    "pending_payment", "distribution_mode", "foreign_withholding_tax_eur",
    "domestic_tax_and_solidarity_surcharge_eur", "total_dividend_tax_eur",
    "reinvested_amount_eur", "reinvested_quantity", "net_settlement_cash_effect_eur",
    "gross_source", "foreign_withholding_source",
]


def build_unified_dividend_calendar_events(dividends, upcoming_dividends, dividend_forecast_events, asof):
    """Create a presentation-only calendar adapter over canonical dividend ledgers.

    Amounts, entitlement, reconciliation, and net-retention results are copied from
    their canonical sources.  This function only classifies and date-labels rows for
    the HTML calendar; explicit null and numeric zero retain different meanings.
    """
    rows = []
    asof_ts = pd.to_datetime(asof, errors="coerce")

    def text(value):
        return "" if value is None or pd.isna(value) else str(value)

    def number(value):
        if value is None or pd.isna(value):
            return None
        parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        return None if pd.isna(parsed) else float(parsed)

    def date_text(value):
        parsed = pd.to_datetime(value, errors="coerce")
        return None if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")

    def append_row(row):
        calendar_date = date_text(row.get("calendar_date"))
        parsed = pd.to_datetime(calendar_date, errors="coerce")
        row["calendar_date"] = calendar_date
        row["calendar_year"] = None if pd.isna(parsed) else int(parsed.year)
        row["calendar_month"] = None if pd.isna(parsed) else int(parsed.month)
        rows.append({column: row.get(column) for column in DIVIDEND_CALENDAR_COLUMNS})

    actual = dividends if isinstance(dividends, pd.DataFrame) else pd.DataFrame()
    for event_number, (_, record) in enumerate(actual.iterrows(), start=1):
        payment_date = date_text(record.get("payment_date"))
        isin = text(record.get("isin"))
        net_amount = number(record.get("net_dividend_eur"))
        warning = ""
        reconciliation_error = number(record.get("reconciliation_error"))
        if reconciliation_error is not None and abs(reconciliation_error) > 1e-9:
            warning = f"Broker dividend reconciliation difference: {reconciliation_error:.6f} EUR"
        append_row({
            # Presentation identifiers must never reuse the broker transaction UUID.
            # Date + ISIN + stable ledger order remains unique for the calendar while
            # keeping raw transaction IDs and source-row identifiers out of HTML.
            "calendar_event_id": f"ACTUAL:{payment_date or 'UNKNOWN'}:{isin or 'NO_ISIN'}:{event_number}",
            "security_name": text(record.get("security_name")),
            "isin": isin,
            "calendar_date": payment_date,
            "date_role": "CONFIRMED_BROKER_PAYMENT_DATE",
            "event_class": "ACTUAL_RECEIVED",
            "underlying_category": "ACTUAL",
            "status_label": "Actual — Reinvested" if text(record.get("distribution_mode")).upper() == "REINVESTED" else "Actual received",
            "payment_date": payment_date,
            "expected_payment_date": payment_date,
            "ex_dividend_date": None,
            "gross_dividend_eur": number(record.get("gross_dividend_eur")),
            "estimated_net_dividend_eur": net_amount,
            "net_status": "KNOWN" if net_amount is not None else "UNKNOWN",
            "entitlement_quantity": number(record.get("shares")),
            "gross_dps": number(record.get("dividend_per_share_gross_eur")),
            "dividend_currency": text(record.get("currency_clean")) or "EUR",
            "source_provider": "Trade Republic CSV",
            "source_ticker": "",
            "confidence": "BROKER_OBSERVED",
            "warning": warning,
            "pending_payment": False,
            "distribution_mode": text(record.get("distribution_mode")) or "CASH",
            "foreign_withholding_tax_eur": number(record.get("foreign_withholding_tax_eur")),
            "domestic_tax_and_solidarity_surcharge_eur": number(record.get("domestic_tax_and_solidarity_surcharge_eur")),
            "total_dividend_tax_eur": number(record.get("total_dividend_tax_eur")),
            "reinvested_amount_eur": number(record.get("reinvested_amount_eur")),
            "reinvested_quantity": number(record.get("reinvested_quantity")),
            "net_settlement_cash_effect_eur": number(record.get("net_settlement_cash_effect_eur")),
            "gross_source": text(record.get("gross_source")),
            "foreign_withholding_source": text(record.get("foreign_withholding_source")),
        })

    upcoming = upcoming_dividends if isinstance(upcoming_dividends, pd.DataFrame) else pd.DataFrame()
    upcoming_by_id = {
        text(record.get("event_id")): record
        for _, record in upcoming.iterrows()
        if text(record.get("event_id"))
    }
    forecast = dividend_forecast_events if isinstance(dividend_forecast_events, pd.DataFrame) else pd.DataFrame()
    seen_external_ids = set()
    for index, record in forecast.iterrows():
        event_id = text(record.get("event_id")) or f"FORECAST:{index}"
        seen_external_ids.add(event_id)
        detail = upcoming_by_id.get(event_id, {})
        category = text(record.get("category")).upper() or "ESTIMATED"
        method = text(record.get("method"))
        expected_payment_date = date_text(record.get("expected_payment_date"))
        ex_date = date_text(record.get("ex_dividend_date")) or date_text(getattr(detail, "get", lambda *_: None)("ex_dividend_date"))
        calendar_date = expected_payment_date or ex_date
        if method == "EXTERNAL_PAYMENT_DATE":
            date_role = "CONFIRMED_EXTERNAL_PAYMENT_DATE"
            payment_date = expected_payment_date
        elif method == "EXTERNAL_HISTORY_PAYMENT_LAG_ESTIMATE":
            date_role = "ESTIMATED_PAYMENT_LAG"
            payment_date = None
        elif method == "EXTERNAL_HISTORY_EX_DATE_TIMING_PROXY":
            date_role = "EX_DATE_TIMING_PROXY"
            payment_date = None
        else:
            date_role = "EXPECTED_DATE_UNCONFIRMED" if expected_payment_date else "EX_DATE_ONLY"
            payment_date = None
        gross_amount = number(record.get("gross_dividend_eur"))
        net_amount = number(record.get("estimated_net_dividend_eur"))
        unresolved = []
        if calendar_date is None:
            unresolved.append("DATE_UNKNOWN")
        if gross_amount is None:
            unresolved.append("GROSS_UNKNOWN")
        if net_amount is None:
            unresolved.append("NET_UNKNOWN")
        event_class = (
            "UNRESOLVED" if unresolved
            else "DECLARED_LOCKED" if category == "DECLARED"
            else "ESTIMATED_SEASONAL"
        )
        detail_get = getattr(detail, "get", lambda *_: None)
        warnings = [text(detail_get("diagnostic"))]
        if date_role in {"ESTIMATED_PAYMENT_LAG", "EX_DATE_TIMING_PROXY"}:
            warnings.append("Payment timing is an estimate, not a confirmed payment date")
        warnings.extend(unresolved)
        status_label = "Declared / locked" if category == "DECLARED" else "Estimated / seasonal"
        if unresolved:
            status_label += " · unresolved"
        append_row({
            "calendar_event_id": event_id,
            "security_name": text(record.get("security_name")),
            "isin": text(record.get("isin")),
            "calendar_date": calendar_date,
            "date_role": date_role,
            "event_class": event_class,
            "underlying_category": category,
            "status_label": status_label,
            "payment_date": payment_date,
            "expected_payment_date": expected_payment_date,
            "ex_dividend_date": ex_date,
            "gross_dividend_eur": gross_amount,
            "estimated_net_dividend_eur": net_amount,
            "net_status": "KNOWN" if net_amount is not None else "UNKNOWN",
            "entitlement_quantity": number(detail_get("entitlement_quantity")),
            "gross_dps": number(detail_get("declared_dividend_per_share_eur")),
            "dividend_currency": text(detail_get("dividend_currency")) or "EUR",
            "source_provider": text(detail_get("source_provider")) or "Yahoo/yfinance",
            "source_ticker": text(record.get("source_ticker")) or text(detail_get("source_ticker")),
            "confidence": text(record.get("confidence")) or text(detail_get("confidence")),
            "warning": "; ".join(part for part in warnings if part),
            "pending_payment": bool(pd.isna(asof_ts) or pd.isna(pd.to_datetime(calendar_date, errors="coerce")) or pd.to_datetime(calendar_date) >= asof_ts),
        })

    # Declared entitlements without a payment date are excluded from the scheduled
    # forecast by design.  Preserve them in the visual calendar on their known
    # ex-date, explicitly labelled as unresolved rather than inventing a payment date.
    for index, record in upcoming.iterrows():
        event_id = text(record.get("event_id")) or f"UPCOMING:{index}"
        if event_id in seen_external_ids or text(record.get("forecast_inclusion")) != "UNSCHEDULED":
            continue
        ex_date = date_text(record.get("ex_dividend_date"))
        gross_amount = number(record.get("expected_gross_dividend_eur"))
        net_amount = number(record.get("estimated_net_dividend_eur"))
        warnings = [text(record.get("diagnostic")), "Payment date unavailable; positioned by ex-date"]
        if net_amount is None:
            warnings.append("NET_UNKNOWN")
        append_row({
            "calendar_event_id": event_id,
            "security_name": text(record.get("security_name")),
            "isin": text(record.get("isin")),
            "calendar_date": ex_date,
            "date_role": "EX_DATE_ONLY",
            "event_class": "UNRESOLVED",
            "underlying_category": "DECLARED",
            "status_label": "Declared / locked · payment date unresolved",
            "payment_date": None,
            "expected_payment_date": None,
            "ex_dividend_date": ex_date,
            "gross_dividend_eur": gross_amount,
            "estimated_net_dividend_eur": net_amount,
            "net_status": "KNOWN" if net_amount is not None else "UNKNOWN",
            "entitlement_quantity": number(record.get("entitlement_quantity")),
            "gross_dps": number(record.get("declared_dividend_per_share_eur")),
            "dividend_currency": text(record.get("dividend_currency")) or "EUR",
            "source_provider": text(record.get("source_provider")),
            "source_ticker": text(record.get("source_ticker")),
            "confidence": text(record.get("confidence")),
            "warning": "; ".join(part for part in warnings if part),
            "pending_payment": True,
        })

    result = pd.DataFrame(rows, columns=DIVIDEND_CALENDAR_COLUMNS)
    if result.empty:
        return result
    return result.sort_values(
        ["calendar_date", "security_name", "calendar_event_id"],
        na_position="last",
        kind="stable",
    ).reset_index(drop=True)


# ============================================================
# 8. Build all data
# ============================================================

from worker_hooks import install_hooks
install_hooks(globals())

stage_start(1, 7, "Reading and validating the Trade Republic CSV")
SOURCE_FILE_HASH = file_sha256(INPUT_CSV)
raw = pd.read_csv(INPUT_CSV)
df = classify_for_diagnostics(normalize_raw_dataframe(raw))
dividend_reinvestment_match = match_dividend_reinvestments(df)
dividend_reinvestment_events = dividend_reinvestment_match["events"]
dividend_reinvestment_diagnostics = dividend_reinvestment_match["diagnostics"]
matched_dividend_reinvestment_source_rows = dividend_reinvestment_match["matched_source_rows"]
matched_dividend_reinvestment_action_rows = dividend_reinvestment_match["matched_action_source_rows"]
dividend_reinvestment_candidate_cash_rows = dividend_reinvestment_match["candidate_cash_source_rows"]
unresolved_dividend_reinvestment_source_rows = dividend_reinvestment_match["unresolved_source_rows"]
worthless_derecognition_match = match_known_worthless_derecognitions(df)
worthless_derecognition_events = worthless_derecognition_match["events"]
worthless_derecognition_diagnostics = worthless_derecognition_match["diagnostics"]
matched_worthless_derecognition_source_rows = worthless_derecognition_match["matched_source_rows"]
unresolved_worthless_derecognition_source_rows = worthless_derecognition_match["unresolved_source_rows"]
if matched_dividend_reinvestment_action_rows:
    matched_mask = df["source_row"].isin(matched_dividend_reinvestment_action_rows)
    df.loc[matched_mask, "normalized_category"] = "Dividend reinvestment / Wahldividende"
    df.loc[matched_mask, "include_in_portfolio_model"] = "TRUE_DIVIDEND_REINVESTMENT"
    df.loc[matched_mask, "manual_review_flag"] = "OK"
if unresolved_dividend_reinvestment_source_rows:
    unresolved_reinvestment_mask = df["source_row"].isin(unresolved_dividend_reinvestment_source_rows)
    df.loc[unresolved_reinvestment_mask, "normalized_category"] = "Unknown / manual review"
    df.loc[unresolved_reinvestment_mask, "include_in_portfolio_model"] = "REVIEW"
    df.loc[unresolved_reinvestment_mask, "manual_review_flag"] = "REVIEW"
if matched_worthless_derecognition_source_rows:
    derecognition_mask = df["source_row"].isin(matched_worthless_derecognition_source_rows)
    df.loc[derecognition_mask, "broker_type_norm"] = df.loc[derecognition_mask, "type_norm"]
    df.loc[derecognition_mask, "type_norm"] = WORTHLESS_DERECOGNITION
    df.loc[derecognition_mask, "normalized_category"] = "Worthless security derecognition"
    df.loc[derecognition_mask, "include_in_portfolio_model"] = "TRUE_WORTHLESS_DERECOGNITION"
    df.loc[derecognition_mask, "manual_review_flag"] = "OK"
    event_by_row = worthless_derecognition_events.set_index("source_row")
    df.loc[derecognition_mask, "known_security_event_id"] = df.loc[derecognition_mask, "source_row"].map(event_by_row["event_id"])
    df.loc[derecognition_mask, "tax_treatment_status"] = df.loc[derecognition_mask, "source_row"].map(event_by_row["tax_treatment_status"])
pre_diagnostics = build_pre_accounting_diagnostics(df)
transaction_support_matrix = build_transaction_support_matrix(
    df,
    supported_dividend_reinvestment_source_rows=matched_dividend_reinvestment_action_rows,
    supported_worthless_derecognition_source_rows=matched_worthless_derecognition_source_rows,
)
stage_end(f"{len(df):,} rows")

stage_start(2, 7, "Calculating stock/fund FIFO and transaction ledgers")
dividends = build_clean_dividend_ledger(
    df,
    dividend_reinvestment_events,
    dividend_reinvestment_candidate_cash_rows,
)
interest = build_interest_ledger(df)
trades, promo, allocated_ipo_source_rows = build_trade_ledger(df, dividend_reinvestment_events)
trades, corporate_action_audit, consumed_corporate_action_source_rows = normalize_corporate_actions(trades)
consumed_corporate_action_source_rows.update(matched_dividend_reinvestment_action_rows)
lineage_alias_map = (
    trades[["raw_isin", "current_isin"]]
    .dropna().drop_duplicates().set_index("raw_isin")["current_isin"].to_dict()
    if not trades.empty and {"raw_isin", "current_isin"}.issubset(trades.columns)
    else {}
)
if not dividends.empty and lineage_alias_map:
    dividends["raw_isin"] = dividends["isin"]
    dividends["isin"] = dividends["isin"].map(lineage_alias_map).fillna(dividends["isin"])
holdings, realized, open_lots, split_allocations, corporate_action_audit = fifo_realized_and_positions(
    trades, corporate_action_audit
)
if not worthless_derecognition_events.empty:
    derec_realized = realized[
        realized.get("close_type", pd.Series(dtype=str)).astype(str).eq(WORTHLESS_DERECOGNITION)
    ].copy() if not realized.empty else pd.DataFrame()
    if not derec_realized.empty:
        event_totals = derec_realized.groupby("known_security_event_id", dropna=False).agg(
            closed_acquisition_basis_eur=("allocated_acquisition_cost_basis_eur", "sum"),
            closed_user_funded_basis_eur=("allocated_user_funded_basis_eur", "sum"),
            realized_economic_pl_eur=("realized_pl_acquisition_basis_eur", "sum"),
            fifo_quantity_closed=("quantity_sold", "sum"),
        )
        worthless_derecognition_events = worthless_derecognition_events.merge(
            event_totals, left_on="event_id", right_index=True, how="left"
        )
trades = annotate_historical_scales(trades, corporate_action_audit)
stage_end(f"{len(holdings):,} stock/fund positions")

stage_start(3, 7, "Resolving stock/fund tickers, live prices and exposure metadata")
if ENABLE_STOCK_FUND_LIVE_PRICES:
    holdings = enrich_holdings_with_dynamic_live_prices(holdings)
else:
    for col in ["live_current_value_eur", "live_unrealized_pl_acquisition_basis_eur", "live_unrealized_pl_user_basis_eur", "live_simple_return_acquisition_basis_pct", "price_status"]:
        holdings[col] = np.nan
active_stock_positions = (
    holdings["position_status"].eq("ACTIVE")
    if "position_status" in holdings.columns
    else pd.Series(False, index=holdings.index)
)
resolved_stock_prices = (
    int((active_stock_positions & holdings["live_price_eur"].notna()).sum())
    if "live_price_eur" in holdings.columns
    else 0
)
security_sector_industry = build_security_sector_industry_metadata(
    holdings,
    metadata_fetcher=fetch_yahoo_security_metadata,
    last_known_good_cache=SECURITY_METADATA_LKG_CACHE,
)
stage_end(
    f"{resolved_stock_prices}/{int(active_stock_positions.sum())} active prices resolved; "
    f"{int(security_sector_industry['classification_status'].isin(['CLASSIFIED', 'PARTIALLY_CLASSIFIED']).sum()) if not security_sector_industry.empty else 0} direct equities classified"
)

stage_start(4, 7, "Building dividend, income and growth analytics")
combined, ttm_start, max_date = build_income_holdings_combined(holdings, dividends, df)

# Actual observed dividend yield on cost.
# This uses the shares in each dividend row, not today's later/current shares.
actual_observed_dividend_yoc = build_actual_observed_dividend_yoc_table(
    combined=combined,
    dividends=dividends,
    trades=trades,
    raw_df=df
)
if SHOW_DETAILED_PROGRESS:
    print(f"Actual observed dividend YoC rows: {len(actual_observed_dividend_yoc):,}")

# Active-only income projection base avoids projecting dividends from closed positions.
dividend_projection_by_holding, dividend_projection_by_month = build_stock_fund_dividend_projection(combined, dividends, max_date)

# Optional growth-adjusted layer. It never changes accounting; it only adds analytical projection columns.
dividend_analytics = enrich_dividend_projection_with_external_growth(
    dividend_projection_by_holding, combined=combined, trades=trades,
    dividends=dividends, actions=corporate_action_audit, asof=max_date,
)
dividends, dividend_reinvestment_events = reconcile_reinvestment_gross(
    dividends,
    dividend_reinvestment_events,
    dividend_analytics["upcoming"],
)
reconciled_reinvestments = (
    not dividend_reinvestment_events.empty
    and dividend_reinvestment_events["reconciliation_status"].eq("RECONCILED_DECLARED_DPS").any()
)
if reconciled_reinvestments:
    # The external layer supplies only high-confidence missing gross semantics.
    # Rebuild dependent income views, then rerun the cached event layer so the
    # receipt is matched as actual and excluded from future forecasts.
    combined, ttm_start, max_date = build_income_holdings_combined(holdings, dividends, df)
    actual_observed_dividend_yoc = build_actual_observed_dividend_yoc_table(
        combined=combined, dividends=dividends, trades=trades, raw_df=df
    )
    dividend_projection_by_holding, dividend_projection_by_month = build_stock_fund_dividend_projection(
        combined, dividends, max_date
    )
    dividend_analytics = enrich_dividend_projection_with_external_growth(
        dividend_projection_by_holding, combined=combined, trades=trades,
        dividends=dividends, actions=corporate_action_audit, asof=max_date,
    )
dividend_projection_by_holding = dividend_analytics["projection"]
dividend_projection_by_month = dividend_analytics["monthly"]
dividend_growth_history = dividend_analytics["history"]
upcoming_dividends = dividend_analytics["upcoming"]
dividend_forecast_events = dividend_analytics["forecast"]
dividend_external_audit = dividend_analytics["audit"]
dividend_event_metrics = dividend_analytics["metrics"]
dividend_calendar_events = build_unified_dividend_calendar_events(
    dividends=dividends,
    upcoming_dividends=upcoming_dividends,
    dividend_forecast_events=dividend_forecast_events,
    asof=max_date,
)

# Synchronize the Income tab's forward-looking columns with the
# growth-adjusted forecast used in the YoC / Future Dividends tab.
# All historical actual-observed columns remain unchanged.
if (
    actual_observed_dividend_yoc is not None
    and not actual_observed_dividend_yoc.empty
    and dividend_projection_by_holding is not None
    and not dividend_projection_by_holding.empty
):
    projection_sync = (
        dividend_projection_by_holding[
            [
                "isin",
                "growth_adjusted_forward_12m_net_dividend_eur",
                "growth_adjusted_net_yoc_acquisition_basis_pct",
            ]
        ]
        .drop_duplicates(subset=["isin"], keep="first")
        .copy()
    )

    projection_sync["isin"] = (
        projection_sync["isin"].astype(str).str.strip()
    )
    actual_observed_dividend_yoc["isin"] = (
        actual_observed_dividend_yoc["isin"].astype(str).str.strip()
    )

    growth_net_by_isin = projection_sync.set_index("isin")[
        "growth_adjusted_forward_12m_net_dividend_eur"
    ]
    growth_yoc_by_isin = projection_sync.set_index("isin")[
        "growth_adjusted_net_yoc_acquisition_basis_pct"
    ]

    mapped_growth_net = (
        actual_observed_dividend_yoc["isin"].map(growth_net_by_isin)
    )
    mapped_growth_yoc = (
        actual_observed_dividend_yoc["isin"].map(growth_yoc_by_isin)
    )

    actual_observed_dividend_yoc[
        "next_cycle_implied_net_dividend_on_current_shares_eur"
    ] = mapped_growth_net.where(
        actual_observed_dividend_yoc["isin"].isin(growth_net_by_isin.index),
        actual_observed_dividend_yoc["next_cycle_implied_net_dividend_on_current_shares_eur"],
    )

    actual_observed_dividend_yoc[
        "next_cycle_implied_net_yoc_on_current_basis_pct"
    ] = mapped_growth_yoc.where(
        actual_observed_dividend_yoc["isin"].isin(growth_yoc_by_isin.index),
        actual_observed_dividend_yoc["next_cycle_implied_net_yoc_on_current_basis_pct"],
    )

active_projection_net = safe_float(dividend_projection_by_holding["forward_12m_net_dividend_eur"].sum()) if not dividend_projection_by_holding.empty else 0
active_projection_gross = safe_float(dividend_projection_by_holding["forward_12m_gross_dividend_eur"].sum()) if not dividend_projection_by_holding.empty else 0
growth_adjusted_projection_net = safe_float(dividend_projection_by_holding["growth_adjusted_forward_12m_net_dividend_eur"].sum()) if not dividend_projection_by_holding.empty and "growth_adjusted_forward_12m_net_dividend_eur" in dividend_projection_by_holding.columns else active_projection_net
growth_adjusted_projection_gross = safe_float(dividend_projection_by_holding["growth_adjusted_forward_12m_gross_dividend_eur"].sum()) if not dividend_projection_by_holding.empty and "growth_adjusted_forward_12m_gross_dividend_eur" in dividend_projection_by_holding.columns else active_projection_gross
growth_adjusted_projection_net = dividend_event_metrics["forward_scheduled_net_estimate_eur"]
growth_adjusted_projection_net = np.nan if growth_adjusted_projection_net is None else growth_adjusted_projection_net
growth_adjusted_projection_gross = dividend_event_metrics["forward_scheduled_gross_eur"]
growth_adjusted_projection_gross = np.nan if growth_adjusted_projection_gross is None else growth_adjusted_projection_gross
forward_gross_forecast = dividend_event_metrics["forward_12m_gross_forecast_eur"]
forward_known_net_subtotal = dividend_event_metrics["forward_12m_estimated_net_known_subtotal_eur"]
forward_gross_awaiting_net = dividend_event_metrics["forward_12m_gross_awaiting_net_estimation_eur"]
active_projection_monthly_net = active_projection_net / 12.0 if active_projection_net else 0
growth_adjusted_projection_monthly_net = growth_adjusted_projection_net / 12.0 if growth_adjusted_projection_net else 0
active_seasonal_projection_net = growth_adjusted_projection_net
active_seasonal_projection_gross = growth_adjusted_projection_gross
growth_adjusted_seasonal_projection_net = safe_float(dividend_projection_by_month.get("growth_adjusted_projected_net_dividend_eur", pd.Series(dtype=float)).sum()) if not dividend_projection_by_month.empty else 0
growth_adjusted_seasonal_projection_gross = safe_float(dividend_projection_by_month.get("growth_adjusted_projected_gross_dividend_eur", pd.Series(dtype=float)).sum()) if not dividend_projection_by_month.empty else 0
external_growth_positions = int((dividend_projection_by_holding.get("external_dividend_history_years", pd.Series(dtype=float)).fillna(0) >= 2).sum()) if not dividend_projection_by_holding.empty else 0
growth_adjusted_seasonal_projection_net = growth_adjusted_projection_net
growth_adjusted_seasonal_projection_gross = growth_adjusted_projection_gross
active_income_projection = {
    "active_ttm_gross_dividends": round(active_projection_gross, 2),
    "active_ttm_net_dividends": round(active_projection_net, 2),
    "active_estimated_monthly_net_dividends": round(active_projection_monthly_net, 2),
    "active_seasonal_forward_12m_gross_dividends": round(active_seasonal_projection_gross, 2),
    "active_seasonal_forward_12m_net_dividends": round(active_seasonal_projection_net, 2),
    "growth_adjusted_forward_12m_gross_dividends": round(growth_adjusted_projection_gross, 2),
    "growth_adjusted_forward_12m_net_dividends": round(growth_adjusted_projection_net, 2),
    "growth_adjusted_estimated_monthly_net_dividends": round(growth_adjusted_projection_monthly_net, 2),
    "growth_adjusted_seasonal_forward_12m_gross_dividends": round(growth_adjusted_seasonal_projection_gross, 2),
    "growth_adjusted_seasonal_forward_12m_net_dividends": round(growth_adjusted_seasonal_projection_net, 2),
    "forward_12m_gross_forecast": round(forward_gross_forecast, 2),
    "forward_12m_estimated_net_known_subtotal": round(forward_known_net_subtotal, 2),
    "forward_12m_gross_awaiting_net_estimation": round(forward_gross_awaiting_net, 2),
    "forward_12m_net_estimable_event_count": dividend_event_metrics["forward_12m_net_estimable_event_count"],
    "forward_12m_relevant_gross_event_count": dividend_event_metrics["forward_12m_relevant_gross_event_count"],
    "forward_12m_net_unresolved_event_count": dividend_event_metrics["forward_12m_net_unresolved_event_count"],
    "forward_12m_net_coverage_event_pct": dividend_event_metrics["forward_12m_net_coverage_event_pct"],
    "forward_12m_net_coverage_gross_pct": dividend_event_metrics["forward_12m_net_coverage_gross_pct"],
    "forward_12m_net_forecast_completeness": dividend_event_metrics["forward_12m_net_forecast_completeness"],
    "forward_12m_net_unresolved_securities": "; ".join(dividend_event_metrics["forward_12m_net_unresolved_securities"]),
    "external_growth_positions": external_growth_positions,
    "closed_or_nontrade_ttm_net_dividends_excluded": round(safe_float(combined.loc[~combined["position_status"].eq("ACTIVE"), "ttm_net_dividends"].sum() if not combined.empty else 0), 2),
}
stage_end(f"{len(actual_observed_dividend_yoc):,} observed YoC rows")

stage_start(5, 7, "Resolving derivatives and building portfolio diagnostics")
derivative_ledger = build_derivative_ledger(df)
derivative_positions, derivative_realized, derivative_open_lots = fifo_derivative_positions(derivative_ledger)
derivative_positions, derivative_quote_attempts = enrich_derivatives_with_quote_attempts(derivative_positions)
active_derivatives = derivative_positions[derivative_positions["position_status"].eq("ACTIVE")].copy() if not derivative_positions.empty else pd.DataFrame()
closed_derivatives = derivative_positions[derivative_positions["position_status"].eq("CLOSED")].copy() if not derivative_positions.empty else pd.DataFrame()
valuation_diagnostics = build_valuation_diagnostics(holdings, active_derivatives)
valuation_blockers = (
    valuation_diagnostics[valuation_diagnostics["valuation_status"].eq("BLOCKING")].copy()
    if not valuation_diagnostics.empty else pd.DataFrame()
)

stockfund_concentration = build_stockfund_concentration_table(combined, active_derivatives)

manual_review_mask = df["manual_review_flag"].eq("REVIEW") | df["include_in_portfolio_model"].eq("REVIEW")
if allocated_ipo_source_rows:
    manual_review_mask &= ~df["source_row"].isin(allocated_ipo_source_rows)
manual_review = df[manual_review_mask].copy()
unknown_rows = df[df["normalized_category"].eq("Unknown / manual review")].copy()
unmatched_stock_sells = realized[realized["source_buy_trade_id"].astype(str).str.startswith("UNMATCHED_")].copy() if not realized.empty and "source_buy_trade_id" in realized.columns else pd.DataFrame()
unmatched_derivative_sells = derivative_realized[derivative_realized["source_buy_trade_id"].eq("UNMATCHED_SELL")].copy() if not derivative_realized.empty and "source_buy_trade_id" in derivative_realized.columns else pd.DataFrame()
div_recon_errors = dividends[dividends["reconciliation_error"].abs() > 1e-6].copy() if not dividends.empty else pd.DataFrame()
corporate_action_blockers = (
    corporate_action_audit[~corporate_action_audit["validation_status"].eq("PASS")].copy()
    if not corporate_action_audit.empty else pd.DataFrame()
)
support_blocking_rows = int(transaction_support_matrix.loc[transaction_support_matrix["blocking"].eq(True), "row_count"].sum()) if not transaction_support_matrix.empty else 0
diagnostic_additions = pd.DataFrame([
    {"check": "unknown_transaction_rows", "value": len(unknown_rows), "severity": "BLOCKING" if support_blocking_rows else ("WARNING" if len(unknown_rows) else "PASS")},
    {"check": "dividend_reinvestment_candidate_events", "value": len(dividend_reinvestment_diagnostics), "severity": "INFO"},
    {"check": "dividend_reinvestment_matched_events", "value": len(dividend_reinvestment_events), "severity": "PASS" if len(dividend_reinvestment_events) else "INFO"},
    {"check": "dividend_reinvestment_unresolved_events", "value": int((dividend_reinvestment_diagnostics["validation_status"] != "PASS").sum()) if not dividend_reinvestment_diagnostics.empty else 0, "severity": "BLOCKING" if (not dividend_reinvestment_diagnostics.empty and (dividend_reinvestment_diagnostics["validation_status"] != "PASS").any()) else "PASS"},
    {"check": "dividend_reinvestment_amount_eur", "value": round(safe_float(dividend_reinvestment_events.get("reinvested_amount_eur", pd.Series(dtype=float)).sum()), 8), "severity": "INFO"},
    {"check": "dividend_reinvestment_credited_quantity", "value": round(safe_float(dividend_reinvestment_events.get("reinvested_quantity", pd.Series(dtype=float)).sum()), 8), "severity": "INFO"},
    {"check": "dividend_reinvestment_gross_reconciled_events", "value": int(dividend_reinvestment_events.get("reconciliation_status", pd.Series(dtype=str)).eq("RECONCILED_DECLARED_DPS").sum()), "severity": "PASS" if len(dividend_reinvestment_events) and dividend_reinvestment_events.get("reconciliation_status", pd.Series(dtype=str)).eq("RECONCILED_DECLARED_DPS").all() else "INFO"},
    {"check": "worthless_derecognition_candidate_events", "value": len(worthless_derecognition_diagnostics), "severity": "INFO"},
    {"check": "worthless_derecognition_matched_events", "value": len(worthless_derecognition_events), "severity": "PASS" if len(worthless_derecognition_events) else "INFO"},
    {"check": "worthless_derecognition_unresolved_events", "value": len(unresolved_worthless_derecognition_source_rows), "severity": "BLOCKING" if unresolved_worthless_derecognition_source_rows else "PASS"},
    {"check": "worthless_derecognition_zero_cash_effect_eur", "value": round(safe_float(worthless_derecognition_events.get("cash_effect_eur", pd.Series(dtype=float)).sum()), 8), "severity": "PASS" if len(worthless_derecognition_events) else "INFO"},
    {"check": "supported_corporate_action_rows_consumed", "value": len(consumed_corporate_action_source_rows), "severity": "PASS" if len(consumed_corporate_action_source_rows) else "INFO"},
    {"check": "unsupported_or_failed_corporate_actions", "value": len(corporate_action_blockers), "severity": "BLOCKING" if len(corporate_action_blockers) else "PASS"},
    {"check": "unmatched_stock_sell_rows", "value": len(unmatched_stock_sells), "severity": "BLOCKING" if len(unmatched_stock_sells) else "PASS"},
    {"check": "unmatched_derivative_sell_rows", "value": len(unmatched_derivative_sells), "severity": "BLOCKING" if len(unmatched_derivative_sells) else "PASS"},
    {"check": "dividend_reconciliation_errors", "value": len(div_recon_errors), "severity": "BLOCKING" if len(div_recon_errors) else "PASS"},
    {"check": "current_valuation_blocking_positions", "value": len(valuation_blockers), "severity": "BLOCKING" if len(valuation_blockers) else "PASS"},
])
pre_diagnostics = pd.concat([pre_diagnostics, diagnostic_additions], ignore_index=True)
fee_rows = df[df["fee"].fillna(0).ne(0)].copy()
fee_rows["fee_paid_abs_eur"] = fee_rows["fee"].abs()
fee_recon = fee_rows.groupby(["asset_class_clean", "type_norm"], dropna=False).agg(rows=("source_row", "count"), fees_eur=("fee_paid_abs_eur", "sum")).reset_index().sort_values("fees_eur", ascending=False) if not fee_rows.empty else pd.DataFrame(columns=["asset_class_clean", "type_norm", "rows", "fees_eur"])
cash_ledger = build_cash_ledger(df)
expense_analytics = build_expense_analytics(df)
expense_metrics = expense_analytics["metrics"]
expense_ledger = expense_analytics["ledger"]
expense_monthly = expense_analytics["monthly"]
expense_categories = expense_analytics["categories"]
expense_merchants = expense_analytics["merchants"]
expense_weekdays = expense_analytics["weekdays"]
expense_daily = expense_analytics["daily"]
expense_recurring_candidates = expense_analytics["recurring_candidates"]
expense_narrative = expense_analytics["narrative"]
_automated_price_dates = pd.to_datetime(
    combined.get("live_price_date", pd.Series(dtype="object")), errors="coerce"
).dropna()
automated_valuation_date = max(
    [pd.Timestamp(max_date)] + ([pd.Timestamp(_automated_price_dates.max())] if not _automated_price_dates.empty else [])
)
saveback_analytics = build_saveback_analytics(
    transactions=df,
    promo=promo,
    trades=trades,
    open_lots=open_lots,
    realized=realized,
    combined=combined,
    expense_monthly=expense_monthly,
    as_of=automated_valuation_date,
)
saveback_events = saveback_analytics["saveback_events"]
saveback_summary = saveback_analytics["saveback_summary"]
saveback_monthly = saveback_analytics["monthly"]
saveback_security = saveback_analytics["security_summary"]
saveback_spending = saveback_analytics["spending_diagnostic"]
saveback_compounding = saveback_analytics["compounding_scenarios"]
saveback_diagnostics = saveback_analytics["diagnostics"]
unsupported_summary = build_unsupported_activity_summary(df[~df["source_row"].isin(allocated_ipo_source_rows)] if allocated_ipo_source_rows else df)

stock_active = combined[combined["position_status"].eq("ACTIVE")].copy() if not combined.empty else pd.DataFrame()
stock_live_series = pd.to_numeric(stock_active.get("live_current_value_eur"), errors="coerce") if not stock_active.empty else pd.Series(dtype=float)
stock_unrealized_series = pd.to_numeric(stock_active.get("live_unrealized_pl_acquisition_basis_eur"), errors="coerce") if not stock_active.empty else pd.Series(dtype=float)
stock_valuation_complete = bool(stock_active.empty or stock_live_series.notna().all())
stock_partial_live_value = safe_float(stock_live_series.sum())
stock_partial_unrealized = safe_float(stock_unrealized_series.sum())
stock_live_value = stock_partial_live_value if stock_valuation_complete else np.nan
stock_unrealized = stock_partial_unrealized if stock_valuation_complete else np.nan
deriv_active_quoted = active_derivatives[active_derivatives["live_price_eur"].notna()].copy() if not active_derivatives.empty and "live_price_eur" in active_derivatives.columns else pd.DataFrame()
derivative_valuation_complete = bool(active_derivatives.empty or ("estimated_live_value_eur" in active_derivatives.columns and active_derivatives["estimated_live_value_eur"].notna().all()))
deriv_partial_live_value = safe_float(deriv_active_quoted["estimated_live_value_eur"].sum()) if not deriv_active_quoted.empty else 0
deriv_partial_unrealized = safe_float(deriv_active_quoted["estimated_unrealized_pl_eur"].sum()) if not deriv_active_quoted.empty else 0
deriv_live_value = deriv_partial_live_value if derivative_valuation_complete else np.nan
deriv_unrealized = deriv_partial_unrealized if derivative_valuation_complete else np.nan

advanced_insights = build_advanced_portfolio_insights(
    df=df,
    trades=trades,
    dividends=dividends,
    interest=interest,
    combined=combined,
    open_lots=open_lots,
    realized=realized,
    derivative_ledger=derivative_ledger,
    derivative_realized=derivative_realized,
    derivative_open_lots=derivative_open_lots,
    active_derivatives=active_derivatives,
    dividend_projection_by_holding=dividend_projection_by_holding,
    dividend_projection_by_month=dividend_projection_by_month,
    max_date=max_date,
    ttm_start=ttm_start,
)
advanced_metrics = advanced_insights["metrics"]
mwr_cashflows = advanced_insights["mwr_cashflows"]
realized_behavior_events = advanced_insights["realized_events"]
insight_position_snapshot = advanced_insights["position_snapshot"]
income_resilience_by_company = advanced_insights["income_by_company"]
stress_test_scenarios = advanced_insights["stress_scenarios"]
what_stands_out = advanced_insights["insight_messages"]

lifetime_performance = build_lifetime_performance(
    df=df,
    trades=trades,
    dividends=dividends,
    interest=interest,
    combined=combined,
    derivative_ledger=derivative_ledger,
    derivative_positions=derivative_positions,
    active_derivatives=active_derivatives,
    advanced_metrics=advanced_metrics,
)
lifetime_metrics = lifetime_performance["metrics"]
lifetime_cashflow_ledger = lifetime_performance["cashflow_ledger"]
lifetime_profit_bridge = lifetime_performance["profit_bridge"]
lifetime_return_definitions = lifetime_performance["return_definitions"]
duplicate_id_blocking = int(pre_diagnostics.loc[pre_diagnostics["check"].eq("duplicate_transaction_id_rows"), "value"].sum()) > 0
accounting_blocking = bool(
    duplicate_id_blocking or support_blocking_rows or len(corporate_action_blockers)
    or len(unmatched_stock_sells) or len(unmatched_derivative_sells) or len(div_recon_errors)
    or unresolved_dividend_reinvestment_source_rows or unresolved_worthless_derecognition_source_rows
)
accounting_status = "BLOCKING" if accounting_blocking else "COMPLETE"
if accounting_blocking:
    for metric in [
        "lifetime_current_tracked_open_value_eur", "lifetime_stock_open_pl_eur",
        "lifetime_derivative_open_pl_eur", "lifetime_profit_ex_interest_eur",
        "lifetime_economic_profit_eur", "lifetime_personal_benefit_including_promos_eur",
        "lifetime_component_profit_eur", "lifetime_profit_reconciliation_difference_eur",
        "lifetime_simple_return_on_gross_outflows_pct",
        "lifetime_simple_return_on_user_funded_outflows_pct",
        "lifetime_return_on_net_committed_acquisition_pct",
        "lifetime_return_on_net_committed_user_pct",
        "lifetime_capital_efficiency_on_peak_user_commitment_pct",
        "lifetime_tracked_investments_mwr_pct",
    ]:
        lifetime_metrics[metric] = np.nan
    lifetime_metrics["lifetime_valuation_status"] = "INCOMPLETE_ACCOUNTING"
    lifetime_metrics["lifetime_profit_status"] = "INCOMPLETE_ACCOUNTING"
    lifetime_metrics["lifetime_tracked_investments_mwr_status"] = "INCOMPLETE_ACCOUNTING"
    advanced_metrics["tracked_investments_mwr_pct"] = np.nan
    advanced_metrics["tracked_investments_mwr_status"] = "INCOMPLETE_ACCOUNTING"
    if not lifetime_return_definitions.empty:
        lifetime_return_definitions["status"] = "INCOMPLETE_ACCOUNTING"

# Human-readable companion export. The complete audit ledger remains unchanged.
_lifetime_simple_columns = {
    "event_date": "date",
    "asset_scope": "scope",
    "cashflow_type": "transaction_type",
    "security_name": "investment",
    "isin": "isin",
    "acquisition_outflow_eur": "acquisition_outflow_eur",
    "user_funded_outflow_eur": "user_funded_outflow_eur",
    "core_recovery_eur": "cash_recovered_ex_interest_eur",
    "interest_recovery_eur": "interest_received_eur",
    "matched_promo_credit_eur": "promotional_funding_eur",
    "net_commitment_change_user_ecosystem_eur": "net_user_capital_change_eur",
    "cumulative_net_committed_user_ecosystem_eur": "cumulative_user_capital_committed_eur",
    "note": "explanation",
}
if lifetime_cashflow_ledger is None or lifetime_cashflow_ledger.empty:
    lifetime_cashflow_simplified = pd.DataFrame(columns=list(_lifetime_simple_columns.values()))
else:
    lifetime_cashflow_simplified = (
        lifetime_cashflow_ledger[[c for c in _lifetime_simple_columns if c in lifetime_cashflow_ledger.columns]]
        .rename(columns=_lifetime_simple_columns)
        .copy()
    )

historical_analytics = build_historical_analytics(
    df=df,
    trades=trades,
    dividends=dividends,
    interest=interest,
    holdings=holdings,
    combined=combined,
    derivative_positions=derivative_positions,
    realized_behavior_events=realized_behavior_events,
    advanced_metrics=advanced_metrics,
    max_date=max_date,
    benchmark_ticker=BENCHMARK_TICKER,
    benchmark_name=BENCHMARK_NAME,
    annual_risk_free_rate_pct=RISK_FREE_RATE_PCT,
)
historical_metrics = historical_analytics["metrics"]
historical_metrics.update({
    "annual_risk_free_rate_pct": RISK_FREE_RATE_PCT,
    "risk_free_rate_source": RISK_FREE_RATE_SOURCE,
    "risk_free_rate_reference_date": RISK_FREE_RATE_DATE,
})
daily_nav_history = historical_analytics["daily_nav"]
monthly_return_history = historical_analytics["monthly_returns"]
calendar_return_history = historical_analytics["calendar_returns"]
rolling_return_history = historical_analytics["rolling_returns"]
period_performance = historical_analytics["period_performance"]
period_performance_history = historical_analytics["period_performance_history"]
drawdown_episodes = historical_analytics["drawdown_episodes"]
benchmark_cashflow_audit = historical_analytics["benchmark_cashflows"]
historical_ticker_audit = historical_analytics["historical_ticker_audit"]
cash_history = historical_analytics["cash_history"]
cash_deployment_events = historical_analytics["cash_deployment_events"]
wealth_contribution = historical_analytics["wealth_contribution"]
allocation_breakdown = historical_analytics["allocation_breakdown"]
sold_hindsight = historical_analytics["sold_hindsight"]
portfolio_story_messages = historical_analytics["story_messages"]

exposure_analytics = build_sector_industry_analytics(
    combined=combined,
    wealth_contribution=wealth_contribution,
    metadata=security_sector_industry,
)
security_exposure_detail = exposure_analytics["security_detail"]
sector_exposure = exposure_analytics["sector_exposure"]
industry_exposure = exposure_analytics["industry_exposure"]
exposure_coverage = exposure_analytics["coverage"]

metadata_coverage_status = str(exposure_coverage.get("metadata_coverage_status", "OK"))
metadata_diagnostics = pd.DataFrame([
    {"check": "sector_metadata_current_classifications", "value": exposure_coverage.get("metadata_current_classifications", 0), "severity": "PASS" if exposure_coverage.get("metadata_current_classifications", 0) else "INFO"},
    {"check": "sector_metadata_cached_fallback_classifications", "value": exposure_coverage.get("metadata_cached_fallback_classifications", 0), "severity": "WARNING" if exposure_coverage.get("metadata_cached_fallback_classifications", 0) else "PASS"},
    {"check": "sector_metadata_genuinely_unclassified", "value": exposure_coverage.get("metadata_genuinely_unclassified", 0), "severity": "WARNING" if exposure_coverage.get("metadata_genuinely_unclassified", 0) else "PASS"},
    {"check": "sector_metadata_no_classification_current_responses", "value": exposure_coverage.get("metadata_no_classification_current_responses", 0), "severity": "WARNING" if exposure_coverage.get("metadata_no_classification_current_responses", 0) else "PASS"},
    {"check": "sector_metadata_current_lookup_failures", "value": exposure_coverage.get("metadata_lookup_failures", 0), "severity": "WARNING" if exposure_coverage.get("metadata_lookup_failures", 0) else "PASS"},
    {"check": "direct_equity_sector_value_coverage_pct", "value": exposure_coverage.get("sector_value_coverage_pct", np.nan), "severity": "INFO"},
    {"check": "direct_equity_industry_value_coverage_pct", "value": exposure_coverage.get("industry_value_coverage_pct", np.nan), "severity": "INFO"},
    {"check": "sector_metadata_coverage_status", "value": metadata_coverage_status, "severity": "WARNING" if metadata_coverage_status.startswith("WARNING") else "PASS"},
])
pre_diagnostics = pd.concat([pre_diagnostics, metadata_diagnostics], ignore_index=True)

if stockfund_concentration is not None and not stockfund_concentration.empty:
    top_concentration_row = stockfund_concentration.sort_values("value_weight_tracked_ex_cash_pct", ascending=False).iloc[0]
    top_cost_concentration_row = stockfund_concentration.sort_values("cost_weight_total_open_risk_pct", ascending=False).iloc[0]
    top_value_concentration_label = f"{top_concentration_row['security_name']} · {safe_float(top_concentration_row['value_weight_tracked_ex_cash_pct']):.2f}%"
    top_cost_concentration_label = f"{top_cost_concentration_row['security_name']} · {safe_float(top_cost_concentration_row['cost_weight_total_open_risk_pct']):.2f}%"
else:
    top_value_concentration_label = ""
    top_cost_concentration_label = ""


from worker_hooks import write_results
write_results(globals())
