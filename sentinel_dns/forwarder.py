"""Forwarding DNS resolver.

Accepts UDP DNS queries, optionally scores each query name with the lexical
classifier (heuristics + n-gram LR) backed by an LRU decision cache, and
forwards to an upstream resolver. Scoring is measurement-only — we log the
decision but do not yet block.
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

        if self.classifier is not None and request.question:
            self._score_inline(str(request.question[0].name).rstrip("."))

        wire = await self._forward(request)
        assert self.transport is not None
        self.transport.sendto(wire, addr)

    def _score_inline(self, qname: str) -> None:
        """Cache-first inline scoring. Measurement-only — we log the
        decision but do not yet block the response."""
        assert self.classifier is not None

        if self.cache is not None:
            cached = self.cache.get(qname)
            if cached is not None:
                if self.config.score_logging:
                    log.info(
                        "score qname=%s ml=%.4f heur=%.3f would_block=%s cache=hit",
                        qname,
                        cached.ml_score,
                        cached.heuristic_score,
                        cached.would_block,
                    )
                return

        t0 = time.perf_counter_ns()
        ml = self.classifier.score(qname)
        h = heuristic_score(qname)
        elapsed_us = (time.perf_counter_ns() - t0) / 1_000.0
        would_block = ml >= self.config.block_threshold
        decision = Decision(ml_score=ml, heuristic_score=h, would_block=would_block)

        if self.cache is not None:
            self.cache.put(qname, decision)

        if self.config.score_logging:
            log.info(
                "score qname=%s ml=%.4f heur=%.3f would_block=%s cache=miss inline_us=%.1f",
                qname,
                ml,
                h,
                would_block,
                elapsed_us,
            )

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
        "listening on %s:%d, upstream %s:%d, classifier=%s, cache=%s",
        config.listen_host,
        config.listen_port,
        config.upstream_host,
        config.upstream_port,
        "on" if classifier is not None else "off",
        f"capacity={config.cache_capacity}" if cache is not None else "off",
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
        help="ML score >= this would block (measurement only in this spike).",
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
        score_logging=not args.quiet_scoring,
        cache_capacity=args.cache_capacity,
    )
    asyncio.run(serve(config, classifier, cache))


if __name__ == "__main__":
    main()
