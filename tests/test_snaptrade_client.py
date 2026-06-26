"""Tests for SnapTradeClient's retry behavior.

Regression coverage for the 2026-06-25 production failure: a stalled
connection caused urllib3's default Retry(total=3) to resend an
already-signed SnapTrade request, and by the time the resend reached the
server its PartnerTimestamp had gone stale, so SnapTrade rejected it with
401 "Invalid timestamp" — aborting that day's run entirely (no alerts).
"""

from __future__ import annotations

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
