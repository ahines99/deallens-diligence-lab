"""Schemas for the G45 workspace export bundle verification contract."""
from __future__ import annotations

from pydantic import BaseModel


class BundleVerificationCheck(BaseModel):
    name: str
    expected: str | None
    actual: str | None
    passed: bool


class BundleVerificationResult(BaseModel):
    valid: bool
    checks: list[BundleVerificationCheck]
