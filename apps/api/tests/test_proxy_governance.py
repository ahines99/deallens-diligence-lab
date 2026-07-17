"""G13 — DEF 14A proxy ingestion: exec-comp table parse + governance red-flag heuristics.

Fully offline: the pure parse functions are fed synthetic proxy HTML/text; the fetch path is
monkeypatched so no test hits live SEC EDGAR. The endpoint roundtrip drives store + get through
the app's test database.
"""
from __future__ import annotations

from src.services import proxy_service


# --- (a) Summary Compensation Table parse ------------------------------------
# A realistic-enough SCT: name+title stacked via <br>, money with $ and commas, and one NEO
# (the CFO) whose Bonus cell is blank (a dash) — that value must stay None, never imputed.
_SCT_HTML = """
<html><body>
<p>Summary Compensation Table</p>
<table>
  <tr>
    <th>Name and Principal Position</th><th>Year</th><th>Salary ($)</th>
    <th>Bonus ($)</th><th>Stock Awards ($)</th><th>Total ($)</th>
  </tr>
  <tr>
    <td>Jane Doe<br/>Chief Executive Officer</td><td>2024</td><td>$1,200,000</td>
    <td>$500,000</td><td>$8,000,000</td><td>$9,700,000</td>
  </tr>
  <tr>
    <td>John Roe<br/>Chief Financial Officer</td><td>2024</td><td>$750,000</td>
    <td>&mdash;</td><td>$3,000,000</td><td>$3,750,000</td>
  </tr>
</table>
</body></html>
"""


def test_parse_summary_compensation_table_rows():
    rows = proxy_service.parse_summary_compensation_table(_SCT_HTML)
    assert len(rows) == 2

    ceo = rows[0]
    assert ceo["name"] == "Jane Doe"
    assert ceo["title"] == "Chief Executive Officer"
    assert ceo["salary"] == 1_200_000.0
    assert ceo["bonus"] == 500_000.0
    assert ceo["stock_awards"] == 8_000_000.0
    assert ceo["total"] == 9_700_000.0


def test_parse_summary_compensation_table_missing_value_stays_none():
    rows = proxy_service.parse_summary_compensation_table(_SCT_HTML)
    cfo = rows[1]
    assert cfo["name"] == "John Roe"
    assert cfo["title"] == "Chief Financial Officer"
    assert cfo["salary"] == 750_000.0
    # The blank/dash Bonus cell must be None, not 0.0 — the parser never imputes.
    assert cfo["bonus"] is None
    assert cfo["stock_awards"] == 3_000_000.0


def test_parse_summary_compensation_table_skips_rowspan_year_rows():
    """Audit M3: rowspan SCT layouts must not yield junk NEOs named "2023"/"2022"."""
    html = """
    <table>
      <tr>
        <th>Name and Principal Position</th><th>Year</th><th>Salary ($)</th>
        <th>Bonus ($)</th><th>Stock Awards ($)</th><th>Total ($)</th>
      </tr>
      <tr>
        <td rowspan="3">Jane Doe<br/>Chief Executive Officer</td><td>2024</td>
        <td>$1,200,000</td><td>$500,000</td><td>$8,000,000</td><td>$9,700,000</td>
      </tr>
      <tr><td>2023</td><td>$1,100,000</td><td>$450,000</td><td>$7,000,000</td><td>$8,550,000</td></tr>
      <tr><td>2022</td><td>$1,000,000</td><td>$400,000</td><td>$6,000,000</td><td>$7,400,000</td></tr>
    </table>
    """
    rows = proxy_service.parse_summary_compensation_table(html)
    assert [row["name"] for row in rows] == ["Jane Doe"]
    assert rows[0]["salary"] == 1_200_000.0  # the most recent fiscal year row
    assert rows[0]["total"] == 9_700_000.0


def test_parse_summary_compensation_table_no_table_returns_empty():
    assert proxy_service.parse_summary_compensation_table("") == []
    assert proxy_service.parse_summary_compensation_table("<p>No comp table here.</p>") == []


def test_parse_money_never_imputes():
    assert proxy_service._parse_money("$1,234,567") == 1_234_567.0
    assert proxy_service._parse_money("—") is None
    assert proxy_service._parse_money("") is None
    assert proxy_service._parse_money("N/A") is None
    assert proxy_service._parse_money("0") == 0.0


# --- (b) staggered / classified board ----------------------------------------
def test_staggered_board_flag_fires_on_classified_board():
    text = (
        "Our board of directors is a classified board divided into three classes of directors, "
        "with each class serving a staggered three-year term."
    )
    flags = {f["flag"]: f for f in proxy_service.detect_red_flags(text)}
    assert flags["staggered_board"]["present"] is True
    assert flags["staggered_board"]["evidence"]
    assert "classified board" in flags["staggered_board"]["evidence"].lower()


def test_staggered_board_flag_quiet_on_clean_text():
    text = (
        "All directors are elected annually by the stockholders to serve one-year terms, and "
        "there is a single class of common stock with one vote per share."
    )
    flags = {f["flag"]: f for f in proxy_service.detect_red_flags(text)}
    assert flags["staggered_board"]["present"] is False
    assert flags["staggered_board"]["evidence"] is None
    # A clean single-class annual-election proxy trips no governance flags.
    assert all(not f["present"] for f in flags.values())


# --- (c) dual-class share structure ------------------------------------------
def test_dual_class_flag_fires_on_super_voting_language():
    text = (
        "The company has two classes of authorized common stock: Class A common stock entitled "
        "to one vote per share and Class B common stock entitled to ten votes per share, a "
        "super-voting structure held by the founders."
    )
    flags = {f["flag"]: f for f in proxy_service.detect_red_flags(text)}
    assert flags["dual_class"]["present"] is True
    assert flags["dual_class"]["evidence"]


# --- (d) combined CEO / Chair -------------------------------------------------
def test_combined_ceo_chair_flag_fires():
    text = (
        "Ms. Jane Doe serves as our Chairman and Chief Executive Officer, combining the roles of "
        "board leadership and executive management."
    )
    flags = {f["flag"]: f for f in proxy_service.detect_red_flags(text)}
    assert flags["combined_ceo_chair"]["present"] is True
    assert flags["combined_ceo_chair"]["evidence"]


def test_poison_pill_flag_fires():
    text = "The board adopted a shareholder rights plan (a so-called poison pill) in 2024."
    flags = {f["flag"]: f for f in proxy_service.detect_red_flags(text)}
    assert flags["poison_pill"]["present"] is True


# --- (e) source unavailable → unavailable status -----------------------------
def test_fetch_unavailable_when_edgar_down(monkeypatch):
    from src.services import edgar_client
    from src.services.edgar_client import EdgarError

    def boom(*_args, **_kwargs):
        raise EdgarError("offline")

    monkeypatch.setattr(edgar_client, "recent_filings", boom)
    result = proxy_service.fetch_proxy_governance("0000000000")
    assert result["source_status"] == "unavailable"
    assert result["exec_comp"] == []
    assert result["red_flags"] == []
    assert result["raw_note"]


def test_fetch_unavailable_when_no_proxy_on_file(monkeypatch):
    from src.services import edgar_client

    monkeypatch.setattr(edgar_client, "recent_filings", lambda *a, **k: [])
    result = proxy_service.fetch_proxy_governance("0000000000")
    assert result["source_status"] == "unavailable"
    assert "No DEF 14A" in result["raw_note"]


def test_fetch_available_parses_comp_and_flags(monkeypatch):
    from src.services import edgar_client

    proxy = edgar_client.FilingMeta(
        form="DEF 14A",
        filing_date="2025-04-01",
        accession="0000000000-25-000001",
        primary_document="proxy.htm",
        primary_doc_url="https://sec.gov/proxy.htm",
        report_date="2025-04-01",
    )
    monkeypatch.setattr(edgar_client, "recent_filings", lambda *a, **k: [proxy])
    html = _SCT_HTML + "<p>The board is a classified board with three classes of directors.</p>"
    monkeypatch.setattr(edgar_client, "fetch_document_html", lambda url: html)

    result = proxy_service.fetch_proxy_governance("0000000000")
    assert result["source_status"] == "available"
    assert result["def14a_accession"] == "0000000000-25-000001"
    assert result["filing_date"] == "2025-04-01"
    assert len(result["exec_comp"]) == 2
    staggered = next(f for f in result["red_flags"] if f["flag"] == "staggered_board")
    assert staggered["present"] is True


def test_fetch_prefers_definitive_proxy_over_newer_defa14a_supplement(monkeypatch):
    """Audit M3: a newer DEFA14A press-release supplement must not shadow the DEF 14A body."""
    from src.services import edgar_client

    def meta(form: str, filing_date: str, accession: str) -> edgar_client.FilingMeta:
        return edgar_client.FilingMeta(
            form=form,
            filing_date=filing_date,
            accession=accession,
            primary_document="doc.htm",
            primary_doc_url=f"https://sec.gov/{accession}.htm",
            report_date=filing_date,
        )

    filings = [  # newest-first, as EDGAR submissions arrive
        meta("DEFA14A", "2025-05-10", "acc-defa"),
        meta("DEF 14A", "2025-04-01", "acc-def"),
        meta("DEF 14A", "2024-04-02", "acc-def-prior"),
    ]
    monkeypatch.setattr(edgar_client, "recent_filings", lambda *a, **k: filings)
    monkeypatch.setattr(edgar_client, "fetch_document_html", lambda url: _SCT_HTML)

    result = proxy_service.fetch_proxy_governance("0000000000")
    assert result["def14a_accession"] == "acc-def"
    assert result["filing_date"] == "2025-04-01"
    assert result["source_status"] == "available"


def test_fetch_partial_when_comp_table_unparseable(monkeypatch):
    from src.services import edgar_client

    proxy = edgar_client.FilingMeta(
        form="DEF 14A",
        filing_date="2025-04-01",
        accession="acc-2",
        primary_document="proxy.htm",
        primary_doc_url="https://sec.gov/proxy.htm",
        report_date="2025-04-01",
    )
    monkeypatch.setattr(edgar_client, "recent_filings", lambda *a, **k: [proxy])
    monkeypatch.setattr(
        edgar_client,
        "fetch_document_html",
        lambda url: "<html><body><p>Proxy with no compensation table.</p></body></html>",
    )
    result = proxy_service.fetch_proxy_governance("0000000000")
    assert result["source_status"] == "partial"
    assert result["exec_comp"] == []
    # Red flags were still scanned (source was retrievable), so this is partial, not unavailable.
    assert isinstance(result["red_flags"], list) and result["red_flags"]


# --- (f) endpoint store + get roundtrip --------------------------------------
def _make_workspace_with_target(cik: str = "0000000000") -> str:
    from src.db.session import SessionLocal
    from src.models import Target
    from src.schemas.workspace import WorkspaceCreate
    from src.services import workspace_service

    with SessionLocal() as session:
        ws = workspace_service.create_workspace(
            session, WorkspaceCreate(name="Proxy Co", deal_type="buyout")
        )
        session.add(
            Target(
                workspace_id=ws.id,
                name="Proxy Co",
                target_type="public_company",
                cik=cik,
            )
        )
        session.commit()
        return ws.id


def test_governance_profile_store_and_get_roundtrip(client, monkeypatch):
    workspace_id = _make_workspace_with_target()

    payload = {
        "def14a_accession": "0000000000-25-000001",
        "filing_date": "2025-04-01",
        "exec_comp": [
            {"name": "Jane Doe", "title": "CEO", "salary": 1_200_000.0,
             "bonus": 500_000.0, "stock_awards": 8_000_000.0, "total": 9_700_000.0},
            {"name": "John Roe", "title": "CFO", "salary": 750_000.0,
             "bonus": None, "stock_awards": 3_000_000.0, "total": 3_750_000.0},
        ],
        "red_flags": [
            {"flag": "staggered_board", "label": "Staggered / classified board",
             "present": True, "evidence": "classified board with three classes"},
            {"flag": "dual_class", "label": "Dual-class share structure",
             "present": False, "evidence": None},
        ],
        "source_status": "available",
        "raw_note": None,
    }
    monkeypatch.setattr(proxy_service, "fetch_proxy_governance", lambda cik10: payload)

    post = client.post(f"/api/workspaces/{workspace_id}/governance-profile")
    assert post.status_code == 200, post.text
    body = post.json()
    assert body["workspace_id"] == workspace_id
    assert body["source_status"] == "available"
    assert len(body["exec_comp"]) == 2
    assert body["exec_comp"][1]["bonus"] is None
    assert body["red_flags"][0]["flag"] == "staggered_board"

    got = client.get(f"/api/workspaces/{workspace_id}/governance-profile")
    assert got.status_code == 200, got.text
    assert got.json()["def14a_accession"] == "0000000000-25-000001"
    assert got.json()["exec_comp"][0]["name"] == "Jane Doe"


def test_get_governance_profile_404_before_build(client):
    workspace_id = _make_workspace_with_target()
    resp = client.get(f"/api/workspaces/{workspace_id}/governance-profile")
    assert resp.status_code == 404


def test_build_requires_cik(client):
    from src.db.session import SessionLocal
    from src.schemas.workspace import WorkspaceCreate
    from src.services import workspace_service

    with SessionLocal() as session:
        ws = workspace_service.create_workspace(
            session, WorkspaceCreate(name="No CIK Co", deal_type="buyout")
        )
        workspace_id = ws.id

    resp = client.post(f"/api/workspaces/{workspace_id}/governance-profile")
    assert resp.status_code == 404
