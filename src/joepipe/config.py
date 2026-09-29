"""Load config/config.yaml + .env into typed, validated settings.

Every scoring knob lives in config.yaml (PLAN.md section 4) -- nothing here
should hardcode a magic constant that the user might want to tune.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


class GoogleConfig(BaseModel):
    spreadsheet_id: str
    worksheet_name: str
    calendar_id: str

    @field_validator("calendar_id")
    @classmethod
    def _not_primary(cls, v: str) -> str:
        # The hard "never primary" guardrail is enforced at call sites too
        # (so --i-know-what-im-doing can override); this just normalizes.
        return v.strip()


class FetchConfig(BaseModel):
    include_previous_issue: bool = True
    cache_dir: str = "data/raw"


class TargetEmployers(BaseModel):
    points: int
    names: list[str] = Field(default_factory=list)


class FieldConfig(BaseModel):
    points: int
    jel: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class NegativeRule(BaseModel):
    points: int
    patterns: list[str] = Field(default_factory=list)


class LocationRule(BaseModel):
    points: int
    patterns: list[str] = Field(default_factory=list)


class LocationsConfig(BaseModel):
    preferred: LocationRule
    penalize: LocationRule


class LongTermTemporary(BaseModel):
    min_months: int = 12
    sections: dict[str, int] = Field(default_factory=dict)


class ScoringConfig(BaseModel):
    min_score_to_sheet: int
    min_score_to_calendar: int
    sections: dict[str, int] = Field(default_factory=dict)
    long_term_temporary: LongTermTemporary = Field(default_factory=LongTermTemporary)
    jel_inference: dict[str, list[str]] = Field(default_factory=dict)
    target_employers: TargetEmployers
    exclude_employers: list[str] = Field(default_factory=list)
    fields: dict[str, FieldConfig] = Field(default_factory=dict)
    word_start_keywords: list[str] = Field(default_factory=list)
    max_field_points: int
    negative: list[NegativeRule] = Field(default_factory=list)
    locations: LocationsConfig


class CalendarConfig(BaseModel):
    reminder_days: list[int] = Field(default_factory=list)
    event_prefix: str = "[JOE]"
    create_for_marked_rows: bool = True


class Config(BaseModel):
    google: GoogleConfig
    fetch: FetchConfig
    scoring: ScoringConfig
    calendar: CalendarConfig


def load_config(config_path: str | Path = "config/config.yaml", env_path: str | Path = ".env") -> Config:
    env_path = Path(env_path)
    if env_path.exists():
        load_dotenv(env_path)

    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. Copy config/config.yaml.example if present, "
            "or restore config/config.yaml."
        )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    # Secrets / IDs may be overridden via .env if the repo is public (section 3.2).
    env_spreadsheet_id = os.environ.get("JOEPIPE_SPREADSHEET_ID")
    if env_spreadsheet_id:
        raw.setdefault("google", {})["spreadsheet_id"] = env_spreadsheet_id
    env_calendar_id = os.environ.get("JOEPIPE_CALENDAR_ID")
    if env_calendar_id:
        raw.setdefault("google", {})["calendar_id"] = env_calendar_id

    return Config.model_validate(raw)
