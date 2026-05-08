"""Configuration: dataclass + TOML loader + CLI merging.

Precedence (lowest to highest):
1. Hard-coded defaults on the `Config` dataclass.
2. Values from the TOML file passed via `--config`.
3. CLI flags the user explicitly set.

Implementation: argparse defaults are set to a sentinel object. After
parsing, anything still equal to the sentinel falls through to the
TOML value if present, then the dataclass default. This lets the user
mix-and-match — set most things in the file, override one with a flag.

The TOML schema is flat (no sections) — each key maps directly to a
Config field. Boring, but unambiguous and trivial to load.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Config:
    listen_host: str = "127.0.0.1"
    listen_port: int = 5354

    upstream_host: str = "1.1.1.1"
    upstream_port: int = 53
    upstream_timeout: float = 2.0
    # When set, queries go upstream over DoH instead of plain UDP.
    # ISP can see only the DoH endpoint, not which domains are queried.
    upstream_doh_url: str | None = None

    model_path: Path | None = None
    block_threshold: float = 0.836  # 0.1% FPR operating point from Spike B
    enforce: bool = False

    score_logging: bool = True
    cache_capacity: int = 100_000

    blocklist_url: str | None = None
    blocklist_refresh_s: int = 3600

    log_path: Path | None = None
    log_retention_days: int = 7


# Fields whose Config type is `Path | None` — TOML strings get coerced.
_PATH_FIELDS: frozenset[str] = frozenset({"model_path", "log_path"})


def load_toml(path: Path) -> dict[str, Any]:
    """Read a TOML file. Returns a flat dict of overrides."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected top-level table, got {type(data).__name__}")
    # Reject sections — flat-only schema.
    nested = {k for k, v in data.items() if isinstance(v, dict)}
    if nested:
        raise ValueError(
            f"{path}: TOML sections {sorted(nested)} not supported. "
            "Use flat key=value form (see docs/configuration.md)."
        )
    return data


def coerce(field_name: str, raw: Any) -> Any:
    """Type-coerce a raw TOML value to the dataclass type."""
    if raw is None:
        return None
    if field_name in _PATH_FIELDS:
        return Path(raw) if raw != "" else None
    return raw


def merge(
    cli_overrides: dict[str, Any],
    file_overrides: dict[str, Any],
) -> Config:
    """Build a Config with CLI > file > defaults precedence.

    Both override dicts use Config field names as keys. CLI overrides
    should only contain keys the user explicitly set (not argparse
    defaults). File overrides contain whatever was in the TOML.

    Unknown keys in either dict raise — fail loudly rather than
    silently ignore typos in config files.
    """
    valid = {f.name for f in fields(Config)}

    unknown_cli = set(cli_overrides) - valid
    if unknown_cli:
        raise ValueError(f"unknown CLI override keys: {sorted(unknown_cli)}")

    unknown_file = set(file_overrides) - valid
    if unknown_file:
        raise ValueError(
            f"unknown TOML keys: {sorted(unknown_file)}. "
            f"Valid keys: {sorted(valid)}"
        )

    merged: dict[str, Any] = {}
    for name in valid:
        if name in cli_overrides:
            merged[name] = coerce(name, cli_overrides[name])
        elif name in file_overrides:
            merged[name] = coerce(name, file_overrides[name])
        # else: dataclass default applies
    return Config(**merged)
