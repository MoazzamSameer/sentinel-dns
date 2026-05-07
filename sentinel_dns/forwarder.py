"""Minimal forwarding DNS resolver — Spike A.

Accepts UDP DNS queries on a configurable local address and forwards them to
an upstream resolver. No filtering, no caching, no AI. Just the bare wire to
measure how much overhead the Python + asyncio path adds before we start
adding real logic on top.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass

import dns.asyncquery
import dns.exception
import dns.message
import dns.rcode

log = logging.getLogger("sentinel_dns.forwarder")


@dataclass(frozen=True)
class Config:
    listen_host: str = "127.0.0.1"
    listen_port: int = 5354
    upstream_host: str = "1.1.1.1"
    upstream_port: int = 53
    upstream_timeout: float = 2.0


class ForwardingProtocol(asyncio.DatagramProtocol):
    def __init__(self, config: Config) -> None:
        self.config = config
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

        wire = await self._forward(request)
        assert self.transport is not None
        self.transport.sendto(wire, addr)

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


async def serve(config: Config) -> None:
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: ForwardingProtocol(config),
        local_addr=(config.listen_host, config.listen_port),
    )
    log.info(
        "listening on %s:%d, upstream %s:%d",
        config.listen_host,
        config.listen_port,
        config.upstream_host,
        config.upstream_port,
    )
    try:
        await asyncio.Event().wait()
    finally:
        transport.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="sentinel-dns spike forwarder")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=5354)
    parser.add_argument("--upstream-host", default="1.1.1.1")
    parser.add_argument("--upstream-port", type=int, default=53)
    parser.add_argument("--upstream-timeout", type=float, default=2.0)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = Config(
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        upstream_host=args.upstream_host,
        upstream_port=args.upstream_port,
        upstream_timeout=args.upstream_timeout,
    )
    asyncio.run(serve(config))


if __name__ == "__main__":
    main()
