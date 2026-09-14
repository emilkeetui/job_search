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

    def row_values(self, row_num):
        if row_num > len(self.rows):
            return []
        return list(self.rows[row_num - 1])

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


def make_scored(joe_id_suffix="1", score_total=10, title="Economist", deadline="2026-12-01"):
    listing = Listing(
        jp_id=joe_id_suffix,
        issue="2026-02",
        section="Full-Time Nonacademic",
        title=title,
        institution="The Brattle Group",
        division="",
        department="",
        salary_range="",
        deadline=deadline,
        full_text="",
        keywords=[],
        locations=[],
        jel_classes=[JELClass(code="Q51", description="x")],
    )
    return ScoredListing(listing=listing, score=ScoreResult(total=score_total, fields=["environmental"], reasons=["x +5"]))


def test_new_row_appended_with_blank_user_columns():
    ws = FakeWorksheet(sheets.HEADER)
    spreadsheet = FakeSpreadsheet({"Sheet1": ws})
    scored = make_scored()

    _ws, new_count, updated_count, written_ids, new_ids = sheets.upsert_listings(
        spreadsheet, "Sheet1", [scored], date(2026, 8, 29)
    )

    assert new_count == 1
    assert updated_count == 0
    assert written_ids == [scored.listing.joe_id]
    assert new_ids == [scored.listing.joe_id]

    data_row = ws.rows[1]
    assert data_row[sheets.COL_JOE_ID] == scored.listing.joe_id
    assert data_row[sheets.COL_DEADLINE] == "2026-12-01"
    assert data_row[sheets.COL_ORG] == "The Brattle Group"
    assert data_row[sheets.COL_TITLE] == "Economist"
    assert data_row[sheets.COL_FIELD] == "environmental"
    # User-owned columns stay blank on insert.
    assert data_row[sheets.COL_APPLY_BY] == ""
    assert data_row[sheets.COL_APPLIED] == ""
    assert data_row[sheets.COL_STATUS] == ""
    assert data_row[sheets.COL_INDUSTRY] == ""
    # Notes gets a one-line score summary, but only at insert time.
    assert "score 10" in data_row[sheets.COL_NOTES]


def test_updating_existing_row_preserves_user_owned_columns():
    ws = FakeWorksheet(sheets.HEADER)
    scored_v1 = make_scored(score_total=5, title="Old Title", deadline="2026-11-01")
    joe_id = scored_v1.listing.joe_id
    # Pre-populate a row as if a prior run wrote it, then the user filled in their own columns.
    row = [""] * len(sheets.HEADER)
    row[sheets.COL_JOE_ID] = joe_id
    row[sheets.COL_DEADLINE] = "2026-11-01"
    row[sheets.COL_TITLE] = "Old Title"
    row[sheets.COL_APPLY_BY] = "ASAP"
    row[sheets.COL_APPLIED] = "9/10/2026"
    row[sheets.COL_STATUS] = "Applied"
    row[sheets.COL_INDUSTRY] = "Consulting"
    row[sheets.COL_NOTES] = "great fit, talked to recruiter"
    ws.rows.append(row)

    spreadsheet = FakeSpreadsheet({"Sheet1": ws})
    scored_v2 = make_scored(score_total=12, title="New Title", deadline="2026-12-15")

    _ws, new_count, updated_count, written_ids, new_ids = sheets.upsert_listings(
        spreadsheet, "Sheet1", [scored_v2], date(2026, 8, 29)
    )

    assert new_count == 0
    assert updated_count == 1
    assert new_ids == []

    data_row = ws.rows[1]
    # Pipeline-owned columns refresh.
    assert data_row[sheets.COL_TITLE] == "New Title"
    assert data_row[sheets.COL_DEADLINE] == "2026-12-15"
    # User-owned columns must survive untouched.
    assert data_row[sheets.COL_APPLY_BY] == "ASAP"
    assert data_row[sheets.COL_APPLIED] == "9/10/2026"
    assert data_row[sheets.COL_STATUS] == "Applied"
    assert data_row[sheets.COL_INDUSTRY] == "Consulting"
    assert data_row[sheets.COL_NOTES] == "great fit, talked to recruiter"


def test_preexisting_manual_rows_without_joe_id_are_ignored():
    ws = FakeWorksheet(sheets.HEADER)
    manual_row = [""] * len(sheets.HEADER)
    manual_row[sheets.COL_DEADLINE] = "02/28/2026"
    manual_row[sheets.COL_ORG] = "Stanford"
    manual_row[sheets.COL_STATUS] = "incomplete application (need lori letter in JOE)"
    ws.rows.append(manual_row)

    spreadsheet = FakeSpreadsheet({"Sheet1": ws})
    scored = make_scored()

    _ws, new_count, updated_count, written_ids, new_ids = sheets.upsert_listings(
        spreadsheet, "Sheet1", [scored], date(2026, 8, 29)
    )

    assert new_count == 1
    assert updated_count == 0
    # The manual row is untouched and still present.
    assert ws.rows[1][sheets.COL_ORG] == "Stanford"
    assert ws.rows[1][sheets.COL_STATUS] == "incomplete application (need lori letter in JOE)"
    assert len(ws.rows) == 3  # header + manual row + newly appended row


def test_header_extended_with_title_and_joe_id_without_disturbing_existing_header():
    existing_header = sheets.HEADER[:13]  # the real sheet's pre-existing 13 columns only
    ws = FakeWorksheet(existing_header)
    spreadsheet = FakeSpreadsheet({"Sheet1": ws})

    sheets.get_or_create_worksheet(spreadsheet, "Sheet1")

    header_row = ws.rows[0]
    assert header_row[: len(existing_header)] == existing_header
    assert header_row[sheets.COL_TITLE] == "Title"
    assert header_row[sheets.COL_JOE_ID] == "joe_id"
