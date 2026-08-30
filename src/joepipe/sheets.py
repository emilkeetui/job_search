"""Google Sheets upsert (PLAN.md section 6).

Hard rules enforced here:
  - Never clear() the sheet, never delete rows.
  - Columns O/P/Q (Track, Status, Notes) are user-owned and must never be
    overwritten by the pipeline once a row exists.
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
    "joe_id", "First Seen", "Score", "Fields", "Why", "Section", "Institution",
    "Title", "Location", "Deadline", "Days Left", "JEL", "Link", "Contact/URL",
    "Track", "Status", "Notes",
]
# Column indices (0-based) for clarity when building Sheets API requests.
COL_JOE_ID, COL_FIRST_SEEN, COL_SCORE, COL_FIELDS, COL_WHY, COL_SECTION = range(6)
COL_INSTITUTION, COL_TITLE, COL_LOCATION, COL_DEADLINE, COL_DAYS_LEFT = 6, 7, 8, 9, 10
COL_JEL, COL_LINK, COL_CONTACT, COL_TRACK, COL_STATUS, COL_NOTES = 11, 12, 13, 14, 15, 16

STATUS_OPTIONS = ["Interested", "Applying", "Applied", "Interview", "Rejected", "Offer"]


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
        return ws, False
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=len(HEADER))
        _with_backoff(ws.update, "A1", [HEADER])
        _with_backoff(ws.freeze, rows=1)
        _apply_validations(spreadsheet, ws)
        return ws, True


def _apply_validations(spreadsheet: gspread.Spreadsheet, ws: gspread.Worksheet) -> None:
    sheet_id = ws.id
    requests = [
        {
            "setDataValidation": {
                "range": {
                    "sheetId": sheet_id, "startRowIndex": 1,
                    "startColumnIndex": COL_TRACK, "endColumnIndex": COL_TRACK + 1,
                },
                "rule": {"condition": {"type": "BOOLEAN"}, "strict": True},
            }
        },
        {
            "setDataValidation": {
                "range": {
                    "sheetId": sheet_id, "startRowIndex": 1,
                    "startColumnIndex": COL_STATUS, "endColumnIndex": COL_STATUS + 1,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [{"userEnteredValue": v} for v in STATUS_OPTIONS],
                    },
                    "strict": False,
                    "showCustomUi": True,
                },
            }
        },
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": sheet_id, "startRowIndex": 1,
                        "startColumnIndex": COL_DAYS_LEFT, "endColumnIndex": COL_DAYS_LEFT + 1,
                    }],
                    "booleanRule": {
                        "condition": {"type": "NUMBER_LESS_THAN_EQ", "values": [{"userEnteredValue": "7"}]},
                        "format": {"backgroundColor": {"red": 0.96, "green": 0.80, "blue": 0.80}},
                    },
                },
                "index": 0,
            }
        },
    ]
    _with_backoff(spreadsheet.batch_update, {"requests": requests})


def compute_days_left(deadline: str | None, today: date) -> str | int:
    if not deadline:
        return "—"  # em dash
    try:
        d = date.fromisoformat(deadline)
    except ValueError:
        return "—"
    return (d - today).days


def build_row(scored: ScoredListing, today: date, first_seen: str) -> list:
    """Columns A..N, in order. Caller supplies first_seen (owned, never recomputed)."""
    listing = scored.listing
    deadline_str = listing.deadline or "—"
    days_left = compute_days_left(listing.deadline, today)
    location = "; ".join(loc.formatted() for loc in listing.locations if loc.formatted())
    jel = ";".join(jc.code for jc in listing.jel_classes)
    link = f'=HYPERLINK("{listing.listing_url}","{listing.joe_id}")'
    contact = listing.urls[0] if listing.urls else (listing.emails[0] if listing.emails else "")
    return [
        listing.joe_id,
        first_seen,
        scored.score.total,
        ";".join(scored.score.fields),
        " | ".join(scored.score.reasons),
        listing.section,
        listing.institution,
        listing.title,
        location,
        deadline_str,
        days_left,
        jel,
        link,
        contact,
    ]


def read_sheet_state(ws: gspread.Worksheet) -> dict[str, dict]:
    """joe_id -> {row, first_seen, track, status, notes}."""
    values = _with_backoff(ws.get_all_values)
    state: dict[str, dict] = {}
    for i, row in enumerate(values[1:], start=2):
        if not row or not row[0]:
            continue
        row = row + [""] * (len(HEADER) - len(row))
        state[row[COL_JOE_ID]] = {
            "row": i,
            "first_seen": row[COL_FIRST_SEEN],
            "track": row[COL_TRACK].strip().upper() in ("TRUE", "1", "YES"),
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
    fetched_ids = {sl.listing.joe_id for sl in scored_listings}

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
            row_values = build_row(sl, today, first_seen=existing[joe_id]["first_seen"])
            # Only A and C..N -- never touch B (First Seen, set once) or O/P/Q (user-owned).
            update_data.append({"range": f"A{row_num}", "values": [[row_values[0]]]})
            update_data.append({
                "range": f"{_col_letter(COL_SCORE)}{row_num}:{_col_letter(COL_CONTACT)}{row_num}",
                "values": [row_values[2:]],
            })
            updated_count += 1
        else:
            row_values = build_row(sl, today, first_seen=today.isoformat())
            append_rows.append(row_values + ["", "", ""])  # Track/Status/Notes blank
            new_count += 1
            new_ids.append(joe_id)

    if append_rows:
        _with_backoff(ws.append_rows, append_rows, value_input_option="USER_ENTERED")

    if update_data:
        _with_backoff(ws.batch_update, update_data, value_input_option="USER_ENTERED")

    # Listings that vanished from the feed (issue rolled over): never delete,
    # just mark Days Left as expired.
    expired_updates = [
        {"range": f"{_col_letter(COL_DAYS_LEFT)}{info['row']}", "values": [["expired"]]}
        for joe_id, info in existing.items()
        if joe_id not in fetched_ids
    ]
    if expired_updates:
        _with_backoff(ws.batch_update, expired_updates, value_input_option="USER_ENTERED")

    return ws, new_count, updated_count, written_ids, new_ids
