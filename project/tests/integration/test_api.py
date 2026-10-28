"""API tier. Part 1 replaces the worker tier with a fake runner; part 2 runs the real
stack end to end with only the LLM provider faked."""

import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from batch_engine.api import create_app
from batch_engine.models import COMPLETED_WITH_ERRORS, FAILED, SUCCEEDED, ResultRecord
from tests.conftest import FakeProvider, prompts, write_input


class FakeRunner:
    """Stands in for the worker tier: records calls and writes canned results."""

    def __init__(self, store):
        self.store = store
        self.calls = []
        self.release = asyncio.Event()
        self.block = False

    async def run(self, job_id, input_path, config):
        self.calls.append((job_id, input_path, config))
        await asyncio.to_thread(self.store.mark_running, job_id)
        if self.block:
            await self.release.wait()
        records = [
            ResultRecord(0, "a", SUCCEEDED, prompt="q0", response="r0", attempts=1, latency_ms=3),
            ResultRecord(1, "b", FAILED, error_type="invalid_input", error_message="missing 'prompt'"),
            ResultRecord(2, "c", SUCCEEDED, prompt="q2", response="r2", attempts=2, latency_ms=9),
        ]
        await asyncio.to_thread(self.store.add_rows_read, job_id, 3)
        await asyncio.to_thread(self.store.set_total, job_id, 3)
        await asyncio.to_thread(self.store.write_results, job_id, records, 1, 0)
        await asyncio.to_thread(self.store.finish_job, job_id, COMPLETED_WITH_ERRORS)


@pytest.fixture
def fake_api(settings):
    write_input(settings.data_dir / "sample_batch.json", prompts(3))
    holder = {}

    def factory(store, inference, s, hook_http):
        holder["runner"] = FakeRunner(store)
        return holder["runner"]

    with TestClient(create_app(settings, runner_factory=factory)) as client:
        yield client, holder["runner"]


def wait_for(client, job_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/job/{job_id}/status").json()
        if status["status"] not in ("pending", "running"):
            return status
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish: {status}")


class TestApiWithFakeWorker:
    def test_submit_returns_202_with_links_immediately(self, fake_api):
        client, runner = fake_api
        runner.block = True
        started = time.monotonic()
        resp = client.post("/job", json={})
        assert time.monotonic() - started < 1.0
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "pending" and len(body["job_id"]) == 32
        assert body["status_url"] == f"/job/{body['job_id']}/status"
        assert body["download_url"] == f"/job/{body['job_id']}/download"
        runner.release.set()

    def test_no_body_uses_the_default_sample(self, fake_api):
        client, runner = fake_api
        job_id = client.post("/job").json()["job_id"]
        wait_for(client, job_id)
        assert runner.calls[0][1].name == "sample_batch.json"

    def test_request_overrides_reach_the_job_config(self, fake_api, settings):
        client, runner = fake_api
        body = {"model": "m2", "chunk_size": 7, "webhook_url": "https://hooks.example.com/x",
                "retry": {"rate_limit": {"max_retries": 20}, "server_error": {"max_delay_s": 1}}}
        wait_for(client, client.post("/job", json=body).json()["job_id"])
        cfg = runner.calls[0][2]
        assert (cfg.model, cfg.chunk_size, cfg.webhook_url) == ("m2", 7, "https://hooks.example.com/x")
        assert cfg.rate_limit.max_retries == 20 and cfg.rate_limit.max_delay_s == settings.rate_limit_policy.max_delay_s
        assert cfg.server_error.max_delay_s == 1 and cfg.server_error.max_retries == settings.server_error_policy.max_retries

    def test_status_while_running_and_download_conflict(self, fake_api):
        client, runner = fake_api
        runner.block = True
        job_id = client.post("/job", json={}).json()["job_id"]
        deadline = time.monotonic() + 5
        while client.get(f"/job/{job_id}/status").json()["status"] != "running":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        resp = client.get(f"/job/{job_id}/download")
        assert resp.status_code == 409 and resp.json()["status"]["status"] == "running"
        runner.release.set()
        assert wait_for(client, job_id)["status"] == COMPLETED_WITH_ERRORS

    def test_status_payload_shape(self, fake_api):
        client, _ = fake_api
        status = wait_for(client, client.post("/job", json={}).json()["job_id"])
        assert {k: status[k] for k in ("rows_read", "total", "ingestion_complete", "processed", "succeeded", "failed")} \
            == {"rows_read": 3, "total": 3, "ingestion_complete": True, "processed": 3, "succeeded": 2, "failed": 1}
        assert status["retries"] == {"rate_limited": 1, "server_error": 0}

    def test_download_report_format(self, fake_api):
        client, _ = fake_api
        job_id = client.post("/job", json={}).json()["job_id"]
        wait_for(client, job_id)
        resp = client.get(f"/job/{job_id}/download")
        assert resp.status_code == 200 and resp.headers["content-type"] == "application/json"
        report = resp.json()
        assert report["summary"] == {"total": 3, "succeeded": 2, "failed": 1}
        assert [r["id"] for r in report["results"]] == ["a", "c"]
        assert report["results"][1] == {"row_index": 2, "id": "c", "prompt": "q2", "response": "r2",
                                        "attempts": 2, "latency_ms": 9}
        assert report["errors"] == [{"row_index": 1, "id": "b", "error_type": "invalid_input",
                                     "message": "missing 'prompt'", "http_status": None, "attempts": 0}]

    @pytest.mark.parametrize("path", ["../secrets.json", "/etc/passwd", "sub/../../x.json"])
    def test_paths_outside_data_dir_are_rejected(self, fake_api, path):
        client, _ = fake_api
        assert client.post("/job", json={"input_path": path}).status_code == 400

    def test_missing_input_file_is_404(self, fake_api):
        client, _ = fake_api
        assert client.post("/job", json={"input_path": "nope.json"}).status_code == 404

    @pytest.mark.parametrize("body", [{"chunk_size": 0}, {"webhook_url": "not a url"}, {"retry": {"rate_limit": {"max_retries": 99}}}])
    def test_invalid_bodies_are_422(self, fake_api, body):
        client, _ = fake_api
        assert client.post("/job", json=body).status_code == 422

    def test_unknown_job_is_404(self, fake_api):
        client, _ = fake_api
        assert client.get("/job/nope/status").status_code == 404
        assert client.get("/job/nope/download").status_code == 404

    def test_healthz(self, fake_api):
        client, _ = fake_api
        assert client.get("/healthz").json()["status"] == "ok"


class TestEndToEnd:
    def test_full_stack_with_flaky_provider(self, settings):
        def rule(prompt, n):
            if prompt == "prompt number 13":
                return 500
            return 429 if n == 1 and prompt.endswith("7") else 200

        provider = FakeProvider(rule, retry_after="0")
        data = prompts(120) + [{"id": "broken"}]
        write_input(settings.data_dir / "batch.json", data)
        hooks = []
        hook_transport = httpx.MockTransport(lambda r: hooks.append(json.loads(r.content)) or httpx.Response(200))
        app = create_app(settings, transport=provider.transport(), webhook_transport=hook_transport)
        with TestClient(app) as client:
            job_id = client.post("/job", json={"input_path": "batch.json", "webhook_url": "http://hooks.test/x"}).json()["job_id"]
            status = wait_for(client, job_id)
            report = client.get(f"/job/{job_id}/download").json()

        assert status["status"] == COMPLETED_WITH_ERRORS
        assert (status["total"], status["succeeded"], status["failed"]) == (121, 119, 2)
        assert status["retries"]["rate_limited"] == 12
        assert len(report["results"]) == 119 and {e["id"] for e in report["errors"]} == {"p13", "broken"}
        assert [r["row_index"] for r in report["results"]] == sorted(r["row_index"] for r in report["results"])
        assert provider.requests[0]["headers"]["authorization"] == "Bearer test-key"
        assert hooks and hooks[0]["job_id"] == job_id and hooks[0]["status"] == COMPLETED_WITH_ERRORS

    def test_restart_marks_unfinished_jobs_interrupted(self, settings):
        from batch_engine.store import Store

        store = Store(settings.db_path)
        store.create_job("old", "x.json", {})
        store.mark_running("old")
        write_input(settings.data_dir / "sample_batch.json", prompts(1))
        with TestClient(create_app(settings, transport=FakeProvider().transport())) as client:
            status = client.get("/job/old/status").json()
            assert status["status"] == "interrupted"
            assert client.get("/job/old/download").status_code == 200
