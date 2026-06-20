"""Load manual tax lot data from lots.yaml.

Used as a fallback when SnapTrade's transactions endpoint returns HTTP 410
(gated behind paid tier for accounts created after April 2026).
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import yaml

from .portfolio_types import Lot

log = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).parent.parent / "lots.yaml"


def load_lots(path: Path | None = None) -> dict[str, list[Lot]]:
    """Return manually recorded lots keyed by symbol (upper-case).

    Returns an empty dict if the file doesn't exist — callers treat that as
    "no manual data available" and fall back to whatever they had before.
    """
    p = path or _DEFAULT_PATH
    if not p.exists():
        return {}

    try:
        raw = yaml.safe_load(p.read_text()) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to parse %s: %s", p, exc)
        return {}

    result: dict[str, list[Lot]] = {}
    for symbol, entries in raw.items():
        if not entries or not isinstance(entries, list):
            continue
        lots: list[Lot] = []
        for entry in entries:
            try:
                acquired = date.fromisoformat(str(entry["acquired"]))
                lots.append(Lot(
                    symbol=symbol.upper(),
                    quantity=float(entry["quantity"]),
                    cost_basis_per_share=float(entry["cost_per_share"]),
                    acquired_on=acquired,
                ))
            except Exception as exc:  # noqa: BLE001
                log.warning("Skipping malformed lot entry for %s: %s — %s", symbol, entry, exc)
        if lots:
            result[symbol.upper()] = lots

    log.debug("Loaded manual lots for %d symbol(s) from %s", len(result), p)
    return result
