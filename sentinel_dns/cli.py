"""Top-level CLI dispatcher.

`sentinel-dns` is the single console script. Its first positional arg
selects what to do; missing or flag-only arg falls through to the
forwarder for backward compatibility with the original entry point.

  sentinel-dns                       → forwarder.main()  (default)
  sentinel-dns --listen-port 5354    → forwarder.main()  (still works)
  sentinel-dns --config sentinel.toml→ forwarder.main()  (still works)
  sentinel-dns tail [...]            → tail_cmd.main()
  sentinel-dns --help                → top-level help

Per-subcommand help: `sentinel-dns tail --help`.
"""

from __future__ import annotations

import sys

USAGE = """\
sentinel-dns — AI-assisted DNS resolver

Usage:
  sentinel-dns [<flags>...]                Run the forwarder (default).
  sentinel-dns tail [<flags>...]           Stream recent queries from the SQLite log.
  sentinel-dns explain <domain> [<flags>]  Show why a domain was allowed or blocked.
  sentinel-dns --help                      Show this message.

Per-subcommand help: e.g. `sentinel-dns tail --help`
"""

_SUBCOMMANDS = {"tail", "explain"}


def main() -> None:
    argv = sys.argv[1:]

    # `sentinel-dns --help` (with no subcommand): top-level help.
    # `sentinel-dns tail --help`: tail's own help.
    if argv and argv[0] in ("-h", "--help"):
        print(USAGE)
        return

    # `sentinel-dns` or `sentinel-dns --flag ...` → forwarder
    if not argv or argv[0].startswith("-"):
        from sentinel_dns import forwarder

        forwarder.main()
        return

    cmd = argv[0]
    if cmd not in _SUBCOMMANDS:
        sys.stderr.write(f"unknown command: {cmd!r}\n\n{USAGE}")
        sys.exit(2)

    # Drop the subcommand so subcommand parsers see a clean argv.
    sys.argv = [f"{sys.argv[0]} {cmd}"] + argv[1:]

    if cmd == "tail":
        from sentinel_dns import tail_cmd

        tail_cmd.main()
    elif cmd == "explain":
        from sentinel_dns import explain_cmd

        explain_cmd.main()


if __name__ == "__main__":
    main()
