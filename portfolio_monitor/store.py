"""SQLite-backed alert log + cooldown bookkeeping.

Two responsibilities:

1. **Cooldown gate** — before dispatching an alert, ask ``in_cooldown(symbol, rule)``.
   If True, suppress. Cooldowns are configurable in ``config.yaml``.
2. **Audit log** — every dispatched alert is recorded with timestamp, symbol,
   rule name, severity, and the JSON payload that drove it. Useful for backtests
   and for reviewing what the system has flagged.

Single-file SQLite. Lives at ``~/.portfolio_monitor/state.db`` by default.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

DEFAULT_DB_PATH = Path.home() / ".portfolio_monitor" / "state.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc       TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    rule         TEXT NOT NULL,
    severity     TEXT NOT NULL,
    title        TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_symbol_rule_ts
    ON alerts (symbol, rule, ts_utc DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_ts
    ON alerts (ts_utc DESC);
"""


@dataclass(frozen=True)
class AlertRecord:
    id: int
    ts_utc: datetime
    symbol: str
    rule: str
    severity: str
    title: str
    payload: dict[str, Any]


class Store:
    """Thin SQLite wrapper for cooldowns and the audit log."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def in_cooldown(self, symbol: str, rule: str, cooldown_days: int) -> bool:
        """True iff an alert for (symbol, rule) was logged within ``cooldown_days``."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=cooldown_days)).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM alerts WHERE symbol = ? AND rule = ? AND ts_utc >= ? LIMIT 1",
                (symbol.upper(), rule, cutoff),
            ).fetchone()
        return row is not None

    def record(
        self,
        *,
        symbol: str,
        rule: str,
        severity: str,
        title: str,
        payload: dict[str, Any],
        ts_utc: datetime | None = None,
    ) -> int:
        ts = (ts_utc or datetime.now(timezone.utc)).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO alerts (ts_utc, symbol, rule, severity, title, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, symbol.upper(), rule, severity, title, json.dumps(payload, default=str)),
            )
        return int(cur.lastrowid)

    def last_alert(self, symbol: str, rule: str) -> AlertRecord | None:
        """Return the most recent alert for (symbol, rule), or None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, ts_utc, symbol, rule, severity, title, payload_json "
                "FROM alerts WHERE symbol = ? AND rule = ? "
                "ORDER BY ts_utc DESC LIMIT 1",
                (symbol.upper(), rule),
            ).fetchone()
        if row is None:
            return None
        return AlertRecord(
            id=row["id"],
            ts_utc=datetime.fromisoformat(row["ts_utc"]),
            symbol=row["symbol"],
            rule=row["rule"],
            severity=row["severity"],
            title=row["title"],
            payload=json.loads(row["payload_json"]),
        )

    def recent(self, limit: int = 50) -> list[AlertRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, ts_utc, symbol, rule, severity, title, payload_json "
                "FROM alerts ORDER BY ts_utc DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            AlertRecord(
                id=r["id"],
                ts_utc=datetime.fromisoformat(r["ts_utc"]),
                symbol=r["symbol"],
                rule=r["rule"],
                severity=r["severity"],
                title=r["title"],
                payload=json.loads(r["payload_json"]),
            )
            for r in rows
        ]

    def prune(self, retain_days: int) -> int:
        """Drop alerts older than retain_days. Returns rows deleted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retain_days)).isoformat()
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM alerts WHERE ts_utc < ?", (cutoff,))
            return cur.rowcount
