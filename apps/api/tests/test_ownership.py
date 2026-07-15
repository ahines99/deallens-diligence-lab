"""Tests for G14 (13F institutional ownership + holder concentration) and G15 (13D/13G activist
stakes). All offline: pure parsers/classifiers are fed synthetic SEC inputs, and the live service
functions are exercised with a monkeypatched EDGAR client — mirroring tests/test_signals.py.

The source_status discipline is asserted explicitly: an upstream outage yields `unavailable`, never
a clean-looking empty result.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

# --- Synthetic 13F information table (namespaced, six positions) --------------
# Values 300/250/200/150/60/40 (Σ=1000) → weights .30/.25/.20/.15/.06/.04.
#   HHI       = .30²+.25²+.20²+.15²+.06²+.04² = .09+.0625+.04+.0225+.0036+.0016 = 0.2202
#   top5_share = .30+.25+.20+.15+.06 = 0.96
#   holder_count = 6 ; total_value = 1000
_NS = "http://www.sec.gov/edgar/document/thirteenf/informationtable"
_VALUES = [300, 250, 200, 150, 60, 40]


def _infotable_xml(values: list[int]) -> bytes:
    rows = "".join(
        f"""
      <infoTable>
        <nameOfIssuer>ISSUER {i}</nameOfIssuer>
        <titleOfClass>COM</titleOfClass>
        <cusip>00000000{i}</cusip>
        <value>{v}</value>
        <shrsOrPrnAmt>
          <sshPrnamt>{v * 10}</sshPrnamt>
          <sshPrnamtType>SH</sshPrnamtType>
        </shrsOrPrnAmt>
        <investmentDiscretion>SOLE</investmentDiscretion>
        <votingAuthority><Sole>{v * 10}</Sole><Shared>0</Shared><None>0</None></votingAuthority>
      </infoTable>"""
        for i, v in enumerate(values)
    )
    return f'<informationTable xmlns="{_NS}">{rows}</informationTable>'.encode("utf-8")


# --- (a) 13F information-table parse → correct holdings -----------------------
def test_parse_13f_infotable_reads_all_positions():
    from src.services import ownership_service

    holdings = ownership_service.parse_13f_infotable(_infotable_xml(_VALUES))
    assert len(holdings) == 6
    first = holdings[0]
    assert first["issuer"] == "ISSUER 0"
    assert first["cusip"] == "000000000"
    assert first["title"] == "COM"
    assert first["value"] == 300.0
    assert first["shares"] == 3000.0
    assert [h["value"] for h in holdings] == [float(v) for v in _VALUES]


def test_parse_13f_infotable_malformed_returns_empty():
    from src.services import ownership_service

    assert ownership_service.parse_13f_infotable(b"<not-xml") == []
    assert ownership_service.parse_13f_infotable(b"<informationTable></informationTable>") == []


# --- (b) concentration math: HHI, top-5 share, holder count ------------------
def test_concentration_math_is_hand_verified():
    from src.services import ownership_service

    holdings = [{"value": float(v)} for v in _VALUES]
    conc = ownership_service.concentration(holdings)
    assert conc["holder_count"] == 6
    assert conc["total_value"] == 1000.0
    assert conc["hhi"] == pytest.approx(0.2202)
    assert conc["top5_share"] == pytest.approx(0.96)


def test_concentration_is_scale_invariant_and_single_holding_is_one():
    from src.services import ownership_service

    # Scaling every value by 1000 leaves the ratios unchanged.
    scaled = ownership_service.concentration([{"value": v * 1000.0} for v in _VALUES])
    assert scaled["hhi"] == pytest.approx(0.2202)
    assert scaled["top5_share"] == pytest.approx(0.96)
    # A lone position is maximally concentrated.
    solo = ownership_service.concentration([{"value": 42.0}])
    assert solo["hhi"] == pytest.approx(1.0)
    assert solo["top5_share"] == pytest.approx(1.0)
    assert solo["holder_count"] == 1


def test_concentration_excludes_missing_and_nonpositive_values_never_imputes():
    from src.services import ownership_service

    conc = ownership_service.concentration(
        [{"value": 300.0}, {"value": None}, {"value": 0.0}, {"value": -5.0}, {"value": 700.0}]
    )
    assert conc["holder_count"] == 2  # only the two positive positions count
    assert conc["total_value"] == 1000.0
    assert conc["hhi"] == pytest.approx(0.58)  # .3²+.7² = .09+.49


def test_concentration_empty_is_none_not_zero():
    from src.services import ownership_service

    conc = ownership_service.concentration([])
    assert conc == {"hhi": None, "top5_share": None, "holder_count": 0, "total_value": None}


def test_parse_and_concentration_compose_end_to_end():
    from src.services import ownership_service

    holdings = ownership_service.parse_13f_infotable(_infotable_xml(_VALUES))
    conc = ownership_service.concentration(holdings)
    assert conc["hhi"] == pytest.approx(0.2202)
    assert conc["top5_share"] == pytest.approx(0.96)
    assert conc["holder_count"] == 6


# --- (c) 13D → activist, 13G → passive classification ------------------------
def test_classify_13d_is_activist_and_13g_is_passive():
    from src.services import ownership_service

    d = ownership_service.classify_stake("SC 13D")
    assert d == {"type": "13D", "is_activist": True, "is_amendment": False}
    g = ownership_service.classify_stake("SC 13G")
    assert g == {"type": "13G", "is_activist": False, "is_amendment": False}


def test_classify_marks_amendments_and_tolerates_form_variants():
    from src.services import ownership_service

    assert ownership_service.classify_stake("SC 13D/A")["is_amendment"] is True
    assert ownership_service.classify_stake("SC 13D/A")["is_activist"] is True
    assert ownership_service.classify_stake("SC 13G/A")["is_amendment"] is True
    assert ownership_service.classify_stake("13D")["type"] == "13D"
    with pytest.raises(ValueError):
        ownership_service.classify_stake("8-K")


def test_build_stake_event_preserves_missing_fields_as_none():
    from src.services import ownership_service

    ev = ownership_service.build_stake_event({"form": "SC 13D", "filing_date": "2026-02-01"})
    assert ev["type"] == "13D"
    assert ev["is_activist"] is True
    assert ev["filer"] is None
    assert ev["percent_owned"] is None
    full = ownership_service.build_stake_event(
        {"form": "SC 13G/A", "filing_date": "2026-03-02", "filer": "Vanguard Group",
         "percent_owned": 8.4, "accession": "acc-1", "url": "https://x/1"}
    )
    assert full == {
        "type": "13G", "form": "SC 13G/A", "filer": "Vanguard Group", "filing_date": "2026-03-02",
        "accession": "acc-1", "url": "https://x/1", "percent_owned": 8.4,
        "is_activist": False, "is_amendment": True,
    }


def test_cover_page_extraction_of_filer_and_percent():
    from src.services import ownership_service

    text = (
        "SCHEDULE 13D CUSIP No. 12345 "
        "NAME OF REPORTING PERSON ValueAct Capital Master Fund, L.P. "
        "S.S. OR I.R.S. IDENTIFICATION NO. OF ABOVE PERSON 98-1234567 "
        "PERCENT OF CLASS REPRESENTED BY AMOUNT IN ROW (11) 9.5 %"
    )
    assert ownership_service.extract_reporting_person(text).startswith("ValueAct Capital")
    assert ownership_service.extract_percent(text) == 9.5
    assert ownership_service.extract_reporting_person("no cover here") is None
    assert ownership_service.extract_percent("no percent here") is None


# --- (d) upstream outage → unavailable, never a clean-empty ------------------
def _target(monkeypatch):
    from src.services import ownership_service

    target = SimpleNamespace(cik="0001067983", ticker="BRK-A", name="BERKSHIRE HATHAWAY INC")
    monkeypatch.setattr(ownership_service, "get_target", lambda s, w: target)
    return target


def test_institutional_ownership_outage_is_unavailable_not_clean_empty(monkeypatch):
    from src.services import edgar_client, ownership_service

    _target(monkeypatch)
    monkeypatch.setattr(
        edgar_client, "get_submissions",
        lambda *_a, **_k: (_ for _ in ()).throw(edgar_client.EdgarError("offline")),
    )
    out = ownership_service.institutional_ownership(object(), "ws1")
    assert out["source_status"] == "unavailable"
    assert out["holdings"] == []
    assert out["concentration"]["holder_count"] == 0
    assert out["concentration"]["hhi"] is None


def test_activist_stakes_outage_is_unavailable_not_clean_empty(monkeypatch):
    from src.services import edgar_client, ownership_service

    _target(monkeypatch)
    monkeypatch.setattr(
        edgar_client, "get_submissions",
        lambda *_a, **_k: (_ for _ in ()).throw(edgar_client.EdgarError("offline")),
    )
    out = ownership_service.activist_stakes(object(), "ws1")
    assert out["source_status"] == "unavailable"
    assert out["events"] == []
    assert out["source_error"]


def test_non_manager_target_is_not_applicable_with_honest_note(monkeypatch):
    from src.services import edgar_client, ownership_service

    _target(monkeypatch)
    # An operating company: no 13F-HR among its forms.
    monkeypatch.setattr(
        edgar_client, "get_submissions",
        lambda *_a, **_k: {"name": "ACME CORP", "filings": {"recent": {"form": ["10-K", "8-K"]}}},
    )
    out = ownership_service.institutional_ownership(object(), "ws1")
    assert out["scope"] == "not_applicable"
    assert out["source_status"] == "unavailable"
    assert out["holdings"] == []
    assert "reverse index" in out["note"]


def test_manager_target_reports_portfolio_concentration(monkeypatch):
    from src.services import edgar_client, ownership_service

    _target(monkeypatch)
    monkeypatch.setattr(
        edgar_client, "get_submissions",
        lambda *_a, **_k: {
            "name": "BERKSHIRE HATHAWAY INC",
            "filings": {"recent": {
                "form": ["13F-HR", "10-K"],
                "filingDate": ["2026-05-15", "2026-02-20"],
                "reportDate": ["2026-03-31", "2025-12-31"],
                "accessionNumber": ["0001067983-26-000001", "0001067983-26-000000"],
            }},
        },
    )
    monkeypatch.setattr(
        ownership_service, "_find_infotable_doc", lambda cik10, acc: _infotable_xml(_VALUES)
    )
    out = ownership_service.institutional_ownership(object(), "ws1")
    assert out["scope"] == "manager_portfolio"
    assert out["source_status"] == "available"
    assert out["period_of_report"] == "2026-03-31"
    assert len(out["holdings"]) == 6
    assert out["holdings"][0]["value"] == 300.0  # ranked largest-first
    assert out["concentration"]["hhi"] == pytest.approx(0.2202)
    assert out["concentration"]["top5_share"] == pytest.approx(0.96)


def test_activist_stakes_classifies_submissions_into_timeline(monkeypatch):
    from src.services import edgar_client, ownership_service

    _target(monkeypatch)
    monkeypatch.setattr(
        edgar_client, "get_submissions",
        lambda *_a, **_k: {"filings": {"recent": {
            "form": ["SC 13D", "8-K", "SC 13G/A", "10-Q"],
            "filingDate": ["2026-06-01", "2026-05-01", "2026-04-01", "2026-03-01"],
            "accessionNumber": ["acc-d", "acc-x", "acc-g", "acc-q"],
            "primaryDocument": ["d.htm", "x.htm", "g.htm", "q.htm"],
        }}},
    )
    # Keep the test offline: no real cover-page fetches.
    monkeypatch.setattr(edgar_client, "fetch_document_text", lambda url: "")
    out = ownership_service.activist_stakes(object(), "ws1")
    assert out["source_status"] == "available"
    assert [e["type"] for e in out["events"]] == ["13D", "13G"]
    assert out["events"][0]["is_activist"] is True
    assert out["events"][1]["is_activist"] is False
    assert out["events"][1]["is_amendment"] is True
