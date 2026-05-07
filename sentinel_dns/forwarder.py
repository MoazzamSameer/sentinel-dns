"""Forwarding DNS resolver.

Accepts UDP DNS queries, scores each query name with the lexical classifier
(heuristics + n-gram LR) backed by an LRU decision cache, and either
forwards to an upstream resolver or — when `--enforce` is set — returns
NXDOMAIN for queries the classifier flags as malicious.

The classifier and cache are optional. Without `--model-path` the forwarder
is a bare proxy. Without `--enforce` it scores but never blocks (useful for
measurement and for letting the cache warm up before turning enforcement on).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import dns.asyncquery
import dns.exception
import dns.message
import dns.rcode

from sentinel_dns.cache import Decision, DecisionCache
from sentinel_dns.classifier import (
    LexicalClassifier,
    heuristic_score,
)

log = logging.getLogger("sentinel_dns.forwarder")


@dataclass(frozen=True)
class Config:
    listen_host: str = "127.0.0.1"
    listen_port: int = 5354
    upstream_host: str = "1.1.1.1"
    upstream_port: int = 53
    upstream_timeout: float = 2.0
    model_path: Path | None = None
    block_threshold: float = 0.836  # 0.1% FPR operating point from spike B
    enforce: bool = False  # off by default; flip on once you trust the classifier
    score_logging: bool = True
    cache_capacity: int = 100_000  # 0 disables caching


class ForwardingProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        config: Config,
        classifier: LexicalClassifier | None,
        cache: DecisionCache | None,
    ) -> None:
        self.config = config
        self.classifier = classifier
        self.cache = cache
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        assert isinstance(transport, asyncio.DatagramTransport)
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        asyncio.create_task(self._handle(data, addr))

    async def _handle(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            request = dns.message.from_wire(data)
        except dns.exception.DNSException:
            log.warning("malformed query from %s", addr)
            return

        decision: Decision | None = None
        if self.classifier is not None and request.question:
            qname = str(request.question[0].name).rstrip(".")
            decision = self._score_inline(qname)

        assert self.transport is not None

        if self.config.enforce and decision is not None and decision.would_block:
            wire = self._make_nxdomain(request)
            self.transport.sendto(wire, addr)
            return

        wire = await self._forward(request)
        self.transport.sendto(wire, addr)

    def _score_inline(self, qname: str) -> Decision:
        """Cache-first inline scoring. Returns the Decision so the caller
        can decide whether to enforce."""
        assert self.classifier is not None

        if self.cache is not None:
            cached = self.cache.get(qname)
            if cached is not None:
                self._log_decision(qname, cached, cache_state="hit", inline_us=None)
                return cached

        t0 = time.perf_counter_ns()
        ml = self.classifier.score(qname)
        h = heuristic_score(qname)
        elapsed_us = (time.perf_counter_ns() - t0) / 1_000.0
        decision = Decision(
            ml_score=ml,
            heuristic_score=h,
            would_block=ml >= self.config.block_threshold,
        )

        if self.cache is not None:
            self.cache.put(qname, decision)

        self._log_decision(qname, decision, cache_state="miss", inline_us=elapsed_us)
        return decision

    def _log_decision(
        self,
        qname: str,
        decision: Decision,
        *,
        cache_state: str,
        inline_us: float | None,
    ) -> None:
        if not self.config.score_logging:
            return
        prefix = "BLOCK" if (self.config.enforce and decision.would_block) else "score"
        timing = f" inline_us={inline_us:.1f}" if inline_us is not None else ""
        log.info(
            "%s qname=%s ml=%.4f heur=%.3f would_block=%s cache=%s%s",
            prefix,
            qname,
            decision.ml_score,
            decision.heuristic_score,
            decision.would_block,
            cache_state,
            timing,
        )

    @staticmethod
    def _make_nxdomain(request: dns.message.Message) -> bytes:
        response = dns.message.make_response(request)
        response.set_rcode(dns.rcode.NXDOMAIN)
        return response.to_wire()

    async def _forward(self, request: dns.message.Message) -> bytes:
        try:
            response = await dns.asyncquery.udp(
                request,
                self.config.upstream_host,
                port=self.config.upstream_port,
                timeout=self.config.upstream_timeout,
            )
            return response.to_wire()
        except (dns.exception.Timeout, OSError) as e:
            log.warning("upstream error: %s", e)
            response = dns.message.make_response(request)
            response.set_rcode(dns.rcode.SERVFAIL)
            return response.to_wire()


async def serve(
    config: Config,
    classifier: LexicalClassifier | None,
    cache: DecisionCache | None,
) -> None:
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: ForwardingProtocol(config, classifier, cache),
        local_addr=(config.listen_host, config.listen_port),
    )
    log.info(
        "listening on %s:%d, upstream %s:%d, classifier=%s, cache=%s, enforce=%s",
        config.listen_host,
        config.listen_port,
        config.upstream_host,
        config.upstream_port,
        "on" if classifier is not None else "off",
        f"capacity={config.cache_capacity}" if cache is not None else "off",
        "on" if config.enforce else "off",
    )
    try:
        await asyncio.Event().wait()
    finally:
        transport.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="sentinel-dns forwarder")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=5354)
    parser.add_argument("--upstream-host", default="1.1.1.1")
    parser.add_argument("--upstream-port", type=int, default=53)
    parser.add_argument("--upstream-timeout", type=float, default=2.0)
    parser.add_argument(
        "--model-path",
        type=Path,
        default=None,
        help="Path to a trained classifier (.joblib). If omitted, no scoring.",
    )
    parser.add_argument(
        "--block-threshold",
        type=float,
        default=0.836,
        help="ML score >= this is treated as malicious.",
    )
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="Block queries whose ML score >= --block-threshold (return NXDOMAIN). "
        "Off by default — measurement mode logs decisions but forwards everything.",
    )
    parser.add_argument(
        "--cache-capacity",
        type=int,
        default=100_000,
        help="Decision cache capacity (0 disables).",
    )
    parser.add_argument(
        "--quiet-scoring",
        action="store_true",
        help="Disable per-query score logging (still scores, just doesn't log).",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.enforce and args.model_path is None:
        parser.error("--enforce requires --model-path (no classifier means nothing to enforce)")

    classifier: LexicalClassifier | None = None
    if args.model_path is not None:
        log.info("loading classifier from %s", args.model_path)
        t0 = time.perf_counter()
        classifier = LexicalClassifier.load(args.model_path)
        # warm — first call has import + JIT-ish overhead inside sklearn.
        _ = classifier.score("warmup.example.com")
        log.info("classifier ready in %.2fs", time.perf_counter() - t0)

    cache: DecisionCache | None = None
    if classifier is not None and args.cache_capacity > 0:
        cache = DecisionCache(capacity=args.cache_capacity)

    config = Config(
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        upstream_host=args.upstream_host,
        upstream_port=args.upstream_port,
        upstream_timeout=args.upstream_timeout,
        model_path=args.model_path,
        block_threshold=args.block_threshold,
        enforce=args.enforce,
        score_logging=not args.quiet_scoring,
        cache_capacity=args.cache_capacity,
    )
    asyncio.run(serve(config, classifier, cache))


if __name__ == "__main__":
    main()
