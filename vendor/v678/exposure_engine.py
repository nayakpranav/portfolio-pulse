"""Additive sector/industry exposure analytics for the Trade Republic dashboard.

The module consumes already-resolved security identities and canonical accounting
outputs.  It never changes accounting values and deliberately avoids fund/ETF
look-through.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd


FUND_BUCKET = "Funds / ETFs"
FUND_INDUSTRY = "Fund / ETF — no look-through"
UNCLASSIFIED = "Unclassified"
METADATA_CACHE_SCHEMA_VERSION = "1.0.0"


def _clean_provider_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "null", "nan", "n/a", "unknown", "unclassified", "-", "--"}:
        return ""
    lowered = text.lower()
    if any(token in lowered for token in ("error", "failed", "unauthorized", "not found", "too many requests")):
        return ""
    return text


def _parsed_timestamp(value: Any) -> pd.Timestamp | None:
    stamp = pd.to_datetime(value, errors="coerce", utc=True)
    return None if pd.isna(stamp) else pd.Timestamp(stamp)


class LastKnownGoodMetadataCache:
    """Durable public security metadata keyed by ISIN with ticker fallback.

    A bundled seed survives deployments; an optional writable overlay retains
    later successful provider responses across normal local/Streamlit reruns.
    Failed or empty lookups never replace a valid record.
    """

    def __init__(self, seed_path: str | Path | None = None, cache_path: str | Path | None = None):
        self.seed_path = Path(seed_path) if seed_path else None
        self.cache_path = Path(cache_path) if cache_path else None
        self.records: dict[str, dict[str, Any]] = {}
        self.load_errors: list[str] = []
        self.write_error = ""
        self._load(self.seed_path)
        self._load(self.cache_path)

    @staticmethod
    def _keys(isin: Any, ticker: Any) -> list[str]:
        keys: list[str] = []
        isin_text = str(isin or "").strip().upper()
        ticker_text = str(ticker or "").strip().upper()
        if isin_text:
            keys.append(f"ISIN:{isin_text}")
        if ticker_text:
            keys.append(f"TICKER:{ticker_text}")
        return keys

    @staticmethod
    def _valid_record(record: dict[str, Any]) -> bool:
        return bool(
            _clean_provider_label(record.get("sector"))
            or _clean_provider_label(record.get("industry"))
        )

    def _load(self, path: Path | None) -> None:
        if path is None or not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for record in payload.get("records", []):
                if not isinstance(record, dict) or not self._valid_record(record):
                    continue
                self._merge_record(record, persist=False)
        except Exception as exc:
            self.load_errors.append(f"{path}: {type(exc).__name__}: {exc}")

    def _merge_record(self, incoming: dict[str, Any], *, persist: bool) -> bool:
        keys = self._keys(incoming.get("isin"), incoming.get("ticker"))
        if not keys or not self._valid_record(incoming):
            return False
        incoming_isin = str(incoming.get("isin") or "").strip().upper()
        existing = next(
            (
                self.records[key]
                for key in keys
                if key in self.records
                and (
                    not incoming_isin
                    or not str(self.records[key].get("isin") or "").strip()
                    or str(self.records[key].get("isin") or "").strip().upper() == incoming_isin
                )
            ),
            None,
        )
        incoming_stamp = _parsed_timestamp(incoming.get("successful_retrieved_at") or incoming.get("retrieved_at"))
        existing_stamp = _parsed_timestamp((existing or {}).get("successful_retrieved_at"))
        if existing is not None and incoming_stamp is None and existing_stamp is not None:
            return False
        if existing is not None and incoming_stamp is not None and existing_stamp is not None and incoming_stamp < existing_stamp:
            return False
        merged = dict(existing or {})
        for field in ("isin", "ticker", "sector", "industry", "quote_type", "source_provider"):
            value = _clean_provider_label(incoming.get(field))
            if value:
                merged[field] = value
        if incoming_stamp is not None:
            merged["successful_retrieved_at"] = incoming_stamp.isoformat()
        elif not merged.get("successful_retrieved_at"):
            merged["successful_retrieved_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        for key in self._keys(merged.get("isin"), merged.get("ticker")):
            self.records[key] = merged
        if persist:
            self._persist()
        return True

    def lookup(self, isin: Any, ticker: Any) -> dict[str, Any] | None:
        for key in self._keys(isin, ticker):
            record = self.records.get(key)
            if record is not None and self._valid_record(record):
                requested_isin = str(isin or "").strip().upper()
                record_isin = str(record.get("isin") or "").strip().upper()
                if requested_isin and record_isin and requested_isin != record_isin:
                    continue
                return deepcopy(record)
        return None

    def record_success(self, isin: Any, ticker: Any, result: dict[str, Any]) -> bool:
        if str(result.get("lookup_status", "")).upper() != "FOUND":
            return False
        record = {
            "isin": str(isin or "").strip(),
            "ticker": str(ticker or result.get("ticker") or "").strip(),
            "sector": result.get("sector", ""),
            "industry": result.get("industry", ""),
            "quote_type": result.get("quote_type", ""),
            "source_provider": result.get("source_provider", "Yahoo/yfinance"),
            "successful_retrieved_at": result.get("retrieved_at", ""),
        }
        return self._merge_record(record, persist=True)

    def _persist(self) -> None:
        if self.cache_path is None:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            unique: dict[str, dict[str, Any]] = {}
            for record in self.records.values():
                isin = str(record.get("isin", "")).strip().upper()
                ticker = str(record.get("ticker", "")).strip().upper()
                unique[isin or f"TICKER:{ticker}"] = record
            payload = {
                "schema_version": METADATA_CACHE_SCHEMA_VERSION,
                "records": sorted(unique.values(), key=lambda row: (str(row.get("isin", "")), str(row.get("ticker", "")))),
            }
            temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            temporary.replace(self.cache_path)
            self.write_error = ""
        except Exception as exc:
            self.write_error = f"{type(exc).__name__}: {exc}"


def fetch_yahoo_security_metadata(
    ticker: str,
    ticker_factory: Callable[[str], Any],
    cache: dict[str, dict[str, Any]] | None = None,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    """Fetch one ticker's descriptive metadata using yfinance's supported info API.

    The cache is caller-owned so Streamlit/run-level caching remains authoritative.
    Provider failures return an auditable result and never raise into accounting.
    """

    symbol = str(ticker or "").strip()
    if cache is not None and symbol in cache:
        cached = deepcopy(cache[symbol])
        cached["cache_status"] = "HIT"
        return cached

    stamp = retrieved_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    result = {
        "ticker": symbol,
        "sector": "",
        "industry": "",
        "quote_type": "",
        "sector_field": "",
        "industry_field": "",
        "source_provider": "Yahoo/yfinance",
        "lookup_status": "LOOKUP_FAILED" if symbol else "NO_RESOLVED_TICKER",
        "warning": "",
        "error": "",
        "retrieved_at": stamp,
        "cache_status": "MISS",
    }
    if not symbol:
        result["warning"] = "No resolved Yahoo ticker was available."
        return result

    try:
        instrument = ticker_factory(symbol)
        getter = getattr(instrument, "get_info", None)
        info = getter() if callable(getter) else getattr(instrument, "info", {})
        if not isinstance(info, dict):
            raise TypeError("Yahoo info response was not an object")
        for field in ("sector", "sectorDisp"):
            value = _clean_provider_label(info.get(field))
            if value:
                result["sector"] = value
                result["sector_field"] = field
                break
        for field in ("industry", "industryDisp"):
            value = _clean_provider_label(info.get(field))
            if value:
                result["industry"] = value
                result["industry_field"] = field
                break
        result["quote_type"] = _clean_provider_label(info.get("quoteType") or info.get("typeDisp"))
        result["lookup_status"] = "FOUND" if result["sector"] or result["industry"] else "NO_CLASSIFICATION"
        if result["lookup_status"] == "NO_CLASSIFICATION":
            result["warning"] = "Yahoo returned no defensible sector or industry classification."
    except Exception as exc:  # enrichment must never block portfolio accounting
        result["lookup_status"] = "LOOKUP_FAILED"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["warning"] = "External classification lookup failed; security remains unclassified."

    if cache is not None and result["lookup_status"] == "FOUND":
        cache[symbol] = deepcopy(result)
    return result


def build_security_sector_industry_metadata(
    holdings: pd.DataFrame,
    metadata_fetcher: Callable[[str], dict[str, Any]],
    last_known_good_cache: LastKnownGoodMetadataCache | None = None,
) -> pd.DataFrame:
    """Build the canonical metadata audit without mutating holdings/accounting."""

    columns = [
        "security_name", "isin", "asset_class", "position_status", "yahoo_ticker",
        "sector", "industry", "sector_source", "industry_source", "quote_type",
        "classification_status", "classification_warning", "metadata_lookup_status",
        "metadata_error", "metadata_retrieved_at", "metadata_cache_status",
        "metadata_current_lookup_status", "metadata_current_cache_status",
    ]
    if holdings is None or holdings.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, Any]] = []
    for _, holding in holdings.iterrows():
        asset_class = str(holding.get("asset_class", "") or "").strip().upper()
        status = str(holding.get("position_status", "") or "").strip().upper()
        base = {
            "security_name": holding.get("security_name", ""),
            "isin": holding.get("isin", ""),
            "asset_class": asset_class,
            "position_status": status,
            "yahoo_ticker": holding.get("yahoo_ticker", ""),
        }
        if status != "ACTIVE":
            rows.append({
                **base, "sector": UNCLASSIFIED, "industry": UNCLASSIFIED,
                "sector_source": "", "industry_source": "", "quote_type": "",
                "classification_status": "NOT_ACTIVE_NOT_QUERIED",
                "classification_warning": "Closed/non-active security was not queried for current exposure metadata.",
                "metadata_lookup_status": "NOT_QUERIED", "metadata_error": "",
                "metadata_retrieved_at": "", "metadata_cache_status": "",
            })
            continue
        if asset_class == "FUND":
            rows.append({
                **base, "sector": FUND_BUCKET, "industry": FUND_INDUSTRY,
                "sector_source": "instrument_asset_class", "industry_source": "instrument_asset_class",
                "quote_type": "FUND/ETF", "classification_status": "FUND_NO_LOOKTHROUGH",
                "classification_warning": "Fund/ETF constituent look-through is intentionally not performed.",
                "metadata_lookup_status": "NOT_REQUIRED", "metadata_error": "",
                "metadata_retrieved_at": "", "metadata_cache_status": "",
            })
            continue
        if asset_class != "STOCK":
            rows.append({
                **base, "sector": UNCLASSIFIED, "industry": UNCLASSIFIED,
                "sector_source": "", "industry_source": "", "quote_type": "",
                "classification_status": "UNSUPPORTED_ASSET_CLASS",
                "classification_warning": "Only direct equities are externally sector/industry classified.",
                "metadata_lookup_status": "NOT_QUERIED", "metadata_error": "",
                "metadata_retrieved_at": "", "metadata_cache_status": "",
            })
            continue

        ticker = str(base["yahoo_ticker"] or "")
        fetched = metadata_fetcher(ticker)
        current_lookup_status = str(fetched.get("lookup_status", "") or "")
        current_cache_status = str(fetched.get("cache_status", "") or "")
        if current_lookup_status == "FOUND" and last_known_good_cache is not None:
            cached = last_known_good_cache.lookup(base["isin"], ticker)
            last_known_good_cache.record_success(base["isin"], ticker, fetched)
            missing_sector = not _clean_provider_label(fetched.get("sector"))
            missing_industry = not _clean_provider_label(fetched.get("industry"))
            if cached and ((missing_sector and _clean_provider_label(cached.get("sector"))) or (missing_industry and _clean_provider_label(cached.get("industry")))):
                fetched = {
                    **fetched,
                    "sector": fetched.get("sector") or cached.get("sector", ""),
                    "industry": fetched.get("industry") or cached.get("industry", ""),
                    "lookup_status": "FOUND_WITH_CACHED_FIELDS",
                    "cache_status": "PARTIAL_FALLBACK",
                    "warning": "Current provider profile is partial; missing classification field restored from last-known-good metadata.",
                }
        elif current_lookup_status in {"NO_CLASSIFICATION", "LOOKUP_FAILED", "NO_RESOLVED_TICKER"} and last_known_good_cache is not None:
            cached = last_known_good_cache.lookup(base["isin"], ticker)
            if cached:
                current_warning = str(fetched.get("warning", "") or "").strip()
                fetched = {
                    **fetched,
                    "sector": cached.get("sector", ""),
                    "industry": cached.get("industry", ""),
                    "quote_type": cached.get("quote_type", fetched.get("quote_type", "")),
                    "source_provider": cached.get("source_provider", "Yahoo/yfinance"),
                    "lookup_status": "CACHED_LAST_KNOWN_GOOD",
                    "cache_status": "FALLBACK",
                    "retrieved_at": cached.get("successful_retrieved_at", ""),
                    "warning": (current_warning + " Using last-known-good public security metadata.").strip(),
                }
        sector = _clean_provider_label(fetched.get("sector")) or UNCLASSIFIED
        industry = _clean_provider_label(fetched.get("industry")) or UNCLASSIFIED
        found_sector = sector != UNCLASSIFIED
        found_industry = industry != UNCLASSIFIED
        if found_sector and found_industry:
            classification_status = "CLASSIFIED"
        elif found_sector or found_industry:
            classification_status = "PARTIALLY_CLASSIFIED"
        else:
            classification_status = "UNCLASSIFIED"
        rows.append({
            **base,
            "sector": sector,
            "industry": industry,
            "sector_source": fetched.get("source_provider", "") if found_sector else "",
            "industry_source": fetched.get("source_provider", "") if found_industry else "",
            "quote_type": fetched.get("quote_type", ""),
            "classification_status": classification_status,
            "classification_warning": fetched.get("warning", ""),
            "metadata_lookup_status": fetched.get("lookup_status", ""),
            "metadata_error": fetched.get("error", ""),
            "metadata_retrieved_at": fetched.get("retrieved_at", ""),
            "metadata_cache_status": fetched.get("cache_status", ""),
            "metadata_current_lookup_status": current_lookup_status,
            "metadata_current_cache_status": current_cache_status,
        })
    return pd.DataFrame(rows, columns=columns)


def _number_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _weighted_sum(series: pd.Series) -> float:
    numeric = pd.to_numeric(series, errors="coerce")
    return float(numeric.sum(min_count=1)) if numeric.notna().any() else np.nan


def build_sector_industry_analytics(
    combined: pd.DataFrame,
    wealth_contribution: pd.DataFrame,
    metadata: pd.DataFrame,
) -> dict[str, Any]:
    """Aggregate canonical instrument values by defensible classifications."""

    if combined is None or combined.empty:
        empty = pd.DataFrame()
        return {"security_detail": empty, "sector_exposure": empty, "industry_exposure": empty, "coverage": {}}

    active = combined[
        combined.get("position_status", pd.Series("", index=combined.index)).astype(str).eq("ACTIVE")
        & combined.get("asset_class", pd.Series("", index=combined.index)).astype(str).str.upper().isin({"STOCK", "FUND"})
    ].copy()
    meta_cols = [
        "isin", "sector", "industry", "sector_source", "industry_source", "quote_type",
        "classification_status", "classification_warning", "metadata_lookup_status",
        "metadata_error", "metadata_retrieved_at", "metadata_cache_status",
        "metadata_current_lookup_status", "metadata_current_cache_status",
    ]
    available_meta = metadata[[c for c in meta_cols if c in metadata.columns]].copy() if metadata is not None and not metadata.empty else pd.DataFrame(columns=meta_cols)
    detail = active.merge(available_meta, on="isin", how="left", suffixes=("", "_metadata"))

    contribution_cols = [
        "isin", "open_pl_eur", "realized_pl_eur", "net_income_eur",
        "economic_contribution_eur", "personal_benefit_contribution_eur",
    ]
    contributions = (
        wealth_contribution[
            wealth_contribution.get("instrument_type", pd.Series("", index=wealth_contribution.index)).astype(str).str.upper().isin({"STOCK", "FUND"})
        ][[c for c in contribution_cols if c in wealth_contribution.columns]].copy()
        if wealth_contribution is not None and not wealth_contribution.empty
        else pd.DataFrame(columns=contribution_cols)
    )
    detail = detail.merge(contributions, on="isin", how="left", suffixes=("", "_contribution"))
    detail["asset_class"] = detail["asset_class"].astype(str).str.upper()
    fund_mask = detail["asset_class"].eq("FUND")
    detail["sector"] = detail.get("sector", pd.Series(index=detail.index, dtype=object)).fillna(UNCLASSIFIED).replace("", UNCLASSIFIED)
    detail["industry"] = detail.get("industry", pd.Series(index=detail.index, dtype=object)).fillna(UNCLASSIFIED).replace("", UNCLASSIFIED)
    detail.loc[fund_mask, "sector"] = FUND_BUCKET
    detail.loc[fund_mask, "industry"] = FUND_INDUSTRY
    detail["classification_status"] = detail.get("classification_status", pd.Series(index=detail.index, dtype=object)).fillna("UNCLASSIFIED")
    detail.loc[fund_mask, "classification_status"] = "FUND_NO_LOOKTHROUGH"

    detail["current_value_eur"] = _number_series(detail, "live_current_value_eur")
    detail["unrealized_pl_eur"] = _number_series(detail, "live_unrealized_pl_acquisition_basis_eur")
    detail["unrealized_return_pct"] = _number_series(detail, "live_simple_return_acquisition_basis_pct")
    for column in ("realized_pl_eur", "net_income_eur", "economic_contribution_eur", "open_pl_eur"):
        detail[column] = _number_series(detail, column)

    valued = detail["current_value_eur"].notna()
    total_value = float(detail.loc[valued, "current_value_eur"].sum()) if valued.any() else np.nan
    stock_mask = detail["asset_class"].eq("STOCK")
    direct_value = float(detail.loc[stock_mask & valued, "current_value_eur"].sum()) if (stock_mask & valued).any() else np.nan
    detail["portfolio_weight_pct"] = np.where(
        valued & np.isfinite(total_value) & (abs(total_value) > 1e-12),
        detail["current_value_eur"] / total_value * 100.0,
        np.nan,
    )
    detail["direct_equity_weight_pct"] = np.where(
        stock_mask & valued & np.isfinite(direct_value) & (abs(direct_value) > 1e-12),
        detail["current_value_eur"] / direct_value * 100.0,
        np.nan,
    )

    def aggregate(column: str, include_direct_weight: bool) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for category, group in detail.groupby(column, dropna=False):
            current_value = _weighted_sum(group["current_value_eur"])
            direct_group_value = _weighted_sum(group.loc[group["asset_class"].eq("STOCK"), "current_value_eur"])
            rows.append({
                column: str(category or UNCLASSIFIED),
                "current_value_eur": current_value,
                "portfolio_weight_pct": current_value / total_value * 100.0 if np.isfinite(current_value) and np.isfinite(total_value) and abs(total_value) > 1e-12 else np.nan,
                "direct_equity_weight_pct": direct_group_value / direct_value * 100.0 if include_direct_weight and np.isfinite(direct_group_value) and np.isfinite(direct_value) and abs(direct_value) > 1e-12 else np.nan,
                "holding_count": int(len(group)),
                "unrealized_pl_eur": _weighted_sum(group["unrealized_pl_eur"]),
                "realized_pl_eur": _weighted_sum(group["realized_pl_eur"]),
                "net_income_eur": _weighted_sum(group["net_income_eur"]),
                "economic_contribution_eur": _weighted_sum(group["economic_contribution_eur"]),
            })
        return pd.DataFrame(rows).sort_values("current_value_eur", ascending=False, na_position="last").reset_index(drop=True) if rows else pd.DataFrame()

    sector = aggregate("sector", True)
    industry = aggregate("industry", True)
    direct = detail[stock_mask].copy()
    sector_classified = direct["sector"].ne(UNCLASSIFIED)
    industry_classified = direct["industry"].ne(UNCLASSIFIED)
    direct_valued = direct["current_value_eur"].notna()
    classified_value = float(direct.loc[sector_classified & direct_valued, "current_value_eur"].sum()) if (sector_classified & direct_valued).any() else 0.0
    unclassified_value = float(direct.loc[~sector_classified & direct_valued, "current_value_eur"].sum()) if ((~sector_classified) & direct_valued).any() else 0.0
    industry_value = float(direct.loc[industry_classified & direct_valued, "current_value_eur"].sum()) if (industry_classified & direct_valued).any() else 0.0
    treemap_mask = detail["current_value_eur"].notna() & detail["current_value_eur"].gt(0)
    represented_value = float(detail.loc[treemap_mask, "current_value_eur"].sum()) if treemap_mask.any() else 0.0
    current_status = direct.get("metadata_current_lookup_status", direct.get("metadata_lookup_status", pd.Series("", index=direct.index))).astype(str)
    effective_status = direct.get("metadata_lookup_status", pd.Series("", index=direct.index)).astype(str)
    current_successes = int(current_status.eq("FOUND").sum())
    cached_fallbacks = int(effective_status.isin(["CACHED_LAST_KNOWN_GOOD", "FOUND_WITH_CACHED_FIELDS"]).sum())
    genuinely_unclassified = int((~sector_classified).sum())
    no_classification_responses = int(current_status.eq("NO_CLASSIFICATION").sum())
    lookup_failures = int(current_status.eq("LOOKUP_FAILED").sum())
    degraded_responses = no_classification_responses + lookup_failures + int(current_status.eq("NO_RESOLVED_TICKER").sum())
    if len(direct) and current_successes == 0 and degraded_responses == len(direct):
        metadata_coverage_status = "WARNING_COMPLETE_CURRENT_LOOKUP_COLLAPSE_RECOVERED_FROM_CACHE" if cached_fallbacks else "WARNING_COMPLETE_METADATA_COLLAPSE_NO_CACHE"
    elif degraded_responses:
        metadata_coverage_status = "WARNING_PARTIAL_CURRENT_METADATA_DEGRADATION"
    else:
        metadata_coverage_status = "OK"
    coverage = {
        "active_direct_equities": int(len(direct)),
        "sector_classified_direct_equities": int(sector_classified.sum()),
        "industry_classified_direct_equities": int(industry_classified.sum()),
        "unclassified_direct_equities": int((~sector_classified).sum()),
        "classified_direct_equity_value_eur": classified_value,
        "industry_classified_direct_equity_value_eur": industry_value,
        "unclassified_direct_equity_value_eur": unclassified_value,
        "direct_equity_valued_eur": direct_value,
        "sector_value_coverage_pct": classified_value / direct_value * 100.0 if np.isfinite(direct_value) and abs(direct_value) > 1e-12 else np.nan,
        "industry_value_coverage_pct": industry_value / direct_value * 100.0 if np.isfinite(direct_value) and abs(direct_value) > 1e-12 else np.nan,
        "fund_etf_positions_no_lookthrough": int(fund_mask.sum()),
        "fund_etf_value_eur": float(detail.loc[fund_mask & valued, "current_value_eur"].sum()) if (fund_mask & valued).any() else 0.0,
        "metadata_current_classifications": current_successes,
        "metadata_cached_fallback_classifications": cached_fallbacks,
        "metadata_genuinely_unclassified": genuinely_unclassified,
        "metadata_no_classification_current_responses": no_classification_responses,
        "metadata_lookup_failures": lookup_failures,
        "metadata_degraded_current_responses": degraded_responses,
        "metadata_coverage_status": metadata_coverage_status,
        "sector_count": int(direct.loc[sector_classified, "sector"].nunique()),
        "industry_count": int(direct.loc[industry_classified, "industry"].nunique()),
        "valued_stock_fund_assets_eur": total_value,
        "treemap_represented_value_eur": represented_value,
        "treemap_value_coverage_pct": represented_value / total_value * 100.0 if np.isfinite(total_value) and abs(total_value) > 1e-12 else np.nan,
        "valuation_blocker_count": int((~valued).sum()),
    }
    detail_columns = [
        "sector", "industry", "security_name", "isin", "asset_class", "yahoo_ticker",
        "current_value_eur", "portfolio_weight_pct", "direct_equity_weight_pct",
        "unrealized_return_pct", "unrealized_pl_eur", "realized_pl_eur", "net_income_eur",
        "economic_contribution_eur", "sector_source", "industry_source", "quote_type",
        "classification_status", "classification_warning", "metadata_lookup_status",
        "metadata_error", "metadata_retrieved_at", "metadata_cache_status",
        "metadata_current_lookup_status", "metadata_current_cache_status",
    ]
    detail = detail[[c for c in detail_columns if c in detail.columns]].sort_values("current_value_eur", ascending=False, na_position="last").reset_index(drop=True)
    return {"security_detail": detail, "sector_exposure": sector, "industry_exposure": industry, "coverage": coverage}
