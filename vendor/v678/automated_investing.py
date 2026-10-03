"""SaveBack attribution over the canonical portfolio ledgers.

This module is deliberately analytical: it never mutates FIFO, cost basis,
cash, performance, or lifetime-accounting outputs. SaveBack is external reward
capital attributed to purchases that the portfolio engine already accounts for.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np
import pandas as pd


SAVEBACK_EVENT_COLUMNS = [
    "reward_date", "reward_earning_month", "reward_amount_eur",
    "investment_date", "days_to_investment", "security_name", "isin",
    "purchase_quantity", "purchase_price_eur", "reward_funded_quantity",
    "open_reward_funded_quantity", "realized_reward_funded_quantity",
    "current_price_eur", "valuation_date", "open_current_value_eur",
    "realized_proceeds_eur", "current_and_realized_wealth_eur",
    "market_gain_loss_eur", "simple_return_pct", "annualized_return_pct",
    "holding_days", "lot_status", "cumulative_reward_capital_eur",
    "cumulative_current_value_eur", "match_status", "match_method",
    "provenance", "warning",
]

def _num(value: Any, default: float = np.nan) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _date(value: Any) -> pd.Timestamp:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return pd.NaT
    return pd.Timestamp(parsed).normalize()


def _sum_known(values: Iterable[Any]) -> float:
    series = pd.to_numeric(pd.Series(list(values), dtype="object"), errors="coerce")
    return float(series.sum()) if series.notna().any() else np.nan


def calculate_xirr(cashflows: Iterable[tuple[Any, float]]) -> float:
    """Return annualized XIRR as a percentage, or NaN when it is undefined."""
    clean = [(_date(d), _num(v)) for d, v in cashflows]
    clean = [(d, v) for d, v in clean if pd.notna(d) and math.isfinite(v) and abs(v) > 1e-12]
    if not clean or not any(v < 0 for _, v in clean) or not any(v > 0 for _, v in clean):
        return np.nan
    clean.sort(key=lambda item: item[0])
    origin = clean[0][0]

    def npv(rate: float) -> float:
        if rate <= -1.0:
            return math.inf
        return sum(v / ((1.0 + rate) ** (((d - origin).days) / 365.0)) for d, v in clean)

    low, high = -0.999999, 1.0
    f_low, f_high = npv(low), npv(high)
    for _ in range(80):
        if math.isfinite(f_low) and math.isfinite(f_high) and f_low * f_high <= 0:
            break
        high *= 2.0
        f_high = npv(high)
    else:
        return np.nan
    for _ in range(160):
        mid = (low + high) / 2.0
        f_mid = npv(mid)
        if abs(f_mid) < 1e-10:
            return mid * 100.0
        if f_low * f_mid <= 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return ((low + high) / 2.0) * 100.0


def _trade_for_promo(promo_row: pd.Series, trades: pd.DataFrame) -> tuple[pd.Series | None, str]:
    if trades is None or trades.empty:
        return None, "NO_TRADE_LEDGER"
    buys = trades[trades.get("type_norm", pd.Series(index=trades.index, dtype="object")).eq("BUY")].copy()
    matched_trade_id = str(promo_row.get("matched_trade_id", "") or "").strip()
    if matched_trade_id and "trade_id" in buys.columns:
        exact = buys[buys["trade_id"].astype(str).eq(matched_trade_id)]
        if len(exact) == 1:
            return exact.iloc[0], "EXISTING_PROMO_TRADE_ID"
        if len(exact) > 1:
            return None, "AMBIGUOUS_TRADE_ID"
    source_row = _num(promo_row.get("matched_trade_source_row"))
    if math.isfinite(source_row) and "source_row" in buys.columns:
        exact = buys[pd.to_numeric(buys["source_row"], errors="coerce").eq(source_row)]
        if len(exact) == 1:
            return exact.iloc[0], "EXISTING_PROMO_SOURCE_ROW"
        if len(exact) > 1:
            return None, "AMBIGUOUS_SOURCE_ROW"

    matched_date = _date(promo_row.get("matched_buy_date"))
    matched_isin = str(promo_row.get("matched_isin", "") or "").strip()
    amount = _num(promo_row.get("matched_amount_eur"), 0.0)
    if pd.isna(matched_date) or not matched_isin or amount <= 0:
        return None, "PROMO_MATCH_FIELDS_INCOMPLETE"
    candidates = buys[
        pd.to_datetime(buys.get("event_date"), errors="coerce").dt.normalize().eq(matched_date)
        & buys.get("isin", pd.Series(index=buys.index, dtype="object")).fillna("").astype(str).eq(matched_isin)
    ].copy()
    if "matched_promo_credit_eur" in candidates.columns:
        candidates = candidates[
            (pd.to_numeric(candidates["matched_promo_credit_eur"], errors="coerce") - amount).abs().le(0.02)
        ]
    if len(candidates) == 1:
        return candidates.iloc[0], "EXISTING_PROMO_FIELDS_UNIQUE"
    return None, "AMBIGUOUS_EXISTING_PROMO_MATCH" if len(candidates) > 1 else "MATCHED_BUY_NOT_FOUND"


def _price_lookup(combined: pd.DataFrame, isin: str) -> tuple[float, Any]:
    if combined is None or combined.empty or "isin" not in combined.columns:
        return np.nan, None
    rows = combined[combined["isin"].fillna("").astype(str).eq(str(isin))]
    if rows.empty and "current_isin" in combined.columns:
        rows = combined[combined["current_isin"].fillna("").astype(str).eq(str(isin))]
    if rows.empty:
        return np.nan, None
    row = rows.iloc[0]
    return _num(row.get("live_price_eur")), row.get("live_price_date")


def _lot_lineage(trade: pd.Series, open_lots: pd.DataFrame, realized: pd.DataFrame, fraction: float) -> dict[str, float]:
    trade_id = str(trade.get("trade_id", "") or "")
    open_qty = np.nan
    if open_lots is not None and not open_lots.empty and "source_trade_id" in open_lots.columns and trade_id:
        rows = open_lots[open_lots["source_trade_id"].astype(str).eq(trade_id)]
        open_qty = _sum_known(rows.get("quantity_remaining", pd.Series(dtype=float))) * fraction if not rows.empty else 0.0
    realized_qty = 0.0
    realized_proceeds = 0.0
    if realized is not None and not realized.empty and "source_buy_trade_id" in realized.columns and trade_id:
        rows = realized[realized["source_buy_trade_id"].astype(str).eq(trade_id)]
        if not rows.empty:
            realized_qty = _sum_known(rows.get("quantity_sold", pd.Series(dtype=float))) * fraction
            realized_proceeds = _sum_known(rows.get("allocated_net_sell_proceeds_eur", pd.Series(dtype=float))) * fraction
    return {
        "open_quantity": open_qty,
        "realized_quantity": realized_qty,
        "realized_proceeds": realized_proceeds,
    }


def build_saveback_events(
    promo: pd.DataFrame,
    trades: pd.DataFrame,
    open_lots: pd.DataFrame,
    realized: pd.DataFrame,
    combined: pd.DataFrame,
    as_of: Any,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if promo is None or promo.empty:
        return pd.DataFrame(columns=SAVEBACK_EVENT_COLUMNS)
    source = promo[promo.get("promo_type", pd.Series(index=promo.index, dtype="object")).eq("BENEFITS_SAVEBACK")].copy()
    source = source.sort_values([c for c in ["event_date", "source_row"] if c in source.columns])
    cumulative_capital = 0.0
    cumulative_value = 0.0
    valuation_as_of = _date(as_of)
    for _, reward in source.iterrows():
        reward_date = _date(reward.get("event_date"))
        reward_amount = _num(reward.get("promo_amount_eur"), 0.0)
        cumulative_capital += reward_amount
        trade, provenance = _trade_for_promo(reward, trades)
        row: dict[str, Any] = {
            "reward_date": reward_date,
            "reward_earning_month": str(reward_date.to_period("M") - 1) if pd.notna(reward_date) else None,
            "reward_amount_eur": reward_amount,
            "investment_date": pd.NaT,
            "days_to_investment": np.nan,
            "security_name": reward.get("matched_security_name") or reward.get("security_name") or "",
            "isin": reward.get("matched_isin") or reward.get("isin") or "",
            "purchase_quantity": np.nan,
            "purchase_price_eur": np.nan,
            "reward_funded_quantity": np.nan,
            "open_reward_funded_quantity": np.nan,
            "realized_reward_funded_quantity": np.nan,
            "current_price_eur": np.nan,
            "valuation_date": pd.NaT,
            "open_current_value_eur": np.nan,
            "realized_proceeds_eur": np.nan,
            "current_and_realized_wealth_eur": np.nan,
            "market_gain_loss_eur": np.nan,
            "simple_return_pct": np.nan,
            "annualized_return_pct": np.nan,
            "holding_days": np.nan,
            "lot_status": "UNRESOLVED",
            "cumulative_reward_capital_eur": cumulative_capital,
            "cumulative_current_value_eur": np.nan,
            "match_status": str(reward.get("promo_match_status", "UNMATCHED") or "UNMATCHED"),
            "match_method": str(reward.get("promo_match_method", "") or ""),
            "provenance": provenance,
            "warning": "",
        }
        if trade is None:
            row["warning"] = "Reward could not be linked uniquely to one canonical BUY."
            rows.append(row)
            continue
        investment_date = _date(trade.get("event_date"))
        acquisition_cost = _num(trade.get("acquisition_cost_basis_eur"), 0.0)
        purchase_qty = abs(_num(trade.get("quantity"), 0.0))
        fraction = reward_amount / acquisition_cost if acquisition_cost > 0 else np.nan
        if not math.isfinite(fraction) or fraction < 0 or fraction > 1.000001:
            row["warning"] = "Reward-funded lot fraction is unavailable or outside the canonical BUY cost."
            rows.append(row)
            continue
        fraction = min(1.0, fraction)
        funded_qty = purchase_qty * fraction
        lineage = _lot_lineage(trade, open_lots, realized, fraction)
        current_price, valuation_date = _price_lookup(combined, str(trade.get("isin", "")))
        open_value = (
            lineage["open_quantity"] * current_price
            if math.isfinite(lineage["open_quantity"]) and math.isfinite(current_price)
            else np.nan
        )
        realized_proceeds = lineage["realized_proceeds"]
        wealth = (
            open_value + realized_proceeds
            if math.isfinite(open_value) and math.isfinite(realized_proceeds)
            else np.nan
        )
        gain = wealth - reward_amount if math.isfinite(wealth) else np.nan
        simple_return = gain / reward_amount * 100.0 if reward_amount > 0 and math.isfinite(gain) else np.nan
        holding_days = (valuation_as_of - investment_date).days if pd.notna(valuation_as_of) and pd.notna(investment_date) else np.nan
        annualized = (
            ((wealth / reward_amount) ** (365.0 / holding_days) - 1.0) * 100.0
            if reward_amount > 0 and math.isfinite(wealth) and wealth > 0 and holding_days and holding_days > 0
            else np.nan
        )
        open_qty = lineage["open_quantity"]
        realized_qty = lineage["realized_quantity"]
        if math.isfinite(open_qty):
            lot_status = "OPEN" if realized_qty <= 1e-12 else ("PARTIALLY_REALIZED" if open_qty > 1e-12 else "FULLY_REALIZED")
        else:
            lot_status = "LINEAGE_UNAVAILABLE"
        if math.isfinite(open_value):
            cumulative_value += open_value + (realized_proceeds if math.isfinite(realized_proceeds) else 0.0)
        row.update({
            "investment_date": investment_date,
            "days_to_investment": (investment_date - reward_date).days if pd.notna(investment_date) and pd.notna(reward_date) else np.nan,
            "security_name": trade.get("security_name", row["security_name"]),
            "isin": trade.get("isin", row["isin"]),
            "purchase_quantity": purchase_qty,
            "purchase_price_eur": _num(trade.get("trade_price_eur")),
            "reward_funded_quantity": funded_qty,
            "open_reward_funded_quantity": open_qty,
            "realized_reward_funded_quantity": realized_qty,
            "current_price_eur": current_price,
            "valuation_date": _date(valuation_date),
            "open_current_value_eur": open_value,
            "realized_proceeds_eur": realized_proceeds,
            "current_and_realized_wealth_eur": wealth,
            "market_gain_loss_eur": gain,
            "simple_return_pct": simple_return,
            "annualized_return_pct": annualized,
            "holding_days": holding_days,
            "lot_status": lot_status,
            "cumulative_current_value_eur": cumulative_value,
            "match_status": "MATCHED",
        })
        rows.append(row)
    return pd.DataFrame(rows, columns=SAVEBACK_EVENT_COLUMNS)


def _saveback_monthly_summary(saveback: pd.DataFrame, as_of: Any) -> pd.DataFrame:
    reward_dates = pd.to_datetime(
        saveback.get("reward_date", pd.Series(dtype="object")), errors="coerce"
    ).dropna() if saveback is not None and not saveback.empty else pd.Series(dtype="datetime64[ns]")
    if reward_dates.empty:
        return pd.DataFrame(columns=[
            "month_start", "year_month", "saveback_reward_eur",
            "saveback_event_count", "cumulative_saveback_eur",
        ])
    start = reward_dates.min().to_period("M").to_timestamp()
    end = _date(as_of)
    end = end.to_period("M").to_timestamp() if pd.notna(end) else reward_dates.max().to_period("M").to_timestamp()
    months = pd.DataFrame({"month_start": pd.date_range(start, end, freq="MS")})
    months["year_month"] = months["month_start"].dt.strftime("%Y-%m")
    sb = pd.DataFrame(columns=["year_month", "saveback_reward_eur", "saveback_event_count"])
    if saveback is not None and not saveback.empty:
        work = saveback.copy()
        work["year_month"] = pd.to_datetime(work["reward_date"], errors="coerce").dt.strftime("%Y-%m")
        sb = work.groupby("year_month", dropna=False).agg(
            saveback_reward_eur=("reward_amount_eur", "sum"),
            saveback_event_count=("reward_amount_eur", "size"),
        ).reset_index()
    out = months.merge(sb, how="left", on="year_month")
    for column in ["saveback_reward_eur", "saveback_event_count"]:
        out[column] = pd.to_numeric(out[column], errors="coerce").fillna(0)
    out["cumulative_saveback_eur"] = out["saveback_reward_eur"].cumsum()
    return out


def _saveback_security_summary(saveback: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if saveback is not None and not saveback.empty:
        for (name, isin), group in saveback.groupby(["security_name", "isin"], dropna=False):
            funded_qty = _sum_known(group["reward_funded_quantity"])
            open_qty = _sum_known(group["open_reward_funded_quantity"])
            execution_prices = pd.to_numeric(group["purchase_price_eur"], errors="coerce")
            funded_quantities = pd.to_numeric(group["reward_funded_quantity"], errors="coerce")
            weighted_execution_price = (
                float((execution_prices * funded_quantities).sum() / funded_quantities.sum())
                if funded_quantities.notna().any() and funded_quantities.sum() > 0 else np.nan
            )
            current_prices = pd.to_numeric(group["current_price_eur"], errors="coerce").dropna()
            wealth = _sum_known(group["current_and_realized_wealth_eur"])
            capital = _sum_known(group["reward_amount_eur"])
            realized_qty = _sum_known(group["realized_reward_funded_quantity"])
            rows.append({
                "source": "SaveBack", "capital_source": "Trade Republic reward",
                "security_name": name, "isin": isin, "event_count": int(len(group)),
                "contributed_capital_eur": capital,
                "reward_funded_quantity": funded_qty,
                "open_reward_funded_quantity": open_qty,
                "weighted_average_execution_price_eur": weighted_execution_price,
                "current_price_eur": float(current_prices.iloc[-1]) if not current_prices.empty else np.nan,
                "open_current_value_eur": _sum_known(group["open_current_value_eur"]),
                "realized_proceeds_eur": _sum_known(group["realized_proceeds_eur"]),
                "current_and_realized_wealth_eur": wealth,
                "market_gain_loss_eur": _sum_known(group["market_gain_loss_eur"]),
                "simple_return_pct": (wealth / capital - 1.0) * 100.0 if capital > 0 and math.isfinite(wealth) else np.nan,
                "first_saveback_investment": pd.to_datetime(group["investment_date"], errors="coerce").min(),
                "latest_saveback_investment": pd.to_datetime(group["investment_date"], errors="coerce").max(),
                "lot_status": "OPEN" if realized_qty <= 1e-12 else ("CLOSED" if open_qty <= 1e-12 else "PARTIALLY_REALIZED"),
            })
    return pd.DataFrame(rows, columns=[
        "source", "capital_source", "security_name", "isin", "event_count",
        "contributed_capital_eur", "reward_funded_quantity", "open_reward_funded_quantity",
        "weighted_average_execution_price_eur", "current_price_eur", "open_current_value_eur",
        "realized_proceeds_eur", "current_and_realized_wealth_eur", "market_gain_loss_eur",
        "simple_return_pct", "first_saveback_investment", "latest_saveback_investment", "lot_status",
    ])


def _saveback_compounding_scenarios(monthly: pd.DataFrame) -> pd.DataFrame:
    saveback_average = _num(monthly.get("saveback_reward_eur", pd.Series(dtype=float)).mean(), 0.0) if not monthly.empty else 0.0
    rows = []
    for annual_rate in [5.0, 7.0, 10.0]:
        monthly_rate = (1.0 + annual_rate / 100.0) ** (1.0 / 12.0) - 1.0
        for years in [5, 10, 20, 30]:
            periods = years * 12
            for source, contribution in [
                ("SaveBack historical monthly average", saveback_average),
            ]:
                future_value = contribution * (((1.0 + monthly_rate) ** periods - 1.0) / monthly_rate) if monthly_rate else contribution * periods
                rows.append({
                    "scenario_source": source,
                    "monthly_contribution_eur": contribution,
                    "annual_return_assumption_pct": annual_rate,
                    "years": years,
                    "contributed_capital_eur": contribution * periods,
                    "illustrative_future_value_eur": future_value,
                    "illustrative_growth_eur": future_value - contribution * periods,
                    "assumption": "End-of-month contributions; constant return; no tax, fees or inflation. Illustration, not a forecast.",
                })
    return pd.DataFrame(rows)


def _spending_diagnostic(monthly: pd.DataFrame, expense_monthly: pd.DataFrame) -> pd.DataFrame:
    if expense_monthly is None or expense_monthly.empty:
        return pd.DataFrame(columns=[
            "year_month", "gross_consumer_spend_eur", "saveback_reward_eur",
            "saveback_to_gross_spend_pct", "diagnostic_warning",
        ])
    cols = [c for c in ["year_month", "gross_consumer_spend_eur"] if c in expense_monthly.columns]
    out = expense_monthly[cols].copy()
    if "gross_consumer_spend_eur" not in out.columns:
        out["gross_consumer_spend_eur"] = np.nan
    reward = monthly[["year_month", "saveback_reward_eur"]] if not monthly.empty else pd.DataFrame(columns=["year_month", "saveback_reward_eur"])
    out = out.merge(reward, how="left", on="year_month")
    out["saveback_reward_eur"] = pd.to_numeric(out["saveback_reward_eur"], errors="coerce").fillna(0.0)
    spend = pd.to_numeric(out["gross_consumer_spend_eur"], errors="coerce")
    out["saveback_to_gross_spend_pct"] = np.where(spend.gt(0), out["saveback_reward_eur"] / spend * 100.0, np.nan)
    out["diagnostic_warning"] = "Gross dashboard spending is not equivalent to SaveBack-eligible spending; timing, exclusions and historical rules differ."
    return out


def _saveback_summary(events: pd.DataFrame, monthly: pd.DataFrame, as_of: Any) -> dict[str, Any]:
    total = _sum_known(events.get("reward_amount_eur", pd.Series(dtype=float))) if not events.empty else 0.0
    open_value = _sum_known(events.get("open_current_value_eur", pd.Series(dtype=float))) if not events.empty else 0.0
    realized = _sum_known(events.get("realized_proceeds_eur", pd.Series(dtype=float))) if not events.empty else 0.0
    wealth = open_value + realized if math.isfinite(open_value) and math.isfinite(realized) else np.nan
    gain = wealth - total if math.isfinite(wealth) else np.nan
    cashflows = []
    if not events.empty:
        cashflows.extend((d, -a) for d, a in zip(events["investment_date"], events["reward_amount_eur"]) if pd.notna(d) and _num(a, 0.0) > 0)
        if realized > 0:
            # Exact sale-date cash flows are not retained at event-summary level;
            # realized proceeds stay in wealth but XIRR is withheld in that case.
            cashflows = []
        if open_value > 0:
            cashflows.append((_date(as_of), open_value))
    monthly_values = pd.to_numeric(monthly.get("saveback_reward_eur", pd.Series(dtype=float)), errors="coerce")
    positive_months = monthly_values[monthly_values.gt(0)]
    first_date = events["reward_date"].min() if not events.empty else pd.NaT
    latest_date = events["reward_date"].max() if not events.empty else pd.NaT
    months_active = (
        int((_date(as_of).year - first_date.year) * 12 + _date(as_of).month - first_date.month + 1)
        if pd.notna(first_date) and pd.notna(_date(as_of)) else 0
    )
    return {
        "status": "OK" if not events.empty else "NO_SAVEBACK_EVENTS",
        "event_count": int(len(events)),
        "matched_event_count": int(events.get("match_status", pd.Series(dtype="object")).eq("MATCHED").sum()) if not events.empty else 0,
        "unresolved_event_count": int((~events.get("match_status", pd.Series(dtype="object")).eq("MATCHED")).sum()) if not events.empty else 0,
        "first_reward_date": first_date,
        "latest_reward_date": latest_date,
        "months_active": months_active,
        "destination_security_count": int(events[["security_name", "isin"]].drop_duplicates().shape[0]) if not events.empty else 0,
        "total_reward_capital_eur": total,
        "open_current_value_eur": open_value,
        "realized_proceeds_eur": realized,
        "current_and_realized_wealth_eur": wealth,
        "market_gain_loss_eur": gain,
        "simple_return_pct": gain / total * 100.0 if total > 0 and math.isfinite(gain) else np.nan,
        "money_weighted_return_xirr_pct": calculate_xirr(cashflows),
        "average_monthly_saveback_eur": float(positive_months.mean()) if not positive_months.empty else 0.0,
        "median_monthly_saveback_eur": float(positive_months.median()) if not positive_months.empty else 0.0,
        "maximum_monthly_saveback_eur": float(positive_months.max()) if not positive_months.empty else 0.0,
        "months_at_exactly_15_eur": int(np.isclose(monthly_values, 15.0, atol=0.005).sum()),
        "attributable_dividends_eur": np.nan,
        "dividend_attribution_status": "NOT_INCLUDED_EXACT_ENTITLEMENT_NOT_AVAILABLE",
    }


def build_saveback_analytics(
    transactions: pd.DataFrame,
    promo: pd.DataFrame,
    trades: pd.DataFrame,
    open_lots: pd.DataFrame,
    realized: pd.DataFrame,
    combined: pd.DataFrame,
    expense_monthly: pd.DataFrame,
    as_of: Any,
) -> dict[str, Any]:
    saveback = build_saveback_events(promo, trades, open_lots, realized, combined, as_of)
    monthly = _saveback_monthly_summary(saveback, as_of)
    security = _saveback_security_summary(saveback)
    saveback_summary = _saveback_summary(saveback, monthly, as_of)
    diagnostics = pd.DataFrame([
        {"check": "saveback_event_reconciliation", "status": "PASS" if saveback_summary["unresolved_event_count"] == 0 else "WARNING", "value": saveback_summary["unresolved_event_count"], "message": "Unresolved SaveBack rewards"},
        {"check": "dividend_attribution", "status": "INFO", "value": np.nan, "message": "Not included because exact historical lot entitlement is not currently available."},
    ])
    return {
        "saveback_events": saveback,
        "saveback_summary": saveback_summary,
        "monthly": monthly,
        "security_summary": security,
        "spending_diagnostic": _spending_diagnostic(monthly, expense_monthly),
        "compounding_scenarios": _saveback_compounding_scenarios(monthly),
        "diagnostics": diagnostics,
    }
