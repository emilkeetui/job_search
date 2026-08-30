# job_search / joepipe

AEA JOE listings -> relevance scoring -> Google Sheets + Calendar pipeline.
See [PLAN.md](PLAN.md) for the full design; this file is just the "how do I run it" cheat sheet
for a future Claude session.

**To run the pipeline:** `python -m joepipe run` from the repo root (venv at `.venv`).
It is idempotent and safe to re-run -- running it twice in a row should produce
`New 0 · Events created 0` and never touch a user-edited sheet cell.

- If it exits 2, tell the user to run `joepipe doctor` (or `joepipe auth`) to see exactly what's
  missing -- **do not** attempt any browser-based OAuth flow yourself; this project uses a
  Google service account, not user OAuth (see PLAN.md section 3).
- If it exits 1, the sheet write succeeded but the calendar sync failed (or vice versa);
  the summary line says which half.
- `python -m joepipe preview` fetches + scores + prints a table with zero Google calls --
  safe to run anytime, including before credentials exist.
- Never hand-construct the JOE `q` export parameter or scrape the listings HTML for job data
  (PLAN.md section 0). Never use `calendar_id: primary`. Never `clear()` the sheet or delete
  a row. Never delete a calendar event lacking `extendedProperties.private.source == "joepipe"`.
- Do not commit `.env`, `credentials/`, or `data/`.
