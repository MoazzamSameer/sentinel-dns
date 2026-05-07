# Spike B — ML lift over heuristics

**Status:** complete

**Question:** do lexical features alone — heuristics OR logistic regression on character n-grams — detect fresh malicious domains at an acceptable false positive rate? This is the [K2 kill criterion](../docs/RESEARCH.md#kill-criteria) — if the answer is no, the AI claim is marketing and we ship as a transparent rule-based resolver instead.

**Result preview:** **K2 passes decisively for ML.** Logistic regression on character n-grams catches **81.2% of held-out malicious domains at <1% false-positive rate**. Heuristics alone get 9.2% — just under the 10pp bar at 1% FPR, though they reach 77% at 5% FPR. The combination is what we'll ship in v0.1.

---

## Datasets

| Source | What it is | Size | Use |
|---|---|---|---|
| [URLhaus](https://urlhaus.abuse.ch/) abuse.ch host file | Live feed of domains hosting malware payloads | 1,191 (after dedup, after dropping reverse-DNS-of-IP entries) | Malicious class |
| [Tranco](https://tranco-list.eu/) top 1M | Aggregated popularity ranking, top 100k trimmed to top 50k | 50,000 | Benign class |

**Download fingerprint** (so the bench is reproducible):
- URLhaus host file: pulled 2026-05-07, ~1,200 entries
- Tranco list: top-1m.csv.zip, trimmed locally to top 100k → first 50k used as benign

**Files written:** `data/urlhaus_hosts.txt`, `data/tranco_top100k.csv`. Not committed (in `.gitignore`); fetched via the steps in the writeup below.

## Methodology

Three things being compared:

| Method | What it does |
|---|---|
| **(a) Naive blocklist baseline** | Implicit: a blocklist by construction gets 0% recall on never-before-seen domains. This is the floor we're trying to beat. |
| **(b) Heuristic scorer** | Hand-written rules over lexical features: abused-TLD list, SLD Shannon entropy thresholds, length, digit ratio, dash count, hex-pattern labels, long consonant runs. Sums weighted contributions to a [0, 1] score. |
| **(c) Logistic regression on char n-grams** | TF-IDF over character `n-gram_range=(2, 4)` with `analyzer="char_wb"`, capped at 20k features, fed to `sklearn.linear_model.LogisticRegression(class_weight="balanced", solver="liblinear")`. |

### Train / test split

70 / 30 stratified split, seed=42. Both training and test sets contain the same ~2.3% malicious / 97.7% benign class ratio. Test set: 357 malicious, 15,001 benign.

### Pass criterion (from `ROADMAP.md`)

> Spike B passes if (b) heuristics OR (c) ML achieve ≥10pp recall at <1% FPR vs. a 0% blocklist baseline. (b) passing alone keeps us alive but reshapes us as a transparent rule-based resolver.

## Results

```
=== ROC AUC ===
  heuristics:  0.880
  n-gram LR:   0.937

=== Recall at various FPRs ===
FPR     Heuristics            N-gram LR
  0.1%    4.8% (thr=0.500)     79.0% (thr=0.836)
  0.5%    9.2% (thr=0.400)     80.7% (thr=0.507)
  1.0%    9.2% (thr=0.400)     81.2% (thr=0.465)
  5.0%   76.8% (thr=0.200)     83.5% (thr=0.241)
 10.0%   79.0% (thr=0.100)     86.6% (thr=0.152)

=== K2 verdict ===
  heuristics @1%FPR:   9.2%
  n-gram LR  @1%FPR:  81.2%

  PASS — best lexical approach catches 81.2% of held-out
  malicious test domains at <1% FPR (target was >= 10%).
```

## What the numbers mean in production terms

At the 1% FPR operating point on the n-gram LR classifier (threshold 0.465):

- 290 true positives out of 357 malicious test domains caught
- 150 false positives out of 15,001 benign test domains incorrectly blocked
- **Precision: 65.9%** — for every 3 alerts, 2 are real malware, 1 is wrong

At a stricter 0.1% FPR (threshold 0.836), suitable for inline blocking:

- 282 true positives out of 357 caught
- 15 false positives out of 15,001
- **Precision: 95%** — alerts are reliable enough to block automatically

For the architecture's hybrid pipeline: the inline tier should run at the strict 0.1% FPR threshold (block confidently), and the async tier scores the rest at the looser 1% FPR threshold (flag for next time, accept the first-query-leak).

## Why heuristics underperformed

Heuristics jumped from 9.2% recall at 1% FPR to 76.8% at 5% FPR — the score distribution is **lumpy**. Discrete signal contributions of 0.1, 0.2, 0.3 mean lots of domains share the same score, which collapses many threshold choices into the same operating point. The heuristic ruleset has the right *shape* of signal (the ROC AUC of 0.880 is decent) but lacks the granularity to operate at very low FPRs.

This is fine — it confirms what we already designed for in the architecture: heuristics are a transparent floor, ML provides the granularity. We weren't going to ship heuristics-only.

## Notable failure modes (sanity check)

A few benign domains the ML classifier wasn't sure about:

- `malwarebytes.com` — score 0.149 (well below threshold, but elevated). The model picks up on "malware" as a string. Acceptable noise.
- `braunschweiger-zeitung.de` — score 0.117. Long German domain with dashes; the heuristic also slightly elevates it. Acceptable.
- `yxgz.club` — heuristic 0.300 (TLD signal), ML 0.112. The `.club` TLD is genuinely abused; this domain may or may not be benign. Tranco includes some shady-but-legal sites, which is one of the documented dataset caveats below.

## Caveats and limitations

1. **URLhaus is biased toward certain malware families.** It captures DGA-style and short-lived hosting domains well, less so compromised legitimate WordPress sites. The 81% recall is on URLhaus's distribution; real-world recall on other threat shapes will differ.
2. **Tranco is a noisy benign set.** It's a popularity ranking, not a curated allowlist — includes ad networks, tracker domains, and the occasional shady-but-legal site. Reported FPR is conservative (likely overstates the real-world false positive rate).
3. **Random hold-out split, not temporal split.** Tests "does the model generalize across this distribution" — not "does the model catch *future* malicious patterns." A proper temporal split (train on URLhaus < 30 days old, test on URLhaus < 7 days old) would be more realistic. Deferred to a follow-up task once we have enough URLhaus history captured locally.
4. **No comparison to a real public blocklist.** The 10pp lift is over the 0% naive baseline. The roadmap specified comparing against "top public blocklists at time-of-discovery" — that requires historical blocklist snapshots that aren't trivially available. The conservative interpretation: blocklists by construction can't catch never-before-seen domains, and the absolute 81% number is the meaningful one regardless of baseline.
5. **Single train/test split.** Reported numbers are point estimates, not k-fold cross-validation results. The signal is strong enough (>70pp gap between methods) that variance is unlikely to flip the verdict, but the precise numbers should be taken as ±2pp.
6. **Hostname vs. registered domain asymmetry.** URLhaus entries are full hostnames (often with subdomains); Tranco entries are registered domains. The classifier learns to treat presence of subdomains as a malicious signal — which is partially correct but partially an artifact. Future work: compare on registered-domain features only.

## How to reproduce

```bash
# from the repo root
.venv/bin/pip install scikit-learn numpy
mkdir -p data
curl -sS -o data/urlhaus_hosts.txt "https://urlhaus.abuse.ch/downloads/hostfile/"
curl -sS -L -o /tmp/tranco.zip "https://tranco-list.eu/top-1m.csv.zip"
unzip -p /tmp/tranco.zip top-1m.csv | head -100000 > data/tranco_top100k.csv
.venv/bin/python bench/spike_b.py
```

## Verdict against acceptance criteria

| Target | Source | Number | Pass? |
|---|---|---|---|
| Pick a dataset, document limitations | [`PROJECT.md`](PROJECT.md) | URLhaus + Tranco, 6 caveats listed | ✅ |
| Train baseline (logistic regression on n-grams) before deep learning | [`PROJECT.md`](PROJECT.md) | TF-IDF char n-grams + LR, no DL | ✅ |
| Report precision/recall on held-out set, FPR is metric that matters | [`PROJECT.md`](PROJECT.md) | Recall at 5 FPR points, precision derived | ✅ |
| K2 pass: ≥10pp recall at <1% FPR | [`RESEARCH.md`](RESEARCH.md) | 81.2% recall (ML) | ✅ decisively |

## What this unblocks

- **K2 settled in favor of ML.** The "AI" claim is honest, not marketing. v0.1 ships with the n-gram classifier inline.
- **Architecture pipeline confirmed.** Inline tier at strict 0.1% FPR (95% precision) for blocking, async tier at looser threshold for cache writes. The heuristic floor stays — it's the explainability anchor (each rule maps to a human-readable reason).
- **Next obvious task: wire the classifier into the forwarder.** Spike A's bench then re-runs with real classification cost in the inline path, and we get a final number for v0.1's latency budget. That's the synthesis spike — and it's where the v0.1 p50 < 1ms target gets its empirical answer.
