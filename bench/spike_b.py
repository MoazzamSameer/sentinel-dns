"""Spike B: domain classifier on URLhaus + Tranco.

Question: do lexical features alone — heuristics OR logistic regression on
character n-grams — detect fresh malicious domains at an acceptable false
positive rate?

Pass criterion (per ROADMAP.md K2): at least one of (heuristics, ML)
achieves >= 10pp recall at < 1% FPR on a held-out test set. A naive
blocklist baseline gets 0% recall on never-before-seen domains by
construction, so 10pp is a low but meaningful bar — it's the test that
lexical features carry signal at all.

Run after fetching the data files:

    python bench/spike_b.py
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.model_selection import train_test_split

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
URLHAUS_PATH = DATA_DIR / "urlhaus_hosts.txt"
TRANCO_PATH = DATA_DIR / "tranco_top100k.csv"

ABUSED_TLDS = {
    "xyz", "top", "club", "icu", "live", "work", "pw",
    "buzz", "lat", "surf", "click", "cyou", "rest",
    "monster", "best", "bond", "sbs", "shop", "online",
}


# ---------- Data loading ----------

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


def load_tranco(limit: int | None = None) -> list[str]:
    out: list[str] = []
    for line in TRANCO_PATH.read_text().splitlines():
        if "," not in line:
            continue
        _, domain = line.split(",", 1)
        out.append(domain.lower())
        if limit and len(out) >= limit:
            break
    return out


# ---------- (b) Heuristic scorer ----------

def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for c in s:
        counts[c] = counts.get(c, 0) + 1
    total = len(s)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def heuristic_score(domain: str) -> float:
    """Score in [0, 1] — higher means more likely malicious.

    Combines simple, transparent rules over lexical features. Designed
    to be explainable: every contribution is a discrete signal we can
    surface to the user in the explanation generator.
    """
    score = 0.0
    parts = domain.split(".")

    tld = parts[-1] if parts else ""
    if tld in ABUSED_TLDS:
        score += 0.30

    sld = parts[-2] if len(parts) >= 2 else (parts[0] if parts else "")
    if sld:
        ent = shannon_entropy(sld)
        if ent > 3.5:
            score += 0.20
        if ent > 4.0:
            score += 0.10

    if len(domain) > 30:
        score += 0.10
    if len(domain) > 50:
        score += 0.10

    if sld:
        digit_ratio = sum(c.isdigit() for c in sld) / len(sld)
        if digit_ratio > 0.30:
            score += 0.10

    if domain.count("-") >= 3:
        score += 0.10

    for label in parts:
        if re.match(r"^[0-9a-f]{16,}$", label):
            score += 0.20
            break

    for label in parts[:-1]:
        if re.search(r"[bcdfghjklmnpqrstvwxz]{6,}", label):
            score += 0.10
            break

    return min(score, 1.0)


# ---------- (c) N-gram logistic regression classifier ----------

class NGramClassifier:
    def __init__(self, ngram_range: tuple[int, int] = (2, 4), max_features: int = 20_000) -> None:
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

    def predict_proba(self, domains: list[str]) -> np.ndarray:
        X = self.vectorizer.transform(domains)
        return self.model.predict_proba(X)[:, 1]


# ---------- Evaluation ----------

def recall_at_fpr(y_true: np.ndarray, scores: np.ndarray, target_fpr: float) -> tuple[float, float]:
    """Maximum recall across all thresholds with FPR <= target_fpr."""
    fpr, tpr, thresholds = roc_curve(y_true, scores)
    mask = fpr <= target_fpr
    if not mask.any():
        return 0.0, float("inf")
    best_idx_in_mask = int(np.argmax(tpr[mask]))
    return float(tpr[mask][best_idx_in_mask]), float(thresholds[mask][best_idx_in_mask])


def main() -> None:
    print("loading data...")
    malicious = load_urlhaus()
    benign = load_tranco(limit=50_000)
    print(f"  malicious: {len(malicious):>6}")
    print(f"  benign:    {len(benign):>6}")

    domains = malicious + benign
    labels = np.array([1] * len(malicious) + [0] * len(benign))

    train_d, test_d, train_y, test_y = train_test_split(
        domains, labels, test_size=0.3, random_state=42, stratify=labels
    )
    print(f"  train:     {len(train_d):>6} ({int(train_y.sum())} malicious)")
    print(f"  test:      {len(test_d):>6} ({int(test_y.sum())} malicious)")

    print("\nscoring with heuristics (b)...")
    heuristic_scores = np.array([heuristic_score(d) for d in test_d])

    print("training and scoring with n-gram logistic regression (c)...")
    clf = NGramClassifier()
    clf.fit(train_d, train_y)
    ngram_scores = clf.predict_proba(test_d)

    print("\n=== ROC AUC ===")
    print(f"  heuristics:  {roc_auc_score(test_y, heuristic_scores):.3f}")
    print(f"  n-gram LR:   {roc_auc_score(test_y, ngram_scores):.3f}")

    print("\n=== Recall at various FPRs ===")
    print(f"{'FPR':<8}{'Heuristics':<22}{'N-gram LR':<22}")
    for fpr_target in [0.001, 0.005, 0.01, 0.05, 0.10]:
        h_recall, h_thr = recall_at_fpr(test_y, heuristic_scores, fpr_target)
        n_recall, n_thr = recall_at_fpr(test_y, ngram_scores, fpr_target)
        print(
            f"{fpr_target*100:>5.1f}%  "
            f"{h_recall*100:>5.1f}% (thr={h_thr:.3f})    "
            f"{n_recall*100:>5.1f}% (thr={n_thr:.3f})"
        )

    print("\n=== K2 verdict ===")
    h_recall_1, _ = recall_at_fpr(test_y, heuristic_scores, 0.01)
    n_recall_1, _ = recall_at_fpr(test_y, ngram_scores, 0.01)
    print(f"  heuristics @1%FPR: {h_recall_1*100:5.1f}%")
    print(f"  n-gram LR  @1%FPR: {n_recall_1*100:5.1f}%")

    target_pp = 10.0
    best = max(h_recall_1, n_recall_1) * 100
    if best >= target_pp:
        print(f"\n  PASS — best lexical approach catches {best:.1f}% of held-out")
        print(f"  malicious test domains at <1% FPR (target was >= {target_pp:.0f}%).")
    else:
        print(f"\n  FAIL — best lexical approach only catches {best:.1f}% at <1% FPR.")
        print(f"  Target was >= {target_pp:.0f}%. Reshape to transparent rule-based or kill.")

    print("\n=== Sample predictions (sanity check) ===")
    sample_indices = list(range(5)) + list(range(len(test_d) - 5, len(test_d)))
    for i in sample_indices:
        d = test_d[i]
        label = "malicious" if test_y[i] == 1 else "benign   "
        print(
            f"  {label}  h={heuristic_scores[i]:.3f}  ml={ngram_scores[i]:.3f}  {d[:70]}"
        )


if __name__ == "__main__":
    main()
