# 06: Implementation (14:10–14:26)

Coding was approved at 14:10, with the in-process three-tier layout and file-path input.

## Modules (`project/src/batch_engine/`)

| Module | Tier | What it does |
|---|---|---|
| `api.py` | Client-facing | `create_app(settings, transport=, webhook_transport=, runner_factory=)` is an app factory, with the transports and runner injectable for tests. `JobManager` checks that the input path stays inside `DATA_DIR`, merges per-job overrides into a `JobConfig`, creates the job row, and starts `runner.run` as a background task. Routes: `POST /job` (202), `GET /job/{id}/status`, `GET /job/{id}/download` (streamed, 409 while active), `GET /healthz`. On startup, active jobs left over from a previous process are marked `interrupted`. |
| `job_queue.py` | Queue | The `JobQueue` protocol (`put`, `get`, `close(consumers)`) is the seam for Redis or SQS later. `InMemoryQueue` wraps a bounded `asyncio.Queue`, rejects `maxsize < 1`, and tracks a `high_water` mark. It was renamed from `queue.py` so it can't shadow the standard-library `queue` module. |
| `worker.py` | Worker pool | `JobRunner.run()` uses a `TaskGroup` for the producer and W workers, plus one collector task. `process_chunk` fires a chunk's prompts with `gather`, and per-item exceptions become `internal_error` records. Final status is `failed` (ingestion error), `completed_with_errors` (any failed rows) or `completed`. The webhook fires after the status is persisted. Returns a `RunReport` with the queue high-water marks. |
| `inference.py` | Worker pool | `InferenceClient.complete()` makes one POST to `/chat/completions` per prompt. The `limiter` semaphore is held only around the HTTP call. Retry budgets are counted separately per `ErrorClass`. `RateLimitGate` is a shared cool-down set by 429 + `Retry-After`. The clock, sleep and rng are injectable. |
| `retry.py` | Shared | `RetryPolicy.backoff` uses full jitter. `merged()` applies partial overrides. `classify_status`: 429 is rate-limited; None, 408 and 5xx are server errors; other 4xx are non-retryable. `parse_retry_after` handles delta-seconds and HTTP dates, clamps to [0, 300] s, and returns None for NaN. |
| `ingest.py` | Worker pool | `read_chunks` streams with ijson's async API, using a thread-backed reader so disk I/O stays off the event loop. It first checks that the file starts with `[`. Invalid rows ride along in their chunk's `invalid` list, and ijson errors are raised as `InputFormatError`. |
| `store.py` | Shared | SQLite in WAL mode, with a new connection per call so it is thread-safe. `write_results` upserts rows and increments counters in one transaction. `iter_results` uses keyset pagination over the index `(job_id, status, row_index)`. |
| `webhook.py` | Extension | `send_webhook`: 3 attempts, retrying on 429, 5xx and connection errors; any other 4xx stops immediately. It uses a **separate httpx client**, so the inference key is never sent to webhook URLs. |
| `config.py` | Shared | A frozen `Settings` dataclass. `from_env()` uses a minimal `.env` loader where real environment variables win. Retry settings are `RETRY_429_*` and `RETRY_5XX_*`. |
| `models.py` | Shared | Dataclasses (`PromptItem`, `ResultRecord`, `Chunk`, `ChunkResult`, `JobConfig`), the pydantic `JobRequest` and `RetryPolicyOverride`, job state constants, and `status_payload()`. |

## Deviations from design.md

- The file names changed. `engine.py` is split into `worker.py` (pipeline) and `job_queue.py` (queue), to match the user's three-tier request. design.md section 13 was updated.
- Counters are incremented per chunk rather than recomputed. That is correct as long as each row is written once; a future resume feature would need to subtract any rows it overwrites.

## Tests (135 tests, about 4 s, run three times with no flakes)

- `tests/conftest.py` provides:
  - `FakeClock`, whose sleep advances time instantly and records each delay.
  - `FakeProvider`, an httpx `MockTransport` handler. A `rule(prompt, call_number)` returns a status, `"timeout"`, `"connect_error"`, `"bad_json"` or a Response. It tracks calls and the maximum concurrency seen.
  - Fast retry policies.
  - A `settings` fixture with a temporary data directory and database, chunk size 10, 4 workers, queue size 2 and 8 in flight.
- Unit tests:
  - `test_retry.py`: back-off curve, cap, jitter bounds, classification, `Retry-After` parsing.
  - `test_ingest.py`: every invalid-row type, chunk sizes and order, invalid rows riding along, empty and non-array files, truncated files, 5,000-row streaming.
  - `test_job_queue.py`: `put` blocks when full, sentinels, high-water mark ≤ maxsize.
  - `test_inference.py`: payload and auth header, model override, exact back-off delays, exhausting each budget, separate budgets, transport errors, non-retryable 4xx, bad body, `Retry-After` on 429 versus 5xx, gate behaviour, the concurrency cap, and the slot being released during back-off.
  - `test_store.py`: defaults, lifecycle, atomic counters, upsert idempotency, batched ordered iteration, per-job isolation, `mark_interrupted`, persistence.
  - `test_config_and_webhook.py`: environment overrides, `.env` precedence, defaults, request validation, webhook delivery, retry and give-up.
- Integration tests, each tier with its neighbours faked:
  - `test_worker_pipeline.py` (real store, ingestion and queues; fake provider): 1,000 rows with bounded queues and in-flight cap, a storm of 429s with zero drops (600 retries), isolated 500s, corrupt rows never sent, malformed and non-array files, empty input, slow prompts, the webhook succeeding and failing, an internal exception isolated to its row.
  - `test_api.py`: (a) the API with a `FakeRunner` in place of the worker tier: 202 returned quickly, default input, overrides reaching `JobConfig`, 409 while running, status shape, report format, path traversal returning 400, a missing file returning 404, invalid bodies returning 422, unknown jobs returning 404, healthz. (b) The full stack with a flaky `FakeProvider` and webhook. (c) Restart marking jobs `interrupted`.
- One flaky test was found and fixed: the slot-release test relied on a back-off delay, and full jitter could make that delay near zero. It now uses `rng=1.0`.

## Manual demo (about 14:22)

The mock provider (`scripts/mock_inference.py`: 10% 429s with `Retry-After: 1`, 2% 500s, 50–400 ms latency) and the real uvicorn app processed the 1,000-row sample in about 20 s. Result: `completed_with_errors`, 995 succeeded and 5 failed (exactly the 5 deliberately invalid rows), with 116 rate-limit retries, 26 server-error retries, **zero drops**, and a 232 KB report.

## Environment gotchas found while coding

- The agent sandbox blocks writing `.pth` files, so `pip install -e .` fails inside the agent shell. The fix is `pythonpath = ["src", "."]` in the pytest config, and `--app-dir src` for uvicorn. CI does a normal editable install.
- `fastapi.testclient` emits a deprecation warning suggesting `httpx2`. It is harmless.
- A file staged earlier (`scratch.txt`) got into the first commit despite the ignore rule. It was untracked with `git rm --cached`.
