# sentinel-dns

A self-hosted DNS resolver that **explains every block in plain English**, and catches fresh malicious domains via lexical analysis — not just threat-feed lookups.

```
$ dig @127.0.0.1 -p 5354 1ce6-route.fixionmunici9al.lat
;; status: NXDOMAIN

(forwarder log)
BLOCK qname=1ce6-route.fixionmunici9al.lat ml=0.9980 ... source=classifier signals=lexical_classifier,abused_tld
explain qname=1ce6-route.fixionmunici9al.lat — Our ML model is 100% confident this name looks malicious. Uses the .lat TLD, frequently abused for short-lived attack domains.
```

## Status

**Early v0.x.** Phase 0 (research) and Phase 1 (spikes) complete; Phase 2 (build) in progress. See [`docs/PROJECT.md`](docs/PROJECT.md) for current state and what's next.

The forwarder runs end-to-end: classifier + cache + URLhaus blocklist + plain-language explanations + SQLite query log + DoH upstream + CLI inspection tools. **Not yet on PyPI**, not yet packaged for Docker, not yet verified on Pi 4 hardware. Install from source.

## Why?

Pi-hole says "blocked." Quad9 says "blocked for security." sentinel-dns says specifically *which signals fired* and *what they mean*. The combination — self-hosted **and** plain-English explanations **and** fresh-domain detection from lexical features — is the wedge:

| Tool | Self-hosted | Plain-English explanations | Fresh-domain detection |
|---|---|---|---|
| Pi-hole | ✅ | ❌ (just "blocked") | ❌ (rule-based only) |
| Quad9 | ❌ | ❌ | partial (feeds only) |
| NextDNS | ❌ | category labels | partial |
| Cloudflare 1.1.1.1 (Families) | ❌ | ❌ | ❌ |
| AdGuard DNS | ❌ | category labels | partial |
| **sentinel-dns** | ✅ | ✅ structured + plain English | ✅ inline lexical + async deeper |

The full reasoning is in [`docs/RESEARCH.md`](docs/RESEARCH.md).

## Quickstart

### 30-second version: blocklist-only enforcement (Docker)

```bash
docker run --rm -p 5354:5354/udp ghcr.io/moazzamsameer/sentinel-dns:latest \
    --listen-host 0.0.0.0 \
    --blocklist-url https://urlhaus.abuse.ch/downloads/hostfile/ \
    --enforce
```

Or from source — no Python training required, pulls URLhaus on startup:

```bash
git clone https://github.com/MoazzamSameer/sentinel-dns.git
cd sentinel-dns
python3.11 -m venv .venv
.venv/bin/pip install -e .

.venv/bin/sentinel-dns \
    --listen-port 5354 \
    --blocklist-url https://urlhaus.abuse.ch/downloads/hostfile/ \
    --enforce
```

Test it:

```bash
dig @127.0.0.1 -p 5354 google.com +short
# → 142.251.223.142

dig @127.0.0.1 -p 5354 1ce6-route.fixionmunici9al.lat +short
# → ;; status: NXDOMAIN
```

### Full version: classifier + blocklist + cache + log

```bash
# Fetch training data (URLhaus malicious + Tranco benign)
mkdir -p data
curl -sS -o data/urlhaus_hosts.txt https://urlhaus.abuse.ch/downloads/hostfile/
curl -sS -L -o /tmp/tranco.zip https://tranco-list.eu/top-1m.csv.zip
unzip -p /tmp/tranco.zip top-1m.csv | head -100000 > data/tranco_top100k.csv

# Train the lexical classifier (~5 sec, produces models/classifier_v0.joblib at ~320 KB)
.venv/bin/python scripts/train_classifier.py

# Copy the example config and tweak as needed
cp sentinel-dns.example.toml sentinel-dns.toml

# Run with everything on
.venv/bin/sentinel-dns --config sentinel-dns.toml --enforce
```

In another shell:

```bash
.venv/bin/sentinel-dns tail -f --log-path sentinel.db
```

Then `dig` away. Each block in the tail will include a plain-language explanation:

```
2026-05-08 17:13:04  BLOCK classifier  cache=miss  127.0.0.1  1ce6-route.fixionmunici9al.lat
  └ Our ML model is 100% confident this name looks malicious. Uses the .lat TLD, frequently abused for short-lived attack domains.
```

Inspect any past decision:

```bash
.venv/bin/sentinel-dns explain example.com --log-path sentinel.db
```

## What it does

- **Forwarder over UDP** with optional DoH upstream (`--upstream-doh-url`). The ISP can't see which domains you query when DoH is on. ([docs/doh-upstream.md](docs/doh-upstream.md))
- **Inline tier**: decision cache → static URLhaus blocklist → heuristics + lexical classifier. ~145µs p50 classifier inference; cache hits are sub-microsecond. ([docs/decision-cache.md](docs/decision-cache.md), [docs/static-blocklist.md](docs/static-blocklist.md))
- **Plain-language explanations** templated from structured signals — no LLM at query time. Same prose in the forwarder log, in the SQLite log, and from `sentinel-dns explain`. ([docs/explanations.md](docs/explanations.md))
- **SQLite query log** with retention, async batched writes, never blocks the response path. ([docs/query-log.md](docs/query-log.md))
- **CLI inspection tools**: `tail -f` for live streaming, `explain <domain>` for retrospective lookups. ([docs/cli.md](docs/cli.md))
- **TOML config** with CLI override precedence. ([docs/configuration.md](docs/configuration.md))
- **Multi-arch Docker image** (`amd64` + `arm64`) at `ghcr.io/moazzamsameer/sentinel-dns`. Three documented deployment patterns including `--cap-add NET_BIND_SERVICE` for binding `:53`. ([docs/docker.md](docs/docker.md))

## How well does it work?

Numbers from the spike phase:

- **Classifier:** 81% recall on held-out URLhaus malware at <1% false-positive rate; 95% precision at the strict 0.1% FPR operating point used for inline blocking ([docs/spike-b-results.md](docs/spike-b-results.md))
- **Latency:** with cache populated, +0.89ms p50 vs raw forwarding to 1.1.1.1 ([docs/decision-cache.md](docs/decision-cache.md))
- **Pi 4 projection** via M1 efficiency-core simulation: +2.37ms p50 end-to-end. Real hardware verification still pending. ([docs/pi4-projection-results.md](docs/pi4-projection-results.md))

Caveats and what isn't yet measured are documented in each writeup.

## Configuration

`sentinel-dns.example.toml` walks through every key. The schema is flat (no sections); each key maps directly to a `Config` field. CLI flags override file values.

```toml
listen_host = "127.0.0.1"
listen_port = 5354

upstream_host = "1.1.1.1"
# Or, for ISP privacy: upstream_doh_url = "https://cloudflare-dns.com/dns-query"

model_path = "models/classifier_v0.joblib"
blocklist_url = "https://urlhaus.abuse.ch/downloads/hostfile/"
log_path = "sentinel.db"
log_retention_days = 7
enforce = false  # flip to true once you've watched the log for a day or two
```

## Project layout

```
sentinel_dns/         resolver, classifier, cache, blocklist, query log, CLI
bench/                latency benchmarks (forwarder, synthesis, cache, Pi 4 projection)
scripts/              training script for the classifier
docs/                 design + spike writeups (links above)
sentinel-dns.example.toml   annotated config example
```

## Status & roadmap

- **Phase 0 (research):** complete — viability analysis, architecture, MVP scope
- **Phase 1 (spikes):** complete — forwarder, classifier, synthesis, cache, Pi 4 projection
- **Phase 2 (build v0.1):** in progress — see [`docs/PROJECT.md`](docs/PROJECT.md)
- **v0.1 release:** gated on actual Pi 4 hardware verification + PyPI/Docker distribution

## License

MIT. See [LICENSE](LICENSE).
