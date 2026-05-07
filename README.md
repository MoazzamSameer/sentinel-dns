# sentinel-dns

An AI-assisted DNS resolver that doesn't just answer queries — it understands them.

## What is this?

A normal DNS resolver is a phone book: it converts `google.com` to `142.250.80.46`.

`sentinel-dns` is the smart security guard who reads the phone book — it knows which domains are scammers, blocks them, explains why, and can tell you what every device on your network is talking to.

## Status

**Research / spike phase.** No code yet. We're answering the hard questions first:

- Is this even viable at the latency budget DNS demands (<100ms end-to-end)?
- Where does AI actually help vs. plain rule-based blocklists?
- What's the privacy story?
- Who's the user — consumer, prosumer, SMB, enterprise?

See [docs/RESEARCH.md](docs/RESEARCH.md) for the open questions and [docs/PROJECT.md](docs/PROJECT.md) for what's being worked on.

## Why Python?

For the research phase: fastest iteration, easiest ML integration. The production resolver may be rewritten in Go later — but that decision is downstream of "does the AI actually add value."

## License

MIT. See [LICENSE](LICENSE).
