"""Lexical classifier — char n-gram TF-IDF + logistic regression.

Extracted from bench/spike_b.py for reuse from the forwarder. Spike B's
results justified picking this exact shape — 81% recall at <1% FPR, 95%
precision at 0.1% FPR, fast enough (we hope) for inline scoring. This
module is the production embodiment of that result.

Two things kept from the spike intentionally:
- Same hyperparameters (char_wb 2-4 grams, 20k features, balanced LR).
- Same heuristic ruleset, kept alongside, because the architecture
  commits to heuristics+ML+explanations as the inline tier.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

ABUSED_TLDS: frozenset[str] = frozenset({
    "xyz", "top", "club", "icu", "live", "work", "pw",
    "buzz", "lat", "surf", "click", "cyou", "rest",
    "monster", "best", "bond", "sbs", "shop", "online",
})

_HEX_LABEL = re.compile(r"^[0-9a-f]{16,}$")
_LONG_CONSONANTS = re.compile(r"[bcdfghjklmnpqrstvwxz]{6,}")


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for c in s:
        counts[c] = counts.get(c, 0) + 1
    total = len(s)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


@dataclass(frozen=True)
class HeuristicReasons:
    """Structured signals — feeds the explanation generator."""
    abused_tld: bool
    high_entropy: bool
    very_high_entropy: bool
    long: bool
    very_long: bool
    digit_heavy: bool
    many_dashes: bool
    hex_label: bool
    consonant_run: bool

    @property
    def score(self) -> float:
        score = 0.0
        if self.abused_tld:
            score += 0.30
        if self.high_entropy:
            score += 0.20
        if self.very_high_entropy:
            score += 0.10
        if self.long:
            score += 0.10
        if self.very_long:
            score += 0.10
        if self.digit_heavy:
            score += 0.10
        if self.many_dashes:
            score += 0.10
        if self.hex_label:
            score += 0.20
        if self.consonant_run:
            score += 0.10
        return min(score, 1.0)


def heuristic_signals(domain: str) -> HeuristicReasons:
    parts = domain.split(".")
    tld = parts[-1] if parts else ""
    sld = parts[-2] if len(parts) >= 2 else (parts[0] if parts else "")
    sld_entropy = shannon_entropy(sld) if sld else 0.0
    digit_ratio = (sum(c.isdigit() for c in sld) / len(sld)) if sld else 0.0
    return HeuristicReasons(
        abused_tld=tld in ABUSED_TLDS,
        high_entropy=sld_entropy > 3.5,
        very_high_entropy=sld_entropy > 4.0,
        long=len(domain) > 30,
        very_long=len(domain) > 50,
        digit_heavy=digit_ratio > 0.30,
        many_dashes=domain.count("-") >= 3,
        hex_label=any(_HEX_LABEL.match(label) for label in parts),
        consonant_run=any(_LONG_CONSONANTS.search(label) for label in parts[:-1]),
    )


def heuristic_score(domain: str) -> float:
    """Convenience wrapper. Use heuristic_signals() if you also need reasons."""
    return heuristic_signals(domain).score


class LexicalClassifier:
    """TF-IDF char n-grams + logistic regression. Spike B's winner."""

    def __init__(
        self,
        ngram_range: tuple[int, int] = (2, 4),
        max_features: int = 20_000,
    ) -> None:
        self.vectorizer = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=ngram_range,
            max_features=max_features,
            lowercase=True,
        )
        self.model = LogisticRegression(
            max_iter=1000,
            class_weight="balanced",
            C=1.0,
            solver="liblinear",
        )

    def fit(self, domains: list[str], labels: np.ndarray) -> None:
        X = self.vectorizer.fit_transform(domains)
        self.model.fit(X, labels)

    def score(self, domain: str) -> float:
        X = self.vectorizer.transform([domain])
        return float(self.model.predict_proba(X)[0, 1])

    def score_batch(self, domains: list[str]) -> np.ndarray:
        X = self.vectorizer.transform(domains)
        return self.model.predict_proba(X)[:, 1]

    def save(self, path: str | Path) -> None:
        joblib.dump(
            {"vectorizer": self.vectorizer, "model": self.model},
            path,
            compress=3,
        )

    @classmethod
    def load(cls, path: str | Path) -> "LexicalClassifier":
        bundle = joblib.load(path)
        instance = cls.__new__(cls)
        instance.vectorizer = bundle["vectorizer"]
        instance.model = bundle["model"]
        return instance
