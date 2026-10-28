import httpx
import pytest

from batch_engine.config import Settings
from batch_engine.models import JobRequest, RetryPolicyOverride
from batch_engine.retry import RetryPolicy
from batch_engine.webhook import send_webhook
from tests.conftest import FakeClock


class TestSettings:
    def test_env_overrides_and_separate_retry_policies(self, monkeypatch, tmp_path):
        monkeypatch.setenv("INFERENCE_MODEL", "tiny")
        monkeypatch.setenv("MAX_IN_FLIGHT", "5")
        monkeypatch.setenv("RETRY_429_MAX_RETRIES", "11")
        monkeypatch.setenv("RETRY_5XX_MAX_DELAY_S", "2.5")
        s = Settings.from_env(dotenv=None)
        assert (s.inference_model, s.max_in_flight) == ("tiny", 5)
        assert s.rate_limit_policy == RetryPolicy(11, 1.0, 30.0)
        assert s.server_error_policy == RetryPolicy(3, 0.5, 2.5)

    def test_dotenv_fills_missing_but_never_overrides_real_env(self, monkeypatch, tmp_path):
        monkeypatch.delenv("INFERENCE_API_KEY", raising=False)
        monkeypatch.setenv("INFERENCE_MODEL", "from-env")
        dotenv = tmp_path / ".env"
        dotenv.write_text("# comment\nINFERENCE_API_KEY='secret'\nINFERENCE_MODEL=from-file\nnot a pair\n")
        s = Settings.from_env(dotenv=dotenv)
        assert s.inference_api_key == "secret" and s.inference_model == "from-env"
        monkeypatch.delenv("INFERENCE_API_KEY")

    def test_defaults(self, monkeypatch):
        for var in ("INFERENCE_MODEL", "CHUNK_SIZE", "RETRY_429_MAX_RETRIES"):
            monkeypatch.delenv(var, raising=False)
        s = Settings.from_env(dotenv=None)
        assert s.chunk_size == 25 and s.rate_limit_policy.max_retries == 8 and s.server_error_policy.max_retries == 3


class TestRequestModel:
    def test_partial_override_merges_with_defaults(self):
        merged = RetryPolicyOverride(max_retries=1).apply(RetryPolicy(8, 1.0, 30.0))
        assert merged == RetryPolicy(1, 1.0, 30.0)

    @pytest.mark.parametrize("body", [{"chunk_size": 0}, {"webhook_url": "ftp://x"},
                                      {"retry": {"rate_limit": {"max_retries": -1}}}, {"input_path": ""}])
    def test_invalid_requests_rejected(self, body):
        with pytest.raises(ValueError):
            JobRequest(**body)


class TestWebhook:
    async def test_delivers_payload(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            assert await send_webhook(http, "http://hook.test/cb", {"job_id": "j"})
        assert seen[0].method == "POST" and seen[0].read() == b'{"job_id":"j"}'

    async def test_retries_5xx_then_succeeds(self):
        statuses = iter([503, 500, 200])
        clock = FakeClock()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(next(statuses)))) as http:
            assert await send_webhook(http, "http://hook.test", {}, sleep=clock.sleep)
        assert clock.sleeps == [1, 2]

    async def test_gives_up_after_attempts(self):
        clock = FakeClock()

        def boom(request):
            raise httpx.ConnectError("down", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as http:
            assert not await send_webhook(http, "http://hook.test", {}, attempts=3, sleep=clock.sleep)
        assert len(clock.sleeps) == 2

    async def test_4xx_is_not_retried(self):
        clock = FakeClock()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as http:
            assert not await send_webhook(http, "http://hook.test", {}, sleep=clock.sleep)
        assert clock.sleeps == []
