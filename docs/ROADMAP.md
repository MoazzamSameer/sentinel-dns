# Roadmap

What we're building, in what order, and how we'll know each phase succeeded. Anchored to [`RESEARCH.md`](RESEARCH.md) (the why) and [`ARCHITECTURE.md`](ARCHITECTURE.md) (the how).

Time horizons are deliberately omitted. Phase gates are gated by acceptance criteria, not calendar dates.

---

## What v0.1 does that nothing else does

The honest answer, after looking at every comparable tool:

> **A self-hosted DNS resolver that explains every block in plain English, and catches fresh malicious domains via lexical analysis — not just threat-feed lookups.**

| Tool | Self-hosted | Explanations | Fresh-domain detection |
|---|---|---|---|
| Pi-hole | ✅ | ❌ (just "blocked") | ❌ (rule-based only) |
| Quad9 | ❌ | ❌ | Partial (feeds only) |
| NextDNS | ❌ | Category labels | Partial (feeds + some heuristics) |
| Cloudflare 1.1.1.1 (Families) | ❌ | ❌ | ❌ |
| AdGuard DNS | ❌ | Category labels | Partial |
| **sentinel-dns v0.1** | ✅ | ✅ structured + plain-English | ✅ inline lexical + async deeper |

The combination is the wedge. Each individual cell exists somewhere; nobody combines them.

---

## Phases

### Phase 0 — Research *(current)*

**Goal:** decide whether the wedge is real and what shape v0.1 takes.

**Acceptance criteria:**
- [ ] [`RESEARCH.md`](RESEARCH.md) — every section has a verdict (no remaining `_TBD._`)
- [ ] [`ARCHITECTURE.md`](ARCHITECTURE.md) — load-bearing decisions named, deferred questions explicitly tagged `(TBD: spike)`
- [ ] [`ROADMAP.md`](ROADMAP.md) (this doc) — phase gates and success metrics defined

**Exit:** all three docs land on `main`. Phase 1 begins.

### Phase 1 — Spike

**Goal:** answer the kill-criterion questions empirically. Decide whether to build v0.1 at all.

The spike phase is two short, focused experiments. Either one failing kills or reshapes the project — we want that information cheaply, before months of build.

**Spike A — Latency floor (gates K1):**
- A minimal Python forwarder that accepts UDP/53 and forwards to 1.1.1.1.
- Measure added latency vs. raw `dig @1.1.1.1` on representative domains.
- Add a stub inline classifier (any lightweight model returning a score) and re-measure.
- **Pass:** added p99 < 5ms with the classifier wired in.
- **Fail:** classifier alone adds >10ms p99 → reshape to async-only or kill.

**Spike B — ML lift (gates K2):**
- Pull URLhaus malicious-domain reports scoped to first-24h-after-disclosure.
- Build three classifiers on the same train/test split:
  - (a) Top public blocklists at time-of-discovery
  - (b) Hand-written heuristic ruleset
  - (c) Logistic regression on character n-grams
- Report recall at <1% FPR for each.
- **Pass:** (c) beats (a) by ≥10pp recall at <1% FPR, OR (b) does — either keeps the project alive (the second case reshapes us as a transparent rule-based resolver).
- **Fail:** neither beats threat feeds → kill or pivot.

**Acceptance criteria for Phase 1:**
- [ ] Spike A complete with measured numbers in a writeup
- [ ] Spike B complete with measured numbers in a writeup
- [ ] Explicit go/no-go decision recorded in `docs/`

**Exit:** if both pass, Phase 2 begins. If either fails, kill or pivot per the criteria above.

### Phase 2 — MVP v0.1

**Goal:** ship the smallest thing that proves the wedge to a real user.

**In scope (the v0.1 feature list):**

| Component | What it does | Why it's in v0.1 |
|---|---|---|
| Forwarder over UDP/53 | Accept queries, forward to upstream over DoH, return answers | Without this, nothing else matters |
| Static blocklist | URLhaus-sourced hashed set, refreshed daily | Floor of detection |
| Heuristic ruleset | ~5–10 hand-written rules covering DGA shape, typosquats, young-TLD signals | Floor of "value beyond Pi-hole" |
| Inline lexical classifier | Logistic regression on char n-grams, sub-ms inference | The thing that makes us ML-claimable |
| Async scorer | Worker pool, WHOIS age + ASN reputation lookups, decision cache writes | The "free for repeat queries" part |
| Plain-language explanations | Templated from structured reasons | The differentiator users actually feel |
| Local SQLite query log | Per-query record with retention config | So users can answer "what did my TV do today?" |
| CLI: `sentinel-dns tail`, `sentinel-dns explain <domain>` | Live tail + retrospective explanation | The v0.1 user interface |
| Single config file (TOML) | Sensible defaults, zero-config first run | Prosumer install bar |
| Distribution: PyPI + Docker | One install command per channel | Reach the homelab segment |

**Out of scope (deferred or ruled out):**
- Web UI — CLI + JSON only in v0.1
- Recursive resolution — we forward
- DNSSEC validation in-process
- DoH/DoT *server-side* termination
- Multi-tenancy
- Mobile apps
- Cloud-hosted offering (that's the SMB v0.2 path)
- LLM-generated explanations at query time (templated only)

**Acceptance criteria for Phase 2:**
- [ ] Forwarder passes a conformance test against a small real-world query corpus (no malformed responses, no dropped queries under load)
- [ ] Inline pipeline meets technical metrics below
- [ ] At least 3 documented "real catches" — domains the system blocked that public blocklists missed at the time
- [ ] One non-author runs it for ≥7 days on their network and provides feedback
- [ ] PyPI + Docker installs work clean on a fresh machine, documented in a quickstart

**Exit:** v0.1 published. Phase 3 begins if there's pull.

### Phase 3 — Beyond v0.1

Speculative — sequencing depends on what Phase 2 reveals about real users.

Candidate work, in rough priority:
- **Per-device behavioral baselining** — anomaly detection per client IP. Closes the third gap from RESEARCH.md.
- **Web dashboard** — query history, explanations, blocklist management. Driven by user feedback.
- **DoH/DoT server-side termination** — for users who want encrypted client→resolver too.
- **SMB packaging** — managed offering, multi-network, RBAC. Phase 2 of the user-segment plan.
- **Local DNSSEC validation**.

Phase 3 isn't planned in detail because planning past validated reality is wasted work.

---

## Success metrics

### Technical (Phase 2 v0.1)

These are gates the v0.1 release must meet:

| Metric | Target | Measured how |
|---|---|---|
| Added latency vs. raw forwarding | p50 < 1ms, p99 < 5ms | Bench against a fixed query corpus |
| Memory footprint | < 256MB resident steady-state | Run on a Raspberry Pi 4 for 24h |
| Inline pipeline throughput | > 1000 qps single-process | Synthetic load test |
| Detection rate on fresh malicious domains | ≥10pp recall over top public blocklist at <1% FPR | URLhaus first-24h corpus, held-out |
| False-positive rate on Tranco top 10k | < 0.1% | Held-out test |
| Cold-start time | < 2s from `sentinel-dns run` to first answer | Stopwatch |

If we miss the latency or FPR targets, we don't ship. Those are the things prosumers will revert over.

### Adoption (Phase 2 v0.1, 30-day window post-release)

These are the kill-criterion-K3 numbers from `RESEARCH.md`:

| Metric | Target | Source |
|---|---|---|
| Self-hosted installs | ≥ 100 | PyPI download stats + Docker pulls |
| GitHub stars | ≥ 100 | GitHub |
| Non-author "real catch" reports | ≥ 1 | GitHub issues, written-up |
| Substantive feedback (issues, PRs, blog mentions) | ≥ 5 distinct individuals | Manual count |

Stars are vanity; the catches and the substantive feedback are signal. Hitting the install number without any of those means we have curiosity, not pull.

### Long-term (Phase 3 entry gate)

We don't seriously plan Phase 3 unless:
- v0.1 has been running on at least one non-author network for ≥30 days without an incident the user couldn't self-resolve, AND
- We have ≥3 distinct user requests for the same Phase 3 candidate (don't build features one user asked for once)

---

## Kill criteria summary

Re-stated from `RESEARCH.md` for visibility — the spike phase makes these concrete:

- **K1:** inline classifier alone adds >10ms p99 → async-only or kill
- **K2:** ML doesn't beat blocklist + heuristics by ≥10pp recall at <1% FPR → ship as transparent rule-based resolver, drop AI claim
- **K3:** <100 installs in 30 days post-v0.1 → reconsider segment or shelve
- **K4:** major resolver ships explanations + zero-day detection during our window → shelve unless our privacy-architecture angle still differentiates
