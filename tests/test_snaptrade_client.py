"""Tests for SnapTradeClient's retry behavior.

Regression coverage for two related production failures:

- 2026-06-25: a stalled connection caused urllib3's default Retry(total=3)
  to resend an already-signed SnapTrade request, and by the time the resend
  reached the server its PartnerTimestamp had gone stale, so SnapTrade
  rejected it with 401 "Invalid timestamp" — aborting that day's run
  entirely (no alerts).
- 2026-06-26: the SDK's rest.py always explicitly passes timeout=None to
  urllib3 (since the generated API methods never supply one), which
  urllib3 treats as "block forever" — overriding any pool-level default.
  This hung the daily run for 20+ minutes with no error and no alerts;
  even run_guarded.py's hard SIGALRM safety net couldn't interrupt it
  (signals don't get delivered mid-blocking-syscall in a C extension).
"""

from __future__ import annotations

import urllib3
from unittest.mock import MagicMock, patch

import pytest

from portfolio_monitor.snaptrade_client import SnapTradeClient


class TestWithRetry:
    def test_returns_result_on_first_success(self):
        call = MagicMock(return_value="ok")
        result = SnapTradeClient._with_retry(call, attempts=3)
        assert result == "ok"
        assert call.call_count == 1

    def test_retries_by_reinvoking_the_callable_fresh(self):
        """Each retry must call the SDK method again from scratch (so it
        re-signs with a current timestamp), not resend a stored response."""
        call = MagicMock(side_effect=[RuntimeError("stall"), "ok"])
        with patch("portfolio_monitor.snaptrade_client.time.sleep"):
            result = SnapTradeClient._with_retry(call, attempts=3)
        assert result == "ok"
        assert call.call_count == 2

    def test_raises_after_exhausting_attempts(self):
        call = MagicMock(side_effect=RuntimeError("still stale"))
        with patch("portfolio_monitor.snaptrade_client.time.sleep"):
            with pytest.raises(RuntimeError, match="still stale"):
                SnapTradeClient._with_retry(call, attempts=3)
        assert call.call_count == 3

    def test_does_not_retry_beyond_configured_attempts(self):
        call = MagicMock(side_effect=RuntimeError("x"))
        with patch("portfolio_monitor.snaptrade_client.time.sleep"):
            with pytest.raises(RuntimeError):
                SnapTradeClient._with_retry(call, attempts=1)
        assert call.call_count == 1


class TestInitDisablesSdkBlindRetry:
    def test_sdk_internal_retries_are_disabled(self):
        """The SDK's urllib3-level Retry(total=3) must be turned off so a
        stalled connection never blindly resends a stale-signed request —
        our own _with_retry() handles retries by re-signing fresh instead."""
        with patch("portfolio_monitor.snaptrade_client.get_secret", return_value="fake"):
            client = SnapTradeClient()
        assert client._client.account_information.api_client.configuration.retries == 0


class TestInitInjectsRealTimeout:
    """The SDK's rest.py always explicitly passes timeout=None to
    pool_manager.request() (since the generated API methods never supply
    one) — and urllib3 treats an explicit None as "block forever", ignoring
    any pool-level connection_pool_kw default. __init__ must wrap
    pool_manager.request itself to substitute a real timeout whenever the
    SDK passes None, confirmed by testing against a non-routable IP: the
    call hung indefinitely without this wrapper, and failed cleanly within
    ~65-125s with it.
    """

    def test_explicit_none_timeout_is_replaced_with_a_real_timeout(self):
        captured = {}

        def fake_request(self, method, url, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            raise RuntimeError("stop before any real network call")

        with patch.object(urllib3.PoolManager, "request", fake_request):
            with patch("portfolio_monitor.snaptrade_client.get_secret", return_value="fake"):
                client = SnapTradeClient()
            pool_manager = client._client.account_information.api_client.rest_client.pool_manager
            with pytest.raises(RuntimeError, match="stop before any real network call"):
                pool_manager.request("GET", "https://example.com", timeout=None)

        assert isinstance(captured["timeout"], urllib3.Timeout)
        assert captured["timeout"].connect_timeout == pytest.approx(10)
        assert captured["timeout"].read_timeout == pytest.approx(60)

    def test_caller_supplied_timeout_is_left_alone(self):
        """If anything ever does pass an explicit timeout, the wrapper must
        not clobber it."""
        captured = {}

        def fake_request(self, method, url, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            raise RuntimeError("stop before any real network call")

        with patch.object(urllib3.PoolManager, "request", fake_request):
            with patch("portfolio_monitor.snaptrade_client.get_secret", return_value="fake"):
                client = SnapTradeClient()
            pool_manager = client._client.account_information.api_client.rest_client.pool_manager
            with pytest.raises(RuntimeError, match="stop before any real network call"):
                pool_manager.request("GET", "https://example.com", timeout=5)

        assert captured["timeout"] == 5
