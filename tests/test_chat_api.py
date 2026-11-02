"""The chat API (M3): identity, conversations, asking over SSE, and limits.

The database, the migration, the store and zotero-rag-core's whole query pipeline
(retrieval, fusion, citations) are real; only the model calls are fakes.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from typer.testing import CliRunner

from tests.conftest import make_settings
from zotero_rag_core.fingerprint import StoreFingerprint
from zotero_rag_core.querying import NO_CONTEXT_ANSWER, Source
from zotero_rag_core.stores.pg import PgFullTextSearcher, PgStore
from zotero_rag_web.api.library import library_url
from zotero_rag_web.app import create_app
from zotero_rag_web.chat.service import AnswerEngine, source_json
from zotero_rag_web.cli import app as cli
from zotero_rag_web.identity import COOKIE_NAME
from zotero_rag_web.settings import Settings

WIDTH = 8
ANSWER = "A fog node sits between the edge devices and the cloud [1]."


class FakeModels:
    """The three model calls, recorded."""

    def __init__(self) -> None:
        self.prompts: list[list[BaseMessage]] = []
        self.condensed: list[list[BaseMessage]] = []
        self.answer = ANSWER
        self.fail_after: int | None = None

    def embed_query(self, text: str) -> list[float]:
        return [1.0] + [0.0] * (WIDTH - 1)

    def stream(self, messages: list[BaseMessage]) -> Iterator[AIMessageChunk]:
        """Like OpenRouter streaming: token counts on the last chunk, but no cost."""
        self.prompts.append(messages)
        for n, word in enumerate(self.answer.split(" ")):
            if self.fail_after is not None and n >= self.fail_after:
                raise RuntimeError("provider went away")
            yield AIMessageChunk(content=word if n == 0 else " " + word)
        yield AIMessageChunk(
            content="",
            usage_metadata={"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100},
        )

    def condense(self, messages: list[BaseMessage]) -> AIMessage:
        self.condensed.append(messages)
        return AIMessage(content="Where does a fog node sit?")


def settings_for(url: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": url,
        "secret_key": "test-secret",
        "openrouter_api_key": "o",
        "embedding_model": "test/embedder",
        "embedding_dimensions": WIDTH,
        "reranker": "none",
        "cookie_secure": False,
        "log_format": "console",
    }
    return make_settings(**(values | overrides))


@pytest.fixture
def indexed(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A migrated database holding one indexed paper, in collection NET > EDGE."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert CliRunner().invoke(cli, ["migrate"]).exit_code == 0
    monkeypatch.delenv("DATABASE_URL")
    store = PgStore.from_url(database_url)
    store.add(
        ids=["A1:3:0"],
        embeddings=[[1.0] + [0.0] * (WIDTH - 1)],
        documents=["A fog node sits between the edge devices and the cloud."],
        metadatas=[
            {
                "attachment_key": "A1",
                "item_key": "P1",
                "document_format": "pdf",
                "title": "What is a Fog Node",
                "authors": "Tordera, Eva",
                "year": "2016",
                "year_int": 2016,
                "page": 3,
                "zotero_url": "https://www.zotero.org/groups/1/items/P1",
                "collection_keys": ["EDGE"],
                "tags": ["fog"],
            }
        ],
    )
    store.write_fingerprint(
        StoreFingerprint.intended(settings_for(database_url).core_config, embedding_dim=WIDTH)
    )
    store.refresh_keyword_stats()
    store.close()
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO collections (key, parent_key, name, path) VALUES "
            "('NET', NULL, 'Networking', 'Networking'), "
            "('EDGE', 'NET', 'Edge', 'Networking/Edge'), "
            "('OTHER', NULL, 'Other', 'Other')"
        )
        conn.execute(
            "INSERT INTO library (id, group_id, name, web_url, last_sync_status) "
            "VALUES (1, 1, 'Team', 'https://www.zotero.org/groups/1', 'ok')"
        )
    yield database_url


@pytest.fixture
def models() -> FakeModels:
    return FakeModels()


def engine_for(url: str, models: FakeModels, **overrides: Any) -> AnswerEngine:
    settings = settings_for(url, **overrides)
    store = PgStore.from_url(url)
    return AnswerEngine(
        settings=settings,
        store=store,
        embed_query=models.embed_query,
        stream_chat=models.stream,
        condense_chat=models.condense,
        keyword=PgFullTextSearcher(store),
        rerank=None,
    )


@pytest.fixture
def client(indexed: str, models: FakeModels) -> Iterator[TestClient]:
    with TestClient(create_app(settings_for(indexed), engine_for(indexed, models))) as test:
        yield test


def events(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse a Server-Sent Events body into (event, data) pairs."""
    parsed = []
    for block in text.split("\n\n"):
        lines = [line for line in block.splitlines() if not line.startswith(":")]
        if not lines:
            continue
        name = next(line[7:] for line in lines if line.startswith("event: "))
        data = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
        parsed.append((name, data))
    return parsed


def ask(client: TestClient, conversation: str, question: str) -> list[tuple[str, dict[str, Any]]]:
    response = client.post(
        f"/api/conversations/{conversation}/messages", json={"question": question}
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return events(response.text)


def new_conversation(client: TestClient, **filters: Any) -> str:
    response = client.post("/api/conversations", json={"filters": filters})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


# --- identity ---------------------------------------------------------------------------


def test_a_browser_gets_a_signed_id_once(client: TestClient) -> None:
    first = client.get("/api/conversations")
    cookie = first.cookies.get(COOKIE_NAME)
    assert cookie and "HttpOnly" in first.headers["set-cookie"]
    assert "Secure" not in first.headers["set-cookie"]  # COOKIE_SECURE=false here
    again = client.get("/api/conversations")
    assert "set-cookie" not in again.headers  # kept, not reissued


def test_a_forged_cookie_is_replaced_not_trusted(client: TestClient) -> None:
    forged = f"{uuid.uuid4()}.forged-signature"
    client.cookies.set(COOKIE_NAME, forged)
    response = client.get("/api/conversations")
    issued = response.cookies.get(COOKIE_NAME)
    assert issued is not None and issued != forged


def test_conversations_are_private_to_their_browser(indexed: str, models: FakeModels) -> None:
    app = create_app(settings_for(indexed), engine_for(indexed, models))
    with TestClient(app) as alice, TestClient(app) as bob:
        mine = new_conversation(alice)
        assert bob.get("/api/conversations").json() == []
        assert bob.get(f"/api/conversations/{mine}").status_code == 404
        assert bob.delete(f"/api/conversations/{mine}").status_code == 404
        assert (
            bob.post(f"/api/conversations/{mine}/messages", json={"question": "?"}).status_code
            == 404
        )
        assert [c["id"] for c in alice.get("/api/conversations").json()] == [mine]


# --- conversations ------------------------------------------------------------------------


def test_create_read_rename_refilter_delete(client: TestClient) -> None:
    key = new_conversation(client)
    assert client.patch(f"/api/conversations/{key}", json={"title": "Fog"}).json()["title"] == "Fog"
    changed = client.patch(f"/api/conversations/{key}", json={"filters": {"collection": "NET"}})
    assert changed.json()["filters"] == {"collection": "NET", "include_undated": True}
    read = client.get(f"/api/conversations/{key}").json()
    assert [(m["role"], m["content"]) for m in read["messages"]] == [("event", "filters_changed")]
    assert client.delete(f"/api/conversations/{key}").status_code == 204
    assert client.get(f"/api/conversations/{key}").status_code == 404


def test_an_unknown_collection_or_id_is_refused(client: TestClient) -> None:
    response = client.post("/api/conversations", json={"filters": {"collection": "GONE"}})
    assert (response.status_code, response.json()["error"]["code"]) == (400, "unknown_collection")
    assert client.get("/api/conversations/not-a-uuid").status_code == 404
    bad = client.post("/api/conversations", json={"filters": {"year_min": 2020, "year_max": 1}})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_request"


# --- asking -------------------------------------------------------------------------------


def test_an_answer_streams_and_is_saved(client: TestClient) -> None:
    key = new_conversation(client)
    stream = ask(client, key, "What is a fog node?")
    names = [name for name, _ in stream]
    assert names[:2] == ["status", "status"]
    assert [d["stage"] for n, d in stream if n == "status"] == ["retrieving", "answering"]
    tokens = "".join(d["text"] for n, d in stream if n == "token")
    assert tokens == ANSWER
    name, done = stream[-1]
    assert name == "done" and done["status"] == "done"
    (source,) = done["sources"]
    assert (source["number"], source["title"], source["locator"]) == (
        1,
        "What is a Fog Node",
        "p. 3",
    )
    # A PDF opens in zotero.org's reader, not on its item's details page.
    assert source["zotero_url"] == "https://www.zotero.org/groups/1/items/P1/attachment/A1/reader"
    assert "excerpts" not in source

    saved = client.get(f"/api/conversations/{key}").json()
    assert saved["title"] == "What is a fog node?"
    assert [(m["role"], m["status"]) for m in saved["messages"]] == [
        ("user", "done"),
        ("assistant", "done"),
    ]
    assert saved["messages"][1]["content"] == ANSWER
    usage = saved["messages"][1]["usage"]
    assert usage["cost_source"] == "estimate"  # no list price given to this engine


def test_a_follow_up_is_condensed_and_answered_with_history(
    client: TestClient, models: FakeModels
) -> None:
    key = new_conversation(client)
    ask(client, key, "What is a fog node?")
    stream = ask(client, key, "And where does it sit?")
    assert [d["stage"] for n, d in stream if n == "status"] == [
        "condensing",
        "retrieving",
        "answering",
    ]
    assert ("query", {"standalone": "Where does a fog node sit?"}) in stream
    assert stream[-1][1]["standalone"] == "Where does a fog node sit?"
    sent = [str(m.content) for m in models.prompts[-1]]
    assert "What is a fog node?" in sent  # the earlier question, as history
    assert "A fog node sits between the edge devices and the cloud." in sent  # markers stripped
    saved = client.get(f"/api/conversations/{key}").json()["messages"]
    assert saved[-1]["standalone_query"] == "Where does a fog node sit?"


def test_the_conversations_filters_limit_what_is_searched(
    client: TestClient, models: FakeModels
) -> None:
    inside = new_conversation(client, collection="NET")  # EDGE is below NET
    assert ask(client, inside, "fog?")[-1][1]["sources"]
    outside = new_conversation(client, collection="OTHER")
    done = ask(client, outside, "fog?")[-1][1]
    assert done["answer"] == NO_CONTEXT_ANSWER and done["sources"] == []
    assert len(models.prompts) == 1  # nothing retrieved: the model was never called


def test_a_model_failure_mid_answer_is_an_error_event(
    client: TestClient, models: FakeModels
) -> None:
    models.fail_after = 3
    key = new_conversation(client)
    stream = ask(client, key, "What is a fog node?")
    assert stream[-1] == ("error", {"code": "llm_failed"})
    saved = client.get(f"/api/conversations/{key}").json()["messages"]
    assert saved[-1]["status"] == "error"


def test_stopping_keeps_the_partial_answer(indexed: str, models: FakeModels) -> None:
    """Stop (the client leaving) at the service level: the stream ends, the text so far stays."""
    engine = engine_for(indexed, models)
    cancelled = threading.Event()
    seen: list[str] = []

    def emit(name: str, data: dict[str, Any]) -> None:
        if name == "token":
            seen.append(data["text"])
            cancelled.set()  # the asker leaves after the first word

    answer = engine.ask("What is a fog node?", [], None, emit, cancelled)
    assert answer.stopped and answer.partial == "A"
    assert seen == ["A"]


# --- refusals before any cost ---------------------------------------------------------------


def test_refusals_are_json_errors_before_any_model_call(indexed: str, models: FakeModels) -> None:
    app = create_app(settings_for(indexed, max_question_chars=20), engine_for(indexed, models))
    with TestClient(app) as client:
        key = new_conversation(client)
        long = client.post(f"/api/conversations/{key}/messages", json={"question": "x" * 21})
        assert (long.status_code, long.json()["error"]["code"]) == (400, "question_too_long")
        blank = client.post(f"/api/conversations/{key}/messages", json={"question": "   "})
        assert blank.status_code == 422
    assert models.prompts == []


def test_an_empty_index_or_no_key_says_so(
    database_url: str, models: FakeModels, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert CliRunner().invoke(cli, ["migrate"]).exit_code == 0
    with TestClient(create_app(settings_for(database_url), engine_for(database_url, models))) as c:
        response = c.post(
            f"/api/conversations/{new_conversation(c)}/messages", json={"question": "hi"}
        )
        assert (response.status_code, response.json()["error"]["code"]) == (503, "index_empty")
    no_key = settings_for(database_url).model_copy(update={"openrouter_api_key": None})
    with TestClient(create_app(no_key)) as c:
        response = c.post(
            f"/api/conversations/{new_conversation(c)}/messages", json={"question": "hi"}
        )
        assert (response.status_code, response.json()["error"]["code"]) == (503, "not_configured")


def test_the_hourly_limit_per_browser(indexed: str, models: FakeModels) -> None:
    app = create_app(
        settings_for(indexed, rate_limit_per_browser_hour=2), engine_for(indexed, models)
    )
    with TestClient(app) as client:
        key = new_conversation(client)
        ask(client, key, "one?")
        ask(client, key, "two?")
        third = client.post(f"/api/conversations/{key}/messages", json={"question": "three?"})
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "rate_limited"
    assert 0 < int(third.headers["Retry-After"]) <= 3601


def test_the_daily_spend_cap(indexed: str, models: FakeModels) -> None:
    app = create_app(settings_for(indexed, daily_spend_cap_usd=0.01), engine_for(indexed, models))
    with TestClient(app) as client:
        key = new_conversation(client)
        ask(client, key, "one?")
        with psycopg.connect(indexed, autocommit=True) as conn:
            conn.execute(
                "UPDATE messages SET usage = jsonb_set(usage, '{total_cost}', '0.02') "
                "WHERE role = 'assistant'"
            )
        refused = client.post(f"/api/conversations/{key}/messages", json={"question": "two?"})
    assert (refused.status_code, refused.json()["error"]["code"]) == (429, "budget_exhausted")


# --- the library ---------------------------------------------------------------------------


def test_library_collections_and_tags(client: TestClient) -> None:
    library = client.get("/api/library").json()
    assert (library["name"], library["chunks"], library["indexed"]) == ("Team", 1, True)
    assert (library["year_min"], library["year_max"]) == (2016, 2016)
    assert library["library_url"] == "https://www.zotero.org/groups/1/team/library"
    paths = [c["path"] for c in client.get("/api/collections").json()]
    assert paths == ["Networking", "Networking/Edge", "Other"]
    assert client.get("/api/tags").json() == [{"tag": "fog", "items": 1}]
    assert client.get("/api/tags", params={"q": "zz"}).json() == []


# --- Stop, through a real server ------------------------------------------------------------


def test_closing_the_connection_stops_the_answer_and_keeps_its_start(
    indexed: str, models: FakeModels
) -> None:
    """What the UI's Stop does: abort the request. Needs a real server, because only a
    real connection close reaches the app as a disconnect (and the middleware must
    pass it through, which BaseHTTPMiddleware would not)."""
    import socket
    import time

    import httpx
    import uvicorn

    slow = models.stream

    def slow_stream(messages: list[BaseMessage]) -> Iterator[AIMessageChunk]:
        for chunk in slow(messages):
            time.sleep(0.3)
            yield chunk

    models.stream = slow_stream  # type: ignore[method-assign]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    app = create_app(settings_for(indexed), engine_for(indexed, models))
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        while not server.started:
            time.sleep(0.05)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}") as http:
            key = http.post("/api/conversations", json={}).json()["id"]
            with http.stream(
                "POST",
                f"/api/conversations/{key}/messages",
                json={"question": "What is a fog node?"},
            ) as response:
                for line in response.iter_lines():
                    if line.startswith("event: token"):
                        break  # the first word arrived: press Stop
            deadline = time.time() + 10
            while time.time() < deadline:
                last = http.get(f"/api/conversations/{key}").json()["messages"][-1]
                if last["status"] != "streaming":
                    break
                time.sleep(0.1)
        assert last["status"] == "stopped"
        assert last["content"] and last["content"] != ANSWER
        assert ANSWER.startswith(last["content"])
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_a_streamed_answer_is_priced_from_its_tokens(indexed: str, models: FakeModels) -> None:
    """Found live: OpenRouter reports no cost for a streamed call, so every answer
    counted as free and the daily spend cap could never trip."""
    from zotero_rag_core.llm import ModelPrice

    engine = engine_for(indexed, models)
    engine.answer_price = ModelPrice(prompt=1e-6, completion=2e-6)
    answer = engine.ask("What is a fog node?", [], None, lambda *_: None, threading.Event())
    assert answer.cost_source == "list_price"
    assert answer.result.usage.answer_cost == pytest.approx(1000 * 1e-6 + 100 * 2e-6)


def test_without_a_price_an_answer_counts_against_the_cap(indexed: str, models: FakeModels) -> None:
    from zotero_rag_web.chat.service import FALLBACK_ANSWER_COST_USD, total_cost

    answer = engine_for(indexed, models).ask("fog?", [], None, lambda *_: None, threading.Event())
    assert answer.cost_source == "estimate"
    assert total_cost(answer) >= FALLBACK_ANSWER_COST_USD


@pytest.mark.parametrize(
    ("attachment_key", "formats", "url"),
    [
        ("A1", {"A1": "pdf"}, "https://www.zotero.org/groups/1/items/P1/attachment/A1/reader"),
        ("A1", {"A1": "html"}, "https://www.zotero.org/groups/1/items/P1"),
        ("A1", {"A1": "markdown"}, "https://www.zotero.org/groups/1/items/P1"),
        ("N1", {"N1": "note"}, "https://www.zotero.org/groups/1/items/P1"),
        ("A1", {}, "https://www.zotero.org/groups/1/items/P1"),
        ("", {"": "pdf"}, "https://www.zotero.org/groups/1/items/P1"),
    ],
)
def test_only_a_pdf_links_to_the_reader(
    attachment_key: str, formats: dict[str, str], url: str
) -> None:
    source = Source(
        number=1,
        item_key="P1",
        attachment_key=attachment_key,
        zotero_url="https://www.zotero.org/groups/1/items/P1",
    )
    assert source_json(source, formats)["zotero_url"] == url
    assert source_json(source)["zotero_url"] == "https://www.zotero.org/groups/1/items/P1"


@pytest.mark.parametrize(
    ("web_url", "name", "url"),
    [
        (
            "https://www.zotero.org/groups/6743389",
            "AI_EDU_UPB",
            "https://www.zotero.org/groups/6743389/ai_edu_upb/library",
        ),
        (
            "https://www.zotero.org/groups/7",
            "Redes & Edge (2026)",
            "https://www.zotero.org/groups/7/redes_edge_2026/library",
        ),
        ("https://www.zotero.org/groups/7", None, "https://www.zotero.org/groups/7/group/library"),
        (None, "Team", None),
    ],
)
def test_the_library_link_carries_a_short_name_zotero_org_can_redirect(
    web_url: str | None, name: str | None, url: str | None
) -> None:
    assert library_url(web_url, name) == url
