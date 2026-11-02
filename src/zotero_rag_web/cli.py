"""`zrw`: the operator's command line. The web server is started by uvicorn.

`index` is what the platform runs on a schedule; its exit code tells the platform
whether the run needs attention.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Annotated, Any

import psycopg
import typer
from alembic import command
from alembic.config import Config as AlembicConfig
from pydantic import ValidationError

from zotero_rag_web import __version__, logs
from zotero_rag_web.db import libpq_url
from zotero_rag_web.settings import MissingSettingError, Settings

#: `zrw index` exit codes, for the platform's job history.
EXIT_OK, EXIT_ERROR, EXIT_ATTENTION = 0, 1, 3

app = typer.Typer(help="Operate a zotero-rag-web deployment.", no_args_is_help=True)


def _settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        typer.echo(f"Invalid configuration:\n{exc}", err=True)
        raise typer.Exit(code=2) from exc


def alembic_config(database_url: str) -> AlembicConfig:
    """Alembic's configuration, pointing at the migrations inside this package."""
    config = AlembicConfig()
    config.set_main_option("script_location", str(resources.files("zotero_rag_web") / "migrations"))
    # Alembic interpolates %(name)s in option values; a password may contain '%'.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@app.command()
def migrate() -> None:
    """Create or upgrade the database schema. Safe to run on every deploy."""
    settings = _settings()
    try:
        settings.require("database")
    except MissingSettingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    command.upgrade(alembic_config(settings.database_url), "head")
    typer.echo("Database schema is up to date.")


@app.command("config")
def show_config() -> None:
    """Print the effective settings, credentials reduced to whether they are set."""
    typer.echo(json.dumps(_settings().redacted(), indent=2, default=str))


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(f"zotero-rag-web {__version__}")


def _require(settings: Settings, *names: Any) -> None:
    try:
        settings.require(*names)
    except MissingSettingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc


@app.command()
def index(
    full: Annotated[
        bool, typer.Option("--full", help="Re-plan even if the library version is unchanged.")
    ] = False,
    allow_mass_removal: Annotated[
        bool,
        typer.Option(
            "--allow-mass-removal",
            help="Remove attachments gone from Zotero even beyond PRUNE_MAX_FRACTION.",
        ),
    ] = False,
    trigger: Annotated[str, typer.Option(help="Recorded with the run: cron, manual, ...")] = (
        "manual"
    ),
) -> None:
    """Sync the Zotero group into the database. Exit 0 ok, 1 error, 3 needs attention."""
    from zotero_rag_web.indexing.sync import run_sync

    settings = _settings()
    _require(settings, "database", "zotero", "openrouter")
    logs.configure(settings.log_level, settings.log_format)
    report = run_sync(settings, trigger=trigger, full=full, allow_mass_removal=allow_mass_removal)
    typer.echo(json.dumps({"status": report.status, **report.stats()}, indent=2))
    if report.status == "error":
        raise typer.Exit(code=EXIT_ERROR)
    if report.status == "needs_attention":
        raise typer.Exit(code=EXIT_ATTENTION)


@app.command()
def reindex(
    repair: Annotated[
        bool,
        typer.Option("--repair", help="Re-embed the stored text after changing EMBEDDING_MODEL."),
    ] = False,
    rechunk: Annotated[
        bool,
        typer.Option(
            "--rechunk", help="Rebuild every chunk after changing CHUNK_SIZE or CHUNK_OVERLAP."
        ),
    ] = False,
) -> None:
    """Rebuild the index after a settings change the fingerprint refuses."""
    from zotero_rag_core.embedding import build_embeddings
    from zotero_rag_core.fingerprint import StoreFingerprint
    from zotero_rag_core.indexer import repair_embeddings
    from zotero_rag_core.stores.pg import PgStore
    from zotero_rag_web.indexing import library
    from zotero_rag_web.indexing.lock import sync_lock
    from zotero_rag_web.indexing.sync import run_sync

    if repair == rechunk:
        typer.echo("Give exactly one of --repair or --rechunk.", err=True)
        raise typer.Exit(code=2)
    settings = _settings()
    _require(settings, "database", "zotero", "openrouter")
    logs.configure(settings.log_level, settings.log_format)
    url = libpq_url(settings.database_url)

    if rechunk:
        with sync_lock(url) as acquired:
            if not acquired:
                typer.echo("A sync is running; try again when it has finished.", err=True)
                raise typer.Exit(code=EXIT_ERROR)
            store = PgStore.from_url(url)
            try:
                store.reset()
            finally:
                store.close()
            with psycopg.connect(url, autocommit=True) as conn:
                library.forget_cursor(conn)
        typer.echo("Chunks removed; re-indexing the whole library.")
        report = run_sync(settings, trigger="rechunk", full=True)
        typer.echo(json.dumps({"status": report.status, **report.stats()}, indent=2))
        raise typer.Exit(code=EXIT_OK if report.status == "ok" else EXIT_ERROR)

    with sync_lock(url) as acquired:
        if not acquired:
            typer.echo("A sync is running; try again when it has finished.", err=True)
            raise typer.Exit(code=EXIT_ERROR)
        config = settings.core_config
        store = PgStore.from_url(url)
        try:
            stats = repair_embeddings(
                store, build_embeddings(config).embed_documents, settings.embedding_model
            )
            store.refresh_keyword_stats()
            store.write_fingerprint(
                StoreFingerprint.intended(config, embedding_dim=store.derived_dimension() or 0)
            )
        finally:
            store.close()
    typer.echo(json.dumps(stats.model_dump(), indent=2))


@app.command()
def status() -> None:
    """What is indexed, how the last syncs went, and how the store was built."""
    settings = _settings()
    _require(settings, "database")
    with psycopg.connect(libpq_url(settings.database_url)) as conn:
        counts = conn.execute(
            "SELECT (SELECT count(*) FROM items), (SELECT count(*) FROM attachments), "
            "(SELECT count(*) FROM chunks), (SELECT count(*) FROM collections)"
        ).fetchone()
        lib = conn.execute(
            "SELECT group_id, name, last_version, last_sync_at, last_sync_status "
            "FROM library WHERE id = 1"
        ).fetchone()
        runs = conn.execute(
            "SELECT id, trigger, status, started_at, finished_at, stats->>'message' "
            "FROM index_runs ORDER BY id DESC LIMIT 5"
        ).fetchall()
        fingerprint = conn.execute("SELECT fingerprint FROM chunk_store WHERE id = 1").fetchone()
    assert counts is not None
    summary = {
        "items": counts[0],
        "attachments": counts[1],
        "chunks": counts[2],
        "collections": counts[3],
        "library": None
        if lib is None
        else dict(
            zip(("group_id", "name", "last_version", "last_sync_at", "status"), lib, strict=True)
        ),
        "recent_runs": [
            dict(
                zip(("id", "trigger", "status", "started", "finished", "message"), run, strict=True)
            )
            for run in runs
        ],
        "fingerprint": fingerprint[0] if fingerprint else None,
    }
    typer.echo(json.dumps(summary, indent=2, default=str))
