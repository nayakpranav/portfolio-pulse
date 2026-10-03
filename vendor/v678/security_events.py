"""Auditable handling for narrowly approved non-trade security events.

The registry in this module is intentionally exact.  A negative broker
``FREE_RECEIPT`` is not generally a disposal: unmatched or partially matched
rows remain blocking until their custody meaning is known.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

import numpy as np
import pandas as pd


WORTHLESS_DERECOGNITION = "WORTHLESS_DERECOGNITION"


@dataclass(frozen=True)
class KnownWorthlessDerecognition:
    event_id: str
    transaction_id_sha256: str
    event_date: str
    isin: str
    security_name: str
    category: str
    asset_class: str
    broker_type: str
    quantity: float
    description: str


KNOWN_WORTHLESS_DERECOGNITIONS = ()  # No personal event registry.


EVENT_COLUMNS = [
    "event_id", "event_type", "event_date", "security_name", "isin",
    "broker_type", "category", "transaction_id", "source_row",
    "quantity_removed", "pre_event_open_quantity", "post_event_quantity",
    "proceeds_eur", "cash_effect_eur", "fee_eur", "tax_eur",
    "tax_treatment_status", "classification_status", "classification_basis",
]

DIAGNOSTIC_COLUMNS = [
    "source_row", "event_date", "security_name", "isin", "broker_type",
    "category", "quantity", "transaction_id", "validation_status",
    "diagnostic", "matched_event_id", "pre_event_open_quantity",
]


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _transaction_id_sha256(row: pd.Series) -> str:
    supplied = _text(row.get("transaction_id_sha256"))
    if supplied:
        return supplied.lower()
    transaction_id = _text(row.get("transaction_id"))
    return hashlib.sha256(transaction_id.encode("utf-8")).hexdigest() if transaction_id else ""


def _prior_open_quantity(frame: pd.DataFrame, candidate: pd.Series) -> tuple[float | None, str]:
    """Conservatively reconstruct pre-event quantity for the exact ISIN.

    This is a classification guard, not a parallel accounting system.  The
    canonical FIFO engine subsequently validates and consumes the actual lots.
    If prior activity cannot be represented safely here, classification is
    refused and the row remains blocking.
    """
    before = frame[
        frame["isin"].astype(str).eq(_text(candidate.get("isin")))
        & (
            (pd.to_datetime(frame["event_datetime"], errors="coerce") < pd.to_datetime(candidate.get("event_datetime"), errors="coerce"))
            | (
                pd.to_datetime(frame["event_datetime"], errors="coerce").eq(pd.to_datetime(candidate.get("event_datetime"), errors="coerce"))
                & (pd.to_numeric(frame["source_row"], errors="coerce") < float(candidate.get("source_row")))
            )
        )
    ].sort_values(["event_datetime", "source_row"])
    if before.empty:
        return 0.0, "NO_PRIOR_POSITION"

    supported = {"BUY", "SELL", "DIVIDEND_REINVESTMENT", "SPLIT", "REVERSE_SPLIT"}
    security_activity = before[
        before["asset_class_clean"].astype(str).isin({"STOCK", "FUND"})
        & pd.to_numeric(before["shares"], errors="coerce").notna()
    ]
    unsupported = security_activity[~security_activity["type_norm"].astype(str).isin(supported)]
    if not unsupported.empty:
        return None, "UNSUPPORTED_PRIOR_SECURITY_ACTIVITY"

    quantity = 0.0
    for _, row in security_activity.iterrows():
        typ = _text(row.get("type_norm")).upper()
        shares = _finite(row.get("shares"))
        if shares is None:
            return None, "MISSING_PRIOR_QUANTITY"
        if typ in {"BUY", "DIVIDEND_REINVESTMENT"}:
            quantity += abs(shares)
        elif typ == "SELL":
            quantity -= abs(shares)
        else:
            # Trade Republic split rows encode the signed quantity change.
            quantity += shares
    return quantity, "OK"


def match_known_worthless_derecognitions(df: pd.DataFrame) -> dict[str, Any]:
    """Match only registered, zero-consideration, full-position removals."""
    empty = {
        "events": pd.DataFrame(columns=EVENT_COLUMNS),
        "diagnostics": pd.DataFrame(columns=DIAGNOSTIC_COLUMNS),
        "matched_source_rows": set(),
        "unresolved_source_rows": set(),
    }
    if df is None or df.empty:
        return empty

    candidates = df[
        df.get("type_norm", pd.Series("", index=df.index)).astype(str).str.upper().eq("FREE_RECEIPT")
        & df.get("asset_class_clean", pd.Series("", index=df.index)).astype(str).str.upper().isin({"STOCK", "FUND"})
    ].copy()
    if candidates.empty:
        return empty

    events: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    matched: set[int] = set()
    unresolved: set[int] = set()
    for _, row in candidates.iterrows():
        source_row = int(row.get("source_row"))
        reasons: list[str] = []
        matched_spec: KnownWorthlessDerecognition | None = None
        for spec in KNOWN_WORTHLESS_DERECOGNITIONS:
            event_date = pd.to_datetime(row.get("event_date"), errors="coerce")
            quantity = _finite(row.get("shares"))
            exact = (
                _transaction_id_sha256(row) == spec.transaction_id_sha256
                and pd.notna(event_date) and str(event_date.date()) == spec.event_date
                and _text(row.get("isin")) == spec.isin
                and _text(row.get("security_name")) == spec.security_name
                and _text(row.get("category")).upper() == spec.category
                and _text(row.get("asset_class_clean")).upper() == spec.asset_class
                and _text(row.get("type_norm")).upper() == spec.broker_type
                and quantity is not None and abs(quantity - spec.quantity) <= 1e-9
                and _text(row.get("description_clean")) == spec.description
            )
            if exact:
                matched_spec = spec
                break
        if matched_spec is None:
            reasons.append("NOT_IN_EXPLICIT_KNOWN_EVENT_REGISTRY")

        for field in ("amount", "price", "fee", "tax"):
            value = _finite(row.get(field))
            if value is not None and abs(value) > 1e-12:
                reasons.append(f"NONZERO_{field.upper()}")

        pre_quantity, quantity_status = _prior_open_quantity(df, row)
        removal = abs(_finite(row.get("shares")) or 0.0)
        if pre_quantity is None:
            reasons.append(quantity_status)
        elif pre_quantity <= 1e-10:
            reasons.append("NO_OPEN_POSITION")
        elif abs(pre_quantity - removal) > 1e-8:
            reasons.append("NOT_EXACT_FULL_OPEN_QUANTITY")

        if matched_spec is not None and not reasons:
            matched.add(source_row)
            events.append({
                "event_id": matched_spec.event_id,
                "event_type": WORTHLESS_DERECOGNITION,
                "event_date": pd.Timestamp(row.get("event_date")).normalize(),
                "security_name": matched_spec.security_name,
                "isin": matched_spec.isin,
                "broker_type": matched_spec.broker_type,
                "category": matched_spec.category,
                "transaction_id": "[REDACTED_ID]",
                "source_row": source_row,
                "quantity_removed": removal,
                "pre_event_open_quantity": pre_quantity,
                "post_event_quantity": pre_quantity - removal,
                "proceeds_eur": 0.0,
                "cash_effect_eur": 0.0,
                "fee_eur": 0.0,
                "tax_eur": 0.0,
                "tax_treatment_status": "NOT_ASSERTED_BROKER_EXPORT_HAS_NO_TAX_TREATMENT",
                "classification_status": "SUPPORTED_KNOWN_EVENT",
                "classification_basis": "EXACT_REGISTRY_AND_ZERO_CONSIDERATION_AND_FULL_OPEN_QUANTITY",
            })
            status = "PASS"
            diagnostic = "SUPPORTED_KNOWN_ZERO_VALUE_DERECOGNITION"
            event_id = matched_spec.event_id
        else:
            unresolved.add(source_row)
            status = "BLOCKING"
            diagnostic = ";".join(dict.fromkeys(reasons))
            event_id = ""
        diagnostics.append({
            "source_row": source_row,
            "event_date": row.get("event_date"),
            "security_name": row.get("security_name", ""),
            "isin": row.get("isin", ""),
            "broker_type": row.get("type_norm", ""),
            "category": row.get("category", ""),
            "quantity": row.get("shares"),
            "transaction_id": "[REDACTED_ID]" if _text(row.get("transaction_id")) else "",
            "validation_status": status,
            "diagnostic": diagnostic,
            "matched_event_id": event_id,
            "pre_event_open_quantity": pre_quantity,
        })

    return {
        "events": pd.DataFrame(events, columns=EVENT_COLUMNS),
        "diagnostics": pd.DataFrame(diagnostics, columns=DIAGNOSTIC_COLUMNS),
        "matched_source_rows": matched,
        "unresolved_source_rows": unresolved,
    }
