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


class QuotaUsageOut(BaseModel):
    organization_id: str
    buckets: list[QuotaBucketOut]
