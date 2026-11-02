"""The app for the browser tests (web/e2e): real backend, fake models, no cost.

Starts a throwaway pgvector database, migrates and seeds it with two papers, and
serves the built React app (web/dist) with zotero-rag-core's real pipeline:
retrieval, BM25 in SQL, citations. Only the model calls are fakes:

- the embedder sends questions mentioning "fog" towards the fog paper, others
  towards the ECMP paper;
- the chat model answers by quoting the first passage it was given, citing [1],
  word by word (slowly when the question contains "slowly", for Stop);
- the condenser marks its rewrite, so a follow-up's "Searched for" is visible.

    uv run python tests/e2e/server.py [--port 8765]
"""

from __future__ import annotations

import argparse
import re
import time
from collections.abc import Iterator
from pathlib import Path

import psycopg
import uvicorn
from alembic import command
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from pydantic import SecretStr
from testcontainers.community.postgres import PostgresContainer

from zotero_rag_core.fingerprint import StoreFingerprint
from zotero_rag_core.llm import ModelPrice
from zotero_rag_core.stores.pg import PgFullTextSearcher, PgStore
from zotero_rag_web.app import create_app
from zotero_rag_web.chat.service import AnswerEngine
from zotero_rag_web.cli import alembic_config
from zotero_rag_web.settings import Settings

ROOT = Path(__file__).parents[2]
WIDTH = 8
FOG = [1.0, 0, 0, 0, 0, 0, 0, 0]
ECMP = [0, 1.0, 0, 0, 0, 0, 0, 0]

PAPERS = [
    (
        "A1",
        "P1",
        "What is a Fog Node",
        "Tordera, Eva",
        2016,
        ["EDGE"],
        ["fog"],
        FOG,
        "A fog node sits between the edge devices and the cloud.",
    ),
    (
        "A2",
        "P2",
        "ECMP and elephant flows",
        "Doe, Jane",
        2021,
        ["OTHER"],
        ["routing"],
        ECMP,
        "Hash collisions can put several elephant flows on one path.",
    ),
]


def seed(url: str, settings: Settings) -> None:
    store = PgStore.from_url(url)
    store.add(
        ids=[f"{a}:1:0" for a, *_ in PAPERS],
        embeddings=[vector for *_, vector, _ in PAPERS],
        documents=[text for *_, text in PAPERS],
        metadatas=[
            {
                "attachment_key": a,
                "item_key": i,
                "title": t,
                "authors": au,
                "year": str(y),
                "year_int": y,
                "page": 1,
                "collection_keys": c,
                "tags": tg,
                "zotero_url": f"https://www.zotero.org/groups/1/items/{i}",
            }
            for a, i, t, au, y, c, tg, _, _ in PAPERS
        ],
    )
    store.write_fingerprint(StoreFingerprint.intended(settings.core_config, embedding_dim=WIDTH))
    store.refresh_keyword_stats()
    store.close()
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO collections (key, parent_key, name, path, item_count) VALUES "
            "('NET', NULL, 'Networking', 'Networking', 0), "
            "('EDGE', 'NET', 'Edge', 'Networking/Edge', 1), "
            "('OTHER', NULL, 'Routing', 'Routing', 1)"
        )
        conn.execute(
            "INSERT INTO library (id, group_id, name, last_sync_at, last_sync_status) "
            "VALUES (1, 1, 'Test Library', now(), 'ok')"
        )


def embed_query(text: str) -> list[float]:
    return FOG if "fog" in text.lower() else ECMP


def stream_chat(messages: list[BaseMessage]) -> Iterator[AIMessageChunk]:
    latest = str(messages[-1].content)
    match = re.search(r"\[1\][^\n]*\n(.+?)(?:\n\n\[2\]|\n\nQuestion:)", latest, re.S)
    passage = match.group(1).strip() if match else ""
    question = latest.rsplit("Question:", 1)[-1].lower()
    shared = {w for w in re.findall(r"[a-z]{3,}", question)} & set(
        re.findall(r"[a-z]{3,}", passage.lower())
    )
    delay = 0.4 if "slowly" in question else 0.03
    # Like a real model: answer, citing [1], only from a passage that is on topic.
    text = f"From the library: {passage} [1]" if shared else "The passages do not say."
    words = text.split(" ")
    for n, word in enumerate(words):
        time.sleep(delay)
        yield AIMessageChunk(content=word if n == 0 else " " + word)
    yield AIMessageChunk(
        content="", usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
    )


def condense(messages: list[BaseMessage]) -> AIMessage:
    question = str(messages[-1].content).rsplit("Latest question:", 1)[-1].strip()
    return AIMessage(content=f"{question} (with context)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    port = parser.parse_args().port

    container = PostgresContainer("pgvector/pgvector:pg17", driver=None)
    container.start()
    try:
        url = container.get_connection_url()
        command.upgrade(alembic_config(url), "head")
        settings = Settings(
            _env_file=None,
            database_url=url,
            secret_key=SecretStr("e2e"),
            openrouter_api_key=SecretStr("unused"),
            embedding_model="test/embedder",
            embedding_dimensions=WIDTH,
            reranker="none",
            cookie_secure=False,
            static_dir=ROOT / "web" / "dist",
            log_format="console",
            log_level="WARNING",
        )
        seed(url, settings)
        store = PgStore.from_url(url)
        engine = AnswerEngine(
            settings=settings,
            store=store,
            embed_query=embed_query,
            stream_chat=stream_chat,
            condense_chat=condense,
            keyword=PgFullTextSearcher(store),
            rerank=None,
            answer_price=ModelPrice(prompt=1e-7, completion=4e-7),
        )
        uvicorn.run(create_app(settings, engine), host="127.0.0.1", port=port, log_level="warning")
    finally:
        container.stop()


if __name__ == "__main__":
    main()
