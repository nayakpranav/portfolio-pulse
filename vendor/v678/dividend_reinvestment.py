"""Deterministic Trade Republic dividend-reinvestment reconciliation.

The normal application consumes only the transaction CSV.  A reinvestment is
accepted only when one credited-quantity corporate action can be linked to one
positive dividend base and one equal-and-opposite funding row for the same
instrument.  Original issuer gross and foreign withholding remain nullable
until the existing external dividend-event layer supplies a unique declared-DPS
match.
"""

from __future__ import annotations

from hashlib import sha256
import math
import re
from typing import Any

import numpy as np
import pandas as pd


REINVESTMENT_EVENT_COLUMNS = [
    "event_id", "validation_status", "diagnostic", "action_date", "payment_date",
    "security_name", "isin", "entitlement_quantity", "reinvested_quantity",
    "broker_reinvestment_base_eur", "gross_dividend_eur",
    "foreign_withholding_tax_eur", "domestic_tax_and_solidarity_surcharge_eur",
    "domestic_tax_refund_eur", "total_dividend_tax_eur",
    "net_dividend_income_eur", "reinvested_amount_eur",
    "reinvested_acquisition_price_eur", "net_settlement_cash_effect_eur",
    "gross_dividend_per_share_eur", "distribution_mode", "gross_source",
    "foreign_withholding_source", "reconciliation_status", "action_source_row",
    "positive_dividend_source_row", "funding_source_row", "source_row_ids",
]

REINVESTMENT_DIAGNOSTIC_COLUMNS = [
    "action_source_row", "action_date", "security_name", "isin",
    "candidate_positive_rows", "candidate_funding_rows", "candidate_pairs",
    "validation_status", "diagnostic",
]


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return re.sub(r"\s+", " ", str(value).strip())


def _date(value: Any) -> pd.Timestamp | None:
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else pd.Timestamp(parsed).normalize()


def _same_security(left: Any, right: Any) -> bool:
    a, b = _text(left).casefold(), _text(right).casefold()
    return bool(a and b and a == b)


def _source(value: Any) -> int | None:
    parsed = _number(value)
    return int(parsed) if parsed is not None else None


def _empty_result() -> dict[str, Any]:
    return {
        "events": pd.DataFrame(columns=REINVESTMENT_EVENT_COLUMNS),
        "diagnostics": pd.DataFrame(columns=REINVESTMENT_DIAGNOSTIC_COLUMNS),
        "matched_source_rows": set(),
        "matched_action_source_rows": set(),
        "unresolved_action_source_rows": set(),
        "candidate_cash_source_rows": set(),
        "unresolved_source_rows": set(),
    }


def match_dividend_reinvestments(
    transactions: pd.DataFrame,
    day_window: int = 3,
    amount_tolerance_eur: float = 0.005,
) -> dict[str, Any]:
    """Match corporate-action, income, and funding legs without guessing.

    The credited share quantity is deliberately *not* compared with the cash
    rows' share field: the former is the newly acquired fractional quantity,
    while the latter is the dividend entitlement quantity.  The two cash rows
    must agree with each other on entitlement quantity.
    """
    if transactions is None or transactions.empty:
        return _empty_result()

    work = transactions.copy()
    for column, default in {
        "type_norm": "", "asset_class_clean": "", "security_name": "",
        "isin": "", "description_clean": "", "event_date": pd.NaT,
        "event_datetime": pd.NaT, "shares": np.nan, "amount": np.nan,
        "fee": np.nan, "tax": np.nan, "source_row": np.nan,
    }.items():
        if column not in work:
            work[column] = default

    actions = work[
        work["type_norm"].astype(str).str.upper().eq("DIVIDEND_REINVESTMENT")
        & work["asset_class_clean"].astype(str).str.upper().isin({"STOCK", "FUND"})
    ].copy()
    dividend_rows = work[
        work["type_norm"].astype(str).str.upper().isin({"DIVIDEND", "DISTRIBUTION"})
        & work["description_clean"].astype(str).str.contains(
            r"\bdividend\s+reinvestment\b", case=False, regex=True, na=False
        )
    ].copy()

    # Cash legs are themselves auditable evidence.  Keep them blocking when
    # the credited-share action is absent instead of silently returning an
    # empty result.
    if actions.empty and dividend_rows.empty:
        return _empty_result()

    candidates_by_action: dict[int, list[tuple[int, int]]] = {}
    counts_by_action: dict[int, tuple[int, int]] = {}
    reasons: dict[int, str] = {}
    action_rows: dict[int, pd.Series] = {}

    for action_index, action in actions.sort_values(
        ["event_date", "event_datetime", "source_row"], kind="stable"
    ).iterrows():
        action_rows[action_index] = action
        action_source = _source(action.get("source_row"))
        action_date = _date(action.get("event_date"))
        isin = _text(action.get("isin"))
        credited_qty = _number(action.get("shares"))
        base_reason = ""
        if action_source is None or action_date is None or not isin or credited_qty is None or credited_qty <= 0:
            base_reason = "INVALID_ACTION_IDENTITY_DATE_OR_QUANTITY"

        nearby = dividend_rows.iloc[0:0].copy()
        if not base_reason:
            dates = pd.to_datetime(dividend_rows["event_date"], errors="coerce").dt.normalize()
            nearby = dividend_rows[
                dividend_rows["isin"].astype(str).str.strip().eq(isin)
                & dates.notna()
                & dates.ge(action_date)
                & dates.le(action_date + pd.Timedelta(days=day_window))
            ].copy()
            security_mask = nearby["security_name"].map(
                lambda value: _same_security(value, action.get("security_name"))
            ).astype(bool)
            nearby = nearby.loc[security_mask].copy()

        positive = nearby[pd.to_numeric(nearby["amount"], errors="coerce").gt(0)].copy()
        negative = nearby[pd.to_numeric(nearby["amount"], errors="coerce").lt(0)].copy()
        counts_by_action[action_index] = (len(positive), len(negative))
        pairs: list[tuple[int, int]] = []
        for pidx, positive_row in positive.iterrows():
            pamount = _number(positive_row.get("amount"))
            pentitlement = _number(positive_row.get("shares"))
            if pamount is None or pentitlement is None or pentitlement <= 0:
                continue
            for nidx, negative_row in negative.iterrows():
                namount = _number(negative_row.get("amount"))
                nentitlement = _number(negative_row.get("shares"))
                if namount is None or nentitlement is None or nentitlement <= 0:
                    continue
                tolerance = max(amount_tolerance_eur, abs(pamount) * 1e-6)
                qty_tolerance = max(1e-6, abs(pentitlement) * 1e-7)
                if abs(pamount + namount) <= tolerance and abs(pentitlement - nentitlement) <= qty_tolerance:
                    pairs.append((pidx, nidx))
        candidates_by_action[action_index] = pairs
        if base_reason:
            reasons[action_index] = base_reason
        elif positive.empty:
            reasons[action_index] = "POSITIVE_DIVIDEND_BASE_MISSING"
        elif negative.empty:
            reasons[action_index] = "REINVESTMENT_FUNDING_ROW_MISSING"
        elif not pairs:
            reasons[action_index] = "CASH_LEGS_AMOUNT_OR_ENTITLEMENT_MISMATCH"

    pair_owners: dict[tuple[int, int], list[int]] = {}
    for action_index, pairs in candidates_by_action.items():
        for pair in pairs:
            pair_owners.setdefault(pair, []).append(action_index)

    events: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    matched_sources: set[int] = set()
    matched_actions: set[int] = set()
    unresolved_actions: set[int] = set()
    candidate_cash_sources = {
        source for source in (_source(value) for value in dividend_rows["source_row"]) if source is not None
    }

    for action_index, action in action_rows.items():
        action_source = _source(action.get("source_row"))
        action_date = _date(action.get("event_date"))
        positive_count, negative_count = counts_by_action.get(action_index, (0, 0))
        pairs = candidates_by_action.get(action_index, [])
        unique_pairs = [pair for pair in pairs if len(pair_owners.get(pair, [])) == 1]
        matched = len(pairs) == 1 and len(unique_pairs) == 1
        if matched:
            pidx, nidx = unique_pairs[0]
            positive_row, funding_row = work.loc[pidx], work.loc[nidx]
            positive_source = _source(positive_row.get("source_row"))
            funding_source = _source(funding_row.get("source_row"))
            base = float(positive_row["amount"])
            credited_qty = float(action["shares"])
            entitlement = abs(float(positive_row["shares"]))
            tax_cash_effect = sum(
                _number(row.get("tax")) or 0.0 for row in (positive_row, funding_row)
            )
            domestic_tax = max(0.0, -tax_cash_effect)
            domestic_refund = max(0.0, tax_cash_effect)
            fee_cash_effect = sum(
                _number(row.get("fee")) or 0.0 for row in (positive_row, funding_row)
            )
            settlement_cash = base + float(funding_row["amount"]) + tax_cash_effect + fee_cash_effect
            net_income = base - domestic_tax + domestic_refund
            source_rows = [value for value in (action_source, positive_source, funding_source) if value is not None]
            event_id = "DR:" + sha256(
                ("|".join(map(str, source_rows)) + "|" + _text(action.get("isin"))).encode("utf-8")
            ).hexdigest()[:16]
            events.append({
                "event_id": event_id,
                "validation_status": "PASS",
                "diagnostic": "CSV_ACTION_AND_EQUAL_OPPOSITE_DIVIDEND_CASH_LEGS_MATCHED",
                "action_date": action_date,
                "payment_date": _date(positive_row.get("event_date")),
                "security_name": _text(action.get("security_name")),
                "isin": _text(action.get("isin")),
                "entitlement_quantity": entitlement,
                "reinvested_quantity": credited_qty,
                "broker_reinvestment_base_eur": base,
                "gross_dividend_eur": np.nan,
                "foreign_withholding_tax_eur": np.nan,
                "domestic_tax_and_solidarity_surcharge_eur": domestic_tax,
                "domestic_tax_refund_eur": domestic_refund,
                "total_dividend_tax_eur": np.nan,
                "net_dividend_income_eur": net_income,
                "reinvested_amount_eur": base,
                "reinvested_acquisition_price_eur": base / credited_qty,
                "net_settlement_cash_effect_eur": settlement_cash,
                "gross_dividend_per_share_eur": np.nan,
                "distribution_mode": "REINVESTED",
                "gross_source": "UNAVAILABLE_CSV_ONLY",
                "foreign_withholding_source": "UNAVAILABLE_CSV_ONLY",
                "reconciliation_status": "MATCHED_CSV_LEGS_GROSS_PENDING",
                "action_source_row": action_source,
                "positive_dividend_source_row": positive_source,
                "funding_source_row": funding_source,
                "source_row_ids": ";".join(map(str, source_rows)),
            })
            matched_sources.update(source_rows)
            if action_source is not None:
                matched_actions.add(action_source)
            status, diagnostic = "PASS", "MATCHED_UNIQUELY"
        else:
            status = "BLOCKING"
            diagnostic = reasons.get(action_index)
            if not diagnostic:
                diagnostic = "AMBIGUOUS_MULTIPLE_CANDIDATES" if len(pairs) > 1 else "AMBIGUOUS_SHARED_CANDIDATE"
            if action_source is not None:
                unresolved_actions.add(action_source)

        diagnostics.append({
            "action_source_row": action_source,
            "action_date": action_date,
            "security_name": _text(action.get("security_name")),
            "isin": _text(action.get("isin")),
            "candidate_positive_rows": positive_count,
            "candidate_funding_rows": negative_count,
            "candidate_pairs": len(pairs),
            "validation_status": status,
            "diagnostic": diagnostic,
        })

    orphan_cash_sources = candidate_cash_sources - matched_sources
    for source in sorted(orphan_cash_sources):
        row = dividend_rows[pd.to_numeric(dividend_rows["source_row"], errors="coerce").eq(source)].iloc[0]
        diagnostics.append({
            "action_source_row": np.nan,
            "action_date": _date(row.get("event_date")),
            "security_name": _text(row.get("security_name")),
            "isin": _text(row.get("isin")),
            "candidate_positive_rows": int((_number(row.get("amount")) or 0.0) > 0),
            "candidate_funding_rows": int((_number(row.get("amount")) or 0.0) < 0),
            "candidate_pairs": 0,
            "validation_status": "BLOCKING",
            "diagnostic": "DIVIDEND_REINVESTMENT_CASH_ROW_WITHOUT_MATCHED_ACTION",
        })

    return {
        "events": pd.DataFrame(events, columns=REINVESTMENT_EVENT_COLUMNS),
        "diagnostics": pd.DataFrame(diagnostics, columns=REINVESTMENT_DIAGNOSTIC_COLUMNS),
        "matched_source_rows": matched_sources,
        "matched_action_source_rows": matched_actions,
        "unresolved_action_source_rows": unresolved_actions,
        "candidate_cash_source_rows": candidate_cash_sources,
        "unresolved_source_rows": unresolved_actions | orphan_cash_sources,
    }


def reconcile_reinvestment_gross(
    dividends: pd.DataFrame,
    reinvestments: pd.DataFrame,
    external_events: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply a unique declared-DPS gross match while retaining provenance."""
    if dividends is None:
        dividends = pd.DataFrame()
    if reinvestments is None:
        reinvestments = pd.DataFrame(columns=REINVESTMENT_EVENT_COLUMNS)
    ledger, events = dividends.copy(), reinvestments.copy()
    if ledger.empty or events.empty or external_events is None or external_events.empty:
        return ledger, events

    for event_index, event in events.iterrows():
        if _text(event.get("validation_status")) != "PASS":
            continue
        isin = _text(event.get("isin"))
        payment = _date(event.get("payment_date"))
        entitlement = _number(event.get("entitlement_quantity"))
        base = _number(event.get("broker_reinvestment_base_eur"))
        if not isin or payment is None or entitlement is None or base is None:
            continue
        candidate_rows = []
        for external_index, candidate in external_events.iterrows():
            if _text(candidate.get("isin")) != isin:
                continue
            ex_date = _date(candidate.get("ex_dividend_date"))
            pay_date = _date(candidate.get("payment_date"))
            quantity = _number(candidate.get("entitlement_quantity"))
            dps = _number(candidate.get("declared_dividend_per_share_eur"))
            gross = _number(candidate.get("expected_gross_dividend_eur"))
            confidence = _text(candidate.get("confidence"))
            if ex_date is None or ex_date > payment or (payment - ex_date).days > 120:
                continue
            if pay_date is not None and abs((payment - pay_date).days) > 10:
                continue
            if quantity is None or abs(quantity - entitlement) > max(1e-6, entitlement * 1e-6):
                continue
            if dps is None or gross is None or gross + 0.005 < base:
                continue
            if abs(gross - dps * entitlement) > max(0.005, gross * 1e-8):
                continue
            if confidence not in {"EXTERNAL_DATES_AND_AMOUNT", "PARTIAL_EXTERNAL_EVIDENCE"}:
                continue
            candidate_rows.append((external_index, candidate, gross, dps))
        if len(candidate_rows) != 1:
            events.loc[event_index, "reconciliation_status"] = (
                "GROSS_RECONCILIATION_AMBIGUOUS" if candidate_rows else "GROSS_UNAVAILABLE"
            )
            continue

        _, candidate, gross, dps = candidate_rows[0]
        foreign = gross - base
        if foreign < -0.005:
            events.loc[event_index, "reconciliation_status"] = "GROSS_BELOW_REINVESTMENT_BASE_REJECTED"
            continue
        foreign = max(0.0, foreign)
        domestic = _number(event.get("domestic_tax_and_solidarity_surcharge_eur")) or 0.0
        domestic_refund = _number(event.get("domestic_tax_refund_eur")) or 0.0
        total_tax = foreign + domestic - domestic_refund
        updates = {
            "gross_dividend_eur": gross,
            "foreign_withholding_tax_eur": foreign,
            "total_dividend_tax_eur": total_tax,
            "gross_dividend_per_share_eur": dps,
            "gross_source": "RECONCILED_DECLARED_DPS",
            "foreign_withholding_source": "DERIVED_FROM_RECONCILED_GROSS_AND_BROKER_REINVESTMENT_BASE",
            "reconciliation_status": "RECONCILED_DECLARED_DPS",
            "diagnostic": _text(event.get("diagnostic")) + ";EXTERNAL_DECLARED_DPS_RECONCILED",
        }
        for column, value in updates.items():
            events.loc[event_index, column] = value

        mask = ledger.get("reinvestment_event_id", pd.Series("", index=ledger.index)).astype(str).eq(
            _text(event.get("event_id"))
        )
        if not mask.any():
            continue
        ledger.loc[mask, "gross_dividend_eur"] = gross
        ledger.loc[mask, "foreign_withholding_tax_eur"] = foreign
        ledger.loc[mask, "dividend_tax_withheld_eur"] = total_tax
        ledger.loc[mask, "total_dividend_tax_eur"] = total_tax
        ledger.loc[mask, "dividend_per_share_gross_eur"] = dps
        ledger.loc[mask, "gross_source"] = updates["gross_source"]
        ledger.loc[mask, "foreign_withholding_source"] = updates["foreign_withholding_source"]
        ledger.loc[mask, "reinvestment_reconciliation_status"] = updates["reconciliation_status"]
        refund = _number(ledger.loc[mask, "dividend_tax_refund_eur"].iloc[0]) or 0.0
        net = _number(ledger.loc[mask, "net_dividend_eur"].iloc[0]) or 0.0
        ledger.loc[mask, "reconciliation_error"] = gross - total_tax + refund - net
    return ledger, events
