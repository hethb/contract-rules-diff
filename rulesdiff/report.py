"""Text and HTML reports for a set of matched pairs."""

from __future__ import annotations

import html
from collections import Counter

from .model import PairReport, Severity

_COLORS = {
    Severity.CRITICAL: "#b3261e",
    Severity.MATERIAL: "#b26a00",
    Severity.MINOR: "#5a6472",
    Severity.INFO: "#5a6472",
}


def text_report(pairs: list[PairReport]) -> str:
    lines: list[str] = []
    counts: Counter[Severity] = Counter()
    for p in pairs:
        counts[p.worst] += 1
    lines.append(
        f"{len(pairs)} matched pairs: {counts[Severity.CRITICAL]} with critical differences, "
        f"{counts[Severity.MATERIAL]} material, {len(pairs) - counts[Severity.CRITICAL] - counts[Severity.MATERIAL]} equivalent for practical purposes.\n"
    )
    for p in pairs:
        lines.append(f"{p.left.contract.title}")
        lines.append(
            f"  {p.left.contract.ref}  vs  {p.right.contract.ref}   (title match {p.match_score:.2f})"
        )
        if not p.differences:
            lines.append("  no differences found in the extracted facets")
        for d in p.differences:
            lines.append(f"  {d.describe()}")
        lines.append("")
    return "\n".join(lines)


def html_report(pairs: list[PairReport], title: str = "Cross-venue settlement rule differences") -> str:
    counts: Counter[Severity] = Counter(p.worst for p in pairs)
    cards = []
    for p in pairs:
        rows = []
        for d in p.differences:
            evidence = ""
            if d.left_evidence or d.right_evidence:
                lv = html.escape(d.left_evidence or "-")
                rv = html.escape(d.right_evidence or "-")
                lvenue = html.escape(p.left.contract.venue.value)
                rvenue = html.escape(p.right.contract.venue.value)
                evidence = (
                    "<details><summary>evidence</summary>"
                    f"<p><b>{lvenue}:</b> {lv}</p><p><b>{rvenue}:</b> {rv}</p></details>"
                )
            rows.append(
                f"""<tr class="sev-{d.severity.value}">
<td><span class="pill" style="background:{_COLORS[d.severity]}">{d.severity.value}</span></td>
<td class="facet">{html.escape(d.facet.value.replace("_", " "))}</td>
<td>{html.escape(d.left or "not stated")}</td>
<td>{html.escape(d.right or "not stated")}</td>
<td class="why">{html.escape(d.consequence)}{evidence}</td></tr>"""
            )
        body = (
            f"<table><tr><th></th><th>Facet</th><th>{html.escape(p.left.contract.venue.value)}</th>"
            f"<th>{html.escape(p.right.contract.venue.value)}</th><th>Why it matters</th></tr>{''.join(rows)}</table>"
            if rows
            else "<p class='muted'>No differences found in the extracted facets.</p>"
        )
        cards.append(
            f"""<section class="card">
<h2><span class="pill" style="background:{_COLORS[p.worst]}">{p.worst.value}</span> {html.escape(p.left.contract.title)}</h2>
<p class="muted small">{html.escape(p.left.contract.ref)} &nbsp;vs&nbsp; {html.escape(p.right.contract.ref)} &nbsp;&middot;&nbsp; title match {p.match_score:.2f}</p>
{body}
</section>"""
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{margin:0;background:#f6f7f9;color:#15181d;font:14px/1.5 -apple-system,"Segoe UI",Inter,sans-serif}}
.wrap{{max-width:1080px;margin:0 auto;padding:28px}}
h1{{font-size:22px;margin:0 0 4px}}
h2{{font-size:15px;margin:0 0 4px;display:flex;gap:8px;align-items:center}}
.muted{{color:#646b76}}.small{{font-size:12px}}
.card{{background:#fff;border:1px solid #e3e6eb;border-radius:12px;padding:16px 18px;margin-bottom:14px}}
.kpis{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:16px 0}}
.kpi{{background:#fff;border:1px solid #e3e6eb;border-radius:12px;padding:14px 16px}}
.kpi b{{display:block;font-size:24px}}.kpi span{{color:#646b76;font-size:12px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
td,th{{text-align:left;padding:7px 8px;border-top:1px solid #eef0f3;vertical-align:top}}
th{{color:#646b76;font-size:12px;border-top:none;font-weight:600}}
.facet{{font-weight:600}}
.pill{{color:#fff;border-radius:999px;padding:1px 8px;font-size:11px;text-transform:uppercase;letter-spacing:.04em}}
.why{{color:#3b414b}}
details{{margin-top:4px}}summary{{cursor:pointer;color:#646b76;font-size:12px}}
details p{{margin:4px 0;color:#646b76;font-size:12px}}
</style></head><body><div class="wrap">
<h1>{html.escape(title)}</h1>
<p class="muted">Matched contracts across venues, with every extracted settlement facet that differs and what it can do to a payout.</p>
<div class="kpis">
<div class="kpi"><b>{len(pairs)}</b><span>matched pairs</span></div>
<div class="kpi"><b>{counts[Severity.CRITICAL]}</b><span>pairs that can settle to opposite outcomes</span></div>
<div class="kpi"><b>{counts[Severity.MATERIAL]}</b><span>pairs with material but less direct differences</span></div>
</div>
{"".join(cards)}
</div></body></html>"""
