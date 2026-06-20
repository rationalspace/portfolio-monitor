"""SQLite persistence for Second Opinion results.

One row per request. Lives at the repo root as ``second_opinions.db`` —
already covered by the blanket ``*.db`` exclude in ``.git/info/exclude``.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "second_opinions.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS second_opinions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol           TEXT NOT NULL,
    rule             TEXT NOT NULL,
    alert_date       TEXT,
    requested_at     TEXT NOT NULL,
    prompt           TEXT NOT NULL,
    response_markdown TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_second_opinions_symbol_ts
    ON second_opinions (symbol, requested_at DESC);
"""


@dataclass(frozen=True)
class SecondOpinionRecord:
    id: int
    symbol: str
    rule: str
    alert_date: str | None
    requested_at: datetime
    prompt: str
    response_markdown: str


class SecondOpinionStore:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
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

    def record(
        self,
        *,
        symbol: str,
        rule: str,
        alert_date: str | None,
        prompt: str,
        response_markdown: str,
    ) -> SecondOpinionRecord:
        requested_at = datetime.now(timezone.utc)
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO second_opinions "
                "(symbol, rule, alert_date, requested_at, prompt, response_markdown) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (symbol.upper(), rule, alert_date, requested_at.isoformat(), prompt, response_markdown),
            )
        return SecondOpinionRecord(
            id=int(cur.lastrowid),
            symbol=symbol.upper(),
            rule=rule,
            alert_date=alert_date,
            requested_at=requested_at,
            prompt=prompt,
            response_markdown=response_markdown,
        )

    def get(self, record_id: int) -> SecondOpinionRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM second_opinions WHERE id = ?", (record_id,)
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def recent_for_symbol(self, symbol: str, limit: int = 2) -> list[SecondOpinionRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM second_opinions WHERE symbol = ? "
                "ORDER BY requested_at DESC LIMIT ?",
                (symbol.upper(), limit),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def all_history(self, limit: int = 200) -> list[SecondOpinionRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM second_opinions ORDER BY requested_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def history_for_symbol(self, symbol: str, limit: int = 100) -> list[SecondOpinionRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM second_opinions WHERE symbol = ? "
                "ORDER BY requested_at DESC LIMIT ?",
                (symbol.upper(), limit),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> SecondOpinionRecord:
        return SecondOpinionRecord(
            id=row["id"],
            symbol=row["symbol"],
            rule=row["rule"],
            alert_date=row["alert_date"],
            requested_at=datetime.fromisoformat(row["requested_at"]),
            prompt=row["prompt"],
            response_markdown=row["response_markdown"],
        )
