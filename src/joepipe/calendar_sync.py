"""Google Calendar deadline sync (PLAN.md section 7).

Hard rules enforced here:
  - Idempotency key is extendedProperties.private.joe_id -- never search or
    dedupe by summary text.
  - Every event this pipeline creates is stamped private.source = "joepipe".
  - Only events carrying that stamp may ever be deleted by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from joepipe.config import CalendarConfig
from joepipe.models import ScoredListing

SOURCE_TAG = "joepipe"
MAX_REMINDER_MINUTES = 40320  # Calendar API cap: 28 days


class CalendarGuardError(RuntimeError):
    """Raised when a guardrail (e.g. calendar_id == 'primary') is violated."""


@dataclass
class SyncCounts:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    joe_ids_created: list[str] | None = None
    joe_ids_updated: list[str] | None = None
    joe_ids_deleted: list[str] | None = None

    def __post_init__(self):
        self.joe_ids_created = self.joe_ids_created or []
        self.joe_ids_updated = self.joe_ids_updated or []
        self.joe_ids_deleted = self.joe_ids_deleted or []


def get_calendar_service(creds: Credentials):
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def assert_not_primary(calendar_id: str, i_know_what_im_doing: bool = False) -> None:
    if calendar_id.strip().lower() == "primary" and not i_know_what_im_doing:
        raise CalendarGuardError(
            "calendar_id is 'primary'. This pipeline refuses to write to your primary "
            "calendar. Create a dedicated secondary calendar (PLAN.md section 3) or pass "
            "--i-know-what-im-doing if you really mean it."
        )


def _effective_reminder_minutes(reminder_days: list[int], days_left: int) -> list[int]:
    minutes = []
    for d in reminder_days:
        if d > days_left:
            continue  # would already be in the past relative to the deadline
        clamped_days = min(d, 28)
        minutes.append(clamped_days * 24 * 60)
    return sorted(set(m for m in minutes if m <= MAX_REMINDER_MINUTES))


def build_event_body(scored: ScoredListing, status: str, cal_cfg: CalendarConfig, today: date) -> dict | None:
    listing = scored.listing
    if not listing.deadline:
        return None
    try:
        deadline_date = date.fromisoformat(listing.deadline)
    except ValueError:
        return None
    if deadline_date < today:
        return None  # past deadlines: skip creation entirely

    days_left = (deadline_date - today).days
    reminder_minutes = _effective_reminder_minutes(cal_cfg.reminder_days, days_left)

    location = "; ".join(loc.formatted() for loc in listing.locations if loc.formatted())
    jel = ", ".join(jc.code for jc in listing.jel_classes)
    description = "\n".join(
        [
            f"Score: {scored.score.total}",
            f"Why: {' | '.join(scored.score.reasons)}",
            f"Location: {location or 'n/a'}",
            f"JEL: {jel or 'n/a'}",
            f"Link: {listing.listing_url}",
            f"Status: {status or '(blank)'}",
        ]
    )

    reminders = (
        {"useDefault": False, "overrides": [{"method": "popup", "minutes": m} for m in reminder_minutes]}
        if reminder_minutes
        else {"useDefault": True}
    )

    return {
        "summary": f"{cal_cfg.event_prefix} {listing.institution} — {listing.title}",
        "description": description,
        "start": {"date": deadline_date.isoformat()},
        "end": {"date": (deadline_date + timedelta(days=1)).isoformat()},
        "reminders": reminders,
        "extendedProperties": {"private": {"joe_id": listing.joe_id, "source": SOURCE_TAG}},
    }


def _find_event(service, calendar_id: str, joe_id: str) -> dict | None:
    resp = (
        service.events()
        .list(
            calendarId=calendar_id,
            privateExtendedProperty=f"joe_id={joe_id}",
            showDeleted=False,
            singleEvents=True,
        )
        .execute()
    )
    items = resp.get("items", [])
    for item in items:
        if item.get("extendedProperties", {}).get("private", {}).get("source") == SOURCE_TAG:
            return item
    return None


def _normalize_reminders(reminders: dict | None) -> dict:
    """The API drops empty override lists and returns overrides in its own order. It also
    stores useDefault=True as useDefault=False on this calendar (the service account has no
    default reminders), so "default" and "no overrides" compare equal."""
    overrides = (reminders or {}).get("overrides") or []
    return {"overrides": sorted(overrides, key=lambda o: (o.get("minutes", 0), o.get("method", "")))}


def _event_matches(existing: dict, desired: dict) -> bool:
    return (
        existing.get("summary") == desired["summary"]
        and existing.get("description") == desired["description"]
        and existing.get("start", {}).get("date") == desired["start"]["date"]
        and existing.get("end", {}).get("date") == desired["end"]["date"]
        and _normalize_reminders(existing.get("reminders")) == _normalize_reminders(desired["reminders"])
    )


def _should_delete(scored: ScoredListing, status: str, track: bool, min_score_to_calendar: int) -> bool:
    if status == "Rejected":
        return True
    if not track and scored.score.total < min_score_to_calendar:
        return True
    return False


def sync_calendar(
    service,
    calendar_id: str,
    scored_listings: list[ScoredListing],
    sheet_state: dict[str, dict],
    cal_cfg: CalendarConfig,
    min_score_to_calendar: int,
    today: date,
) -> SyncCounts:
    counts = SyncCounts()

    for scored in scored_listings:
        joe_id = scored.listing.joe_id
        info = sheet_state.get(joe_id, {})
        status = info.get("status", "")
        track = info.get("track", False)

        should_have_event = track or scored.score.total >= min_score_to_calendar

        existing = _find_event(service, calendar_id, joe_id)

        if _should_delete(scored, status, track, min_score_to_calendar) and existing:
            try:
                service.events().delete(calendarId=calendar_id, eventId=existing["id"]).execute()
            except HttpError as exc:
                if exc.resp.status not in (404, 410):
                    raise
            counts.deleted += 1
            counts.joe_ids_deleted.append(joe_id)
            continue

        if not should_have_event:
            continue

        desired = build_event_body(scored, status, cal_cfg, today)
        if desired is None:
            continue  # no deadline, or deadline already past

        if existing is None:
            service.events().insert(calendarId=calendar_id, body=desired).execute()
            counts.created += 1
            counts.joe_ids_created.append(joe_id)
        elif not _event_matches(existing, desired):
            if desired["reminders"].get("useDefault") and existing.get("reminders", {}).get("overrides"):
                # patch() merges nested objects, so old overrides would survive alongside
                # useDefault=True (API 400). Clear them in a separate patch first.
                service.events().patch(
                    calendarId=calendar_id, eventId=existing["id"],
                    body={"reminders": {"useDefault": False, "overrides": []}},
                ).execute()
            service.events().patch(calendarId=calendar_id, eventId=existing["id"], body=desired).execute()
            counts.updated += 1
            counts.joe_ids_updated.append(joe_id)

    return counts


def delete_events_for(service, calendar_id: str, joe_ids: list[str]) -> list[str]:
    """Delete the joepipe-stamped event (if any) for each joe_id. Returns joe_ids deleted."""
    deleted: list[str] = []
    for joe_id in joe_ids:
        existing = _find_event(service, calendar_id, joe_id)
        if existing is None:
            continue
        try:
            service.events().delete(calendarId=calendar_id, eventId=existing["id"]).execute()
        except HttpError as exc:
            if exc.resp.status not in (404, 410):
                raise
        deleted.append(joe_id)
    return deleted


def list_joepipe_events(service, calendar_id: str) -> list[dict]:
    """All events this pipeline created, regardless of current listing set."""
    events: list[dict] = []
    page_token = None
    while True:
        resp = (
            service.events()
            .list(
                calendarId=calendar_id,
                privateExtendedProperty=f"source={SOURCE_TAG}",
                showDeleted=False,
                singleEvents=True,
                pageToken=page_token,
            )
            .execute()
        )
        events.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return events


def purge_all(service, calendar_id: str) -> int:
    """Delete exactly the events carrying source=joepipe. The undo button."""
    events = list_joepipe_events(service, calendar_id)
    deleted = 0
    for event in events:
        try:
            service.events().delete(calendarId=calendar_id, eventId=event["id"]).execute()
            deleted += 1
        except HttpError as exc:
            if exc.resp.status not in (404, 410):
                raise
    return deleted
