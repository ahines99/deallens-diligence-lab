"""Per-organization quota inspection contracts (G39)."""
from __future__ import annotations

from pydantic import BaseModel


class QuotaBucketOut(BaseModel):
    name: str
    used: int
    limit: int
    window_seconds: int
    # ``None`` when the bucket is unlimited (``limit == 0``); otherwise ``max(0, limit - used)``.
    remaining: int | None


class LlmSpendByModelOut(BaseModel):
    model: str
    calls: int
    input_tokens: int
    output_tokens: int


class LlmSpendOut(BaseModel):
    """G80 rollup of recorded live-call token usage for the org, windowed."""

    window_hours: int
    total_calls: int
    input_tokens: int
    output_tokens: int
    by_model: list[LlmSpendByModelOut]


class QuotaUsageOut(BaseModel):
    organization_id: str
    buckets: list[QuotaBucketOut]
    llm_spend: LlmSpendOut
