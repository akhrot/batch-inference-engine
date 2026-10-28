import asyncio

import httpx
import pytest

from batch_engine.inference import InferenceClient, RateLimitGate
from batch_engine.retry import RetryPolicy
from tests.conftest import FakeProvider

R429 = RetryPolicy(max_retries=3, base_delay_s=1.0, max_delay_s=8.0)
R5XX = RetryPolicy(max_retries=2, base_delay_s=0.5, max_delay_s=2.0)


def make_client(provider, clock, *, max_in_flight=4, rng=lambda: 1.0):
    http = httpx.AsyncClient(base_url="http://fake/v1", transport=provider.transport(),
                             headers={"Authorization": "Bearer k"})
    gate = RateLimitGate(clock=clock, sleep=clock.sleep)
    client = InferenceClient(http, model="cheap-model", max_tokens=16, limiter=asyncio.Semaphore(max_in_flight),
                             gate=gate, sleep=clock.sleep, rng=rng, clock=clock)
    return client, gate


async def complete(client, prompt="hello", r429=R429, r5xx=R5XX, **kw):
    return await client.complete(prompt, rate_limit=r429, server_error=r5xx, **kw)


async def test_success_returns_text_and_sends_openai_payload(clock):
    provider = FakeProvider()
    client, _ = make_client(provider, clock)
    out = await complete(client)
    assert out.ok and out.response == "echo: hello"
    assert (out.attempts, out.retries_429, out.retries_5xx, out.http_status) == (1, 0, 0, 200)
    req = provider.requests[0]
    assert req["url"] == "http://fake/v1/chat/completions"
    assert req["body"] == {"model": "cheap-model", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 16}
    assert req["headers"]["authorization"] == "Bearer k"


async def test_per_job_model_override(clock):
    provider = FakeProvider()
    client, _ = make_client(provider, clock)
    await complete(client, model="other-model")
    assert provider.requests[0]["body"]["model"] == "other-model"


async def test_429_then_success_uses_rate_limit_backoff(clock):
    provider = FakeProvider(lambda p, n: 429 if n <= 2 else 200)
    client, _ = make_client(provider, clock)
    out = await complete(client)
    assert out.ok and out.attempts == 3 and out.retries_429 == 2 and out.retries_5xx == 0
    assert clock.sleeps == [1.0, 2.0]  # base * 2^retry with rng=1.0


async def test_429_exhausts_its_budget(clock):
    provider = FakeProvider(lambda p, n: 429)
    client, _ = make_client(provider, clock)
    out = await complete(client)
    assert not out.ok and out.error_type == "rate_limited" and out.http_status == 429
    assert out.attempts == R429.max_retries + 1 and provider.total_calls == 4
    assert clock.sleeps == [1.0, 2.0, 4.0]


async def test_persistent_500_becomes_server_error(clock):
    provider = FakeProvider(lambda p, n: 500)
    client, _ = make_client(provider, clock)
    out = await complete(client)
    assert not out.ok and out.error_type == "server_error" and out.http_status == 500
    assert out.attempts == 3 and out.retries_5xx == 2
    assert clock.sleeps == [0.5, 1.0]  # uses the 5xx policy, not the 429 one


async def test_budgets_are_counted_separately(clock):
    sequence = {1: 429, 2: 429, 3: 503, 4: 429, 5: 502, 6: 200}
    provider = FakeProvider(lambda p, n: sequence[n])
    client, _ = make_client(provider, clock)
    out = await complete(client)  # 3 x 429 (limit 3) and 2 x 5xx (limit 2): both budgets exactly used
    assert out.ok and out.retries_429 == 3 and out.retries_5xx == 2 and out.attempts == 6


async def test_one_more_5xx_than_budget_fails_even_with_429_budget_left(clock):
    provider = FakeProvider(lambda p, n: {1: 500, 2: 429, 3: 500, 4: 500}.get(n, 200))
    client, _ = make_client(provider, clock)
    out = await complete(client)
    assert not out.ok and out.error_type == "server_error" and out.attempts == 4


@pytest.mark.parametrize("failure", ["timeout", "connect_error"])
async def test_transport_errors_follow_the_5xx_policy(clock, failure):
    provider = FakeProvider(lambda p, n: failure if n == 1 else 200)
    client, _ = make_client(provider, clock)
    out = await complete(client)
    assert out.ok and out.retries_5xx == 1


async def test_persistent_timeout_reports_no_status(clock):
    provider = FakeProvider(lambda p, n: "timeout")
    client, _ = make_client(provider, clock)
    out = await complete(client)
    assert not out.ok and out.error_type == "server_error" and out.http_status is None
    assert "timeout" in out.error_message


@pytest.mark.parametrize("status", [400, 401, 404, 422])
async def test_non_retryable_client_errors_fail_immediately(clock, status):
    provider = FakeProvider(lambda p, n: status)
    client, _ = make_client(provider, clock)
    out = await complete(client)
    assert not out.ok and out.error_type == "non_retryable" and out.http_status == status
    assert out.attempts == 1 and clock.sleeps == []


async def test_malformed_success_body_is_bad_response(clock):
    provider = FakeProvider(lambda p, n: "bad_json")
    client, _ = make_client(provider, clock)
    out = await complete(client)
    assert not out.ok and out.error_type == "bad_response" and out.attempts == 1


async def test_retry_after_on_429_is_honored_and_pauses_the_gate(clock):
    provider = FakeProvider(lambda p, n: 429 if n == 1 else 200, retry_after="5")
    client, gate = make_client(provider, clock, rng=lambda: 0.0)
    out = await complete(client)
    assert out.ok
    assert clock.sleeps[0] == pytest.approx(5.0)  # Retry-After, not the 1s backoff


async def test_retry_after_on_5xx_does_not_pause_the_gate(clock):
    provider = FakeProvider(lambda p, n: httpx.Response(503, headers={"Retry-After": "4"}) if n == 1 else 200)
    client, gate = make_client(provider, clock, rng=lambda: 0.0)
    clock_before = clock.now
    out = await complete(client)
    assert out.ok and clock.sleeps == [4.0]
    assert gate.remaining == 0 and clock.now - clock_before == pytest.approx(4.0)


async def test_gate_pause_blocks_other_requests(clock):
    provider = FakeProvider()
    client, gate = make_client(provider, clock)
    gate.pause(10)
    out = await complete(client)
    assert out.ok and clock.now >= 10 and clock.sleeps[0] == pytest.approx(10)


async def test_gate_pause_keeps_the_later_deadline(clock):
    gate = RateLimitGate(clock=clock, sleep=clock.sleep)
    gate.pause(10)
    gate.pause(3)
    assert gate.remaining == 10
    await gate.wait()
    assert gate.remaining == 0


async def test_limiter_caps_concurrent_requests():
    provider = FakeProvider(latency=0.01)
    http = httpx.AsyncClient(base_url="http://fake/v1", transport=provider.transport())
    client = InferenceClient(http, model="m", max_tokens=8, limiter=asyncio.Semaphore(3), gate=RateLimitGate())
    results = await asyncio.gather(*(complete(client, f"p{i}") for i in range(20)))
    assert all(r.ok for r in results)
    assert provider.max_in_flight == 3


async def test_slot_is_released_while_backing_off():
    """A request sleeping before a retry must not block others from using the slot."""
    provider = FakeProvider(lambda p, n: 500 if p == "slow" and n == 1 else 200)
    http = httpx.AsyncClient(base_url="http://fake/v1", transport=provider.transport())
    client = InferenceClient(http, model="m", max_tokens=8, limiter=asyncio.Semaphore(1), gate=RateLimitGate(),
                             rng=lambda: 1.0)
    slow_policy = RetryPolicy(max_retries=1, base_delay_s=0.2, max_delay_s=0.2)
    slow = asyncio.create_task(client.complete("slow", rate_limit=R429, server_error=slow_policy))
    await asyncio.sleep(0.02)
    fast = await asyncio.wait_for(complete(client, "fast"), timeout=0.15)
    assert fast.ok and not slow.done()
    assert (await slow).ok
