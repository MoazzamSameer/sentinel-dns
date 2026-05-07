# Architecture

Target architecture for `sentinel-dns` v0.1, anchored to the verdicts in [`RESEARCH.md`](RESEARCH.md). This is a design document — it predates the spike phase. Anything marked **(TBD: spike)** gets settled by experimental evidence, not opinion.

## Design principles

These are the load-bearing decisions. Everything else falls out of them.

1. **Self-hosted first.** Queries never leave the user's network unless they opt in. This is the privacy story.
2. **Hybrid inference, not inline-everything.** Cheap checks block in-line; expensive checks run async and feed the cache. Latency stays under the budget.
3. **Heuristics + ML, not "AI-first."** The marketing is honest, and a transparent rule layer is the floor we never fall below if the ML disappoints.
4. **Single binary, single config.** A homelab user should be able to `pip install sentinel-dns && sentinel-dns run` and be done.
5. **Plain-language explanations are a first-class feature, not telemetry.** Every block produces a human-readable reason.

## Component diagram

```
            ┌──────────────────┐
            │  Client device   │
            └────────┬─────────┘
                     │  DNS query (UDP/53, optionally DoH/DoT later)
                     ▼
   ┌────────────────────────────────────────────────────┐
   │  sentinel-dns process (single binary)              │
   │                                                    │
   │   ┌──────────────┐         ┌─────────────────┐     │
   │   │ Listener     │────────▶│ Query pipeline  │     │
   │   │ (asyncio)    │         │ (inline tier)   │     │
   │   └──────────────┘         └────────┬────────┘     │
   │                                     │              │
   │   ┌──────────────────────┐          ▼              │
   │   │ Decision cache (LRU) │◀───┬───────────┐        │
   │   └──────────────────────┘    │           │        │
   │            ▲                  │           │        │
   │            │                  ▼           ▼        │
   │   ┌────────────────┐  ┌────────────┐  ┌──────────┐ │
   │   │ Async scorer   │  │ Blocklist  │  │ Lexical  │ │
   │   │ (worker pool)  │  │ (rules)    │  │ classif. │ │
   │   └────────┬───────┘  └────────────┘  └──────────┘ │
   │            │                                       │
   │            │           ┌──────────────────┐        │
   │            └──────────▶│ Upstream client  │        │
   │                        │ (DoH → 1.1.1.1   │        │
   │                        │  + 9.9.9.9)      │        │
   │                        └──────────────────┘        │
   │                                                    │
   │   ┌──────────────────────┐  ┌──────────────────┐   │
   │   │ Local query log      │  │ Explanation      │   │
   │   │ (SQLite)             │  │ generator        │   │
   │   └──────────────────────┘  └──────────────────┘   │
   └────────────────────────────────────────────────────┘
                     │
                     ▼
              ┌───────────────┐
              │ CLI / JSON    │  (no web UI in v0.1)
              │ inspection    │
              └───────────────┘
```

## Resolver core

### Stub vs recursive: forwarder

`sentinel-dns` is a **forwarder**, not a recursive resolver, in v0.1. Concretely: it accepts queries from clients, applies its own decision logic, and forwards anything that needs upstream resolution to a public recursive resolver (1.1.1.1, 9.9.9.9, 8.8.8.8).

Why not full recursive:

- A correct recursive resolver is a serious project on its own — root hints, NS chasing, DNSSEC validation, negative caching nuances, EDNS Client Subnet handling. Months of work before the first interesting `sentinel-dns`-specific feature lands.
- The prosumer wedge does not care about cutting Cloudflare out of the loop. They already trust *some* recursive resolver — they just want filtering and explanations on top.
- Forwarding lets us inherit Cloudflare/Quad9's anycast network for performance for free.

Trade-off accepted: a user who categorically refuses to query a public resolver isn't our v0.1 customer. We revisit in v2 if there's pull.

### Upstream strategy

Default upstreams: **1.1.1.1 (primary), 9.9.9.9 (secondary), 8.8.8.8 (tertiary)**. Fully configurable.

- **Transport:** DoH (DNS-over-HTTPS) where supported, plain UDP fallback. DoH means the upstream can't see plaintext queries on the wire, even if the user's ISP can identify which resolver they're talking to.
- **Failover:** simple latency-based: prefer the fastest responder over a rolling window. Hard fail to the next upstream after N consecutive timeouts.
- **DNSSEC:** rely on upstream validation initially. Local validation is **(TBD: post-v0.1)**.

### Caching

Two-layer caching:

| Layer | Keyed on | Purpose | Lifetime |
|---|---|---|---|
| **Answer cache** | (qname, qtype, qclass) | Standard DNS response cache | TTL from the response, capped at config max |
| **Decision cache** | qname | "Is this domain blocked / suspicious / clean, and why?" | Independent of TTL — typically longer |

The decision cache is the important one architecturally. The async scorer writes to it; the inline tier reads from it. This is how heavy classification cost gets amortized to zero on repeat queries.

Eviction: simple LRU. Capacity **(TBD: spike)** — depends on memory budget for the prosumer hardware target (a Raspberry Pi 4 has 4–8GB; we should fit comfortably in 256MB).

## AI layer

### Hybrid pipeline (the core architectural commitment)

```
query arrives
    │
    ▼
┌─────────────────────────┐
│ Decision cache hit?     │──── yes ──▶ apply cached decision (block / allow)
└─────────┬───────────────┘
          │ no
          ▼
┌─────────────────────────┐
│ Inline tier (<1ms total)│
│   1. Static blocklist   │──── hit ──▶ block, log, explain
│   2. Heuristic ruleset  │──── hit ──▶ block, log, explain
│   3. Lexical classifier │──── high ──▶ block, log, explain
└─────────┬───────────────┘     conf.
          │ allow
          ▼
   forward upstream, return answer
          │
          ▼
   enqueue for async scorer (does not block the response)
          │
          ▼
   ┌───────────────────────────┐
   │ Async scorer (worker pool)│
   │   - WHOIS age, ASN        │
   │   - threat-intel lookups  │
   │   - heavier model         │
   └────────────┬──────────────┘
                │
                ▼
        update decision cache
```

### Inline tier

Three checks in series. All sub-millisecond on commodity CPU.

1. **Static blocklist.** Hashed set of known-bad domains, loaded at startup, refreshed periodically. Sources: URLhaus, abuse.ch, optionally StevenBlack hosts. Lookup is O(1).
2. **Heuristic ruleset.** Hand-written rules over lexical features (entropy, length, TLD, character class mix). The honest-framing floor — even if the ML disappoints, we still ship value here. Examples:
   - High-entropy + young TLD + recently-registered → suspect
   - Edit distance ≤2 from any of the top 10k Tranco domains, but registered <30 days ago → typosquat
3. **Lexical classifier.** Logistic regression on character n-grams as the v0.1 baseline (per the research verdict to "start simple before deep learning"). Single-prediction latency is microseconds. Confidence-thresholded — only blocks at high confidence; ambiguous queries fall through to the async tier.

### Async scorer

Decoupled worker pool consuming a queue of (qname, client metadata) tuples. Adds the signals the inline tier can't afford:

- WHOIS age (was this domain registered in the last 24 hours?)
- ASN / hosting reputation (lookup against a reputation feed)
- Heavier model — **(TBD: spike)** whether a deeper model meaningfully outperforms the lexical baseline, per kill criterion K2 in `RESEARCH.md`

The first query to a malicious domain may go through. The second one is blocked, because the async scorer has updated the decision cache. This is the explicit trade-off from the latency analysis.

### Model choices

| Stage | Model | Why |
|---|---|---|
| Inline lexical | Logistic regression on char-3gram TF-IDF | Sub-ms inference; interpretable (coefficients map to features for the explanation generator) |
| Async heavier | **(TBD: spike)** small CNN, gradient-boosted trees on engineered features, or LLM-based classification | Pick whichever wins on the K2 benchmark in `RESEARCH.md` |

Models are **bundled with the binary** for the prosumer self-hosted case. Updates ship as signed releases. No live model-fetching against a remote endpoint by default — that would leak telemetry the privacy model forbids.

### Explanation generator

Every block produces a structured explanation:

```json
{
  "domain": "g00gle-secure-login.xyz",
  "decision": "block",
  "reasons": [
    {"signal": "typosquat", "of": "google.com", "edit_distance": 2},
    {"signal": "young_tld", "tld": "xyz"},
    {"signal": "lexical_classifier", "score": 0.94}
  ],
  "human": "Looks like a typo of google.com, on a TLD frequently used for short-lived attacks, and our model is 94% confident it's malicious."
}
```

The "human" string is templated from the structured reasons — not generated by an LLM at query time. Latency-safe, deterministic, no external dependency.

## Privacy model

Three categories of data, each with explicit rules:

| Data | What it is | Default | User can change to |
|---|---|---|---|
| **Raw queries** | qname, qtype, client IP, timestamp | Local SQLite, configurable retention (default 7 days) | Disabled, or longer retention |
| **Decision metadata** | qname → (decision, reasons) | Local cache + log | Disabled |
| **Aggregated stats** | Counts of decision categories per day | Off | Opt-in: send to a project-controlled endpoint to improve shared models |

Hard rules:

- **No raw queries leave the device, ever.** Aggregated telemetry never includes domain strings, only counts by category.
- **No remote model fetches by default.** Updates are pull-based via release artifacts the user explicitly upgrades.
- **Telemetry endpoint is open-source.** If we ever ship the opt-in aggregate path, the receiving service is a separate repo with its own audit trail.

This is the "privacy by architecture" claim concretized. It's also a constraint that simplifies engineering: nothing crosses the network boundary that we haven't deliberately put there.

## Storage

SQLite for v0.1, single file per install.

| Table | Purpose | Retention |
|---|---|---|
| `queries` | Per-query log (qname, client, timestamp, decision, reasons FK) | Configurable, default 7 days |
| `decisions` | Decision cache, persistent across restarts | LRU + max size cap |
| `blocklist_meta` | Source / version of currently-loaded blocklists | Forever (small) |

ClickHouse / Postgres are **(TBD: post-v0.1)** if multi-device telemetry becomes a thing.

## Configuration and deployment

- **Config file:** TOML, single file. Sensible defaults so first-run requires zero config.
- **Distribution:** PyPI package + Docker image. Static binary (PyInstaller / Nuitka) is **(TBD: post-v0.1)** if it makes installation noticeably easier for the homelab segment.
- **Process model:** single asyncio process. The async scorer is a worker pool inside that process — not a separate service — to keep deployment trivial.
- **Privileges:** binds to UDP/53 (privileged port). Document the standard `setcap` / `--cap-add=NET_BIND_SERVICE` workarounds.

## Non-goals (v0.1)

These are deferred to keep v0.1 tractable — most are revisited based on what we learn:

- Recursive resolution (we forward to public resolvers)
- DNSSEC validation in-process (rely on upstream)
- DoH/DoT *server-side* termination (we accept plain DNS from clients, optionally use DoH *upstream*)
- Multi-tenancy (one install = one network)
- Distributed deployment / shared cache across nodes
- Web UI (CLI + JSON output is the entire v0.1 interface)
- Mobile apps
- Kubernetes / cloud-native packaging beyond a Docker image

## Open questions deferred to spike

Tracked in [`PROJECT.md`](PROJECT.md), summarized here for visibility:

- **Decision cache size** for the Raspberry-Pi-class prosumer target.
- **Async vs. inline trade-off:** does first-query-leak matter in practice for the threats we're catching?
- **Heavier-model choice** in the async tier (kill criterion K2).
- **Inline classifier latency** on the smallest target hardware (kill criterion K1).
- **DoH-upstream latency penalty** vs. plain UDP — does it cost us our latency budget?
