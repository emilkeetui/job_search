"""joepipe: one-command AEA JOE -> Sheets + Calendar pipeline (PLAN.md section 8)."""

from __future__ import annotations

import sys

import typer
from rich.console import Console
from rich.table import Table

from joepipe.auth import AuthError, load_credentials, service_account_email
from joepipe.calendar_sync import CalendarGuardError, get_calendar_service, purge_all
from joepipe.config import load_config
from joepipe.pipeline import fetch_and_score, run_doctor, run_pipeline

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001 -- best-effort; some streams can't be reconfigured
            pass

app = typer.Typer(add_completion=False, help="AEA JOE listings -> relevance scoring -> Sheets + Calendar.")
console = Console(width=180)


@app.command()
def run(
    dry_run: bool = typer.Option(False, "--dry-run", help="Fetch + score + print, zero writes."),
    apply: bool = typer.Option(False, "--apply", help="Required to confirm the first write to a new sheet."),
    i_know_what_im_doing: bool = typer.Option(
        False, "--i-know-what-im-doing", help="Allow calendar_id: primary. Do not use unless you mean it."
    ),
) -> None:
    """Fetch JOE listings, score them, upsert to Sheets, sync Calendar deadlines."""
    try:
        cfg = load_config()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Config error:[/red] {exc}")
        raise typer.Exit(code=2)

    try:
        result = run_pipeline(cfg, dry_run=dry_run, apply_=apply, i_know_what_im_doing=i_know_what_im_doing)
    except CalendarGuardError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2)

    for msg in result.messages:
        style = "red" if result.exit_code else "yellow"
        console.print(f"[{style}]{msg}[/{style}]")

    summary = (
        f"Fetched {result.fetched} · Kept {result.kept} · New {result.new} · "
        f"Updated {result.updated} · Events created {result.events_created} · "
        f"updated {result.events_updated} · deleted {result.events_deleted}"
    )
    console.print(summary)

    new_listings = [sl for sl in result.scored_kept if sl.listing.joe_id in set(result.new_ids)]
    if new_listings:
        _print_table(new_listings[:10], title="New listings")

    if result.sheet_url:
        console.print(f"Sheet: {result.sheet_url}")

    raise typer.Exit(code=result.exit_code)


@app.command()
def preview(
    field: str = typer.Option(None, "--field", help="Filter to one target area, e.g. environmental."),
    top: int = typer.Option(25, "--top", help="How many rows to show."),
) -> None:
    """Top listings by score, terminal only. No Google calls."""
    cfg = load_config()
    scored = fetch_and_score(cfg)
    kept = [sl for sl in scored if sl.score.total >= cfg.scoring.min_score_to_sheet]
    if field:
        kept = [sl for sl in kept if field in sl.score.fields]
    kept.sort(key=lambda sl: sl.score.total, reverse=True)
    _print_table(kept[:top], title=f"Top {min(top, len(kept))} of {len(kept)} kept (fetched {len(scored)})")


def _print_table(scored_listings, title: str) -> None:
    table = Table(title=title)
    table.add_column("Score", justify="right", width=5)
    table.add_column("Fields", max_width=18, overflow="fold")
    table.add_column("Institution", max_width=28, overflow="ellipsis", no_wrap=True)
    table.add_column("Title", max_width=32, overflow="ellipsis", no_wrap=True)
    table.add_column("Section", max_width=14, overflow="ellipsis", no_wrap=True)
    table.add_column("Deadline", width=10)
    table.add_column("joe_id", max_width=18, overflow="ellipsis", no_wrap=True)
    for sl in scored_listings:
        table.add_row(
            str(sl.score.total),
            ";".join(sl.score.fields),
            sl.listing.institution,
            sl.listing.title,
            sl.listing.section,
            sl.listing.deadline or "—",
            sl.listing.joe_id,
        )
    console.print(table)


@app.command()
def doctor() -> None:
    """Check config, creds, sheet + calendar access, and that calendar_id is not primary."""
    try:
        cfg = load_config()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Config error:[/red] {exc}")
        raise typer.Exit(code=2)

    checks = run_doctor(cfg)
    all_ok = True
    for check in checks:
        icon = "[green]OK[/green]" if check.ok else "[red]FAIL[/red]"
        console.print(f"{icon}  {check.name}: {check.detail}")
        all_ok = all_ok and check.ok

    raise typer.Exit(code=0 if all_ok else 2)


@app.command()
def auth() -> None:
    """Report service-account credential status. There is no interactive auth step."""
    console.print(
        "joepipe uses a Google service account, not user OAuth -- there is no browser "
        "flow to run.\nSee README.md for the setup walkthrough (Google Cloud project, "
        "service account key, sharing the sheet + calendar)."
    )
    try:
        creds = load_credentials()
    except AuthError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2)
    console.print(f"[green]Key found.[/green] Service account: {service_account_email(creds)}")
    console.print("Make sure that address has Editor access on the sheet and event-edit access on the calendar.")


@app.command()
def purge(
    confirm: bool = typer.Option(False, "--confirm", help="Required. Deletes all joepipe-created calendar events."),
) -> None:
    """Delete ONLY events tagged private.source=joepipe on the configured calendar. The undo button."""
    if not confirm:
        console.print("[yellow]Refusing to purge without --confirm.[/yellow]")
        raise typer.Exit(code=1)

    cfg = load_config()
    try:
        creds = load_credentials()
    except AuthError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2)

    if not cfg.google.calendar_id.strip():
        console.print("[red]calendar_id is not set in config.yaml.[/red]")
        raise typer.Exit(code=2)

    service = get_calendar_service(creds)
    deleted = purge_all(service, cfg.google.calendar_id)
    console.print(f"Deleted {deleted} joepipe-created event(s) from {cfg.google.calendar_id}.")


if __name__ == "__main__":
    app()
