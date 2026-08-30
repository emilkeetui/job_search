import pytest

from joepipe.config import load_config
from joepipe.models import JELClass, Listing, Location
from joepipe.score import score


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
