"""`sentinel-dns explain <domain>` — show why a domain was allowed or blocked.

Reads the SQLite query log read-only (same `mode=ro` URI form as
`tail`) and surfaces the most recent decision for the named domain
along with its plain-English explanation.

Default output is terse — domain, decision, source, human reason.
`--verbose` adds the structured fields (raw scores, cache state,
inline timing). `-n N` shows the last N decisions instead of just one,
useful when diagnosing flapping classifications.

This subcommand only reads from the log. It does not load the
classifier or blocklist to score a hypothetical unseen domain — that
would be heavy for an interactive command. If a domain has never been
queried, you'll get a "no records" message.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from sentinel_dns.cache import Decision
from sentinel_dns.explanation import explain

DEFAULT_LOG_PATH = Path("sentinel.db")


def _open_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        sys.stderr.write(f"log file not found: {path}\n")
        sys.exit(1)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _format_record(row: sqlite3.Row, verbose: bool) -> str:
    ts = row["timestamp_ns"] / 1e9
    dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    decision = row["decision"]
    qname = row["qname"]
    client = row["client_addr"] or "-"

    if decision == "block":
        label = "BLOCKED"
        source = f" (source: {row['block_source']})" if row["block_source"] else ""
    else:
        label = "allowed"
        source = ""

    lines: list[str] = []
    lines.append(f"{qname} — {label} at {dt} from {client}{source}")

    decision_obj = Decision(
        ml_score=row["ml_score"] or 0.0,
        heuristic_score=row["heuristic_score"] or 0.0,
        would_block=(decision == "block"),
        block_source=row["block_source"],
    )
    expl = explain(qname, decision_obj)

    if expl.reasons:
        lines.append("")
        for r in expl.reasons:
            lines.append(f"  • {r.signal:<22} {r.human}")

    if verbose:
        lines.append("")
        lines.append("  ML score:        {:.4f}".format(row["ml_score"] or 0.0))
        lines.append("  Heuristic score: {:.3f}".format(row["heuristic_score"] or 0.0))
        lines.append(f"  Cache state:     {row['cache_state']}")
        if row["inline_us"] is not None:
            lines.append(f"  Inline scoring:  {row['inline_us']:.1f}µs")
        lines.append(
            f"  Stored signals:  {row['signals'] or '(none)'}"
        )

    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(
        prog="sentinel-dns explain",
        description="Show the most recent decision for a domain "
        "along with its plain-English explanation.",
    )
    p.add_argument("domain", help="The fully-qualified domain name to look up.")
    p.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help=f"Path to the SQLite log file (default {DEFAULT_LOG_PATH}).",
    )
    p.add_argument(
        "-n", "--count",
        type=int,
        default=1,
        help="Show the last N records for this domain (default 1).",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Include raw scores, cache state, and inline timing.",
    )
    args = p.parse_args()

    qname = args.domain.lower().rstrip(".")
    conn = _open_readonly(args.log_path)

    rows = conn.execute(
        "SELECT * FROM queries WHERE qname = ? ORDER BY id DESC LIMIT ?",
        (qname, args.count),
    ).fetchall()

    if not rows:
        sys.stderr.write(f"{qname} — no records in log (never queried, or already purged)\n")
        sys.exit(2)

    # Walk in chronological order so multi-record output reads naturally.
    for i, row in enumerate(reversed(rows)):
        if i > 0:
            print()
        print(_format_record(row, args.verbose))


if __name__ == "__main__":
    main()
