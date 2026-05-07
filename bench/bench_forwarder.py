"""Bench: added latency of the spike forwarder vs. direct upstream.

Sends the same set of A queries to (a) the upstream resolver directly and
(b) the spike forwarder pointed at the same upstream. Reports p50 / p95 /
p99 / mean for each, plus the delta — that delta is the headroom we have
for filtering, classification, and explanation work in v0.1.

Two methodological details:

- Both paths are primed before measurement so neither pays cold-cache cost
  the other doesn't.
- Direct and via-forwarder samples are interleaved, so transient network
  jitter (which we observed in early runs causing 200ms+ outliers on one
  path but not the other) affects both roughly equally.

Run the forwarder first:

    python -m sentinel_dns.forwarder

Then run this script:

    python bench/bench_forwarder.py
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


def report(label: str, samples: list[float]) -> None:
    s = sorted(samples)
    p50 = s[len(s) // 2]
    p95 = s[int(len(s) * 0.95)]
    p99 = s[int(len(s) * 0.99)]
    print(
        f"\n=== {label} ===\n  n={len(s):4d}  "
        f"p50={p50:6.2f}ms  p95={p95:6.2f}ms  p99={p99:6.2f}ms  "
        f"mean={statistics.mean(samples):6.2f}ms  "
        f"min={min(samples):.2f}ms  max={max(samples):.2f}ms"
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream-host", default="1.1.1.1")
    parser.add_argument("--upstream-port", type=int, default=53)
    parser.add_argument("--forwarder-host", default="127.0.0.1")
    parser.add_argument("--forwarder-port", type=int, default=5354)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args()

    print("priming both paths...")
    for d in DOMAINS:
        await measure(args.upstream_host, args.upstream_port, d, args.timeout)
        await measure(args.forwarder_host, args.forwarder_port, d, args.timeout)

    direct: list[float] = []
    via: list[float] = []
    for _ in range(args.iterations):
        for d in DOMAINS:
            t1 = await measure(args.upstream_host, args.upstream_port, d, args.timeout)
            t2 = await measure(args.forwarder_host, args.forwarder_port, d, args.timeout)
            if t1 is not None:
                direct.append(t1)
            if t2 is not None:
                via.append(t2)

    if not direct or not via:
        print("\ninsufficient samples")
        return

    report(f"direct upstream — {args.upstream_host}:{args.upstream_port}", direct)
    report(f"via spike forwarder — {args.forwarder_host}:{args.forwarder_port}", via)

    direct_sorted = sorted(direct)
    via_sorted = sorted(via)
    d50 = direct_sorted[len(direct_sorted) // 2]
    v50 = via_sorted[len(via_sorted) // 2]
    d95 = direct_sorted[int(len(direct_sorted) * 0.95)]
    v95 = via_sorted[int(len(via_sorted) * 0.95)]
    d99 = direct_sorted[int(len(direct_sorted) * 0.99)]
    v99 = via_sorted[int(len(via_sorted) * 0.99)]

    print("\n=== overhead (forwarder − direct) ===")
    print(f"  p50:  {v50 - d50:+.2f}ms")
    print(f"  p95:  {v95 - d95:+.2f}ms")
    print(f"  p99:  {v99 - d99:+.2f}ms")
    print(f"  mean: {statistics.mean(via) - statistics.mean(direct):+.2f}ms")


if __name__ == "__main__":
    asyncio.run(main())
