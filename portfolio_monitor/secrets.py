"""macOS Keychain wrapper.

All credentials live under the keychain service ``portfolio-monitor``. Nothing is
ever written to disk in plain text. Failed reads raise :class:`SecretMissing` so
the caller can prompt the user to run the bootstrap script.

Usage::

    from portfolio_monitor.secrets import get_secret, set_secret, SecretKey

    token = get_secret(SecretKey.SNAPTRADE_CLIENT_ID)
"""

from __future__ import annotations

from enum import Enum

import keyring

SERVICE_NAME = "portfolio-monitor"


class SecretKey(str, Enum):
    """Stable names for every credential stored in the Keychain."""

    SNAPTRADE_CLIENT_ID = "snaptrade_client_id"
    SNAPTRADE_CONSUMER_KEY = "snaptrade_consumer_key"
    SNAPTRADE_USER_ID = "snaptrade_user_id"
    SNAPTRADE_USER_SECRET = "snaptrade_user_secret"
    GMAIL_ADDRESS = "gmail_address"
    GMAIL_APP_PASSWORD = "gmail_app_password"
    GHOSTFOLIO_API_TOKEN = "ghostfolio_api_token"
    PUSHOVER_USER_KEY = "pushover_user_key"  # optional
    PUSHOVER_APP_TOKEN = "pushover_app_token"  # optional
    COPYTRADE_EMAIL = "copytrade_email"
    COPYTRADE_PASSWORD = "copytrade_password"


class SecretMissing(RuntimeError):
    """Raised when a required secret is not present in the Keychain."""


def get_secret(key: SecretKey, *, required: bool = True) -> str | None:
    """Read a secret from the macOS Keychain.

    Parameters
    ----------
    key:
        Which credential to fetch.
    required:
        When True (default), raises :class:`SecretMissing` if the key is absent.
        Set False for optional credentials (Pushover, etc.) so callers can
        gracefully skip features.
    """
    value = keyring.get_password(SERVICE_NAME, key.value)
    if value is None and required:
        raise SecretMissing(
            f"Missing keychain entry: {SERVICE_NAME}/{key.value}. "
            "Run `python -m portfolio_monitor.scripts.bootstrap_keychain`."
        )
    return value


def set_secret(key: SecretKey, value: str) -> None:
    """Write a secret to the Keychain. Used only by the bootstrap script."""
    keyring.set_password(SERVICE_NAME, key.value, value)


def delete_secret(key: SecretKey) -> None:
    """Remove a secret. Best-effort — silently ignores missing entries."""
    try:
        keyring.delete_password(SERVICE_NAME, key.value)
    except keyring.errors.PasswordDeleteError:
        pass
