"""Loading contracts: from the bundled sample file, or live from each venue.

The live adapters are thin and dependency-free (urllib), and each one is a
pure `parse_*` function plus a fetch wrapper, so the parsers are tested
against recorded payloads without network access.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from .model import Contract, Venue

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
POLYMARKET_API = "https://gamma-api.polymarket.com"
SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample_contracts.json"


def _dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


# ------------------------------------------------------------------- sample data


def load_sample(path: str | Path | None = None) -> list[Contract]:
    raw = json.loads(Path(path or SAMPLE).read_text())
    return [
        Contract(
            venue=Venue(c["venue"]),
            id=c["id"],
            title=c["title"],
            question=c.get("question", c["title"]),
            rules=c["rules"],
            close_time=_dt(c.get("close_time")),
            settle_time=_dt(c.get("settle_time")),
            stated_source=c.get("stated_source"),
        )
        for c in raw["contracts"]
    ]


# ------------------------------------------------------------------------ Kalshi


def parse_kalshi(payload: dict[str, Any]) -> list[Contract]:
    """Parse a /markets response."""
    out = []
    for m in payload.get("markets", []):
        rules = " ".join(
            x
            for x in (m.get("rules_primary"), m.get("rules_secondary"), m.get("settlement_sources_text"))
            if x
        )
        if not rules:
            rules = m.get("subtitle", "") or ""
        out.append(
            Contract(
                venue=Venue.KALSHI,
                id=m.get("ticker", ""),
                title=m.get("title", ""),
                question=m.get("yes_sub_title") or m.get("subtitle") or m.get("title", ""),
                rules=rules,
                close_time=_dt(m.get("close_time")),
                settle_time=_dt(m.get("expiration_time")),
                stated_source=(m.get("settlement_sources") or [{}])[0].get("name")
                if m.get("settlement_sources")
                else None,
            )
        )
    return out


def fetch_kalshi(
    limit: int = 100, status: str = "open", opener=urllib.request.urlopen
) -> list[Contract]:  # pragma: no cover - network
    q = urllib.parse.urlencode({"limit": limit, "status": status})
    with opener(f"{KALSHI_API}/markets?{q}", timeout=30) as r:
        return parse_kalshi(json.load(r))


# -------------------------------------------------------------------- Polymarket


def parse_polymarket(payload: Iterable[dict[str, Any]]) -> list[Contract]:
    """Parse a Gamma /markets response (a list of market objects)."""
    out = []
    for m in payload:
        out.append(
            Contract(
                venue=Venue.POLYMARKET,
                id=m.get("slug") or str(m.get("id", "")),
                title=m.get("question") or m.get("title", ""),
                question=m.get("question") or m.get("title", ""),
                rules=m.get("description", "") or "",
                close_time=_dt(m.get("endDate")),
                settle_time=_dt(m.get("closedTime")),
                stated_source=m.get("resolutionSource") or None,
            )
        )
    return out


def fetch_polymarket(
    limit: int = 100, closed: bool = False, opener=urllib.request.urlopen
) -> list[Contract]:  # pragma: no cover - network
    q = urllib.parse.urlencode({"limit": limit, "closed": str(closed).lower()})
    with opener(f"{POLYMARKET_API}/markets?{q}", timeout=30) as r:
        return parse_polymarket(json.load(r))
