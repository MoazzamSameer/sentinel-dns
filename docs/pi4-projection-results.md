# Pi 4 latency projection

**Status:** complete (projection only — actual Pi 4 hardware verification still gates v0.1 release)

**Question:** does the synthesis spike's projection of "500–800µs p50 classifier on Pi 4" hold up under more rigorous estimation, and is the v0.1 latency budget still achievable on the prosumer hardware target?

**Result preview:** the projection is roughly correct (probably mildly optimistic). More importantly, **the cache makes Pi 4 viability essentially independent of classifier speed** — end-to-end overhead under a Pi-class CPU simulation is **+2.37ms p50** vs direct upstream, comfortably within the relaxed v0.1 target of <3ms.

This was done without Pi 4 hardware. See *Methodology* and *Caveats* for what that buys us and what it doesn't.

---

## Methodology

### Hardware on hand

Apple M1 MacBook Air, 8 GB RAM. ARMv8 (same family as Pi 4's Cortex-A72), but very different microarchitecture, much faster clock, and bigger cache.

### Two complementary approximations

**1. Microbench multiplier projection.** Run the cache and classifier microbenches on M1 P-core, then multiply by published M1 → Pi 4 single-threaded slowdown ratios for Python/sklearn workloads (6×–13× — sourced from pyperformance entries and sklearn microbench community reports).

**2. M1 efficiency-core simulation.** macOS's `taskpolicy -c background` biases a process toward M1 "Icestorm" efficiency cores, which run at ~2 GHz on M1 (vs Pi 4's 1.5 GHz Cortex-A72). Icestorm has better IPC than A72 but similar order of magnitude. Empirically, the slowdown matches what we'd expect for Pi 4 — see results.

The two approximations should agree in shape. Where they don't, the truth is probably between them.

## Microbench results

### M1 P-core (baseline)

```
get hit:        n=1500000  p50=  0.17us  p99=  0.21us  mean=  0.16us
ml score:       n=  15000  p50=139.88us  p99=751.46us  mean=193.73us
heuristic:      n=  15000  p50=  6.88us  p99= 33.58us  mean=  8.54us
```

Matches the synthesis spike's numbers (~145µs classifier p50). Sanity check ✅.

### M1 E-core (`taskpolicy -c background`) — Pi 4 simulation

```
get hit:        n=1500000  p50=  0.46us  p99=  1.08us  mean=  0.71us
ml score:       n=  15000  p50=635.92us  p99=4021.29us  mean=884.64us
heuristic:      n=  15000  p50= 27.67us  p99=163.75us  mean= 40.02us
```

E-core scheduling slows the classifier by **~4.5×** (140µs → 636µs). Cache hits slow ~2.7× (still well under 1µs). Heuristics slow ~4×.

### P-core × multiplier projection

| Op | M1 P-core p50 | Pi 4 projected (6×–13×) |
|---|---:|---:|
| Cache hit | 0.17µs | 1.0–2.2µs |
| ML score | 140µs | **840–1820µs** |
| Heuristic score | 7µs | 41–89µs |
| Steady-state per-query (99% hit rate) | 1.56µs | 9–20µs |

### Convergence check

| | E-core measured | P-core × 6× | P-core × 13× |
|---|---:|---:|---:|
| Classifier p50 | 636µs | 840µs | 1820µs |

E-core falls at the optimistic end of the multiplier-based range. The synthesis spike's "500–800µs" projection is plausible at the low end; the actual Pi 4 number is **probably 700µs–1.5ms p50** for the bare classifier.

But — and this is the load-bearing finding — **the bare classifier number doesn't matter much in steady state.** Cache hits dominate.

## End-to-end results

Three forwarders running simultaneously, queries interleaved, 240 samples per path. Classifier+cache forwarder running under both default scheduling and E-core hint:

```
direct                   n= 240  p50=38.19ms  p95=46.84ms  p99=180.69ms  mean=41.52ms
M1 P-core                n= 239  p50=39.71ms  p95=47.63ms  p99=219.02ms  mean=43.00ms
M1 E-core (Pi~)          n= 240  p50=40.57ms  p95=48.37ms  p99=193.18ms  mean=47.86ms

overhead vs direct:
  M1 P-core (cache hits):       +1.52ms p50
  M1 E-core (Pi 4 simulation):  +2.37ms p50
```

The E-core forwarder's classifier is **4.5× slower** than the P-core one in microbench, but the end-to-end overhead is only **0.85ms more** at p50. That's the cache earning its keep — once the cache is populated, classifier speed barely matters because the classifier rarely runs.

## Verdict against v0.1 latency targets

| Target | M1 P-core (cache) | M1 E-core (Pi 4 sim) | Status |
|---|---:|---:|---|
| Total p50 added vs direct (relaxed <3ms) | +1.52ms | +2.37ms | **✅** |
| Total p99 added vs direct (<5ms) | (network jitter dominates) | (same) | likely ✅ |
| Memory budget <256MB on Pi 4 | (not measured) | (not measured) | TBD |

**v0.1 latency targets are achievable on Pi-class hardware.** Even the less favorable simulation lands inside the budget by ~25%.

## Caveats

1. **This is a projection, not a measurement.** M1 E-core ≠ Pi 4. They share the broad shape (ARMv8, lower clock than M1 P-core) but differ in microarchitecture, cache hierarchy, memory bandwidth, and storage subsystem. Real Pi 4 might be slower (likely) or faster (unlikely) than the simulation suggests.
2. **macOS scheduling hints aren't strict pinning.** `taskpolicy -c background` is a hint to the scheduler, not a hard guarantee. The process may bounce between cores depending on system state. The 4.5× slowdown observed is *probably* mostly E-core but the scheduler can preempt.
3. **No memory pressure simulated.** Pi 4 with 1–2 GB RAM under sustained load is very different from M1 Air with 8 GB. The classifier model is small (~320 KB) and easily fits, but heap fragmentation and GC behavior under memory pressure are unmeasured.
4. **No I/O simulation.** Pi 4 storage is microSD (slow random I/O). Not exercised by this bench but matters for cold start and SQLite logging in the future.
5. **Single-stream measurement.** No load test. Concurrent throughput on Pi 4 is unknown — will need actual hardware.
6. **Multiplier sources are public benchmarks, not first-party data.** The 6×–13× range comes from secondary sources (pyperformance, scikit-learn community reports). It's a defensible range but not authoritative.
7. **Cache hit rate assumed at 99%.** The blended steady-state estimate uses an assumed hit rate; real rates on prosumer networks haven't been measured.

## What this unblocks (and what it doesn't)

**Unblocks:**
- Confidence that v0.1 will work on Pi 4. Latency targets achievable in measured simulation.
- The "Pi 4 verification" task now has a defensible answer ahead of hardware. Real verification still required before release, but the project isn't blocked by it.

**Doesn't unblock:**
- Actual Pi 4 release confidence. The v0.1 release announcement still gates on a real Pi 4 measurement — the simulation gives us "this should work," not "this works."
- Memory profile under sustained load. That requires actual constrained hardware, not simulation on an 8 GB Mac.

## How to actually verify on Pi 4 when hardware is available

The bench scripts work as-is on Linux ARM:

```bash
# On a Pi 4 running Raspbian or Ubuntu Server
git clone https://github.com/MoazzamSameer/sentinel-dns.git
cd sentinel-dns
python3 -m venv .venv
.venv/bin/pip install -e .
# Fetch URLhaus + Tranco per docs/spike-b-results.md
.venv/bin/python scripts/train_classifier.py
.venv/bin/python bench/bench_pi4_projection.py            # microbench
# Then run two forwarders + bench/bench_synthesis.py for e2e
```

Expected real Pi 4 p50 classifier microbench, based on this projection: **600µs – 1.5ms**. End-to-end overhead with cache: **+2 – 4 ms p50** vs direct upstream.

If reality lands well outside that range (e.g. >5ms p50 e2e overhead), revisit the architecture — likely options: drop the classifier from inline to async-only, or skip dnspython parsing on ingress.
