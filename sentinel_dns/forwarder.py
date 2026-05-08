"""Forwarding DNS resolver.

Accepts UDP DNS queries, runs them through the inline tier (decision cache
→ static blocklist → classifier), and either forwards to upstream or — when
`--enforce` is set — returns NXDOMAIN for queries flagged as malicious.

The classifier, cache, and blocklist are independently optional. Without
`--model-path` there's no classifier; without `--blocklist-url` there's no
blocklist; without `--enforce` nothing actually blocks (measurement mode).
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

from sentinel_dns.blocklist import StaticBlocklist, URLHAUS_URL
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
    blocklist_url: str | None = None  # None disables the static blocklist
    blocklist_refresh_s: int = 3600


class ForwardingProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        config: Config,
        classifier: LexicalClassifier | None,
        cache: DecisionCache | None,
        blocklist: StaticBlocklist | None,
    ) -> None:
        self.config = config
        self.classifier = classifier
        self.cache = cache
        self.blocklist = blocklist
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
        if self._has_inline_tier() and request.question:
            qname = str(request.question[0].name).rstrip(".")
            decision = self._score_inline(qname)

        assert self.transport is not None

        if self.config.enforce and decision is not None and decision.would_block:
            wire = self._make_nxdomain(request)
            self.transport.sendto(wire, addr)
            return

        wire = await self._forward(request)
        self.transport.sendto(wire, addr)

    def _has_inline_tier(self) -> bool:
        return self.classifier is not None or self.blocklist is not None

    def _score_inline(self, qname: str) -> Decision:
        """Cache → blocklist → classifier. Returns the Decision so the
        caller can decide whether to enforce."""
        if self.cache is not None:
            cached = self.cache.get(qname)
            if cached is not None:
                self._log_decision(qname, cached, cache_state="hit", inline_us=None)
                return cached

        # Layer 1: static blocklist (O(1))
        if self.blocklist is not None and qname in self.blocklist:
            decision = Decision(
                ml_score=0.0,
                heuristic_score=0.0,
                would_block=True,
                block_source="blocklist",
            )
            if self.cache is not None:
                self.cache.put(qname, decision)
            self._log_decision(qname, decision, cache_state="miss", inline_us=None)
            return decision

        # Layer 2/3: heuristic + ML classifier
        if self.classifier is not None:
            t0 = time.perf_counter_ns()
            ml = self.classifier.score(qname)
            h = heuristic_score(qname)
            elapsed_us = (time.perf_counter_ns() - t0) / 1_000.0
            would_block = ml >= self.config.block_threshold
            decision = Decision(
                ml_score=ml,
                heuristic_score=h,
                would_block=would_block,
                block_source="classifier" if would_block else None,
            )
            if self.cache is not None:
                self.cache.put(qname, decision)
            self._log_decision(qname, decision, cache_state="miss", inline_us=elapsed_us)
            return decision

        # No classifier and no blocklist hit — nothing to block on
        decision = Decision(ml_score=0.0, heuristic_score=0.0, would_block=False)
        if self.cache is not None:
            self.cache.put(qname, decision)
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
        source = f" source={decision.block_source}" if decision.block_source else ""
        log.info(
            "%s qname=%s ml=%.4f heur=%.3f would_block=%s cache=%s%s%s",
            prefix,
            qname,
            decision.ml_score,
            decision.heuristic_score,
            decision.would_block,
            cache_state,
            source,
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
    blocklist: StaticBlocklist | None,
) -> None:
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: ForwardingProtocol(config, classifier, cache, blocklist),
        local_addr=(config.listen_host, config.listen_port),
    )
    log.info(
        "listening on %s:%d, upstream %s:%d, classifier=%s, cache=%s, blocklist=%s, enforce=%s",
        config.listen_host,
        config.listen_port,
        config.upstream_host,
        config.upstream_port,
        "on" if classifier is not None else "off",
        f"capacity={config.cache_capacity}" if cache is not None else "off",
        f"size={blocklist.size}" if blocklist is not None else "off",
        "on" if config.enforce else "off",
    )

    refresh_task: asyncio.Task | None = None
    if blocklist is not None:
        refresh_task = asyncio.create_task(blocklist.run_refresh_loop())

    try:
        await asyncio.Event().wait()
    finally:
        if refresh_task is not None:
            refresh_task.cancel()
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
        help="Block queries flagged as malicious (return NXDOMAIN). "
        "Off by default — measurement mode logs decisions but forwards everything.",
    )
    parser.add_argument(
        "--cache-capacity",
        type=int,
        default=100_000,
        help="Decision cache capacity (0 disables).",
    )
    parser.add_argument(
        "--blocklist-url",
        default=None,
        help=f"Static blocklist source (default off; set to {URLHAUS_URL!r} to enable URLhaus).",
    )
    parser.add_argument(
        "--blocklist-refresh-s",
        type=int,
        default=3600,
        help="Blocklist refresh interval in seconds (default 3600).",
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

    if args.enforce and args.model_path is None and args.blocklist_url is None:
        parser.error(
            "--enforce requires --model-path and/or --blocklist-url "
            "(no inline tier means nothing to enforce against)"
        )

    classifier: LexicalClassifier | None = None
    if args.model_path is not None:
        log.info("loading classifier from %s", args.model_path)
        t0 = time.perf_counter()
        classifier = LexicalClassifier.load(args.model_path)
        # warm — first call has import + JIT-ish overhead inside sklearn.
        _ = classifier.score("warmup.example.com")
        log.info("classifier ready in %.2fs", time.perf_counter() - t0)

    blocklist: StaticBlocklist | None = None
    if args.blocklist_url is not None:
        blocklist = StaticBlocklist(
            source_url=args.blocklist_url,
            refresh_interval_s=args.blocklist_refresh_s,
        )
        # Synchronous initial load so we don't start serving with an empty list.
        try:
            blocklist.refresh_sync()
        except Exception as e:
            log.error("blocklist initial load failed: %s", e)
            log.error("not starting; pass --blocklist-url '' to disable, or fix network access")
            raise SystemExit(1)

    cache: DecisionCache | None = None
    if (classifier is not None or blocklist is not None) and args.cache_capacity > 0:
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
        blocklist_url=args.blocklist_url,
        blocklist_refresh_s=args.blocklist_refresh_s,
    )
    asyncio.run(serve(config, classifier, cache, blocklist))


if __name__ == "__main__":
    main()
