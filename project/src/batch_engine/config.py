"""Runtime settings, read from environment variables and an optional project/.env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

from .retry import RetryPolicy

PROJECT_DIR = Path(__file__).resolve().parents[2]

T = TypeVar("T")


def load_dotenv(path: Path) -> None:
    """Minimal KEY=VALUE loader. Real environment variables take precedence."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _env(name: str, default: T, cast: Callable[[str], T]) -> T:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else cast(raw)


@dataclass(frozen=True)
class Settings:
    inference_base_url: str = "https://inference.do-ai.run/v1"
    inference_api_key: str = ""
    inference_model: str = "llama3-8b-instruct"
    inference_max_tokens: int = 128
    inference_timeout_s: float = 60.0
    data_dir: Path = PROJECT_DIR / "data"
    db_path: Path = PROJECT_DIR / "var" / "batch_engine.db"
    chunk_size: int = 25
    workers: int = 8
    queue_max_chunks: int = 16
    result_queue_max: int = 16
    max_in_flight: int = 32
    rate_limit_policy: RetryPolicy = RetryPolicy(max_retries=8, base_delay_s=1.0, max_delay_s=30.0)
    server_error_policy: RetryPolicy = RetryPolicy(max_retries=3, base_delay_s=0.5, max_delay_s=5.0)

    @classmethod
    def from_env(cls, dotenv: Path | None = PROJECT_DIR / ".env") -> Settings:
        if dotenv is not None:
            load_dotenv(dotenv)
        d = cls()
        return cls(
            inference_base_url=_env("INFERENCE_BASE_URL", d.inference_base_url, str),
            inference_api_key=_env("INFERENCE_API_KEY", d.inference_api_key, str),
            inference_model=_env("INFERENCE_MODEL", d.inference_model, str),
            inference_max_tokens=_env("INFERENCE_MAX_TOKENS", d.inference_max_tokens, int),
            inference_timeout_s=_env("INFERENCE_TIMEOUT_S", d.inference_timeout_s, float),
            data_dir=_env("DATA_DIR", d.data_dir, Path),
            db_path=_env("DB_PATH", d.db_path, Path),
            chunk_size=_env("CHUNK_SIZE", d.chunk_size, int),
            workers=_env("WORKERS", d.workers, int),
            queue_max_chunks=_env("QUEUE_MAX_CHUNKS", d.queue_max_chunks, int),
            result_queue_max=_env("RESULT_QUEUE_MAX", d.result_queue_max, int),
            max_in_flight=_env("MAX_IN_FLIGHT", d.max_in_flight, int),
            rate_limit_policy=_policy_from_env("RETRY_429", d.rate_limit_policy),
            server_error_policy=_policy_from_env("RETRY_5XX", d.server_error_policy),
        )


def _policy_from_env(prefix: str, default: RetryPolicy) -> RetryPolicy:
    return RetryPolicy(
        max_retries=_env(f"{prefix}_MAX_RETRIES", default.max_retries, int),
        base_delay_s=_env(f"{prefix}_BASE_DELAY_S", default.base_delay_s, float),
        max_delay_s=_env(f"{prefix}_MAX_DELAY_S", default.max_delay_s, float),
        multiplier=_env(f"{prefix}_MULTIPLIER", default.multiplier, float),
    )
