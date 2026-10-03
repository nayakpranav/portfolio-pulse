"""Pure, testable primitives shared by the Trade Republic engine and UI.

This module deliberately has no Streamlit, CLI, network, or file-system side effects.
It centralizes versioning, nullable presentation semantics, schema aliases,
transaction support classification, and corporate-action normalization.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Iterable

import numpy as np
import pandas as pd


APP_VERSION = "V6.7.8"
MANIFEST_SCHEMA_VERSION = "1.2.0"

STOCK_FUND_ASSET_CLASSES = {"STOCK", "FUND"}
CORPORATE_ACTION_TYPES = {"SPLIT", "REVERSE_SPLIT"}

CANONICAL_COLUMNS = {
    "date", "datetime", "type", "amount", "name", "symbol", "asset_class",
    "currency", "shares", "price", "fee", "tax", "transaction_id",
    "category", "description", "original_amount", "original_currency",
    "fx_rate", "counterparty_name", "counterparty_iban", "payment_reference",
    "mcc_code", "account_type",
}

COLUMN_ALIASES = {
    "timestamp": "datetime",
    "transaction_datetime": "datetime",
    "booking_datetime": "datetime",
    "booking_date": "date",
    "transaction_date": "date",
    "transaction_type": "type",
    "asset_type": "asset_class",
    "security_name": "name",
    "isin": "symbol",
    "quantity": "shares",
    "units": "shares",
    "fees": "fee",
    "taxes": "tax",
    "id": "transaction_id",
}


def normalized_column_name(value: Any) -> str:
    text = str(value or "").replace("\ufeff", "").strip().lower()
    text = "_".join(text.replace("-", " ").split())
    return COLUMN_ALIASES.get(text, text)


def canonical_column_mapping(columns: Iterable[Any]) -> dict[Any, str]:
    """Return a deterministic rename mapping for supported schema variations."""
    return {column: normalized_column_name(column) for column in columns}


@dataclass(frozen=True)
class SchemaValidation:
    valid: bool
    severity: str
    message: str
    canonical_columns: tuple[str, ...]
    missing_recommended: tuple[str, ...]


def validate_export_columns(columns: Iterable[Any]) -> SchemaValidation:
    canonical = tuple(canonical_column_mapping(columns).values())
    available = set(canonical)
    fatal: list[str] = []
    if "type" not in available:
        fatal.append("type")
    if not ({"date", "datetime"} & available):
        fatal.append("date or datetime")
    if fatal:
        return SchemaValidation(
            False,
            "BLOCKING",
            "Missing required Trade Republic accounting field(s): " + ", ".join(fatal),
            canonical,
            (),
        )
    recommended = tuple(sorted({"amount", "asset_class", "name", "symbol", "currency"} - available))
    if recommended:
        return SchemaValidation(
            True,
            "WARNING",
            "CSV is readable; optional/recommended fields are absent and affected rows may be blocked: "
            + ", ".join(recommended),
            canonical,
            recommended,
        )
    return SchemaValidation(True, "PASS", "CSV structure recognised.", canonical, ())


def optional_float(value: Any) -> float | None:
    """Return a finite float or None. Missing is never coerced to zero."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def format_money(value: Any, missing: str = "—") -> str:
    result = optional_float(value)
    return missing if result is None else f"€{result:,.2f}"


def format_pct(value: Any, missing: str = "—") -> str:
    result = optional_float(value)
    return missing if result is None else f"{result:.2f}%"


def optional_sum(*values: Any) -> float | None:
    parsed = [optional_float(value) for value in values]
    return None if any(value is None for value in parsed) else float(sum(parsed))


KNOWN_CASH_TYPES = {
    "CUSTOMER_INPAYMENT", "CUSTOMER_INBOUND", "TRANSFER_INBOUND",
    "TRANSFER_INSTANT_INBOUND", "TRANSFER_INSTANT_OUTBOUND",
    "MANUAL_CASH_TRANSFER", "GIFT", "REFERRAL", "TAX_OPTIMIZATION",
}
KNOWN_EXPENSE_TYPES = {
    "CARD_TRANSACTION", "CARD_TRANSACTION_INTERNATIONAL", "CARD_ORDERING_FEE",
}


def build_transaction_support_matrix(
    df: pd.DataFrame,
    supported_dividend_reinvestment_source_rows: set[int] | None = None,
    supported_worthless_derecognition_source_rows: set[int] | None = None,
) -> pd.DataFrame:
    """Describe every observed type/category/asset-class combination.

    Unknown security activity is blocking because it can change quantity, basis,
    cash flows, or returns. Unknown non-security cash activity is preserved as a
    warning until its economic meaning is known.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "type", "category", "asset_class", "row_count", "normalized_category",
            "investment_accounting_treatment", "cash_flow_treatment",
            "expense_treatment", "supported", "review_required", "blocking", "notes",
        ])

    work = df.copy()
    for column in ("type_norm", "category", "asset_class_clean"):
        if column not in work:
            work[column] = ""
    grouped = (
        work.groupby(["type_norm", "category", "asset_class_clean"], dropna=False)
        .size().reset_index(name="row_count")
    )
    supported_reinvestment_rows = supported_dividend_reinvestment_source_rows or set()
    supported_derecognition_rows = supported_worthless_derecognition_source_rows or set()
    rows: list[dict[str, Any]] = []
    for _, row in grouped.iterrows():
        typ = str(row.get("type_norm", "") or "").upper().strip()
        asset = str(row.get("asset_class_clean", "") or "").upper().strip()
        normalized = "Unknown / manual review"
        investment = "EXCLUDED"
        cash = "RETAINED_DIAGNOSTIC"
        expense = "EXCLUDED"
        supported = True
        review = False
        blocking = False
        notes = "Known non-investment activity; excluded from portfolio P/L."

        if typ in {"BUY", "SELL"} and asset in STOCK_FUND_ASSET_CLASSES:
            normalized = "Stock/fund trade"
            investment = "FIFO"
            cash = "INVESTMENT_OUTFLOW" if typ == "BUY" else "INVESTMENT_RECOVERY"
            notes = "Included in stock/fund FIFO and investment cash flows."
        elif typ == "DIVIDEND_REINVESTMENT" and asset in STOCK_FUND_ASSET_CLASSES:
            same_type = work[
                work["type_norm"].astype(str).str.upper().str.strip().eq(typ)
                & work["asset_class_clean"].astype(str).str.upper().str.strip().eq(asset)
                & work["category"].astype(str).eq(row.get("category", ""))
            ]
            source_rows = {
                int(value) for value in pd.to_numeric(
                    same_type.get("source_row", pd.Series(dtype=float)), errors="coerce"
                ).dropna()
            }
            fully_matched = bool(source_rows) and source_rows.issubset(supported_reinvestment_rows)
            normalized = "Dividend reinvestment / Wahldividende"
            investment = "LINKED_INCOME_TAX_AND_FIFO_ACQUISITION" if fully_matched else "BLOCKED_PENDING_CLASSIFICATION"
            cash = "INTERNAL_REINVESTMENT_WITH_EXPLICIT_SETTLEMENT_CASH" if fully_matched else "RETAINED_DIAGNOSTIC"
            supported = fully_matched
            review = not fully_matched
            blocking = not fully_matched
            notes = (
                "Matched corporate-action quantity, dividend base, tax and funding legs; creates one auditable FIFO lot."
                if fully_matched
                else "Dividend-reinvestment legs are incomplete or ambiguous; quantity and basis mutation rejected."
            )
        elif typ in CORPORATE_ACTION_TYPES and asset in STOCK_FUND_ASSET_CLASSES:
            normalized = "Corporate action"
            investment = "SECURITY_LINEAGE_AND_FIFO"
            cash = "ZERO_UNLESS_EXPLICIT_CASH_ROW"
            notes = "Must be consumed exactly once by corporate-action normalization."
        elif typ == "WORTHLESS_DERECOGNITION" and asset in STOCK_FUND_ASSET_CLASSES:
            same_type = work[
                work["type_norm"].astype(str).str.upper().str.strip().eq(typ)
                & work["asset_class_clean"].astype(str).str.upper().str.strip().eq(asset)
                & work["category"].astype(str).eq(row.get("category", ""))
            ]
            source_rows = {
                int(value) for value in pd.to_numeric(
                    same_type.get("source_row", pd.Series(dtype=float)), errors="coerce"
                ).dropna()
            }
            fully_matched = bool(source_rows) and source_rows.issubset(supported_derecognition_rows)
            normalized = "Worthless security derecognition"
            investment = "ZERO_PROCEEDS_FIFO_DERECOGNITION" if fully_matched else "BLOCKED_PENDING_CLASSIFICATION"
            cash = "ZERO_CASH_EFFECT" if fully_matched else "RETAINED_DIAGNOSTIC"
            supported = fully_matched
            review = not fully_matched
            blocking = not fully_matched
            notes = (
                "Explicit known event; closes the full FIFO position at zero proceeds without a cash flow."
                if fully_matched else
                "Derecognition did not satisfy the exact known-event and full-open-quantity controls."
            )
        elif typ in {"DIVIDEND", "DISTRIBUTION"}:
            normalized = "Investment income"
            investment = "INCOME_LEDGER"
            cash = "INVESTMENT_RECOVERY"
            notes = "Included as dividend/distribution income."
        elif typ in {"STOCKPERK", "BENEFITS_SAVEBACK"}:
            normalized = "Promotional funding"
            investment = "USER_BASIS_ADJUSTMENT"
            cash = "PROMOTIONAL_CREDIT"
            notes = "Kept separate from acquisition economics and matched audibly."
        elif typ == "INTEREST_PAYMENT":
            normalized = "Interest"
            investment = "SEPARATE_CASH_INCOME"
            cash = "INTEREST_RECOVERY"
            notes = "Included in lifetime economic benefit, excluded from tracked MWR."
        elif asset == "DERIVATIVE" and typ in {"BUY", "SELL", "TILG", "WARRANT_EXERCISE"}:
            normalized = "Derivative activity"
            investment = "DERIVATIVE_FIFO_COST_AT_RISK"
            cash = "DERIVATIVE_CASHFLOW"
            notes = "Included in derivative accounting; daily stock/fund TWR excludes derivatives."
        elif typ == "IPO_SUBSCRIPTION":
            normalized = "IPO subscription"
            investment = "ALLOCATE_TO_MATCHED_BUY_OR_REVIEW"
            cash = "INVESTMENT_OUTFLOW_OR_REFUND"
            review = True
            notes = "Supported only when cash rows reconcile to a same-ISIN IPO buy."
        elif typ in KNOWN_EXPENSE_TYPES:
            normalized = "Card / expense"
            expense = "PRIVATE_EXPENSE_LAYER"
            cash = "EXCLUDED_FROM_INVESTMENT_MODEL"
            notes = "Processed only in the isolated private expense layer."
        elif typ in KNOWN_CASH_TYPES:
            normalized = "Known cash activity"
        elif asset == "CRYPTO" and typ in {"BUY", "SELL"}:
            normalized = "Crypto intentionally excluded"
            investment = "OUTSIDE_MAIN_FIFO_SCOPE"
            cash = "RETAINED_DIAGNOSTIC"
            review = True
            notes = "Known unsupported scope; not silently discarded."
        else:
            supported = False
            review = True
            blocking = asset in STOCK_FUND_ASSET_CLASSES | {"DERIVATIVE"} or str(row.get("category", "")).upper() == "CORPORATE_ACTION"
            normalized = "Unknown security activity" if blocking else "Unknown non-investment activity"
            investment = "BLOCKED_PENDING_CLASSIFICATION" if blocking else "EXCLUDED_PENDING_REVIEW"
            notes = (
                "Unknown security/corporate activity can affect holdings or basis."
                if blocking else "Preserved for review; not assumed harmless or coerced into another type."
            )

        rows.append({
            "type": typ,
            "category": str(row.get("category", "") or ""),
            "asset_class": asset,
            "row_count": int(row["row_count"]),
            "normalized_category": normalized,
            "investment_accounting_treatment": investment,
            "cash_flow_treatment": cash,
            "expense_treatment": expense,
            "supported": supported,
            "review_required": review,
            "blocking": blocking,
            "notes": notes,
        })
    return pd.DataFrame(rows).sort_values(
        ["blocking", "supported", "row_count"], ascending=[False, True, False]
    ).reset_index(drop=True)


def _lineage_id(aliases: Iterable[str]) -> str:
    clean = sorted({str(alias).strip() for alias in aliases if str(alias).strip()})
    digest = sha256("|".join(clean).encode("utf-8")).hexdigest()[:16]
    return f"LINEAGE:{digest}"


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, value: str) -> str:
        self.parent.setdefault(value, value)
        if self.parent[value] != value:
            self.parent[value] = self.find(self.parent[value])
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[b] = a


CORPORATE_AUDIT_COLUMNS = [
    "action_id", "event_date", "source_row_ids", "corporate_action_type",
    "old_name", "new_name", "old_isin", "new_isin", "quantity_before",
    "source_quantity_change", "destination_quantity", "quantity_after",
    "derived_or_declared_ratio", "acquisition_basis_before_eur",
    "acquisition_basis_after_eur", "user_funded_basis_before_eur",
    "user_funded_basis_after_eur", "canonical_instrument_id", "current_isin",
    "historical_isin_aliases", "resolution_method", "validation_status",
    "realized_pl_caused_eur", "investment_cash_flow_caused_eur", "warning_error",
]


def normalize_corporate_actions(
    trades: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, set[int]]:
    """Normalize stock/fund splits into auditable economic-lineage events.

    Trade Republic represents an ISIN-changing reverse split as two rows: a
    full negative quantity on the obsolete ISIN and a positive destination
    quantity on the new ISIN. This function pairs such rows only when the same
    action timestamp has exactly one negative and one positive leg. Ambiguous
    rows remain explicit BLOCKING events.
    """
    if trades is None or trades.empty:
        return trades.copy() if trades is not None else pd.DataFrame(), pd.DataFrame(columns=CORPORATE_AUDIT_COLUMNS), set()

    out = trades.copy()
    out["raw_isin"] = out.get("isin", "").fillna("").astype(str).str.strip()
    out["raw_security_name"] = out.get("security_name", "").fillna("").astype(str)
    out["corporate_action_id"] = ""
    out["corporate_action_type"] = ""
    out["corporate_action_factor"] = np.nan
    out["old_isin"] = ""
    out["new_isin"] = ""
    out["source_row_ids"] = ""
    out["corporate_action_status"] = ""

    uf = _UnionFind()
    directed: dict[str, str] = {}
    audit_rows: list[dict[str, Any]] = []
    consumed: set[int] = set()
    drop_indices: set[Any] = set()

    action_rows = out[out["type_norm"].isin(CORPORATE_ACTION_TYPES)].copy()
    action_rows["_event_key"] = pd.Series(index=action_rows.index, dtype="string")
    if not action_rows.empty:
        action_rows["_event_key"] = action_rows.apply(
            lambda row: (
                str(pd.to_datetime(row.get("event_datetime"), errors="coerce"))
                if pd.notna(pd.to_datetime(row.get("event_datetime"), errors="coerce"))
                else str(pd.to_datetime(row.get("event_date"), errors="coerce"))
            ) + "|" + str(row.get("type_norm", "")),
            axis=1,
        )

    for event_key, group in action_rows.groupby("_event_key", sort=True):
        typ = str(group["type_norm"].iloc[0])
        negative = group[pd.to_numeric(group["signed_quantity"], errors="coerce") < -1e-12]
        positive = group[pd.to_numeric(group["signed_quantity"], errors="coerce") > 1e-12]

        if typ == "REVERSE_SPLIT" and len(negative) == 1 and len(positive) == 1 and len(group) == 2:
            old_index, old = next(iter(negative.iterrows()))
            new_index, new = next(iter(positive.iterrows()))
            old_qty = abs(float(old["signed_quantity"]))
            new_qty = float(new["signed_quantity"])
            factor = new_qty / old_qty if old_qty > 1e-12 else np.nan
            old_isin = str(old.get("raw_isin", ""))
            new_isin = str(new.get("raw_isin", ""))
            old_source = int(old.get("source_row"))
            new_source = int(new.get("source_row"))
            action_id = "CA:" + sha256(f"{event_key}|{old_source}|{new_source}".encode("utf-8")).hexdigest()[:16]
            valid = bool(old_isin and new_isin and np.isfinite(factor) and 0 < factor < 1)
            action_type = "REVERSE_SPLIT_ISIN_MIGRATION" if old_isin != new_isin else "REVERSE_SPLIT_SAME_ISIN"
            status = "PASS" if valid else "BLOCKING"
            warning = "" if valid else "Reverse-split pair has invalid identifiers or ratio."
            source_rows = f"{old_source};{new_source}"

            out.loc[old_index, "type_norm"] = "SPLIT"
            out.loc[old_index, "quantity"] = new_qty - old_qty
            out.loc[old_index, "signed_quantity"] = new_qty - old_qty
            out.loc[old_index, "security_name"] = str(new.get("security_name", "")) or str(old.get("security_name", ""))
            out.loc[old_index, "corporate_action_id"] = action_id
            out.loc[old_index, "corporate_action_type"] = action_type
            out.loc[old_index, "corporate_action_factor"] = factor
            out.loc[old_index, "old_isin"] = old_isin
            out.loc[old_index, "new_isin"] = new_isin
            out.loc[old_index, "source_row_ids"] = source_rows
            out.loc[old_index, "corporate_action_status"] = status
            drop_indices.add(new_index)
            consumed.update({old_source, new_source})
            if valid and old_isin != new_isin:
                uf.union(old_isin, new_isin)
                directed[old_isin] = new_isin

            audit_rows.append({
                "action_id": action_id,
                "event_date": old.get("event_date"),
                "source_row_ids": source_rows,
                "corporate_action_type": action_type,
                "old_name": old.get("security_name", ""),
                "new_name": new.get("security_name", ""),
                "old_isin": old_isin,
                "new_isin": new_isin,
                "quantity_before": old_qty,
                "source_quantity_change": -old_qty,
                "destination_quantity": new_qty,
                "quantity_after": new_qty,
                "derived_or_declared_ratio": factor,
                "acquisition_basis_before_eur": np.nan,
                "acquisition_basis_after_eur": np.nan,
                "user_funded_basis_before_eur": np.nan,
                "user_funded_basis_after_eur": np.nan,
                "canonical_instrument_id": "",
                "current_isin": new_isin,
                "historical_isin_aliases": "",
                "resolution_method": "PAIRED_SAME_TIMESTAMP_NEGATIVE_SOURCE_POSITIVE_DESTINATION",
                "validation_status": status,
                "realized_pl_caused_eur": 0.0,
                "investment_cash_flow_caused_eur": 0.0,
                "warning_error": warning,
            })
            continue

        if typ == "SPLIT" and len(group) == 1 and len(positive) == 1:
            index, row = next(iter(group.iterrows()))
            source = int(row.get("source_row"))
            isin = str(row.get("raw_isin", ""))
            action_id = "CA:" + sha256(f"{event_key}|{source}".encode("utf-8")).hexdigest()[:16]
            out.loc[index, "corporate_action_id"] = action_id
            out.loc[index, "corporate_action_type"] = "FORWARD_SPLIT_SAME_ISIN"
            out.loc[index, "old_isin"] = isin
            out.loc[index, "new_isin"] = isin
            out.loc[index, "source_row_ids"] = str(source)
            out.loc[index, "corporate_action_status"] = "PASS"
            consumed.add(source)
            audit_rows.append({
                "action_id": action_id,
                "event_date": row.get("event_date"),
                "source_row_ids": str(source),
                "corporate_action_type": "FORWARD_SPLIT_SAME_ISIN",
                "old_name": row.get("security_name", ""),
                "new_name": row.get("security_name", ""),
                "old_isin": isin,
                "new_isin": isin,
                "quantity_before": np.nan,
                "source_quantity_change": float(row.get("signed_quantity", 0.0)),
                "destination_quantity": np.nan,
                "quantity_after": np.nan,
                "derived_or_declared_ratio": np.nan,
                "acquisition_basis_before_eur": np.nan,
                "acquisition_basis_after_eur": np.nan,
                "user_funded_basis_before_eur": np.nan,
                "user_funded_basis_after_eur": np.nan,
                "canonical_instrument_id": "",
                "current_isin": isin,
                "historical_isin_aliases": isin,
                "resolution_method": "SAME_ISIN_POSITIVE_QUANTITY_DELTA",
                "validation_status": "PASS",
                "realized_pl_caused_eur": 0.0,
                "investment_cash_flow_caused_eur": 0.0,
                "warning_error": "",
            })
            continue

        # Ambiguous actions stay present as zero-quantity blocking events. They
        # cannot accidentally mutate FIFO, and affected analytics can be blocked.
        for index, row in group.iterrows():
            source = int(row.get("source_row"))
            action_id = "CA:" + sha256(f"{event_key}|{source}|BLOCK".encode("utf-8")).hexdigest()[:16]
            isin = str(row.get("raw_isin", ""))
            out.loc[index, "type_norm"] = "UNSUPPORTED_CORPORATE_ACTION"
            out.loc[index, "quantity"] = 0.0
            out.loc[index, "signed_quantity"] = 0.0
            out.loc[index, "corporate_action_id"] = action_id
            out.loc[index, "corporate_action_type"] = typ
            out.loc[index, "old_isin"] = isin
            out.loc[index, "new_isin"] = ""
            out.loc[index, "source_row_ids"] = str(source)
            out.loc[index, "corporate_action_status"] = "BLOCKING"
            audit_rows.append({
                "action_id": action_id, "event_date": row.get("event_date"),
                "source_row_ids": str(source), "corporate_action_type": typ,
                "old_name": row.get("security_name", ""), "new_name": "",
                "old_isin": isin, "new_isin": "", "quantity_before": np.nan,
                "source_quantity_change": row.get("signed_quantity", np.nan),
                "destination_quantity": np.nan, "quantity_after": np.nan,
                "derived_or_declared_ratio": np.nan,
                "acquisition_basis_before_eur": np.nan, "acquisition_basis_after_eur": np.nan,
                "user_funded_basis_before_eur": np.nan, "user_funded_basis_after_eur": np.nan,
                "canonical_instrument_id": "", "current_isin": isin,
                "historical_isin_aliases": isin, "resolution_method": "AMBIGUOUS_OR_UNPAIRED",
                "validation_status": "BLOCKING", "realized_pl_caused_eur": 0.0,
                "investment_cash_flow_caused_eur": 0.0,
                "warning_error": "Corporate-action rows could not be paired unambiguously; FIFO mutation rejected.",
            })

    if drop_indices:
        out = out.drop(index=list(drop_indices))

    all_isins = {str(value).strip() for value in out["raw_isin"] if str(value).strip()}
    all_isins.update(directed.keys())
    all_isins.update(directed.values())
    for isin in all_isins:
        uf.find(isin)
    components: dict[str, set[str]] = {}
    for isin in all_isins:
        components.setdefault(uf.find(isin), set()).add(isin)

    metadata: dict[str, tuple[str, str, str]] = {}
    for aliases in components.values():
        destinations = {directed.get(alias, "") for alias in aliases if directed.get(alias, "")}
        terminal = sorted(alias for alias in aliases if alias not in directed)
        current = terminal[-1] if len(terminal) == 1 else sorted(destinations or aliases)[-1]
        canonical = _lineage_id(aliases)
        alias_text = ";".join(sorted(aliases))
        for alias in aliases:
            metadata[alias] = (canonical, current, alias_text)

    out["canonical_instrument_id"] = out["raw_isin"].map(lambda value: metadata.get(str(value), (_lineage_id([str(value)]), str(value), str(value)))[0])
    out["current_isin"] = out["raw_isin"].map(lambda value: metadata.get(str(value), ("", str(value), str(value)))[1])
    out["historical_isin_aliases"] = out["raw_isin"].map(lambda value: metadata.get(str(value), ("", str(value), str(value)))[2])
    # Downstream quote, dividend, FIFO, and historical consumers operate on the
    # current identifier while raw_isin/source_row preserve audit traceability.
    out["isin"] = out["current_isin"]

    audit = pd.DataFrame(audit_rows, columns=CORPORATE_AUDIT_COLUMNS)
    if not audit.empty:
        for index, row in audit.iterrows():
            old_isin = str(row.get("old_isin", ""))
            canonical, current, aliases = metadata.get(old_isin, (_lineage_id([old_isin]), str(row.get("current_isin", "")), old_isin))
            audit.loc[index, "canonical_instrument_id"] = canonical
            audit.loc[index, "current_isin"] = current
            audit.loc[index, "historical_isin_aliases"] = aliases
    return out.sort_values(["event_datetime", "event_date", "source_row"]).reset_index(drop=True), audit, consumed


def apply_split_factor_to_lots(lots: list[dict[str, Any]], factor: float) -> None:
    """Apply a pure split factor in place while conserving both basis concepts."""
    if not np.isfinite(factor) or factor <= 0:
        raise ValueError("Corporate-action factor must be positive and finite.")
    for lot in lots:
        lot["quantity_remaining"] = float(lot["quantity_remaining"]) * factor
        lot["quantity_original"] = float(lot["quantity_original"]) * factor
        buy_price = optional_float(lot.get("buy_price"))
        if buy_price is not None:
            lot["buy_price"] = buy_price / factor


def annotate_historical_scales(
    trades: pd.DataFrame,
    corporate_action_audit: pd.DataFrame,
) -> pd.DataFrame:
    """Express historical quantities/prices in each lineage's current units.

    Corporate actions then produce no artificial quantity/value jump in daily
    TWR reconstruction. Cash flows remain their actual observed EUR amounts.
    """
    if trades is None or trades.empty:
        return trades.copy() if trades is not None else pd.DataFrame()
    out = trades.copy()
    out["historical_quantity_scale"] = 1.0
    out["historical_signed_quantity"] = pd.to_numeric(out.get("signed_quantity"), errors="coerce").fillna(0.0)
    out["historical_trade_price_eur"] = pd.to_numeric(out.get("trade_price_eur"), errors="coerce")
    if corporate_action_audit is None or corporate_action_audit.empty:
        return out

    passed = corporate_action_audit[
        corporate_action_audit["validation_status"].eq("PASS")
        & pd.to_numeric(corporate_action_audit["derived_or_declared_ratio"], errors="coerce").gt(0)
    ].copy()
    passed["event_date"] = pd.to_datetime(passed["event_date"], errors="coerce")
    for index, trade in out.iterrows():
        if str(trade.get("type_norm", "")) == "SPLIT":
            out.loc[index, "historical_signed_quantity"] = 0.0
            continue
        trade_date = pd.to_datetime(trade.get("event_date"), errors="coerce")
        lineage = str(trade.get("canonical_instrument_id", ""))
        future = passed[
            passed["canonical_instrument_id"].astype(str).eq(lineage)
            & passed["event_date"].ge(trade_date)
        ]
        factor = float(pd.to_numeric(future["derived_or_declared_ratio"], errors="coerce").prod()) if not future.empty else 1.0
        out.loc[index, "historical_quantity_scale"] = factor
        out.loc[index, "historical_signed_quantity"] = float(trade.get("signed_quantity", 0.0)) * factor
        price = optional_float(trade.get("trade_price_eur"))
        if price is not None and factor > 0:
            out.loc[index, "historical_trade_price_eur"] = price / factor
    return out


def transaction_price_fallback_with_quality(
    trades: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    price_column: str = "historical_trade_price_eur",
) -> tuple[pd.Series, pd.Series]:
    """Build an explicit lower-confidence fallback series and provenance labels."""
    series = pd.Series(index=calendar, dtype=float)
    quality = pd.Series("UNRESOLVED", index=calendar, dtype=object)
    if trades is not None and not trades.empty:
        points = trades.copy()
        points["event_date"] = pd.to_datetime(points.get("event_date"), errors="coerce").dt.normalize()
        selected = price_column if price_column in points.columns else "trade_price_eur"
        points["_price"] = pd.to_numeric(points.get(selected), errors="coerce")
        points = points.dropna(subset=["event_date", "_price"])
        points = points[points["_price"] > 0]
        if not points.empty:
            weights = pd.to_numeric(points.get("quantity", 1.0), errors="coerce")
            if not isinstance(weights, pd.Series):
                weights = pd.Series(float(weights), index=points.index)
            weights = weights.abs().fillna(1.0).replace(0, 1.0)
            points["_weighted"] = points["_price"] * weights
            points["_weight"] = weights
            grouped = points.groupby("event_date").agg(weighted=("_weighted", "sum"), weight=("_weight", "sum"))
            observed = grouped["weighted"] / grouped["weight"]
            for date, value in observed.items():
                if date in series.index:
                    series.loc[date] = float(value)
                    quality.loc[date] = "TRANSACTION_OBSERVED_PRICE"
    if not series.notna().any():
        return series, quality
    observed = series.copy()
    first, last = observed.first_valid_index(), observed.last_valid_index()
    interpolated = observed.interpolate(method="time", limit_area="inside")
    quality.loc[observed.isna() & interpolated.notna()] = "INTERPOLATED_PRICE"
    series = interpolated.ffill().bfill()
    quality.loc[quality.eq("UNRESOLVED") & (quality.index < first) & series.notna()] = "BACKWARD_FILLED_PRICE"
    quality.loc[quality.eq("UNRESOLVED") & (quality.index > last) & series.notna()] = "FORWARD_FILLED_PRICE"
    return series, quality
