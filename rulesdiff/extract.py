"""Read settlement rules and pull out the facets that decide a payout.

This is deliberately rule-based rather than a language model: the output has
to be auditable (every extraction carries the sentence it came from), stable
across runs, and cheap enough to run over every contract on both venues
whenever rules change. Where a pattern does not fire, the facet is simply
absent, which the diff reports as "not stated" rather than guessing.
"""

from __future__ import annotations

import re

from .model import Contract, Extraction, Facet, RuleProfile

# --------------------------------------------------------------------- helpers

_ABBREV = re.compile(r"\b(?:U\.S|Inc|No|Dr|Mr|Ms|e\.g|i\.e|a\.m|p\.m|Jan|Feb|Aug|Sept|Oct|Nov|Dec)\.$")


def sentences(text: str) -> list[str]:
    """Split on sentence ends, keeping common abbreviations intact."""
    out: list[str] = []
    buf = ""
    for piece in re.split(r"(?<=[.!?])\s+", text.strip()):
        buf = f"{buf} {piece}".strip() if buf else piece
        if not _ABBREV.search(buf):
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return [s for s in out if s]


def _find(text: str, pattern: str) -> re.Match[str] | None:
    return re.search(pattern, text, re.I)


# --------------------------------------------------------------------- sources

#: Canonical names for the agencies and outlets that settle most contracts.
SOURCES: dict[str, tuple[str, ...]] = {
    "BLS": ("bureau of labor statistics", r"\bbls\b"),
    "BEA": ("bureau of economic analysis", r"\bbea\b"),
    "FRED": (r"\bfred\b", "federal reserve economic data"),
    "Federal Reserve": ("federal reserve", "fomc", "federalreserve.gov"),
    "Census Bureau": ("census bureau",),
    "EIA": ("energy information administration", r"\beia\b"),
    "NOAA": ("noaa", "national weather service"),
    "AP": ("associated press", r"\bap\b(?! article)"),
    "Edison Research": ("edison research",),
    "Decision Desk HQ": ("decision desk",),
    "CoinGecko": ("coingecko",),
    "Coinbase": ("coinbase",),
    "Binance": ("binance",),
    "Chainlink": ("chainlink",),
    "Nasdaq": ("nasdaq",),
    "NYSE": (r"\bnyse\b", "new york stock exchange"),
    "S&P": (r"s&p", "standard & poor"),
    "Box Office Mojo": ("box office mojo",),
    "Reuters": ("reuters",),
    "Bloomberg": ("bloomberg",),
    "FEC": ("federal election commission", r"\bfec\b"),
    "state election officials": ("secretary of state", "state election", "certified results"),
    "credible public reporting": ("credible public reporting", "consensus of credible", "reputable news"),
}


def _scan_sources(text: str) -> list[Extraction]:
    out: list[Extraction] = []
    for sent in sentences(text):
        for name, patterns in SOURCES.items():
            if any(_find(sent, p) for p in patterns):
                out.append(Extraction(Facet.SOURCE, name, sent))
    # Deduplicate while keeping the first evidence for each source.
    seen: set[str] = set()
    uniq = []
    for e in out:
        if e.value not in seen:
            seen.add(e.value)
            uniq.append(e)
    return uniq


def extract_sources(contract: Contract) -> list[Extraction]:
    """Prefer the venue's own settlement-source field over a scan of the rules.

    An index or statistic is usually named in the rules text ("the S&P 500
    closes above 6,000") even when a different organization publishes the
    number that settles the contract, so a scan alone conflates the subject of
    the bet with its source. When the venue states the source separately, that
    field wins.
    """
    if contract.stated_source:
        stated = _scan_sources(contract.stated_source)
        if stated:
            return [
                Extraction(e.facet, e.value, f"stated settlement source: {contract.stated_source}")
                for e in stated
            ]
    return _scan_sources(contract.rules)


# ------------------------------------------------------------ numbers and comparators

_NUM = r"(-?\$?\d[\d,]*(?:\.\d+)?\s*(?:%|percent|bps|basis points|million|billion|trillion|k)?)"

_COMPARATORS: list[tuple[str, str]] = [
    (r"greater than or equal to|at least|no less than|or (?:higher|more|above)|\bgte\b|>=|≥", ">="),
    (r"less than or equal to|at most|no more than|or (?:lower|less|below)|\blte\b|<=|≤", "<="),
    (r"strictly greater than|greater than|more than|above|exceeds?|\bgt\b|>", ">"),
    (r"strictly less than|less than|fewer than|below|under|\blt\b|<", "<"),
    (r"exactly equal to|equals?|exactly", "=="),
]


def normalize_number(raw: str) -> str:
    """'3.0 percent' -> '3.0%', '$1,500' -> '1500', '2 million' -> '2000000'."""
    s = raw.strip().lower().replace(",", "").replace("$", "")
    pct = s.endswith("%") or "percent" in s
    mult = 1.0
    for word, factor in (("trillion", 1e12), ("billion", 1e9), ("million", 1e6), ("k", 1e3)):
        if s.endswith(word):
            mult = factor
            s = s[: -len(word)]
            break
    s = s.replace("percent", "").replace("%", "").replace("basis points", "").replace("bps", "").strip()
    try:
        v = float(s) * mult
    except ValueError:
        return raw.strip()

    def fmt(x: float) -> str:
        return f"{x:.10f}".rstrip("0").rstrip(".") or "0"

    if "bps" in raw.lower() or "basis point" in raw.lower():
        return f"{fmt(v / 100)}%"
    text = fmt(v)
    return f"{text}%" if pct else text


def extract_threshold(text: str) -> list[Extraction]:
    """Find the comparison that decides the contract, plus its number."""
    out: list[Extraction] = []
    for sent in sentences(text):
        if not _find(sent, r"resolve|settle|yes\b|no\b"):
            continue
        for pattern, symbol in _COMPARATORS:
            m = re.search(rf"({pattern})\s+{_NUM}", sent, re.I)
            if not m:
                continue
            out.append(Extraction(Facet.COMPARATOR, symbol, sent))
            out.append(Extraction(Facet.THRESHOLD, normalize_number(m.group(2)), sent))
            break
        if out:
            break
    return out


# ------------------------------------------------------------------- revisions


def extract_revisions(text: str) -> list[Extraction]:
    rules = [
        (
            r"initial (?:release|print|estimate|reading)|first (?:release|print)|as (?:first|initially) (?:published|released)|advance estimate",
            "initial_release",
        ),
        (
            r"revis(?:ed|ions?) (?:data|figures?|values?)|final (?:release|estimate|revision)|third estimate|subsequent revisions? (?:will|shall) be used",
            "revised_data",
        ),
        (
            r"revisions? (?:will|shall|are) not|not (?:be )?(?:subject to|affected by) (?:any )?revisions?|ignore (?:any )?(?:subsequent )?revisions?",
            "initial_release",
        ),
        (
            r"(?:value|figure|data) (?:available|published|reported) (?:at|as of|on) (?:the )?(?:time of )?(?:expiration|settlement|resolution)|most recently published value as of",
            "value_at_settlement",
        ),
    ]
    out = []
    for sent in sentences(text):
        for pattern, value in rules:
            if _find(sent, pattern):
                out.append(Extraction(Facet.REVISIONS, value, sent))
                break
    return out[:1]


# -------------------------------------------------------------------- rounding


def extract_rounding(text: str) -> list[Extraction]:
    for sent in sentences(text):
        m = _find(sent, r"round(?:ed|ing)?\s+(?:to\s+)?(?:the\s+)?(?:nearest\s+)?([\w .]+?)(?:[.,]|$)")
        if m:
            return [Extraction(Facet.ROUNDING, f"to {m.group(1).strip()}", sent)]
        if _find(sent, r"(?:as|exactly as) (?:published|reported|printed)|without rounding|unrounded"):
            return [Extraction(Facet.ROUNDING, "as published", sent)]
    return []


# -------------------------------------------------------------------- timezone

_TZ = {
    "ET": (r"\bET\b", r"\beastern time\b", r"\bEST\b", r"\bEDT\b"),
    "UTC": (r"\bUTC\b", r"\bGMT\b", r"\bzulu\b"),
    "CT": (r"\bCT\b", r"\bcentral time\b"),
    "PT": (r"\bPT\b", r"\bpacific time\b"),
}


def extract_timezone(text: str) -> list[Extraction]:
    for sent in sentences(text):
        for name, patterns in _TZ.items():
            if any(re.search(p, sent) for p in patterns):
                return [Extraction(Facet.TIMEZONE, name, sent)]
    return []


# -------------------------------------------------------- deadlines and windows

_MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"


def extract_deadline(text: str) -> list[Extraction]:
    for sent in sentences(text):
        if not _find(
            sent,
            r"\bby\b|before|on or before|no later than|deadline|expir|\bas of\b|if .* (?:has )?not .*(?:occur|happen|announc)",
        ):
            continue
        m = re.search(rf"({_MONTH}\s+\d{{1,2}},?\s+\d{{4}})", sent, re.I)
        if m:
            return [Extraction(Facet.DEADLINE, m.group(1).replace(",", ""), sent)]
        m = re.search(r"(\d{4}-\d{2}-\d{2})", sent)
        if m:
            return [Extraction(Facet.DEADLINE, m.group(1), sent)]
    return []


def extract_window(text: str) -> list[Extraction]:
    out = []
    for sent in sentences(text):
        m = _find(
            sent,
            rf"(?:for|covering|during|in) (?:the )?({_MONTH}\s+\d{{4}}|Q[1-4]\s+\d{{4}}|\d{{4}}\s+Q[1-4]|(?:first|second|third|fourth) quarter(?: of)? \d{{4}})",
        )
        if m:
            out.append(Extraction(Facet.MEASUREMENT_WINDOW, m.group(1).strip().title(), sent))
            break
        m = _find(
            sent,
            r"(?:over|across|during) (?:the|a) (?:trailing |preceding |last )?(\d+[- ](?:day|hour|week|month)s?)",
        )
        if m:
            out.append(Extraction(Facet.MEASUREMENT_WINDOW, m.group(1).replace("-", " "), sent))
            break
    return out


# ---------------------------------------------------- early resolution and voids


def extract_early_resolution(text: str) -> list[Extraction]:
    for sent in sentences(text):
        if _find(
            sent,
            r"may resolve early|resolve(?:s|d)? (?:immediately|as soon as)|before (?:the )?(?:scheduled )?(?:expiration|close) if|once the (?:outcome|threshold) (?:is|has been)",
        ):
            return [Extraction(Facet.EARLY_RESOLUTION, "allowed", sent)]
        if _find(
            sent,
            r"(?:will|shall) not resolve (?:before|until)|only (?:at|after) (?:the )?(?:scheduled )?expiration|no early (?:resolution|settlement)",
        ):
            return [Extraction(Facet.EARLY_RESOLUTION, "not allowed", sent)]
    return []


def extract_void(text: str) -> list[Extraction]:
    out = []
    for sent in sentences(text):
        if _find(sent, r"\b(?:void|cancell?ed|refund(?:ed)?|unwound|50-50|50/50|resolve(?:s|d)? to 50)"):
            value = "void_or_refund"
            if _find(sent, r"50-50|50/50|resolve(?:s|d)? to 50"):
                value = "resolves_50_50"
            out.append(Extraction(Facet.VOID_CONDITIONS, value, sent))
    return out[:2]


def extract_tie(text: str) -> list[Extraction]:
    for sent in sentences(text):
        if _find(sent, r"\btie\b|\bties\b|exactly equal|precisely equal|lands on the (?:threshold|strike)"):
            if _find(sent, r"resolve(?:s)? (?:to )?(?:yes|no)"):
                side = "yes" if _find(sent, r"resolve(?:s)? (?:to )?yes") else "no"
                return [Extraction(Facet.TIE_HANDLING, f"exact value resolves {side}", sent)]
            return [Extraction(Facet.TIE_HANDLING, "addressed", sent, confidence=0.6)]
    return []


def extract_resolver(text: str, contract: Contract) -> list[Extraction]:
    for sent in sentences(text):
        if _find(sent, r"uma|optimistic oracle|token holders? vote|dispute(?:d|s)? (?:are|is) resolved by"):
            return [Extraction(Facet.RESOLVER, "UMA optimistic oracle", sent)]
        if _find(
            sent,
            r"(?:exchange|market operator|kalshi) (?:will )?determine|at the (?:sole )?discretion of the (?:exchange|operator)",
        ):
            return [Extraction(Facet.RESOLVER, "venue discretion", sent)]
    default = {"polymarket": "UMA optimistic oracle", "kalshi": "venue discretion"}.get(contract.venue.value)
    if default:
        return [
            Extraction(
                Facet.RESOLVER, default, "venue default (not stated in the rules text)", confidence=0.4
            )
        ]
    return []


# ------------------------------------------------------------------------ entry


def profile(contract: Contract) -> RuleProfile:
    """Extract every facet from a contract's rules."""
    text = contract.rules
    if contract.stated_source:
        text = f"{text}\nSource: {contract.stated_source}."
    out: list[Extraction] = []
    out += extract_sources(contract)
    out += extract_threshold(text)
    out += extract_window(text)
    out += extract_revisions(text)
    out += extract_rounding(text)
    out += extract_timezone(text)
    out += extract_deadline(text)
    out += extract_early_resolution(text)
    out += extract_void(text)
    out += extract_tie(text)
    out += extract_resolver(text, contract)
    return RuleProfile(contract, out)
