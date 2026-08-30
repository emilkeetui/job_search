"""Fetch and parse AEA JOE listings.

See PLAN.md section 0 for the verified two-step fetch and XML schema. Do not
hand-construct the `q` export parameter, and do not scrape the listings HTML
for job data -- only for the export link and the current issue code.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

from joepipe.models import JELClass, Listing, Location

BASE = "https://www.aeaweb.org"
LISTINGS_URL = f"{BASE}/joe/listings"
LISTINGS_PATH_URL = f"{BASE}/joe/listings/"
EXPORT_URL = f"{BASE}/joe/resultset_output.php"
ARCHIVE_URL = f"{BASE}/joe/listings.php"

USER_AGENT = "Mozilla/5.0 (compatible; joe-pipeline/1.0)"

ISSUE_RE = re.compile(r'name="ListingsForm\[issue\]"\s+value="([^"]+)"')
EXPORT_LINK_RE = re.compile(r'resultset_output\.php\?mode=full_xml&q=([^"]+)')
URL_RE = re.compile(r"https?://\S+")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

SLEEP_BETWEEN_REQUESTS = 1.0


class JoeFetchError(RuntimeError):
    """Raised when the JOE site's HTML no longer matches our scrape assumptions."""


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def get_current_issue(session: requests.Session) -> str:
    """Read the current issue code from the hidden form field on /joe/listings."""
    resp = session.get(LISTINGS_URL, timeout=30)
    resp.raise_for_status()
    m = ISSUE_RE.search(resp.text)
    if not m:
        raise JoeFetchError(
            f"Could not find ListingsForm[issue] hidden field at {resp.url}. "
            "JOE's HTML may have changed."
        )
    return m.group(1)


def previous_issue(issue: str) -> str:
    """YYYY-02 -> YYYY-01; YYYY-01 -> (YYYY-1)-02."""
    year_s, num_s = issue.split("-")
    year, num = int(year_s), int(num_s)
    if num == 2:
        return f"{year}-01"
    return f"{year - 1}-02"

def issue_exists(session: requests.Session, issue: str) -> bool:
    """Cross-check a derived issue code against the archive links page."""
    resp = session.get(ARCHIVE_URL, params={"issue": issue}, timeout=30)
    if resp.status_code != 200:
        return False
    return f"issue={issue}" in resp.text or f'value="{issue}"' in resp.text


def fetch_issue_export_xml(session: requests.Session, issue: str) -> str:
    """The two-step fetch (PLAN.md section 0.1) for a single issue code."""
    params = {
        "ListingsForm[issue]": issue,
        "ListingsForm[originalIssue]": issue,
    }
    resp = session.get(LISTINGS_PATH_URL, params=params, timeout=30)
    resp.raise_for_status()
    m = EXPORT_LINK_RE.search(resp.text)
    if not m:
        raise JoeFetchError(
            f"Could not find export link in {resp.url} -- JOE's HTML may have changed."
        )
    q = m.group(1)
    time.sleep(SLEEP_BETWEEN_REQUESTS)
    export_resp = session.get(EXPORT_URL, params={"mode": "full_xml", "q": q}, timeout=60)
    export_resp.raise_for_status()
    return export_resp.text


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def parse_export_xml(xml_text: str) -> list[Listing]:
    """Parse a JOE_EXPORT XML document into Listings (PLAN.md section 0.3)."""
    root = ET.fromstring(xml_text)
    listings: list[Listing] = []
    for year_el in root.findall("year"):
        year = year_el.get("joe_year_ID", "")
        for issue_el in year_el.findall("issue"):
            issue_num = issue_el.get("joe_issue_ID", "")
            issue = f"{year}-{int(issue_num):02d}" if year and issue_num else ""
            for pos in issue_el.findall("position"):
                listings.append(_parse_position(pos, issue))
    return listings


def _parse_position(pos: ET.Element, issue: str) -> Listing:
    jp_id = pos.get("jp_id", "")
    deadline_raw = _text(pos.find("jp_application_deadline"))
    deadline = deadline_raw.split(" ")[0] if deadline_raw else None

    keywords_raw = _text(pos.find("jp_keywords"))
    keywords = [k.strip() for k in keywords_raw.split("\n") if k.strip()]

    full_text = _text(pos.find("jp_full_text"))

    locations = []
    locations_el = pos.find("locations")
    if locations_el is not None:
        for loc_el in locations_el.findall("location"):
            locations.append(
                Location(
                    city=_text(loc_el.find("city")),
                    state=_text(loc_el.find("state")),
                    country=_text(loc_el.find("country")),
                )
            )

    jel_classes = []
    jel_el = pos.find("JEL_Classifications")
    if jel_el is not None:
        for jc in jel_el.findall("jel_class"):
            code = _text(jc.find("jc_code"))
            desc = _text(jc.find("jc_description"))
            if code:
                jel_classes.append(JELClass(code=code, description=desc))

    urls = URL_RE.findall(full_text)
    emails = EMAIL_RE.findall(full_text)

    return Listing(
        jp_id=jp_id,
        issue=issue,
        section=_text(pos.find("jp_section")),
        title=_text(pos.find("jp_title")),
        institution=_text(pos.find("jp_institution")),
        division=_text(pos.find("jp_division")),
        department=_text(pos.find("jp_department")),
        salary_range=_text(pos.find("jp_salary_range")),
        deadline=deadline,
        full_text=full_text,
        keywords=keywords,
        locations=locations,
        jel_classes=jel_classes,
        urls=urls,
        emails=emails,
    )


def fetch_all(cache_dir: str | Path, include_previous_issue: bool = True) -> list[Listing]:
    """Fetch current (+ previous) issue, cache raw XML, parse, dedupe on joe_id."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    session = make_session()

    today = time.strftime("%Y%m%d")
    all_listings: dict[str, Listing] = {}

    try:
        current_issue = get_current_issue(session)
        issues_to_fetch = [current_issue]
        if include_previous_issue:
            prev = previous_issue(current_issue)
            time.sleep(SLEEP_BETWEEN_REQUESTS)
            if issue_exists(session, prev):
                issues_to_fetch.append(prev)

        for issue in issues_to_fetch:
            xml_text = fetch_issue_export_xml(session, issue)
            cache_path = cache_dir / f"joe_{issue}_{today}.xml"
            cache_path.write_text(xml_text, encoding="utf-8")
            for listing in parse_export_xml(xml_text):
                all_listings[listing.joe_id] = listing
            time.sleep(SLEEP_BETWEEN_REQUESTS)

    except (requests.RequestException, JoeFetchError) as exc:
        cached = _freshest_cache(cache_dir, max_age_hours=24)
        if not cached:
            raise
        print(f"WARNING: live fetch failed ({exc}); using cached XML from {cached}")
        for path in cached:
            for listing in parse_export_xml(path.read_text(encoding="utf-8")):
                all_listings[listing.joe_id] = listing

    return list(all_listings.values())


def _freshest_cache(cache_dir: Path, max_age_hours: int) -> list[Path]:
    """Return the most recent cached XML file per issue, if fresh enough."""
    if not cache_dir.exists():
        return []
    cutoff = time.time() - max_age_hours * 3600
    by_issue: dict[str, Path] = {}
    for path in sorted(cache_dir.glob("joe_*.xml")):
        if path.stat().st_mtime < cutoff:
            continue
        # filename: joe_{issue}_{YYYYMMDD}.xml -- issue may itself contain a hyphen
        stem = path.stem  # joe_2026-02_20260829
        parts = stem.split("_")
        if len(parts) < 3:
            continue
        issue = "_".join(parts[1:-1])
        by_issue[issue] = path  # sorted filenames -> latest date wins
    return list(by_issue.values())
