from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class SpoolItem:
    id: int
    payload: dict[str, Any]


class TelemetrySpool:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS heartbeat_spool (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def enqueue(self, payload: dict[str, Any]) -> None:
        payload_json = json.dumps(payload, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO heartbeat_spool (payload_json) VALUES (?)",
                (payload_json,),
            )

    def pending(self, limit: int = 25) -> Iterable[SpoolItem]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, payload_json FROM heartbeat_spool ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()

        for item_id, payload_json in rows:
            yield SpoolItem(id=item_id, payload=json.loads(payload_json))

    def delete(self, item_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM heartbeat_spool WHERE id = ?", (item_id,))
