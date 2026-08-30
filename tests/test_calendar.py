from datetime import date, timedelta

import pytest

from joepipe import calendar_sync
from joepipe.config import CalendarConfig
from joepipe.models import Listing, ScoreResult, ScoredListing

TODAY = date(2026, 8, 29)
FUTURE_DEADLINE = (TODAY + timedelta(days=30)).isoformat()


class FakeExec:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeEvents:
    def __init__(self, store):
        self.store = store
        self._next_id = 1

    def list(self, calendarId, privateExtendedProperty=None, showDeleted=None,
              singleEvents=None, pageToken=None, maxResults=None):
        items = list(self.store.values())
        if privateExtendedProperty:
            key, val = privateExtendedProperty.split("=", 1)
            items = [e for e in items if e.get("extendedProperties", {}).get("private", {}).get(key) == val]
        return FakeExec({"items": items})

    def insert(self, calendarId, body):
        event_id = f"evt{self._next_id}"
        self._next_id += 1
        event = dict(body)
        event["id"] = event_id
        self.store[event_id] = event
        return FakeExec(dict(event))

    def patch(self, calendarId, eventId, body):
        self.store[eventId].update(body)
        return FakeExec(dict(self.store[eventId]))

    def delete(self, calendarId, eventId):
        self.store.pop(eventId, None)
        return FakeExec({})


class FakeService:
    def __init__(self):
        self._store: dict = {}
        self._events = FakeEvents(self._store)

    def events(self):
        return self._events


@pytest.fixture
def cal_cfg():
    return CalendarConfig(reminder_days=[14, 3], event_prefix="[JOE]", create_for_marked_rows=True)


def make_scored(jp_id, score_total=10, title="Economist", institution="The Brattle Group", deadline=FUTURE_DEADLINE):
    listing = Listing(
        jp_id=jp_id,
        issue="2026-02",
        section="Full-Time Nonacademic",
        title=title,
        institution=institution,
        division="",
        department="",
        salary_range="",
        deadline=deadline,
        full_text="",
        keywords=[],
        locations=[],
        jel_classes=[],
    )
    return ScoredListing(listing=listing, score=ScoreResult(total=score_total, fields=["environmental"], reasons=["x +5"]))


def test_insert_once(cal_cfg):
    service = FakeService()
    scored = make_scored("1")
    counts = calendar_sync.sync_calendar(service, "cal1", [scored], {}, cal_cfg, min_score_to_calendar=6, today=TODAY)
    assert counts.created == 1
    assert counts.updated == 0
    assert len(service._store) == 1
    event = next(iter(service._store.values()))
    assert event["extendedProperties"]["private"]["joe_id"] == scored.listing.joe_id
    assert event["extendedProperties"]["private"]["source"] == "joepipe"


def test_skip_when_identical(cal_cfg):
    service = FakeService()
    scored = make_scored("2")
    calendar_sync.sync_calendar(service, "cal1", [scored], {}, cal_cfg, min_score_to_calendar=6, today=TODAY)
    counts = calendar_sync.sync_calendar(service, "cal1", [scored], {}, cal_cfg, min_score_to_calendar=6, today=TODAY)
    assert counts.created == 0
    assert counts.updated == 0
    assert len(service._store) == 1


def test_patch_on_change(cal_cfg):
    service = FakeService()
    scored_v1 = make_scored("3", score_total=8)
    calendar_sync.sync_calendar(service, "cal1", [scored_v1], {}, cal_cfg, min_score_to_calendar=6, today=TODAY)

    scored_v2 = make_scored("3", score_total=15)  # same joe_id, different score -> description changes
    counts = calendar_sync.sync_calendar(service, "cal1", [scored_v2], {}, cal_cfg, min_score_to_calendar=6, today=TODAY)

    assert counts.created == 0
    assert counts.updated == 1
    assert len(service._store) == 1
    event = next(iter(service._store.values()))
    assert "Score: 15" in event["description"]


def test_delete_on_reject(cal_cfg):
    service = FakeService()
    scored = make_scored("4")
    calendar_sync.sync_calendar(service, "cal1", [scored], {}, cal_cfg, min_score_to_calendar=6, today=TODAY)
    assert len(service._store) == 1

    sheet_state = {scored.listing.joe_id: {"status": "Rejected", "track": False}}
    counts = calendar_sync.sync_calendar(service, "cal1", [scored], sheet_state, cal_cfg, min_score_to_calendar=6, today=TODAY)

    assert counts.deleted == 1
    assert len(service._store) == 0


def test_never_dedupes_by_summary(cal_cfg):
    service = FakeService()
    scored_a = make_scored("5", title="Economist", institution="Same Corp")
    scored_b = make_scored("6", title="Economist", institution="Same Corp")  # identical summary, different joe_id
    calendar_sync.sync_calendar(service, "cal1", [scored_a, scored_b], {}, cal_cfg, min_score_to_calendar=6, today=TODAY)
    assert len(service._store) == 2


def test_skips_past_deadline(cal_cfg):
    service = FakeService()
    past = (TODAY - timedelta(days=5)).isoformat()
    scored = make_scored("7", deadline=past)
    counts = calendar_sync.sync_calendar(service, "cal1", [scored], {}, cal_cfg, min_score_to_calendar=6, today=TODAY)
    assert counts.created == 0
    assert len(service._store) == 0


def test_below_threshold_and_not_tracked_skipped(cal_cfg):
    service = FakeService()
    scored = make_scored("8", score_total=2)
    counts = calendar_sync.sync_calendar(service, "cal1", [scored], {}, cal_cfg, min_score_to_calendar=6, today=TODAY)
    assert counts.created == 0
    assert len(service._store) == 0


def test_tracked_row_below_threshold_still_creates_event(cal_cfg):
    service = FakeService()
    scored = make_scored("9", score_total=2)
    sheet_state = {scored.listing.joe_id: {"status": "", "track": True}}
    counts = calendar_sync.sync_calendar(service, "cal1", [scored], sheet_state, cal_cfg, min_score_to_calendar=6, today=TODAY)
    assert counts.created == 1


def test_assert_not_primary_refuses():
    with pytest.raises(calendar_sync.CalendarGuardError):
        calendar_sync.assert_not_primary("primary")
    calendar_sync.assert_not_primary("primary", i_know_what_im_doing=True)  # should not raise
    calendar_sync.assert_not_primary("abc123@group.calendar.google.com")  # should not raise


def test_purge_deletes_only_joepipe_events(cal_cfg):
    service = FakeService()
    scored = make_scored("10")
    calendar_sync.sync_calendar(service, "cal1", [scored], {}, cal_cfg, min_score_to_calendar=6, today=TODAY)
    # An unrelated event with no joepipe stamp.
    service._store["other1"] = {"id": "other1", "summary": "Dentist"}

    deleted = calendar_sync.purge_all(service, "cal1")

    assert deleted == 1
    assert "other1" in service._store
    assert len(service._store) == 1
