# Configuration

Until this task, all settings came from CLI flags only — fine for benchmarks, awkward for a daemon you actually want to run. This adds a TOML config file. CLI flags still work; they override file values.

## Quick start

```bash
cp sentinel-dns.example.toml sentinel-dns.toml
$EDITOR sentinel-dns.toml
sentinel-dns --config sentinel-dns.toml
```

Without `--config`, the forwarder runs with hard-coded defaults — same behavior as before this PR. Zero-config first run still works.

## Schema

Flat: every key in the TOML file maps directly to a field on the [`Config`](../sentinel_dns/config.py) dataclass. **No sections** — they're rejected at load time with a clear error pointing at the field that's nested. The flat format is uglier but keeps the loader to ~30 lines, makes precedence merging trivial, and means typos in TOML produce errors that point at exactly the right place.

See [`sentinel-dns.example.toml`](../sentinel-dns.example.toml) for the full set of keys with comments. Every key is optional; missing keys fall back to the dataclass default.

## Precedence

Lowest to highest:

1. **Hard-coded defaults** on the `Config` dataclass.
2. **TOML file** (if `--config <path>` is set).
3. **CLI flags** the user explicitly set on the command line.

This means you can put 99% of your settings in the file and override one for an ad-hoc run:

```bash
# Use the file, but override --listen-port for this run
sentinel-dns --config sentinel-dns.toml --listen-port 5360
```

### How CLI overrides are detected

`argparse.SUPPRESS` is set as the default for every Config-mapped flag. `argparse` then leaves un-passed flags out of the resulting `Namespace` entirely, so the merge logic sees exactly what the user typed — no need for sentinels or `==` checks against defaults.

The non-Config args (`--config`, `--log-level`) keep normal argparse defaults — they're not merged into the Config.

## Validation

Two validations run at startup, before the listener binds:

1. **Unknown keys.** Typos in the TOML produce errors that list valid keys:

   ```
   $ sentinel-dns --config bad.toml
   error: unknown TOML keys: ['listen_porr']. Valid keys: ['block_threshold',
   'blocklist_refresh_s', 'blocklist_url', 'cache_capacity', 'enforce',
   'listen_host', 'listen_port', 'log_path', 'log_retention_days',
   'model_path', 'score_logging', 'upstream_host', 'upstream_port',
   'upstream_timeout']
   ```

2. **TOML sections.** Sectioned TOML (`[listen]\nport = 5354`) is rejected with a pointer to this doc.

3. **`enforce=true` with no inline tier.** Same check as before — if you turn enforcement on without specifying a `model_path` or `blocklist_url`, the parser errors out. Nothing to enforce against otherwise.

## Verification

End-to-end smoke from the four test cases:

```
# file-only
$ cat /tmp/cfg.toml
listen_port = 5399
upstream_host = "9.9.9.9"

$ sentinel-dns --config /tmp/cfg.toml
... listening on 127.0.0.1:5399, upstream 9.9.9.9:53, ...

# CLI overrides file
$ sentinel-dns --config /tmp/cfg.toml --listen-port 5398
... listening on 127.0.0.1:5398, upstream 9.9.9.9:53, ...
                          ^ from CLI         ^ from file

# typo in TOML
$ echo 'listen_porr = 5399' > /tmp/bad.toml
$ sentinel-dns --config /tmp/bad.toml
error: unknown TOML keys: ['listen_porr']. Valid keys: [...]

# sectioned TOML
$ sentinel-dns --config /tmp/sectioned.toml
error: --config: /tmp/sectioned.toml: TOML sections ['listen']
       not supported. Use flat key=value form (see docs/configuration.md).
```

## Caveats

1. **No section / nested schema.** Flat-only is a deliberate simplification; sectioned TOML is rejected to prevent silent ignoring of misplaced keys. If we ever genuinely need nesting (e.g. multiple upstream resolvers), we'll revisit — but for v0.1's flat config, sections would be cosmetic and would complicate the loader.
2. **No env-var or `--config` discovery.** Pass `--config <path>` explicitly. We don't search `~/.config/sentinel-dns/`, `/etc/sentinel-dns/`, or read `$SENTINEL_DNS_CONFIG`. Discovery makes "where is the value coming from" harder to reason about. Add it later if user feedback asks.
3. **No reload on SIGHUP.** Restart the daemon to pick up config changes. Live reload is post-v0.1 polish.
4. **Path values are not resolved against the config file's directory.** `model_path = "models/x.joblib"` is interpreted relative to the process's cwd, not the config file's location. Worth a follow-up if it bites in practice.
5. **No environment variable interpolation in TOML values.** TOML strings are taken literally. Secrets handling is post-v0.1.
6. **`score_logging` instead of `quiet_scoring`.** The CLI has `--quiet-scoring` (a flag), but the TOML key uses the underlying boolean field name `score_logging` (default `true`). Slight asymmetry; documenting both forms in the example file.

## What this unblocks

- The `README.md` quickstart task — a one-liner like `sentinel-dns --config /etc/sentinel-dns/sentinel.toml` reads cleanly in install instructions.
- PyPI / Docker distribution — Docker images can ship with a default config file mounted at a known path, no flag-engineering required.
- Any future setting we add only needs to extend the `Config` dataclass; the merge logic picks it up automatically (with valid-keys validation).
