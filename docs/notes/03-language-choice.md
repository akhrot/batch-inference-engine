# 03: Language Choice

**Decision (12:42): Python**, chosen by the user because they are much more fluent in it. The spec makes the candidate "fully responsible for the review, architecture, and correctness" of the code, so being able to explain and defend the code outweighs the technical fit.

## What was considered

The agent's technical ranking was **Go first, Python a close second.**

- **Go:** goroutines, channels and `context` map directly onto a bounded worker pool with scatter-gather. Bounded channels give backpressure for free. `json.Decoder` streams large arrays using only the standard library. Go 1.22+ routing understands the `GET /job/{id}/status` syntax. `httptest` and `-race` make concurrency tests easy.
- **Python:** `asyncio` with `httpx`, a semaphore and FastAPI is the fastest route to a working version. The downsides are that streaming JSON needs `ijson`, async code has subtle failure modes, and per-object memory is higher, which weakens the 500,000-item argument unless everything is streamed.
- **TypeScript/Node:** good for async I/O, but costs time on TypeScript configuration and needs libraries such as `p-limit` and `stream-json`.
- **Java 21:** virtual threads and the Jackson streaming parser fit well, but the boilerplate is too expensive for a 3-hour session.

## How Python covers the spec's needs

| Need | Python choice |
|---|---|
| REST API with background jobs | FastAPI plus asyncio tasks, served by uvicorn |
| Bounded worker pool and queue | `asyncio.Queue(maxsize=…)` with N worker tasks |
| Global in-flight cap | `asyncio.Semaphore` |
| HTTP client | one shared `httpx.AsyncClient` with connection limits |
| Streaming a 500,000-item JSON file | `ijson` (C backend, prebuilt cp315 wheel available) |
| Durable results | standard-library `sqlite3`, run off the event loop with `asyncio.to_thread` |
| Tests | pytest, pytest-asyncio and `httpx.MockTransport` |

The design (bounded pool, streaming input, separate retry policies, isolated failures) is language-agnostic; only the libraries change.
