"""Pair contracts that are supposed to be the same bet.

Matching is intentionally conservative: a pair is only proposed when the
titles agree on the parts that carry meaning. Two scores are combined:

* character trigram cosine similarity over the normalized title and question,
  which tolerates different house styles ("Will CPI be above 3%?" versus
  "CPI above 3.0% in March"), and
* an anchor check on the tokens that change the bet: numbers, months, years
  and capitalized names. A pair that disagrees on a number or a month is
  rejected outright rather than scored down, because those are exactly the
  near-identical titles a human skims past.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from .model import Contract

_STOP = {
    "will",
    "the",
    "be",
    "a",
    "an",
    "of",
    "in",
    "on",
    "at",
    "to",
    "for",
    "by",
    "is",
    "are",
    "do",
    "does",
    "than",
    "or",
    "and",
    "this",
    "that",
    "it",
    "any",
    "market",
    "contract",
}

_MONTHS = {
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
}
_MONTH_ABBR = {m[:3]: m for m in _MONTHS}


def normalize(text: str) -> str:
    t = text.lower()
    t = t.replace("%", " percent ").replace("$", " dollar ")
    t = re.sub(r"[^a-z0-9. ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    words = []
    for w in t.split():
        words.append(_MONTH_ABBR.get(w.rstrip("."), w))
    return " ".join(w for w in words if w not in _STOP)


def trigrams(text: str) -> Counter[str]:
    s = f"  {text} "
    return Counter(s[i : i + 3] for i in range(len(s) - 2))


def cosine(a: Counter[str], b: Counter[str]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b[k] for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _canonical_number(tok: str) -> str:
    try:
        return f"{float(tok.rstrip('.')):g}"
    except ValueError:
        return tok


def anchors(text: str) -> tuple[set[str], set[str], set[str]]:
    """Return (quantities, months, years) that a matching pair must agree on."""
    norm = normalize(text)
    numbers = {_canonical_number(t) for t in re.findall(r"\b\d+(?:\.\d+)?\b", norm)}
    months = {t for t in norm.split() if t in _MONTHS}
    years = {t for t in numbers if re.fullmatch(r"(19|20)\d\d", t)}
    return numbers - years, months, years


def similarity(a: Contract, b: Contract) -> float:
    """0 to 1 title similarity, or 0 when the anchors disagree."""
    ta, tb = f"{a.title} {a.question}", f"{b.title} {b.question}"
    # Each kind of anchor present on both sides must agree; one side omitting
    # it is allowed, since venues abbreviate titles differently.
    for left, right in zip(anchors(ta), anchors(tb), strict=True):
        if left and right and not (left & right):
            return 0.0
    title = cosine(trigrams(normalize(a.title)), trigrams(normalize(b.title)))
    full = cosine(trigrams(normalize(ta)), trigrams(normalize(tb)))
    return max(title, full)


def match(
    left: list[Contract], right: list[Contract], threshold: float = 0.55
) -> list[tuple[Contract, Contract, float]]:
    """Greedy one-to-one matching of contracts across two venues, best pairs first."""
    scored = [(a, b, s) for a in left for b in right if (s := similarity(a, b)) >= threshold]
    scored.sort(key=lambda t: -t[2])
    used_l: set[str] = set()
    used_r: set[str] = set()
    out = []
    for a, b, s in scored:
        if a.ref in used_l or b.ref in used_r:
            continue
        used_l.add(a.ref)
        used_r.add(b.ref)
        out.append((a, b, s))
    return out
