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
- [ ] Local SQLite query log
  - [ ] Schema for `queries` (qname, client, timestamp, decision, reasons FK) and `decisions` (qname → decision cache, persistent)
  - [ ] Retention config (default 7 days)
  - [ ] Forwarder writes async — must not block the response path
- [ ] TOML config file
  - [ ] Replace argparse-only with a config file + sane defaults
  - [ ] Zero-config first run
- [ ] CLI: `sentinel-dns tail`
  - [ ] Live stream of recent queries + decisions from the SQLite log
  - [ ] Filter by client, by decision type, by score threshold
- [ ] CLI: `sentinel-dns explain <domain>`
  - [ ] Show the latest decision for a domain, with structured reasons + plain-English explanation
- [ ] DoH upstream
  - [ ] Switch `dns.asyncquery.udp` → DoH client. Configurable endpoint.
  - [ ] Latency re-bench — DoH adds ~5–20ms vs UDP; measure on representative networks
- [ ] README quickstart
  - [ ] One-page install + first-run flow for a homelab user
  - [ ] Explain config defaults, blocklist sources, where logs live
- [ ] PyPI distribution
  - [ ] CI publish on tag (GitHub Actions)
  - [ ] Test that `pipx install sentinel-dns` works clean on a fresh machine
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

### Phase 1 follow-ups

- Decision cache — LRU cache from qname → Decision in `sentinel_dns/cache.py`, wired into forwarder ahead of the classifier. Cache hits are ~170 ns p50 (microbench), three orders of magnitude faster than the classifier. End-to-end overhead drops from +2.37ms p50 to +0.89ms p50 vs direct upstream — inside the relaxed v0.1 target. Writeup in [`docs/decision-cache.md`](decision-cache.md). (PR #10)
- Pi 4 projection via M1 E-core simulation — classifier ~636µs p50 under E-core hint (4.5× P-core slowdown, plausibly Pi 4-class). End-to-end forwarder + classifier + cache adds +2.37ms p50 vs direct upstream even on E-core, comfortably inside the relaxed <3ms v0.1 target. Cache makes Pi 4 viability essentially independent of classifier speed. Writeup in [`docs/pi4-projection-results.md`](pi4-projection-results.md). Real hardware verification still gates v0.1 release. (PR #11)

## Notes

- Tasks are listed in priority order. Top of the list = next thing to work on.
- A task gets checked off when its PR merges. The PR must include the PROJECT.md update.
- If a task is bigger than one clean PR, break it into subtasks and ship each as its own PR.
- Pi 4 hardware verification stays at the bottom of the build list because it gates the release announcement, not most of the v0.1 work itself.
