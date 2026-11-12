"""Synthetic-only validation of non-cash stock/fund corporate-action lineage.

The fixture names, identifiers, transactions, and market prices are fabricated.
The customer's CSV and any private output must never enter GitHub.
"""
import csv
from io import StringIO
from pathlib import Path
import sys

import pytest
import pandas as pd

from pulse.synthetic import COLUMNS, row
from pulse.runner import run_analysis
from pulse.adapter import prepare


OLD = "ZZ0000000011"
MID = "ZZ0000000022"
NEW = "ZZ0000000033"


def transaction(day, kind, isin, qty, *, time="12:00:00", name="Fictional Issuer",
                fee="", amount="", category=None, asset="STOCK"):
    item = row(day, kind, shares=qty, fee=fee, amount=amount, asset=asset,
               category=category or ("TRADING" if kind in {"BUY", "SELL"} else "CORPORATE_ACTION"))
    item.update(datetime=f"{day}T{time}Z", symbol=isin, name=name)
    return item


def sequence():
    return [
        transaction("2024-03-15", "BUY", OLD, 5, amount=-1.98, fee=-1),
        transaction("2024-09-19", "REVERSE_SPLIT", OLD, -5),
        transaction("2024-09-19", "REVERSE_SPLIT", MID, .131579),
        transaction("2026-10-06", "EXCHANGE", MID, -.131579),
        transaction("2026-10-06", "EXCHANGE", NEW, .131579, name="Fictional Successor"),
    ]


def csv_bytes(rows):
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=COLUMNS)
    writer.writeheader()
    for index, item in enumerate(rows):
        candidate = dict(item)
        candidate["transaction_id"] = f"synthetic-exchange-{index}"
        writer.writerow(candidate)
    return output.getvalue().encode("utf-8")


def prices(*, missing=False):
    return {
        "asof": "2026-10-12",
        "securities": {NEW: {"start": 20, "end": 20, "missing": missing}},
        "benchmark": {"start": 100, "end": 109},
        "benchmarks": {"IWDA.AS": {"start": 100, "end": 109}},
    }


def execute(rows, *, missing=False):
    # A non-security dated cash movement sets the synthetic source cutoff.
    rows = [*rows, transaction("2026-10-12", "CUSTOMER_INBOUND", "", "", asset="",
                              amount=1, category="CASH")]
    return run_analysis(csv_bytes(rows), prices=prices(missing=missing))


def test_reverse_split_followed_by_exchange_conserves_lots_and_cash():
    result = execute(sequence())
    assert result["accounting_status"] == "COMPLETE"
    actions = result["corporate_action_audit"]
    assert len(actions) == 2
    assert all(action["validation_status"] == "PASS" for action in actions)
    assert all(action["investment_cash_flow_caused_eur"] == 0 for action in actions)
    assert all(action["realized_pl_caused_eur"] == 0 for action in actions)
    for action in actions:
        assert action["acquisition_basis_after_eur"] == pytest.approx(
            action["acquisition_basis_before_eur"]
        )
        assert action["user_funded_basis_after_eur"] == pytest.approx(
            action["user_funded_basis_before_eur"]
        )
    active = [x for x in result["holdings"] if x["position_status"] == "ACTIVE"]
    assert len(active) == 1
    assert active[0]["isin"] == NEW
    assert active[0]["current_quantity"] == pytest.approx(.131579)
    assert active[0]["remaining_acquisition_cost_basis_eur"] == pytest.approx(2.98)
    # An ordinary user-funded purchase includes its transaction fee in both
    # bases; there is no promotional funding in this fixture.
    assert active[0]["remaining_user_funded_basis_eur"] == pytest.approx(2.98)
    assert all(x["isin"] != MID and x["isin"] != OLD for x in active)
    assert result["lifetime_metrics"]["lifetime_stock_realized_pl_eur"] == 0


def test_conversion_does_not_create_additional_cash_flow_or_profit():
    result = execute(sequence())
    # Equivalent direct purchase of current units must have identical lifetime basis,
    # capital and recovery, even though historical quote provenance may differ.
    direct = transaction("2024-03-15", "BUY", NEW, .131579,
                         amount=-1.98, fee=-1, name="Fictional Successor")
    baseline = execute([direct])
    assert result["accounting_status"] == baseline["accounting_status"] == "COMPLETE"
    for field in ("lifetime_gross_investment_outflows_eur",
                  "lifetime_user_funded_investment_outflows_eur",
                  "lifetime_ecosystem_recovery_eur",
                  "lifetime_economic_profit_eur"):
        assert result["lifetime_metrics"][field] == pytest.approx(
            baseline["lifetime_metrics"][field]
        )


@pytest.mark.parametrize("defect", ["missing", "cash", "fee", "quantity",
                                     "partial", "asset", "time", "category",
                                     "tax", "original_amount", "account", "date", "ambiguous"])
def test_unsupported_or_ambiguous_exchange_remains_blocking(defect):
    rows = sequence()
    if defect == "missing":
        rows.pop()
    elif defect == "cash":
        rows[-1]["amount"] = "0.01"
    elif defect == "fee":
        rows[-1]["fee"] = "-0.01"
    elif defect == "quantity":
        rows[-1]["shares"] = ".20"
    elif defect == "partial":
        rows[-2]["shares"] = "-.10"
        rows[-1]["shares"] = ".10"
    elif defect == "asset":
        rows[-1]["asset_class"] = "FUND"
    elif defect == "time":
        rows[-1]["datetime"] = "2026-10-06T13:00:00Z"
    elif defect == "category":
        rows[-1]["category"] = "DELIVERY"
    elif defect in {"tax", "original_amount"}:
        rows[-1][defect] = ".01"
    elif defect == "account":
        rows[-1]["account_type"] = "OTHER"
    elif defect == "date":
        rows[-1]["date"] = "2026-10-05"
    elif defect == "ambiguous":
        rows.append(transaction("2026-10-06", "EXCHANGE", "ZZ0000000044", .131579))
    result = execute(rows)
    assert result["accounting_status"] == "BLOCKING"
    assert any(x["validation_status"] == "BLOCKING" for x in result["corporate_action_audit"])
    model = prepare(result, analysis_scope="stocks_funds")
    assert model["by_key"]["capital"].value is None
    assert model["by_key"]["recovery"].value is None


def test_missing_successor_quote_is_not_substituted_with_zero():
    result = execute(sequence(), missing=True)
    assert result["accounting_status"] == "COMPLETE"
    model = prepare(result, analysis_scope="stocks_funds")
    assert model["by_key"]["value"].value is None
    assert model["by_key"]["profit"].value is None


@pytest.fixture
def engine(monkeypatch, tmp_path):
    """Load retained definitions only, with all paths confined to pytest temp."""
    from pulse import core  # Register the selected pure canonical module.
    path = Path(__file__).parents[1] / "vendor/v678/analysis_engine.py"
    monkeypatch.setattr(sys, "argv", [str(path), "--input-csv", str(tmp_path / "input.csv"),
        "--output-root", str(tmp_path / "output"), "--manifest", str(tmp_path / "manifest.json"),
        "--no-enable-dividend-growth", "--no-enable-derivative-quotes"])
    source = path.read_text(encoding="utf-8")
    boundary = source.index('from worker_hooks import install_hooks')
    ns = {"__name__": "__main__", "__file__": str(path)}
    exec(compile(source[:boundary], str(path), "exec"), ns)
    return ns


def ledger(ns, rows):
    frame = ns["classify_for_diagnostics"](ns["normalize_raw_dataframe"](pd.DataFrame(rows)))
    return ns["build_trade_ledger"](frame)[0]


def normalized_fifo(ns, rows):
    trades, audit, consumed = ns["normalize_corporate_actions"](ledger(ns, rows))
    holdings, realized, lots, _, audit = ns["fifo_realized_and_positions"](trades, audit)
    return trades, audit, holdings, realized, lots


def test_distinct_bases_original_dates_and_source_lots_preserved(engine):
    trades = ledger(engine, sequence())
    trades.loc[trades.type_norm.eq("BUY"), "user_funded_cost_basis_eur"] = 1.98
    source_id = trades.loc[trades.type_norm.eq("BUY"), "trade_id"].iloc[0]
    trades, audit, consumed = engine["normalize_corporate_actions"](trades)
    holdings, realized, lots, _, audit = engine["fifo_realized_and_positions"](trades, audit)
    assert len(consumed) == 4 and audit.validation_status.eq("PASS").all()
    assert lots.iloc[0].source_trade_id == source_id
    assert lots.iloc[0].buy_date == pd.Timestamp("2024-03-15")
    assert lots.iloc[0].cost_basis_remaining_eur == pytest.approx(2.98)
    assert lots.iloc[0].user_basis_remaining_eur == pytest.approx(1.98)
    assert realized.empty


@pytest.mark.parametrize("same_timestamp", [False, True])
def test_consecutive_actions_and_metadata_use_broker_chronology(engine, same_timestamp):
    rows = sequence()
    if same_timestamp:
        for item in rows[3:]:
            item.update(date="2024-09-19", datetime="2024-09-19T12:00:00Z")
    rows += [transaction("2026-10-07", "EXCHANGE", NEW, 0, name="Renamed Successor"),
             transaction("2026-10-08", "SPLIT", NEW, .131579),
             transaction("2026-10-09", "EXCHANGE", NEW, -.263158),
             transaction("2026-10-09", "EXCHANGE", "ZZ0000000044", .263158, name="Final Successor")]
    trades, audit, h, realized, lots = normalized_fifo(engine, rows)
    assert audit.validation_status.eq("PASS").all() and len(audit) == 5
    assert h.iloc[0]["isin"] == "ZZ0000000044" and h.iloc[0].security_name == "Final Successor"
    assert h.iloc[0].current_quantity == pytest.approx(.263158)
    assert h.iloc[0].remaining_acquisition_cost_basis_eur == pytest.approx(2.98)
    assert realized.empty


def test_same_day_purchase_after_split_not_scaled_again(engine):
    rows = sequence()[:3] + [transaction("2024-09-19", "BUY", MID, 1,
        time="14:00:00", amount=-10)]
    trades, audit, _, _, _ = normalized_fifo(engine, rows)
    scaled = engine["annotate_historical_scales"](trades, audit)
    assert scaled.loc[scaled.event_datetime.eq(pd.Timestamp("2024-09-19 14:00")), "historical_signed_quantity"].iloc[0] == 1


def test_paired_forward_split_accepts_destination_first(engine):
    rows = sequence()[:3]
    rows[1]["type"] = rows[2]["type"] = "SPLIT"
    rows[2]["shares"] = 10
    trades, audit, h, realized, _ = normalized_fifo(engine, [rows[0], rows[2], rows[1]])
    assert audit.validation_status.eq("PASS").all()
    assert h.iloc[0].current_quantity == 10
    assert h.iloc[0].remaining_acquisition_cost_basis_eur == pytest.approx(2.98)
    assert realized.empty


@pytest.mark.parametrize("quantity", ["", None])
def test_missing_quantity_is_not_a_zero_quantity_metadata_event(engine, quantity):
    rows = sequence() + [transaction("2026-10-07", "EXCHANGE", NEW, quantity)]
    _, audit, _, _, _ = normalized_fifo(engine, rows)
    assert audit.iloc[-1].validation_status == "BLOCKING"


@pytest.mark.parametrize("successor_history", [True, False])
def test_predecessor_history_stays_within_verified_same_unit_interval(engine, successor_history):
    rows = sequence()
    frame = engine["classify_for_diagnostics"](engine["normalize_raw_dataframe"](pd.DataFrame(rows)))
    trades, audit, h, _, _ = normalized_fifo(engine, rows)
    trades = engine["annotate_historical_scales"](trades, audit)
    h = h.assign(yahoo_ticker="NEW", live_price_currency="EUR", live_price_eur=20.,
        live_price_date="2026-10-12", live_current_value_eur=.131579*20)
    requests = []
    def resolve(isin, name="", asset=""):
        assert isin == MID  # Never use pre-reverse-split prices in current units.
        return dict(ticker="MID", matched_currency="EUR")
    def history(ticker, start, end):
        requests.append((ticker, pd.Timestamp(start), pd.Timestamp(end)))
        days = pd.date_range(start, end, freq="B")
        if ticker == "NEW":
            days = days[days >= "2026-10-06"] if successor_history else days[:0]
        return pd.DataFrame({"close_native":20., "adjusted_close_native":20.}, index=days), "EUR", "OK", ""
    engine["find_best_yahoo_ticker"] = resolve
    engine["_fetch_yahoo_history"] = history
    empty = pd.DataFrame()
    result = engine["build_historical_analytics"](frame, trades, empty, empty, h, h,
        empty, empty, {}, pd.Timestamp("2026-10-12"), "BENCHMARK", "Fabricated benchmark")
    assert ("MID", pd.Timestamp("2024-09-19"), pd.Timestamp("2026-10-06")) in requests
    assert "ONE_FOR_ONE_UNITS" in result["historical_ticker_audit"].iloc[0].predecessor_history_provenance
    nav = result["daily_nav"].set_index("date")
    assert nav.loc[pd.Timestamp("2026-10-05"), "external_market_price_coverage_pct"] == 100
    assert nav.loc[pd.Timestamp("2026-10-06"), "external_market_price_coverage_pct"] == (100 if successor_history else 0)


def test_conversion_is_neutral_for_all_history_and_benchmark_flows():
    rows = sequence()
    # Start within the existing synthetic provider's explicitly observed range.
    rows[0].update(date="2025-01-06", datetime="2025-01-06T12:00:00Z")
    for item in rows[1:3]:
        item.update(date="2025-02-03", datetime="2025-02-03T12:00:00Z")
    converted = execute(rows)
    direct = execute([transaction("2025-01-06", "BUY", NEW, .131579, amount=-1.98, fee=-1, name="Fictional Successor")])
    for field in ("lifetime_metrics", "historical_metrics", "daily_nav_history", "period_performance"):
        assert converted[field] == direct[field], field
    # Retain the canonical event-count convention: non-cash action ledger
    # events change average fee per recorded investment event, not returns.
    for key, value in converted["advanced_metrics"].items():
        if key != "average_fee_per_investment_trade_eur":
            assert value == direct["advanced_metrics"][key], key
    assert converted["advanced_metrics"]["average_fee_per_investment_trade_eur"] == pytest.approx(1 / 3)
    assert direct["advanced_metrics"]["average_fee_per_investment_trade_eur"] == 1
    nav = pd.DataFrame(converted["daily_nav_history"]).set_index("date")
    for day in ("2025-02-03T00:00:00.000", "2026-10-06T00:00:00.000"):
        assert nav.loc[day, "external_flow_to_stockfund_eur"] == 0
        assert nav.loc[day, "daily_twr_return_pct"] == pytest.approx(0, abs=1e-8)
    # Audit identities/names may differ; economic cash amounts and dates cannot.
    flows = lambda result: [{k:v for k,v in r.items() if k not in {"isin", "security_name"}} for r in result["lifetime_cashflow_ledger"]]
    assert flows(converted) == flows(direct)
