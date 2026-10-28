from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from batch_engine.retry import (
    MAX_RETRY_AFTER_S,
    ErrorClass,
    RetryPolicy,
    classify_status,
    parse_retry_after,
    retry_after_delay,
)

POLICY = RetryPolicy(max_retries=5, base_delay_s=1.0, max_delay_s=10.0, multiplier=2.0)


class TestBackoff:
    @pytest.mark.parametrize("retry, ceiling", [(0, 1.0), (1, 2.0), (2, 4.0), (3, 8.0), (4, 10.0), (10, 10.0)])
    def test_ceiling_grows_exponentially_then_caps(self, retry, ceiling):
        assert POLICY.backoff(retry, rng=lambda: 1.0) == pytest.approx(ceiling)

    def test_full_jitter_lower_bound_is_zero(self):
        assert POLICY.backoff(3, rng=lambda: 0.0) == 0.0

    def test_jitter_scales_ceiling(self):
        assert POLICY.backoff(2, rng=lambda: 0.25) == pytest.approx(1.0)

    def test_random_delays_stay_within_bounds(self):
        for retry in range(8):
            for _ in range(200):
                assert 0.0 <= POLICY.backoff(retry) <= POLICY.max_delay_s

    def test_custom_multiplier(self):
        p = RetryPolicy(max_retries=3, base_delay_s=0.5, max_delay_s=100, multiplier=3)
        assert p.backoff(2, rng=lambda: 1.0) == pytest.approx(4.5)

    def test_merged_applies_only_non_none(self):
        merged = POLICY.merged(max_retries=1, base_delay_s=None)
        assert merged == RetryPolicy(max_retries=1, base_delay_s=1.0, max_delay_s=10.0, multiplier=2.0)


class TestClassify:
    def test_429_is_rate_limited(self):
        assert classify_status(429) is ErrorClass.RATE_LIMITED

    @pytest.mark.parametrize("status", [None, 408, 500, 502, 503, 504, 599])
    def test_transient_server_errors(self, status):
        assert classify_status(status) is ErrorClass.SERVER_ERROR

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
    def test_other_client_errors_are_not_retried(self, status):
        assert classify_status(status) is ErrorClass.NON_RETRYABLE


class TestRetryAfter:
    @pytest.mark.parametrize("value, expected", [("0", 0.0), ("3", 3.0), (" 2.5 ", 2.5), ("-4", 0.0)])
    def test_delta_seconds(self, value, expected):
        assert parse_retry_after(value) == expected

    @pytest.mark.parametrize("value", [None, "", "   ", "soon", "nan"])
    def test_missing_or_garbage_returns_none(self, value):
        assert parse_retry_after(value) is None

    def test_http_date(self):
        now = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)
        header = format_datetime(now + timedelta(seconds=7), usegmt=True)
        assert parse_retry_after(header, now=now) == pytest.approx(7.0)

    def test_http_date_in_the_past_is_zero(self):
        now = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)
        header = format_datetime(now - timedelta(minutes=5), usegmt=True)
        assert parse_retry_after(header, now=now) == 0.0

    def test_huge_values_are_capped(self):
        assert parse_retry_after("999999") == MAX_RETRY_AFTER_S
        assert parse_retry_after("inf") == MAX_RETRY_AFTER_S

    def test_retry_after_delay_adds_up_to_ten_percent(self):
        assert retry_after_delay(10, rng=lambda: 0.0) == 10
        assert retry_after_delay(10, rng=lambda: 1.0) == pytest.approx(11)
