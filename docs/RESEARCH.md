# Research

The viability question for `sentinel-dns`, broken into five sections. Each ends with a verdict.

This document is a desk analysis. Where it cites numbers, those are either published figures or quick measurements from a single developer machine — they are directional, not authoritative. Real benchmarking is part of the spike phase (`docs/PROJECT.md`).

---

## 1. Latency budget

### The constraint

DNS sits on the critical path of every web request. If our resolver is slow, page loads are slow, users notice, users switch back. The budget is unforgiving:

- **Browser DNS timeout:** typically 5s, but the browser starts trying alternatives well before that.
- **Perceptible to users:** anything past ~100ms of DNS time on a fresh page load.
- **Competitive baseline:** Cloudflare's 1.1.1.1 averages ~10–15ms globally. Quad9 ~30ms. Public benchmarks (DNSPerf) routinely show p50s under 20ms for top resolvers.

### Baseline measurement

Five queries each to three public resolvers, from a single developer machine on a residential connection (cache state mixed):

| Resolver | Query times (ms) |
|---|---|
| 1.1.1.1 (Cloudflare) | 37, 37, 364, 74, 72 |
| 9.9.9.9 (Quad9) | 37, 42, 67, 49, 41 |
| 8.8.8.8 (Google) | 36, 36, 38, 36, 40 |

Steady-state cached lookups run ~35–50ms from this machine. The 364ms spike on 1.1.1.1 is a cold-cache recursive fetch — the worst case we have to compete with, not the average.

### How much room does AI inference have?

The honest answer depends on **where** inference happens:

**Inline (block before responding):**
- Inference must complete before we send the answer back.
- Realistic budget: ~5ms added latency before users feel it, ~10ms before competitive resolvers beat us on cached queries.
- That's enough room for: hashed blocklist lookup (~µs), small classifier on lexical features only (logistic regression / small CNN, single-digit ms on CPU).
- Not enough room for: anything that loads a transformer, anything that issues a network call, anything that does feature engineering across recent history.

**Async (answer first, score after):**
- Inference happens after the response goes out.
- Budget: effectively unbounded.
- Trade-off: the **first** query to a malicious domain succeeds. Subsequent queries get blocked once the classifier has scored it.
- This is the same model AV scanners use ("scan after exec, quarantine on next run"). Acceptable for most threat models — most attacks need many queries to do damage.

**Hybrid (inline cheap, async deep):**
- Inline: blocklist + tiny lexical classifier (<1ms).
- Async: richer model, behavioral signals, threat intel lookups, results cached for next time.
- Best of both. The added complexity is real but justified.

### Verdict

**Latency is not the kill criterion.** A hybrid pipeline (inline lookup + sub-millisecond lexical classifier, async heavier scoring) fits comfortably in the budget. Pure inline AI with anything bigger than a small model will lose on speed, so we shouldn't promise it.

The spike phase needs to confirm: (a) a small classifier really does run in <1ms on commodity CPU, and (b) the first-query-leak from async scoring is acceptable in practice.

---

## 2. Competitive landscape

The six players that matter today, and what each does well and badly:

| Service | Model | Strengths | Weaknesses |
|---|---|---|---|
| **Cloudflare 1.1.1.1** | Free public | Fastest globally; audited "no-log" privacy; "for Families" variant blocks malware | No filtering on default endpoint; minimal user-facing analysis |
| **Quad9** | Free, nonprofit | Threat-feed blocking from ~20 sources; Swiss jurisdiction; privacy-respecting | Block-only ("blocked for security" is the entire explanation); no per-user customization |
| **NextDNS** | Freemium ($2/mo) | Per-user blocklists; rich analytics dashboard; configurable; APIs | Reasoning behind blocks shown as category labels, not explanations; centralized log |
| **AdGuard DNS** | Freemium | Ad/tracker blocking; family controls | Similar feature surface to NextDNS; less differentiated |
| **ControlD** | Freemium ($2/mo) | Per-device profiles; advanced filtering rules | Niche; smaller blocklist coverage |
| **Pi-hole** | Self-hosted, free | Network-wide; open source; runs on $35 hardware; privacy by default | Rule-based only; requires technical setup; no detection of new threats |

### What none of them do well

Looking across the table, four real gaps:

1. **Plain-language explanations.** "Blocked: malware" is not an explanation. A user wants to know *what* the domain is, *who* registered it, *why* it's classified that way, *what device on their network asked for it*. Nobody does this well.
2. **Detection of fresh malicious domains** (registered in the last 24–72 hours). Threat feeds lag this by hours-to-days. Lexical / structural ML is the standard answer in the academic literature and barely productized.
3. **Per-device behavioral signals.** Pi-hole and NextDNS show per-device counts; nobody flags "this device is behaving anomalously vs. its own baseline."
4. **Conversational query.** "What did my smart TV connect to today?" → natural-language answer. Currently you read a JSON log.

### Where's the wedge?

Gaps (1) and (2) reinforce each other: the same model that catches a fresh domain can also explain why it caught it. (3) is interesting but harder — it needs per-client state and longer baselining. (4) is a feature, not a product.

So the wedge is: **a resolver that catches fresh malicious domains via lexical/structural ML, and explains its decisions in plain English.** Everything else is table stakes (fast, privacy-respecting) or later-phase features.

### Verdict

There is a real gap, and it's defensible. The risk is that Cloudflare or NextDNS adds explanations and lexical detection in a quarter and obviates us. The mitigation: ship the open-source self-hosted version first — that's a position they structurally won't take, because their business is hosted DNS.

---

## 3. Who's the user?

| Segment | Pain | Existing solution | Willingness to pay | Acquisition difficulty |
|---|---|---|---|---|
| **Consumer** | "Is my TV spying on me?" — vague, low-priority | Default ISP DNS (no filtering) or 1.1.1.1 | Very low | Very high — they don't change DNS |
| **Prosumer / homelab** | "My Pi-hole is dumb — it can't catch new threats" | Pi-hole + NextDNS | $5–15/mo | Low — reachable on r/homelab, HN, lobste.rs |
| **SMB** | "We get phished, we don't have a SOC, MS Defender misses things" | Cisco Umbrella ($3/user/mo+, but enterprise-priced) or nothing | $500–5000/mo | Medium — need sales motion |
| **Enterprise** | Already mostly solved | Umbrella, Zscaler, Akamai | High but locked up | Very high — incumbent moats |

### Strategic logic

- **Consumer is a bad first customer.** They won't change DNS settings, won't pay, and provide noisy signal. Skip.
- **Prosumer is the right wedge.** They are technically capable (zero install support burden), they self-select for caring about this stuff, they will tell you exactly what's broken, and they convert into evangelists. The market is small but loud. Pi-hole has ~250k+ active installations — this is the addressable beachhead.
- **SMB is where the revenue is.** SMBs underserved by the enterprise tools and overserved by free consumer DNS. They have budget, they have pain, they don't have time to evaluate carefully — meaning a credible product with good docs wins the trial.
- **Enterprise is a trap for v1.** Long sales cycles, RFPs, certifications. Revisit once we have ~50 paying SMBs and a real reference list.

### Verdict

**Prosumer first to prove the tech and earn evangelism. SMB second for revenue.** The product needs to be designed so the same code runs in both contexts — a self-hosted prosumer build and a managed SMB offering.

---

## 4. Privacy

DNS queries are an intimate data stream. Every domain a device touches is a fact about its user. Centralizing that data is both a liability and a marketing problem ("we don't log" is a claim we'd have to defend forever).

### Options

| Option | What it means | Pros | Cons |
|---|---|---|---|
| **On-device / on-network inference** | Model runs in the resolver itself; no query leaves the user's network | Bulletproof privacy story; no centralized data risk | Limits model size; updates pushed instead of pulled; per-user behavioral baselining is harder |
| **Federated learning** | Devices send model gradients, never raw queries | Privacy-preserving + improves with scale | Complex; slow to develop; mostly research-tier today |
| **Differential privacy on aggregates** | Centralized stats only, with noise injection | Allows global insight | Loses per-device value; doesn't satisfy users who want zero-trust |
| **Trust + audits** | Standard "we don't log raw queries" + third-party audit | Cloudflare-style; what most users settle for | Hard to differentiate on; you're competing on Cloudflare's home turf |

### Verdict

**Self-hosted-first naturally solves privacy** for the prosumer wedge — queries never leave the user's network. The hosted SMB version comes later with a documented privacy posture (no raw query logs, minimal retention, third-party audit). Skip federated learning until v2+; it's not worth the engineering cost early.

This isn't just defensive. "Privacy by architecture, not by promise" is the actual marketing line for prosumers.

---

## 5. AI vs. rule-based

The existential question: **does ML actually beat a well-maintained blocklist?**

It's worth being direct: most "AI security" products are mostly threat feeds with marketing. If `sentinel-dns` ends up there, it has no reason to exist.

### Where ML genuinely adds signal

The academic literature (Antonakakis et al. on DGA detection, Bilge et al. on domain reputation, plenty since) is clear that lexical and structural features of domain names carry real signal beyond what threat feeds catch:

- **DGA-generated domains** (algorithmically created C2 endpoints) have characteristic n-gram distributions. Detection rates of 90%+ are routine in published work.
- **Typosquats** (`g00gle.com`, `microsft-update.net`) are catchable from string similarity to a known-good list.
- **Newly-registered + suspicious-TLD + high-entropy** combinations are statistical signals threat feeds miss for the first few hours of a domain's life.

### Where ML is overkill

For most of these signals, smart heuristics get you 70–80% of the value:

- "Block any domain registered <72 hours ago on `.xyz` / `.top` / `.click` with >0.7 character entropy" catches a lot of DGAs without a trained model.
- "Edit distance ≤2 from any of the top 10k Tranco domains" catches most typosquats.

The ML version generalizes better and adapts to new patterns without rule rewriting, but it's not a 10x improvement over careful heuristics.

### Honest framing

The right pitch is **"smart heuristics + ML for edge cases + plain-language explanations."** Not "AI-first DNS." That framing is honest, defensible, and harder for a competitor to copy in a marketing slide.

### Test that decides this

The spike phase has to answer concretely:

1. Take 10k malicious domains from URLhaus, scoped to the first 24 hours after each was first reported.
2. Measure recall at <1% FPR for: (a) top public blocklists at time-of-discovery, (b) heuristic ruleset, (c) a small ML classifier on lexical features.
3. If (c) does not beat (a) by ≥10 percentage points of recall, the AI story is marketing and we should ship as a transparent rule-based resolver instead.

### Verdict

**ML adds real but bounded value, and the honest framing is heuristics + ML + explanations, not "AI DNS."** The kill criterion above gates whether ML stays in the v0.1 critical path.

---

## Kill criteria

We stop, pivot, or shelve if any of these turn out true after the spike phase:

- **K1 — Latency:** The smallest viable inline classifier adds >10ms p50 on commodity CPU. Mitigation: go async-only. If async-only doesn't catch enough threats fast enough, kill.
- **K2 — ML is marketing:** A trained classifier doesn't beat a well-maintained blocklist + heuristics by ≥10pp of recall at <1% FPR on fresh malicious domains. Reframe as a transparent heuristic resolver, drop the AI claim.
- **K3 — No prosumer pull:** After a working alpha posted to r/homelab + HN, fewer than 100 self-hosted installs in 30 days. The wedge isn't real; reconsider the segment or shelve.
- **K4 — Cloudflare ships it:** A major resolver releases per-decision plain-language explanations + zero-day detection during our research window. Differentiation collapses. Shelve unless we have a privacy-architecture angle they structurally can't match.

---

## Recommendation

**Build it, but with intellectual honesty about what's novel.**

- **Wedge:** prosumer / homelab, self-hosted, open source, MIT license.
- **Differentiator:** explain-every-decision + lexical/structural detection of fresh malicious domains.
- **Architecture posture:** privacy by being self-hosted, not by promise.
- **Honest framing:** "smart heuristics + ML + explanations" — not "AI-first."
- **Phase 2 question:** does the same codebase, packaged as a managed service, win SMBs?

The next gate is the spike phase. It has to answer the K1 and K2 questions empirically before we put serious time into a v0.1.
