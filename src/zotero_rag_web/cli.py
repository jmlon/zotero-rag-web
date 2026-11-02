"""`zrw`: the operator's command line. The web server is started by uvicorn.

M1 provides `migrate` and `config`; `index`, `reindex` and `status` come with the
indexer (M2).
"""

from __future__ import annotations

import json
from importlib import resources

import typer
from alembic import command
from alembic.config import Config as AlembicConfig
from pydantic import ValidationError

from zotero_rag_web import __version__
from zotero_rag_web.settings import MissingSettingError, Settings

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
