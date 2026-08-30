from pathlib import Path

import pytest
import responses

from joepipe import joe

FIXTURE = Path(__file__).parent / "fixtures" / "joe_2026-02.xml"


@pytest.fixture(scope="module")
def xml_text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def listings(xml_text):
    return joe.parse_export_xml(xml_text)


def test_parses_all_positions(listings):
    assert len(listings) == 135


def test_composite_id_construction(listings):
    for lst in listings:
        assert lst.joe_id == f"{lst.issue}_{lst.jp_id}"
        assert lst.issue == "2026-02"
        assert lst.listing_url == f"https://www.aeaweb.org/joe/listing.php?JOE_ID={lst.joe_id}"


def test_empty_deadline_handled(listings):
    empty_deadline = [lst for lst in listings if lst.deadline is None]
    assert len(empty_deadline) == 2


def test_multi_location_parsing(listings):
    multi = [lst for lst in listings if len(lst.locations) > 1]
    assert multi, "expected at least one listing with multiple locations in the fixture"
    for lst in multi:
        for loc in lst.locations:
            assert hasattr(loc, "city")
            assert hasattr(loc, "state")
            assert hasattr(loc, "country")


def test_multi_jel_parsing(listings):
    multi = [lst for lst in listings if len(lst.jel_classes) > 1]
    assert multi, "expected at least one listing with multiple JEL codes in the fixture"


def test_keywords_split_and_stripped(listings):
    with_keywords = [lst for lst in listings if lst.keywords]
    assert with_keywords
    for lst in with_keywords:
        for kw in lst.keywords:
            assert kw == kw.strip()
            assert kw != ""


def test_previous_issue():
    assert joe.previous_issue("2026-02") == "2026-01"
    assert joe.previous_issue("2026-01") == "2025-02"


EXPORT_LINK_HTML = (
    '<form id="ListingsForm" action="/joe/listings/" method="get">'
    '<a href="/joe/resultset_output.php?mode=full_xml&amp;q=abc123XYZ">export</a>'
)

ISSUE_HTML = '<input type="hidden" name="ListingsForm[issue]" value="2026-02">'


@responses.activate
def test_get_current_issue_regex():
    responses.add(responses.GET, joe.LISTINGS_URL, body=ISSUE_HTML, status=200)
    session = joe.make_session()
    assert joe.get_current_issue(session) == "2026-02"


@responses.activate
def test_export_link_regex(xml_text):
    responses.add(responses.GET, joe.LISTINGS_PATH_URL, body=EXPORT_LINK_HTML.replace("&amp;", "&"), status=200)
    responses.add(
        responses.GET,
        joe.EXPORT_URL,
        body=xml_text,
        status=200,
    )
    session = joe.make_session()
    result = joe.fetch_issue_export_xml(session, "2026-02")
    assert result == xml_text


@responses.activate
def test_get_current_issue_raises_on_missing_field():
    responses.add(responses.GET, joe.LISTINGS_URL, body="<html>nothing here</html>", status=200)
    session = joe.make_session()
    with pytest.raises(joe.JoeFetchError):
        joe.get_current_issue(session)
