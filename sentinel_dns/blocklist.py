"""Static blocklist — first layer of the architecture's inline tier.

Loads a hostfile-format feed (URLhaus by default) into a frozenset for
O(1) lookup, refreshes on a configurable interval, and fails open on
refresh errors so a network blip never empties the blocklist.

The architecture (docs/ARCHITECTURE.md) puts the static blocklist
*before* the ML classifier in the inline tier — domains the feed has
already flagged are blocked without spending a single classifier
microsecond on them.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import urllib.error
import urllib.request

URLHAUS_URL = "https://urlhaus.abuse.ch/downloads/hostfile/"
DEFAULT_REFRESH_INTERVAL_S = 3600  # one hour

_IP_LIKE = re.compile(r"^\d+\.\d+\.\d+\.\d+")

log = logging.getLogger("sentinel_dns.blocklist")


class StaticBlocklist:
    """Hostfile-format blocklist with periodic refresh.

    Thread-/coroutine-safety: `contains` reads `_domains` which is a
    frozenset (immutable). `_apply` swaps the reference atomically. Safe
    under asyncio's single-threaded model and mostly safe under threads
    too (assignment of a Python attribute is atomic in CPython).
    """

    def __init__(
        self,
        source_url: str = URLHAUS_URL,
        refresh_interval_s: int = DEFAULT_REFRESH_INTERVAL_S,
        request_timeout_s: float = 30.0,
    ) -> None:
        self.source_url = source_url
        self.refresh_interval_s = refresh_interval_s
        self.request_timeout_s = request_timeout_s
        self._domains: frozenset[str] = frozenset()
        self._last_refresh_ts: float = 0.0
        self._last_error: str | None = None

    def __contains__(self, qname: str) -> bool:
        return qname.lower() in self._domains

    @property
    def size(self) -> int:
        return len(self._domains)

    @property
    def stats(self) -> dict[str, object]:
        return {
            "size": self.size,
            "last_refresh_ts": self._last_refresh_ts,
            "last_error": self._last_error,
            "source_url": self.source_url,
        }

    def refresh_sync(self) -> int:
        """Fetch + parse + atomically replace. Returns count loaded.
        Raises on network or parse errors — caller decides retry."""
        log.info("fetching blocklist from %s", self.source_url)
        try:
            with urllib.request.urlopen(
                self.source_url, timeout=self.request_timeout_s
            ) as resp:
                text = resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            self._last_error = repr(e)
            raise

        new_domains = self._parse(text)
        self._apply(new_domains)
        log.info("blocklist refreshed: %d domains", len(new_domains))
        return len(new_domains)

    async def refresh_async(self) -> int:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.refresh_sync)

    async def run_refresh_loop(self) -> None:
        """Background task: refresh on the configured interval. Logs and
        carries on across errors so a network blip never empties the
        blocklist."""
        while True:
            await asyncio.sleep(self.refresh_interval_s)
            try:
                await self.refresh_async()
            except Exception as e:  # noqa: BLE001 — fail open, log, keep going
                log.warning("blocklist refresh failed, keeping %d cached: %s", self.size, e)

    def _apply(self, new_domains: frozenset[str]) -> None:
        self._domains = new_domains
        self._last_refresh_ts = time.time()
        self._last_error = None

    @staticmethod
    def _parse(text: str) -> frozenset[str]:
        """Parse hostfile format: '127.0.0.1\\thostname' lines, skipping
        comments and reverse-DNS-of-IP entries that URLhaus sometimes
        includes (those aren't real C2 domains)."""
        out: set[str] = set()
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                host = parts[1].lower()
                if not _IP_LIKE.match(host):
                    out.add(host)
        return frozenset(out)
