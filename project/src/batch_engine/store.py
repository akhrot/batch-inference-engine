"""SQLite persistence for jobs and per-row results.

Methods are synchronous; async callers run them via `asyncio.to_thread`.
Each call opens its own connection, so the store is safe to use from any thread.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from .models import ACTIVE_STATES, INTERRUPTED, PENDING, RUNNING, SUCCEEDED, ResultRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    status        TEXT NOT NULL,
    input_path    TEXT NOT NULL,
    config_json   TEXT NOT NULL,
    rows_read     INTEGER NOT NULL DEFAULT 0,
    total         INTEGER,
    succeeded     INTEGER NOT NULL DEFAULT 0,
    failed        INTEGER NOT NULL DEFAULT 0,
    retries_429   INTEGER NOT NULL DEFAULT 0,
    retries_5xx   INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT
);
CREATE TABLE IF NOT EXISTS results (
    job_id        TEXT NOT NULL REFERENCES jobs(id),
    row_index     INTEGER NOT NULL,
    item_id       TEXT,
    status        TEXT NOT NULL,
    prompt        TEXT,
    response      TEXT,
    error_type    TEXT,
    error_message TEXT,
    http_status   INTEGER,
    attempts      INTEGER NOT NULL DEFAULT 0,
    latency_ms    INTEGER,
    PRIMARY KEY (job_id, row_index)
);
CREATE INDEX IF NOT EXISTS results_by_status ON results (job_id, status, row_index);
"""

RESULT_COLUMNS = (
    "row_index", "item_id", "status", "prompt", "response", "error_type",
    "error_message", "http_status", "attempts", "latency_ms",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:  # commit on success, roll back on error
                yield conn
        finally:
            conn.close()

    # --- jobs -------------------------------------------------------------

    def create_job(self, job_id: str, input_path: str, config: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, status, input_path, config_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (job_id, PENDING, input_path, json.dumps(config), _now()),
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        job = dict(row)
        job["config"] = json.loads(job.pop("config_json"))
        return job

    def mark_running(self, job_id: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE jobs SET status = ?, started_at = ? WHERE id = ?", (RUNNING, _now(), job_id))

    def add_rows_read(self, job_id: str, rows: int) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE jobs SET rows_read = rows_read + ? WHERE id = ?", (rows, job_id))

    def set_total(self, job_id: str, total: int) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE jobs SET total = ? WHERE id = ?", (total, job_id))

    def finish_job(self, job_id: str, status: str, error: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
                (status, error, _now(), job_id),
            )

    def mark_interrupted(self) -> int:
        """Called at startup: jobs left active by a previous process can no longer progress."""
        placeholders = ",".join("?" * len(ACTIVE_STATES))
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE jobs SET status = ?, finished_at = ?, error = 'process restarted while job was active' "
                f"WHERE status IN ({placeholders})",
                (INTERRUPTED, _now(), *ACTIVE_STATES),
            )
            return cur.rowcount

    # --- results ----------------------------------------------------------

    def write_results(
        self, job_id: str, records: Sequence[ResultRecord], retries_429: int = 0, retries_5xx: int = 0
    ) -> None:
        """Upsert a chunk of results and bump the job counters in one transaction."""
        succeeded = sum(1 for r in records if r.status == SUCCEEDED)
        with self._connect() as conn:
            conn.executemany(
                f"INSERT OR REPLACE INTO results (job_id, {', '.join(RESULT_COLUMNS)}) "
                f"VALUES (?, {', '.join('?' * len(RESULT_COLUMNS))})",
                [(job_id, *(getattr(r, c) for c in RESULT_COLUMNS)) for r in records],
            )
            conn.execute(
                "UPDATE jobs SET succeeded = succeeded + ?, failed = failed + ?, "
                "retries_429 = retries_429 + ?, retries_5xx = retries_5xx + ? WHERE id = ?",
                (succeeded, len(records) - succeeded, retries_429, retries_5xx, job_id),
            )

    def iter_results(self, job_id: str, status: str, batch_size: int = 500) -> Iterator[list[dict[str, Any]]]:
        """Yield results in row order, `batch_size` at a time (keyset pagination, constant memory)."""
        last = -1
        while True:
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT {', '.join(RESULT_COLUMNS)} FROM results "
                    "WHERE job_id = ? AND status = ? AND row_index > ? ORDER BY row_index LIMIT ?",
                    (job_id, status, last, batch_size),
                ).fetchall()
            if not rows:
                return
            yield [dict(r) for r in rows]
            last = rows[-1]["row_index"]
