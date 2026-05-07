"""Bench: cache-hit latency vs classifier-score latency.

Two pieces:

1. Microbench: cache.get() / cache.put() in isolation. We expect
   sub-microsecond hits because OrderedDict + move_to_end is hash-table
   fast.

2. End-to-end: rerun the synthesis 3-way bench with cache enabled in the
   classifier forwarder. After the priming round, all queries are
   cache-hits, so the classifier-vs-no-classifier delta should collapse
   to roughly zero.

Run:

    python -m sentinel_dns.forwarder --listen-port 5354 &
    python -m sentinel_dns.forwarder --listen-port 5355 \\
        --model-path models/classifier_v0.joblib --quiet-scoring &
    python bench/bench_cache.py
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

import dns.asyncquery
import dns.exception
import dns.message

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sentinel_dns.cache import Decision, DecisionCache  # noqa: E402

DOMAINS = [
    "google.com",
    "github.com",
    "cloudflare.com",
    "wikipedia.org",
    "amazon.com",
    "stackoverflow.com",
    "ycombinator.com",
    "openai.com",
    "anthropic.com",
    "nytimes.com",
    "reddit.com",
    "apple.com",
]


def microbench_cache(iterations: int = 100_000) -> None:
    print("=== Microbench: DecisionCache ===")
    cache = DecisionCache(capacity=10_000)
    decision = Decision(ml_score=0.1, heuristic_score=0.0, would_block=False)

    # Populate first so subsequent get() calls hit
    for d in DOMAINS:
        cache.put(d, decision)

    # Hot get — every call is a hit
    hit_us: list[float] = []
    for _ in range(iterations):
        for d in DOMAINS:
            t0 = time.perf_counter_ns()
            cache.get(d)
            t1 = time.perf_counter_ns()
            hit_us.append((t1 - t0) / 1_000.0)

    # Put — alternating put on existing keys (LRU touch path)
    put_us: list[float] = []
    for _ in range(iterations):
        for d in DOMAINS:
            t0 = time.perf_counter_ns()
            cache.put(d, decision)
            t1 = time.perf_counter_ns()
            put_us.append((t1 - t0) / 1_000.0)

    # Miss — none of these are in the cache
    miss_us: list[float] = []
    for i in range(iterations):
        t0 = time.perf_counter_ns()
        cache.get(f"never-seen-{i}.example.com")
        t1 = time.perf_counter_ns()
        miss_us.append((t1 - t0) / 1_000.0)

    def report(label: str, samples: list[float]) -> None:
        s = sorted(samples)
        n = len(s)
        print(
            f"  {label:12} n={n:7d}  "
            f"p50={s[n // 2]:5.2f}us  p95={s[int(n * 0.95)]:5.2f}us  "
            f"p99={s[int(n * 0.99)]:5.2f}us  mean={statistics.mean(samples):5.2f}us"
        )

    report("get hit:", hit_us)
    report("get miss:", miss_us)
    report("put:", put_us)


async def measure(host: str, port: int, qname: str, timeout: float) -> float | None:
    msg = dns.message.make_query(qname, "A")
    start = time.perf_counter()
    try:
        await dns.asyncquery.udp(msg, host, port=port, timeout=timeout)
    except (dns.exception.DNSException, OSError):
        return None
    return (time.perf_counter() - start) * 1000


async def e2e_bench(args: argparse.Namespace) -> None:
    print("\n=== End-to-end: forwarder + classifier + cache vs forwarder only ===\n")
    paths = [
        ("direct", args.upstream_host, args.upstream_port),
        ("no-classifier", "127.0.0.1", args.no_classifier_port),
        ("classifier+cache", "127.0.0.1", args.classifier_port),
    ]

    print("priming all paths (also fills the cache for the third)...")
    for d in DOMAINS:
        for _, host, port in paths:
            await measure(host, port, d, args.timeout)

    samples: dict[str, list[float]] = {label: [] for label, _, _ in paths}
    for _ in range(args.iterations):
        for d in DOMAINS:
            for label, host, port in paths:
                t = await measure(host, port, d, args.timeout)
                if t is not None:
                    samples[label].append(t)

    if not all(samples[l] for l, _, _ in paths):
        print("\ninsufficient samples — is each forwarder running?")
        return

    stats: dict[str, dict[str, float]] = {}
    for label in samples:
        s = sorted(samples[label])
        n = len(s)
        stats[label] = {
            "p50": s[n // 2],
            "p95": s[int(n * 0.95)],
            "p99": s[int(n * 0.99)],
            "mean": statistics.mean(samples[label]),
        }
        print(
            f"=== {label} ===\n  n={n:4d}  "
            f"p50={stats[label]['p50']:6.2f}ms  p95={stats[label]['p95']:6.2f}ms  "
            f"p99={stats[label]['p99']:6.2f}ms  mean={stats[label]['mean']:6.2f}ms"
        )

    print("\n=== overhead deltas ===")
    for metric in ["p50", "p95", "p99", "mean"]:
        d = stats["direct"][metric]
        nc = stats["no-classifier"][metric]
        cl = stats["classifier+cache"][metric]
        print(
            f"  {metric:>4}: "
            f"forwarder={nc - d:+6.2f}ms (vs direct)  "
            f"cache hit={cl - nc:+6.2f}ms (vs forwarder)  "
            f"total={cl - d:+6.2f}ms (vs direct)"
        )


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--upstream-host", default="1.1.1.1")
    p.add_argument("--upstream-port", type=int, default=53)
    p.add_argument("--no-classifier-port", type=int, default=5354)
    p.add_argument("--classifier-port", type=int, default=5355)
    p.add_argument("--iterations", type=int, default=20)
    p.add_argument("--timeout", type=float, default=2.0)
    p.add_argument("--skip-e2e", action="store_true", help="microbench only")
    args = p.parse_args()

    microbench_cache()
    if not args.skip_e2e:
        await e2e_bench(args)


if __name__ == "__main__":
    asyncio.run(main())
