"""Configuration, from environment variables only (PRD §7).

Every setting has the name the PRD gives it, unprefixed, so a Railway or Render
dashboard reads like the PRD's table. Credentials are optional here, because
`zrw migrate` needs only the database; the commands and services that use a
credential check for it themselves (`require`), as the CLI's `require_zotero` does.
"""

from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from zotero_rag_core.config import Config, GlobalConfig, IndexConfig, is_reranker_name

#: pgvector's HNSW index takes at most this many dimensions (S4, docs/spikes.md).
MAX_EMBEDDING_DIMENSIONS = 2000

#: Widths of the embedding models zotero-rag's catalogue lists. A model not listed
#: here needs EMBEDDING_DIMENSIONS, so the width limit is checked before any write.
KNOWN_EMBEDDING_DIMENSIONS = {
    "perplexity/pplx-embed-v1-0.6b": 384,
    "qwen/qwen3-embedding-8b": 4096,
    "baai/bge-m3": 1024,
    "openai/text-embedding-3-small": 1536,
    "openai/text-embedding-3-large": 3072,
    "amazon/titan-embed-text-v2": 1024,
    "voyageai/voyage-4": 1024,
    "google/gemini-embedding-001": 768,
    "nvidia/nemotron-3.5-embed-1b:free": 1024,
}

DEFAULT_ATTACHMENT_PREFERENCE = ["markdown", "pdf", "docx", "odt", "epub", "html", "txt"]

Credential = Literal["database", "zotero", "openrouter", "secret_key"]


class MissingSettingError(RuntimeError):
    """A setting this command needs is not set."""


class Settings(BaseSettings):
    """The deployment's settings. Defaults are the CLI's best measured configuration."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- where things are -----------------------------------------------------
    #: Out of `repr`: the URL carries the password. `redacted()` shows it without.
    database_url: str = Field(default="", repr=False)
    zotero_group_id: int | None = None
    zotero_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    openrouter_endpoint: str = "https://openrouter.ai/api/v1"
    #: Signs the browser-id cookie (PRD §8.2). Required once conversations exist (M3).
    secret_key: SecretStr | None = None
    #: The built React app; served at `/`.
    static_dir: Path = Path("web/dist")
    port: int = 8000

    # --- answering ------------------------------------------------------------
    llm_model: str = "openai/gpt-6-luna"
    #: Rewrites follow-ups into standalone questions; empty means `llm_model`.
    condense_model: str | None = None
    condense: bool = True
    history_turns: int = Field(default=6, ge=0)
    embedding_model: str = "openai/text-embedding-3-small"
    #: Only for a model missing from KNOWN_EMBEDDING_DIMENSIONS.
    embedding_dimensions: int | None = Field(default=None, gt=0)
    #: An OpenRouter reranker id, `cohere`, or `none`.
    reranker: str = "voyageai/rerank-3-lite"
    top_k: int = Field(default=7, gt=0)
    reranker_fanout: int = Field(default=4, ge=1)
    retrieval: Literal["hybrid", "vector"] = "hybrid"
    temperature: float = Field(default=0.0, ge=0.0)

    # --- indexing -------------------------------------------------------------
    chunk_size: int = Field(default=1000, gt=0)
    chunk_overlap: int = Field(default=200, ge=0)
    attachment_preference: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_ATTACHMENT_PREFERENCE)
    )
    index_notes: bool = True
    index_workers: int = Field(default=4, ge=1)
    prune_max_fraction: float = Field(default=0.2, gt=0.0, le=1.0)

    # --- abuse and cost controls (PRD §8.3) -----------------------------------
    rate_limit_per_browser_hour: int = Field(default=30, ge=0)
    rate_limit_per_ip_hour: int = Field(default=60, ge=0)
    #: 0 disables the cap.
    daily_spend_cap_usd: float = Field(default=5.0, ge=0.0)
    max_question_chars: int = Field(default=2000, gt=0)

    # --- operations -----------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    cookie_secure: bool = True

    @field_validator("attachment_preference", mode="before")
    @classmethod
    def _split_formats(cls, value: object) -> object:
        """`markdown,pdf,html` in the environment; a list when built in code."""
        if isinstance(value, str):
            return [part.strip().lower() for part in value.split(",") if part.strip()]
        return value

    @field_validator("reranker")
    @classmethod
    def _known_reranker(cls, value: str) -> str:
        if value != "none" and not is_reranker_name(value):
            raise ValueError(
                f"unknown reranker {value!r}: use an OpenRouter reranker id such as "
                "voyageai/rerank-3-lite, `cohere`, or `none`"
            )
        return value

    @field_validator("condense_model", mode="before")
    @classmethod
    def _empty_means_unset(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def _consistent(self) -> Settings:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"CHUNK_OVERLAP ({self.chunk_overlap}) must be smaller than "
                f"CHUNK_SIZE ({self.chunk_size})"
            )
        width = self.embedding_dimensions or KNOWN_EMBEDDING_DIMENSIONS.get(self.embedding_model)
        if width is None:
            raise ValueError(
                f"EMBEDDING_MODEL {self.embedding_model!r} is not one this app knows the "
                "width of; set EMBEDDING_DIMENSIONS to the number of dimensions it returns"
            )
        if width > MAX_EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"EMBEDDING_MODEL {self.embedding_model!r} returns {width} dimensions, and "
                f"pgvector can index at most {MAX_EMBEDDING_DIMENSIONS}. Choose a narrower "
                "model, such as openai/text-embedding-3-small (1536)."
            )
        return self

    # --- derived --------------------------------------------------------------

    @property
    def embedding_width(self) -> int:
        width = self.embedding_dimensions or KNOWN_EMBEDDING_DIMENSIONS[self.embedding_model]
        return width

    @property
    def reranker_name(self) -> str | None:
        return None if self.reranker == "none" else self.reranker

    def require(self, *names: Credential) -> None:
        """Fail with every missing setting named, before any work starts."""
        present = {
            "database": bool(self.database_url),
            "zotero": self.zotero_group_id is not None and self.zotero_api_key is not None,
            "openrouter": self.openrouter_api_key is not None,
            "secret_key": self.secret_key is not None,
        }
        variables = {
            "database": "DATABASE_URL",
            "zotero": "ZOTERO_GROUP_ID and ZOTERO_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "secret_key": "SECRET_KEY",
        }
        missing = [variables[name] for name in names if not present[name]]
        if missing:
            raise MissingSettingError("Set " + ", ".join(missing) + " (see .env.example).")

    @cached_property
    def core_config(self) -> Config:
        """The same settings as zotero-rag-core's `Config`, for the pipeline it runs.

        The library is a group library; credentials are filled in directly rather
        than read from the environment by name, as the CLI does.
        """
        global_ = GlobalConfig(zotero_library_type="group")
        global_.endpoint = self.openrouter_endpoint
        global_.api_key = (
            self.openrouter_api_key.get_secret_value() if self.openrouter_api_key else ""
        )
        global_.zotero_library_id = str(self.zotero_group_id or "")
        global_.zotero_api_key = (
            self.zotero_api_key.get_secret_value() if self.zotero_api_key else ""
        )
        global_.defaults.llm_model = self.llm_model
        global_.defaults.embedding_model = self.embedding_model
        global_.defaults.vector_db = "pgvector"
        index = IndexConfig(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            top_k=self.top_k,
            retrieval=self.retrieval,
            reranker=self.reranker_name,
            reranker_fanout=self.reranker_fanout,
            temperature=self.temperature,
            attachment_preference=list(self.attachment_preference),
            index_notes=self.index_notes,
        )
        return Config.model_validate({"global": global_, "index": index})

    def redacted(self) -> dict[str, object]:
        """Every setting, with credentials reduced to whether they are set."""
        values = self.model_dump()
        for name in ("zotero_api_key", "openrouter_api_key", "secret_key"):
            values[name] = "set" if values[name] is not None else "not set"
        if self.database_url:
            values["database_url"] = _without_password(self.database_url)
        values["static_dir"] = str(self.static_dir)
        return values


def _without_password(url: str) -> str:
    """`postgresql://user:***@host/db`: the password must never reach a log."""
    scheme, sep, rest = url.partition("://")
    credentials, at, location = rest.rpartition("@")
    if not at or ":" not in credentials:
        return url
    user = credentials.split(":", 1)[0]
    return f"{scheme}{sep}{user}:***@{location}"
