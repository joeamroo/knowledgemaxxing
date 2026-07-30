"""km command-line interface."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

app = typer.Typer(
    name="km",
    help="knowledgemaxxing: mine your digital exhaust into organized, searchable knowledge.",
    no_args_is_help=True,
)
console = Console()
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def _cfg():
    from km.config import load_config

    return load_config(Path.cwd())


@app.command()
def version() -> None:
    """Print km version."""
    from km import __version__

    typer.echo(f"km {__version__}")


@app.command()
def discover(
    gdrive_api: bool = typer.Option(False, "--gdrive-api", help="Also search Google Drive via API"),
    download_icloud: bool = typer.Option(
        False, "--download-icloud", help="Offer to materialize evicted iCloud files"
    ),
) -> None:
    """Scan disk, Chrome, iCloud, and Google Drive for source files; write manifest.json."""
    from km.discover.datasources import missing_report, write_data_sources_md
    from km.discover.gdrive import SETUP_HELP, scan_gdrive_api
    from km.discover.icloud import materialize, pending_downloads
    from km.discover.manifest import build_manifest, render_manifest
    from km.discover.scanner import discover_all

    cfg = _cfg()
    console.print("[bold]Scanning[/bold] local roots, live Chrome, iCloud, Google Drive mount...")
    entries = discover_all(cfg)

    if gdrive_api:
        api_entries, err = scan_gdrive_api(cfg)
        if err:
            console.print(f"[yellow]Google Drive API mode unavailable:[/yellow] {err}")
            console.print(SETUP_HELP)
        else:
            entries.extend(api_entries)

    manifest = build_manifest(entries)
    manifest_path = cfg.project_root / "manifest.json"
    manifest.save(manifest_path)
    render_manifest(manifest, console)

    pending = pending_downloads(entries)
    if pending:
        console.print(
            f"[yellow]{len(pending)} iCloud files are evicted (listed as needs_download).[/yellow]"
        )
        if download_icloud:
            for e in pending:
                if typer.confirm(f"Download {e.path}?", default=False):
                    ok, msg = materialize(Path(e.path))
                    console.print(("[green]" if ok else "[red]") + msg)
        else:
            console.print("Re-run with --download-icloud to materialize them one by one.")

    ds_path = cfg.project_root / "DATA_SOURCES.md"
    write_data_sources_md(manifest, ds_path)
    report = missing_report(manifest)
    console.print("\n[bold]Source coverage[/bold]")
    for source, found in report.items():
        mark = "[green]FOUND[/green]" if found else "[red]MISSING[/red]"
        console.print(f"  {mark} {source}")
    console.print(f"\nInstructions for missing exports: [bold]{ds_path.name}[/bold]")


@app.command()
def ingest(
    manifest_path: Path = typer.Option(
        Path("manifest.json"), "--manifest", help="Manifest produced by km discover"
    ),
    include_generic: bool = typer.Option(
        False, "--include-generic", help="Also ingest content-sniffed generic URL files"
    ),
) -> None:
    """Ingest every ready file in the manifest into data/knowledge.db."""
    from km.db import get_db
    from km.ingest import ingest_manifest
    from km.models import Manifest

    cfg = _cfg()
    if not manifest_path.exists():
        console.print("[red]manifest.json not found. Run km discover first.[/red]")
        raise typer.Exit(1)
    manifest = Manifest.load(manifest_path)
    conn = get_db(cfg.db_path)
    report = ingest_manifest(conn, manifest, cfg, include_generic=include_generic)
    for path, count in report.ingested:
        console.print(f"[green]ok[/green] {path}: {count} items")
    for path in report.already:
        console.print(f"[dim]unchanged[/dim] {path}")
    for path, reason in report.skipped:
        console.print(f"[yellow]skipped[/yellow] {path}: {reason}")
    console.print(
        f"\n[bold]{report.total_items}[/bold] items from {len(report.ingested)} files "
        f"({len(report.already)} unchanged, {len(report.skipped)} skipped, see skipped.log)"
    )


@app.command()
def login(
    check: bool = typer.Option(False, "--check", help="Only verify sessions, open nothing"),
    cdp: Optional[int] = typer.Option(None, "--cdp", help="Attach to real Chrome via CDP port"),
) -> None:
    """Sign into X, Reddit, Substack, and HN in the km browser profile."""
    from km.scrapers.session import SERVICES, browser_context, check_all

    if check:
        with browser_context(headed=False, cdp_port=cdp) as context:
            results = check_all(context)
        for name, ok in results.items():
            mark = "[green]valid[/green]" if ok else "[red]not logged in[/red]"
            console.print(f"  {mark} {SERVICES[name]['label']}")
        return

    with browser_context(headed=True, cdp_port=cdp) as context:
        for name, spec in SERVICES.items():
            from km.scrapers.session import session_valid

            if session_valid(context, name):
                console.print(f"[green]already logged in:[/green] {spec['label']}")
                continue
            console.print(f"\n[bold]Log into {spec['label']}[/bold]")
            page = context.new_page()
            page.goto(spec["login_url"])
            typer.prompt(
                f"Complete the {spec['label']} login in the browser, then press Enter",
                default="", show_default=False,
            )
            page.close()
            ok = session_valid(context, name)
            mark = "[green]session valid[/green]" if ok else "[red]still not logged in[/red]"
            console.print(f"  {mark} {spec['label']}")
        console.print("\n[bold]Final session status[/bold]")
        for name, ok in check_all(context).items():
            mark = "[green]valid[/green]" if ok else "[red]not logged in[/red]"
            console.print(f"  {mark} {SERVICES[name]['label']}")


@app.command()
def fetch(
    scraper: str = typer.Argument(..., help="x-bookmarks | reddit | substack | hn | apple-notes | all"),
    headed: bool = typer.Option(False, "--headed", help="Show the browser window"),
    cdp: Optional[int] = typer.Option(None, "--cdp", help="Attach to real Chrome via CDP port"),
) -> None:
    """Run authenticated scrapers. X bookmarks defaults to headed."""
    from km.db import get_db
    from km.scrapers.base import CleanStop
    from km.scrapers.session import browser_context

    cfg = _cfg()
    conn = get_db(cfg.db_path)

    if scraper == "apple-notes":
        from km.sources.apple_notes import NotesAccessError, sync_notes

        try:
            with console.status("syncing Apple Notes (first run can take minutes)..."):
                synced, locked = sync_notes(conn, cfg)
            console.print(
                f"[green]{synced} notes synced[/green]"
                + (f", {locked} locked/unreadable skipped" if locked else "")
            )
            console.print("Tip: schedule this with: km notes-schedule")
        except NotesAccessError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
        return

    def build(name: str, context):
        from km.scrapers.hn import HnScraper
        from km.scrapers.reddit_saved import RedditScraper
        from km.scrapers.substack_saved import SubstackScraper
        from km.scrapers.x_bookmarks import XBookmarksScraper

        return {
            "x-bookmarks": XBookmarksScraper,
            "reddit": RedditScraper,
            "substack": SubstackScraper,
            "hn": HnScraper,
        }[name](conn, cfg, context)

    names = ["hn", "reddit", "substack", "x-bookmarks"] if scraper == "all" else [scraper]
    if any(n not in ("x-bookmarks", "reddit", "substack", "hn") for n in names):
        console.print(f"[red]unknown scraper: {scraper}[/red]")
        raise typer.Exit(1)
    if scraper == "all":
        try:
            from km.sources.apple_notes import NotesAccessError, sync_notes

            synced, _ = sync_notes(conn, cfg)
            console.print(f"[green]apple-notes: {synced} synced[/green]")
        except Exception as exc:
            console.print(f"[yellow]apple-notes skipped: {exc}[/yellow]")

    # X is aggressive about automation: default that one to headed
    use_headed = headed or "x-bookmarks" in names
    with browser_context(headed=use_headed, cdp_port=cdp) as context:
        for name in names:
            console.print(f"\n[bold]Fetching {name}...[/bold]")
            s = build(name, context)
            try:
                count = s.run()
                console.print(f"[green]{name}: {count} items saved[/green]")
            except CleanStop as exc:
                console.print(f"[yellow]{exc}[/yellow]")


@app.command()
def extract(
    verify_fetch: bool = typer.Option(
        False, "--verify-fetch", help="Fetch essay candidates and verify with trafilatura"
    ),
    verify_limit: int = typer.Option(200, "--verify-limit", help="Max pages to verify-fetch"),
    resolve_links: bool = typer.Option(
        False, "--resolve-links", help="Resolve t.co/bit.ly short links (cached)"
    ),
) -> None:
    """Run heuristics: essay detection, threads, reading lists, interest scores."""
    from km.db import get_db
    from km.extract.essays import mark_essays, verify_fetch as run_verify
    from km.extract.reading_lists import mark_reading_lists
    from km.extract.score import compute_scores
    from km.extract.threads import mark_threads

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    if resolve_links or cfg.network.resolve_links:
        from km.resolve import resolve_tweet_links

        resolved = resolve_tweet_links(conn)
        console.print(f"[bold]Short links resolved:[/bold] {resolved}")
    buckets = mark_essays(conn, cfg.load_domains())
    console.print(f"[bold]Essay buckets:[/bold] {buckets}")
    threads = mark_threads(conn)
    console.print(f"[bold]Threads marked:[/bold] {threads}")
    lists_marked = mark_reading_lists(conn)
    console.print(f"[bold]Reading-list items:[/bold] {lists_marked}")
    scored = compute_scores(conn)
    console.print(f"[bold]Interest scores computed:[/bold] {scored}")
    from km.extract.wisdom import export_wisdom, run_wisdom_pass

    wisdom = run_wisdom_pass(conn)
    console.print(f"[bold]Wisdom heuristics:[/bold] {wisdom}")
    exported = export_wisdom(conn, cfg.exports_dir / "wisdom.md")
    console.print(f"[bold]wisdom.md:[/bold] {exported} entries")
    if verify_fetch:
        results = run_verify(conn, cfg, limit=verify_limit)
        console.print(f"[bold]Verify fetch:[/bold] {results}")


@app.command()
def classify(
    no_ai: bool = typer.Option(False, "--no-ai", help="Skip all API calls"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Classify at most N items"),
    model: Optional[str] = typer.Option(None, "--model", help="Override the Claude model"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the cost confirmation prompt"),
) -> None:
    """Categorize liked/retweeted/bookmarked tweets with the Claude API."""
    from km.classify.client import estimate_cost, get_client
    from km.classify.passes import pending_items, run_pass
    from km.classify.tweet_categories import TWEET_PASS
    from km.db import get_db

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    if no_ai:
        console.print("[yellow]--no-ai set, nothing to do.[/yellow]")
        return
    use_model = model or cfg.classification.model
    rows = pending_items(conn, TWEET_PASS, limit)
    if not rows:
        console.print("[green]Nothing to classify (all items cached for this prompt version).[/green]")
        return
    texts = [(r["text"] or "")[:1000] for r in rows]
    estimate = estimate_cost(
        texts, prompt_overhead_chars=len(TWEET_PASS.system_prompt) + 400,
        model=use_model, batch_size=cfg.classification.batch_size,
    )
    console.print(f"[bold]Estimate:[/bold] {estimate.describe()} (model {use_model})")
    if not yes and not typer.confirm("Proceed with the paid classification run?"):
        console.print("Aborted, nothing was sent.")
        return
    if not cfg.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY not set (put it in .env).[/red]")
        raise typer.Exit(1)
    client = get_client()
    try:
        with console.status("classifying..."):
            result = run_pass(
                conn, client, TWEET_PASS, use_model,
                batch_size=cfg.classification.batch_size, limit=limit,
                progress=lambda done, total: console.print(f"  {done}/{total}", end="\r"),
            )
    except Exception as exc:  # API errors: report cleanly, progress is committed per batch
        message = str(exc)
        if "credit balance" in message:
            console.print(
                "[red]Anthropic API says the credit balance is too low.[/red] "
                "Add credits at console.anthropic.com (Plans & Billing), then re-run; "
                "completed batches are cached and will not be re-sent."
            )
        else:
            console.print(f"[red]Classification stopped:[/red] {message}")
        console.print("Progress so far is saved; re-running resumes where it left off.")
        raise typer.Exit(1)
    console.print(
        f"[green]{result.classified} classified[/green], "
        f"{result.failed} fell back to 'other', {result.batches} batches"
    )


@app.command()
def mentor(
    persona: str = typer.Option("harsh", "--persona", help="harsh | analyst"),
    model: Optional[str] = typer.Option(None, "--model"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the cost confirmation"),
) -> None:
    """AI reading of your whole archive: psychoanalyst or harsh mentor."""
    from km.classify.client import get_client
    from km.classify.mentor import PERSONAS, estimate_pack_cost, run_mentor
    from km.db import get_db

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    if persona not in PERSONAS:
        console.print(f"[red]persona must be one of: {', '.join(PERSONAS)}[/red]")
        raise typer.Exit(1)
    use_model = model or cfg.classification.model
    estimate = estimate_pack_cost(conn, use_model)
    console.print(
        f"[bold]Evidence pack:[/bold] ~{estimate.est_input_tokens:,} input tokens, "
        f"estimated cost ${estimate.est_dollars:.2f} (model {use_model})"
    )
    if not yes and not typer.confirm(f"Send the pack to Claude as the {persona} persona?"):
        console.print("Aborted, nothing was sent.")
        return
    if not cfg.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY not set (put it in .env).[/red]")
        raise typer.Exit(1)
    try:
        with console.status(f"the {persona} is reading your archive..."):
            reading = run_mentor(conn, get_client(), use_model, persona)
    except Exception as exc:
        message = str(exc)
        if "credit balance" in message:
            console.print("[red]Anthropic API credit balance too low; add credits and re-run.[/red]")
        else:
            console.print(f"[red]Mentor run failed:[/red] {message}")
        raise typer.Exit(1)
    from datetime import date

    out = cfg.exports_dir / f"mentor-{persona}-{date.today().isoformat()}.md"
    out.write_text(reading + "\n")
    console.print(reading)
    console.print(f"\n[green]Saved to {out}[/green]")


@app.command()
def talk(
    persona: str = typer.Option("companion", "--persona",
                                help="companion | analyst | harsh | therapist | secretary | future"),
    model: Optional[str] = typer.Option(None, "--model"),
    new: bool = typer.Option(False, "--new", help="Start a fresh session instead of resuming"),
) -> None:
    """Talk with an AI that has read your whole archive. Sessions persist."""
    from km.classify.client import get_client
    from km.classify.talk import (
        TALK_PERSONAS, build_system, latest_session, load_history, save_session, talk_turn,
    )
    from km.db import get_db

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    if persona not in TALK_PERSONAS:
        console.print(f"[red]persona must be one of: {', '.join(TALK_PERSONAS)}[/red]")
        raise typer.Exit(1)
    if not cfg.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY not set (put it in .env).[/red]")
        raise typer.Exit(1)

    use_model = model or cfg.classification.model
    session_path = None if new else latest_session(cfg.data_dir, persona)
    messages = load_history(session_path) if session_path else []
    if messages:
        console.print(f"[dim]resuming session with {len(messages) // 2} prior exchanges "
                      f"({session_path.name}); --new starts fresh[/dim]")

    console.print(f"[bold]km talk[/bold] · {persona} persona · they have read your archive.")
    console.print("[dim]Type your message; 'exit' ends and saves. First reply may take a moment.[/dim]\n")
    system = build_system(conn, persona)
    client = get_client()

    while True:
        try:
            user_input = console.input("[bold]you ›[/bold] ").strip()
        except (KeyboardInterrupt, EOFError):
            user_input = "exit"
        if user_input.lower() in ("exit", "quit", "q"):
            if messages:
                path = save_session(cfg.data_dir, persona, session_path, messages)
                console.print(f"\n[green]Session saved:[/green] {path.with_suffix('.md')}")
            break
        if not user_input:
            continue
        messages.append({"role": "user", "content": user_input})
        try:
            with console.status("..."):
                reply = talk_turn(client, use_model, system, messages)
        except Exception as exc:
            message = str(exc)
            messages.pop()
            if "credit balance" in message:
                console.print("[red]API credit balance too low; add credits and re-run.[/red]")
                break
            console.print(f"[red]{message[:200]}[/red]")
            continue
        messages.append({"role": "assistant", "content": reply})
        console.print(f"\n{reply}\n")
        session_path = save_session(cfg.data_dir, persona, session_path, messages)


@app.command(name="notes-schedule")
def notes_schedule() -> None:
    """Install a launchd job that syncs Apple Notes every 6 hours."""
    import subprocess
    from pathlib import Path as P

    cfg = _cfg()
    plist_path = P.home() / "Library/LaunchAgents/com.km.apple-notes-sync.plist"
    km_bin = cfg.project_root / ".venv" / "bin" / "km"
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.km.apple-notes-sync</string>
  <key>ProgramArguments</key>
  <array><string>{km_bin}</string><string>fetch</string><string>apple-notes</string></array>
  <key>WorkingDirectory</key><string>{cfg.project_root}</string>
  <key>StartInterval</key><integer>21600</integer>
  <key>StandardOutPath</key><string>/tmp/km-notes-sync.log</string>
  <key>StandardErrorPath</key><string>/tmp/km-notes-sync.log</string>
</dict></plist>
"""
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist)
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    result = subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True, text=True)
    if result.returncode == 0:
        console.print(f"[green]Installed:[/green] Apple Notes sync every 6 hours ({plist_path})")
        console.print("Remove with: launchctl unload " + str(plist_path))
    else:
        console.print(f"[red]launchctl load failed:[/red] {result.stderr}")


@app.command()
def export() -> None:
    """Regenerate all Markdown exports in exports/."""
    from km.db import get_db
    from km.exporters.markdown import export_all

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    written = export_all(conn, cfg.exports_dir)
    for path in written:
        console.print(f"[green]wrote[/green] {path.relative_to(cfg.project_root)}")


@app.command()
def embed(
    model: Optional[str] = typer.Option(None, "--model", help="Override the embedding model"),
) -> None:
    """Embed all un-embedded items locally (incremental, cached)."""
    from km.db import get_db
    from km.embedding.embedder import get_embedder
    from km.embedding.store import embed_pending

    cfg = _cfg()
    if model:
        cfg.embedding.model = model
    conn = get_db(cfg.db_path)
    console.print(f"Loading embedding model {cfg.embedding.model} (downloads once)...")
    embedder = get_embedder(cfg)
    count = embed_pending(
        conn, embedder,
        progress=lambda done, total: console.print(f"  {done}/{total} chunks", end="\r"),
    )
    console.print(f"\n[green]{count} chunks embedded.[/green]")


def _parse_filters(category, source, domain, kind=None):
    from km.search.keyword import Filters

    return Filters(category=category, source=source, domain=domain, kind=kind)


@app.command()
def search(
    query: str = typer.Argument(...),
    category: Optional[str] = typer.Option(None, "--category"),
    source: Optional[str] = typer.Option(None, "--source"),
    domain: Optional[str] = typer.Option(None, "--domain"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """Keyword (FTS5) search. Supports site:/kind:/cat:/before:/after: operators."""
    from km.db import get_db
    from km.search.hybrid import fetch_results
    from km.search.keyword import keyword_search, parse_query

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    query, filters = parse_query(query, _parse_filters(category, source, domain))
    if not query.strip():
        console.print("[yellow]Only operators given; add some words to search for.[/yellow]")
        raise typer.Exit(1)
    scored = keyword_search(conn, query, filters, limit)
    _print_results(fetch_results(conn, [(i, -r) for i, r in scored]))


@app.command()
def ask(
    query: str = typer.Argument(...),
    ai: bool = typer.Option(False, "--ai", help="Re-rank candidates with Claude"),
    k: int = typer.Option(20, "--k"),
    category: Optional[str] = typer.Option(None, "--category"),
    source: Optional[str] = typer.Option(None, "--source"),
    domain: Optional[str] = typer.Option(None, "--domain"),
    model: Optional[str] = typer.Option(None, "--model", help="Claude model for --ai"),
) -> None:
    """Semantic/hybrid search; use --ai to find half-remembered things."""
    from km.db import get_db
    from km.search.hybrid import fetch_results, hybrid_search

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    embedder = None
    try:
        from km.embedding.embedder import get_embedder

        embedder = get_embedder(cfg)
    except RuntimeError as exc:
        console.print(f"[yellow]{exc}; keyword-only retrieval.[/yellow]")

    pool = 50 if ai else k
    scored = hybrid_search(
        conn, query, embedder, _parse_filters(category, source, domain),
        k=pool, candidate_pool=100,
    )
    results = fetch_results(conn, scored)

    if ai and results:
        from km.classify.client import get_client
        from km.search.rerank import rerank

        if not cfg.anthropic_api_key:
            console.print("[red]--ai needs ANTHROPIC_API_KEY in .env[/red]")
            raise typer.Exit(1)
        picks = rerank(get_client(), model or cfg.classification.model, query, results)
        if picks:
            console.print("[bold]Claude's picks:[/bold]")
            for pick in picks:
                console.print(f"\n[bold cyan]{pick['title'] or pick['snippet'][:80]}[/bold cyan]")
                console.print(f"  {pick['url']}")
                console.print(f"  [italic]{pick['reasoning']}[/italic]")
            return
        console.print("[yellow]Claude found no strong match; hybrid results:[/yellow]")
    _print_results(results[:k])


def _print_results(results: list[dict]) -> None:
    if not results:
        console.print("[yellow]No results.[/yellow]")
        return
    for r in results:
        label = r["title"] or (r["snippet"][:80] if r["snippet"] else r["url"])
        cat = f" [{r['category']}]" if r.get("category") else ""
        console.print(f"[bold]{label}[/bold]{cat} [dim]({r['kind']}, {', '.join(r['sources'])})[/dim]")
        if r["snippet"] and r["title"]:
            console.print(f"  {r['snippet'][:160]}")
        console.print(f"  [blue]{r['url'] or ''}[/blue] [dim]{(r['created_at'] or '')[:10]}[/dim]")


@app.command()
def random(
    category: Optional[str] = typer.Option(None, "--category"),
) -> None:
    """Resurface one random item for recall practice."""
    from km.db import get_db
    from km.search.hybrid import fetch_results

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    where = ""
    params: list = []
    if category:
        where = """WHERE i.id IN (
            SELECT c.item_id FROM classifications c
            LEFT JOIN user_edits u ON u.item_id = c.item_id
            WHERE coalesce(u.category_override, c.category) = ?)"""
        params.append(category)
    row = conn.execute(
        f"SELECT id FROM items i {where} ORDER BY RANDOM() LIMIT 1", params
    ).fetchone()
    if not row:
        console.print("[yellow]No items yet.[/yellow]")
        return
    _print_results(fetch_results(conn, [(row["id"], 1.0)]))


@app.command()
def ui(
    port: int = typer.Option(8765, "--port"),
) -> None:
    """Serve the local web UI on 127.0.0.1."""
    try:
        import uvicorn
    except ImportError:
        console.print("[red]web UI needs the web extras: uv sync --extra web[/red]")
        raise typer.Exit(1)
    from km.web.server import create_app

    cfg = _cfg()
    console.print(f"[bold]km ui[/bold] at http://127.0.0.1:{port}")
    import threading
    import webbrowser

    threading.Timer(1.2, webbrowser.open, [f"http://127.0.0.1:{port}"]).start()
    uvicorn.run(create_app(cfg), host="127.0.0.1", port=port, log_level="warning")


@app.command()
def sync(
    scrape: bool = typer.Option(False, "--scrape", help="Also run authenticated scrapers (opens a browser)"),
    embed_new: bool = typer.Option(True, "--embed/--no-embed", help="Embed new items after ingest"),
) -> None:
    """Continuous ingestion: pull fresh data from every local source, one pass.

    Re-ingests live Chrome history, auto-ingests newly discovered export
    files of recognized types (generic sniffed files still need manual
    approval), syncs Apple Notes, optionally runs scrapers, embeds what's
    new, and refreshes heuristics. Schedule it with km sync-schedule.
    """
    from datetime import datetime, timezone

    from km.db import get_db
    from km.discover.scanner import scan_chrome_live, scan_roots, scan_safari_live
    from km.ingest import ingest_manifest
    from km.models import Manifest

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    stamp = datetime.now(timezone.utc).isoformat()
    new_items = 0

    console.print("[bold]km sync[/bold] · live browser history (Chrome + Safari incl. iPhone)")
    live = scan_chrome_live() + [e for e in scan_safari_live() if e.status == "ready"]
    if live:
        report = ingest_manifest(conn, Manifest(generated_at=stamp, entries=live), cfg)
        new_items += report.total_items
        console.print(f"  {report.total_items:,} new items from {len(report.ingested)} profile(s)")

    console.print("[bold]km sync[/bold] · new export files on disk")
    found = scan_roots(cfg)
    ready = [e for e in found if e.status == "ready" and e.source_type != "generic"]
    if ready:
        report = ingest_manifest(conn, Manifest(generated_at=stamp, entries=ready), cfg)
        new_items += report.total_items
        fresh = [p for p, n in report.ingested if n > 0]
        if fresh:
            console.print(f"  {report.total_items:,} new items from {len(fresh)} new file(s)")
        else:
            console.print("  nothing new")
    generic = [e for e in found if e.source_type == "generic"]
    if generic:
        console.print(f"  [yellow]{len(generic)} generic file(s) still need manual approval (km ingest --include-generic)[/yellow]")

    console.print("[bold]km sync[/bold] · Apple Notes")
    try:
        from km.sources.apple_notes import sync_notes

        synced, locked = sync_notes(conn, cfg)
        console.print(f"  {synced} notes synced" + (f", {locked} locked skipped" if locked else ""))
    except Exception as exc:
        console.print(f"  [yellow]skipped: {exc}[/yellow]")

    console.print("[bold]km sync[/bold] · reading feed")
    try:
        from km.feed import build_daily_feed, refresh_feeds

        feed_stats = refresh_feeds(conn)
        built = build_daily_feed(conn)
        console.print(f"  {feed_stats['new_posts']} fresh posts, today's feed has {built} pieces")
    except Exception as exc:
        console.print(f"  [yellow]skipped: {exc}[/yellow]")

    if scrape:
        from km.scrapers.base import CleanStop
        from km.scrapers.session import browser_context

        console.print("[bold]km sync[/bold] · scrapers")
        try:
            with browser_context(headed=True) as context:
                from km.scrapers.hn import HnScraper
                from km.scrapers.reddit_saved import RedditScraper
                from km.scrapers.substack_saved import SubstackScraper
                from km.scrapers.x_bookmarks import XBookmarksScraper

                for cls in (HnScraper, RedditScraper, SubstackScraper, XBookmarksScraper):
                    try:
                        count = cls(conn, cfg, context).run()
                        new_items += count
                        console.print(f"  {cls.__name__}: {count} saved")
                    except CleanStop as exc:
                        console.print(f"  [yellow]{cls.__name__}: {exc}[/yellow]")
        except Exception as exc:
            console.print(f"  [yellow]scrapers skipped: {exc}[/yellow]")

    if embed_new:
        console.print("[bold]km sync[/bold] · embeddings")
        try:
            from km.embedding.embedder import get_embedder
            from km.embedding.store import embed_pending

            count = embed_pending(conn, get_embedder(cfg))
            console.print(f"  {count} new chunks embedded")
        except Exception as exc:
            console.print(f"  [yellow]skipped: {exc}[/yellow]")

    console.print("[bold]km sync[/bold] · heuristics")
    from km.extract.essays import mark_essays
    from km.extract.reading_lists import mark_reading_lists
    from km.extract.score import compute_scores
    from km.extract.threads import mark_threads
    from km.extract.wisdom import run_wisdom_pass

    mark_essays(conn, cfg.load_domains())
    mark_threads(conn)
    mark_reading_lists(conn)
    compute_scores(conn)
    run_wisdom_pass(conn)
    console.print(f"\n[green]sync complete:[/green] {new_items:,} new items this pass")


@app.command()
def feed(
    refresh: bool = typer.Option(False, "--refresh", help="Probe/fetch RSS feeds first (network)"),
) -> None:
    """Today's reading feed: new posts from blogs you follow + buried gems."""
    from km.db import get_db
    from km.feed import build_daily_feed, get_daily_feed, refresh_feeds

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    if refresh:
        with console.status("probing feeds on your recurring domains..."):
            stats = refresh_feeds(conn)
        console.print(
            f"[dim]{stats['discovered']} feeds discovered, {stats['fetched']} fetched, "
            f"{stats['new_posts']} fresh posts[/dim]")
    build_daily_feed(conn)
    entries = get_daily_feed(conn)
    if not entries:
        console.print("[yellow]Feed is empty; try km feed --refresh after some ingestion.[/yellow]")
        return
    console.print(f"[bold]Today's reading · {len(entries)} pieces[/bold]\n")
    lines = ["# Today's reading", ""]
    for entry in entries:
        mark = "[green]✓[/green] " if entry["read"] else "  "
        title = entry["title"] or (entry["text"] or "")[:80] or entry["url"]
        console.print(f"{mark}[bold]{title[:90]}[/bold]  [dim]({entry['reason']})[/dim]")
        if entry["url"]:
            console.print(f"   [dim]{entry['url']}[/dim]")
        lines.append(f"- [{title}]({entry['url']}) · {entry['reason']}")
    from datetime import datetime, timezone

    out = cfg.exports_dir / f"feed-{datetime.now(timezone.utc).date().isoformat()}.md"
    out.write_text("\n".join(lines) + "\n")


@app.command(name="discover-similar")
def discover_similar(
    target: str = typer.Argument(..., help="Item id (from km search) or a URL"),
    k: int = typer.Option(8, "--k"),
    ai: bool = typer.Option(False, "--ai", help="Let Claude browse the web (needs credits)"),
    ingest: bool = typer.Option(True, "--ingest/--no-ingest", help="Save picks into the archive"),
) -> None:
    """Find essays like this one, out on the live web.

    Local mode ranks the essay's link neighborhood with your embeddings;
    --ai has Claude search the web and explain each pick."""
    from km.db import get_db
    from km.discover_web import discover_ai, discover_local, ingest_discoveries

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    if target.startswith("http"):
        row = conn.execute("SELECT id FROM items WHERE url=? OR canonical_url=?",
                           (target, target)).fetchone()
        if not row:
            from km.models import NormalizedItem
            from km.store import add_source, upsert_item
            from km.urls import canonicalize

            sid, _ = add_source(conn, "manual", "cli", "manual")
            item_id = upsert_item(conn, NormalizedItem(
                kind="linked", dedupe_key=f"url:{canonicalize(target)}", url=target), sid)
        else:
            item_id = row["id"]
    else:
        item_id = int(target)
    strategy = "ai" if ai else "local"
    try:
        with console.status("reading the essay and walking its neighborhood..."
                            if not ai else "Claude is browsing..."):
            picks = (discover_ai(conn, cfg, item_id, k) if ai
                     else discover_local(conn, cfg, item_id, k))
    except Exception as exc:
        if "credit balance" in str(exc):
            console.print("[red]Anthropic API credit balance too low.[/red]")
        else:
            console.print(f"[red]discovery failed:[/red] {exc}")
        raise typer.Exit(1)
    if not picks:
        console.print("[yellow]Nothing similar found; try --ai or a richer essay.[/yellow]")
        return
    for pick in picks:
        sim = f" · {pick['similarity']:.2f}" if pick.get("similarity") else ""
        console.print(f"[bold]{pick['title'][:90]}[/bold]{sim}")
        if pick.get("why"):
            console.print(f"  [dim]{pick['why']}[/dim]")
        console.print(f"  [dim]{pick['url']}[/dim]")
    if ingest:
        saved = ingest_discoveries(conn, item_id, picks, strategy)
        console.print(f"\n[green]{saved} discoveries saved[/green]; they can appear in your feed.")


@app.command()
def enrich() -> None:
    """Grow the reading pool: probe the seed-blog canon for feeds and mine
    curated lists-of-lists (nabeelqu, Collison, guzey...) into your essays."""
    from km.db import get_db
    from km.extract.essays import mark_essays
    from km.extract.score import compute_scores
    from km.feed import enrich_from_curated_lists, refresh_feeds

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    with console.status("mining curated reading lists..."):
        stats = enrich_from_curated_lists(conn)
    console.print(f"[green]{stats['links']} links[/green] mined from {stats['pages']} curated list pages")
    with console.status("probing seed blogs for feeds..."):
        feed_stats = refresh_feeds(conn, max_new_probes=40)
    console.print(
        f"[green]{feed_stats['discovered']} feeds discovered[/green], "
        f"{feed_stats['new_posts']} fresh posts pulled")
    mark_essays(conn, cfg.load_domains())
    compute_scores(conn)
    console.print("Essay heuristics refreshed. Your feed just got a much deeper bench.")


task_app = typer.Typer(help="The lock-in list: what you said you'd do.")
app.add_typer(task_app, name="task")


@task_app.command("add")
def task_add(
    text: str = typer.Argument(...),
    due: Optional[str] = typer.Option(None, "--due", help="YYYY-MM-DD"),
) -> None:
    """Add a task."""
    from km.db import get_db
    from km.taskdriver import add_task

    cfg = _cfg()
    task_id = add_task(get_db(cfg.db_path), text, due)
    console.print(f"[green]#{task_id} added[/green]" + (f" · due {due}" if due else ""))


@task_app.command("list")
def task_list() -> None:
    """List open tasks, overdue first."""
    from km.db import get_db
    from km.taskdriver import list_tasks

    cfg = _cfg()
    tasks = list_tasks(get_db(cfg.db_path))
    if not tasks:
        console.print("Nothing open. Suspicious.")
        return
    for t in tasks:
        flag = "[red]OVERDUE[/red] " if t["overdue"] else ("[yellow]today[/yellow] " if t["due_today"] else "")
        due = f" · due {t['due']}" if t["due"] else ""
        console.print(f"  #{t['id']} {flag}{t['text']}{due} [dim]({t['source']})[/dim]")


@task_app.command("done")
def task_done(task_id: int = typer.Argument(...)) -> None:
    """Mark a task done."""
    from km.db import get_db
    from km.taskdriver import set_status

    set_status(get_db(_cfg().db_path), task_id, "done")
    console.print(f"[green]#{task_id} done.[/green]")


@task_app.command("harvest")
def task_harvest() -> None:
    """Mine TODO and checkbox lines out of your Apple Notes into tasks."""
    from km.db import get_db
    from km.taskdriver import harvest_from_notes

    added = harvest_from_notes(get_db(_cfg().db_path))
    if not added:
        console.print("No new TODOs found in notes.")
        return
    for a in added:
        console.print(f"  [green]+[/green] {a['text']} [dim]({a['source']})[/dim]")
    console.print(f"[green]{len(added)} task(s) harvested.[/green]")


@app.command(name="sync-schedule")
def sync_schedule(
    hours: int = typer.Option(12, "--hours", help="Run every N hours"),
) -> None:
    """Install a launchd job that runs km sync on a schedule. The archive
    keeps itself current: Chrome history, new exports, Apple Notes, embeddings."""
    import subprocess
    from pathlib import Path as P

    cfg = _cfg()
    plist_path = P.home() / "Library/LaunchAgents/com.km.sync.plist"
    km_bin = cfg.project_root / ".venv" / "bin" / "km"
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.km.sync</string>
  <key>ProgramArguments</key>
  <array><string>{km_bin}</string><string>sync</string></array>
  <key>WorkingDirectory</key><string>{cfg.project_root}</string>
  <key>StartInterval</key><integer>{hours * 3600}</integer>
  <key>StandardOutPath</key><string>/tmp/km-sync.log</string>
  <key>StandardErrorPath</key><string>/tmp/km-sync.log</string>
</dict></plist>
"""
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist)
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    result = subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True, text=True)
    if result.returncode == 0:
        console.print(f"[green]Installed:[/green] km sync every {hours}h ({plist_path})")
        console.print("Log: /tmp/km-sync.log · remove with: launchctl unload " + str(plist_path))
    else:
        console.print(f"[red]launchctl load failed:[/red] {result.stderr}")


@app.command()
def reflect(
    days: int = typer.Option(30, "--days", help="How many recent days to read"),
    model: Optional[str] = typer.Option(None, "--model"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the cost confirmation"),
) -> None:
    """AI reflection on your recent traces: what preoccupied you, what you missed."""
    from km.classify.client import get_client
    from km.classify.reflect import estimate_reflect_cost, gather_recent, run_reflect
    from km.db import get_db

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    use_model = model or cfg.classification.model
    pack = gather_recent(conn, days)
    cost = estimate_reflect_cost(pack)
    console.print(
        f"[bold]Reflection on the last {days} days:[/bold] "
        f"{len(pack['searches'])} searches, {len(pack['notes'])} notes, "
        f"{len(pack['chats'])} chats · rough cost ${cost:.2f} ({use_model})"
    )
    if not yes and not typer.confirm("Send these traces (text only) for reflection?"):
        console.print("Aborted, nothing was sent.")
        return
    if not cfg.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY not set (put it in .env).[/red]")
        raise typer.Exit(1)
    try:
        with console.status("reading your last few weeks..."):
            text = run_reflect(conn, get_client(), use_model, days)
    except Exception as exc:
        if "credit balance" in str(exc):
            console.print("[red]Anthropic API credit balance too low; add credits and re-run.[/red]")
        else:
            console.print(f"[red]Reflection failed:[/red] {exc}")
        raise typer.Exit(1)
    from datetime import datetime, timezone

    out_dir = cfg.exports_dir / "reflections"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"reflection-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.md"
    out.write_text(f"# Reflection · last {days} days\n\n{text}\n")
    console.print()
    console.print(text)
    console.print(f"\n[green]saved[/green] {out}")


@app.command(name="digest-schedule")
def digest_schedule(
    hour: int = typer.Option(9, "--hour", help="Local hour (0-23) for the daily digest"),
) -> None:
    """Install a launchd job posting the daily digest as a macOS notification."""
    import subprocess
    from pathlib import Path as P

    cfg = _cfg()
    plist_path = P.home() / "Library/LaunchAgents/com.km.daily-digest.plist"
    km_bin = cfg.project_root / ".venv" / "bin" / "km"
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.km.daily-digest</string>
  <key>ProgramArguments</key>
  <array><string>{km_bin}</string><string>digest</string><string>--notify</string></array>
  <key>WorkingDirectory</key><string>{cfg.project_root}</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>{hour}</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/tmp/km-digest.log</string>
  <key>StandardErrorPath</key><string>/tmp/km-digest.log</string>
</dict></plist>
"""
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist)
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    result = subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True, text=True)
    if result.returncode == 0:
        console.print(f"[green]Installed:[/green] daily digest notification at {hour:02d}:00 ({plist_path})")
    else:
        console.print(f"[red]launchctl load failed:[/red] {result.stderr}")


@app.command()
def digest(
    notify: bool = typer.Option(False, "--notify", help="Also post a macOS notification"),
) -> None:
    """Today's memory mix: on-this-day items plus resurfaced gems."""
    from km.db import get_db
    from km.extract.reports import daily_digest

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    d = daily_digest(conn)
    if notify:
        import subprocess

        first = (d["on_this_day"][0]["label"][:120] if d["on_this_day"]
                 else (d["gems"][0]["title"][:120] if d["gems"] else "your archive is quiet today"))
        body = first.replace('"', "'").replace("\\", "")
        subprocess.run([
            "osascript", "-e",
            f'display notification "{body}" with title "km · on this day"',
        ], capture_output=True)
    console.print(f"[bold]km digest · {d['date']}[/bold]\n")
    if d["on_this_day"]:
        console.print("[bold]On this day[/bold]")
        for entry in d["on_this_day"]:
            console.print(
                f"  · {entry['years_ago']} year{'s' if entry['years_ago'] != 1 else ''} ago "
                f"({entry['kind']}): {entry['label'][:120]}"
            )
            if entry["url"]:
                console.print(f"    [dim]{entry['url']}[/dim]")
        console.print()
    for gem in d["gems"]:
        console.print(f"[bold]{gem['label'].capitalize()}[/bold]")
        console.print(f"  {gem['title'][:160]}")
        if gem["url"]:
            console.print(f"  [dim]{gem['url']}[/dim]")
        console.print()


@app.command()
def timeline() -> None:
    """Life timeline: what each month was about, plus recurring threads."""
    from km.db import get_db
    from km.extract.timeline import export_life_timeline

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    months = export_life_timeline(conn, cfg.exports_dir / "life-timeline.md")
    console.print(f"[green]wrote[/green] exports/life-timeline.md ({months} months)")
    from km.extract.timeline import export_recurring_threads

    counts = export_recurring_threads(conn, cfg.exports_dir / "recurring-threads.md")
    console.print(
        f"[green]wrote[/green] exports/recurring-threads.md "
        f"({counts['searches']} searches, {counts['domains']} domains, "
        f"{counts['chat_topics']} chat topics)"
    )


@app.command()
def themes(
    model: Optional[str] = typer.Option(None, "--model"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the cost confirmation"),
) -> None:
    """AI-label each month with life themes and name your repeated loops."""
    from km.classify.client import get_client
    from km.classify.themes import export_themes, run_themes
    from km.db import get_db
    from km.extract.timeline import compact_timeline_for_ai

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    use_model = model or cfg.classification.model
    import json as _json

    timeline_json = _json.dumps(compact_timeline_for_ai(conn))
    est_tokens = len(timeline_json) // 4 + 4000
    console.print(
        f"[bold]Timeline:[/bold] ~{est_tokens:,} input tokens across chunked calls, "
        f"rough cost ${est_tokens / 1e6 * 3 + 0.15:.2f} (model {use_model})"
    )
    if not yes and not typer.confirm("Run the themes + repeated-mistakes analysis?"):
        console.print("Aborted, nothing was sent.")
        return
    if not cfg.anthropic_api_key:
        console.print("[red]ANTHROPIC_API_KEY not set (put it in .env).[/red]")
        raise typer.Exit(1)
    try:
        with console.status("labeling your months..."):
            month_themes, mistakes = run_themes(conn, get_client(), use_model)
    except Exception as exc:
        message = str(exc)
        if "credit balance" in message:
            console.print("[red]Anthropic API credit balance too low; add credits and re-run.[/red]")
        else:
            console.print(f"[red]Themes run failed:[/red] {message}")
        raise typer.Exit(1)
    out = cfg.exports_dir / "life-themes.md"
    export_themes(month_themes, mistakes, out)
    console.print(f"[green]{len(month_themes)} months labeled; wrote {out}[/green]")


@app.command()
def reports() -> None:
    """Generate all analytics exports: obsessions, best tweets, reading debt,
    questions, rhythms."""
    from km.db import get_db
    from km.extract.curiosity import export_questions
    from km.extract.debt import export_reading_debt
    from km.extract.reports import export_best_own_tweets, export_obsessions
    from km.extract.rhythms import export_rhythms

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    years = export_obsessions(conn, cfg.exports_dir / "obsessions.md")
    console.print(f"[green]wrote[/green] exports/obsessions.md ({years} years)")
    tweets = export_best_own_tweets(conn, cfg.exports_dir / "my-best-tweets.md")
    console.print(f"[green]wrote[/green] exports/my-best-tweets.md ({tweets} tweets)")
    debt = export_reading_debt(conn, cfg.exports_dir / "reading-debt.md")
    console.print(
        f"[green]wrote[/green] exports/reading-debt.md "
        f"({debt['count']:,} unread saves, ~{debt['hours']}h to repay)"
    )
    questions = export_questions(conn, cfg.exports_dir / "questions.md")
    console.print(f"[green]wrote[/green] exports/questions.md ({questions:,} questions)")
    rhythm = export_rhythms(conn, cfg.exports_dir / "rhythms.md")
    console.print(
        f"[green]wrote[/green] exports/rhythms.md "
        f"({rhythm['timed']:,} timed traces, longest streak {rhythm['longest_streak']} days)"
    )


@app.command()
def rewind(
    year: Optional[str] = typer.Argument(None, help="Year, e.g. 2025. Default: last full year."),
) -> None:
    """A year in review: new obsessions, discovered places, notes, chats, tweets."""
    from datetime import datetime, timezone

    from km.db import get_db
    from km.extract.rewind import export_rewind, year_rewind

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    year = year or str(datetime.now(timezone.utc).year - 1)
    data = year_rewind(conn, year)
    if data["total"] == 0:
        console.print(f"[yellow]No traces found for {year}.[/yellow]")
        raise typer.Exit(1)
    out = cfg.exports_dir / f"rewind-{year}.md"
    export_rewind(conn, year, out)
    console.print(f"[bold]{year} rewind[/bold] · {data['total']:,} traces\n")
    if data["new_obsessions"]:
        console.print("[bold]New obsessions[/bold]")
        for entry in data["new_obsessions"][:10]:
            console.print(f"  · {entry['term']} ({entry['count']} searches, {entry['before']} before)")
        console.print()
    if data["new_domains"]:
        console.print("[bold]Discovered this year[/bold]")
        for entry in data["new_domains"][:10]:
            console.print(f"  · {entry['domain']} ({entry['visits']} visits)")
        console.print()
    console.print(
        f"{len(data['notes'])} notes, {len(data['chats'])} AI conversations, "
        f"{len(data['best_tweets'])} tweets that landed"
    )
    console.print(f"[green]wrote[/green] {out}")


@app.command()
def wrapped(
    year: Optional[str] = typer.Argument(None, help="Year, e.g. 2025. Default: last full year."),
    open_page: bool = typer.Option(True, "--open/--no-open", help="Open the page in your browser"),
    ai: bool = typer.Option(False, "--ai", help="Add a Claude-written closing paragraph"),
    model: Optional[str] = typer.Option(None, "--model"),
) -> None:
    """A shareable year-in-review page (like a music wrapped, but it's your mind)."""
    import webbrowser
    from datetime import datetime, timezone

    from km.db import get_db
    from km.exporters.wrapped import ai_epilogue, export_wrapped, wrapped_data

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    year = year or str(datetime.now(timezone.utc).year - 1)
    out = cfg.exports_dir / f"wrapped-{year}.html"
    epilogue = None
    if ai:
        if not cfg.anthropic_api_key:
            console.print("[red]--ai needs ANTHROPIC_API_KEY in .env.[/red]")
            raise typer.Exit(1)
        from km.classify.client import get_client

        try:
            with console.status("asking Claude what the year meant..."):
                epilogue = ai_epilogue(wrapped_data(conn, year), get_client(),
                                       model or cfg.classification.model)
        except Exception as exc:
            if "credit balance" in str(exc):
                console.print("[yellow]API credits empty; generating without the epilogue.[/yellow]")
            else:
                console.print(f"[yellow]epilogue skipped: {exc}[/yellow]")
    data = export_wrapped(conn, year, out, epilogue=epilogue)
    if data["total"] == 0:
        console.print(f"[yellow]No traces found for {year}.[/yellow]")
        raise typer.Exit(1)
    console.print(
        f"[green]wrote[/green] {out} ({data['total']:,} traces, "
        f"{len(data['new_obsessions'])} new obsessions)"
    )
    if open_page:
        webbrowser.open(out.as_uri())


@app.command()
def doctor() -> None:
    """Health check: DB integrity, FTS sync, date coverage, source freshness."""
    import os
    import shutil
    from datetime import datetime, timezone

    from km.db import get_db

    cfg = _cfg()
    problems = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal problems
        mark = "[green]ok[/green]" if ok else "[red]FAIL[/red]"
        if not ok:
            problems += 1
        console.print(f"  {mark}  {label}" + (f" [dim]({detail})[/dim]" if detail else ""))

    console.print("[bold]km doctor[/bold]\n")
    db_exists = cfg.db_path.exists()
    check("database exists", db_exists, str(cfg.db_path))
    if not db_exists:
        raise typer.Exit(1)
    size_mb = cfg.db_path.stat().st_size / 1e6
    conn = get_db(cfg.db_path)
    integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
    check("integrity (quick_check)", integrity == "ok", integrity)
    items = conn.execute("SELECT count(*) FROM items").fetchone()[0]
    fts = conn.execute("SELECT count(*) FROM items_fts").fetchone()[0]
    check("FTS index in sync", fts == items, f"{fts:,} indexed / {items:,} items")
    dated = conn.execute("SELECT count(*) FROM items WHERE created_at IS NOT NULL").fetchone()[0]
    pct = dated / items * 100 if items else 0
    check("items with dates", pct >= 80, f"{pct:.0f}% of {items:,}")
    embedded = conn.execute("SELECT count(*) FROM embedding_cache").fetchone()[0]
    check("embeddings", embedded > 0, f"{embedded:,} of {items:,} embedded"
          if embedded else "none yet; run km embed")
    check("ANTHROPIC_API_KEY set", bool(cfg.anthropic_api_key))
    free_gb = shutil.disk_usage(cfg.db_path.parent).free / 1e9
    check("disk space", free_gb >= 5, f"{free_gb:.1f} GB free")
    console.print("\n[bold]Sources[/bold]")
    now = datetime.now(timezone.utc)
    for row in conn.execute(
        """SELECT kind, max(ingested_at) last FROM sources GROUP BY kind ORDER BY kind"""
    ):
        try:
            age_days = (now - datetime.fromisoformat(row["last"])).days
            age = f"{age_days}d ago"
        except (ValueError, TypeError):
            age = "unknown"
        console.print(f"  · {row['kind']}: last ingest {age}")
    for row in conn.execute("SELECT scraper, last_run_at FROM scrape_state"):
        console.print(f"  · scraper {row['scraper']}: last run {(row['last_run_at'] or '?')[:10]}")
    console.print(
        f"\nDB {size_mb:.0f} MB, {items:,} items. "
        + ("[green]All checks passed.[/green]" if problems == 0
           else f"[red]{problems} problem(s) found.[/red]")
    )
    if problems:
        raise typer.Exit(1)


@app.command(name="expand-links")
def expand_links_cmd(
    limit: int = typer.Option(120, "--limit", help="Max list-pages to fetch"),
) -> None:
    """Links of links: mine the pages inside your saved reading lists."""
    from km.db import get_db
    from km.extract.link_expansion import expand_links, export_links_of_links

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    console.print(f"Fetching up to {limit} saved list-pages (cached, ~1/sec)...")
    results = expand_links(
        conn, cfg, limit=limit,
        progress=lambda done, total, url: console.print(f"  {done}/{total} {url[:70]}", end="\r"),
    )
    console.print(
        f"\n[green]{results['links']} links[/green] mined from {results['pages']} pages "
        f"({results['errors']} fetch errors)"
    )
    exported = export_links_of_links(conn, cfg.exports_dir / "links-of-links.md")
    console.print(f"[green]wrote[/green] exports/links-of-links.md ({exported} entries)")


@app.command(name="eval")
def eval_search(
    path: Path = typer.Option(Path("evals.yaml"), "--file", help="Eval definitions"),
    k: int = typer.Option(10, "--k", help="Target must appear in the top K"),
) -> None:
    """Score retrieval against your half-memory eval set (evals.yaml)."""
    import yaml

    from km.db import get_db
    from km.search.hybrid import fetch_results, hybrid_search
    from km.search.keyword import keyword_search

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    if not path.exists():
        console.print(f"[red]{path} not found; edit the evals.yaml template first.[/red]")
        raise typer.Exit(1)
    evals = (yaml.safe_load(path.read_text()) or {}).get("evals") or []
    if not evals:
        console.print("[yellow]No evals defined yet; add queries to evals.yaml.[/yellow]")
        return

    embedder = None
    try:
        from km.embedding.embedder import get_embedder

        embedder = get_embedder(cfg)
    except (RuntimeError, Exception):
        console.print("[dim]embeddings unavailable; hybrid runs keyword-only[/dim]")

    def hit(results: list[dict], needle: str) -> Optional[int]:
        needle = needle.lower()
        for rank, r in enumerate(results, 1):
            haystack = " ".join(
                str(r.get(field) or "") for field in ("url", "title", "snippet")
            ).lower()
            if needle in haystack:
                return rank
        return None

    keyword_hits = hybrid_hits = 0
    for entry in evals:
        query, needle = entry["query"], entry["match"]
        kw = fetch_results(conn, [(i, -s) for i, s in keyword_search(conn, query, limit=k)])
        hy = fetch_results(conn, hybrid_search(conn, query, embedder, k=k))
        kw_rank, hy_rank = hit(kw, needle), hit(hy, needle)
        keyword_hits += kw_rank is not None
        hybrid_hits += hy_rank is not None
        console.print(
            f"[bold]{query[:70]}[/bold]\n"
            f"  keyword: {'#' + str(kw_rank) if kw_rank else '[red]miss[/red]'}"
            f"   hybrid: {'#' + str(hy_rank) if hy_rank else '[red]miss[/red]'}"
        )
    total = len(evals)
    console.print(
        f"\n[bold]top-{k} hit rate:[/bold] keyword {keyword_hits}/{total}, hybrid {hybrid_hits}/{total}"
    )


@app.command()
def backup(
    dest: Optional[Path] = typer.Option(None, "--dest", help="Backup directory (default: ~/km-backups)"),
) -> None:
    """Snapshot the knowledge DB, config, and talk sessions into one zip."""
    import shutil
    import sqlite3 as sq
    import zipfile
    from datetime import datetime, timezone

    cfg = _cfg()
    dest_dir = (dest or Path.home() / "km-backups").expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    out = dest_dir / f"km-backup-{stamp}.zip"

    # consistent DB snapshot even while the UI or a fetch is running
    snapshot = cfg.data_dir / f".backup-tmp-{stamp}.db"
    source = sq.connect(cfg.db_path)
    target = sq.connect(snapshot)
    with target:
        source.backup(target)
    source.close(); target.close()

    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(snapshot, "knowledge.db")
            for name in ("config.yaml", "domains.yaml", "manifest.json"):
                path = cfg.project_root / name
                if path.exists():
                    zf.write(path, name)
            sessions = cfg.data_dir / "talk-sessions"
            if sessions.exists():
                for f in sessions.iterdir():
                    zf.write(f, f"talk-sessions/{f.name}")
            exports = cfg.exports_dir
            for f in exports.rglob("*.md"):
                zf.write(f, f"exports/{f.relative_to(exports)}")
    finally:
        snapshot.unlink(missing_ok=True)
    size_mb = out.stat().st_size / 1e6
    console.print(f"[green]Backup written:[/green] {out} ({size_mb:.1f} MB)")
    console.print("Copy it somewhere off this machine.")


@app.command()
def stats() -> None:
    """Show counts per source, top domains, and date coverage."""
    from rich.table import Table

    from km.db import get_db
    from km.store import stats as get_stats

    cfg = _cfg()
    conn = get_db(cfg.db_path)
    s = get_stats(conn)
    console.print(f"[bold]Total items:[/bold] {s['total_items']}")

    t = Table(title="Items by kind")
    t.add_column("Kind"); t.add_column("Count", justify="right")
    for kind, count in s["by_kind"].items():
        t.add_row(kind, str(count))
    console.print(t)

    t = Table(title="Items by source")
    t.add_column("Source"); t.add_column("Items", justify="right"); t.add_column("Date coverage")
    for kind, count in s["by_source_kind"].items():
        lo_hi = s["date_coverage"].get(kind)
        coverage = f"{lo_hi[0][:10]} to {lo_hi[1][:10]}" if lo_hi and lo_hi[0] else ""
        t.add_row(kind, str(count), coverage)
    console.print(t)

    if s["top_domains"]:
        t = Table(title="Top 30 domains")
        t.add_column("Domain"); t.add_column("Items", justify="right")
        for domain, count in s["top_domains"]:
            t.add_row(domain, str(count))
        console.print(t)

    if s["categories"]:
        t = Table(title="Tweet categories")
        t.add_column("Category"); t.add_column("Count", justify="right")
        for cat, count in s["categories"].items():
            t.add_row(cat, str(count))
        console.print(t)


if __name__ == "__main__":
    app()
