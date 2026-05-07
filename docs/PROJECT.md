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
- [ ] Define MVP scope and success criteria in `docs/ROADMAP.md`
  - [ ] What does v0.1 do that nothing else does?
  - [ ] What metrics tell us the AI layer is earning its keep?
- [ ] Spike: proof-of-concept Python DNS resolver that answers `A` queries against an upstream
  - [ ] Choose library (`dnslib` vs. `dnspython` vs. raw `asyncio`)
  - [ ] Measure baseline latency on common domains
- [ ] Spike: domain classifier on a public dataset (e.g. URLhaus, PhishTank)
  - [ ] Pick a dataset and document its limitations
  - [ ] Train a baseline (logistic regression on n-grams) before reaching for deep learning
  - [ ] Report precision/recall on a held-out set — false positive rate is the metric that matters

## Completed

- Viability analysis with verdicts on latency, competitive landscape, user segments, privacy, AI-vs-rules — wedge identified as prosumer/homelab self-hosted, with kill criteria for the spike phase. (PR #1)
- Target architecture for v0.1 — forwarder (not recursive), hybrid inline + async AI pipeline, self-hosted privacy model, single-binary deployment. (PR #2)

## Notes

- Tasks are listed in priority order. Top of the list = next thing to work on.
- A task gets checked off when its PR merges. The PR must include the PROJECT.md update.
- If a spike answers a question and produces no code worth keeping, that's a successful spike — write up what was learned and close it.
