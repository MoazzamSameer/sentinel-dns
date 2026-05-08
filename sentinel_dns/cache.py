"""Decision cache — qname → cached classification decision.

The synthesis spike showed the classifier costs ~150µs per query, which
on a cache miss is fine but on every query is wasteful. This cache
amortizes that cost across repeat queries so the steady-state per-query
classifier cost approaches zero.

Architecture commitment (docs/ARCHITECTURE.md):
- Keyed on qname only (not qtype/qclass — the decision is about whether
  the *name* is malicious, not the record type)
- LRU eviction
- Lifetime independent of DNS TTL — typically longer
- v0.1 is in-memory only; persistence comes later

Single-threaded asyncio means no locking. If we ever go multi-threaded,
swap OrderedDict for a thread-safe structure.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    """Cached classification decision for a single qname.

    `block_source` records *why* something would block: "blocklist" for a
    static-feed hit, "classifier" for an ML-threshold trigger, None when
    not blocking. Used by the explanation generator and the structured
    log line.
    """

    ml_score: float
    heuristic_score: float
    would_block: bool
    block_source: str | None = None


class DecisionCache:
    """Bounded-capacity LRU cache from qname to Decision."""

    def __init__(self, capacity: int = 100_000) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {capacity}")
        self._capacity = capacity
        self._cache: OrderedDict[str, Decision] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, qname: str) -> Decision | None:
        decision = self._cache.get(qname)
        if decision is None:
            self._misses += 1
            return None
        self._cache.move_to_end(qname)
        self._hits += 1
        return decision

    def put(self, qname: str, decision: Decision) -> None:
        if qname in self._cache:
            self._cache.move_to_end(qname)
            self._cache[qname] = decision
            return
        if len(self._cache) >= self._capacity:
            self._cache.popitem(last=False)
        self._cache[qname] = decision

    @property
    def stats(self) -> dict[str, int | float]:
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return {
            "size": len(self._cache),
            "capacity": self._capacity,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": hit_rate,
        }

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, qname: str) -> bool:
        return qname in self._cache
