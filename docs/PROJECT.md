# PROJECT

Single source of truth for what's being worked on. One top-level item = one PR.

## Phase: Research / Spike

Goal: decide whether `sentinel-dns` is worth building, in what shape, and for whom — before writing the resolver.

## Tasks

- [x] Write up the problem and viability analysis in `docs/RESEARCH.md` (PR #1)
  - [x] Latency budget: measure how much headroom AI inference has on a DNS lookup
  - [x] Competitive landscape: Cloudflare 1.1.1.1, NextDNS, Quad9, Pi-hole, AdGuard — what they do, what they don't
  - [x] User segments: consumer / prosumer / SMB / enterprise — pick one to target first
- [x] Draft target architecture in `docs/ARCHITECTURE.md` (PR #2)
  - [x] Resolver core: stub vs. recursive, upstream strategy, caching
  - [x] AI layer: where it sits (inline / sidecar / async), model choices
  - [x] Privacy model: what gets logged, what stays on-device, what's aggregated
- [x] Define MVP scope and success criteria in `docs/ROADMAP.md` (PR #3)
  - [x] What does v0.1 do that nothing else does?
  - [x] What metrics tell us the AI layer is earning its keep?
- [x] Spike: proof-of-concept Python DNS resolver that answers `A` queries against an upstream (PR #4)
  - [x] Choose library (`dnslib` vs. `dnspython` vs. raw `asyncio`)
  - [x] Measure baseline latency on common domains
- [x] Spike: domain classifier on a public dataset (e.g. URLhaus, PhishTank) (PR #5)
  - [x] Pick a dataset and document its limitations
  - [x] Train a baseline (logistic regression on n-grams) before reaching for deep learning
  - [x] Report precision/recall on a held-out set — false positive rate is the metric that matters
- [x] Spike A+B synthesis: wire the n-gram classifier into the forwarder, re-bench latency (PR #6)
  - [x] Pull classifier into a reusable module (extract from `bench/spike_b.py`)
  - [x] Persist trained model to disk; load at forwarder startup
  - [x] Score every query inline; log decision but don't block yet (measurement, not enforcement)
  - [x] Re-run bench with classifier inline; settle whether v0.1's p50 < 1ms target is reachable or needs relaxing
- [x] Add the architecture's decision-cache layer in front of the inline tier (PR #10)
  - [x] Without cache, every query pays ~1ms classifier overhead. With cache, ~99% of queries pay sub-microsecond. Highest-leverage piece of v0.1 work.
- [x] Pi 4 latency *projection* via M1 efficiency-core simulation (PR #11)
  - [x] Microbench under taskpolicy + multiplier-based projection from M1 P-core
  - [x] End-to-end forwarder bench under Pi-class CPU constraints
  - [x] Verdict: v0.1 latency targets achievable on Pi 4 (e2e +2.37ms p50 even on E-core sim)
- [ ] Actual Pi 4 hardware verification (gates v0.1 release)
  - [ ] When hardware is available, run `bench/bench_pi4_projection.py` + 2-forwarder e2e bench. Expected: 600µs–1.5ms p50 classifier, +2–4ms p50 e2e. Outside that range = architecture revisit.

## Completed

- Viability analysis with verdicts on latency, competitive landscape, user segments, privacy, AI-vs-rules — wedge identified as prosumer/homelab self-hosted, with kill criteria for the spike phase. (PR #1)
- Target architecture for v0.1 — forwarder (not recursive), hybrid inline + async AI pipeline, self-hosted privacy model, single-binary deployment. (PR #2)
- MVP scope and success criteria for v0.1 — concrete in/out feature list, two-spike Phase 1 plan with go/no-go gates, technical + adoption targets, K1–K4 kill criteria carried forward. (PR #3)
- Spike A — minimal asyncio forwarder over dnspython, +1.85ms p50 / +0.37ms p99 added latency vs direct upstream. Within Spike A's pass threshold; v0.1 p50 < 1ms target needs revisiting after Spike B. Writeup in [`docs/spike-a-results.md`](spike-a-results.md). (PR #4)
- Spike B — domain classifier on URLhaus + Tranco. K2 passes decisively: logistic regression on char n-grams catches 81.2% of held-out malicious domains at <1% FPR (heuristics 9.2%). The "AI" claim is honest, not marketing. Writeup in [`docs/spike-b-results.md`](spike-b-results.md). (PR #5)
- Synthesis spike — classifier extracted to `sentinel_dns/classifier.py`, wired into forwarder. Inline classifier costs 145µs p50 / 629µs p99 (microbench). End-to-end forwarder + classifier adds +2.37ms p50 vs direct upstream — v0.1's p50 < 1ms target needs relaxing to <3ms; the architecture's decision-cache layer is now the highest-leverage piece of v0.1 work. Writeup in [`docs/spike-synthesis-results.md`](spike-synthesis-results.md). (PR #6)
- Decision cache — LRU cache from qname → Decision in `sentinel_dns/cache.py`, wired into forwarder ahead of the classifier. Cache hits are ~170 ns p50 (microbench), three orders of magnitude faster than the classifier. End-to-end overhead drops from +2.37ms p50 to +0.89ms p50 vs direct upstream — inside the relaxed v0.1 target. Writeup in [`docs/decision-cache.md`](decision-cache.md). (PR #10)
- Pi 4 projection via M1 E-core simulation — classifier ~636µs p50 under E-core hint (4.5× P-core slowdown, plausibly Pi 4-class). End-to-end forwarder + classifier + cache adds +2.37ms p50 vs direct upstream even on E-core, comfortably inside the relaxed <3ms v0.1 target. Cache makes Pi 4 viability essentially independent of classifier speed. Writeup in [`docs/pi4-projection-results.md`](pi4-projection-results.md). Real hardware verification still gates v0.1 release. (PR #11)

## Notes

- Tasks are listed in priority order. Top of the list = next thing to work on.
- A task gets checked off when its PR merges. The PR must include the PROJECT.md update.
- If a spike answers a question and produces no code worth keeping, that's a successful spike — write up what was learned and close it.
