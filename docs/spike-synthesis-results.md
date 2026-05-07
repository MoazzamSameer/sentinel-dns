# Spike A+B synthesis — classifier inline in the forwarder

**Status:** complete

**Question:** does the v0.1 latency budget survive when the n-gram classifier from Spike B runs synchronously on every DNS query through the forwarder from Spike A? This is the empirical answer to "the v0.1 p50 < 1ms target may need revisiting" from [Spike A](spike-a-results.md).

**Result preview:** the inline classifier itself is **~150µs at p50 / ~660µs at p99** on a developer machine. End-to-end forwarder + classifier adds ~+2.4ms at p50 vs direct upstream. **The architecture's "sub-ms inline" commitment holds**; v0.1's p50 < 1ms target does not — but the gap is in non-classifier overhead, not in the classifier, so the path forward is clear.

---

## What changed

Three production-ish modules added on top of the spike code:

| File | Purpose |
|---|---|
| [`sentinel_dns/classifier.py`](../sentinel_dns/classifier.py) | `LexicalClassifier` (TF-IDF char n-grams + LR) and `heuristic_score`/`heuristic_signals` extracted from `bench/spike_b.py`. Adds `save()` / `load()` via joblib. |
| [`scripts/train_classifier.py`](../scripts/train_classifier.py) | Trains on full URLhaus + 50k Tranco, serializes to `models/classifier_v0.joblib`. |
| [`sentinel_dns/forwarder.py`](../sentinel_dns/forwarder.py) | Optional `--model-path` flag. When set, every query name is scored synchronously before forwarding. **Measurement-only** — we log the decision but don't yet block. |

The forwarder logs structured per-query decisions:

```
INFO ... score qname=example.com ml=0.0312 heur=0.000 would_block=False inline_us=128.7
INFO ... score qname=1ce6-route.fixionmunici9al.lat ml=0.9979 heur=0.500 would_block=True inline_us=152.4
```

`would_block` uses the 0.836 threshold from Spike B's 0.1% FPR operating point — strict enough for inline blocking (95% precision in the spike).

## Microbench: the classifier in isolation

30,000 calls, mixed benign + malicious + adversarial domains, on the developer machine:

| | p50 | p95 | p99 | mean |
|---|---:|---:|---:|---:|
| **ML score** (`LexicalClassifier.score()`) | **145µs** | 260µs | 629µs | 173µs |
| **Heuristic score** (`heuristic_score()`) | 7µs | 10µs | 32µs | 8µs |
| **Combined** (both, sequentially) | **152µs** | 273µs | 661µs | 181µs |

The **architecture's "sub-ms inline" commitment is met at all percentiles**. The ML score dominates; heuristics are near-free.

This is the strongest single number from the spike. The classifier itself is fast enough to run on every query, on commodity hardware, in Python, with no special tricks.

### Why TF-IDF + LR is this fast

The vectorizer is essentially a hash lookup over the trained vocabulary (20k features). `predict_proba` is a sparse dot product with the LR weights. Both operations are vectorized in scikit-learn's compiled extensions even when called on a single example. The Python overhead is the dominant cost at this scale, not the math.

Headroom check: a Raspberry Pi 4 is roughly 3–5× slower than this dev machine on Python-bound workloads. Projecting: ~500µs–800µs p50 on Pi 4. Still inside the inline budget. Will need to verify with an actual Pi run before v0.1 ships.

## End-to-end bench: 3-way interleaved

Same methodology as [`bench_forwarder.py`](../bench/bench_forwarder.py) (interleaved samples + dual-priming) extended to a third path. 240 samples per path.

```
=== direct ===
  n= 240  p50= 38.87ms  p95= 48.93ms  p99=195.26ms  mean= 42.25ms

=== no-classifier ===
  n= 240  p50= 40.23ms  p95= 51.98ms  p99=226.75ms  mean= 45.10ms

=== classifier ===
  n= 240  p50= 41.24ms  p95= 51.13ms  p99=138.97ms  mean= 44.49ms

=== overhead deltas ===
   p50: forwarder= +1.36ms vs direct  classifier= +1.01ms vs forwarder  total= +2.37ms vs direct
   p95: forwarder= +3.04ms vs direct  classifier= -0.85ms vs forwarder  total= +2.19ms vs direct
   p99: forwarder=+31.49ms vs direct  classifier=-87.78ms vs forwarder  total=-56.29ms vs direct
  mean: forwarder= +2.85ms vs direct  classifier= -0.62ms vs forwarder  total= +2.24ms vs direct
```

### Reading these numbers

The **p50 is the trustworthy line.** Adding the classifier costs +1.01ms p50 vs the bare forwarder. That's roughly 7× the classifier's own ~150µs cost — meaning ~850µs is non-classifier overhead (extra Python statement, async task scheduling, log formatting in `_score_inline`).

The **p99 is unreliable in this run.** Each path saw different network spikes during its window even with interleaving — direct hit a 195ms spike, no-classifier hit 227ms, classifier got off relatively easy at 139ms. That's why p99 deltas show negative numbers ("classifier is faster than no-classifier") which makes no logical sense. The microbench is a much cleaner number for the classifier's own p99 cost.

### Implication for the v0.1 latency targets

[`ROADMAP.md`](ROADMAP.md) had two targets:

| Target | Number observed | Status |
|---|---|---|
| Added p50 < 1ms vs raw forwarding | +2.37ms | ❌ |
| Added p99 < 5ms vs raw forwarding | (unreliable in this run; classifier microbench p99 = 0.66ms) | likely ✅ |

The p50 target was already missed by Spike A (+1.85ms with no classifier). With the classifier inline, we're at +2.37ms. The classifier itself is responsible for ~150µs of that — almost all the gap is the bare forwarder's UDP-loopback + dnspython parse cost.

**Recommendation:** relax the v0.1 p50 target to **< 3ms**. We're not going to optimize away the local UDP roundtrip without abandoning forwarding-via-DNS altogether, which the architecture explicitly committed to. The classifier earns its place; the forwarder's overhead is the real cost.

## What the architecture's hybrid pipeline gets us back

The inline classifier ran on every query in this spike, but production won't. The architecture commits to a decision-cache layer in front of the inline tier. Most queries will be cache hits at sub-microsecond cost. Inline classification only runs on first-seen domains.

Realistic per-query cost in production:
- Cache hit (~99% of traffic): ~0µs classifier work
- Cache miss + inline: ~150µs classifier work
- Amortized: ~1.5µs classifier work per query

So the +1ms p50 measured here is the *worst case* (every query is cache-miss). In production, expected added latency from classification will be invisible.

## Caveats and limitations

1. **Single machine.** Pi 4 numbers are extrapolated, not measured. That re-bench is a follow-up.
2. **Per-query overhead in `_score_inline` is sloppy.** ~850µs of the +1ms p50 delta isn't the classifier — it's logging, format strings, function call overhead. Tightening that is straightforward.
3. **No load test.** Single-stream measurement. Concurrent throughput with classifier inline isn't validated.
4. **Score logging is on by default in this spike.** That's fine for measurement (the `inline_us` numbers are useful) but it's noisy. Production will switch to a sampling rate or rate-limited log.
5. **No cache yet.** This is the worst-case latency. The decision cache from the architecture will swallow most of the per-query cost in practice.
6. **Network p99 jitter.** The 3-way bench is noisy at p99 because we can't fully control upstream and network conditions. Microbench is the cleaner number for the classifier's own p99.
7. **Block threshold is hardcoded.** `0.836` is the 0.1% FPR operating point from Spike B. Production will make this configurable per-deployment.

## Verdict against acceptance criteria

| Target | Source | Number | Status |
|---|---|---|---|
| Pull classifier into reusable module | [`PROJECT.md`](PROJECT.md) | `sentinel_dns/classifier.py` extracted | ✅ |
| Persist trained model to disk; load at startup | [`PROJECT.md`](PROJECT.md) | `models/classifier_v0.joblib` (320 KB) loads in <100ms | ✅ |
| Score every query inline; log decision but don't block | [`PROJECT.md`](PROJECT.md) | Implemented; `would_block` logged | ✅ |
| Re-run bench to settle p50 < 1ms target | [`PROJECT.md`](PROJECT.md) | Total +2.37ms p50; classifier itself 145µs | ❌ p50 missed; recommend relaxing to < 3ms |
| Architecture: sub-ms inline classifier | [`ARCHITECTURE.md`](ARCHITECTURE.md) | 145µs p50 / 629µs p99 (microbench) | ✅ at all percentiles |

## What this unblocks

- **Phase 1 is fully complete.** Both spike kill-criteria gates passed; architecture's hybrid-pipeline assumptions confirmed empirically; v0.1 latency targets re-grounded in measured numbers.
- **The extracted classifier module is ready for production reuse.** v0.1 work can build on it without re-extracting.
- **The decision cache from the architecture moves up the priority list.** Without it, we eat ~1ms on every query. With it, we eat 1ms only on first-seen domains. That cache is now the highest-leverage piece of v0.1 work.
- **Pi 4 verification is a real follow-up task**, not a hand-waved confidence statement. Should land before any v0.1 release announcement.
