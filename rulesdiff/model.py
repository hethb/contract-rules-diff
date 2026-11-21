"""Core types: contracts, extracted rule facets, and differences."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Venue(StrEnum):
    KALSHI = "kalshi"
    POLYMARKET = "polymarket"
    OTHER = "other"


@dataclass(frozen=True)
class Contract:
    """One binary contract as a venue describes it."""

    venue: Venue
    #: Venue-native identifier (ticker, condition id, slug).
    id: str
    title: str
    #: The yes/no question in the venue's words.
    question: str
    #: Full settlement rules text.
    rules: str
    #: When trading stops and when the contract is expected to settle, if stated.
    close_time: datetime | None = None
    settle_time: datetime | None = None
    #: Free-form source field some venues expose separately from the rules text.
    stated_source: str | None = None

    @property
    def ref(self) -> str:
        return f"{self.venue.value}:{self.id}"


class Facet(StrEnum):
    """A dimension of a settlement rule that can differ between venues."""

    SOURCE = "source"
    THRESHOLD = "threshold"
    COMPARATOR = "comparator"
    MEASUREMENT_WINDOW = "measurement_window"
    REVISIONS = "revisions"
    ROUNDING = "rounding"
    TIMEZONE = "timezone"
    DEADLINE = "deadline"
    EARLY_RESOLUTION = "early_resolution"
    VOID_CONDITIONS = "void_conditions"
    TIE_HANDLING = "tie_handling"
    RESOLVER = "resolver"


#: One extracted statement about a contract, with the text it came from.
@dataclass(frozen=True)
class Extraction:
    facet: Facet
    #: Normalized value, comparable across venues (e.g. "3.0%", "BLS", "initial_release").
    value: str
    #: The sentence or phrase it was read from, for auditing.
    evidence: str
    #: 0 to 1. Low confidence means a human should read the rules themselves.
    confidence: float = 1.0


@dataclass
class RuleProfile:
    contract: Contract
    extractions: list[Extraction] = field(default_factory=list)

    def values(self, facet: Facet) -> list[str]:
        return [e.value for e in self.extractions if e.facet == facet]

    def first(self, facet: Facet) -> Extraction | None:
        return next((e for e in self.extractions if e.facet == facet), None)


class Severity(StrEnum):
    """How much a difference can move the payout."""

    CRITICAL = "critical"  # the two contracts can settle to opposite outcomes
    MATERIAL = "material"  # settlement can differ in realistic cases
    MINOR = "minor"  # affects timing or wording, rarely the outcome
    INFO = "info"


SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.MATERIAL: 1, Severity.MINOR: 2, Severity.INFO: 3}


@dataclass(frozen=True)
class Difference:
    facet: Facet
    severity: Severity
    left: str | None
    right: str | None
    #: Plain-language statement of what can go wrong.
    consequence: str
    left_evidence: str | None = None
    right_evidence: str | None = None

    def describe(self) -> str:
        return f"[{self.severity.value}] {self.facet.value}: {self.left or 'not stated'} vs {self.right or 'not stated'} - {self.consequence}"


@dataclass
class PairReport:
    left: RuleProfile
    right: RuleProfile
    #: Title-matching score that produced the pair, 0 to 1.
    match_score: float
    differences: list[Difference] = field(default_factory=list)

    @property
    def worst(self) -> Severity:
        if not self.differences:
            return Severity.INFO
        return min((d.severity for d in self.differences), key=lambda s: SEVERITY_ORDER[s])

    @property
    def equivalent(self) -> bool:
        """True when nothing found can change which side pays out."""
        return all(d.severity in (Severity.MINOR, Severity.INFO) for d in self.differences)
