"""One-time setup helper — interactively prompt for and store every credential.

Each value goes straight into the macOS Keychain under the ``portfolio-monitor``
service. Nothing is written to disk in plain text. Re-run safely to update.

Usage::

    python -m portfolio_monitor.scripts.bootstrap_keychain
"""

from __future__ import annotations

import getpass
import sys

from ..secrets import SecretKey, get_secret, set_secret

# (key, prompt label, secret?, optional?)
PROMPTS: list[tuple[SecretKey, str, bool, bool]] = [
    (SecretKey.SNAPTRADE_CLIENT_ID, "SnapTrade Client ID", False, False),
    (SecretKey.SNAPTRADE_CONSUMER_KEY, "SnapTrade Consumer Key", True, False),
    (SecretKey.SNAPTRADE_USER_ID, "SnapTrade User ID", False, False),
    (SecretKey.SNAPTRADE_USER_SECRET, "SnapTrade User Secret", True, False),
    (SecretKey.GMAIL_ADDRESS, "Gmail address (sender)", False, False),
    (SecretKey.GMAIL_APP_PASSWORD, "Gmail App Password (16 chars)", True, False),
    (SecretKey.GHOSTFOLIO_API_TOKEN, "Ghostfolio API token (optional)", True, True),
    (SecretKey.PUSHOVER_USER_KEY, "Pushover user key (optional)", False, True),
    (SecretKey.PUSHOVER_APP_TOKEN, "Pushover app token (optional)", True, True),
    (SecretKey.WISDOMHATCH_EMAIL, "WisdomHatch login email", False, False),
    (SecretKey.WISDOMHATCH_PASSWORD, "WisdomHatch password", True, False),
]


def main() -> int:
    print("Portfolio Monitor — Keychain bootstrap")
    print("Press Enter to keep the current value (if any). Optional fields can be left blank.\n")

    for key, label, is_secret, optional in PROMPTS:
        existing = get_secret(key, required=False)
        marker = "[set]" if existing else "[unset]"
        prompt = f"  {label} {marker}: "
        value = getpass.getpass(prompt) if is_secret else input(prompt)
        value = value.strip()

        if not value:
            if optional and not existing:
                continue
            if existing:
                continue
            print(f"  → {label} is required; aborting.")
            return 1

        set_secret(key, value)
        print(f"  → stored.")

    print("\nDone. Run `python -m portfolio_monitor.scripts.run_once --dry-run` to test.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
