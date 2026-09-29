"""Orchestration for `joepipe run` (PLAN.md section 8).

Acceptance bar: running `joepipe run` twice in a row must produce
`New 0 - Events created 0` and must not disturb any user-edited cell.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import gspread

from joepipe import calendar_sync, joe, sheets
from joepipe.auth import AuthError, load_credentials, service_account_email
from joepipe.config import Config
from joepipe.models import ScoredListing
from joepipe.score import is_excluded
from joepipe.score import score as score_listing

RUNS_LOG = Path("data/runs.jsonl")


@dataclass
class RunResult:
    fetched: int = 0
    kept: int = 0
    new: int = 0
    updated: int = 0
    events_created: int = 0
    events_updated: int = 0
    events_deleted: int = 0
    sheet_url: str = ""
    scored_kept: list[ScoredListing] = field(default_factory=list)
    new_ids: list[str] = field(default_factory=list)
    exit_code: int = 0
    messages: list[str] = field(default_factory=list)
    wrote: bool = False


def fetch_and_score(cfg: Config) -> list[ScoredListing]:
    """Scored listings, minus any from scoring.exclude_employers."""
    scored, _excluded_ids = fetch_score_and_exclude(cfg)
    return scored


def fetch_score_and_exclude(cfg: Config) -> tuple[list[ScoredListing], list[str]]:
    """(scored non-excluded listings, joe_ids of excluded listings)."""
    listings = joe.fetch_all(cfg.fetch.cache_dir, cfg.fetch.include_previous_issue)
    scored: list[ScoredListing] = []
    excluded_ids: list[str] = []
    for lst in listings:
        if is_excluded(lst, cfg):
            excluded_ids.append(lst.joe_id)
        else:
            scored.append(ScoredListing(listing=lst, score=score_listing(lst, cfg)))
    return scored, excluded_ids


def run_pipeline(cfg: Config, dry_run: bool, apply_: bool, i_know_what_im_doing: bool = False) -> RunResult:
    calendar_sync.assert_not_primary(cfg.google.calendar_id, i_know_what_im_doing)

    scored, excluded_ids = fetch_score_and_exclude(cfg)
    kept = [sl for sl in scored if sl.score.total >= cfg.scoring.min_score_to_sheet]
    kept.sort(key=lambda sl: sl.score.total, reverse=True)

    result = RunResult(fetched=len(scored) + len(excluded_ids), kept=len(kept), scored_kept=kept)

    if dry_run:
        return result

    if not cfg.google.spreadsheet_id.strip():
        result.exit_code = 2
        result.messages.append(
            "spreadsheet_id is not set. Copy .env.example to .env and fill in "
            "JOEPIPE_SPREADSHEET_ID (see README.md)."
        )
        return result

    try:
        creds = load_credentials()
        gc = sheets.get_client(creds)
        spreadsheet = sheets.open_spreadsheet(gc, cfg.google.spreadsheet_id)
    except AuthError as exc:
        result.exit_code = 2
        result.messages.append(str(exc))
        return result
    except gspread.exceptions.APIError as exc:
        result.exit_code = 2
        result.messages.append(
            f"Cannot open spreadsheet {cfg.google.spreadsheet_id}: {exc}\n"
            "Make sure it is shared with the service account as Editor (see README.md)."
        )
        return result

    result.sheet_url = spreadsheet.url
    ws, _created = sheets.get_or_create_worksheet(spreadsheet, cfg.google.worksheet_name)
    existing_state = sheets.read_sheet_state(ws)
    first_ever_write = len(existing_state) == 0

    if first_ever_write and not apply_:
        result.messages.append(
            "First run against this sheet: no rows exist yet. Re-run with --apply to write "
            f"{len(kept)} listing(s). (Or `joepipe preview` to look before writing.)"
        )
        return result

    today = date.today()
    ws, new_count, updated_count, written_ids, new_ids = sheets.upsert_listings(
        spreadsheet, cfg.google.worksheet_name, kept, today
    )
    result.new = new_count
    result.updated = updated_count
    result.new_ids = new_ids
    result.wrote = True

    sheet_state = sheets.read_sheet_state(ws)

    sync_counts = None
    try:
        service = calendar_sync.get_calendar_service(creds)
        sync_counts = calendar_sync.sync_calendar(
            service,
            cfg.google.calendar_id,
            kept,
            sheet_state,
            cfg.calendar,
            cfg.scoring.min_score_to_calendar,
            today,
        )
        excluded_deleted = calendar_sync.delete_events_for(service, cfg.google.calendar_id, excluded_ids)
        sync_counts.deleted += len(excluded_deleted)
        sync_counts.joe_ids_deleted.extend(excluded_deleted)
        result.events_created = sync_counts.created
        result.events_updated = sync_counts.updated
        result.events_deleted = sync_counts.deleted
    except Exception as exc:  # noqa: BLE001 -- surfaced to the user, not swallowed
        result.exit_code = 1
        result.messages.append(
            f"Sheet upsert succeeded (new={new_count}, updated={updated_count}); "
            f"calendar sync failed: {exc}"
        )

    _log_run(result, written_ids, sync_counts)
    return result


def _log_run(result: RunResult, written_ids: list[str], sync_counts) -> None:
    RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fetched": result.fetched,
        "kept": result.kept,
        "new": result.new,
        "updated": result.updated,
        "sheet_rows_written": written_ids,
        "events_created": sync_counts.joe_ids_created if sync_counts else [],
        "events_updated": sync_counts.joe_ids_updated if sync_counts else [],
        "events_deleted": sync_counts.joe_ids_deleted if sync_counts else [],
    }
    with RUNS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


@dataclass
class DoctorCheck:
    name: str
    ok: bool
    detail: str


def run_doctor(cfg: Config) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []

    checks.append(DoctorCheck("config.yaml loads", True, "ok"))

    if cfg.google.calendar_id.strip().lower() == "primary":
        checks.append(DoctorCheck("calendar_id is not 'primary'", False, "calendar_id is set to 'primary'"))
    elif not cfg.google.calendar_id.strip():
        checks.append(DoctorCheck("calendar_id is not 'primary'", False, "calendar_id is empty in config.yaml"))
    else:
        checks.append(DoctorCheck("calendar_id is not 'primary'", True, cfg.google.calendar_id))

    try:
        creds = load_credentials()
        checks.append(DoctorCheck("service account key found", True, service_account_email(creds)))
    except AuthError as exc:
        checks.append(DoctorCheck("service account key found", False, str(exc)))
        return checks

    if not cfg.google.spreadsheet_id.strip():
        checks.append(
            DoctorCheck(
                "spreadsheet access", False,
                "spreadsheet_id is empty. Copy .env.example to .env and fill in "
                "JOEPIPE_SPREADSHEET_ID.",
            )
        )
    else:
        try:
            gc = sheets.get_client(creds)
            spreadsheet = sheets.open_spreadsheet(gc, cfg.google.spreadsheet_id)
            checks.append(DoctorCheck("spreadsheet access", True, spreadsheet.url))
        except Exception as exc:  # noqa: BLE001
            checks.append(
                DoctorCheck(
                    "spreadsheet access", False,
                    f"{exc}. Share the sheet with {service_account_email(creds)} as Editor.",
                )
            )

    if cfg.google.calendar_id.strip():
        try:
            service = calendar_sync.get_calendar_service(creds)
            service.events().list(calendarId=cfg.google.calendar_id, maxResults=1).execute()
            checks.append(DoctorCheck("calendar access", True, cfg.google.calendar_id))
        except Exception as exc:  # noqa: BLE001
            checks.append(
                DoctorCheck(
                    "calendar access", False,
                    f"{exc}. Share the calendar with {service_account_email(creds)} "
                    "(Make changes to events).",
                )
            )
    else:
        checks.append(DoctorCheck("calendar access", False, "calendar_id not set in config.yaml"))

    return checks
