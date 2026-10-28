"""Worker pool tier: runs one job as reader -> chunk queue -> workers -> result queue -> collector."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import Settings
from .inference import InferenceClient
from .ingest import InputFormatError, read_chunks
from .job_queue import InMemoryQueue
from .models import (
    COMPLETED,
    COMPLETED_WITH_ERRORS,
    FAILED,
    JOB_FAILED,
    SUCCEEDED,
    Chunk,
    ChunkResult,
    JobConfig,
    PromptItem,
    ResultRecord,
    status_payload,
)
from .store import Store
from .webhook import send_webhook

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RunReport:
    status: str
    chunk_queue_high_water: int
    result_queue_high_water: int
    webhook_delivered: bool | None = None


class JobRunner:
    def __init__(
        self,
        store: Store,
        inference: InferenceClient,
        settings: Settings,
        webhook_http: httpx.AsyncClient | None = None,
    ) -> None:
        self.store = store
        self.inference = inference
        self.settings = settings
        self.webhook_http = webhook_http

    async def run(self, job_id: str, input_path: Path, config: JobConfig) -> RunReport:
        s = self.settings
        await asyncio.to_thread(self.store.mark_running, job_id)
        chunks: InMemoryQueue[Chunk] = InMemoryQueue(s.queue_max_chunks)
        results: InMemoryQueue[ChunkResult] = InMemoryQueue(s.result_queue_max)
        ingest_error: str | None = None

        async def produce() -> None:
            nonlocal ingest_error
            total = 0
            try:
                async for chunk in read_chunks(input_path, config.chunk_size):
                    await chunks.put(chunk)
                    total += chunk.rows
                    await asyncio.to_thread(self.store.add_rows_read, job_id, chunk.rows)
                await asyncio.to_thread(self.store.set_total, job_id, total)
            except InputFormatError as exc:
                ingest_error = str(exc)
            except OSError as exc:
                ingest_error = f"cannot read input: {exc}"
            finally:
                await chunks.close(s.workers)

        async def work() -> None:
            while (chunk := await chunks.get()) is not None:
                await results.put(await self.process_chunk(chunk, config))

        async def collect() -> None:
            while (result := await results.get()) is not None:
                await asyncio.to_thread(
                    self.store.write_results, job_id, result.records, result.retries_429, result.retries_5xx
                )

        collector = asyncio.create_task(collect())
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(produce())
                for _ in range(s.workers):
                    tg.create_task(work())
        finally:
            await results.close(1)
            await collector

        job = await asyncio.to_thread(self.store.get_job, job_id)
        assert job is not None
        if ingest_error:
            status = JOB_FAILED
        elif job["failed"]:
            status = COMPLETED_WITH_ERRORS
        else:
            status = COMPLETED
        await asyncio.to_thread(self.store.finish_job, job_id, status, ingest_error)
        log.info("job %s finished: %s (%s ok, %s failed)", job_id, status, job["succeeded"], job["failed"])

        report = RunReport(status, chunks.high_water, results.high_water)
        if config.webhook_url and self.webhook_http is not None:
            final = await asyncio.to_thread(self.store.get_job, job_id)
            report.webhook_delivered = await send_webhook(self.webhook_http, config.webhook_url, status_payload(final))
        return report

    async def process_chunk(self, chunk: Chunk, config: JobConfig) -> ChunkResult:
        """Fire every prompt in the chunk concurrently; the shared limiter caps real concurrency."""
        records = await asyncio.gather(*(self._process_item(item, config) for item in chunk.items))
        result = ChunkResult(chunk_index=chunk.index, records=list(chunk.invalid))
        for record, retries_429, retries_5xx in records:
            result.records.append(record)
            result.retries_429 += retries_429
            result.retries_5xx += retries_5xx
        result.records.sort(key=lambda r: r.row_index)
        return result

    async def _process_item(self, item: PromptItem, config: JobConfig) -> tuple[ResultRecord, int, int]:
        try:
            out = await self.inference.complete(
                item.prompt, model=config.model, rate_limit=config.rate_limit, server_error=config.server_error
            )
        except Exception as exc:  # a bug must not take down the whole chunk
            log.exception("unexpected error processing row %s", item.row_index)
            return ResultRecord(item.row_index, item.item_id, FAILED, prompt=item.prompt,
                                error_type="internal_error", error_message=repr(exc)), 0, 0
        record = ResultRecord(
            row_index=item.row_index,
            item_id=item.item_id,
            status=SUCCEEDED if out.ok else FAILED,
            prompt=item.prompt,
            response=out.response,
            error_type=out.error_type,
            error_message=out.error_message,
            http_status=out.http_status,
            attempts=out.attempts,
            latency_ms=out.latency_ms,
        )
        return record, out.retries_429, out.retries_5xx
