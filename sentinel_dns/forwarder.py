"""Forwarding DNS resolver.

Accepts UDP DNS queries, runs them through the inline tier (decision cache
→ static blocklist → classifier), records the decision (stdout + optional
SQLite query log), and either forwards to upstream or — when `--enforce`
is set — returns NXDOMAIN for queries flagged as malicious.

Classifier, blocklist, decision cache, and SQLite log are all independently
optional. Without any of them this is a bare forwarding proxy.
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
import dns.query
import dns.rcode
import httpx

from sentinel_dns.blocklist import StaticBlocklist, URLHAUS_URL
from sentinel_dns.cache import Decision, DecisionCache
from sentinel_dns.classifier import (
    LexicalClassifier,
    heuristic_score,
)
from sentinel_dns.config import Config, load_toml, merge
from sentinel_dns.explanation import explain
from sentinel_dns.query_log import QueryLog, QueryRecord

log = logging.getLogger("sentinel_dns.forwarder")


@dataclass(frozen=True)
class _ScoringResult:
    """Inline tier output, with the breadcrumbs the caller needs to log."""

    decision: Decision
    cache_state: str  # "hit" or "miss"
    inline_us: float | None  # None on cache hit


class ForwardingProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        config: Config,
        classifier: LexicalClassifier | None,
        cache: DecisionCache | None,
        blocklist: StaticBlocklist | None,
        query_log: QueryLog | None,
        doh_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.classifier = classifier
        self.cache = cache
        self.blocklist = blocklist
        self.query_log = query_log
        self.doh_client = doh_client
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

        result: _ScoringResult | None = None
        if self._has_inline_tier() and request.question:
            qname = str(request.question[0].name).rstrip(".")
            result = self._score_inline(qname)
            self._record(qname, addr[0], result)

        assert self.transport is not None

        if self.config.enforce and result is not None and result.decision.would_block:
            wire = self._make_nxdomain(request)
            self.transport.sendto(wire, addr)
            return

        wire = await self._forward(request)
        self.transport.sendto(wire, addr)

    def _has_inline_tier(self) -> bool:
        return self.classifier is not None or self.blocklist is not None

    def _score_inline(self, qname: str) -> _ScoringResult:
        """Cache → blocklist → classifier."""
        if self.cache is not None:
            cached = self.cache.get(qname)
            if cached is not None:
                return _ScoringResult(decision=cached, cache_state="hit", inline_us=None)

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
            return _ScoringResult(decision=decision, cache_state="miss", inline_us=None)

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
            return _ScoringResult(decision=decision, cache_state="miss", inline_us=elapsed_us)

        # No classifier and no blocklist hit — nothing flagged.
        decision = Decision(ml_score=0.0, heuristic_score=0.0, would_block=False)
        if self.cache is not None:
            self.cache.put(qname, decision)
        return _ScoringResult(decision=decision, cache_state="miss", inline_us=None)

    def _record(self, qname: str, client_addr: str, result: _ScoringResult) -> None:
        """Stdout log (visibility) + SQLite log (history). Each gated on its
        own config — stdout obeys the existing 'only when interesting' rule,
        SQLite logs every query when enabled."""
        decision = result.decision
        is_block_action = self.config.enforce and decision.would_block
        signals_codes: list[str] = []

        if is_block_action:
            explanation = explain(qname, decision)
            signals_codes = explanation.signal_codes
            if self.config.score_logging:
                self._log_block(qname, decision, result, explanation, signals_codes)
        elif self.config.score_logging and self._stdout_worth_logging(decision, result):
            self._log_score(qname, decision, result)

        if self.query_log is not None:
            self.query_log.log_nowait(
                QueryRecord(
                    timestamp_ns=time.time_ns(),
                    qname=qname,
                    client_addr=client_addr,
                    decision="block" if is_block_action else "allow",
                    block_source=decision.block_source,
                    ml_score=decision.ml_score,
                    heuristic_score=decision.heuristic_score,
                    cache_state=result.cache_state,
                    inline_us=result.inline_us,
                    signals=",".join(signals_codes),
                )
            )

    @staticmethod
    def _stdout_worth_logging(decision: Decision, result: _ScoringResult) -> bool:
        """Avoid the blocklist-only-mode 'silent allow' spam: skip stdout
        when no classifier ran, no blocklist hit, no cache hit."""
        if result.cache_state == "hit":
            return True
        if decision.block_source is not None:
            return True
        if result.inline_us is not None:  # classifier actually ran
            return True
        return False

    def _log_score(self, qname: str, decision: Decision, result: _ScoringResult) -> None:
        timing = f" inline_us={result.inline_us:.1f}" if result.inline_us is not None else ""
        source = f" source={decision.block_source}" if decision.block_source else ""
        log.info(
            "score qname=%s ml=%.4f heur=%.3f would_block=%s cache=%s%s%s",
            qname,
            decision.ml_score,
            decision.heuristic_score,
            decision.would_block,
            result.cache_state,
            source,
            timing,
        )

    def _log_block(
        self,
        qname: str,
        decision: Decision,
        result: _ScoringResult,
        explanation,
        signals_codes: list[str],
    ) -> None:
        timing = f" inline_us={result.inline_us:.1f}" if result.inline_us is not None else ""
        source = f" source={decision.block_source}" if decision.block_source else ""
        log.info(
            "BLOCK qname=%s ml=%.4f heur=%.3f would_block=%s cache=%s%s%s signals=%s",
            qname,
            decision.ml_score,
            decision.heuristic_score,
            decision.would_block,
            result.cache_state,
            source,
            timing,
            ",".join(signals_codes),
        )
        log.info("explain qname=%s — %s", qname, explanation.human)

    @staticmethod
    def _make_nxdomain(request: dns.message.Message) -> bytes:
        response = dns.message.make_response(request)
        response.set_rcode(dns.rcode.NXDOMAIN)
        return response.to_wire()

    async def _forward(self, request: dns.message.Message) -> bytes:
        try:
            if self.config.upstream_doh_url is not None:
                # Pin to HTTP/2 (dnspython's default tries HTTP/3 first which
                # needs aioquic). Pass a shared httpx client so connections
                # stay warm — without it, dnspython opens a fresh TLS session
                # per query and adds ~100ms p50.
                response = await dns.asyncquery.https(
                    request,
                    self.config.upstream_doh_url,
                    timeout=self.config.upstream_timeout,
                    http_version=dns.query.HTTPVersion.HTTP_2,
                    client=self.doh_client,
                )
            else:
                response = await dns.asyncquery.udp(
                    request,
                    self.config.upstream_host,
                    port=self.config.upstream_port,
                    timeout=self.config.upstream_timeout,
                )
            return response.to_wire()
        except Exception as e:
            # Catch broadly: dns.exception.Timeout, OSError, httpx.HTTPError,
            # httpx.ConnectError, etc. The per-query task must never crash —
            # any failure means SERVFAIL to the client and a log line for us.
            log.warning("upstream error (%s): %s", type(e).__name__, e)
            response = dns.message.make_response(request)
            response.set_rcode(dns.rcode.SERVFAIL)
            return response.to_wire()


async def serve(
    config: Config,
    classifier: LexicalClassifier | None,
    cache: DecisionCache | None,
    blocklist: StaticBlocklist | None,
    query_log: QueryLog | None,
) -> None:
    if query_log is not None:
        await query_log.start()

    doh_client: httpx.AsyncClient | None = None
    if config.upstream_doh_url is not None:
        # Connection-pooling client lives for the whole forwarder lifetime.
        # Each DoH query reuses the same TLS session.
        doh_client = httpx.AsyncClient(
            http2=True,
            timeout=config.upstream_timeout,
        )

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: ForwardingProtocol(
            config, classifier, cache, blocklist, query_log, doh_client
        ),
        local_addr=(config.listen_host, config.listen_port),
    )
    upstream_label = (
        f"DoH {config.upstream_doh_url}"
        if config.upstream_doh_url is not None
        else f"UDP {config.upstream_host}:{config.upstream_port}"
    )
    log.info(
        "listening on %s:%d, upstream=%s, classifier=%s, cache=%s, blocklist=%s, "
        "log=%s, enforce=%s",
        config.listen_host,
        config.listen_port,
        upstream_label,
        "on" if classifier is not None else "off",
        f"capacity={config.cache_capacity}" if cache is not None else "off",
        f"size={blocklist.size}" if blocklist is not None else "off",
        config.log_path if query_log is not None else "off",
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
        if doh_client is not None:
            await doh_client.aclose()
        if query_log is not None:
            await query_log.stop()


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser. All Config-mapped flags use
    `default=argparse.SUPPRESS` so the resulting Namespace contains
    only keys the user explicitly set — that's what `merge()` needs."""
    p = argparse.ArgumentParser(description="sentinel-dns forwarder")
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to a TOML config file. CLI flags override file values.",
    )
    p.add_argument("--listen-host", default=argparse.SUPPRESS)
    p.add_argument("--listen-port", type=int, default=argparse.SUPPRESS)
    p.add_argument("--upstream-host", default=argparse.SUPPRESS)
    p.add_argument("--upstream-port", type=int, default=argparse.SUPPRESS)
    p.add_argument("--upstream-timeout", type=float, default=argparse.SUPPRESS)
    p.add_argument(
        "--upstream-doh-url", default=argparse.SUPPRESS,
        help="DoH endpoint URL (e.g. https://cloudflare-dns.com/dns-query). "
        "When set, overrides UDP upstream — DNS queries leave the device "
        "wrapped in HTTPS, so the ISP can't see which domains you query.",
    )
    p.add_argument(
        "--model-path", type=Path, default=argparse.SUPPRESS,
        help="Path to a trained classifier (.joblib). If omitted, no scoring.",
    )
    p.add_argument(
        "--block-threshold", type=float, default=argparse.SUPPRESS,
        help="ML score >= this is treated as malicious.",
    )
    p.add_argument(
        "--enforce", action="store_true", default=argparse.SUPPRESS,
        help="Block queries flagged as malicious (return NXDOMAIN). "
        "Off by default — measurement mode logs decisions but forwards everything.",
    )
    p.add_argument(
        "--cache-capacity", type=int, default=argparse.SUPPRESS,
        help="Decision cache capacity (0 disables).",
    )
    p.add_argument(
        "--blocklist-url", default=argparse.SUPPRESS,
        help=f"Static blocklist source (default off; set to {URLHAUS_URL!r} to enable URLhaus).",
    )
    p.add_argument(
        "--blocklist-refresh-s", type=int, default=argparse.SUPPRESS,
        help="Blocklist refresh interval in seconds (default 3600).",
    )
    p.add_argument(
        "--log-path", type=Path, default=argparse.SUPPRESS,
        help="Path to a SQLite file for the query log (default off).",
    )
    p.add_argument(
        "--log-retention-days", type=int, default=argparse.SUPPRESS,
        help="Days of query log history to retain (default 7).",
    )
    p.add_argument(
        "--quiet-scoring", action="store_true", default=argparse.SUPPRESS,
        help="Disable per-query stdout score logging (still scores, still writes "
        "to SQLite if a log path is set).",
    )
    # Non-Config args: not merged.
    p.add_argument("--log-level", default="INFO")
    return p


def _args_to_overrides(args: argparse.Namespace) -> dict:
    """Translate parsed argparse Namespace to Config-field overrides.
    Only keys the user actually set are present (because of SUPPRESS).
    Hyphens become underscores; --quiet-scoring inverts to score_logging."""
    raw = vars(args)
    overrides: dict = {}
    for key, value in raw.items():
        if key in {"config", "log_level"}:
            continue
        if key == "quiet_scoring":
            overrides["score_logging"] = not value
        else:
            overrides[key] = value
    return overrides


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # httpx logs every DoH POST at INFO. We log our own per-query lines;
    # don't double-log the upstream HTTP traffic.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    file_overrides: dict = {}
    if args.config is not None:
        try:
            file_overrides = load_toml(args.config)
        except (OSError, ValueError) as e:
            parser.error(f"--config: {e}")

    cli_overrides = _args_to_overrides(args)
    try:
        config = merge(cli_overrides, file_overrides)
    except ValueError as e:
        parser.error(str(e))

    if config.enforce and config.model_path is None and config.blocklist_url is None:
        parser.error(
            "enforce=true requires model_path and/or blocklist_url "
            "(no inline tier means nothing to enforce against)"
        )

    classifier: LexicalClassifier | None = None
    if config.model_path is not None:
        log.info("loading classifier from %s", config.model_path)
        t0 = time.perf_counter()
        classifier = LexicalClassifier.load(config.model_path)
        # warm — first call has import + JIT-ish overhead inside sklearn.
        _ = classifier.score("warmup.example.com")
        log.info("classifier ready in %.2fs", time.perf_counter() - t0)

    blocklist: StaticBlocklist | None = None
    if config.blocklist_url is not None and config.blocklist_url != "":
        blocklist = StaticBlocklist(
            source_url=config.blocklist_url,
            refresh_interval_s=config.blocklist_refresh_s,
        )
        try:
            blocklist.refresh_sync()
        except Exception as e:
            log.error("blocklist initial load failed: %s", e)
            log.error("not starting; remove blocklist_url or fix network access")
            raise SystemExit(1)

    cache: DecisionCache | None = None
    if (classifier is not None or blocklist is not None) and config.cache_capacity > 0:
        cache = DecisionCache(capacity=config.cache_capacity)

    query_log: QueryLog | None = None
    if config.log_path is not None:
        query_log = QueryLog(
            path=config.log_path,
            retention_days=config.log_retention_days,
        )

    asyncio.run(serve(config, classifier, cache, blocklist, query_log))


if __name__ == "__main__":
    main()
