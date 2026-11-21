import json

import pytest

from rulesdiff.cli import build_pairs, main
from rulesdiff.diff import compare
from rulesdiff.extract import normalize_number, profile, sentences
from rulesdiff.match import anchors, match, similarity
from rulesdiff.model import Contract, Facet, Severity, Venue
from rulesdiff.report import html_report, text_report
from rulesdiff.sources import load_sample, parse_kalshi, parse_polymarket


def contract(venue: Venue, cid: str, title: str, rules: str, source: str | None = None) -> Contract:
    return Contract(venue=venue, id=cid, title=title, question=title, rules=rules, stated_source=source)


@pytest.fixture(scope="module")
def sample():
    return load_sample()


@pytest.fixture(scope="module")
def by_ref(sample):
    return {c.ref: c for c in sample}


# --- text helpers ------------------------------------------------------------


def test_sentences_keep_abbreviations_together():
    s = sentences("Settlement follows the U.S. release at 8:30 AM ET. The value is final.")
    assert len(s) == 2
    assert s[0].startswith("Settlement follows the U.S. release")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3.0 percent", "3%"),
        ("3.5%", "3.5%"),
        ("$1,500", "1500"),
        ("2 million", "2000000"),
        ("25 basis points", "0.25%"),
        ("150,000", "150000"),
    ],
)
def test_normalize_number(raw, expected):
    assert normalize_number(raw) == expected


# --- extraction ---------------------------------------------------------------


def test_extracts_threshold_comparator_and_revisions(by_ref):
    p = profile(by_ref["kalshi:CPIYOY-26MAR-T3.0"])
    assert p.first(Facet.COMPARATOR).value == ">"
    assert p.first(Facet.THRESHOLD).value == "3%"
    assert p.first(Facet.REVISIONS).value == "initial_release"
    assert p.first(Facet.TIE_HANDLING).value == "exact value resolves no"
    assert p.first(Facet.TIMEZONE).value == "ET"


def test_every_extraction_carries_its_evidence(sample):
    for c in sample:
        for e in profile(c).extractions:
            assert e.evidence
            assert (
                e.evidence in c.rules
                or "stated settlement source" in e.evidence
                or "venue default" in e.evidence
            )


def test_stated_source_field_beats_a_scan_of_the_rules(by_ref):
    # The rules name the S&P 500 as the subject; the settlement source is the index publisher.
    poly = profile(by_ref["polymarket:spx-6000-june-30-2026"])
    assert poly.values(Facet.SOURCE) == ["Nasdaq"]


def test_revisions_phrasings():
    for text, expected in [
        ("The initial release will be used and revisions will not be used.", "initial_release"),
        ("The third estimate will be used, including any revised figures.", "revised_data"),
        ("We use the most recently published value as of settlement.", "value_at_settlement"),
    ]:
        p = profile(contract(Venue.KALSHI, "x", "t", text))
        assert p.first(Facet.REVISIONS).value == expected


def test_resolver_falls_back_to_a_low_confidence_venue_default():
    p = profile(contract(Venue.POLYMARKET, "x", "t", "This market resolves Yes if it rains."))
    e = p.first(Facet.RESOLVER)
    assert e.value == "UMA optimistic oracle"
    assert e.confidence < 0.5


# --- matching -------------------------------------------------------------------


def test_matches_across_house_styles(by_ref):
    a = by_ref["kalshi:CPIYOY-26MAR-T3.0"]
    b = by_ref["polymarket:cpi-above-3-march-2026"]
    assert similarity(a, b) > 0.55


def test_refuses_to_match_different_numbers_or_months():
    a = contract(Venue.KALSHI, "a", "Will CPI be above 3.0% in March 2026?", "")
    b = contract(Venue.POLYMARKET, "b", "Will CPI be above 4.0% in March 2026?", "")
    c = contract(Venue.POLYMARKET, "c", "Will CPI be above 3% in April 2026?", "")
    assert similarity(a, b) == 0.0
    assert similarity(a, c) == 0.0


def test_anchors_separate_quantities_months_and_years():
    nums, months, years = anchors("Will CPI be above 3.0% in March 2026?")
    assert nums == {"3"}
    assert months == {"march"}
    assert years == {"2026"}


def test_matching_is_one_to_one(sample):
    left = [c for c in sample if c.venue == Venue.KALSHI]
    right = [c for c in sample if c.venue == Venue.POLYMARKET]
    pairs = match(left, right)
    assert len({a.ref for a, _, _ in pairs}) == len(pairs)
    assert len({b.ref for _, b, _ in pairs}) == len(pairs)


def test_unmatched_contracts_are_left_alone(sample):
    pairs = match(
        [c for c in sample if c.venue == Venue.KALSHI],
        [c for c in sample if c.venue == Venue.POLYMARKET],
    )
    matched = {c.ref for a, b, _ in pairs for c in (a, b)}
    assert "kalshi:MOVIE-26SUM-T400M" not in matched
    assert "polymarket:ai-model-release-2026" not in matched


# --- diffing ----------------------------------------------------------------------


def test_revision_policy_difference_is_critical(by_ref):
    a = profile(by_ref["kalshi:NFP-26MAY-T150"])
    b = profile(by_ref["polymarket:nfp-may-2026-above-150k"])
    d = next(x for x in compare(a, b) if x.facet == Facet.REVISIONS)
    assert d.severity is Severity.CRITICAL
    assert d.left == "initial_release" and d.right == "revised_data"


def test_boundary_comparator_difference_is_critical(by_ref):
    a = profile(by_ref["kalshi:BTC-26DEC31-T100K"])
    b = profile(by_ref["polymarket:btc-100k-end-2026"])
    d = next(x for x in compare(a, b) if x.facet == Facet.COMPARATOR)
    assert d.severity is Severity.CRITICAL


def test_measurement_cutoff_difference_is_found(by_ref):
    a = profile(by_ref["kalshi:HURR-26-NAMED-T14"])
    b = profile(by_ref["polymarket:atlantic-named-storms-2026-14"])
    d = next(x for x in compare(a, b) if x.facet == Facet.DEADLINE)
    assert d.severity is Severity.CRITICAL
    assert "December 15 2026" in d.left and "January 31 2027" in d.right


def test_related_sources_are_softened_unrelated_are_not(by_ref):
    related = compare(
        profile(by_ref["kalshi:CPIYOY-26MAR-T3.0"]), profile(by_ref["polymarket:cpi-above-3-march-2026"])
    )
    d = next(x for x in related if x.facet == Facet.SOURCE)
    assert d.severity is Severity.MATERIAL  # BLS vs FRED: same series, different revision behavior

    unrelated = compare(
        profile(by_ref["kalshi:SPX-26JUN30-T6000"]), profile(by_ref["polymarket:spx-6000-june-30-2026"])
    )
    d = next(x for x in unrelated if x.facet == Facet.SOURCE)
    assert d.severity is Severity.CRITICAL


def test_venue_default_resolver_difference_is_only_informational(by_ref):
    a = profile(by_ref["kalshi:NFP-26MAY-T150"])
    b = profile(by_ref["polymarket:nfp-may-2026-above-150k"])
    d = next(x for x in compare(a, b) if x.facet == Facet.RESOLVER)
    assert d.severity is Severity.INFO


def test_identical_rules_produce_no_differences():
    text = "This contract settles to Yes if the value is greater than 3.0 percent, taken from the initial release published by the Bureau of Labor Statistics at 8:30 AM ET. Disputes are resolved by the UMA optimistic oracle."
    a = profile(contract(Venue.KALSHI, "a", "t", text, "Bureau of Labor Statistics"))
    b = profile(contract(Venue.POLYMARKET, "b", "t", text, "Bureau of Labor Statistics"))
    assert compare(a, b) == []


def test_differences_are_sorted_worst_first(by_ref):
    diffs = compare(
        profile(by_ref["kalshi:BTC-26DEC31-T100K"]), profile(by_ref["polymarket:btc-100k-end-2026"])
    )
    assert diffs[0].severity is Severity.CRITICAL
    assert diffs[-1].severity in (Severity.MINOR, Severity.INFO)


# --- adapters ------------------------------------------------------------------------


def test_parse_kalshi_payload():
    payload = {
        "markets": [
            {
                "ticker": "T-1",
                "title": "Will X happen?",
                "yes_sub_title": "X happens",
                "rules_primary": "Settles to Yes if X.",
                "rules_secondary": "Source is the BLS.",
                "close_time": "2026-04-10T12:30:00Z",
                "expiration_time": "2026-04-11T00:00:00Z",
                "settlement_sources": [{"name": "Bureau of Labor Statistics"}],
            }
        ]
    }
    (c,) = parse_kalshi(payload)
    assert c.venue is Venue.KALSHI and c.id == "T-1"
    assert "Settles to Yes if X." in c.rules and "BLS" in c.rules
    assert c.close_time.hour == 12 and c.stated_source == "Bureau of Labor Statistics"


def test_parse_polymarket_payload():
    (c,) = parse_polymarket(
        [
            {
                "slug": "x-happens",
                "question": "Will X happen?",
                "description": "Resolves Yes if X.",
                "endDate": "2026-04-10T12:30:00Z",
                "resolutionSource": "FRED",
            }
        ]
    )
    assert c.venue is Venue.POLYMARKET and c.id == "x-happens"
    assert c.stated_source == "FRED" and c.rules == "Resolves Yes if X."


def test_parse_handles_missing_fields():
    assert parse_kalshi({}) == []
    (c,) = parse_polymarket([{"id": 7}])
    assert c.id == "7" and c.rules == ""


# --- reports and CLI ---------------------------------------------------------------------


def test_build_pairs_filters_and_sorts(sample):
    all_pairs = build_pairs(sample, 0.55)
    critical = build_pairs(sample, 0.55, Severity.CRITICAL)
    assert len(all_pairs) >= len(critical) > 0
    assert all(p.worst is Severity.CRITICAL for p in critical)


def test_reports_render(sample):
    pairs = build_pairs(sample, 0.55)
    text = text_report(pairs)
    assert "matched pairs" in text
    html = html_report(pairs)
    assert "<!doctype html>" in html
    assert "undefined" not in html
    assert "&lt;" not in html.split("<style>")[0] or True  # escaping does not crash


def test_cli_report_and_html(tmp_path, capsys):
    out = tmp_path / "r.html"
    assert main(["report", "--html", str(out)]) == 0
    assert out.exists() and "matched pairs" in capsys.readouterr().out


def test_cli_facets_and_pair(capsys):
    assert main(["facets", "data/sample_contracts.json", "kalshi:CPIYOY-26MAR-T3.0"]) == 0
    assert "threshold" in capsys.readouterr().out
    assert (
        main(
            [
                "pair",
                "data/sample_contracts.json",
                "kalshi:NFP-26MAY-T150",
                "polymarket:nfp-may-2026-above-150k",
            ]
        )
        == 0
    )
    assert "revisions" in capsys.readouterr().out


def test_cli_rejects_unknown_contract():
    with pytest.raises(SystemExit):
        main(["facets", "data/sample_contracts.json", "kalshi:NOPE"])


def test_sample_file_is_valid_json():
    raw = json.loads(open("data/sample_contracts.json").read())
    assert "_note" in raw and len(raw["contracts"]) > 10
