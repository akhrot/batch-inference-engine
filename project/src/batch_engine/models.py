"""Internal records passed through the pipeline, and the API request schema."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import BaseModel, Field, HttpUrl

from .retry import RetryPolicy

SUCCEEDED = "succeeded"
FAILED = "failed"

# Job states
PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
COMPLETED_WITH_ERRORS = "completed_with_errors"
JOB_FAILED = "failed"
INTERRUPTED = "interrupted"
ACTIVE_STATES = frozenset({PENDING, RUNNING})


@dataclass(frozen=True, slots=True)
class PromptItem:
    row_index: int
    item_id: str
    prompt: str


@dataclass(slots=True)
class ResultRecord:
    row_index: int
    item_id: str | None
    status: str
    prompt: str | None = None
    response: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    http_status: int | None = None
    attempts: int = 0
    latency_ms: int | None = None


@dataclass(slots=True)
class Chunk:
    """A contiguous block of input rows. Invalid rows ride along so they are recorded in order."""

    index: int
    items: list[PromptItem]
    invalid: list[ResultRecord] = field(default_factory=list)

    @property
    def rows(self) -> int:
        return len(self.items) + len(self.invalid)


@dataclass(slots=True)
class ChunkResult:
    chunk_index: int
    records: list[ResultRecord]
    retries_429: int = 0
    retries_5xx: int = 0


@dataclass(frozen=True)
class JobConfig:
    model: str
    chunk_size: int
    rate_limit: RetryPolicy
    server_error: RetryPolicy
    webhook_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RetryPolicyOverride(BaseModel):
    max_retries: int | None = Field(None, ge=0, le=50)
    base_delay_s: float | None = Field(None, ge=0, le=60)
    max_delay_s: float | None = Field(None, ge=0, le=600)
    multiplier: float | None = Field(None, ge=1, le=10)

    def apply(self, base: RetryPolicy) -> RetryPolicy:
        return base.merged(**self.model_dump())


class RetryOverrides(BaseModel):
    rate_limit: RetryPolicyOverride | None = None
    server_error: RetryPolicyOverride | None = None


class JobRequest(BaseModel):
    input_path: str = Field("sample_batch.json", min_length=1, description="Path relative to DATA_DIR")
    model: str | None = Field(None, min_length=1, max_length=200)
    chunk_size: int | None = Field(None, ge=1, le=1000)
    webhook_url: HttpUrl | None = None
    retry: RetryOverrides | None = None


def status_payload(job: dict[str, Any]) -> dict[str, Any]:
    """Public view of a job row, used by GET /status and the webhook."""
    return {
        "job_id": job["id"],
        "status": job["status"],
        "input_path": job["input_path"],
        "rows_read": job["rows_read"],
        "total": job["total"],
        "ingestion_complete": job["total"] is not None,
        "processed": job["succeeded"] + job["failed"],
        "succeeded": job["succeeded"],
        "failed": job["failed"],
        "retries": {"rate_limited": job["retries_429"], "server_error": job["retries_5xx"]},
        "error": job["error"],
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
    }
