# Static blocklist

The architecture's inline tier has three layers: static blocklist → heuristics → ML classifier. Until this task, we had layers 2 and 3 (heuristics fold into the classifier path). This adds layer 1 — a hashed-set lookup against a hostfile-format threat feed, checked **before** the classifier so domains the feed already knows about cost zero classifier microseconds.

## Implementation

[`sentinel_dns/blocklist.py`](../sentinel_dns/blocklist.py) — ~110 lines.

- `StaticBlocklist`: holds a `frozenset[str]` of lowercase hostnames. O(1) membership via the standard `__contains__`.
- `refresh_sync()`: HTTP GET, parse, atomically swap the frozenset reference. Single attribute assignment is atomic in CPython, safe under asyncio.
- `refresh_async()`: same work, on the default thread executor — keeps the event loop free during the blocking HTTP call.
- `run_refresh_loop()`: background task that re-fetches every `refresh_interval_s`. **Fails open** — if the network is down, we keep the cached set and log a warning rather than emptying.
- Filters out reverse-DNS-of-IP entries (URLhaus sometimes lists `176.65.149.223.ptr.pfcloud.network` style records that aren't real C2 domains).

The default source is URLhaus (`https://urlhaus.abuse.ch/downloads/hostfile/`). Any hostfile-format URL works — `--blocklist-url` is configurable.

## Wiring into the inline tier

[`sentinel_dns/forwarder.py`](../sentinel_dns/forwarder.py) `_score_inline` flow now matches the architecture diagram:

```
qname → cache → blocklist → classifier
         hit?    hit?         score
          ↓       ↓             ↓
       Decision Decision     Decision
```

A blocklist hit produces a `Decision(would_block=True, block_source="blocklist")` — distinguishable from a classifier-driven block (`block_source="classifier"`) in both the cache and the structured log.

## `Decision` extended with `block_source`

[`sentinel_dns/cache.py`](../sentinel_dns/cache.py) gets a new field:

```python
@dataclass(frozen=True)
class Decision:
    ml_score: float
    heuristic_score: float
    would_block: bool
    block_source: str | None = None  # "blocklist", "classifier", or None
```

The next task on the list is the plain-language explanation generator, which needs exactly this — to know whether to say "this is on a known-malware feed" vs "this looks like a DGA-generated name." Keeping the field as a simple string for now; structured `Reasons` come with the explanation generator.

## Verification

Live forwarder, blocklist-only (no classifier), live URLhaus pull:

```
$ sentinel-dns --blocklist-url https://urlhaus.abuse.ch/downloads/hostfile/ --enforce
INFO ... blocklist refreshed: 1080 domains
INFO ... listening on 127.0.0.1:5354, ... classifier=off, ... blocklist=size=1080, enforce=on

$ dig @127.0.0.1 -p 5354 google.com    → NOERROR (forwarded, real IP)
$ dig @127.0.0.1 -p 5354 1ce6-route.fixionmunici9al.lat   → NXDOMAIN
$ dig @127.0.0.1 -p 5354 1ce6-route.fixionmunici9al.lat   → NXDOMAIN  (cache hit)
$ dig @127.0.0.1 -p 5354 111101111.ru                     → NXDOMAIN  (different URLhaus domain)
```

Log lines:

```
BLOCK qname=1ce6-route.fixionmunici9al.lat ml=0.0000 heur=0.000 would_block=True cache=miss source=blocklist
BLOCK qname=1ce6-route.fixionmunici9al.lat ml=0.0000 heur=0.000 would_block=True cache=hit source=blocklist
BLOCK qname=111101111.ru ml=0.0000 heur=0.000 would_block=True cache=miss source=blocklist
```

Notice:
- `source=blocklist` now appears in the structured log for blocklist-driven blocks. (Classifier-driven blocks will say `source=classifier`.)
- Second query for the same domain hits the cache. The Decision was cached after the blocklist hit; future queries don't even touch the blocklist set lookup.
- The classifier was off entirely (`classifier=off` in the listening line). Blocklist alone is enough to operate as a Pi-hole-style filter.

## Operating modes

The forwarder now has four meaningful configurations:

| classifier | blocklist | enforce | Use case |
|---|---|---|---|
| off | off | off | Bare proxy (Spike A baseline) |
| **on** | off | off | ML measurement, no block |
| **on** | off | **on** | ML enforcement (PR #13) |
| off | **on** | **on** | Pi-hole-style feed-only enforcement (this PR) |
| **on** | **on** | **on** | **Full architecture inline tier — v0.1 target** |

`--enforce` requires at least one of `--model-path` or `--blocklist-url` (argparse error otherwise — verified). Nothing to enforce against without an inline tier.

## Refresh behavior

- **Initial load is synchronous.** Forwarder does not start serving until the first fetch succeeds. If the initial fetch fails, the forwarder exits with an error rather than starting with an empty blocklist (which would mean every malicious query slipped through until the next refresh succeeded).
- **Subsequent refreshes are async + fail-open.** A network blip during a scheduled refresh logs a warning and keeps the existing cached set. We don't want a transient outage to emptly the security floor.
- **Default interval is 3600s (1 hour).** URLhaus updates several times a day; once an hour is responsive without hammering them. Configurable via `--blocklist-refresh-s`.

## Caveats

1. **Single feed only.** URLhaus is the default. The architecture allowed for multiple feeds (StevenBlack hosts as a second source); deferred to a follow-up. Today, multiple feeds means running multiple forwarder instances with different URLs (silly).
2. **No allowlist override.** If URLhaus has a false positive (rare but possible — a compromised legitimate domain that's been cleaned up before URLhaus removes it), there's no way to bypass except restart with a different URL. Allowlist is on the post-v0.1 list per [`enforcement-mode.md`](enforcement-mode.md).
3. **Memory.** ~1k–5k entries × ~30 bytes per string ≈ <200 KB. Negligible. Adding StevenBlack hosts (~150k entries) bumps it to ~5 MB — still fine.
4. **No persistence across restart.** The blocklist re-fetches on every startup. For a daemon that never restarts, this is fine; for testing it costs a network round-trip. SQLite-backed persistence is a v0.1 follow-up.
5. **HTTP, not authenticated.** No verification that the feed wasn't tampered with in transit. URLhaus serves over HTTPS; we trust the TLS certificate. If we ever add a feed with a known-good hash, we should verify it.
6. **Fails open on initial-load network failure** — wait, we **fail closed** there (exit). Re-reading my own code: yes, initial load failure exits the process; only *subsequent* refreshes fail open. This is the right call: starting with an empty blocklist when you asked for one is much worse than refusing to start.

## What this unblocks

- The forwarder can now run as a **pure Pi-hole replacement** without the classifier — useful for users who want feed-based blocking without the ML tier.
- The full v0.1 inline tier (cache + blocklist + classifier) is now operational.
- The next task on the list (plain-language explanation generator) has the `block_source` field it needs to know whether to say "URLhaus flagged this" vs "the ML model flagged this."
