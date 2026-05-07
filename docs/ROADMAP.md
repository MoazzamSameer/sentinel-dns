# Roadmap

Time horizons are guesses. The research phase decides whether any of this is real.

## Phase 0 — Research (current)

**Done when:** `RESEARCH.md` has verdicts on every section, and we can answer "what's the wedge?" in one sentence.

## Phase 1 — Spike (next)

**Done when:**
- A Python resolver answers `A` queries, forwarded to an upstream, with measured latency.
- A baseline domain classifier reports precision/recall on a public malicious-domain dataset.
- We have a yes/no on whether the AI layer beats a well-maintained blocklist on real data.

If the answer is no, we pivot or shelve. If yes, Phase 2.

## Phase 2 — MVP v0.1

Tentative scope (will be sharpened by Phase 1 results):
- Local resolver (UDP/53 + DoH) with rule-based blocking and async ML scoring
- CLI + JSON log output
- One target user segment (likely prosumer / homelab)

## Phase 3 — Beyond v0.1

Anything past Phase 2 is speculation. Candidates: dashboard, multi-device telemetry, on-device inference for privacy, SMB packaging.

## Success metrics

- **Phase 1 spike:** classifier beats top public blocklist by ≥10% recall at <1% false-positive rate on held-out malicious domains.
- **Phase 2 MVP:** added latency p99 < 5ms vs. raw forwarding.
- **Phase 3:** at least one non-author user runs it for a week and reports a real catch.
