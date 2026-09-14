"""Google Sheets upsert (PLAN.md section 6).

This writes into the user's existing hand-maintained job tracker tab, so the
column layout below mirrors what's already there rather than a schema the
pipeline invented. Only two columns (Title, joe_id) are new -- appended at the
end so nothing existing shifts.

Hard rules enforced here:
  - Never clear() the sheet, never delete rows.
  - User-owned columns (Apply by, Applied, Status, the two letter-tracking
    columns, Letters Submitted, Industry, Notes) are never overwritten once a
    row exists -- they're set blank on first insert and left alone after that.
  - Only ever touch the configured worksheet, never other tabs/spreadsheets.
  - Batch writes; never write cell by cell.
"""

from __future__ import annotations

import time
from datetime import date

import gspread
from google.oauth2.service_account import Credentials

from joepipe.models import ScoredListing

HEADER = [
    "Deadline", "Apply by", "Applied", "Status", "Website", "Organization Name",
    "Letter Writer Type of Upload", "Type of letter required", "Letters Submitted",
    "Location", "Field", "Industry", "Notes", "Title", "joe_id",
]
# Column indices (0-based).
(
    COL_DEADLINE, COL_APPLY_BY, COL_APPLIED, COL_STATUS, COL_WEBSITE, COL_ORG,
    COL_LETTER_UPLOAD, COL_LETTER_TYPE, COL_LETTERS_SUBMITTED,
    COL_LOCATION, COL_FIELD, COL_INDUSTRY, COL_NOTES, COL_TITLE, COL_JOE_ID,
) = range(15)

# Filled/refreshed by the pipeline on every run.
PIPELINE_OWNED = [COL_DEADLINE, COL_WEBSITE, COL_ORG, COL_LOCATION, COL_FIELD, COL_TITLE]
# Set blank on first insert, then only ever edited by the user by hand.
USER_OWNED = [
    COL_APPLY_BY, COL_APPLIED, COL_STATUS, COL_LETTER_UPLOAD, COL_LETTER_TYPE,
    COL_LETTERS_SUBMITTED, COL_INDUSTRY, COL_NOTES,
]


def _col_letter(idx0: int) -> str:
    return chr(ord("A") + idx0)


def _with_backoff(fn, *args, max_retries: int = 5, **kwargs):
    delay = 1.0
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 429 and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise


def get_client(creds: Credentials) -> gspread.Client:
    return gspread.authorize(creds)


def open_spreadsheet(gc: gspread.Client, spreadsheet_id: str) -> gspread.Spreadsheet:
    return gc.open_by_key(spreadsheet_id)


def get_or_create_worksheet(spreadsheet: gspread.Spreadsheet, worksheet_name: str) -> tuple[gspread.Worksheet, bool]:
    try:
        ws = spreadsheet.worksheet(worksheet_name)
        _ensure_header_extended(ws)
        return ws, False
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=len(HEADER))
        _with_backoff(ws.update, "A1", [HEADER])
        _with_backoff(ws.freeze, rows=1)
        return ws, True


def _ensure_header_extended(ws: gspread.Worksheet) -> None:
    """Add the Title/joe_id header cells if this pre-existing tab doesn't have them yet.
    Never touches any other header cell -- the rest of the tab's layout is the user's."""
    row1 = _with_backoff(ws.row_values, 1)
    missing = [
        (idx, name) for idx, name in enumerate(HEADER)
        if idx >= len(row1) or not row1[idx]
    ]
    if not missing:
        return
    updates = [{"range": f"{_col_letter(idx)}1", "values": [[name]]} for idx, name in missing]
    _with_backoff(ws.batch_update, updates, value_input_option="USER_ENTERED")


def compute_website(listing) -> str:
    if listing.urls:
        return listing.urls[0]
    return listing.listing_url


def build_row(scored: ScoredListing) -> list:
    """Full row for a brand-new listing, columns A..O. User-owned columns are
    left blank except Notes, which gets a one-line score summary for context
    on insert only -- it is never touched again after that."""
    listing = scored.listing
    location = "; ".join(loc.formatted() for loc in listing.locations if loc.formatted())
    notes = f"[joepipe] score {scored.score.total} | {' | '.join(scored.score.reasons)}"
    row = [""] * len(HEADER)
    row[COL_DEADLINE] = listing.deadline or ""
    row[COL_WEBSITE] = compute_website(listing)
    row[COL_ORG] = listing.institution
    row[COL_LOCATION] = location
    row[COL_FIELD] = ",".join(scored.score.fields)
    row[COL_TITLE] = listing.title
    row[COL_NOTES] = notes
    row[COL_JOE_ID] = listing.joe_id
    return row


def build_pipeline_owned_values(scored: ScoredListing) -> dict[int, str]:
    """col_idx -> value, for refreshing an existing row without touching user-owned cells."""
    listing = scored.listing
    location = "; ".join(loc.formatted() for loc in listing.locations if loc.formatted())
    return {
        COL_DEADLINE: listing.deadline or "",
        COL_WEBSITE: compute_website(listing),
        COL_ORG: listing.institution,
        COL_LOCATION: location,
        COL_FIELD: ",".join(scored.score.fields),
        COL_TITLE: listing.title,
    }


def read_sheet_state(ws: gspread.Worksheet) -> dict[str, dict]:
    """joe_id -> {row, status, notes}. Rows without a joe_id (the user's pre-existing
    manual entries) are skipped -- they have nothing for the pipeline to key on."""
    values = _with_backoff(ws.get_all_values)
    state: dict[str, dict] = {}
    for i, row in enumerate(values[1:], start=2):
        if len(row) <= COL_JOE_ID or not row[COL_JOE_ID]:
            continue
        row = row + [""] * (len(HEADER) - len(row))
        state[row[COL_JOE_ID]] = {
            "row": i,
            "status": row[COL_STATUS].strip(),
            "notes": row[COL_NOTES],
        }
    return state


def upsert_listings(
    spreadsheet: gspread.Spreadsheet,
    worksheet_name: str,
    scored_listings: list[ScoredListing],
    today: date,
) -> tuple[gspread.Worksheet, int, int, list[str], list[str]]:
    """Upsert scored listings. Returns (worksheet, new_count, updated_count, joe_ids_written, new_joe_ids)."""
    ws, _created = get_or_create_worksheet(spreadsheet, worksheet_name)
    existing = read_sheet_state(ws)

    append_rows: list[list] = []
    update_data: list[dict] = []
    written_ids: list[str] = []
    new_ids: list[str] = []
    new_count = 0
    updated_count = 0

    for sl in scored_listings:
        joe_id = sl.listing.joe_id
        written_ids.append(joe_id)
        if joe_id in existing:
            row_num = existing[joe_id]["row"]
            for col_idx, value in build_pipeline_owned_values(sl).items():
                update_data.append({"range": f"{_col_letter(col_idx)}{row_num}", "values": [[value]]})
            updated_count += 1
        else:
            append_rows.append(build_row(sl))
            new_count += 1
            new_ids.append(joe_id)

    if append_rows:
        _with_backoff(ws.append_rows, append_rows, value_input_option="USER_ENTERED")

    if update_data:
        _with_backoff(ws.batch_update, update_data, value_input_option="USER_ENTERED")

    return ws, new_count, updated_count, written_ids, new_ids
