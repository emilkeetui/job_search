"""Deterministic relevance scoring. Pure functions, no I/O, no LLM calls.

All tunable knobs live in config/config.yaml (PLAN.md section 4/5) -- this
module must not hardcode point values, employer names, or keyword lists.
"""

from __future__ import annotations

import re
from functools import lru_cache

from joepipe.config import Config
from joepipe.models import Listing, ScoreResult


@lru_cache(maxsize=None)
def _pattern_regex(pattern: str) -> re.Pattern:
    """Word-boundary match for short tokens (avoids 'R' matching inside 'Research'),
    plain case-insensitive substring match otherwise. Compiled once per pattern."""
    if len(pattern) < 5:
        return re.compile(rf"\b{re.escape(pattern)}\b", re.IGNORECASE)
    return re.compile(re.escape(pattern), re.IGNORECASE)


def _searchable_text(listing: Listing) -> str:
    return "\n".join(
        [
            listing.title,
            listing.department,
            "\n".join(listing.keywords),
            listing.full_text,
        ]
    )


def score(listing: Listing, cfg: Config) -> ScoreResult:
    scoring = cfg.scoring
    text = _searchable_text(listing)
    total = 0
    reasons: list[str] = []
    fields_fired: list[str] = []

    if listing.section in scoring.sections:
        pts = scoring.sections[listing.section]
        total += pts
        if pts:
            reasons.append(f"section:{listing.section} {pts:+d}")

    for name in scoring.target_employers.names:
        if _pattern_regex(name).search(listing.institution):
            pts = scoring.target_employers.points
            total += pts
            reasons.append(f"employer:{name} {pts:+d}")
            break

    field_total = 0
    for field_name, field_cfg in scoring.fields.items():
        signals: list[str] = []
        for prefix in field_cfg.jel:
            for jc in listing.jel_classes:
                if jc.code.startswith(prefix) and f"jel={jc.code}" not in signals:
                    signals.append(f"jel={jc.code}")
        for kw in field_cfg.keywords:
            if _pattern_regex(kw).search(text):
                signals.append(f"kw={kw}")
        if signals:
            pts = min(field_cfg.points + max(0, len(signals) - 1), field_cfg.points + 3)
            field_total += pts
            fields_fired.append(field_name)
            reasons.append(f"field:{field_name} {','.join(signals)} {pts:+d}")

    total += min(field_total, scoring.max_field_points)

    for rule in scoring.negative:
        matched = [p for p in rule.patterns if _pattern_regex(p).search(text)]
        if matched:
            total += rule.points
            reasons.append(f"negative:{','.join(matched)} {rule.points:+d}")

    loc_text = "; ".join(loc.formatted() for loc in listing.locations)
    for pattern in scoring.locations.preferred.patterns:
        if _pattern_regex(pattern).search(loc_text):
            pts = scoring.locations.preferred.points
            total += pts
            reasons.append(f"location:{pattern} {pts:+d}")
            break
    for pattern in scoring.locations.penalize.patterns:
        if _pattern_regex(pattern).search(loc_text):
            pts = scoring.locations.penalize.points
            total += pts
            reasons.append(f"location_penalty:{pattern} {pts:+d}")
            break

    total = max(total, 0)
    return ScoreResult(total=total, fields=fields_fired, reasons=reasons)
