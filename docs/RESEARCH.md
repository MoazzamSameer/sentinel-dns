# Research

Open questions. Each section ends with a verdict once we have one.

## 1. Latency budget

DNS resolution has a hard ceiling: users notice anything above ~100ms, and high-traffic resolvers answer in <10ms. Every millisecond we spend on AI inference is a millisecond we're not competing on.

**Questions:**
- What's the p50 / p99 round-trip from a home network to 1.1.1.1?
- How fast can a small classifier (e.g. character-level CNN, ~1M params) score a single domain on commodity CPU?
- Can inference run async (don't block the response, log/alert after) without losing the security value?

**Verdict:** _TBD._

## 2. Competitive landscape

Existing players:

| Service | Model | What it does | Gap |
|---|---|---|---|
| Cloudflare 1.1.1.1 | Free public DNS | Fast, privacy-respecting | No filtering by default |
| Quad9 | Free, security-focused | Blocks malware/phishing via threat feeds | Rule-based, no per-user explanation |
| NextDNS | Freemium, configurable | Per-user blocklists, analytics dashboard | Limited "why blocked" detail |
| Pi-hole | Self-hosted, free | Network-wide ad blocking | Rule-based only, requires setup |
| AdGuard DNS | Freemium | Ad/tracker blocking | Similar to NextDNS |

**Question:** Where's the white space? Candidates:
- "Why blocked" explanations a normal user understands
- Detection of *new* malicious domains (zero-day) via ML, not feed lookups
- Per-device behavioral anomaly detection ("your fridge made 500 requests at 3am")
- Privacy-first: on-device inference, no centralized query log

**Verdict:** _TBD._

## 3. Who's the user?

| Segment | Pain | Willingness to pay |
|---|---|---|
| Consumer | "Is my smart TV spying on me?" | Low — expects free |
| Prosumer / homelab | "I want my Pi-hole to be smarter" | Medium — pays for NextDNS today |
| SMB | "We can't afford a SOC but we still get phished" | High |
| Enterprise | Existing solutions are mature here | Already served |

**Verdict:** _TBD._ Recommend prosumer + SMB as the wedge.

## 4. Privacy

Hardest problem. To do AI on queries, you need to see queries. That's a sensitive data stream.

**Options:**
- **On-device inference.** Model runs locally, no query data leaves the network. Limits model size and update cadence.
- **Federated learning.** Devices contribute model updates without sending raw queries. Complex, slow.
- **Differential privacy.** Aggregate stats only. Loses per-device insight.
- **Trust + audits.** Standard "we don't log" promise, third-party audits. What Cloudflare does. Hard to differentiate on.

**Verdict:** _TBD._

## 5. AI vs. rule-based

Honest question: does AI actually outperform a well-maintained blocklist + heuristics?

**To test:**
- Take 10k recent malicious domains (URLhaus). What % are caught by the top 5 public blocklists at the time the domain was first seen?
- Train a simple classifier on domain *names* alone (no DNS traffic features). What's its precision on the same set?
- The answer determines whether "AI DNS" is a real product or marketing on top of a feed aggregator.

**Verdict:** _TBD._
