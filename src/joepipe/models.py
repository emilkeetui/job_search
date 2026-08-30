"""Data models for JOE listings and scoring results."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class JELClass:
    code: str
    description: str


@dataclass(frozen=True)
class Location:
    city: str
    state: str
    country: str

    def formatted(self) -> str:
        parts = [p for p in (self.city, self.state, self.country) if p]
        return ", ".join(parts)


@dataclass
class Listing:
    """A single JOE position, parsed from the XML export."""

    jp_id: str
    issue: str  # e.g. "2026-02"
    section: str
    title: str
    institution: str
    division: str
    department: str
    salary_range: str
    deadline: str | None  # "YYYY-MM-DD" or None
    full_text: str
    keywords: list[str]
    locations: list[Location]
    jel_classes: list[JELClass]
    urls: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)

    @property
    def joe_id(self) -> str:
        return f"{self.issue}_{self.jp_id}"

    @property
    def listing_url(self) -> str:
        return f"https://www.aeaweb.org/joe/listing.php?JOE_ID={self.joe_id}"


@dataclass
class ScoreResult:
    total: int
    fields: list[str]
    reasons: list[str]


@dataclass
class ScoredListing:
    listing: Listing
    score: ScoreResult
