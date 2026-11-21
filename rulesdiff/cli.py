"""Command line interface.

rulesdiff report [--data data/sample_contracts.json] [--html out/report.html] [--min-severity material]
rulesdiff pair data/sample_contracts.json kalshi:CPIYOY-26MAR-T3.0 polymarket:cpi-above-3-march-2026
rulesdiff facets data/sample_contracts.json kalshi:CPIYOY-26MAR-T3.0
rulesdiff live [--limit 100] [--html out/live.html]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .diff import compare
from .extract import profile
from .match import match
from .model import SEVERITY_ORDER, Contract, PairReport, Severity, Venue
from .report import html_report, text_report
from .sources import load_sample


def build_pairs(
    contracts: list[Contract], threshold: float, min_severity: Severity | None = None
) -> list[PairReport]:
    left = [c for c in contracts if c.venue == Venue.KALSHI]
    right = [c for c in contracts if c.venue == Venue.POLYMARKET]
    pairs = []
    for a, b, score in match(left, right, threshold):
        pa, pb = profile(a), profile(b)
        pairs.append(PairReport(pa, pb, score, compare(pa, pb)))
    if min_severity is not None:
        pairs = [p for p in pairs if SEVERITY_ORDER[p.worst] <= SEVERITY_ORDER[min_severity]]
    pairs.sort(key=lambda p: (SEVERITY_ORDER[p.worst], -p.match_score))
    return pairs


def _write_html(pairs: list[PairReport], path: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_report(pairs))
    print(f"wrote {out}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="rulesdiff", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report")
    r.add_argument("--data", default=None)
    r.add_argument("--html")
    r.add_argument("--threshold", type=float, default=0.55)
    r.add_argument("--min-severity", choices=[s.value for s in Severity])

    p = sub.add_parser("pair")
    p.add_argument("data")
    p.add_argument("left")
    p.add_argument("right")

    f = sub.add_parser("facets")
    f.add_argument("data")
    f.add_argument("ref")

    live = sub.add_parser("live")
    live.add_argument("--limit", type=int, default=100)
    live.add_argument("--html")
    live.add_argument("--threshold", type=float, default=0.55)

    a = ap.parse_args(argv)

    if a.cmd == "report":
        pairs = build_pairs(
            load_sample(a.data), a.threshold, Severity(a.min_severity) if a.min_severity else None
        )
        print(text_report(pairs))
        if a.html:
            _write_html(pairs, a.html)
        return 0

    if a.cmd == "pair":
        by_ref = {c.ref: c for c in load_sample(a.data)}
        missing = [x for x in (a.left, a.right) if x not in by_ref]
        if missing:
            raise SystemExit(f"unknown contract(s): {', '.join(missing)}")
        pa, pb = profile(by_ref[a.left]), profile(by_ref[a.right])
        print(text_report([PairReport(pa, pb, 1.0, compare(pa, pb))]))
        return 0

    if a.cmd == "facets":
        by_ref = {c.ref: c for c in load_sample(a.data)}
        if a.ref not in by_ref:
            raise SystemExit(f"unknown contract: {a.ref}")
        pr = profile(by_ref[a.ref])
        print(pr.contract.title)
        for e in pr.extractions:
            conf = "" if e.confidence >= 1 else f"  (confidence {e.confidence:.1f})"
            print(f"  {e.facet.value:20} {e.value}{conf}\n      {e.evidence}")
        return 0

    if a.cmd == "live":  # pragma: no cover - network
        from .sources import fetch_kalshi, fetch_polymarket

        contracts = fetch_kalshi(a.limit) + fetch_polymarket(a.limit)
        print(f"fetched {len(contracts)} contracts")
        pairs = build_pairs(contracts, a.threshold)
        print(text_report(pairs))
        if a.html:
            _write_html(pairs, a.html)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
