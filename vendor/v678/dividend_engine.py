"""External dividend enrichment. Never writes to broker accounting ledgers.

Yahoo history is an ex-date series, NOT a receipt/payment-date series. Dates,
amounts, currencies and tax estimates are nullable. All exported fields are an
explicit allowlist: no broker identifiers, references or provider error bodies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import re
from typing import Callable

import pandas as pd

RECENT_DAYS = 180
EVENT_COLUMNS = [
    "security_name", "isin", "historical_isin_aliases", "current_quantity",
    "event_id", "event_status", "ex_dividend_date", "record_date", "payment_date",
    "declared_dividend_per_share", "dividend_currency", "fx_to_eur",
    "declared_dividend_per_share_eur", "entitlement_quantity", "entitlement_status",
    "expected_gross_dividend_eur", "estimated_net_dividend_eur", "net_method",
    "source_provider", "source_ticker", "data_retrieved_at", "confidence",
    "diagnostic", "reconciliation_status", "forecast_inclusion",
]
FORECAST_COLUMNS = [
    "security_name", "isin", "event_id", "category", "ex_dividend_date",
    "expected_payment_date", "gross_dividend_eur", "estimated_net_dividend_eur",
    "source_ticker", "method", "confidence",
]
AUDIT_COLUMNS = [
    "security_name", "isin", "source_ticker", "lookup_status", "history_events",
    "calendar_status", "dividend_currency", "data_retrieved_at", "diagnostic",
]


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def day(value):
    """Keep the exchange-local calendar date; do not shift midnight into UTC."""
    if value is None or isinstance(value, (list, tuple, dict)):
        return None
    try:
        if isinstance(value, (int, float)):
            return None  # untyped epoch seconds must not become nanoseconds
        stamp = pd.Timestamp(value)
        return None if pd.isna(stamp) else stamp.tz_localize(None).normalize()
    except (TypeError, ValueError):
        return None


def iso(value):
    value = day(value)
    return value.strftime("%Y-%m-%d") if value is not None else None


def complete_sum(values):
    values = list(values)
    return sum(values) if all(number(v) is not None for v in values) else None


def summarize_net_forecast_coverage(rows):
    """Summarize usable net information without treating unknown net as zero.

    Events without a defensible gross amount are excluded from the coverage
    universe because neither their gross weight nor their net can yet be
    quantified.  The strict complete-net total remains a separate concept.
    """
    relevant = [row for row in rows if number(row.get("gross_dividend_eur")) is not None]
    estimable = [row for row in relevant if number(row.get("estimated_net_dividend_eur")) is not None]
    unresolved = [row for row in relevant if number(row.get("estimated_net_dividend_eur")) is None]
    gross_total = sum(number(row.get("gross_dividend_eur")) for row in relevant)
    estimable_gross = sum(number(row.get("gross_dividend_eur")) for row in estimable)
    known_net = sum(number(row.get("estimated_net_dividend_eur")) for row in estimable)
    unresolved_gross = sum(number(row.get("gross_dividend_eur")) for row in unresolved)
    if relevant:
        event_coverage = len(estimable) / len(relevant) * 100
        gross_coverage = estimable_gross / gross_total * 100 if gross_total else (100.0 if not unresolved else 0.0)
        status = "COMPLETE" if not unresolved else "PARTIAL"
    else:
        event_coverage = gross_coverage = None
        status = "NO_QUANTIFIED_EVENTS"
    return {
        "forward_12m_gross_forecast_eur": gross_total,
        "forward_12m_estimated_net_known_subtotal_eur": known_net,
        "forward_12m_gross_awaiting_net_estimation_eur": unresolved_gross,
        "forward_12m_relevant_gross_event_count": len(relevant),
        "forward_12m_net_estimable_event_count": len(estimable),
        "forward_12m_net_unresolved_event_count": len(unresolved),
        "forward_12m_net_coverage_event_pct": event_coverage,
        "forward_12m_net_coverage_gross_pct": gross_coverage,
        "forward_12m_net_forecast_completeness": status,
        "forward_12m_net_unresolved_securities": sorted({
            str(row.get("security_name") or row.get("isin") or "Unknown") for row in unresolved
        }),
    }


def ratio(value, basis):
    basis = number(basis)
    return value / basis * 100 if value is not None and basis and basis > 0 else None


@dataclass
class DividendSnapshot:
    ticker: str
    history: list[dict] = field(default_factory=list)
    calendar: dict = field(default_factory=dict)
    currency: str | None = None
    history_status: str = "OK"
    calendar_status: str = "OK"
    diagnostic: str = ""
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class YahooDividendProvider:
    """At most one history and calendar call per ticker/run; sequential and cached.

    Reuses valuation currency when present, otherwise the public history
    metadata accessor (which yfinance can supplement with an intraday request).
    Explicit per-distribution currency overrides the price currency. No info
    request is needed. Mixed-currency history is not summed as one currency.
    """
    def __init__(self, ticker_factory: Callable, currency_hints=None):
        self.ticker_factory = ticker_factory
        self.currency_hints = currency_hints or {}
        self.cache = {}
        self.rate_limited = False

    def fetch(self, ticker):
        ticker = str(ticker).strip().upper()
        if ticker in self.cache:
            return self.cache[ticker]
        result = DividendSnapshot(ticker)
        self.cache[ticker] = result
        if self.rate_limited:
            result.history_status = result.calendar_status = "UNAVAILABLE"
            result.diagnostic = "RATE_LIMIT_CIRCUIT_OPEN"
            return result
        errors = []
        try:
            obj = self.ticker_factory(ticker)
        except Exception as exc:
            result.history_status = result.calendar_status = "FAILED"
            result.diagnostic = "TICKER_INIT_" + type(exc).__name__
            return result
        for component in ("history", "calendar"):
            try:
                if component == "history":
                    series = obj.get_dividends(period="max")
                    explicit_currencies = {}
                    if isinstance(series, pd.DataFrame) and "Dividends" in series and "currency" in series:
                        explicit_currencies = {iso(d): str(c) for d, c in series["currency"].items() if pd.notna(c)}
                        series = series["Dividends"]
                    if not isinstance(series, pd.Series):
                        raise ValueError("Missing history response")
                    seen = {}
                    for date, amount in series.items():
                        date, amount = iso(date), number(amount)
                        if date and amount is not None and amount > 0:
                            if date in seen and abs(seen[date] - amount) > 1e-8:
                                errors.append("CONFLICTING_HISTORY_SAME_DATE")
                                seen[date] = None
                            elif date not in seen:
                                seen[date] = amount
                    result.history = [{"ex_date": d, "dps": v, "currency": explicit_currencies.get(d)} for d, v in sorted(seen.items()) if v is not None]
                    hint = self.currency_hints.get(ticker)
                    meta = {"currency": hint} if isinstance(hint, str) and hint and hint != "UNKNOWN" else obj.get_history_metadata()
                    result.currency = (meta or {}).get("currency")
                    if explicit_currencies:
                        result.currency = explicit_currencies[max(explicit_currencies)]
                        if len(set(explicit_currencies.values())) > 1:
                            errors.append("MIXED_HISTORY_CURRENCIES_LATEST_CURRENCY_ONLY_FOR_ANNUAL_ESTIMATE")
                    if not meta:
                        # yfinance sometimes swallows an HTTP error and returns
                        # empty history. Do not certify that as a non-payer.
                        errors.append("HISTORY_METADATA_UNAVAILABLE")
                        if not result.history:
                            result.history_status = "UNAVAILABLE"
                else:
                    calendar = obj.get_calendar()
                    if not isinstance(calendar, dict):
                        raise ValueError("Malformed calendar")
                    result.calendar = calendar
                    result.calendar_status = "OK" if calendar else "EMPTY_OR_UNAVAILABLE"
            except Exception as exc:
                setattr(result, component + "_status", "FAILED")
                errors.append(component.upper() + "_" + type(exc).__name__)
                if "ratelimit" in type(exc).__name__.lower() or "429" in str(exc):
                    self.rate_limited = True
                    errors.append("RATE_LIMITED")
                    break
        if not result.currency and self.currency_hints.get(ticker):
            result.currency = self.currency_hints[ticker]
            errors.append("CURRENCY_FROM_VALUATION_METADATA")
        result.diagnostic = ";".join(errors)
        return result


def _security_rows(frame, isin, aliases):
    if frame is None or frame.empty or "isin" not in frame:
        return pd.DataFrame()
    return frame[frame["isin"].astype(str).isin({isin, *aliases})].copy()


def _actions_for(actions, isin, aliases):
    if actions is None or actions.empty:
        return pd.DataFrame()
    mask = pd.Series(False, index=actions.index)
    for col in ("current_isin", "old_isin", "new_isin"):
        if col in actions:
            mask |= actions[col].astype(str).isin({isin, *aliases})
    return actions[mask].copy()


def unit_factor(actions, ex_date):
    """Current units per ex-date unit, for splits AFTER the ex-date."""
    if actions.empty or ex_date is None:
        return 1.0
    factor = 1.0
    for row in actions.to_dict("records"):
        date = day(row.get("event_date"))
        if date is not None and date > ex_date and row.get("validation_status") == "PASS":
            f = number(row.get("derived_or_declared_ratio"))
            if f and f > 0:
                factor *= f
    return factor


def entitlement(trades, actions, ex_date, asof, current_quantity):
    if not actions.empty and not actions["validation_status"].eq("PASS").all():
        return None, "CORPORATE_ACTION_REVIEW_REQUIRED"
    if ex_date is None:
        return None, "EX_DATE_UNKNOWN"
    if ex_date > asof:
        return max(0.0, current_quantity), "FUTURE_EX_DATE_PROVISIONAL"
    if trades.empty:
        return None, "ENTITLEMENT_LEDGER_UNAVAILABLE"
    dates = trades["event_date"].map(day)
    prior = trades[dates.notna() & dates.lt(ex_date)]
    prior = prior[~prior["type_norm"].isin(["SPLIT", "REVERSE_SPLIT"])]
    if "historical_signed_quantity" not in trades and not actions.empty:
        return None, "CURRENT_UNIT_LEDGER_REQUIRED"
    col = "historical_signed_quantity" if "historical_signed_quantity" in trades else "signed_quantity"
    quantity = float(pd.to_numeric(prior[col], errors="coerce").sum()) / unit_factor(actions, ex_date)
    if quantity < -1e-6:
        return None, "NEGATIVE_ENTITLEMENT_REVIEW_REQUIRED"
    return max(0.0, quantity), "EX_DATE_PASSED_ENTITLEMENT_LOCKED"


def retention_ratio(actual):
    if actual.empty:
        return None
    # Exclude tax-only refunds; this is an estimate, never a tax-ledger change.
    gross = pd.to_numeric(actual["gross_dividend_eur"], errors="coerce")
    subset = actual[gross.gt(0)]
    if subset.empty:
        return None
    gross = float(subset["gross_dividend_eur"].sum())
    net = float(subset["net_dividend_eur"].sum())
    result = net / gross
    return result if 0 <= result <= 1 else None


def _reconcile(event, actual, used):
    """Only unique date AND gross-amount matches; never suppress on date alone."""
    amount = event["expected_gross_dividend_eur"]
    ex, pay = day(event["ex_dividend_date"]), day(event["payment_date"])
    if amount is None or amount <= 0 or actual.empty:
        return "NOT_MATCHED"
    matches, associations = [], []
    for idx, row in actual.iterrows():
        date, gross = day(row.get("payment_date")), number(row.get("gross_dividend_eur"))
        if idx in used or date is None:
            continue
        date_match = abs((date - pay).days) <= 10 if pay is not None else (ex is not None and 0 <= (date - ex).days <= 90)
        if ex is not None and date < ex:
            date_match = False
        distribution_mode = str(row.get("distribution_mode", "") or "").upper()
        reinvestment_base = number(row.get("broker_reinvestment_base_eur"))
        shares = number(row.get("shares"))
        eligible = event["entitlement_quantity"]
        if (
            date_match
            and distribution_mode == "REINVESTED"
            and reinvestment_base is not None
            and 0 < reinvestment_base <= amount + max(0.05, amount * 0.03)
            and shares is not None
            and eligible is not None
            and abs(abs(shares) - eligible) <= max(1e-6, eligible * 1e-5)
        ):
            associations.append(idx)
            continue
        if gross is None or gross <= 0:
            continue
        tolerance = max(0.05, amount * (0.03 if event["dividend_currency"] != "EUR" else 0.005))
        if date_match and abs(gross - amount) <= tolerance:
            matches.append(idx)
        elif date_match:
            # Some broker exports report a distribution after foreign source
            # withholding inside the amount field. Do not change its accounting
            # or pretend an exact gross match. A unique date/quantity association
            # goes to review and is excluded from future cash to prevent a likely
            # already-received event being counted twice.
            if (shares is not None and eligible is not None and abs(abs(shares) - eligible) <= max(1e-6, eligible * 1e-5)
                    and .5 * amount <= gross <= 1.1 * amount):
                associations.append(idx)
    if len(matches) == 1:
        used.add(matches[0])
        return "RECEIVED_RECONCILED"
    if not matches and len(associations) == 1:
        used.add(associations[0])
        if str(actual.loc[associations[0]].get("distribution_mode", "") or "").upper() == "REINVESTED":
            return "RECEIVED_REINVESTMENT_ASSOCIATED"
        return "RECEIPT_ASSOCIATION_REVIEW"
    return "AMBIGUOUS_RECEIPT_MATCH" if matches or associations else "NOT_MATCHED"


def _events(snapshot, asof):
    """Recent ex-date evidence plus calendar; no annual rate as declared DPS."""
    history = {iso(r.get("ex_date")): number(r.get("dps")) for r in snapshot.history if day(r.get("ex_date")) is not None}
    currencies = {iso(r.get("ex_date")): r.get("currency") or snapshot.currency for r in snapshot.history}
    cal = snapshot.calendar
    ex, pay = day(cal.get("Ex-Dividend Date")), day(cal.get("Dividend Date"))
    warning = ""
    if ex is not None and pay is not None and not 0 <= (pay - ex).days <= RECENT_DAYS:
        pay = None
        warning = "INCONSISTENT_CALENDAR_PAYMENT_DATE"
    recent = {d: a for d, a in history.items() if asof - pd.Timedelta(days=RECENT_DAYS) <= day(d) <= asof}
    events = []
    if ex is not None and asof - pd.Timedelta(days=RECENT_DAYS) <= ex <= asof + pd.DateOffset(years=1):
        dps = history.get(iso(ex))
        # Standard yfinance calendar supplies no declared amount. This field
        # permits another typed provider/fixture without misusing annual rates.
        explicit = number(cal.get("Declared Dividend Per Share"))
        events.append({"ex": ex, "pay": pay, "record": day(cal.get("Record Date")),
                       "dps": explicit if explicit is not None else dps,
                       "adjusted": explicit is None, "diagnostic": warning, "currency": currencies.get(iso(ex), snapshot.currency)})
        recent.pop(iso(ex), None)
    elif ex is None and pay is not None and asof - pd.Timedelta(days=10) <= pay <= asof + pd.DateOffset(years=1):
        events.append({"ex": None, "pay": pay, "record": None,
                       "dps": number(cal.get("Declared Dividend Per Share")), "adjusted": False,
                       "diagnostic": "EX_DATE_UNKNOWN", "currency": snapshot.currency})
    for date, dps in recent.items():
        events.append({"ex": day(date), "pay": None, "record": None, "dps": dps,
                       "adjusted": True, "diagnostic": "PAYMENT_DATE_UNAVAILABLE", "currency": currencies.get(date, snapshot.currency)})
    return events


def build_dividend_analytics(projection, combined, trades, dividends, actions, asof,
                             provider, candidate_resolver, fx_resolver,
                             growth_stats, enabled=True):
    """Return holding estimates, monthly/events/audit tables and strict totals.

    Active securities + positions with a trade in the past 180 days are queried.
    This bounded lookback covers recently closed earned entitlements, not an
    exhaustive corporate-action registry. No network call receives account data.
    """
    asof = day(asof)
    end = asof + pd.DateOffset(years=1)
    projection = projection.copy()
    events, forecast, audit, annual_rows = [], [], [], []
    new_rows = {}
    for holding in combined.to_dict("records"):
        isin = str(holding.get("isin", "")).strip()
        if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin):
            continue
        asset_class = holding.get("asset_class") or holding.get("asset_class_clean")
        if asset_class not in {"STOCK", "FUND"}:
            continue
        name = str(holding.get("security_name", ""))
        aliases = set(filter(None, str(holding.get("historical_isin_aliases") or "").split(";")))
        instrument_actions = _actions_for(actions, isin, aliases)
        for row in instrument_actions.to_dict("records"):
            for col in ("old_isin", "new_isin"):
                if row.get(col):
                    aliases.add(str(row[col]))
        instrument_trades = _security_rows(trades, isin, aliases)
        actual = _security_rows(dividends, isin, aliases)
        active = holding.get("position_status") == "ACTIVE"
        last_trade = max((d for d in instrument_trades.get("event_date", pd.Series(dtype=object)).map(day) if d is not None), default=None)
        if not active and (last_trade is None or last_trade < asof - pd.Timedelta(days=RECENT_DAYS)):
            continue
        quantity = number(holding.get("current_quantity")) or 0.0
        retention = retention_ratio(actual)
        snapshots = []
        resolver_error = ""
        if enabled:
            try:
                tickers = list(dict.fromkeys(str(t).strip().upper() for t in candidate_resolver(holding) if t))[:2]
            except Exception as exc:
                tickers, resolver_error = [], "IDENTITY_" + type(exc).__name__
            for ticker in tickers:
                try:
                    snapshot = provider.fetch(ticker)
                except Exception as exc:
                    snapshot = DividendSnapshot(ticker, history_status="FAILED", calendar_status="FAILED", diagnostic=type(exc).__name__)
                snapshots.append(snapshot)
                audit.append(dict(security_name=name, isin=isin, source_ticker=ticker,
                                  lookup_status=snapshot.history_status, history_events=len(snapshot.history),
                                  calendar_status=snapshot.calendar_status, dividend_currency=snapshot.currency,
                                  data_retrieved_at=snapshot.retrieved_at, diagnostic=snapshot.diagnostic))
        if not snapshots:
            audit.append(dict(security_name=name, isin=isin, source_ticker=None,
                              lookup_status="DISABLED" if not enabled else "IDENTITY_UNRESOLVED",
                              history_events=0, calendar_status="NOT_QUERIED", dividend_currency=None,
                              data_retrieved_at=None, diagnostic=resolver_error))

        # One history source, never add identical listing histories together.
        best = max(snapshots, key=lambda s: (max((r["ex_date"] for r in s.history), default=""), len(s.history)), default=None)
        history = [r for r in (best.history if best else []) if day(r["ex_date"]) <= asof
                   and (not r.get("currency") or r["currency"] == best.currency)]
        annual = {}
        for row in history:
            year = day(row["ex_date"]).year
            annual[year] = annual.get(year, 0.0) + row["dps"]
        annual_frame = pd.DataFrame([dict(security_name=name, isin=isin, yahoo_ticker=best.ticker,
                                         dividend_year=y, annual_dividend_per_share=a,
                                         dividend_currency=best.currency, source="Yahoo ex-date history", error="")
                                     for y, a in sorted(annual.items())])
        annual_rows.extend(annual_frame.to_dict("records"))
        stats = growth_stats(annual_frame) if not annual_frame.empty else growth_stats(None)
        growth = (number(stats.get("growth_clipped_pct")) or 0) / 100

        candidates = {}
        for snapshot in snapshots:
            for candidate in _events(snapshot, asof):
                key = iso(candidate["ex"]) or ("PAY:" + str(iso(candidate["pay"])))
                candidate.update(snapshot=snapshot)
                previous = candidates.get(key)
                if previous is None:
                    candidates[key] = candidate
                else:
                    # Complement missing dates only when the ex-date identifies
                    # the same event. Never combine two amount/currency bases.
                    if previous["dps"] is None and candidate["dps"] is not None:
                        candidate["pay"] = candidate["pay"] or previous["pay"]
                        candidates[key] = candidate
                    elif previous["pay"] is None:
                        previous["pay"] = candidate["pay"]
                    if previous["pay"] and candidate["pay"] and previous["pay"] != candidate["pay"]:
                        candidates[key]["pay"] = None
                        candidates[key]["diagnostic"] += ";CONFLICTING_PAYMENT_DATES"
                    if (previous["dps"] is not None and candidate["dps"] is not None
                            and previous["snapshot"].currency == snapshot.currency
                            and abs(previous["dps"] - candidate["dps"]) > max(0.01, previous["dps"] * 0.01)):
                        candidates[key]["dps"] = None
                        candidates[key]["diagnostic"] += ";CONFLICTING_DPS"

        used = set()
        security_events = []
        for key, candidate in sorted(candidates.items()):
            snapshot = candidate["snapshot"]
            ex, pay = candidate["ex"], candidate["pay"]
            qty, entitlement_status = entitlement(instrument_trades, instrument_actions, ex, asof, quantity)
            if qty is not None and qty <= 1e-9:
                continue  # investor not entitled, not an upcoming portfolio payment
            dps = candidate["dps"]
            if dps is not None and candidate["adjusted"]:
                dps *= unit_factor(instrument_actions, ex)
            currency = candidate["currency"]
            try:
                fx = number(fx_resolver(currency)) if currency and currency != "UNKNOWN" else None
            except Exception:
                fx = None
            eur_dps = dps * fx if dps is not None and fx is not None else None
            gross = eur_dps * qty if eur_dps is not None and qty is not None else None
            net = gross * retention if gross is not None and retention is not None else None
            diagnostic = ";".join(filter(None, [candidate["diagnostic"], snapshot.diagnostic,
                                     "PAYMENT_DATE_UNAVAILABLE" if pay is None else "",
                                     "NET_TAX_HISTORY_UNAVAILABLE" if retention is None else "",
                                     "DPS_OR_FX_UNAVAILABLE" if eur_dps is None else ""]))
            event = dict(security_name=name, isin=isin, historical_isin_aliases=";".join(sorted(aliases - {isin})),
                         current_quantity=quantity, event_id=isin + ":" + key,
                         event_status="EXTERNAL_DATE_ONLY" if dps is None else entitlement_status,
                         ex_dividend_date=iso(ex), record_date=iso(candidate["record"]), payment_date=iso(pay),
                         declared_dividend_per_share=dps, dividend_currency=currency, fx_to_eur=fx,
                         declared_dividend_per_share_eur=eur_dps, entitlement_quantity=qty,
                         entitlement_status=entitlement_status, expected_gross_dividend_eur=gross,
                         estimated_net_dividend_eur=net, net_method="OBSERVED_ISSUER_RETENTION" if retention is not None else "UNAVAILABLE",
                         source_provider="Yahoo/yfinance", source_ticker=snapshot.ticker,
                         data_retrieved_at=snapshot.retrieved_at,
                         confidence="EXTERNAL_DATES_AND_AMOUNT" if pay is not None and eur_dps is not None else "PARTIAL_EXTERNAL_EVIDENCE",
                         diagnostic=diagnostic, reconciliation_status="NOT_MATCHED", forecast_inclusion="UNSCHEDULED")
            event["reconciliation_status"] = _reconcile(event, actual, used)
            if event["reconciliation_status"] in {"RECEIVED_RECONCILED", "RECEIVED_REINVESTMENT_ASSOCIATED"}:
                event["event_status"] = event["reconciliation_status"]
                event["forecast_inclusion"] = "EXCLUDED_RECEIVED"
            elif event["reconciliation_status"] in {"AMBIGUOUS_RECEIPT_MATCH", "RECEIPT_ASSOCIATION_REVIEW"}:
                event["event_status"] = "RECEIPT_RECONCILIATION_REVIEW"
                event["forecast_inclusion"] = "EXCLUDED_AMBIGUOUS"
                event["diagnostic"] += ";BROKER_RECEIPT_DATE_QUANTITY_MATCH_AMOUNT_BASIS_REQUIRES_REVIEW"
            elif pay is not None and asof <= pay <= end:
                event["forecast_inclusion"] = "DECLARED_PAYMENT_WINDOW"
                forecast.append(dict(security_name=name, isin=isin, event_id=event["event_id"],
                                     category="DECLARED", ex_dividend_date=iso(ex), expected_payment_date=iso(pay),
                                     gross_dividend_eur=gross, estimated_net_dividend_eur=net,
                                     source_ticker=snapshot.ticker, method="EXTERNAL_PAYMENT_DATE", confidence=event["confidence"]))
            elif pay is not None and pay < asof:
                event["event_status"] = "PAYMENT_DATE_PASSED_UNRECONCILED"
                event["forecast_inclusion"] = "EXCLUDED_PAST_DUE"
            elif pay is None and ex is not None and (asof - ex).days > 90:
                event["event_status"] = "HISTORICAL_EVENT_UNRECONCILED"
                event["forecast_inclusion"] = "EXCLUDED_STALE_UNSCHEDULED"
            security_events.append(event)
        events.extend(security_events)

        # Rolling 12-month ex-date seasonality. Infer payment lag only from a
        # matched calendar or uniquely matched broker receipt; otherwise label
        # the ex-date timing proxy (not a promised payment date).
        if active:
            trailing = [r for r in history if asof - pd.DateOffset(years=1) < day(r["ex_date"]) <= asof]
            for row in trailing:
                original_ex = day(row["ex_date"])
                projected_ex = original_ex + pd.DateOffset(years=1)
                lag = None
                # Prior matched cycle is the strongest available seasonal lag.
                same = [e for e in security_events if e["ex_dividend_date"] == iso(original_ex) and e["payment_date"]]
                if same:
                    lag = (day(same[0]["payment_date"]) - original_ex).days
                if lag is None:
                    relevant = [e for e in security_events if e["payment_date"] and e["ex_dividend_date"]]
                    if relevant:
                        lag = int(pd.Series([(day(e["payment_date"]) - day(e["ex_dividend_date"])).days for e in relevant]).median())
                payment = projected_ex + pd.Timedelta(days=lag or 0)
                # Same expected payout cycle, tolerance scales with frequency.
                tolerance = min(45, max(7, int(365 / max(1, len(trailing)) / 3)))
                matched = [e for e in security_events
                           if (e["ex_dividend_date"] and abs((day(e["ex_dividend_date"]) - projected_ex).days) <= tolerance)
                           or (not e["ex_dividend_date"] and e["payment_date"] and abs((day(e["payment_date"]) - payment).days) <= tolerance)]
                if matched or not asof <= payment <= end:
                    continue
                try:
                    fx = number(fx_resolver(best.currency)) if best.currency and best.currency != "UNKNOWN" else None
                except Exception:
                    fx = None
                gross = row["dps"] * quantity * fx * (1 + growth) if fx is not None else None
                forecast.append(dict(security_name=name, isin=isin, event_id=f"{isin}:EST:{iso(projected_ex)}",
                                     category="ESTIMATED", ex_dividend_date=iso(projected_ex), expected_payment_date=iso(payment),
                                     gross_dividend_eur=gross, estimated_net_dividend_eur=gross * retention if gross is not None and retention is not None else None,
                                     source_ticker=best.ticker, method="EXTERNAL_HISTORY_PAYMENT_LAG_ESTIMATE" if lag is not None else "EXTERNAL_HISTORY_EX_DATE_TIMING_PROXY",
                                     confidence="ESTIMATE_NOT_DECLARED"))
            if not trailing and not actual.empty:
                # Broker-history fallback remains an estimate, never a declared
                # event. Preserve actual amounts; no current-quantity inference.
                for i, received in actual.iterrows():
                    date = day(received.get("payment_date"))
                    gross = number(received.get("gross_dividend_eur"))
                    if date is None or gross is None or gross <= 0:
                        continue
                    date += pd.DateOffset(years=1)
                    if not asof <= date <= end:
                        continue
                    if any((e["payment_date"] or e["ex_dividend_date"]) and abs((day(e["payment_date"] or e["ex_dividend_date"]) - date).days) <= 45 for e in security_events):
                        continue
                    forecast.append(dict(security_name=name, isin=isin, event_id=f"{isin}:BROKER_EST:{iso(date)}:{i}",
                                         category="ESTIMATED", ex_dividend_date=None, expected_payment_date=iso(date), gross_dividend_eur=gross,
                                         estimated_net_dividend_eur=number(received.get("net_dividend_eur")), source_ticker=None,
                                         method="BROKER_RECEIPT_SEASONALITY_ESTIMATE", confidence="LOW_NO_EXTERNAL_HISTORY"))
        pending = [e for e in security_events if not e["forecast_inclusion"].startswith("EXCLUDED")]
        status = ("DECLARED_UPCOMING_FOUND" if pending else "EXTERNAL_HISTORY_FOUND" if history
                  else "EXTERNAL_LOOKUP_FAILED" if not snapshots or any(s.history_status != "OK" or s.calendar_status == "FAILED" for s in snapshots)
                  else "NO_EXTERNAL_DIVIDEND_EVIDENCE")
        if not enabled:
            status = "EXTERNAL_LOOKUP_DISABLED"
        elif status == "NO_EXTERNAL_DIVIDEND_EVIDENCE" and asset_class == "FUND" and re.search(r"\b(acc|accumulating|thesaurierend)\b", name, re.I):
            status = "APPARENTLY_ACCUMULATING_NAME_AND_NO_HISTORY"
        rows = [r for r in forecast if r["isin"] == isin]
        gross, net = complete_sum(r["gross_dividend_eur"] for r in rows), complete_sum(r["estimated_net_dividend_eur"] for r in rows)
        if not rows and (status in {"EXTERNAL_LOOKUP_FAILED", "EXTERNAL_LOOKUP_DISABLED"} or pending):
            gross = net = None
        if pending and any(e["payment_date"] is None for e in pending):
            # Do not pretend that the scheduled window covers unscheduled cash.
            status += "_UNSCHEDULED_PAYMENT"
        new_rows[isin] = {**stats, "external_dividend_status": status,
                          "external_dividend_ticker_used": best.ticker if best else None,
                          "external_dividend_currency": best.currency if best else None,
                          "net_retention_ratio": retention, "projection_base_used": "EVENT_AWARE_DECLARED_PLUS_ESTIMATED",
                          "growth_projection_method": "EVENT_AWARE_DECLARED_REPLACES_ESTIMATE",
                          "growth_adjusted_forward_12m_gross_dividend_eur": gross,
                          "growth_adjusted_forward_12m_net_dividend_eur": net,
                          "growth_adjusted_estimated_monthly_net_dividend_eur": net / 12 if net is not None else None,
                          "growth_adjusted_net_yoc_acquisition_basis_pct": ratio(net, holding.get("remaining_acquisition_cost_basis_eur")),
                          "growth_adjusted_net_yoc_user_funded_basis_pct": ratio(net, holding.get("remaining_user_funded_basis_eur")),
                          "growth_adjusted_net_current_yield_pct": ratio(net, holding.get("live_current_value_eur")),
                          "forward_gross_yoc_acquisition_basis_pct": ratio(gross, holding.get("remaining_acquisition_cost_basis_eur")),
                          "projection_warning": status + (";NET_ESTIMATE_UNAVAILABLE" if retention is None else "")}

    # Stable schema even for an empty portfolio or unresolved security identity.
    required = {"growth_adjusted_forward_12m_gross_dividend_eur", "growth_adjusted_forward_12m_net_dividend_eur",
                "growth_adjusted_net_yoc_acquisition_basis_pct", "growth_adjusted_net_yoc_user_funded_basis_pct",
                "growth_adjusted_net_current_yield_pct", "growth_adjusted_estimated_monthly_net_dividend_eur",
                "external_dividend_history_years", "external_dividend_status"}
    for key in required | {key for row in new_rows.values() for key in row}:
        projection[key] = projection["isin"].map({isin: row.get(key) for isin, row in new_rows.items()})
    # Include first/last partial months of the exact rolling 12-month window.
    monthly = []
    for month in pd.period_range(asof, end, freq="M"):
        rows = [r for r in forecast if r["expected_payment_date"].startswith(str(month))]
        declared = [r for r in rows if r["category"] == "DECLARED"]
        estimated = [r for r in rows if r["category"] == "ESTIMATED"]
        coverage = summarize_net_forecast_coverage(rows)
        monthly.append(dict(projection_year_month=str(month),
                            projected_gross_dividend_eur=complete_sum(r["gross_dividend_eur"] for r in rows),
                            projected_net_dividend_eur=complete_sum(r["estimated_net_dividend_eur"] for r in rows),
                            growth_adjusted_projected_gross_dividend_eur=complete_sum(r["gross_dividend_eur"] for r in rows),
                            growth_adjusted_projected_net_dividend_eur=complete_sum(r["estimated_net_dividend_eur"] for r in rows),
                            declared_gross_eur=complete_sum(r["gross_dividend_eur"] for r in declared),
                            estimated_gross_eur=complete_sum(r["gross_dividend_eur"] for r in estimated),
                            unknown_net_events=sum(r["estimated_net_dividend_eur"] is None for r in rows),
                            estimated_net_known_subtotal_eur=coverage["forward_12m_estimated_net_known_subtotal_eur"],
                            gross_awaiting_net_estimation_eur=coverage["forward_12m_gross_awaiting_net_estimation_eur"],
                            quantified_forecast_event_count=coverage["forward_12m_relevant_gross_event_count"],
                            net_estimable_event_count=coverage["forward_12m_net_estimable_event_count"],
                            net_unresolved_event_count=coverage["forward_12m_net_unresolved_event_count"],
                            net_coverage_event_pct=coverage["forward_12m_net_coverage_event_pct"],
                            net_coverage_gross_pct=coverage["forward_12m_net_coverage_gross_pct"],
                            net_forecast_completeness=coverage["forward_12m_net_forecast_completeness"],
                            projection_method="DECLARED_PAYMENT_MONTH_PLUS_UNDECLARED_SEASONAL_ESTIMATES"))
    pending = [e for e in events if not e["forecast_inclusion"].startswith("EXCLUDED")]
    locked = [e for e in pending if e["entitlement_status"] == "EX_DATE_PASSED_ENTITLEMENT_LOCKED"]
    scheduled = [e for e in locked if e["payment_date"] and day(e["payment_date"]) >= asof]
    coverage = summarize_net_forecast_coverage(forecast)
    metrics = dict(asof=iso(asof), forecast_end=iso(end),
                   pending_event_count=len(pending), pending_security_count=len({e["isin"] for e in pending}),
                   locked_known_gross_pending_eur=sum(e["expected_gross_dividend_eur"] for e in locked if e["expected_gross_dividend_eur"] is not None),
                   pending_unknown_amount_events=sum(e["expected_gross_dividend_eur"] is None for e in pending),
                   pending_unknown_payment_date_events=sum(e["payment_date"] is None for e in pending),
                   next_scheduled_payment_date=min((e["payment_date"] for e in scheduled), default=None),
                   forward_scheduled_gross_eur=complete_sum(r["gross_dividend_eur"] for r in forecast),
                   forward_scheduled_net_estimate_eur=complete_sum(r["estimated_net_dividend_eur"] for r in forecast),
                   forward_unknown_net_events=sum(r["estimated_net_dividend_eur"] is None for r in forecast),
                   forward_known_gross_subtotal_eur=sum(r["gross_dividend_eur"] for r in forecast if r["gross_dividend_eur"] is not None),
                   forward_known_net_estimate_subtotal_eur=sum(r["estimated_net_dividend_eur"] for r in forecast if r["estimated_net_dividend_eur"] is not None),
                   receipt_association_review_events=sum(e["reconciliation_status"] in {"RECEIPT_ASSOCIATION_REVIEW", "AMBIGUOUS_RECEIPT_MATCH"} for e in events),
                   lookup_failed_securities=sum("FAILED" in row.get("external_dividend_status", "") for row in new_rows.values()),
                   recent_closed_discovery_days=RECENT_DAYS)
    metrics.update(coverage)
    for days in (30, 90):
        rows = [e for e in scheduled if day(e["payment_date"]) <= asof + pd.Timedelta(days=days)]
        metrics[f"locked_declared_known_gross_next_{days}d_eur"] = sum(e["expected_gross_dividend_eur"] for e in rows if e["expected_gross_dividend_eur"] is not None)
    return dict(projection=projection, monthly=pd.DataFrame(monthly),
                upcoming=pd.DataFrame(events, columns=EVENT_COLUMNS), forecast=pd.DataFrame(forecast, columns=FORECAST_COLUMNS),
                audit=pd.DataFrame(audit, columns=AUDIT_COLUMNS),
                history=pd.DataFrame(annual_rows, columns=["security_name", "isin", "yahoo_ticker", "dividend_year", "annual_dividend_per_share", "dividend_currency", "source", "error"]), metrics=metrics)
