# SQLite query log

Until this task, every query produced a stdout log line and that's it. Restart the forwarder and history is gone. The architecture committed to a local SQLite log so the upcoming `sentinel-dns tail` and `sentinel-dns explain <domain>` commands have data to read from — and so a user can answer "what did my smart TV connect to today?" without wiring up a syslog pipeline.

## Scope split

The architecture's storage section calls for two related things:

- `queries` — per-query log with retention. **This PR.**
- `decisions` — persistent decision cache (in-memory cache survives restarts). **Deferred to a follow-up** — the change touches `DecisionCache`'s lifecycle and is large enough to deserve its own PR. Tracked in PROJECT.md.

## Implementation

[`sentinel_dns/query_log.py`](../sentinel_dns/query_log.py) — ~210 lines.

```
forwarder hot path
        │
        ▼
   log_nowait(record)         ← non-blocking; drops on overflow
        │
        ▼
  asyncio.Queue (10k cap)
        │
        ▼
   writer coroutine           ← drains; batches up to 100 records
        │                        or 1s, whichever first
        ▼
   thread executor
        │
        ▼
  sqlite3 INSERT (WAL mode)   ← never blocks the asyncio loop

  retention coroutine:
   wakes once/hour, purges rows older than --log-retention-days (default 7)
```

### Schema

```sql
CREATE TABLE queries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ns    INTEGER NOT NULL,
    qname           TEXT    NOT NULL,
    client_addr     TEXT,
    decision        TEXT    NOT NULL,    -- 'allow' or 'block'
    block_source    TEXT,                -- 'blocklist' / 'classifier' / NULL
    ml_score        REAL,
    heuristic_score REAL,
    cache_state     TEXT    NOT NULL,    -- 'hit' or 'miss'
    inline_us       REAL,                -- NULL on cache hits
    signals         TEXT                 -- comma-separated signal codes
);

CREATE INDEX queries_timestamp ON queries(timestamp_ns);  -- retention scans
CREATE INDEX queries_qname     ON queries(qname);         -- explain lookups
```

`signals` is denormalized — comma-separated text rather than a join table. Justified: signal codes are short stable strings, the set is small (~10), and we never need integrity constraints on them. A relational `reasons` table would be over-engineering. If we ever need "which queries had signal X" as a hot query, a `LIKE '%X%'` works at this scale; if it ever doesn't, we add an index or normalize.

### What gets logged

**Every query** when `--log-path` is set — both allows and blocks, both cache hits and misses. The CLI tools that come next need full history to be useful.

This is a behavior change vs stdout: stdout's `_stdout_worth_logging` heuristic still skips the silent-allow case (blocklist-only mode + no cache hit + no flagged signal) to avoid log spam, but SQLite gets all of those.

### Hot-path safety

- **`log_nowait` drops on overflow.** A 10k-record queue at typical home traffic (~1 query/sec, peaks ~100/sec) is roughly 100s of buffer at peak — way more than needed for a normal disk hiccup. If we ever blow through it, dropping queries is much better than back-pressuring the response path. Drop counts are tracked and logged at first drop + every 1000th.
- **SQLite work runs in the default thread executor** so the asyncio loop never blocks on disk I/O.
- **WAL journal mode + synchronous=NORMAL.** Writes are ~10× faster than the default `synchronous=FULL` while still being crash-safe (worst case: lose the most recent batch on power loss).
- **Batched commits.** 100 records per transaction or 1s, whichever first. Keeps fsync pressure low.

### Graceful shutdown

The writer coroutine, on `CancelledError`, drains the remaining queue and does one last flush before exiting. The `serve()` function calls `query_log.stop()` in its `finally` block. On a normal Ctrl-C the in-flight records hit disk before the process exits.

## Verification

### End-to-end with mixed traffic

```
$ sentinel-dns --listen-port 5354 \\
    --model-path models/classifier_v0.joblib \\
    --blocklist-url https://urlhaus.abuse.ch/downloads/hostfile/ \\
    --enforce \\
    --log-path /tmp/sentinel.db
... blocklist refreshed: 1115 domains
... query log open at /tmp/sentinel.db (retention 7 days, batch 100, queue 10000)
... listening on 127.0.0.1:5354, ... log=/tmp/sentinel.db, enforce=on

$ for d in google.com github.com example.com 1ce6-route.fixionmunici9al.lat \\
           5cri-logic.xamir3on.lat google.com; do
    dig @127.0.0.1 -p 5354 $d > /dev/null
done

$ sqlite3 /tmp/sentinel.db 'SELECT id, qname, decision, block_source, cache_state, signals FROM queries'
1|google.com                       |allow|       |miss|
2|github.com                       |allow|       |miss|
3|example.com                      |allow|       |miss|
4|1ce6-route.fixionmunici9al.lat   |block|blocklist|miss|blocklist,abused_tld
5|5cri-logic.xamir3on.lat          |block|blocklist|miss|blocklist,abused_tld
6|google.com                       |allow|       |hit |
```

All six recorded. Block source captured. Cache hit on the repeated google.com query. Structured signals on blocks.

### Retention purge

Programmatic test (excerpt):

```python
log = QueryLog(p, retention_days=7)
await log.start()
log.log_nowait(record_now)
log.log_nowait(record_30_days_ago)
await asyncio.sleep(2)  # writer flush

deleted = log._purge(cutoff_7_days_ago)   # → 1
remaining = sqlite_select_qnames()         # → ['fresh.example.com']
```

Old record gone, fresh record kept. Index on `timestamp_ns` keeps the purge fast even on large tables.

## CLI flags

- `--log-path PATH` — enables the SQLite log. Off by default.
- `--log-retention-days N` — retention window. Default 7.
- `--quiet-scoring` — suppresses **stdout** score lines (the SQLite log is unaffected — it captures everything regardless).

## Caveats

1. **No persistent decision cache yet.** Restart still loses the in-memory cache. The architecture wants this; deferred to a follow-up. The schema is there to add a `decisions` table without conflicting with this PR's `queries` table.
2. **Records are dropped on queue overflow, not back-pressured.** The right call for a forwarder, but worth knowing — under sustained load above the writer's drain rate, we lose some history rather than slowing responses. Drop count is exposed in `QueryLog.stats`.
3. **No tooling to query the SQLite file** in this PR. You can use the `sqlite3` CLI directly. The `sentinel-dns tail` and `sentinel-dns explain` commands (next two tasks) will wrap this.
4. **Schema is v0.1 fixed.** No migration system yet. We rely on `IF NOT EXISTS` for new columns/tables; for breaking schema changes pre-1.0 we just rebuild the file. A real migration framework is post-v0.1.
5. **No compression or rotation.** A SQLite file with default 7-day retention at 1k queries/day grows to maybe ~10–50 MB; not a concern at the prosumer scale. If we ever serve much higher traffic, periodic VACUUM and rotation are obvious next steps.
6. **client_addr is the raw socket address (IP only, port stripped).** Doesn't preserve the protocol distinction (UDP vs DoT/DoH downstream); that's a v2 feature alongside DoH/DoT downstream support.
7. **No retention bound on query log queue itself** — only by file age. If retention is set very long and traffic is heavy, the SQLite file can grow large. Default 7 days is a sensible tradeoff.

## What this unblocks

- **`sentinel-dns tail`** — read most-recent N records from `queries`, render with timestamps and the same `signals` field.
- **`sentinel-dns explain <domain>`** — `SELECT * FROM queries WHERE qname=? ORDER BY timestamp_ns DESC LIMIT 1` then call `explain()`.
- The "what did my smart TV do today" promise is now answerable, even if today the answer comes via `sqlite3` CLI rather than a friendly UX.
