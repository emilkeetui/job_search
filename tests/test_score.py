import pytest

from joepipe.config import load_config
from joepipe.models import JELClass, Listing, Location
from joepipe.score import appointment_months, is_excluded, score


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def make_listing(**overrides) -> Listing:
    defaults = dict(
        jp_id="1",
        issue="2026-02",
        section="",
        title="",
        institution="",
        division="",
        department="",
        salary_range="",
        deadline="2026-12-01",
        full_text="",
        keywords=[],
        locations=[],
        jel_classes=[],
    )
    defaults.update(overrides)
    return Listing(**defaults)


def test_brattle_water_quality_scores_high(cfg):
    listing = make_listing(
        section="Full-Time Nonacademic",
        title="Associate",
        institution="The Brattle Group",
        full_text="We seek an economist with expertise in water quality and environmental economics.",
        keywords=["Environmental Economics"],
        locations=[Location(city="Boston", state="Massachusetts", country="UNITED STATES")],
        jel_classes=[JELClass(code="Q51", description="Environmental economics")],
    )
    result = score(listing, cfg)
    assert result.total >= 15
    assert "environmental" in result.fields


@pytest.mark.parametrize(
    "institution",
    ["Federal Reserve Bank of Atlanta", "Council of Economic Advisers, The White House",
     "White House Council of Economic Advisers", "US Census Bureau", "U.S. Department of Justice",
     "Federal Communications Commission", "Office of Management and Budget"],
)
def test_federal_employers_excluded(cfg, institution):
    assert is_excluded(make_listing(institution=institution), cfg)


@pytest.mark.parametrize(
    "institution",
    ["Committee for a Responsible Federal Budget", "National Bureau of Economic Research",
     "The Treasury New Zealand", "Brown University", "Institute for Defense Analyses"],
)
def test_non_federal_employers_not_excluded(cfg, institution):
    assert not is_excluded(make_listing(institution=institution), cfg)


def test_macro_tt_posting_scores_near_zero(cfg):
    listing = make_listing(
        section="US: Full-Time Academic (Permanent, Tenure Track or Tenured)",
        title="Assistant Professor of Macroeconomics",
        institution="Generic State University",
        full_text="Research interests in monetary policy and asset pricing.",
        jel_classes=[JELClass(code="E52", description="Monetary policy")],
    )
    result = score(listing, cfg)
    assert result.total <= 1
    assert result.fields == []


def test_field_points_capped_at_points_plus_3(cfg):
    field_cfg = cfg.scoring.fields["environmental"]
    listing = make_listing(
        full_text=(
            "environmental economics, energy economics, climate, water quality, "
            "drinking water, air quality, pollution, coal, mining, emissions"
        ),
    )
    result = score(listing, cfg)
    assert result.total == field_cfg.points + 3


def test_max_field_points_cap_enforced(cfg):
    # Fire every configured field hard enough to blow past max_field_points if uncapped.
    all_keywords = []
    for field_cfg in cfg.scoring.fields.values():
        all_keywords.extend(field_cfg.keywords)
    listing = make_listing(full_text=", ".join(all_keywords))
    result = score(listing, cfg)
    assert result.total == cfg.scoring.max_field_points
    assert set(result.fields) == set(cfg.scoring.fields.keys())


def test_negative_rule_fires_once_per_rule(cfg):
    listing = make_listing(
        section="US: Full-Time Academic (Permanent, Tenure Track or Tenured)",
        full_text="macroeconomics, monetary policy, asset pricing, corporate finance",
    )
    result = score(listing, cfg)
    negative_reasons = [r for r in result.reasons if r.startswith("negative:")]
    assert len(negative_reasons) == 1


def test_score_never_negative(cfg):
    listing = make_listing(
        section="US: Other Academic (Visiting or Temporary)",
        full_text="postdoc adjunct lecturer macroeconomics monetary policy",
    )
    result = score(listing, cfg)
    assert result.total >= 0


def test_short_token_word_boundary(cfg):
    # "R" should not match inside "Research" -- data_science keyword list includes "R".
    listing = make_listing(full_text="Research position in political science, no stats required.")
    result = score(listing, cfg)
    assert "data_science" not in result.fields


def test_long_term_visiting_scored_like_permanent(cfg):
    base = dict(section="US: Other Academic (Visiting or Temporary)", title="Postdoc")
    short = score(make_listing(**base, full_text="A one-semester visiting position."), cfg)
    long_ = score(make_listing(**base, full_text="A 24-month appointment starting in fall."), cfg)
    part = score(make_listing(**base, full_text="A two-year part-time appointment."), cfg)
    assert long_.total - short.total == 1  # 2 -> 3, the US full-time academic weight
    assert part.total == short.total


@pytest.mark.parametrize(
    "text,months",
    [("a 24-month appointment", 24), ("two-year postdoc", 24), ("12 months", 12),
     ("a one-semester visit", 0), ("teaching at a two-year college", 0), ("multi-year fellowship", 24)],
)
def test_appointment_months(text, months):
    assert appointment_months(text) == months


def test_jel_inferred_from_text_only_when_no_jel(cfg):
    text = "Postdoc on critical minerals and industrial policy. Excellent data management skills."
    no_jel = score(make_listing(full_text=text, jel_classes=[JELClass(code="00", description="Default")]), cfg)
    assert {"environmental", "consulting", "data_science"} <= set(no_jel.fields)
    assert any("jel~=L5" in r for r in no_jel.reasons)
    tagged = score(make_listing(full_text=text, jel_classes=[JELClass(code="Q32", description="x")]), cfg)
    assert not any("jel~" in r for r in tagged.reasons)
    assert "consulting" not in tagged.fields


def test_jel_inference_ignores_mid_word_matches(cfg):
    text = "Applications are reviewed after determining eligibility."
    result = score(make_listing(full_text=text), cfg)
    assert not any("jel~=Q3" in r for r in result.reasons)


def test_mining_keyword_matches_word_start_only(cfg):
    assert "environmental" not in score(make_listing(full_text="Key determining factors."), cfg).fields
    assert "environmental" in score(make_listing(full_text="Mining and extraction policy."), cfg).fields


def test_valuation_still_matches_evaluation(cfg):
    assert "consulting" in score(make_listing(full_text="Program evaluation experience."), cfg).fields
