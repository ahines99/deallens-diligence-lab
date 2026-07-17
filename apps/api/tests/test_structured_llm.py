"""G51 — structured LLM substrate: schema-constrained JSON calls that fail closed.

Every Wave 5 LLM-first path (risk extraction G52, claim extraction G53) rides on
``llm_provider.structured_llm``. These tests pin the gating matrix (consent / mock / no key),
the fail-closed parse and schema paths, and the prompt-manifest provenance binding. No network:
providers are fakes, exactly like ``test_grounded_synthesis``.
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from src.agents import llm_provider
from src.config import settings
from src.services import prompt_registry


class _Payload(BaseModel):
    findings: list[dict]


class _FakeProvider:
    model = "fake-model"

    def __init__(self, response: str, *, raises: bool = False) -> None:
        self._response = response
        self._raises = raises
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        if self._raises:
            raise RuntimeError("provider down")
        return self._response


@pytest.fixture()
def live_mode(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")


def _call(provider: _FakeProvider, *, external_allowed: bool = True):
    return llm_provider.structured_llm(
        "risk_extraction",
        "excerpts here",
        _Payload,
        external_allowed=external_allowed,
        provider_factory=lambda: provider,
    )


def test_no_consent_never_calls_the_provider(live_mode):
    provider = _FakeProvider('{"findings": []}')
    outcome = _call(provider, external_allowed=False)
    assert outcome.data is None
    assert outcome.applied is False
    assert outcome.reason == "no_consent"
    assert provider.calls == 0


def test_mock_mode_never_calls_the_provider():
    provider = _FakeProvider('{"findings": []}')
    outcome = _call(provider)
    assert outcome.data is None
    assert outcome.reason == "mock"
    assert provider.calls == 0


def test_missing_api_key_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "llm_api_key", "")
    provider = _FakeProvider('{"findings": []}')
    outcome = _call(provider)
    assert outcome.data is None
    assert outcome.reason == "no_api_key"
    assert provider.calls == 0


def test_valid_json_in_code_fences_is_parsed_and_manifest_bound(live_mode):
    provider = _FakeProvider(
        'Sure, here is the JSON:\n```json\n{"findings": [{"title": "Concentration"}]}\n```'
    )
    outcome = _call(provider)
    assert outcome.applied is True
    assert outcome.reason == "applied"
    assert isinstance(outcome.data, _Payload)
    assert outcome.data.findings == [{"title": "Concentration"}]
    spec = prompt_registry.get("risk_extraction")
    assert outcome.manifest == {
        "prompt_id": "risk_extraction",
        "prompt_version": spec.prompt_version,
        "prompt_hash": spec.prompt_hash,
        "model": "fake-model",
    }


def test_malformed_json_fails_closed_as_parse_error(live_mode):
    for response in ("no json at all", '{"findings": [unterminated'):
        outcome = _call(_FakeProvider(response))
        assert outcome.data is None
        assert outcome.applied is False
        assert outcome.reason == "parse_error"


def test_schema_mismatch_fails_closed(live_mode):
    outcome = _call(_FakeProvider('{"totally": "wrong shape"}'))
    assert outcome.data is None
    assert outcome.reason == "schema_mismatch"
    assert outcome.manifest is not None  # the call happened; provenance is still recorded


def test_provider_error_fails_closed(live_mode):
    outcome = _call(_FakeProvider("", raises=True))
    assert outcome.data is None
    assert outcome.reason == "error"


def test_unknown_prompt_id_is_a_programmer_error(live_mode):
    with pytest.raises(prompt_registry.UnknownPrompt):
        llm_provider.structured_llm(
            "not-registered", "x", _Payload, external_allowed=True
        )


def test_wave5_prompts_are_registered_and_hashed():
    for prompt_id in ("risk_extraction", "claim_extraction", "cross_corpus_synthesis"):
        spec = prompt_registry.get(prompt_id)
        assert spec.prompt_version
        assert len(spec.prompt_hash) == 64
        assert prompt_id in prompt_registry.prompt_ids()
