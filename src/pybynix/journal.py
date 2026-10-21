"""Bounded in-memory and durable SQLite order correlation journals.

No credentials are stored. A journal records intent before a non-idempotent send;
it cannot turn an uncorrelatable server response into exactly-once execution.
"""

import asyncio
import json
import sqlite3
import time
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from .errors import DuplicateOrderReference


class OrderJournal(Protocol):
    async def reserve(self, scope: str, client_ref: str, payload: dict) -> None: ...
    async def update(self, scope: str, client_ref: str, **fields) -> None: ...
    async def get(self, scope: str, client_ref: str) -> dict | None: ...
    async def unresolved(self, scope: str) -> list[dict]: ...
    async def update_trade(self, scope: str, trade_id: str, state: str) -> None: ...


class MemoryJournal:
    requires_identity = False

    def __init__(self, capacity=1000):
        if not isinstance(capacity, int) or capacity < 1:
            raise ValueError("Journal capacity must be positive")
        self.capacity = capacity
        self._rows = OrderedDict()
        self._lock = asyncio.Lock()

    async def reserve(self, scope, client_ref, payload):
        async with self._lock:
            key = (scope, client_ref)
            if key in self._rows:
                raise DuplicateOrderReference(client_ref)
            if len(self._rows) >= self.capacity:
                # Never evict an unresolved intent merely to make room.
                evict = next(
                    (k for k, v in self._rows.items() if v["state"] in {"settled", "rejected"}),
                    None,
                )
                if evict is None:
                    raise ValueError(
                        "Journal full; reconcile outstanding orders or use SQLiteJournal"
                    )
                self._rows.pop(evict)
            now = time.time()
            self._rows[key] = {
                "scope": scope,
                "client_ref": client_ref,
                "trade_id": None,
                "state": "submitting",
                "payload": dict(payload),
                "created_at": now,
                "updated_at": now,
            }

    async def update(self, scope, client_ref, **fields):
        async with self._lock:
            row = self._rows.get((scope, client_ref))
            if row is not None:
                row.update(fields, updated_at=time.time())

    async def get(self, scope, client_ref):
        async with self._lock:
            row = self._rows.get((scope, client_ref))
            return json.loads(json.dumps(row)) if row is not None else None

    async def unresolved(self, scope):
        async with self._lock:
            return json.loads(
                json.dumps(
                    [
                        v
                        for (s, _), v in self._rows.items()
                        if s == scope and v["state"] in {"submitting", "unknown", "accepted"}
                    ]
                )
            )

    async def update_trade(self, scope, trade_id, state):
        async with self._lock:
            for (owner, _), row in self._rows.items():
                if owner == scope and row.get("trade_id") == trade_id:
                    row.update(state=state, updated_at=time.time())


class SQLiteJournal:
    """Explicit local database for recovery across application restarts.

    File access runs in worker threads; SQLite transactions serialize reservations.
    Protect the database directory as it contains private trade metadata.
    """

    requires_identity = True

    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS orders (
                scope TEXT NOT NULL, client_ref TEXT NOT NULL, trade_id TEXT,
                state TEXT NOT NULL, payload TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY(scope, client_ref))""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    async def reserve(self, scope, client_ref, payload):
        def write():
            now = time.time()
            try:
                with self._connect() as db:
                    db.execute(
                        "INSERT INTO orders VALUES (?, ?, NULL, 'submitting', ?, ?, ?)",
                        (scope, client_ref, json.dumps(payload), now, now),
                    )
            except sqlite3.IntegrityError:
                raise DuplicateOrderReference(client_ref) from None

        await asyncio.to_thread(write)

    async def update(self, scope, client_ref, **fields):
        if not fields.keys() <= {"state", "trade_id"}:
            raise ValueError("Only state and trade_id may be updated")

        def write():
            with self._connect() as db:
                parts = [f"{key} = ?" for key in fields]
                db.execute(
                    f"UPDATE orders SET {', '.join(parts)}, updated_at = ? "
                    "WHERE scope = ? AND client_ref = ?",
                    (*fields.values(), time.time(), scope, client_ref),
                )

        if fields:
            await asyncio.to_thread(write)

    async def get(self, scope, client_ref):
        def read():
            with self._connect() as db:
                return self._decode(
                    db.execute(
                        "SELECT * FROM orders WHERE scope = ? AND client_ref = ?",
                        (scope, client_ref),
                    ).fetchone()
                )

        return await asyncio.to_thread(read)

    async def unresolved(self, scope):
        def read():
            with self._connect() as db:
                return [
                    self._decode(row)
                    for row in db.execute(
                        "SELECT * FROM orders WHERE scope = ? AND state IN ('submitting','unknown','accepted') "
                        "ORDER BY created_at",
                        (scope,),
                    ).fetchall()
                ]

        return await asyncio.to_thread(read)

    async def update_trade(self, scope, trade_id, state):
        def write():
            with self._connect() as db:
                db.execute(
                    "UPDATE orders SET state = ?, updated_at = ? WHERE scope = ? AND trade_id = ?",
                    (state, time.time(), scope, trade_id),
                )

        await asyncio.to_thread(write)
