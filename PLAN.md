# JOE Job Pipeline — Implementation Plan

**Repo:** https://github.com/emilkeetui/job_search
**Implementer:** Sonnet agent. Follow this document top to bottom.
**Goal:** one command (`python -m joepipe run`) that scrapes AEA JOE listings, scores them for relevance, upserts them into a Google Sheet, and creates deadline events in Google Calendar. Idempotent, safe to re-run, no interactive prompts at runtime.

**User context (drives the relevance config):** Cornell applied-economics PhD, JMP on coal mining x drinking-water quality, on the market fall 2026, primary target is economic consulting (Analysis Group, Cornerstone Research, Brattle, CRA, Compass Lexecon, NERA, Edgeworth, Bates White), secondary targets are policy/government research shops and applied-micro academic posts. See `career-strategy-policy-brief-vs-jmp.md` and `networking-for-econ-consulting.md` in this repo.

**Language: Python.** R is not installed on this machine; Python 3.13 and 3.14 are on PATH. Do not write an R implementation.

---

## 0. Verified facts about the JOE data source

These were confirmed live against aeaweb.org on 2026-08-29. **Do not re-derive them, and do not replace this approach with HTML scraping of the listings page** — there is a clean XML export.

### 0.1 The two-step fetch (this is the whole API)

JOE's export URLs carry an opaque `q` parameter: base64-ish of a zlib-compressed JSON filter blob, with a non-obvious character substitution. **Hand-constructed `q` values return HTTP 500.** Do not attempt to build one. Instead:

1. **GET the search page with plain form params.** The search form is `<form id="ListingsForm" action="/joe/listings/" method="get">`, so filters can be passed as ordinary query params:

   ```
   GET https://www.aeaweb.org/joe/listings/?ListingsForm%5Bissue%5D=2026-02&ListingsForm%5BoriginalIssue%5D=2026-02
   ```

2. **Scrape the export link out of the returned HTML** with the regex `resultset_output\.php\?mode=full_xml&q=([^"]+)` (first match).

3. **GET that export URL** to receive the full XML for the filtered result set:

   ```
   https://www.aeaweb.org/joe/resultset_output.php?mode=full_xml&q=<q>
   ```

   Verified: HTTP 200, ~450 KB, 135 `<position>` elements for issue `2026-02`; 285 for issue `2026-01`. The export returns the **entire** result set, not one page — ignore `lpp` / pagination entirely.

Send a normal `User-Agent` (e.g. `Mozilla/5.0 (compatible; joe-pipeline/1.0)`), use a `requests.Session`, and sleep ~1 s between the two requests. Be a polite client: the whole run is 3–4 HTTP requests.

### 0.2 Issue codes

Issues are `YYYY-01` (August issue, fall market) and `YYYY-02` (February issue). **Never hardcode or compute the current issue from today's date.** Read it from the hidden input on the bare `/joe/listings` page:

```
name="ListingsForm[issue]" value="2026-02"
```

Regex: `name="ListingsForm\[issue\]" value="([^"]+)"`. As of 2026-08-29 this returns `2026-02`. Requesting a not-yet-populated future issue silently falls back to the current one, so:

**Fetch the current issue AND the immediately preceding issue, then union and dedupe on `joe_id`.** This makes the pipeline correct across the August/February rollover, when still-open postings from the prior issue matter. The prior issue is derivable: `YYYY-02` → `YYYY-01`; `YYYY-01` → `(YYYY-1)-02`. Cross-check the derived code against the archive links on the page (`/joe/listings.php?issue=XXXX-XX`) and skip it if absent.

### 0.3 XML schema (exact, verified)

```xml
<JOE_EXPORT>
  <year joe_year_ID="2026">
    <issue joe_issue_ID="2">
      <position jp_id="111477663">
        <jp_section>Full-Time Nonacademic</jp_section>
        <jp_title>Research Engineer</jp_title>
        <jp_institution>America First Policy Institute</jp_institution>
        <jp_division/>
        <jp_department>Office for Fiscal and Regulatory Analysis</jp_department>
        <jp_salary_range/>
        <jp_agency_insertion_num/>
        <jp_application_deadline>2027-01-31 00:00:00</jp_application_deadline>
        <jp_full_text>...long plain text, newline-separated...</jp_full_text>
        <jp_keywords>Public Finance\nFiscal Policy\n...</jp_keywords>
        <jp_status/>
        <jp_new/>
        <locations>
          <location><city/><state/><country>UNITED STATES</country></location>
        </locations>
        <JEL_Classifications>
          <jel_class><jc_code>H1</jc_code><jc_description>Structure and Scope of Government</jc_description></jel_class>
        </JEL_Classifications>
      </position>
```

Parsing notes, all verified:

- `jp_id` is on the `<position>` attribute, not a child element.
- `jp_status` and `jp_new` were **empty on all 135 records** — do not depend on them.
- `jp_application_deadline` is `YYYY-MM-DD HH:MM:SS`, and was **missing/empty on 2 of 135**. Handle the empty case (no calendar event; sheet cell reads `—`).
- `jp_keywords` is newline-separated; split on `\n` and strip.
- `locations` and `JEL_Classifications` are repeating; a position can have several of each, and city/state are often empty for international or remote roles.
- There is **no posted-date field** and often **no application URL** in the XML — the one inspected listing had zero URLs in `jp_full_text`. Extract URLs/emails from `jp_full_text` opportunistically (`https?://\S+`, and a simple email regex) but treat them as optional.
- Section values observed (use these verbatim for config matching): `US: Full-Time Academic (Permanent, Tenure Track or Tenured)`, `US: Other Academic (Visiting or Temporary)`, `US: Other Academic (Part-time or Adjunct)`, `International: Full-Time Academic (Permanent, Tenure Track or Tenured)`, `International: Other Academic (Visiting or Temporary)`, `Full-Time Nonacademic`, `Other Nonacademic (Temporary, Part-Time, Non-Salaried, Consulting, Etc.)`.

### 0.4 Canonical listing URL

`https://www.aeaweb.org/joe/listing.php?JOE_ID=<issue>_<jp_id>` — e.g. `...?JOE_ID=2026-02_111477663`. (Passing the bare `jp_id` 302-redirects to this form.) Build the composite id yourself; that composite is also the pipeline's primary key.

---

## 1. Repo setup (do this first)

The working directory `z:\ek559\job_search` is **not yet a git repo** and holds two markdown notes. The GitHub repo exists.

```bash
cd z:/ek559/job_search
git init -b main
git remote add origin https://github.com/emilkeetui/job_search.git
git fetch origin
# If origin/main has commits, rebase onto it; otherwise just proceed.
```

Do not delete or rewrite the two existing `.md` files. Do not force-push. Do not commit or push unless the user asks — leave the work staged and tell them.

---

## 2. Target layout

```
job_search/
  README.md                  # setup + one-command usage
  PLAN.md                    # this file
  pyproject.toml             # deps, console script `joepipe`
  .env.example
  .gitignore                 # .env, credentials/, token.json, data/, .venv/
  config/
    config.yaml              # ALL user-tunable knobs (see section 4)
  src/joepipe/
    __init__.py
    cli.py                   # typer entrypoint: run | auth | preview | doctor
    config.py                # pydantic models; loads config.yaml + .env
    joe.py                   # section 0 fetch + XML parse -> list[Listing]
    models.py                # Listing dataclass, ScoredListing
    score.py                 # deterministic relevance scoring (section 5)
    sheets.py                # Google Sheets upsert (section 6)
    calendar_sync.py         # Google Calendar sync (section 7)
    auth.py                  # one-time OAuth; token cache
    pipeline.py              # orchestration (section 8)
  data/
    raw/                     # cached XML snapshots, gitignored
  tests/
    fixtures/joe_2026-02.xml # real snapshot, committed
    test_joe.py test_score.py test_sheets.py test_calendar.py
```

Dependencies: `requests`, `lxml` (or stdlib `xml.etree`), `pydantic`, `pyyaml`, `python-dotenv`, `gspread`, `google-auth`, `google-auth-oauthlib`, `google-api-python-client`, `typer`, `rich`; dev: `pytest`, `pytest-mock`, `responses`.

**Grab the test fixture before writing any code** (so tests work off-season and offline):

```bash
mkdir -p tests/fixtures
python - <<'PY'
import re, requests
s = requests.Session(); s.headers["User-Agent"] = "Mozilla/5.0"
h = s.get("https://www.aeaweb.org/joe/listings", timeout=30).text
issue = re.search(r'name="ListingsForm\[issue\]" value="([^"]+)"', h).group(1)
q = re.search(r'resultset_output\.php\?mode=full_xml&q=([^"]+)', h).group(1)
x = s.get(f"https://www.aeaweb.org/joe/resultset_output.php?mode=full_xml&q={q}", timeout=60).text
open(f"tests/fixtures/joe_{issue}.xml", "w", encoding="utf-8").write(x)
print(issue, x.count("<position "))
PY
```

---

## 3. Authentication — service account, least privilege

**Use a Google Cloud service account, not user OAuth.** This is a deliberate security choice (see section 3.1) and it is not negotiable without asking the user first.

Setup, to be documented step by step in README.md:

1. Google Cloud console → new project → enable the **Google Sheets API** and **Google Calendar API**.
2. Create a service account; create a JSON key; save it to `credentials/service_account.json` (gitignored). Note its address, `something@project.iam.gserviceaccount.com`.
3. In the target Sheet (ID from `.env`'s `JOEPIPE_SPREADSHEET_ID`), Share → add that address as **Editor**.
4. In Google Calendar, create a **new secondary calendar named "JOE Deadlines"** — do not use `primary`. Settings for that calendar → "Share with specific people" → add the service account address with **"Make changes to events"**. Copy the calendar ID (looks like `...@group.calendar.google.com`) into `config.yaml`.

Auth code is then just:

```python
from google.oauth2.service_account import Credentials
SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/calendar.events"]
creds = Credentials.from_service_account_file("credentials/service_account.json", scopes=SCOPES)
```

No browser, no refresh-token expiry, no interactive step at runtime. If the key file is missing or the sheet/calendar has not been shared, exit code 2 with a message naming the exact missing share.

### 3.1 Why not user OAuth

A user OAuth token with the `spreadsheets` scope can read and write **every spreadsheet in the user's Drive**, and `calendar.events` reaches **every calendar they own**. The service account starts with access to *nothing* and only ever sees what is explicitly shared with it — here, one sheet and one throwaway calendar. Revocation is one click in the sheet's share dialog rather than a scope-wide revoke. The blast radius is the entire point.

The OAuth path is the *fallback*, only if service-account calendar writes turn out not to work for this account: same scopes, `InstalledAppFlow.run_local_server(port=0)` once, token cached at `credentials/token.json`, and the consent screen must be set to "In production" or refresh tokens expire every 7 days. Ask the user before switching to it.

### 3.2 Guardrails the implementation must enforce

These are requirements, not suggestions:

- **Never `primary`.** `calendar_id` must be a dedicated secondary calendar. Refuse to run with `calendar_id: primary` unless the user passes `--i-know-what-im-doing`.
- **Never touch another worksheet.** Operate only on the tab named in `config.yaml`; never enumerate or write other tabs, and never touch other spreadsheets even if the scope would allow it.
- **Never delete a sheet row and never `clear()` a range.** Deletion is not in the pipeline's vocabulary (section 6).
- **Only delete calendar events the pipeline created**, identified by `extendedProperties.private.joe_id`. Never delete an event lacking that key. Also stamp `private.source = "joepipe"`.
- **`joepipe purge --confirm`** must exist: deletes exactly the events carrying `source=joepipe` on the configured calendar, and nothing else. This is the undo button.
- **First run defaults to `--dry-run`.** The first invocation against a sheet the pipeline has never written prints what it *would* do and exits; require `--apply` to actually write. After the first successful apply, `run` writes normally.
- **Secrets never leave `credentials/`.** `.gitignore` must contain `credentials/`, `*.json` under it, `.env`, and `data/`. Verify with `git check-ignore` before the first commit. The GitHub repo is user-owned — if it is public, the spreadsheet ID should live in `.env`, not `config.yaml`.
- **Log every write.** Append one JSON line per run to `data/runs.jsonl`: timestamp, counts, and the joe_ids of rows written and events created/patched/deleted. This makes "what did it do to my calendar last Tuesday" answerable.

---

## 4. `config/config.yaml`

Every knob lives here; **no magic constants in the scoring code**. Ship it prefilled with the user's actual profile:

```yaml
google:
  # spreadsheet_id / calendar_id are NOT committed -- see .env / JOEPIPE_SPREADSHEET_ID
  spreadsheet_id: ""
  worksheet_name: "JOE Listings"     # pipeline owns this tab only; never touches others
  calendar_id: ""                    # REQUIRED: dedicated "JOE Deadlines" secondary calendar
                                     # ...@group.calendar.google.com — "primary" is refused

fetch:
  include_previous_issue: true
  cache_dir: "data/raw"

scoring:
  min_score_to_sheet: 3       # below this, drop entirely
  min_score_to_calendar: 6    # at/above this, auto-create a deadline event
  sections:                   # points by JOE section
    "Full-Time Nonacademic": 5
    "Other Nonacademic (Temporary, Part-Time, Non-Salaried, Consulting, Etc.)": 2
    "US: Full-Time Academic (Permanent, Tenure Track or Tenured)": 3
    "International: Full-Time Academic (Permanent, Tenure Track or Tenured)": 0
    "US: Other Academic (Visiting or Temporary)": 1
  target_employers:           # substring match on institution, case-insensitive
    points: 6
    names: ["Analysis Group", "Cornerstone Research", "Brattle", "Charles River Associates",
            "CRA International", "Compass Lexecon", "NERA", "Edgeworth Economics",
            "Bates White", "Keystone Strategy", "Berkeley Research Group",
            "Resources for the Future", "Mathematica", "Abt", "RAND", "Urban Institute",
            "NBER", "Federal Reserve", "Environmental Protection Agency", "World Bank"]
  # The user's five target areas. A field fires if ANY of its JEL prefixes or keywords
  # match; it scores `points`, +1 for each additional distinct signal, capped at points+3.
  # The names of the fields that fired go in the sheet's `Fields` column so the sheet can
  # be filtered by area.
  fields:
    environmental:
      points: 5
      jel: ["Q5", "Q4", "Q2", "Q3"]     # env econ, energy, renewables/water, nonrenewables
      keywords: ["environmental economics", "energy economics", "climate", "water quality",
                 "drinking water", "air quality", "pollution", "coal", "mining", "emissions",
                 "electricity market", "utility regulation", "natural resource",
                 "environmental justice", "EPA", "sustainability"]
    health:
      points: 4
      jel: ["I1", "I3"]
      keywords: ["health economics", "health policy", "healthcare", "public health",
                 "medicaid", "medicare", "insurance", "pharmaceutical", "hospital",
                 "epidemiolog", "mortality", "health outcomes"]
    micro:
      points: 4
      jel: ["D1", "D6", "D8", "J2", "J3", "H2", "L1", "L4", "R2", "O1"]
      keywords: ["applied microeconomics", "labor economics", "public economics",
                 "industrial organization", "development economics", "urban economics",
                 "causal inference", "quasi-experimental", "difference-in-differences",
                 "instrumental variables", "regression discontinuity", "program evaluation",
                 "policy evaluation", "welfare analysis"]
    data_science:
      points: 4
      jel: ["C1", "C3", "C4", "C5", "C8", "C9"]
      keywords: ["data scientist", "data science", "machine learning", "econometrician",
                 "statistical modeling", "predictive model", "python", "R", "SQL",
                 "experimentation", "a/b test", "causal machine learning", "big data",
                 "quantitative researcher", "quantitative analyst"]
    consulting:
      points: 5
      jel: ["K2", "K4", "L4", "L5"]
      keywords: ["economic consulting", "litigation", "expert testimony", "antitrust",
                 "competition policy", "damages", "class certification", "merger review",
                 "regulatory analysis", "benefit-cost", "cost-benefit", "valuation",
                 "client engagement", "case team"]
  max_field_points: 16        # total cap across all fields
  negative:                   # subtract points
    - {points: -4, patterns: ["macroeconomics", "monetary policy", "asset pricing",
                              "corporate finance", "marketing", "accounting",
                              "economic history", "history of thought"]}
    - {points: -3, patterns: ["postdoc", "post-doctoral", "adjunct", "lecturer"]}
  locations:
    preferred: {points: 2, patterns: ["New York", "Boston", "Washington", "Massachusetts",
                                      "District of Columbia", "Chicago", "Illinois", "Remote"]}
    penalize:  {points: -2, patterns: []}   # non-US handled via section weights

calendar:
  reminder_days: [14, 3]      # popup reminders before the all-day deadline event
  event_prefix: "[JOE]"
  create_for_marked_rows: true  # honor the sheet's Track column (see section 6)
```

---

## 5. `score.py` — deterministic scoring

Pure functions, no I/O, fully unit-testable. Signature:

```python
score(listing, cfg) -> ScoreResult(total: int, fields: list[str], reasons: list[str])
```

`fields` is the list of target areas that fired (`["environmental", "consulting"]`) → sheet column `Fields`. `reasons` is human-readable (`["section:Full-Time Nonacademic +5", "employer:Brattle +6", "field:environmental jel=Q5,kw=water quality +6"]`) → sheet column `Why`. Together these are what let the user tune `config.yaml` without reading code.

Rules:

- Match against `title + department + keywords + full_text`, case-insensitively.
- **Use word-boundary regex, not naive substring**, for tokens shorter than 5 characters — `"R"`, `"SQL"`, `"EPA"` in the `data_science` / `environmental` lists will match inside unrelated words otherwise. Precompile one regex per pattern at config load.
- JEL matches are `jc_code.startswith(prefix)`.
- Per-field cap `points + 3`, total field cap `max_field_points`, then add section + employer + location, subtract negatives, clamp at 0.
- Deadline proximity is **not** part of the score (it is a separate column and sort key) — keep the score stable across runs so the user's mental ranking does not churn.

**No LLM calls in v1.** The scorer must be deterministic and runnable offline. If the user later wants semantic re-ranking, add it behind an explicit `--llm-rerank` flag as a separate column; do not make the core path depend on an API key.

---

## 6. `sheets.py` — Google Sheets upsert

One worksheet, primary key = `joe_id` (`<issue>_<jp_id>`, section 0.4).

Columns, in order:

| # | Column | Owner | Notes |
|---|--------|-------|-------|
| A | joe_id | pipeline | primary key, frozen |
| B | First Seen | pipeline | date of first run that saw it; never overwritten |
| C | Score | pipeline | |
| D | Fields | pipeline | `;`-joined target areas that fired — filter the sheet on this |
| E | Why | pipeline | scoring reasons |
| F | Section | pipeline | |
| G | Institution | pipeline | |
| H | Title | pipeline | |
| I | Location | pipeline | `City, State, Country`, `;`-joined if several |
| J | Deadline | pipeline | `YYYY-MM-DD` or `—` |
| K | Days Left | pipeline | recomputed each run |
| L | JEL | pipeline | `;`-joined codes |
| M | Link | pipeline | `=HYPERLINK(...)` to the section 0.4 URL |
| N | Contact/URL | pipeline | best-effort from full text, may be blank |
| O | **Track** | **user** | checkbox — force a calendar event on |
| P | **Status** | **user** | dropdown: (blank)/Interested/Applying/Applied/Interview/Rejected/Offer |
| Q | **Notes** | **user** | free text |

**The user-owned columns O, P, Q must survive every run.** Implementation:

1. Read the whole sheet once into a `{joe_id: row}` dict.
2. For each scored listing: if new, append with O/P/Q blank; if existing, write **only** columns A–N for that row and leave O–Q untouched.
3. Never `clear()` the sheet. Never re-sort rows in place (row identity is what preserves the user's edits) — sort via a frozen header plus a Sheets basic filter / filter view instead.
4. Batch all updates into a single `worksheet.batch_update()` call; do not write cell by cell (quota).
5. On first run, create the worksheet if absent, write the header, freeze row 1, set column O to a checkbox data-validation rule and column P to a dropdown, and conditional-format `Days Left <= 7` red.
6. Listings scoring below `min_score_to_sheet` are simply not written. Listings that were in the sheet but have vanished from JOE (issue rolled over) are **not deleted** — set `Days Left` to `expired`.

Handle `gspread.exceptions.APIError` 429 with exponential backoff.

---

## 7. `calendar_sync.py` — deadline events

For each listing with a parseable deadline that either scores >= `min_score_to_calendar` **or** has `Track` checked in the sheet:

- **All-day event** on the deadline date (`start.date` = deadline, `end.date` = deadline + 1 day).
- Summary: `[JOE] {Institution} — {Title}`.
- Description: score, why, location, JEL codes, the listing URL, and the current `Status` from the sheet.
- `reminders: {useDefault: false, overrides: [{method: "popup", minutes: d*24*60} for d in reminder_days]}`. Two caveats: the Calendar API caps `minutes` at 40320 (28 days), so clamp and drop any lead time exceeding the days remaining; and **reminder overrides written by a service account may apply to the service account's copy rather than the user's**. Verify this during implementation with a single test event — if the user does not actually get the popup, fall back to `useDefault: true` plus a separate all-day "heads up" event `reminder_days[0]` days earlier, tagged with the same `joe_id` plus `private.kind = "leadtime"`.
- **Idempotency key:** `extendedProperties.private.joe_id = <joe_id>`. Find existing events with `events().list(calendarId=..., privateExtendedProperty=f"joe_id={joe_id}", showDeleted=False, singleEvents=True)`. If found and the payload is unchanged, skip; if changed, `events().patch`; if absent, `events().insert`. **Never create a duplicate, and never search by summary text.**
- If the user sets `Status = Rejected` or unchecks `Track` on a row whose score is below the calendar threshold, delete the event (`events().delete`, tolerate 404/410).
- Deadlines already in the past: skip creation entirely.

---

## 8. `pipeline.py` + `cli.py` — the one-command path

```
joepipe run             # the command the user asks for
joepipe run --dry-run   # fetch + score + print a rich table, zero writes
joepipe run --apply     # required to confirm the very first write to a new sheet
joepipe preview         # top 25 by score, terminal only
joepipe preview --field environmental   # filter the preview to one target area
joepipe doctor          # check config, creds, sheet + calendar access, and that
                        #   calendar_id is not "primary"
joepipe purge --confirm # delete ONLY events tagged private.source=joepipe (the undo)
```

`run` sequence:

1. Load config + `.env`; fail fast with actionable messages.
2. Resolve current issue; fetch current (+ previous) issue XML; cache raw to `data/raw/joe_{issue}_{YYYYMMDD}.xml`. If the network fetch fails and a cache under 24 h old exists, use it and warn.
3. Parse → dedupe on `joe_id` → score → filter.
4. Read the sheet; compute new / updated / unchanged; upsert.
5. Sync the calendar.
6. Print a summary: `Fetched 420 · Kept 63 · New 11 · Updated 52 · Events created 4 · updated 1 · deleted 0`, then the top 10 new listings as a table, then the sheet URL.
7. Exit 0 on success, 1 on partial failure (e.g. calendar failed but the sheet succeeded — say exactly which half succeeded), 2 on auth needed.

**Idempotency is the acceptance bar: running `joepipe run` twice in a row must produce `New 0 · Events created 0` and must not disturb any user-edited cell.**

### Making it trivial for the agent to run

Commit both of these so a future session needs no explanation:

- **`CLAUDE.md`** at repo root: "To run the pipeline: `python -m joepipe run` from the repo root (venv at `.venv`). It is idempotent and safe to re-run. If it exits 2, tell the user to run `joepipe auth` — do not attempt the browser flow yourself."
- **`.claude/commands/joe.md`**: a slash command whose body runs `python -m joepipe run` and summarizes the output.

---

## 9. Tests

- `test_joe.py` — parse `tests/fixtures/joe_2026-02.xml`: assert 135 positions, the empty-deadline case, multi-location and multi-JEL parsing, and composite-id construction. Mock the two-step fetch with `responses`; include a fixture asserting the export-link regex works.
- `test_score.py` — table-driven: a Brattle nonacademic Q5 posting scores high; a macro/finance TT posting scores near zero; caps and negatives fire.
- `test_sheets.py` — with a faked gspread client, assert that updating an existing row writes only A–N and that O/P/Q are preserved.
- `test_calendar.py` — with a faked Calendar service, assert insert-once / patch-on-change / skip-when-identical / delete-on-reject, keyed on `extendedProperties.private.joe_id`.

---

## 10. Build order

1. Repo setup (section 1) + scaffolding + `pyproject.toml` + `.gitignore`.
2. Pull the test fixture (section 2).
3. `models.py`, `joe.py`, `test_joe.py` — get parsing green offline first.
4. `config.py` + `config/config.yaml` + `score.py` + `test_score.py`.
5. `cli.py` with `run --dry-run` and `preview`. **Checkpoint: show the user the top-25 table and let them tune `config.yaml` before any Google integration is written.**
6. `auth.py` (service-account loader) + `doctor` + the guardrail checks in section 3.2.
7. `sheets.py` + tests.
8. `calendar_sync.py` + tests.
9. `pipeline.py` wiring, summary output, exit codes, `purge`, `data/runs.jsonl` audit log.
10. `README.md` (service-account setup walkthrough per section 3, plus a "how to revoke access" section), `CLAUDE.md`, `.claude/commands/joe.md`.
11. Full idempotency check: run twice, confirm the second run is a no-op.

## 11. Optional later phase — unattended runs

Only after the above works and the user asks: a GitHub Actions workflow on a daily cron that restores `client_secret.json` / `token.json` from repo secrets and runs `joepipe run`. Note that the refresh token must be re-seeded if it is ever revoked.

## 12. Rules for the implementer

- **Do not** hand-construct the `q` parameter (section 0.1) or scrape listing HTML instead of the XML.
- **Do not** compute the current issue from the date (section 0.2).
- **Do not** clear/rewrite whole sheet ranges, or delete rows (section 6).
- **Do not** use `primary` as the calendar, or user OAuth instead of the service account, without asking (section 3).
- **Do not** delete any calendar event that lacks `extendedProperties.private.source == "joepipe"`.
- **Do not** deduplicate calendar events by title (section 7).
- **Do not** add LLM calls to the core path (section 5).
- **Do not** commit `.env`, `credentials/`, or `data/`.
- **Do not** `git push` or commit without the user asking.
- If JOE's HTML changes and the export-link regex misses, fail loudly with the URL that was fetched — never silently produce zero listings.
