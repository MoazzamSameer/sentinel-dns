"""Pi 4 projection bench — runs classifier + cache microbenches under
constrained CPU conditions and projects expected Pi 4 numbers.

This is a *projection*, not a measurement. Running on an Apple M1 Air,
even with `taskpolicy -c background` to bias toward efficiency cores,
is not the same as running on a Pi 4. The architectures are both
ARMv8 but with very different microarchitectures, cache hierarchies,
memory bandwidth, and clock characteristics.

The goal: produce a defensible range estimate for Pi 4 performance
that beats the synthesis spike's hand-waved "3-5x slowdown" projection,
without claiming false precision. Real verification waits on hardware.

Run twice for the two-config picture:

    # P-core (default)
    python bench/bench_pi4_projection.py

    # E-core hint (closer to Pi 4 clock characteristics)
    taskpolicy -c background python bench/bench_pi4_projection.py
"""

from __future__ import annotations

import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sentinel_dns.cache import Decision, DecisionCache  # noqa: E402
from sentinel_dns.classifier import (  # noqa: E402
    LexicalClassifier,
    heuristic_score,
)

MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "classifier_v0.joblib"

# Published M1 → Pi 4 single-threaded slowdown range for Python /
# numpy / sklearn workloads. Conservative bookend: Pi 4 is 6–13x
# slower than M1 P-core on the kind of work the classifier does.
# Sources: pyperformance Pi 4 entries, sklearn microbench reports.
PI4_MULTIPLIER_LOW = 6.0
PI4_MULTIPLIER_HIGH = 13.0


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
    "1ce6-route.fixionmunici9al.lat",
    "malwarebytes.com",
    "a-very-long-suspicious-looking-subdomain.weird-tld.xyz",
]


def detect_environment() -> str:
    """Try to identify whether we're hinted to E-core."""
    try:
        result = subprocess.run(
            ["sysctl", "-n", "kern.osproductversion"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        os_ver = result.stdout.strip() if result.returncode == 0 else "unknown"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        os_ver = "non-mac"
    qos_hint = "background" if "QOS_CLASS_BACKGROUND" in str(sys.argv) else "default"
    return f"macos {os_ver}, sched-hint={qos_hint}"


def run_cache_bench(iterations: int = 100_000) -> dict[str, list[float]]:
    cache = DecisionCache(capacity=10_000)
    decision = Decision(ml_score=0.1, heuristic_score=0.0, would_block=False)
    for d in DOMAINS:
        cache.put(d, decision)

    hit_us: list[float] = []
    for _ in range(iterations):
        for d in DOMAINS:
            t0 = time.perf_counter_ns()
            cache.get(d)
            t1 = time.perf_counter_ns()
            hit_us.append((t1 - t0) / 1_000.0)

    return {"cache_hit_us": hit_us}


def run_classifier_bench(iterations: int = 1_000) -> dict[str, list[float]]:
    clf = LexicalClassifier.load(MODEL_PATH)
    # warm
    for d in DOMAINS[:3]:
        clf.score(d)

    ml_us: list[float] = []
    h_us: list[float] = []
    for _ in range(iterations):
        for d in DOMAINS:
            t0 = time.perf_counter_ns()
            clf.score(d)
            t1 = time.perf_counter_ns()
            heuristic_score(d)
            t2 = time.perf_counter_ns()
            ml_us.append((t1 - t0) / 1_000.0)
            h_us.append((t2 - t1) / 1_000.0)

    return {"ml_score_us": ml_us, "heuristic_score_us": h_us}


def percentile(samples: list[float], p: float) -> float:
    s = sorted(samples)
    return s[int(len(s) * p)]


def report(label: str, samples: list[float], project_pi4: bool = True) -> None:
    s = sorted(samples)
    n = len(s)
    p50 = s[n // 2]
    p99 = s[int(n * 0.99)]
    mean = statistics.mean(samples)
    line = (
        f"  {label:24} n={n:>7d}  "
        f"p50={p50:8.2f}us  p99={p99:8.2f}us  mean={mean:8.2f}us"
    )
    print(line)
    if project_pi4:
        lo50 = p50 * PI4_MULTIPLIER_LOW
        hi50 = p50 * PI4_MULTIPLIER_HIGH
        lo99 = p99 * PI4_MULTIPLIER_LOW
        hi99 = p99 * PI4_MULTIPLIER_HIGH
        print(
            f"  {'  → projected Pi 4:':24}            "
            f"p50={lo50:7.1f}–{hi50:.1f}us  p99={lo99:7.1f}–{hi99:.1f}us"
        )


def main() -> None:
    print(f"environment: {detect_environment()}")
    print(f"projection multiplier: {PI4_MULTIPLIER_LOW}x–{PI4_MULTIPLIER_HIGH}x")
    print()
    print("=== Cache microbench ===")
    cache_results = run_cache_bench()
    report("get hit:", cache_results["cache_hit_us"])

    print()
    print("=== Classifier microbench ===")
    clf_results = run_classifier_bench()
    report("ml score:", clf_results["ml_score_us"])
    report("heuristic score:", clf_results["heuristic_score_us"])

    print()
    print("=== Steady-state per-query overhead (mostly cache hits) ===")
    cache_p50 = percentile(cache_results["cache_hit_us"], 0.50)
    clf_p50 = percentile(clf_results["ml_score_us"], 0.50)
    # Assume 99% cache hits in steady state
    blended_p50 = 0.99 * cache_p50 + 0.01 * clf_p50
    print(f"  blended p50 (99% hit rate): {blended_p50:.2f}us")
    print(
        f"  → projected Pi 4: "
        f"{blended_p50 * PI4_MULTIPLIER_LOW:.1f}–{blended_p50 * PI4_MULTIPLIER_HIGH:.1f}us"
    )


if __name__ == "__main__":
    main()
