"""G47 — offline coverage for memo redlines: added/removed/changed + numeric-change detection."""
from __future__ import annotations

import pytest

from src.db.base import Base, new_uuid
from src.db.session import SessionLocal, engine
from src.models.underwriting_data import AnalysisRun  # noqa: F401 - registers tables
from src.models.workspace import Workspace
from src.schemas.underwriting_data import AnalysisRunCreate, ArtifactVersionCreate
from src.services import memo_redline_service as redline
from src.services import underwriting_data_service as underwriting


@pytest.fixture()
def redline_session():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    workspace = Workspace(
        id=new_uuid(),
        name="Redline Workspace",
        deal_type="buyout",
        investment_question="Should the fund proceed?",
        status="draft",
    )
    session.add(workspace)
    session.commit()
    try:
        yield session, workspace
    finally:
        session.close()


def _run_with_memo(session, workspace_id: str, memo: str) -> AnalysisRun:
    run = underwriting.create_analysis_run(
        session,
        workspace_id,
        AnalysisRunCreate(run_type="full_diligence", output_summary={"note": "sealed"}),
    )
    underwriting.create_artifact_version(
        session,
        workspace_id,
        ArtifactVersionCreate(
            artifact_type="diligence_pack",
            analysis_run_id=run.id,
            content_json={"ic_memo_markdown": memo},
        ),
    )
    return run


MEMO_V1 = (
    "Revenue was $120 million in FY2025. "
    "The management team is experienced and stable. "
    "Net leverage stands at 3.5x."
)
MEMO_V2 = (
    "Revenue was $135 million in FY2025. "
    "The management team is experienced and stable. "
    "We identified a new customer-concentration risk."
)


def test_diff_flags_added_removed_and_changed_numeric_claim(redline_session):
    session, workspace = redline_session
    run_a = _run_with_memo(session, workspace.id, MEMO_V1)
    run_b = _run_with_memo(session, workspace.id, MEMO_V2)

    result = redline.diff_runs(session, workspace.id, run_a.id, run_b.id)

    assert result["is_empty"] is False
    assert result["granularity"] == "artifact.content_json.ic_memo_markdown"

    # The revenue sentence changed only in its number → a "changed" claim, flagged numeric.
    changed = result["changed"]
    assert len(changed) == 1
    revenue_change = changed[0]
    assert revenue_change["numeric_change"] is True
    assert revenue_change["numbers_added"] == ["$135million"]
    assert revenue_change["numbers_removed"] == ["$120million"]
    assert result["numeric_changes"] == [revenue_change]

    # The leverage sentence was removed; the new-risk sentence was added; the stable team line
    # is unchanged and therefore in neither list.
    assert any("leverage" in sentence.lower() for sentence in result["removed"])
    assert any("customer-concentration" in sentence.lower() for sentence in result["added"])
    assert all("management team" not in sentence for sentence in result["added"] + result["removed"])
    assert result["counts"] == {"changed": 1, "added": 1, "removed": 1, "numeric_changes": 1}


def test_identical_runs_produce_an_empty_diff(redline_session):
    session, workspace = redline_session
    run_a = _run_with_memo(session, workspace.id, MEMO_V1)
    run_b = _run_with_memo(session, workspace.id, MEMO_V1)

    result = redline.diff_runs(session, workspace.id, run_a.id, run_b.id)
    assert result["is_empty"] is True
    assert result["changed"] == []
    assert result["added"] == []
    assert result["removed"] == []
    assert result["numeric_changes"] == []


def test_comparing_a_run_with_itself_is_rejected(redline_session):
    session, workspace = redline_session
    run_a = _run_with_memo(session, workspace.id, MEMO_V1)
    with pytest.raises(redline.MemoRedlineError):
        redline.diff_runs(session, workspace.id, run_a.id, run_a.id)


def test_unknown_run_is_not_found(redline_session):
    from src.services.common import NotFound

    session, workspace = redline_session
    run_a = _run_with_memo(session, workspace.id, MEMO_V1)
    with pytest.raises(NotFound):
        redline.diff_runs(session, workspace.id, run_a.id, "nonexistent-run")
