import re
from datetime import date

import gspread
import pytest

from joepipe import sheets
from joepipe.models import JELClass, Listing, ScoreResult, ScoredListing


def _col_to_idx(col: str) -> int:
    idx = 0
    for ch in col:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _parse_range(range_str: str):
    m = re.match(r"^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$", range_str)
    col1, row1, col2, _row2 = m.groups()
    row1 = int(row1)
    c1 = _col_to_idx(col1)
    c2 = _col_to_idx(col2) if col2 else c1
    return row1, c1, c2


class FakeWorksheet:
    def __init__(self, header):
        self.rows = [list(header)]
        self.id = 111

    def get_all_values(self):
        return [list(row) for row in self.rows]

    def update(self, range_str, values):
        row1, c1, _c2 = _parse_range(range_str)
        self._ensure_row(row1)
        for i, v in enumerate(values[0]):
            self._set(row1, c1 + i, v)

    def freeze(self, rows=1):
        pass

    def append_rows(self, rows, value_input_option=None):
        for r in rows:
            self.rows.append([str(v) for v in r])

    def batch_update(self, data, value_input_option=None):
        for item in data:
            row1, c1, _c2 = _parse_range(item["range"])
            self._ensure_row(row1)
            for i, v in enumerate(item["values"][0]):
                self._set(row1, c1 + i, v)

    def _ensure_row(self, row_num):
        while len(self.rows) < row_num:
            self.rows.append([""] * len(sheets.HEADER))

    def _set(self, row_num, col_idx, value):
        row = self.rows[row_num - 1]
        while len(row) <= col_idx:
            row.append("")
        row[col_idx] = str(value)


class FakeSpreadsheet:
    def __init__(self, worksheets):
        self._worksheets = worksheets
        self.url = "https://fake.example/sheet"

    def worksheet(self, name):
        if name not in self._worksheets:
            raise gspread.exceptions.WorksheetNotFound(name)
        return self._worksheets[name]


def make_scored(joe_id_suffix="1", score_total=10, title="Economist"):
    listing = Listing(
        jp_id=joe_id_suffix,
        issue="2026-02",
        section="Full-Time Nonacademic",
        title=title,
        institution="The Brattle Group",
        division="",
        department="",
        salary_range="",
        deadline="2026-12-01",
        full_text="",
        keywords=[],
        locations=[],
        jel_classes=[JELClass(code="Q51", description="x")],
    )
    return ScoredListing(listing=listing, score=ScoreResult(total=score_total, fields=["environmental"], reasons=["x +5"]))


def test_new_row_appended_with_blank_user_columns():
    ws = FakeWorksheet(sheets.HEADER)
    spreadsheet = FakeSpreadsheet({"JOE Listings": ws})
    scored = make_scored()

    _ws, new_count, updated_count, written_ids, new_ids = sheets.upsert_listings(
        spreadsheet, "JOE Listings", [scored], date(2026, 8, 29)
    )

    assert new_count == 1
    assert updated_count == 0
    assert written_ids == [scored.listing.joe_id]
    assert new_ids == [scored.listing.joe_id]

    data_row = ws.rows[1]
    assert data_row[sheets.COL_JOE_ID] == scored.listing.joe_id
    assert data_row[sheets.COL_FIRST_SEEN] == "2026-08-29"
    assert data_row[sheets.COL_TRACK] == ""
    assert data_row[sheets.COL_STATUS] == ""
    assert data_row[sheets.COL_NOTES] == ""


def test_updating_existing_row_preserves_user_owned_columns():
    ws = FakeWorksheet(sheets.HEADER)
    scored_v1 = make_scored(score_total=5, title="Old Title")
    joe_id = scored_v1.listing.joe_id
    # Pre-populate a row as if a prior run wrote it, then the user edited O/P/Q.
    row = [""] * len(sheets.HEADER)
    row[sheets.COL_JOE_ID] = joe_id
    row[sheets.COL_FIRST_SEEN] = "2026-01-01"
    row[sheets.COL_SCORE] = "5"
    row[sheets.COL_TITLE] = "Old Title"
    row[sheets.COL_TRACK] = "TRUE"
    row[sheets.COL_STATUS] = "Applied"
    row[sheets.COL_NOTES] = "great fit, talked to recruiter"
    ws.rows.append(row)

    spreadsheet = FakeSpreadsheet({"JOE Listings": ws})
    scored_v2 = make_scored(score_total=12, title="New Title")

    _ws, new_count, updated_count, written_ids, new_ids = sheets.upsert_listings(
        spreadsheet, "JOE Listings", [scored_v2], date(2026, 8, 29)
    )

    assert new_count == 0
    assert updated_count == 1
    assert new_ids == []

    data_row = ws.rows[1]
    assert data_row[sheets.COL_SCORE] == "12"
    assert data_row[sheets.COL_TITLE] == "New Title"
    # User-owned + pipeline-frozen columns must survive untouched.
    assert data_row[sheets.COL_FIRST_SEEN] == "2026-01-01"
    assert data_row[sheets.COL_TRACK] == "TRUE"
    assert data_row[sheets.COL_STATUS] == "Applied"
    assert data_row[sheets.COL_NOTES] == "great fit, talked to recruiter"


def test_vanished_listing_marked_expired_not_deleted():
    ws = FakeWorksheet(sheets.HEADER)
    scored = make_scored()
    joe_id = scored.listing.joe_id
    row = [""] * len(sheets.HEADER)
    row[sheets.COL_JOE_ID] = joe_id
    row[sheets.COL_FIRST_SEEN] = "2026-01-01"
    ws.rows.append(row)

    spreadsheet = FakeSpreadsheet({"JOE Listings": ws})

    # This run's fetch no longer contains `joe_id` at all.
    sheets.upsert_listings(spreadsheet, "JOE Listings", [], date(2026, 8, 29))

    assert len(ws.rows) == 2  # row not deleted
    assert ws.rows[1][sheets.COL_DAYS_LEFT] == "expired"
