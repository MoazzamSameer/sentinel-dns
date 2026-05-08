"""`sentinel-dns tail` — stream recent queries from the SQLite query log.

Read-only attach to the SQLite file (URI mode=ro). Safe to run while
the forwarder is writing — WAL mode supports concurrent readers and
writers without blocking.

Modes:
- One-shot (default): print the last N records and exit.
- Follow (`-f`): keep printing new records as the forwarder writes them.

Filters (combinable):
- `--decision allow|block`
- `--client <ip>`
- `--qname-contains <substring>`
- `--min-ml-score <float>`
- `--block-source blocklist|classifier`
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

from sentinel_dns.cache import Decision
from sentinel_dns.explanation import explain

DEFAULT_LOG_PATH = Path("sentinel.db")
DEFAULT_COUNT = 50


def _build_where(args: argparse.Namespace) -> tuple[str, tuple]:
    """Build a WHERE clause + parameter tuple from filter flags."""
    clauses: list[str] = []
    params: list = []

    if args.decision is not None:
        clauses.append("decision = ?")
        params.append(args.decision)
    if args.client is not None:
        clauses.append("client_addr = ?")
        params.append(args.client)
    if args.qname_contains is not None:
        clauses.append("qname LIKE ?")
        params.append(f"%{args.qname_contains}%")
    if args.min_ml_score is not None:
        clauses.append("ml_score >= ?")
        params.append(args.min_ml_score)
    if args.block_source is not None:
        clauses.append("block_source = ?")
        params.append(args.block_source)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, tuple(params)


def _format_row(row: sqlite3.Row) -> str:
    ts = row["timestamp_ns"] / 1e9
    dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    decision = row["decision"]
    label = "BLOCK" if decision == "block" else "allow"
    source = f" {row['block_source']}" if row["block_source"] else ""
    cache = row["cache_state"]
    client = row["client_addr"] or "-"
    qname = row["qname"]
    return f"{dt}  {label:5}{source:11}  cache={cache:4}  {client:15}  {qname}"


def _format_explanation(row: sqlite3.Row) -> str | None:
    """For block rows, reconstruct the Decision and ask explain() for the
    human string. Returns None for allows (no explanation needed)."""
    if row["decision"] != "block":
        return None
    decision = Decision(
        ml_score=row["ml_score"] or 0.0,
        heuristic_score=row["heuristic_score"] or 0.0,
        would_block=True,
        block_source=row["block_source"],
    )
    return f"  └ {explain(row['qname'], decision).human}"


def _print_row(row: sqlite3.Row) -> None:
    print(_format_row(row))
    expl = _format_explanation(row)
    if expl is not None:
        print(expl)
    sys.stdout.flush()


def _open_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        sys.stderr.write(f"log file not found: {path}\n")
        sys.exit(1)
    # URI form with mode=ro lets us coexist with the forwarder's writes.
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def main() -> None:
    p = argparse.ArgumentParser(
        prog="sentinel-dns tail",
        description="Stream recent queries from the SQLite query log.",
    )
    p.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help=f"Path to the SQLite log file (default {DEFAULT_LOG_PATH}).",
    )
    p.add_argument(
        "-n", "--count", type=int, default=DEFAULT_COUNT,
        help=f"Number of past records to show (default {DEFAULT_COUNT}).",
    )
    p.add_argument(
        "-f", "--follow", action="store_true",
        help="After printing past records, keep streaming new ones.",
    )
    p.add_argument("--decision", choices=["allow", "block"])
    p.add_argument("--client", help="Filter to one client IP.")
    p.add_argument("--qname-contains", help="Filter by qname substring.")
    p.add_argument("--min-ml-score", type=float)
    p.add_argument(
        "--block-source",
        choices=["blocklist", "classifier"],
        help="Filter to one block source.",
    )
    p.add_argument(
        "--poll-interval",
        type=float,
        default=0.5,
        help="Seconds between polls in --follow mode (default 0.5).",
    )
    args = p.parse_args()

    conn = _open_readonly(args.log_path)
    where, where_params = _build_where(args)

    # Initial: most recent N records, in chronological order.
    initial_sql = (
        f"SELECT * FROM (SELECT * FROM queries{where} ORDER BY id DESC LIMIT ?) "
        "ORDER BY id ASC"
    )
    rows = conn.execute(initial_sql, where_params + (args.count,)).fetchall()
    last_id = 0
    for row in rows:
        _print_row(row)
        last_id = max(last_id, row["id"])

    if not args.follow:
        return

    # Follow: poll for new records past last_id.
    follow_sql = (
        f"SELECT * FROM queries{where}{' AND' if where else ' WHERE'} id > ? "
        "ORDER BY id ASC"
    )
    try:
        while True:
            time.sleep(args.poll_interval)
            new_rows = conn.execute(follow_sql, where_params + (last_id,)).fetchall()
            for row in new_rows:
                _print_row(row)
                last_id = row["id"]
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
