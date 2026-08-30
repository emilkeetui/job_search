Run the JOE pipeline and summarize the result for the user.

Run `python -m joepipe run` from the repo root (the venv is at `.venv`, so use
`.venv/Scripts/python.exe -m joepipe run` if the venv isn't already activated).

It is idempotent and safe to re-run. Report back:
- The one-line summary (`Fetched N · Kept N · New N · Updated N · Events created/updated/deleted N`).
- The new-listings table if any were printed.
- The sheet URL.
- If it exited non-zero: exit 2 means auth/setup is missing — point the user at
  `joepipe doctor` and README.md's setup section, do NOT attempt any browser OAuth
  flow. Exit 1 means one half (sheet or calendar) failed while the other succeeded —
  say exactly which, from the printed message.
