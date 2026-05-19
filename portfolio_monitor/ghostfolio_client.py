"""Ghostfolio REST API client (optional fallback / dashboard sync).

Phase 1 reads positions directly from SnapTrade so we don't depend on Ghostfolio
being up. This client exists for two reasons:

1. **Dashboard mirroring** — when SnapTrade returns fresh data, we can push it
   into Ghostfolio so the web UI at localhost:3333 stays in sync.
2. **Fallback path** — if SnapTrade is rate-limited or fails, the engine can
   fall back to reading the most recent Ghostfolio state.

Token comes from the Keychain. No password ever stored.
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import Any

import requests

from .secrets import SecretKey, get_secret

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:3333"

# Ghostfolio's import CSV column order (must match exactly).
_GHOSTFOLIO_CSV_FIELDS = [
    "Date", "Code", "Currency", "DataSource", "Fee",
    "Quantity", "Type", "UnitPrice", "AccountId",
]


class GhostfolioClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = get_secret(SecretKey.GHOSTFOLIO_API_TOKEN, required=False)
        self._session = requests.Session()
        if self._token:
            self._session.headers["Authorization"] = f"Bearer {self._token}"

    @property
    def is_configured(self) -> bool:
        return self._token is not None

    def health(self) -> bool:
        try:
            r = self._session.get(f"{self.base_url}/api/v1/health", timeout=3)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def get_holdings(self) -> list[dict[str, Any]]:
        r = self._session.get(f"{self.base_url}/api/v1/portfolio/holdings", timeout=10)
        r.raise_for_status()
        return r.json().get("holdings", [])

    def get_portfolio_summary(self) -> dict[str, Any]:
        r = self._session.get(f"{self.base_url}/api/v1/portfolio/details", timeout=10)
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ import

    def import_activities_csv(self, csv_path: Path) -> dict[str, Any]:
        """Upload a Ghostfolio-formatted CSV via the import API.

        Ghostfolio accepts a multipart/form-data POST to
        ``/api/v1/import`` with a single file field named ``file``.

        Returns the parsed JSON response (``{"activities": [...], "count": N}``).
        Raises ``requests.HTTPError`` on failure.
        """
        with csv_path.open("rb") as fh:
            r = self._session.post(
                f"{self.base_url}/api/v1/import",
                files={"file": (csv_path.name, fh, "text/csv")},
                timeout=30,
            )
        r.raise_for_status()
        return r.json()

    def import_activities(self, rows: list[dict[str, str]]) -> dict[str, Any]:
        """Upload a list of activity dicts (same shape as the CSV rows) via the API.

        Builds an in-memory CSV so no temp file is needed.
        Raises ``requests.HTTPError`` on failure.
        """
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=_GHOSTFOLIO_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        csv_bytes = buf.getvalue().encode()

        r = self._session.post(
            f"{self.base_url}/api/v1/import",
            files={"file": ("activities.csv", csv_bytes, "text/csv")},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
