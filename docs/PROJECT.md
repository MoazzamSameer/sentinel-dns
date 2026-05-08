# PROJECT

Single source of truth for what's being worked on. One top-level item = one PR.

## Phase: Build v0.1

Phase 0 (research) and Phase 1 (spikes + synthesis) are complete. All four kill-criteria gates passed. v0.1 latency budget validated end-to-end on Pi-class CPU simulation. The remaining work is shipping the components in [`ROADMAP.md`](ROADMAP.md)'s v0.1 scope to a real prosumer install.

## Tasks

Listed in priority order. Top of the list = next thing to work on.

- [x] Enforcement mode — turn the inline classifier from measurement-only into an actual blocker (PR #13)
  - [x] Return NXDOMAIN (or configurable response) when `would_block` fires
  - [x] Structured block log includes the reasons that fired
  - [x] `--enforce` flag / config opt-in (off by default until we have explanations + logs)
- [x] Static blocklist with URLhaus refresh (PR #14)
  - [x] Load URLhaus host file at startup; refresh on a configurable interval
  - [x] Blocklist hits are checked before the classifier (the inline tier's first layer per [`ARCHITECTURE.md`](ARCHITECTURE.md))
  - [ ] Multi-feed support (StevenBlack as a second source) — deferred to a follow-up
- [x] Plain-language explanation generator (PR #15)
  - [x] Convert structured `HeuristicReasons` + classifier signals to a templated human string per the architecture's spec
  - [x] Reused by both the block log (this PR) and the (future) CLI `explain` command
- [x] Local SQLite query log — `queries` table only (PR #16)
  - [x] Schema for `queries` (qname, client, timestamp, decision, signals)
  - [x] Retention config (default 7 days)
  - [x] Forwarder writes async — must not block the response path
- [ ] Persistent decision cache (`decisions` table, in-memory cache survives restart)
  - [ ] Backs the in-memory `DecisionCache` with the SQLite file. On startup, hydrate from disk.
  - [ ] Reuse the QueryLog writer infrastructure rather than rolling a second one.
  - [ ] Architecture commits to this; v0.1 ships fine without it but it's a quick follow-up.
- [x] TOML config file (PR #17)
  - [x] Replace argparse-only with a config file + sane defaults
  - [x] Zero-config first run
- [x] CLI: `sentinel-dns tail` (PR #18)
  - [x] Live stream of recent queries + decisions from the SQLite log
  - [x] Filter by client, by decision type, by score threshold
- [x] CLI: `sentinel-dns explain <domain>` (PR #19)
  - [x] Show the latest decision for a domain, with structured reasons + plain-English explanation
- [x] DoH upstream (PR #20)
  - [x] Switch `dns.asyncquery.udp` → DoH client. Configurable endpoint.
  - [x] Latency re-bench — measured ~36ms p50 vs UDP (higher than initially expected; shared `httpx.AsyncClient` was load-bearing — without it, +118ms p50)
- [x] README quickstart (PR #21)
  - [x] One-page install + first-run flow for a homelab user
  - [x] Explain config defaults, blocklist sources, where logs live
- [x] PyPI distribution (PR #22)
  - [x] CI publish on tag (GitHub Actions, OIDC trusted publishing)
  - [x] Tested locally: `python -m build` → `pip install dist/*.whl` → `sentinel-dns --help` + live DoH/blocklist smoke. Actual `pip install sentinel-dns` is gated on the first PyPI publish.
- [ ] Docker distribution
  - [ ] Multi-arch image (amd64 + arm64) — arm64 is the prosumer/Pi target
  - [ ] Document the `--cap-add=NET_BIND_SERVICE` / port mapping pattern for binding `:53`
- [ ] Async scorer (may slip to v0.2)
  - [ ] Decoupled worker pool consuming a queue of (qname, client metadata)
  - [ ] WHOIS age + ASN reputation lookups
  - [ ] Decision cache writes after async scoring; first-query-leak is the documented trade-off
- [ ] Actual Pi 4 hardware verification (gates v0.1 release)
  - [ ] Run `bench/bench_pi4_projection.py` + 2-forwarder e2e bench on real hardware
  - [ ] Expected: 600µs–1.5ms p50 classifier, +2–4ms p50 e2e. Outside that range = architecture revisit.

## Completed

### Phase 0 — Research

- Viability analysis with verdicts on latency, competitive landscape, user segments, privacy, AI-vs-rules — wedge identified as prosumer/homelab self-hosted, with kill criteria for the spike phase. (PR #1)
- Target architecture for v0.1 — forwarder (not recursive), hybrid inline + async AI pipeline, self-hosted privacy model, single-binary deployment. (PR #2)
- MVP scope and success criteria for v0.1 — concrete in/out feature list, two-spike Phase 1 plan with go/no-go gates, technical + adoption targets, K1–K4 kill criteria carried forward. (PR #3)

### Phase 1 — Spikes

- Spike A — minimal asyncio forwarder over dnspython, +1.85ms p50 / +0.37ms p99 added latency vs direct upstream. Within Spike A's pass threshold; v0.1 p50 < 1ms target needs revisiting after Spike B. Writeup in [`docs/spike-a-results.md`](spike-a-results.md). (PR #4)
- Spike B — domain classifier on URLhaus + Tranco. K2 passes decisively: logistic regression on char n-grams catches 81.2% of held-out malicious domains at <1% FPR (heuristics 9.2%). The "AI" claim is honest, not marketing. Writeup in [`docs/spike-b-results.md`](spike-b-results.md). (PR #5)
- Synthesis spike — classifier extracted to `sentinel_dns/classifier.py`, wired into forwarder. Inline classifier costs 145µs p50 / 629µs p99 (microbench). End-to-end forwarder + classifier adds +2.37ms p50 vs direct upstream — v0.1's p50 < 1ms target needs relaxing to <3ms; the architecture's decision-cache layer is now the highest-leverage piece of v0.1 work. Writeup in [`docs/spike-synthesis-results.md`](spike-synthesis-results.md). (PR #6)

### Phase 2 — Build v0.1

- Enforcement mode — `--enforce` flag turns the inline classifier into an actual blocker. `would_block=True` queries get NXDOMAIN instead of being forwarded; verified with live URLhaus domains. Log lines distinguish `score` (allow) from `BLOCK` (block) prefixes for cleaner grepping. Cache short-circuit and argparse safety checks both verified. Writeup in [`docs/enforcement-mode.md`](enforcement-mode.md). (PR #13)
- Static blocklist with URLhaus — `StaticBlocklist` in `sentinel_dns/blocklist.py`, fetched via `--blocklist-url`, refreshed in a background asyncio task on configurable interval (default 1h, fail-open). Wired into the inline tier as layer 1 (before the classifier per the architecture). Forwarder can now run as classifier-only, blocklist-only, or both. `Decision` extended with a `block_source` field ("blocklist"/"classifier"/None) for the upcoming explanation generator. Writeup in [`docs/static-blocklist.md`](static-blocklist.md). (PR #14)
- Plain-language explanation generator — `explain()` in `sentinel_dns/explanation.py` converts a `Decision` into a structured `list[Reason]` and a templated human string. No LLM at query time — deterministic templates. Wired into the BLOCK log: every block produces a `signals=...` field on the `BLOCK` line plus a follow-up `explain` line with the human paragraph. The "why blocked" differentiator from RESEARCH.md is now real. Writeup in [`docs/explanations.md`](explanations.md). (PR #15)
- SQLite query log (queries table only) — `QueryLog` in `sentinel_dns/query_log.py` with bounded asyncio.Queue + batched background writer + hourly retention purge. WAL mode + `synchronous=NORMAL` + executor-based SQL keeps the asyncio loop unblocked. Drops on overflow rather than back-pressuring the response path. Captures every query (including blocklist-only-mode allows that stdout suppresses). Persistent decision cache split into a follow-up task. Writeup in [`docs/query-log.md`](query-log.md). (PR #16)
- TOML config file — `Config` moved to `sentinel_dns/config.py` with `load_toml()` + `merge()` helpers. Flat schema (sections rejected). Precedence: CLI > file > defaults, detected via `argparse.SUPPRESS` so un-passed flags don't appear in the Namespace. Unknown TOML keys produce errors listing valid keys. Example file at repo root: [`sentinel-dns.example.toml`](../sentinel-dns.example.toml). Writeup in [`docs/configuration.md`](configuration.md). (PR #17)
- CLI: `sentinel-dns tail` — `sentinel_dns/cli.py` dispatcher (default → forwarder, `tail` → tail subcommand) plus `sentinel_dns/tail_cmd.py` reading the SQLite log read-only via `mode=ro` URI form. One-shot or `-f` follow mode (polling every 0.5s); filters by `--decision`, `--client`, `--qname-contains`, `--min-ml-score`, `--block-source`. Block rows get an explanation line via the same `explain()` the forwarder uses. Writeup in [`docs/cli.md`](cli.md). (PR #18)
- CLI: `sentinel-dns explain <domain>` — `sentinel_dns/explain_cmd.py` reuses the same read-only SQLite + `explain()` plumbing. Surfaces the most recent decision as a terse one-liner with structured reason bullets; `--verbose` adds raw scores + cache state + inline timing; `-n N` walks history to spot flapping classifications or cache transitions. Unseen domain → exit code 2 (clean error path for shell scripts). The "why blocked" promise is now exposed end-to-end. Writeup in [`docs/cli.md`](cli.md). (PR #19)
- DoH upstream — `--upstream-doh-url` flag / `upstream_doh_url` TOML key dispatches to `dns.asyncquery.https()` instead of UDP. Single shared `httpx.AsyncClient` (HTTP/2 pinned) holds the TLS session for the forwarder's lifetime; without it, dnspython opens a fresh TLS session per query and adds +118ms p50. With it, +36ms p50 vs UDP forwarder. Bad URLs return SERVFAIL cleanly via a broader `_forward` exception catch. Adds `httpx[http2]` as a base dep. Writeup in [`docs/doh-upstream.md`](doh-upstream.md). (PR #20)
- README quickstart — full README rewrite. Tagline encodes the wedge (self-hosted + plain-English explanations + lexical detection). Two-tier quickstart: 30-second blocklist-only, full version with classifier + cache + log + tail. Comparison table from ROADMAP for differentiation. Status section makes "early v0.x" honest. Quickstart commands smoke-tested end-to-end via `dig`. (PR #21)
- PyPI distribution — `.github/workflows/release.yml` builds sdist+wheel on PRs touching the package, publishes to PyPI on `v*` tag pushes via OIDC trusted publishing (no API token in GitHub secrets). Build job verifies the wheel installs in a clean venv and the `sentinel-dns` entry point loads. Local end-to-end smoke confirmed: clean venv → wheel install → forwarder runs → `dig` resolves benign + blocks URLhaus domain. Process documented in [`docs/releasing.md`](releasing.md). First publish gated on PyPI trusted-publisher one-time setup + actual decision to release. (PR #22)

### Phase 1 follow-ups

- Decision cache — LRU cache from qname → Decision in `sentinel_dns/cache.py`, wired into forwarder ahead of the classifier. Cache hits are ~170 ns p50 (microbench), three orders of magnitude faster than the classifier. End-to-end overhead drops from +2.37ms p50 to +0.89ms p50 vs direct upstream — inside the relaxed v0.1 target. Writeup in [`docs/decision-cache.md`](decision-cache.md). (PR #10)
- Pi 4 projection via M1 E-core simulation — classifier ~636µs p50 under E-core hint (4.5× P-core slowdown, plausibly Pi 4-class). End-to-end forwarder + classifier + cache adds +2.37ms p50 vs direct upstream even on E-core, comfortably inside the relaxed <3ms v0.1 target. Cache makes Pi 4 viability essentially independent of classifier speed. Writeup in [`docs/pi4-projection-results.md`](pi4-projection-results.md). Real hardware verification still gates v0.1 release. (PR #11)

## Notes

- Tasks are listed in priority order. Top of the list = next thing to work on.
- A task gets checked off when its PR merges. The PR must include the PROJECT.md update.
- If a task is bigger than one clean PR, break it into subtasks and ship each as its own PR.
- Pi 4 hardware verification stays at the bottom of the build list because it gates the release announcement, not most of the v0.1 work itself.
