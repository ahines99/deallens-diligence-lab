"""Offline tests for G20 insider-pattern analytics (clusters / 10b5-1 plans / role split).

These exercise the pure analytics on synthetic parsed Form 4 transactions (mirroring how
test_signals.py fabricates SEC data) and the additive Form 4 XML parsing of the 10b5-1 flag and
reporting-owner roles. No live SEC access — the one integration test monkeypatches the feed.
"""
from __future__ import annotations

from types import SimpleNamespace

from src.services import sec_feeds_service


def _tx(
    date: str,
    type_: str,
    *,
    shares: float | None = 100.0,
    value: float | None = 1000.0,
    insider: str = "Insider A",
    plan: bool | None = None,
    officer: bool = False,
    director: bool = False,
    ten: bool = False,
) -> dict:
    return {
        "date": date,
        "type": type_,
        "shares": shares,
        "value": value,
        "insider": insider,
        "plan_10b5_1": plan,
        "is_officer": officer,
        "is_director": director,
        "is_ten_percent_owner": ten,
    }


# --- (a) clustering ---------------------------------------------------------
def test_adjacent_same_direction_trades_form_one_cluster():
    txns = [
        _tx("2026-01-01", "buy", shares=100, value=1000, insider="A"),
        _tx("2026-01-03", "buy", shares=50, value=500, insider="B"),
        _tx("2026-01-06", "buy", shares=25, value=250, insider="A"),
    ]
    clusters = sec_feeds_service.analyze_insider_patterns(txns)["clusters"]
    assert len(clusters) == 1
    c = clusters[0]
    assert c["direction"] == "buy"
    assert (c["start"], c["end"]) == ("2026-01-01", "2026-01-06")
    assert c["transactions"] == 3
    assert c["participants"] == 2  # distinct insiders A, B
    assert c["total_shares"] == 175
    assert c["total_value"] == 1750.0


def test_gap_over_threshold_splits_clusters():
    txns = [
        _tx("2026-01-01", "sell", insider="A"),
        _tx("2026-01-05", "sell", insider="A"),      # 4-day gap -> same window
        _tx("2026-01-20", "sell", insider="B"),      # 15-day gap -> new window
    ]
    clusters = sec_feeds_service.analyze_insider_patterns(txns)["clusters"]
    assert len(clusters) == 2
    assert (clusters[0]["start"], clusters[0]["end"]) == ("2026-01-01", "2026-01-05")
    assert clusters[0]["transactions"] == 2
    assert (clusters[1]["start"], clusters[1]["end"]) == ("2026-01-20", "2026-01-20")
    assert clusters[1]["transactions"] == 1


def test_buys_and_sells_never_merge_into_one_window():
    txns = [_tx("2026-01-01", "buy"), _tx("2026-01-02", "sell")]
    clusters = sec_feeds_service.analyze_insider_patterns(txns)["clusters"]
    assert {c["direction"] for c in clusters} == {"buy", "sell"}
    assert len(clusters) == 2


# --- (b) 10b5-1 plan classification ----------------------------------------
def test_plan_summary_counts_planned_discretionary_and_unknown():
    txns = [
        _tx("2026-01-01", "sell", plan=True),
        _tx("2026-01-02", "sell", plan=True),
        _tx("2026-01-03", "sell", plan=False),
        _tx("2026-01-04", "buy", plan=None),
    ]
    summary = sec_feeds_service.analyze_insider_patterns(txns)["plan_summary"]
    assert summary == {"planned": 2, "discretionary": 1, "unknown": 1}


def test_missing_plan_flag_is_unknown_never_discretionary():
    # A transaction dict lacking the plan_10b5_1 key entirely must not be counted discretionary.
    txns = [{"date": "2026-01-01", "type": "buy", "insider": "A"}]
    summary = sec_feeds_service.analyze_insider_patterns(txns)["plan_summary"]
    assert summary == {"planned": 0, "discretionary": 0, "unknown": 1}


# --- (c) officer-vs-director split -----------------------------------------
def test_role_split_sums_buys_and_sells_by_role():
    txns = [
        _tx("2026-01-01", "buy", officer=True),
        _tx("2026-01-02", "sell", officer=True),
        _tx("2026-01-03", "sell", director=True),
        _tx("2026-01-04", "buy", ten=True),
        _tx("2026-01-05", "buy", officer=True, director=True),  # counts under both roles
    ]
    split = sec_feeds_service.analyze_insider_patterns(txns)["role_split"]
    assert split["officer"] == {"buys": 2, "sells": 1}
    assert split["director"] == {"buys": 1, "sells": 1}
    assert split["ten_percent_owner"] == {"buys": 1, "sells": 0}


# --- (d) unavailable feed is flagged, never a false-clean result ------------
def test_unavailable_feed_is_unavailable_not_clean_zero(monkeypatch):
    from src.services import edgar_client

    target = SimpleNamespace(cik="0000789019", ticker="MSFT", name="MICROSOFT CORP")
    monkeypatch.setattr(sec_feeds_service, "get_target", lambda s, w: target)

    def unavailable(*_args, **_kwargs):
        raise edgar_client.EdgarError("offline")

    monkeypatch.setattr(edgar_client, "get_submissions", unavailable)
    res = sec_feeds_service.insider_patterns(object(), "ws1")
    assert res["source_status"] == "unavailable"
    assert res["source_error"]
    assert res["clusters"] == []
    # Zeros are only acceptable alongside the explicit outage status, not as a clean signal.
    assert res["plan_summary"] == {"planned": 0, "discretionary": 0, "unknown": 0}


# --- additive Form 4 parsing: 10b5-1 provenance + structured roles ----------
def _form4(owner_rel: str, tx_body: str, footnotes: str = "", doc_flag: str = "") -> bytes:
    return (
        "<ownershipDocument>"
        + doc_flag
        + "<reportingOwner><reportingOwnerId><rptOwnerName>Jane Insider</rptOwnerName>"
        "</reportingOwnerId><reportingOwnerRelationship>" + owner_rel
        + "</reportingOwnerRelationship></reportingOwner>"
        + tx_body
        + footnotes
        + "</ownershipDocument>"
    ).encode()


_TX = (
    "<nonDerivativeTransaction>"
    "<transactionDate><value>2026-01-10</value></transactionDate>"
    "<transactionCoding><transactionCode>S</transactionCode></transactionCoding>"
    "<transactionAmounts>"
    "<transactionShares><value>100</value></transactionShares>"
    "<transactionPricePerShare><value>5</value></transactionPricePerShare>"
    "<transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>"
    "</transactionAmounts>"
    '<footnoteId id="F1"/>'
    "</nonDerivativeTransaction>"
)


def test_parse_form4_flags_10b5_1_from_footnote_and_reads_roles():
    xml = _form4(
        "<isOfficer>1</isOfficer><officerTitle>CFO</officerTitle><isDirector>1</isDirector>",
        _TX,
        footnotes='<footnotes><footnote id="F1">Sale under a Rule 10b5-1 trading plan.</footnote></footnotes>',
    )
    rows = sec_feeds_service._parse_form4(xml, "https://sec.test/f4")
    assert len(rows) == 1
    row = rows[0]
    assert row["plan_10b5_1"] is True
    assert row["is_officer"] is True
    assert row["is_director"] is True
    assert row["is_ten_percent_owner"] is False


def test_parse_form4_document_level_10b5_1_checkbox():
    xml = _form4("<isTenPercentOwner>1</isTenPercentOwner>", _TX, doc_flag="<aff10b5One>1</aff10b5One>")
    rows = sec_feeds_service._parse_form4(xml, "https://sec.test/f4")
    assert rows[0]["plan_10b5_1"] is True
    assert rows[0]["is_ten_percent_owner"] is True


def test_parse_form4_no_10b5_1_indicator_is_unknown():
    xml = _form4("<isDirector>1</isDirector>", _TX)  # no footnote text, no checkbox
    rows = sec_feeds_service._parse_form4(xml, "https://sec.test/f4")
    assert rows[0]["plan_10b5_1"] is None
