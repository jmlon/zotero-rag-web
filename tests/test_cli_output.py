"""What `zrw index` prints and logs: a short summary, not the pipeline's chatter."""

from __future__ import annotations

import logging

import pytest

from zotero_rag_web import logs
from zotero_rag_web.cli import summary
from zotero_rag_web.indexing.sync import SyncReport


def test_a_sync_that_added_a_paper_says_so_in_a_few_lines() -> None:
    report = SyncReport(
        status="ok",
        version_before=130,
        version_after=141,
        indexed=1,
        already_indexed=11,
        chunks_added=92,
        embedding_tokens=20053,
        elapsed_s=10.84,
    )
    assert summary(report) == (
        "Sync ok: library version 130 -> 141\n"
        "  Attachments: 1 indexed, 11 already indexed\n"
        "  Added 92 chunks (20,053 embedding tokens)\n"
        "  10.8 s"
    )


def test_failures_removals_and_the_error_are_listed() -> None:
    report = SyncReport(
        status="needs_attention",
        version_before=None,
        version_after=7,
        failed=1,
        removed_attachments=2,
        removed_chunks=40,
        removal_blocked=3,
        message="VectorStoreError: boom",
        failures=[{"attachment_key": "A1", "title": "Fog", "error": "no text layer"}],
    )
    text = summary(report)
    assert text.startswith("Sync needs_attention: library version none -> 7")
    assert "1 failed" in text
    assert "2 attachment(s), 40 chunks removed" in text
    assert "--allow-mass-removal" in text
    assert "Failed: Fog: no text layer" in text
    assert "VectorStoreError: boom" in text


@pytest.mark.parametrize(
    ("status", "expected"),
    [("unchanged", "Library unchanged (version 96)"), ("locked", "Another sync is running")],
)
def test_a_sync_with_nothing_to_do_is_one_line(status: str, expected: str) -> None:
    text = summary(SyncReport(status=status, version_before=96, version_after=96))  # type: ignore[arg-type]
    assert text.startswith(expected) and "\n" not in text


def test_http_clients_and_pypdf_are_quiet(caplog: pytest.LogCaptureFixture) -> None:
    logs.configure("INFO", "console")
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
    assert not logging.getLogger("httpx2").isEnabledFor(logging.INFO)
    assert not logging.getLogger("pypdf").isEnabledFor(logging.WARNING)
    assert logging.getLogger("zotero_rag_core.indexer").isEnabledFor(logging.INFO)


def test_index_keeps_the_pipeline_to_warnings_unless_verbose() -> None:
    logs.configure("INFO", "console", quiet_core=True)
    core = logging.getLogger("zotero_rag_core.indexer")
    assert not core.isEnabledFor(logging.INFO) and core.isEnabledFor(logging.WARNING)
    logs.configure("INFO", "console", quiet_core=False)
    assert core.isEnabledFor(logging.INFO)
