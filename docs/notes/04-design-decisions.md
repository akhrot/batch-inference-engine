# 04: Design Decisions

The full design is in [../design.md](../design.md). This file records **what was decided, why, and who decided it**, so the reasoning survives a context reset.

## Decisions from the user's questions (13:02)

### 1. Scatter: chunks or single prompts? Decided: a hybrid

- Every prompt is still **one HTTP request**, because chat completions has no multi-prompt call. The question is only what goes on the queue.
- The spec says "Partition the 1,000 prompt records into concurrent execution chunks", so chunking is expected.
- **Chosen:** the queue holds **chunks** (default 25 prompts). A worker takes a chunk and fires **all of its prompts concurrently**. A **process-wide semaphore** (`MAX_IN_FLIGHT`) caps total in-flight requests.
  - The chunk is the unit of scheduling, database transactions and checkpoints (for the Spaces extension).
  - The prompt is the unit of execution and retry.
- **Why not chunks processed one prompt at a time:** one prompt stuck in retries would block the rest of its chunk.
- **Why not a queue of single prompts:** it balances load slightly better, but loses the natural checkpoint and transaction unit and matches the spec's wording less well.
- `WORKERS × CHUNK_SIZE` is kept well above `MAX_IN_FLIGHT`, so the semaphore stays saturated and a slow prompt only delays its own chunk.

### 2. Gather: database or files? Decided: SQLite

- Both keep memory flat if results are written as they arrive and downloads are streamed.
- **SQLite was chosen** for durable job status across restarts, counters by query, **idempotent upserts** keyed by `(job_id, row_index)`, easy resume, and no server.
- Costs: the driver blocks, so writes go through `asyncio.to_thread`. A **single collector** does all writes, which avoids lock contention. WAL mode lets readers proceed during writes.
- The store sits behind a small interface, so Postgres can replace it if several API instances ever need to share state.
- **Rejected: JSONL files.** Simpler, but no durable state, awkward deduplication and resume, and counters must be tracked separately.

### 3. Retries: separate settings for 429 and 5xx (the user's requirement)

- There are two independent policies, each with `max_retries`, `base_delay_s`, `max_delay_s`, a multiplier (2) and full jitter.
- They are configured by environment variables (`RETRY_429_*`, `RETRY_5XX_*`) and **can be overridden per job** in the `POST /job` body (`retry.rate_limit` and `retry.server_error`).
- Defaults:
  - **429:** 8 retries, 1.0 s base, 30 s cap. Generous, because the spec says rate limits must not drop prompts.
  - **5xx:** 3 retries, 0.5 s base, 5 s cap. Tight, because the spec says persistent 500s become isolated errors.
- Timeouts and connection errors use the **5xx policy**. Other 4xx errors (400, 401, 422) are **not retried**.
- Budgets are counted **separately per error class** on each prompt.
- **`Retry-After`:** on a 429 it sets a **shared cool-down gate** that pauses all workers. On a 5xx it delays only that request. The wait is `Retry-After` plus up to 10% jitter.
- Back-off formula: `sleep = random(0, min(max_delay, base × multiplier^retry))`, the "full jitter" approach.
- A request **releases its semaphore slot while sleeping** between retries.
- Naming change: the chat first said "max attempts 8 / 3"; the design uses **max retries** (not counting the first try).

### 4. Where does the bounded queue live? In memory, in the same process

- It is an `asyncio.Queue(maxsize=QUEUE_MAX_CHUNKS)` on the same event loop as FastAPI. Workers are asyncio tasks, not separate processes or threads.
- This is enough because the work is I/O-bound (waiting on HTTP). One loop can keep hundreds of requests in flight, and the GIL is not a bottleneck for that.
- **Trade-off:** chunks still in the queue are lost if the process crashes. They are recoverable by re-reading the input and skipping the rows already stored in SQLite.
- Switch to Redis Streams, SQS or RabbitMQ with separate worker processes when work must span several hosts or survive crashes without re-reading the input. This is part of the scale discussion in the README.

## Decisions the agent made in design.md (13:09–13:12), pending user review

| Topic | Decision |
|---|---|
| Submission endpoint | `POST /job` (singular, to match the spec's GETs). The body may contain `input_path`, `model`, `chunk_size`, `webhook_url` and `retry` overrides. Returns `202` with the job ID. |
| Input path safety | It must resolve inside `DATA_DIR`. Returns 400 for a bad path and 404 for a missing file. |
| Download | Returns **409** until the job is finished. The streamed JSON object has `summary`, `results[]` (successes) and `errors[]`, both in input order. |
| Status | `rows_read` grows as the file streams in; `total` is `null` until ingestion completes. Counters include retry counts per error class. |
| Job states | `pending → running → completed / completed_with_errors / failed`. On startup, a job left `running` becomes `interrupted`. Resume is designed for but **not planned** for this build. |
| Row-level vs job-level failures | Invalid rows and exhausted retries are row-level errors; the job ends `completed_with_errors`. Malformed JSON part-way through the file is a job-level `failed`, and rows already processed are kept. |
| Validation | Each row must be an object with a non-empty string `prompt`; `id` is optional. Invalid rows become `invalid_input` errors and are never sent to the model. |
| Shared vs per-job | Each job has its own reader, queue, workers and collector. The semaphore and cool-down gate are process-wide, because rate limits apply per API key. |
| Defaults | `CHUNK_SIZE` 25, `WORKERS` 8, `QUEUE_MAX_CHUNKS` 16, result queue 16, `MAX_IN_FLIGHT` 32. That is about 1,000 records in memory at most, whatever the input size. |
| Inference | OpenAI-compatible `POST {base}/chat/completions`. Default base `https://inference.do-ai.run/v1` (DigitalOcean serverless inference). Default model `llama3-8b-instruct` **(unverified; check with `GET /v1/models` once the key is available)**. `max_tokens` 128, timeout 60 s. |
| Webhook (extension) | POST the status payload when the job reaches a terminal state. Runs in the background with a small retry policy; a failure never changes the job outcome. Only http and https URLs are accepted. |
| Spaces (extension) | After each chunk commits, upload `jobs/{job_id}/chunks/{chunk_index}.jsonl` plus `manifest.json` through the S3 API, with capped concurrent uploads. |
| Testing | A fake backend (`httpx.MockTransport` in tests, `scripts/mock_inference.py` for demos) scripts 429s with `Retry-After`, 500s, timeouts and latency. CI never calls the live endpoint. |
| Throughput estimate | 32 in flight at about 1.5 s latency gives roughly 20 requests per second: about 1 minute for 1,000 prompts and about 7 hours for 500,000. These are illustrative assumptions. |

## Later decisions

- **Input submission (13:17):** by file path. `POST /job {"input_path": "..."}` resolves inside `DATA_DIR`, and the server streams the file. Prompts inline in the body were rejected because FastAPI parses the whole body into memory. Multipart upload is a possible later addition (it would need `python-multipart`).
- **Three tiers (13:58, approved 14:10):** the user wanted FastAPI, a durable queue and worker pools as separate services. That isn't feasible in the remaining time: there is no Redis or Docker installed, and it would add 40–50 minutes of work (broker, shared store, acknowledge/reclaim, cross-process rate limiting, completion detection, CI service container). **Chosen:** the same three tiers as separate modules in one process: `api.py`, `job_queue.py` (the `JobQueue` protocol plus `InMemoryQueue`) and `worker.py`. A Redis Streams queue would be one new class. Requirements for the separate-services version are listed in the README's scale section and design.md §9.

## Open items

- Confirm the exact model ID through the provider's `/v1/models` endpoint once `INFERENCE_API_KEY` is in `project/.env`.
- The user still needs to review and approve `design.md`.
