"""Client-facing tier: FastAPI routes and the job manager.

Run with:  uvicorn --factory batch_engine.api:create_app
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterator, Protocol

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import Settings
from .inference import InferenceClient, RateLimitGate
from .models import ACTIVE_STATES, FAILED, JOB_FAILED, SUCCEEDED, JobConfig, JobRequest, status_payload
from .store import Store
from .worker import JobRunner

log = logging.getLogger(__name__)


class Runner(Protocol):
    async def run(self, job_id: str, input_path: Path, config: JobConfig) -> Any: ...


RunnerFactory = Callable[[Store, InferenceClient, Settings, httpx.AsyncClient], Runner]


class JobManager:
    """Creates jobs and runs them as background tasks so POST /job returns immediately."""

    def __init__(self, store: Store, runner: Runner, settings: Settings) -> None:
        self.store = store
        self.runner = runner
        self.settings = settings
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def resolve_input(self, input_path: str) -> Path:
        data_dir = self.settings.data_dir.resolve()
        path = (data_dir / input_path).resolve()
        if not path.is_relative_to(data_dir):
            raise HTTPException(400, "input_path must be inside the data directory")
        if not path.is_file():
            raise HTTPException(404, f"input file not found: {input_path}")
        return path

    def build_config(self, req: JobRequest) -> JobConfig:
        s = self.settings
        overrides = req.retry
        return JobConfig(
            model=req.model or s.inference_model,
            chunk_size=req.chunk_size or s.chunk_size,
            rate_limit=overrides.rate_limit.apply(s.rate_limit_policy)
            if overrides and overrides.rate_limit else s.rate_limit_policy,
            server_error=overrides.server_error.apply(s.server_error_policy)
            if overrides and overrides.server_error else s.server_error_policy,
            webhook_url=str(req.webhook_url) if req.webhook_url else None,
        )

    async def submit(self, req: JobRequest) -> str:
        path = self.resolve_input(req.input_path)
        config = self.build_config(req)
        job_id = uuid.uuid4().hex
        relative = str(path.relative_to(self.settings.data_dir.resolve()))
        await asyncio.to_thread(self.store.create_job, job_id, relative, config.to_dict())
        task = asyncio.create_task(self._run(job_id, path, config), name=f"job-{job_id}")
        self._tasks[job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job_id, None))
        return job_id

    async def _run(self, job_id: str, path: Path, config: JobConfig) -> None:
        try:
            await self.runner.run(job_id, path, config)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("job %s crashed", job_id)
            await asyncio.to_thread(self.store.finish_job, job_id, JOB_FAILED, f"internal error: {exc!r}")

    @property
    def active_jobs(self) -> int:
        return len(self._tasks)

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)


def create_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    webhook_transport: httpx.AsyncBaseTransport | None = None,
    runner_factory: RunnerFactory | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = Store(settings.db_path)
        if interrupted := store.mark_interrupted():
            log.warning("marked %d job(s) from a previous run as interrupted", interrupted)
        headers = {"Authorization": f"Bearer {settings.inference_api_key}"} if settings.inference_api_key else {}
        async with (
            httpx.AsyncClient(
                base_url=settings.inference_base_url,
                headers=headers,
                timeout=settings.inference_timeout_s,
                limits=httpx.Limits(max_connections=settings.max_in_flight),
                transport=transport,
            ) as inference_http,
            # Separate client so the inference API key is never sent to webhook URLs.
            httpx.AsyncClient(timeout=10, transport=webhook_transport) as webhook_http,
        ):
            inference = InferenceClient(
                inference_http,
                model=settings.inference_model,
                max_tokens=settings.inference_max_tokens,
                limiter=asyncio.Semaphore(settings.max_in_flight),
                gate=RateLimitGate(),
            )
            factory = runner_factory or JobRunner
            app.state.manager = JobManager(store, factory(store, inference, settings, webhook_http), settings)
            yield
            await app.state.manager.shutdown()

    app = FastAPI(title="Batch Inference Engine", version="0.1.0", lifespan=lifespan)

    def manager(request: Request) -> JobManager:
        return request.app.state.manager

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, Any]:
        return {"status": "ok", "active_jobs": manager(request).active_jobs}

    @app.post("/job", status_code=202)
    async def submit_job(request: Request, body: JobRequest | None = None) -> dict[str, Any]:
        job_id = await manager(request).submit(body or JobRequest())
        return {
            "job_id": job_id,
            "status": "pending",
            "status_url": f"/job/{job_id}/status",
            "download_url": f"/job/{job_id}/download",
        }

    @app.get("/job/{job_id}/status")
    async def job_status(request: Request, job_id: str) -> dict[str, Any]:
        job = await asyncio.to_thread(manager(request).store.get_job, job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return status_payload(job)

    @app.get("/job/{job_id}/download", response_model=None)
    async def download(request: Request, job_id: str) -> StreamingResponse | JSONResponse:
        store = manager(request).store
        job = await asyncio.to_thread(store.get_job, job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if job["status"] in ACTIVE_STATES:
            return JSONResponse(
                status_code=409,
                content={"detail": "job has not finished yet", "status": status_payload(job)},
            )
        return StreamingResponse(
            report_stream(store, job),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="job-{job_id}.json"'},
        )

    return app


def report_stream(store: Store, job: dict[str, Any]) -> Iterator[str]:
    """Stream the report as one JSON document without holding all rows in memory."""
    total = job["total"] if job["total"] is not None else job["rows_read"]
    head = {
        "job_id": job["id"],
        "status": job["status"],
        "error": job["error"],
        "summary": {"total": total, "succeeded": job["succeeded"], "failed": job["failed"]},
    }
    yield json.dumps(head)[:-1] + ', "results": ['
    yield from _rows(store, job["id"], SUCCEEDED, _success_view)
    yield '], "errors": ['
    yield from _rows(store, job["id"], FAILED, _error_view)
    yield "]}"


def _rows(store: Store, job_id: str, status: str, view: Callable[[dict[str, Any]], dict[str, Any]]) -> Iterator[str]:
    first = True
    for batch in store.iter_results(job_id, status):
        body = ",".join(json.dumps(view(r)) for r in batch)
        yield body if first else "," + body
        first = False


def _success_view(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "row_index": r["row_index"], "id": r["item_id"], "prompt": r["prompt"],
        "response": r["response"], "attempts": r["attempts"], "latency_ms": r["latency_ms"],
    }


def _error_view(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "row_index": r["row_index"], "id": r["item_id"], "error_type": r["error_type"],
        "message": r["error_message"], "http_status": r["http_status"], "attempts": r["attempts"],
    }
