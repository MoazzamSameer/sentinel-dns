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
- [ ] Spike A+B synthesis: wire the n-gram classifier into the forwarder, re-bench latency
  - [ ] Pull classifier into a reusable module (extract from `bench/spike_b.py`)
  - [ ] Persist trained model to disk; load at forwarder startup
  - [ ] Score every query inline; log decision but don't block yet (measurement, not enforcement)
  - [ ] Re-run bench with classifier inline; settle whether v0.1's p50 < 1ms target is reachable or needs relaxing

## Completed

- Viability analysis with verdicts on latency, competitive landscape, user segments, privacy, AI-vs-rules — wedge identified as prosumer/homelab self-hosted, with kill criteria for the spike phase. (PR #1)
- Target architecture for v0.1 — forwarder (not recursive), hybrid inline + async AI pipeline, self-hosted privacy model, single-binary deployment. (PR #2)
- MVP scope and success criteria for v0.1 — concrete in/out feature list, two-spike Phase 1 plan with go/no-go gates, technical + adoption targets, K1–K4 kill criteria carried forward. (PR #3)
- Spike A — minimal asyncio forwarder over dnspython, +1.85ms p50 / +0.37ms p99 added latency vs direct upstream. Within Spike A's pass threshold; v0.1 p50 < 1ms target needs revisiting after Spike B. Writeup in [`docs/spike-a-results.md`](spike-a-results.md). (PR #4)
- Spike B — domain classifier on URLhaus + Tranco. K2 passes decisively: logistic regression on char n-grams catches 81.2% of held-out malicious domains at <1% FPR (heuristics 9.2%). The "AI" claim is honest, not marketing. Writeup in [`docs/spike-b-results.md`](spike-b-results.md). (PR #5)

## Notes

- Tasks are listed in priority order. Top of the list = next thing to work on.
- A task gets checked off when its PR merges. The PR must include the PROJECT.md update.
- If a spike answers a question and produces no code worth keeping, that's a successful spike — write up what was learned and close it.
