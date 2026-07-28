from __future__ import annotations

import os
import sqlite3
from pathlib import Path


class LocalDeliveryState:
    def __init__(self, path: Path | None = None):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "XianyuConnector" / "data"
        self.path = path or base / "delivery.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS deliveries (global_message_id TEXT PRIMARY KEY, status TEXT NOT NULL)"
            )

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def is_sent(self, global_message_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM deliveries WHERE global_message_id = ?", (global_message_id,)
            ).fetchone()
        return bool(row and row[0] == "sent")

    def mark(self, global_message_id: str, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO deliveries(global_message_id, status) VALUES(?, ?) "
                "ON CONFLICT(global_message_id) DO UPDATE SET status=excluded.status",
                (global_message_id, status),
            )
