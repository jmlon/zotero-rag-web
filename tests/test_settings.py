"""Settings: PRD §7's variables, their defaults, and what is refused at startup."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.conftest import make_settings
from zotero_rag_web.settings import MissingSettingError, Settings


def test_defaults_are_the_clis_best_measured_configuration() -> None:
    settings = make_settings()
    assert (settings.llm_model, settings.embedding_model, settings.reranker) == (
        "openai/gpt-6-luna",
        "openai/text-embedding-3-small",
        "voyageai/rerank-3-lite",
    )
    assert (settings.top_k, settings.reranker_fanout, settings.retrieval) == (7, 4, "hybrid")
    assert (settings.chunk_size, settings.chunk_overlap) == (1000, 200)
    assert settings.embedding_width == 1536


def test_variables_are_read_by_their_prd_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOP_K", "10")
    monkeypatch.setenv("ATTACHMENT_PREFERENCE", "pdf, Markdown ,html")
    monkeypatch.setenv("RERANKER", "none")
    monkeypatch.setenv("ZOTERO_GROUP_ID", "6743389")
    settings = Settings(_env_file=None)
    assert settings.top_k == 10
    assert settings.attachment_preference == ["pdf", "markdown", "html"]
    assert settings.reranker_name is None
    assert settings.zotero_group_id == 6743389


def test_an_overlap_as_large_as_the_chunk_is_refused() -> None:
    with pytest.raises(ValidationError, match="CHUNK_OVERLAP"):
        make_settings(chunk_size=500, chunk_overlap=500)


def test_an_embedding_model_too_wide_to_index_is_refused() -> None:
    """S4: pgvector cannot index more than 2,000 dimensions."""
    with pytest.raises(ValidationError, match="at most 2000"):
        make_settings(embedding_model="qwen/qwen3-embedding-8b")


def test_an_unknown_embedding_model_needs_its_width() -> None:
    with pytest.raises(ValidationError, match="EMBEDDING_DIMENSIONS"):
        make_settings(embedding_model="someone/new-embedder")
    assert (
        make_settings(
            embedding_model="someone/new-embedder", embedding_dimensions=768
        ).embedding_width
        == 768
    )
    with pytest.raises(ValidationError, match="at most 2000"):
        make_settings(embedding_model="someone/new-embedder", embedding_dimensions=3072)


def test_an_unknown_reranker_is_refused() -> None:
    with pytest.raises(ValidationError, match="unknown reranker"):
        make_settings(reranker="rerank-everything")


def test_require_names_every_missing_setting() -> None:
    settings = make_settings(database_url="postgresql://u:p@h/d")
    settings.require("database")
    with pytest.raises(MissingSettingError) as raised:
        settings.require("database", "zotero", "openrouter")
    assert "ZOTERO_GROUP_ID and ZOTERO_API_KEY" in str(raised.value)
    assert "OPENROUTER_API_KEY" in str(raised.value)
    assert "DATABASE_URL" not in str(raised.value)


def test_credentials_never_appear_in_output() -> None:
    settings = make_settings(
        database_url="postgresql://zrw:hunter2@db.example:5432/zrw",
        zotero_api_key="zotero-secret",
        openrouter_api_key="sk-or-secret",
        secret_key="cookie-secret",
    )
    shown = f"{settings!r} {settings.redacted()}"
    for secret in ("hunter2", "zotero-secret", "sk-or-secret", "cookie-secret"):
        assert secret not in shown
    assert settings.redacted()["database_url"] == "postgresql://zrw:***@db.example:5432/zrw"
    assert settings.redacted()["openrouter_api_key"] == "set"


def test_the_core_config_is_a_group_library_with_these_settings() -> None:
    settings = make_settings(
        zotero_group_id=6743389,
        zotero_api_key="z",
        openrouter_api_key="o",
        top_k=5,
        reranker="none",
        attachment_preference="html,pdf",
    )
    config = settings.core_config
    assert config.global_.zotero_library_type == "group"
    assert config.global_.zotero_library_id == "6743389"
    assert (config.global_.api_key, config.global_.zotero_api_key) == ("o", "z")
    assert config.embedding_model == "openai/text-embedding-3-small"
    assert config.llm_model == "openai/gpt-6-luna"
    assert config.index.top_k == 5
    assert config.reranker is None
    assert config.index.attachment_preference == ["html", "pdf"]
