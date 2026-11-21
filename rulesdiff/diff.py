"""Compare two rule profiles and rank the differences by how much they matter.

Severity is assigned from what the difference can do to a payout, not from how
different the words are:

* CRITICAL: a realistic outcome exists where one contract pays YES and the
  other pays NO. Different thresholds, different comparators at the boundary,
  disagreement about whether revised data counts.
* MATERIAL: settlement can differ in plausible cases, for example different
  data sources for the same statistic, or one venue allowing early resolution.
* MINOR: timing and wording that rarely flips a payout.
"""

from __future__ import annotations

from .model import SEVERITY_ORDER, Difference, Facet, RuleProfile, Severity

#: Sources that publish the same statistic and should agree, with the caveat.
EQUIVALENT_SOURCES: list[tuple[set[str], str]] = [
    (
        {"BLS", "FRED"},
        "FRED republishes BLS series, but FRED carries revisions that the BLS release does not",
    ),
    ({"Federal Reserve", "FRED"}, "FRED mirrors Federal Reserve data with a publication lag"),
    (
        {"AP", "Edison Research", "Decision Desk HQ"},
        "race callers can call at different times and have disagreed",
    ),
    (
        {"CoinGecko", "Coinbase", "Binance", "Chainlink"},
        "crypto venues quote different prices at the same instant",
    ),
]

_CONSEQUENCE = {
    Facet.THRESHOLD: "the contracts pay out on different numbers, so any value between them settles them opposite ways",
    Facet.COMPARATOR: "a value exactly on the threshold settles the two contracts opposite ways",
    Facet.REVISIONS: "a revision between the first print and the final figure settles the two contracts opposite ways",
    Facet.MEASUREMENT_WINDOW: "the contracts measure different periods, so they are not the same bet",
    Facet.SOURCE: "the two sources can report different values for the same quantity",
    Facet.ROUNDING: "a value near the threshold can round across it on one venue and not the other",
    Facet.TIE_HANDLING: "an exact tie on the threshold is handled differently",
    Facet.EARLY_RESOLUTION: "one contract can settle before the other, so a later reversal is captured on only one venue",
    Facet.VOID_CONDITIONS: "one venue may refund or resolve 50/50 where the other still pays a side",
    Facet.DEADLINE: "an event occurring between the two deadlines settles the contracts opposite ways",
    Facet.RESOLVER: "a disputed outcome is decided by different parties, which have disagreed before",
    Facet.TIMEZONE: "an event near midnight can fall on different days",
}

_SEVERITY = {
    Facet.THRESHOLD: Severity.CRITICAL,
    Facet.COMPARATOR: Severity.CRITICAL,
    Facet.REVISIONS: Severity.CRITICAL,
    Facet.MEASUREMENT_WINDOW: Severity.CRITICAL,
    Facet.DEADLINE: Severity.CRITICAL,
    Facet.SOURCE: Severity.MATERIAL,
    Facet.ROUNDING: Severity.MATERIAL,
    Facet.TIE_HANDLING: Severity.MATERIAL,
    Facet.EARLY_RESOLUTION: Severity.MATERIAL,
    Facet.VOID_CONDITIONS: Severity.MATERIAL,
    Facet.RESOLVER: Severity.MATERIAL,
    Facet.TIMEZONE: Severity.MINOR,
}

#: Facets where one venue staying silent is itself worth flagging.
_SILENCE_MATTERS = {Facet.REVISIONS, Facet.TIE_HANDLING, Facet.VOID_CONDITIONS, Facet.EARLY_RESOLUTION}


def _source_note(left: set[str], right: set[str]) -> str | None:
    for group, note in EQUIVALENT_SOURCES:
        if left & group and right & group:
            return note
    return None


def compare(left: RuleProfile, right: RuleProfile) -> list[Difference]:
    out: list[Difference] = []

    # Sources are a set comparison: a contract may name a primary and a fallback.
    ls, rs = set(left.values(Facet.SOURCE)), set(right.values(Facet.SOURCE))
    if ls and rs and not (ls & rs):
        note = _source_note(ls, rs)
        out.append(
            Difference(
                Facet.SOURCE,
                Severity.MATERIAL if note else Severity.CRITICAL,
                ", ".join(sorted(ls)),
                ", ".join(sorted(rs)),
                note or "the two sources are unrelated, so they can report different outcomes",
                left.first(Facet.SOURCE).evidence if left.first(Facet.SOURCE) else None,
                right.first(Facet.SOURCE).evidence if right.first(Facet.SOURCE) else None,
            )
        )
    elif ls and rs and (extra := (ls ^ rs)) and (note := _source_note(extra & ls or ls, extra & rs or rs)):
        # The sets overlap but one venue names an extra source, such as a fallback.
        out.append(
            Difference(Facet.SOURCE, Severity.MINOR, ", ".join(sorted(ls)), ", ".join(sorted(rs)), note)
        )

    for facet in (
        Facet.THRESHOLD,
        Facet.COMPARATOR,
        Facet.MEASUREMENT_WINDOW,
        Facet.REVISIONS,
        Facet.ROUNDING,
        Facet.TIMEZONE,
        Facet.DEADLINE,
        Facet.EARLY_RESOLUTION,
        Facet.VOID_CONDITIONS,
        Facet.TIE_HANDLING,
        Facet.RESOLVER,
    ):
        le, re_ = left.first(facet), right.first(facet)
        lv = le.value if le else None
        rv = re_.value if re_ else None
        if lv == rv:
            continue
        if lv is None or rv is None:
            if facet not in _SILENCE_MATTERS:
                continue
            severity = Severity.MATERIAL
            consequence = f"only one venue states this, so {_CONSEQUENCE[facet]}"
        else:
            severity = _SEVERITY[facet]
            consequence = _CONSEQUENCE[facet]
            # Neither venue stated this in its rules; both values are venue
            # defaults, which is structural context rather than a rule difference.
            if le is not None and re_ is not None and min(le.confidence, re_.confidence) < 0.5:
                severity = Severity.INFO
                consequence = (
                    f"venue defaults differ (at least one contract does not state this): {consequence}"
                )
            # A comparator difference only matters if both sides share a threshold.
            if facet == Facet.COMPARATOR and set(left.values(Facet.THRESHOLD)) != set(
                right.values(Facet.THRESHOLD)
            ):
                severity = Severity.MINOR
        out.append(
            Difference(
                facet,
                severity,
                lv,
                rv,
                consequence,
                le.evidence if le else None,
                re_.evidence if re_ else None,
            )
        )

    out.sort(key=lambda d: (SEVERITY_ORDER[d.severity], d.facet.value))
    return out
