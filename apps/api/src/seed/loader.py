"""Helpers to read bundled reference data (risk taxonomy + diligence question templates).

These are the deterministic reference inputs used by the real-data analysis engine. (The earlier
synthetic ChainAssure sample outputs have been removed — the app now runs on live SEC data.)
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

SEED_DIR = Path(__file__).resolve().parent


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _strip_notes(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_notes(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_notes(v) for v in obj]
    return obj


@lru_cache
def risk_taxonomy() -> dict:
    return _strip_notes(_read_json(SEED_DIR / "risk_taxonomy.json"))


@lru_cache
def question_templates() -> list[dict]:
    return _strip_notes(_read_json(SEED_DIR / "diligence_question_templates.json"))["workstreams"]
