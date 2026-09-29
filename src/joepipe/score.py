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


@lru_cache(maxsize=None)
def _word_start_regex(pattern: str) -> re.Pattern:
    """Like _pattern_regex but anchored at a word start, so stems like 'decarboniz' work
    while 'mining' doesn't match 'determining'. Used for JEL inference."""
    if len(pattern) < 5:
        return _pattern_regex(pattern)
    return re.compile(rf"\b{re.escape(pattern)}", re.IGNORECASE)


def _searchable_text(listing: Listing) -> str:
    return "\n".join(
        [
            listing.title,
            listing.department,
            "\n".join(listing.keywords),
            listing.full_text,
        ]
    )


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "nine": 9, "twelve": 12, "eighteen": 18, "twenty-four": 24, "thirty-six": 36,
}
# "24-month appointment", "two-year position", "12 months" -- but not "two-year college".
_DURATION_RE = re.compile(
    r"\b(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")[- ](month|year)s?\b"
    r"(?!\s+(?:college|institution|degree|program|old|period of))",
    re.IGNORECASE,
)
_PART_TIME_RE = re.compile(r"\bpart[- ]time\b", re.IGNORECASE)


def appointment_months(text: str) -> int:
    """Longest appointment length stated in the text, in months (0 if none stated)."""
    months = 0
    for num, unit in _DURATION_RE.findall(text):
        n = int(num) if num.isdigit() else _NUMBER_WORDS[num.lower()]
        months = max(months, n * 12 if unit.lower() == "year" else n)
    if re.search(r"\bmulti-?year\b", text, re.IGNORECASE):
        months = max(months, 24)
    return months


def _real_jel_codes(listing: Listing) -> list[str]:
    """JOE uses code "00" (Default: Any Field) when the poster picked no JEL codes."""
    return [jc.code for jc in listing.jel_classes if jc.code.strip() not in ("", "00")]


def _section_points(listing: Listing, text: str, cfg: Config) -> tuple[int, str]:
    scoring = cfg.scoring
    pts = scoring.sections.get(listing.section, 0)
    long_term = scoring.long_term_temporary
    if (
        listing.section in long_term.sections
        and appointment_months(text) >= long_term.min_months
        and not _PART_TIME_RE.search(text)
    ):
        return long_term.sections[listing.section], f"section:{listing.section} (>={long_term.min_months}mo full-time)"
    return pts, f"section:{listing.section}"


def is_excluded(listing: Listing, cfg: Config) -> bool:
    """True if the institution matches scoring.exclude_employers (hard filter, not a penalty)."""
    return any(_pattern_regex(p).search(listing.institution) for p in cfg.scoring.exclude_employers)


def score(listing: Listing, cfg: Config) -> ScoreResult:
    scoring = cfg.scoring
    text = _searchable_text(listing)
    total = 0
    reasons: list[str] = []
    fields_fired: list[str] = []

    pts, label = _section_points(listing, text, cfg)
    total += pts
    if pts:
        reasons.append(f"{label} {pts:+d}")

    # No JEL codes on the listing -> infer the codes a poster would have tagged from the
    # description, and let them fire fields exactly like real codes (marked "jel~").
    jel_codes = [(code, "jel") for code in _real_jel_codes(listing)]
    if not jel_codes:
        jel_codes = [
            (code, "jel~")
            for code, kws in scoring.jel_inference.items()
            if any(_word_start_regex(kw).search(text) for kw in kws)
        ]

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
            for code, tag in jel_codes:
                if code.startswith(prefix) and f"{tag}={code}" not in signals:
                    signals.append(f"{tag}={code}")
        for kw in field_cfg.keywords:
            regex = _word_start_regex(kw) if kw in scoring.word_start_keywords else _pattern_regex(kw)
            if regex.search(text):
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
