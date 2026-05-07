# Architecture

Skeleton — fill in as the research questions in `RESEARCH.md` get answered.

## Components (proposed)

```
┌─────────────────┐
│  Client device  │
└────────┬────────┘
         │ DNS query (UDP/53 or DoH/DoT)
         ▼
┌────────────────────────────────────┐
│  sentinel-dns resolver             │
│                                    │
│  ┌──────────────┐  ┌────────────┐  │
│  │ Cache        │  │ Blocklist  │  │
│  │ (LRU)        │  │ (rules)    │  │
│  └──────┬───────┘  └─────┬──────┘  │
│         └────────┬───────┘         │
│                  ▼                 │
│         ┌────────────────┐         │
│         │ AI classifier  │ (async) │
│         └────────┬───────┘         │
│                  ▼                 │
│         ┌────────────────┐         │
│         │ Upstream proxy │         │
│         │ → 1.1.1.1 etc. │         │
│         └────────────────┘         │
└────────────────────────────────────┘
         │
         ▼
   Telemetry / dashboard
```

## Open architectural questions

- **Inline or async AI?** Inline = block before answering, costs latency. Async = answer fast, alert/block on next request. Probably async for v0.1.
- **Cache key.** Per-domain or per-(domain, client)? Per-client enables behavioral signals but explodes cache size.
- **Upstream.** Single (1.1.1.1) or pool? Recursive ourselves or always forward?
- **Telemetry plane.** Where do logs go for the dashboard? SQLite for v0.1, ClickHouse later?

## Non-goals (for v0.1)

- DoH/DoT termination at scale
- Multi-tenancy
- A web UI (CLI + JSON output is fine)
- Distributed deployment

These get revisited once the spike proves the core value.
