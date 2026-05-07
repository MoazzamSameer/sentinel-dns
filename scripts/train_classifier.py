"""Train the lexical classifier on URLhaus + Tranco and save to disk.

Same data and split as bench/spike_b.py — this trains on the full
URLhaus + 50k Tranco sample and serializes the model. The forwarder
loads from this file with --model-path.

Run after fetching the data files (see docs/spike-b-results.md):

    python scripts/train_classifier.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sentinel_dns.classifier import LexicalClassifier  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
MODELS_DIR = REPO_ROOT / "models"
URLHAUS_PATH = DATA_DIR / "urlhaus_hosts.txt"
TRANCO_PATH = DATA_DIR / "tranco_top100k.csv"
DEFAULT_MODEL_PATH = MODELS_DIR / "classifier_v0.joblib"


def _is_ip_like(host: str) -> bool:
    return bool(re.match(r"^\d+\.\d+\.\d+\.\d+", host))


def load_urlhaus() -> list[str]:
    out: list[str] = []
    for line in URLHAUS_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            host = parts[1].lower()
            if not _is_ip_like(host):
                out.append(host)
    return out


def load_tranco(limit: int = 50_000) -> list[str]:
    out: list[str] = []
    for line in TRANCO_PATH.read_text().splitlines():
        if "," not in line:
            continue
        _, domain = line.split(",", 1)
        out.append(domain.lower())
        if len(out) >= limit:
            break
    return out


def main() -> None:
    print("loading data...")
    malicious = load_urlhaus()
    benign = load_tranco()
    print(f"  malicious: {len(malicious)}")
    print(f"  benign:    {len(benign)}")

    domains = malicious + benign
    labels = np.array([1] * len(malicious) + [0] * len(benign))

    print("training classifier on full dataset...")
    clf = LexicalClassifier()
    clf.fit(domains, labels)

    MODELS_DIR.mkdir(exist_ok=True)
    clf.save(DEFAULT_MODEL_PATH)
    size_kb = DEFAULT_MODEL_PATH.stat().st_size / 1024
    print(f"saved to {DEFAULT_MODEL_PATH} ({size_kb:.1f} KB)")

    print("\nsmoke test...")
    samples = [
        "google.com",
        "github.com",
        "1ce6-route.fixionmunici9al.lat",
        "5cri-logic.xamir3on.lat",
        "malwarebytes.com",
    ]
    for d in samples:
        print(f"  {clf.score(d):.4f}  {d}")


if __name__ == "__main__":
    main()
