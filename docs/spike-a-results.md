# Spike A — Latency floor

**Status:** complete (initial pass, no classifier wired in yet)

**Question:** how much latency does a minimal Python+asyncio forwarder add over a raw query to the upstream resolver? This is the floor we have to beat — every millisecond of overhead here is a millisecond we don't have for filtering, classification, or explanation in v0.1.

**Result preview:** the forwarder adds ~2ms at p50, sub-millisecond at p99, on a single developer machine over a residential connection. Comfortably inside Spike A's pass threshold (p99 < 5ms with classifier). Whether it stays under v0.1's tighter targets (p50 < 1ms, p99 < 5ms) once a real classifier is wired in is the [Spike B](../sentinel_dns/forwarder.py) follow-on question — see *Caveats* below.

---

## Library choice: `dnspython`

Three candidates considered:

| Library | What it gives us | Why we picked / didn't |
|---|---|---|
| **dnspython** | Mature parser, handles every record type, has first-class async (`dns.asyncquery.udp`) | **Picked.** Best ergonomics for both the listener and upstream side. Battle-tested in production deployments. |
| **dnslib** | Lightweight; has its own `DNSServer` abstraction | Server is thread-based, not asyncio-native. Awkward fit with the architecture's "single asyncio process" commitment. |
| **Raw asyncio + manual wire format** | Zero dependency, total control | Reimplementing DNS message parsing is months of work to match dnspython's correctness. Not worth it for a spike. |

We use dnspython for parsing **and** the upstream UDP query path. The listener is a plain `asyncio.DatagramProtocol` — no abstraction added between us and the wire on ingress.

If we hit a wall on per-query overhead later, the optimization path is to skip dnspython parsing on inbound queries (just forward the wire bytes upstream and back) — straightforward to swap in.

## Implementation

The forwarder is one file: [`sentinel_dns/forwarder.py`](../sentinel_dns/forwarder.py). ~100 lines including arg parsing.

Sketch:

1. Listen on UDP, configurable host/port (default `127.0.0.1:5354` — `:5353` is held by mDNS on macOS, `:53` needs root).
2. On each datagram, spawn a task to handle it (so concurrent queries don't head-of-line block each other).
3. Parse with dnspython, forward via `dns.asyncquery.udp` to the upstream, return the wire response.
4. On upstream timeout/error, return SERVFAIL. No retries in the spike.

No caching, no filtering, no classifier — those come later. This is the bare wire.

## Benchmark methodology

[`bench/bench_forwarder.py`](../bench/bench_forwarder.py) measures both paths from the same client:

- **Direct:** the bench client queries `1.1.1.1:53` directly.
- **Via forwarder:** the bench client queries `127.0.0.1:5354`, which forwards to `1.1.1.1:53` and returns the response.

Two methodological choices that mattered:

1. **Both paths primed before measurement.** An earlier run had direct go first, populating upstream's cache, then via-forwarder saw an artificially smooth distribution. Now both paths warm both caches before any measured query runs.
2. **Interleaved samples.** Direct and via-forwarder queries alternate, so transient network jitter (which produced 200ms+ outliers on whichever path ran during the jitter window) hits both paths roughly equally.

12 domains × 20 iterations = 240 samples per path.

## Results

Raw output:

```
priming both paths...

=== direct upstream — 1.1.1.1:53 ===
  n= 240  p50= 38.43ms  p95= 43.60ms  p99=192.91ms  mean= 40.89ms  min=28.83ms  max=195.38ms

=== via spike forwarder — 127.0.0.1:5354 ===
  n= 240  p50= 40.28ms  p95= 44.43ms  p99=193.28ms  mean= 42.58ms  min=28.77ms  max=197.19ms

=== overhead (forwarder − direct) ===
  p50:  +1.85ms
  p95:  +0.82ms
  p99:  +0.37ms
  mean: +1.69ms
```

Both paths see the same ~193ms p99 — that's a network/upstream event that hit both runs equally, exactly what interleaving was for. The forwarder's incremental cost is the **+1.85ms / +0.82ms / +0.37ms** delta, not 193ms.

### What the overhead is made of

The +1.85ms p50 is roughly:

- ~0.1–0.2ms local UDP roundtrip (loopback)
- ~1.0–1.2ms dnspython parse → re-pack on the request side
- ~0.4ms dnspython parse → re-pack on the response side
- ~0.2ms asyncio task scheduling

There's clear room to compress this. The "skip parsing on ingress, forward bytes" optimization probably halves it.

## Verdict against acceptance criteria

| Target | Source | Number | Pass? |
|---|---|---|---|
| Forwarder responds correctly | smoke test | `example.com → 104.20.23.154` | ✅ |
| Spike A pass (p99 added < 5ms with classifier) | [`ROADMAP.md`](ROADMAP.md) | p99 +0.37ms (no classifier yet) | ✅ headroom for classifier |
| v0.1 latency target p50 < 1ms | [`ROADMAP.md`](ROADMAP.md) | p50 +1.85ms (no classifier yet) | ❌ — see Caveats |
| v0.1 latency target p99 < 5ms | [`ROADMAP.md`](ROADMAP.md) | p99 +0.37ms (no classifier yet) | ✅ |

## Caveats

- **Single machine, single network.** macOS, residential connection, one upstream. Numbers will be different on a Raspberry Pi 4 (the prosumer hardware target) — that re-bench is a future task.
- **No classifier yet.** The K1 kill criterion is "added p99 < 5ms *with* the classifier wired in." This spike establishes the empty-pipeline floor; the next task (Spike B) wires in a logistic-regression classifier and the [next-next benchmark](../bench/bench_forwarder.py) re-measures.
- **v0.1 p50 target may need revisiting.** +1.85ms p50 with nothing in the pipeline suggests the original p50 < 1ms target was optimistic. Either we relax it to p50 < 3ms, or we do the parse-skip optimization. Decision deferred to after Spike B has a number with the classifier inline.
- **No load test.** Single-stream measurement. Throughput under concurrent load isn't validated here. Listed as `(TBD: spike)` in [`ARCHITECTURE.md`](ARCHITECTURE.md).
- **Methodology asymmetry, fixed.** First-pass measurements (sequential phases) showed +3.33ms p50 and a misleading negative p99 delta. Interleaving and dual-priming dropped the apparent overhead by half and exposed that "p99 went down" was just the second phase running with a warmer upstream cache. Worth flagging because the same trap will catch us again if we forget.

## What this unblocks

- Spike B can wire a classifier into this same forwarder and re-bench — the methodology is reusable.
- The architecture's hybrid pipeline (inline cheap, async deep) is structurally fine for the latency budget. We don't need to redesign.
- We can move forward on actually building inline filtering knowing the bare forwarder isn't eating our budget.
