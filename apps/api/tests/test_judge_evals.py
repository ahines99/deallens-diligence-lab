"""G05 — LLM-as-judge faithfulness evals: mock-judge logic + persistence roundtrip."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.db.base import Base
from src.eval.judge import MockJudge
from src.models.eval_run import JudgeEvalRun
from src.services import judge_service

CONTEXT = "Revenue was $100 million in FY2025 [EV-001]. Margin was 20% [EV-002]."


def _db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def test_mock_judge_passes_a_grounded_answer():
    judge = MockJudge()
    verdict = judge(
        "What was revenue?", "Revenue reached $100 million [EV-001].", CONTEXT
    )
    assert verdict.faithful is True
    assert verdict.score == 1.0
    assert verdict.unsupported_numbers == ()
    assert verdict.unsupported_citations == ()


def test_mock_judge_flags_an_unsupported_number():
    judge = MockJudge()
    verdict = judge(
        "What was revenue?", "Revenue was $150 million [EV-001].", CONTEXT
    )
    assert verdict.faithful is False
    assert "$150million" in verdict.unsupported_numbers
    assert verdict.score < 1.0
    assert "unsupported numbers" in verdict.reason


def test_mock_judge_flags_an_unsupported_citation():
    judge = MockJudge()
    verdict = judge(
        "cite it", "Revenue was $100 million [EV-999].", CONTEXT
    )
    assert verdict.faithful is False
    assert "EV-999" in verdict.unsupported_citations


def test_answer_with_no_checkable_claims_is_trivially_faithful():
    judge = MockJudge()
    verdict = judge("qualitative", "Revenue grew and the outlook is positive.", CONTEXT)
    assert verdict.faithful is True
    assert verdict.score == 1.0


def test_persisted_judgment_roundtrips_with_provenance():
    with _db() as session:
        run = judge_service.judge_answer(
            session,
            question="What was revenue?",
            answer="Revenue was $150 million [EV-001].",
            context=CONTEXT,
            model_version="claude-test",
            prompt_version="grounded-synth-v1",
            prompt_hash="a" * 64,
            workspace_id="ws-1",
        )
        run_id = run.id
        assert run.faithful is False

        fetched = session.get(JudgeEvalRun, run_id)
        assert fetched is not None
        assert fetched.judge_name == "mock-faithfulness-v1"
        assert fetched.model_version == "claude-test"
        assert fetched.prompt_version == "grounded-synth-v1"
        assert fetched.prompt_hash == "a" * 64
        assert fetched.faithful is False
        assert "$150million" in fetched.details["unsupported_numbers"]
        session.close()


def test_quality_summary_groups_by_model_and_prompt():
    with _db() as session:
        judge_service.judge_answer(
            session,
            question="q1",
            answer="Revenue was $100 million [EV-001].",  # faithful
            context=CONTEXT,
            model_version="claude-test",
            prompt_version="v1",
            workspace_id="ws-1",
        )
        judge_service.judge_answer(
            session,
            question="q2",
            answer="Revenue was $150 million [EV-001].",  # unfaithful
            context=CONTEXT,
            model_version="claude-test",
            prompt_version="v1",
            workspace_id="ws-1",
        )
        summary = judge_service.quality_summary(session, workspace_id="ws-1")
        assert summary["total"] == 2
        assert summary["faithful"] == 1
        assert summary["faithful_rate"] == 0.5
        group = summary["groups"][0]
        assert group["model_version"] == "claude-test"
        assert group["prompt_version"] == "v1"
        assert group["count"] == 2
        assert group["faithful"] == 1
        assert group["faithful_rate"] == 0.5
        assert 0.0 <= group["mean_score"] <= 1.0
        session.close()
