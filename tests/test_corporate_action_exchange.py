"""Synthetic-only validation of non-cash stock/fund corporate-action lineage.

The fixture names, identifiers, transactions, and market prices are fabricated.
The customer's CSV and any private output must never enter GitHub.
"""
import csv
from io import StringIO

import pytest

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
    assert active[0]["remaining_user_funded_basis_eur"] == pytest.approx(1.98)
    assert all(x["isin"] != MID and x["isin"] != OLD for x in active)
    assert not result["stock_fund_realized_trades"] if "stock_fund_realized_trades" in result else True


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
                  "lifetime_cash_recovered_eur",
                  "lifetime_economic_profit_eur"):
        if field in result["lifetime_metrics"] and field in baseline["lifetime_metrics"]:
            assert result["lifetime_metrics"][field] == pytest.approx(
                baseline["lifetime_metrics"][field]
            )


@pytest.mark.parametrize("defect", ["missing", "cash", "fee", "quantity",
                                     "partial", "asset", "time", "category"])
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
