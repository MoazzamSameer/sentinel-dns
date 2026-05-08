"""Local SQLite query log.

Architecture commitment (docs/ARCHITECTURE.md): every query gets a
durable record so a user can answer "what did my smart TV connect to
today?" — and the (future) `sentinel-dns explain <domain>` command
has data to read from.

Design:
- One asyncio.Queue per process (bounded). The forwarder calls
  `log_nowait(record)` on the hot path — non-blocking, drops on overflow
  rather than back-pressuring the response.
- A background writer coroutine drains the queue, batches inserts, and
  flushes either when the batch hits `batch_size` or `flush_interval_s`
  elapses since the last record. SQLite work runs in the default thread
  executor so the asyncio loop never blocks on disk I/O.
- A separate retention coroutine purges records older than
  `retention_days` once per hour.
- WAL journal mode + synchronous=NORMAL keeps writes fast on the
  prosumer hardware target without sacrificing crash safety.

This PR ships the per-query log only. The architecture also calls for a
persistent `decisions` table (so the in-memory decision cache survives
restarts) — that's tracked as a follow-up.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("sentinel_dns.query_log")


SCHEMA = """
CREATE TABLE IF NOT EXISTS queries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ns  INTEGER NOT NULL,
    qname         TEXT    NOT NULL,
    client_addr   TEXT,
    decision      TEXT    NOT NULL,
    block_source  TEXT,
    ml_score      REAL,
    heuristic_score REAL,
    cache_state   TEXT    NOT NULL,
    inline_us     REAL,
    signals       TEXT
);

CREATE INDEX IF NOT EXISTS queries_timestamp ON queries(timestamp_ns);
CREATE INDEX IF NOT EXISTS queries_qname     ON queries(qname);
"""

INSERT_SQL = """
INSERT INTO queries (
    timestamp_ns, qname, client_addr, decision, block_source,
    ml_score, heuristic_score, cache_state, inline_us, signals
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

SECONDS_PER_DAY = 86_400


@dataclass(frozen=True)
class QueryRecord:
    """One row destined for the queries table."""

    timestamp_ns: int
    qname: str
    client_addr: str | None
    decision: str  # "allow" or "block"
    block_source: str | None
    ml_score: float
    heuristic_score: float
    cache_state: str  # "hit" or "miss"
    inline_us: float | None
    signals: str  # comma-separated signal codes (may be empty)

    def as_row(self) -> tuple:
        return (
            self.timestamp_ns,
            self.qname,
            self.client_addr,
            self.decision,
            self.block_source,
            self.ml_score,
            self.heuristic_score,
            self.cache_state,
            self.inline_us,
            self.signals,
        )


class QueryLog:
    """Async SQLite-backed query log.

    Lifecycle: construct → `await start()` → many `log_nowait(...)` calls
    on the hot path → on shutdown the background tasks are cancelled by
    the caller.
    """

    def __init__(
        self,
        path: Path,
        retention_days: int = 7,
        queue_max: int = 10_000,
        batch_size: int = 100,
        flush_interval_s: float = 1.0,
        retention_interval_s: float = 3600.0,
    ) -> None:
        self.path = Path(path)
        self.retention_days = retention_days
        self.batch_size = batch_size
        self.flush_interval_s = flush_interval_s
        self.retention_interval_s = retention_interval_s
        self.queue: asyncio.Queue[QueryRecord] = asyncio.Queue(maxsize=queue_max)
        self._conn: sqlite3.Connection | None = None
        self._writer_task: asyncio.Task | None = None
        self._retention_task: asyncio.Task | None = None

        self.dropped_overflow = 0
        self.written = 0
        self.purged = 0

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._open)
        self._writer_task = asyncio.create_task(self._run_writer_loop(), name="query-log-writer")
        self._retention_task = asyncio.create_task(
            self._run_retention_loop(), name="query-log-retention"
        )
        log.info(
            "query log open at %s (retention %d days, batch %d, queue %d)",
            self.path,
            self.retention_days,
            self.batch_size,
            self.queue.maxsize,
        )

    async def stop(self) -> None:
        for task in (self._writer_task, self._retention_task):
            if task is not None:
                task.cancel()
        if self._conn is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._close)

    def log_nowait(self, record: QueryRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except asyncio.QueueFull:
            self.dropped_overflow += 1
            # Log the first drop and then every 1000th — visibility without spam.
            if self.dropped_overflow == 1 or self.dropped_overflow % 1000 == 0:
                log.warning("query log queue full, dropped %d records total", self.dropped_overflow)

    @property
    def stats(self) -> dict[str, int]:
        return {
            "queued": self.queue.qsize(),
            "written": self.written,
            "dropped_overflow": self.dropped_overflow,
            "purged": self.purged,
        }

    def _open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        conn.commit()
        self._conn = conn

    def _close(self) -> None:
        assert self._conn is not None
        self._conn.close()
        self._conn = None

    async def _run_writer_loop(self) -> None:
        loop = asyncio.get_running_loop()
        batch: list[QueryRecord] = []
        try:
            while True:
                try:
                    record = await asyncio.wait_for(
                        self.queue.get(), timeout=self.flush_interval_s
                    )
                    batch.append(record)
                    if len(batch) >= self.batch_size:
                        await loop.run_in_executor(None, self._flush, batch)
                        batch = []
                except asyncio.TimeoutError:
                    if batch:
                        await loop.run_in_executor(None, self._flush, batch)
                        batch = []
        except asyncio.CancelledError:
            # Drain remaining queue + batch before exit.
            while not self.queue.empty():
                batch.append(self.queue.get_nowait())
            if batch:
                await loop.run_in_executor(None, self._flush, batch)
            raise

    def _flush(self, records: list[QueryRecord]) -> None:
        assert self._conn is not None
        try:
            with self._conn:
                self._conn.executemany(INSERT_SQL, [r.as_row() for r in records])
            self.written += len(records)
        except sqlite3.Error as e:
            log.warning("query log flush failed: %s, dropping %d records", e, len(records))

    async def _run_retention_loop(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                await asyncio.sleep(self.retention_interval_s)
                cutoff_ns = int((time.time() - self.retention_days * SECONDS_PER_DAY) * 1e9)
                try:
                    deleted = await loop.run_in_executor(None, self._purge, cutoff_ns)
                    if deleted > 0:
                        log.info("retention purge: deleted %d old records", deleted)
                        self.purged += deleted
                except sqlite3.Error as e:
                    log.warning("retention purge failed: %s", e)
        except asyncio.CancelledError:
            raise

    def _purge(self, cutoff_ns: int) -> int:
        assert self._conn is not None
        with self._conn:
            cur = self._conn.execute("DELETE FROM queries WHERE timestamp_ns < ?", (cutoff_ns,))
            return cur.rowcount or 0
