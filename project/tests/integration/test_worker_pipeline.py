"""Worker tier end to end: real ingestion, queue, workers, collector and SQLite.
Only the external LLM provider is faked."""

import asyncio
import json

import httpx
import pytest

from batch_engine.inference import InferenceClient, RateLimitGate
from batch_engine.models import FAILED, SUCCEEDED, JobConfig
from batch_engine.retry import RetryPolicy
from batch_engine.store import Store
from batch_engine.worker import JobRunner
from tests.conftest import FAST_5XX, FAST_429, FakeProvider, prompts, write_input


@pytest.fixture
def harness(settings):
    created = []

    def build(provider: FakeProvider, *, hook_transport=None, s=settings):
        store = Store(s.db_path)
        http = httpx.AsyncClient(base_url=s.inference_base_url, transport=provider.transport())
        hook_http = httpx.AsyncClient(transport=hook_transport) if hook_transport else None
        created.extend(c for c in (http, hook_http) if c)
        inference = InferenceClient(http, model="cheap", max_tokens=16,
                                    limiter=asyncio.Semaphore(s.max_in_flight), gate=RateLimitGate())
        return store, JobRunner(store, inference, s, hook_http)

    yield build


def config(chunk_size=10, webhook_url=None, r429=FAST_429, r5xx=FAST_5XX):
    return JobConfig(model="cheap", chunk_size=chunk_size, rate_limit=r429, server_error=r5xx, webhook_url=webhook_url)


async def run_job(store, runner, path, cfg, job_id="job1"):
    store.create_job(job_id, path.name, cfg.to_dict())
    report = await runner.run(job_id, path, cfg)
    return report, store.get_job(job_id)


def rows(store, status, job_id="job1"):
    return [r for batch in store.iter_results(job_id, status) for r in batch]


async def test_1000_prompts_all_succeed_with_bounded_queues(harness, settings, tmp_path):
    provider = FakeProvider(latency=0.001)
    store, runner = harness(provider)
    path = write_input(tmp_path / "in.json", prompts(1000))
    report, job = await run_job(store, runner, path, config())
    assert report.status == "completed"
    assert (job["total"], job["rows_read"], job["succeeded"], job["failed"]) == (1000, 1000, 1000, 0)
    assert provider.total_calls == 1000
    assert [r["row_index"] for r in rows(store, SUCCEEDED)] == list(range(1000))
    assert report.chunk_queue_high_water <= settings.queue_max_chunks
    assert report.result_queue_high_water <= settings.result_queue_max
    assert provider.max_in_flight <= settings.max_in_flight


async def test_429_storm_drops_nothing(harness, tmp_path):
    provider = FakeProvider(lambda p, n: 429 if n <= 3 else 200, retry_after="0")
    store, runner = harness(provider)
    path = write_input(tmp_path / "in.json", prompts(200))
    report, job = await run_job(store, runner, path, config())
    assert report.status == "completed" and job["succeeded"] == 200
    assert job["retries_429"] == 600 and provider.total_calls == 800


async def test_persistent_500s_are_isolated_errors(harness, tmp_path):
    poisoned = {"prompt number 7", "prompt number 42"}
    provider = FakeProvider(lambda p, n: 500 if p in poisoned else 200)
    store, runner = harness(provider)
    path = write_input(tmp_path / "in.json", prompts(50))
    report, job = await run_job(store, runner, path, config())
    assert report.status == "completed_with_errors"
    assert (job["succeeded"], job["failed"], job["retries_5xx"]) == (48, 2, 2 * FAST_5XX.max_retries)
    errors = rows(store, FAILED)
    assert [(e["row_index"], e["error_type"], e["http_status"], e["attempts"]) for e in errors] == [
        (7, "server_error", 500, 4), (42, "server_error", 500, 4)
    ]


async def test_corrupt_rows_never_reach_the_provider(harness, tmp_path):
    provider = FakeProvider()
    store, runner = harness(provider)
    data = prompts(5) + [{"id": "bad"}, None, {"prompt": 3}] + prompts(2, start=5)
    path = write_input(tmp_path / "in.json", data)
    report, job = await run_job(store, runner, path, config(chunk_size=3))
    assert report.status == "completed_with_errors"
    assert provider.total_calls == 7 and (job["succeeded"], job["failed"], job["total"]) == (7, 3, 10)
    assert {e["row_index"]: e["error_type"] for e in rows(store, FAILED)} == {5: "invalid_input", 6: "invalid_input",
                                                                               7: "invalid_input"}


async def test_malformed_file_fails_job_but_keeps_processed_rows(harness, tmp_path):
    provider = FakeProvider()
    store, runner = harness(provider)
    path = tmp_path / "in.json"
    good = ",".join(json.dumps(p) for p in prompts(25))
    path.write_text(f"[{good}, {{\"prompt\": ")
    report, job = await run_job(store, runner, path, config(chunk_size=10))
    assert report.status == "failed" and "malformed JSON" in job["error"]
    assert job["succeeded"] >= 20 and job["total"] is None


async def test_non_array_input_fails_cleanly(harness, tmp_path):
    store, runner = harness(FakeProvider())
    path = tmp_path / "in.json"
    path.write_text('{"not": "an array"}')
    report, job = await run_job(store, runner, path, config())
    assert report.status == "failed" and job["succeeded"] == 0 and "JSON array" in job["error"]


async def test_empty_input_completes_with_zero_rows(harness, tmp_path):
    store, runner = harness(FakeProvider())
    report, job = await run_job(store, runner, write_input(tmp_path / "in.json", []), config())
    assert report.status == "completed" and job["total"] == 0


async def test_slow_prompt_does_not_stall_other_chunks(harness, settings, tmp_path):
    provider = FakeProvider(lambda p, n: 503 if p == "prompt number 0" and n <= 3 else 200)
    store, runner = harness(provider)
    slow = RetryPolicy(max_retries=3, base_delay_s=0.05, max_delay_s=0.05)
    path = write_input(tmp_path / "in.json", prompts(300))
    report, job = await run_job(store, runner, path, config(r5xx=slow))
    assert report.status == "completed" and job["succeeded"] == 300


async def test_webhook_receives_final_status(harness, tmp_path):
    received = []

    def hook(request):
        received.append(json.loads(request.content))
        return httpx.Response(204)

    store, runner = harness(FakeProvider(), hook_transport=httpx.MockTransport(hook))
    path = write_input(tmp_path / "in.json", prompts(15))
    report, job = await run_job(store, runner, path, config(webhook_url="http://hooks.test/done"))
    assert report.webhook_delivered is True
    assert received[0]["job_id"] == "job1" and received[0]["status"] == "completed"
    assert received[0]["succeeded"] == 15


async def test_webhook_failure_does_not_change_job_outcome(harness, tmp_path):
    store, runner = harness(FakeProvider(), hook_transport=httpx.MockTransport(lambda r: httpx.Response(400)))
    path = write_input(tmp_path / "in.json", prompts(5))
    report, job = await run_job(store, runner, path, config(webhook_url="http://hooks.test/done"))
    assert report.webhook_delivered is False and job["status"] == "completed"


async def test_unexpected_inference_exception_is_isolated(harness, tmp_path):
    store, runner = harness(FakeProvider())

    async def explode(prompt, **kwargs):
        if prompt == "prompt number 2":
            raise RuntimeError("bug")
        return await original(prompt, **kwargs)

    original = runner.inference.complete
    runner.inference.complete = explode
    path = write_input(tmp_path / "in.json", prompts(5))
    report, job = await run_job(store, runner, path, config())
    assert report.status == "completed_with_errors"
    [error] = rows(store, FAILED)
    assert error["row_index"] == 2 and error["error_type"] == "internal_error"
