"""3-way interleaved bench: direct vs forwarder-no-classifier vs forwarder-with-classifier.

Same methodology as bench_forwarder.py — interleaved samples, dual-priming —
extended to a third path. Tells us how much added latency the classifier
costs on top of the bare forwarder, separately from the forwarder's own
overhead vs direct.

Run with the no-classifier forwarder on 5354 and the classifier forwarder
on 5355:

    python -m sentinel_dns.forwarder --listen-port 5354 &
    python -m sentinel_dns.forwarder --listen-port 5355 \\
        --model-path models/classifier_v0.joblib --quiet-scoring &
    python bench/bench_synthesis.py
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time

import dns.asyncquery
import dns.exception
import dns.message

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


async def measure(host: str, port: int, qname: str, timeout: float) -> float | None:
    msg = dns.message.make_query(qname, "A")
    start = time.perf_counter()
    try:
        await dns.asyncquery.udp(msg, host, port=port, timeout=timeout)
    except (dns.exception.DNSException, OSError):
        return None
    return (time.perf_counter() - start) * 1000


def report(label: str, samples: list[float]) -> dict[str, float]:
    s = sorted(samples)
    p50 = s[len(s) // 2]
    p95 = s[int(len(s) * 0.95)]
    p99 = s[int(len(s) * 0.99)]
    mean = statistics.mean(samples)
    print(
        f"\n=== {label} ===\n  n={len(s):4d}  "
        f"p50={p50:6.2f}ms  p95={p95:6.2f}ms  p99={p99:6.2f}ms  "
        f"mean={mean:6.2f}ms  min={min(samples):.2f}ms  max={max(samples):.2f}ms"
    )
    return {"p50": p50, "p95": p95, "p99": p99, "mean": mean}


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--upstream-host", default="1.1.1.1")
    p.add_argument("--upstream-port", type=int, default=53)
    p.add_argument("--no-classifier-port", type=int, default=5354)
    p.add_argument("--classifier-port", type=int, default=5355)
    p.add_argument("--iterations", type=int, default=20)
    p.add_argument("--timeout", type=float, default=2.0)
    args = p.parse_args()

    paths = [
        ("direct", args.upstream_host, args.upstream_port),
        ("no-classifier", "127.0.0.1", args.no_classifier_port),
        ("classifier", "127.0.0.1", args.classifier_port),
    ]

    print("priming all paths...")
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

    stats = {label: report(label, samples[label]) for label, _, _ in paths}

    print("\n=== overhead deltas ===")
    for metric in ["p50", "p95", "p99", "mean"]:
        d = stats["direct"][metric]
        nc = stats["no-classifier"][metric]
        cl = stats["classifier"][metric]
        print(
            f"  {metric:>4}: "
            f"forwarder={nc - d:+6.2f}ms (vs direct)  "
            f"classifier={cl - nc:+6.2f}ms (vs forwarder)  "
            f"total={cl - d:+6.2f}ms (vs direct)"
        )


if __name__ == "__main__":
    asyncio.run(main())
