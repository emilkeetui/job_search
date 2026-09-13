# job_search — JOE pipeline

One command scrapes AEA JOE listings, scores them for relevance against *your*
job-market profile (fully configurable — see "Customizing for your own search"
below), upserts them into a Google Sheet, and creates deadline events in a dedicated
Google Calendar. Idempotent, safe to re-run, no interactive prompts at runtime.

Originally built for a Cornell applied-economics-PhD-on-the-consulting-market job
search; the scoring is config-driven, not hardcoded, so it works for any field or
target sector once you fill in `config/config.yaml`.

See [PLAN.md](PLAN.md) for the full design rationale.

## Setup

### 1. Python environment

Python 3.11+ is required (developed against 3.14). From the repo root:

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -e ".[dev]"
```

### 2. Google Cloud service account (least privilege)

This project uses a **service account**, not your personal Google login. A service
account starts with access to *nothing* and only ever sees what you explicitly share
with it — one spreadsheet and one throwaway calendar. See PLAN.md section 3.1 for why
this is deliberate.

1. Go to the [Google Cloud Console](https://console.cloud.google.com/), create a new
   project (or reuse one), and enable the **Google Sheets API** and **Google Calendar
   API** for it.
2. Under **IAM & Admin → Service Accounts**, create a service account (any name, e.g.
   `joepipe`). No project-level roles are needed.
3. Open the service account → **Keys → Add Key → Create new key → JSON**. Save the
   downloaded file as `credentials/service_account.json` (the `credentials/` folder is
   gitignored — never commit this file). Note the service account's email address,
   something like `joepipe@your-project.iam.gserviceaccount.com`.
4. Create (or reuse) a Google Sheet to hold your listings, and note its ID from the
   URL (`https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/`). Open it →
   **Share** → add the service account's email as **Editor**.
5. In Google Calendar, create a **new secondary calendar** (Settings → "Add calendar" →
   "Create new calendar"), name it something like **"JOE Deadlines"**. Do **not** use
   your primary calendar — the pipeline refuses to write to `primary` on purpose.
6. Open that calendar's settings → **Share with specific people** → add the service
   account's email with **"Make changes to events"** permission.
7. Copy the calendar's ID (Settings → "Integrate calendar" → Calendar ID, looks like
   `xxxxxxxx@group.calendar.google.com`) — you'll need it in the next step.

### 3. Config

This repo is public, so the spreadsheet and calendar IDs are **not** committed —
they live in `.env`, gitignored:

```bash
cp .env.example .env
# then edit .env and fill in:
#   JOEPIPE_SPREADSHEET_ID=<from step 4 above>
#   JOEPIPE_CALENDAR_ID=<from step 7 above>
```

If `config/config.yaml` doesn't exist yet (first-time setup), start from the
template:

```bash
cp config/config.yaml.example config/config.yaml
```

`config.yaml.example` ships with an unfiltered default (every listing passes,
scored only by section) so the pipeline is immediately runnable. It has no opinion
about your field — see "Customizing for your own search" below to make it actually
rank things for you.

### Customizing for your own search

Everything that determines *what counts as a good listing* lives in
`scoring:` in `config/config.yaml` — no code changes needed:

- `target_employers.names` — employers you want ranked to the top (substring match,
  case-insensitive).
- `fields` — your target areas. Each one fires on JEL code prefixes and/or keyword
  matches against the title, department, keywords, and full text; matched fields show
  up in the sheet's `Fields` column so you can filter by area. `score.py`'s
  docstring and `config/config.yaml.example`'s comments show the exact syntax.
  Existing example values in a filled-in `config.yaml` (from the original consulting
  job-market profile this was built for) are a reference, not a requirement — replace
  them with your own.
- `negative` — patterns to actively penalize (subfields you want deprioritized,
  appointment types you want to avoid).
- `sections` — how much weight academic vs. nonacademic vs. government postings get.

Run `joepipe preview` after each change — it fetches, scores, and prints a table with
zero Google calls, so you can iterate on scoring without touching the sheet.

### 4. Verify

```bash
joepipe doctor
```

This checks the config loads, the service-account key exists, the sheet is shared with
it, the calendar is shared with it, and that `calendar_id` isn't `primary`. Fix
whatever it flags before running the real thing.

## Usage

```bash
joepipe preview                          # top 25 by score, terminal only, no Google calls
joepipe preview --field environmental    # filter to one target area

joepipe run --dry-run                    # fetch + score + print, zero writes
joepipe run --apply                      # first-ever write to a new sheet needs this
joepipe run                              # normal run, after the first successful apply

joepipe doctor                           # config/creds/access checks
joepipe purge --confirm                  # delete ONLY events this pipeline created (the undo)
```

The first time `joepipe run` sees a sheet with no rows in it, it prints what it *would*
write and exits without writing — pass `--apply` to confirm. After that first apply,
plain `joepipe run` writes normally.

Running `joepipe run` twice in a row should always print `New 0 · Events created 0`
and never touch a cell you've edited by hand (the `Track`, `Status`, and `Notes`
columns are yours — the pipeline never overwrites them).

## The Sheet

One worksheet (`JOE Listings` by default). Columns A–N are pipeline-owned and get
rewritten every run; columns **Track** (checkbox), **Status** (dropdown), and
**Notes** (free text) are yours and are never touched by the pipeline once a row
exists. Checking **Track** forces a calendar event to be created for that row even if
its score is below the auto-create threshold. Setting **Status** to `Rejected` (or
unchecking **Track** on a low-score row) deletes its calendar event on the next run.

Rows are never deleted, even for listings that have rolled out of JOE's current
export — their `Days Left` column just switches to `expired`.

## Revoking access

Because this uses a service account scoped to exactly one sheet and one calendar,
revocation is one click:
- Sheet: open its Share dialog, remove the service account's email.
- Calendar: open the calendar's Settings → "Share with specific people", remove the
  service account's email.
- To also delete a Cloud project's key entirely: Cloud Console → IAM & Admin →
  Service Accounts → the account → Keys → delete the key (or delete the whole service
  account).

To undo everything the pipeline has created in the calendar without touching the
sheet: `joepipe purge --confirm`. It deletes exactly the events tagged
`extendedProperties.private.source == "joepipe"` and nothing else.

## Known limitation to verify

The Calendar API's popup-reminder overrides are written by the service account, and
in some Workspace/consumer account configurations a service-account-created event's
reminders apply only to the service account's own (invisible) calendar view rather
than to the human owner of the "JOE Deadlines" calendar. **Verify this once your
credentials are set up**: run the pipeline against a listing with a near-term
deadline and confirm you personally receive the popup notification on your phone/
desktop. If you don't, the fix is to fall back to `useDefault: true` plus a separate
"heads up" all-day event tagged `private.kind = "leadtime"` — flag it and we'll wire
that in.

## Development

```bash
pytest                     # unit tests, fully offline (fixture at tests/fixtures/joe_2026-02.xml)
```

`test_joe.py` mocks the two-step JOE fetch with `responses` and parses a real cached
export. `test_score.py` is table-driven against `config/config.yaml`. `test_sheets.py`
and `test_calendar.py` fake the gspread / Calendar API clients — no live Google access
needed to run the suite.

## Repo layout

See PLAN.md section 2.
