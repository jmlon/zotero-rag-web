"""Answering one question: zotero-rag-core's pipeline, built once, run per question.

Like the CLI's terminal UI session, the clients (embeddings, chat model, reranker,
store) are built once at startup and shared; each question runs on a worker thread
(the pipeline is synchronous) and reports what it is doing through `emit`.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage

from zotero_rag_core.fingerprint import StoreFingerprint, blocking
from zotero_rag_core.llm import ModelPrice, fetch_price
from zotero_rag_core.querying import (
    Answerer,
    Condensed,
    QueryResult,
    QuestionEmbedder,
    Source,
    StreamingAnswerer,
    Turn,
    condense_question,
    stream_answer_question,
)
from zotero_rag_core.reranking import RerankFn
from zotero_rag_core.retrieval import KeywordSearcher
from zotero_rag_core.store import ChunkStore, Filters
from zotero_rag_web.settings import Settings

logger = logging.getLogger(__name__)

#: Charged per answer when neither the provider nor a list price says what it cost:
#: high on purpose, so the daily spend cap errs towards stopping early.
FALLBACK_ANSWER_COST_USD = 0.002

Stage = Literal["condensing", "retrieving", "answering"]
#: (event name, payload): what a question's progress looks like to the client.
Emit = Callable[[str, dict[str, Any]], None]


class Cancelled(Exception):
    """The asker went away (closed the page, pressed Stop) while the answer streamed."""


class IndexNotReady(Exception):
    """No question can be answered yet; `code` says why (PRD §6.4)."""

    def __init__(self, code: Literal["index_empty", "index_mismatch"], message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Answer:
    result: QueryResult
    #: The standalone question retrieval used, for a follow-up; else None.
    standalone: str | None
    stopped: bool = False
    partial: str = ""
    #: Where the answer's cost came from: provider, list_price or estimate.
    cost_source: str = "provider"


@dataclass
class AnswerEngine:
    """The pipeline's long-lived parts. `from_settings` builds the real ones."""

    settings: Settings
    store: ChunkStore
    embed_query: QuestionEmbedder
    stream_chat: StreamingAnswerer
    #: Rewrites follow-ups; None disables condensing.
    condense_chat: Answerer | None
    keyword: KeywordSearcher | None
    rerank: RerankFn | None
    #: The answering model's list price. OpenRouter reports what a call cost only
    #: when it is not streamed, so streamed answers are priced from their tokens.
    answer_price: ModelPrice | None = None

    @classmethod
    def from_settings(cls, settings: Settings, pool: Any) -> AnswerEngine:
        from zotero_rag_core.embedding import build_embeddings
        from zotero_rag_core.llm import build_chat
        from zotero_rag_core.reranking import build_reranker
        from zotero_rag_core.stores.pg import PgFullTextSearcher, PgStore

        config = settings.core_config
        store = PgStore(pool)
        chat = build_chat(config)
        condense_chat: Answerer | None = None
        if settings.condense:
            condense_config = config.model_copy(deep=True)
            condense_config.index.llm_model = settings.condense_model or settings.llm_model
            condenser = build_chat(condense_config)

            def condense_chat(messages: list[BaseMessage]) -> AIMessage:
                return cast(AIMessage, condenser.invoke(messages))

        def stream_chat(messages: list[BaseMessage]) -> Iterator[AIMessageChunk]:
            return cast(Iterator[AIMessageChunk], chat.stream(messages))

        price = fetch_price(settings.openrouter_endpoint, settings.llm_model)
        if price is None:
            logger.warning(
                "no list price for %s: each answer counts as $%.3f towards the spend cap",
                settings.llm_model,
                FALLBACK_ANSWER_COST_USD,
            )
        return cls(
            settings=settings,
            store=store,
            answer_price=price,
            embed_query=build_embeddings(config).embed_query,
            stream_chat=stream_chat,
            condense_chat=condense_chat,
            keyword=PgFullTextSearcher(store) if settings.retrieval == "hybrid" else None,
            rerank=build_reranker(config, settings.reranker_name)
            if settings.reranker_name
            else None,
        )

    def check_ready(self) -> None:
        """Refuse before any cost: an empty index, or one built for another model."""
        if self.store.count() == 0:
            raise IndexNotReady("index_empty", "The library has not been indexed yet.")
        recorded = self.store.read_fingerprint()
        if recorded is not None:
            intended = StoreFingerprint.intended(
                self.settings.core_config, embedding_dim=self.settings.embedding_width
            )
            if fatal := blocking(recorded.compare(intended)):
                raise IndexNotReady(
                    "index_mismatch",
                    "The index was built for another embedding model: "
                    + "; ".join(str(m) for m in fatal),
                )

    def ask(
        self,
        question: str,
        history: Sequence[Turn],
        filters: Filters | None,
        emit: Emit,
        cancelled: threading.Event,
    ) -> Answer:
        """Answer, streaming tokens through `emit`; stops early when `cancelled` is set."""
        condensed: Condensed | None = None
        if history and self.condense_chat is not None:
            emit("status", {"stage": "condensing"})
            condensed = condense_question(question, history, self.condense_chat)
            emit("query", {"standalone": condensed.question})
        emit("status", {"stage": "retrieving"})

        def stream(messages: list[BaseMessage]) -> Iterator[AIMessageChunk]:
            # Called once retrieval and reranking are done and the prompt is built.
            emit("status", {"stage": "answering"})
            return self.stream_chat(messages)

        parts: list[str] = []

        def on_token(text: str) -> None:
            if cancelled.is_set():
                raise Cancelled
            parts.append(text)
            emit("token", {"text": text})

        try:
            result = stream_answer_question(
                question,
                store=self.store,
                embed_query=self.embed_query,
                stream=stream,
                on_token=on_token,
                top_k=self.settings.top_k,
                mode=self.settings.retrieval,
                keyword_index=self.keyword,
                rerank=self.rerank,
                rerank_fanout=self.settings.reranker_fanout,
                filters=filters,
                history=history,
                condensed=condensed,
            )
        except Cancelled:
            partial = "".join(parts)
            logger.info("answer stopped by the asker after %d characters", len(partial))
            stopped = QueryResult(question=question, answer=partial.strip(), condensed=condensed)
            return Answer(stopped, condensed.question if condensed else None, True, partial)
        return Answer(
            result, condensed.question if condensed else None, cost_source=self._price(result)
        )

    def _price(self, result: QueryResult) -> str:
        """Fill in what a streamed answer cost, if the provider did not say."""
        usage = result.usage
        if usage.answer_cost is not None:
            return "provider"
        if self.answer_price is not None:
            usage.answer_cost = self.answer_price.cost(usage.prompt_tokens, usage.completion_tokens)
            return "list_price"
        usage.answer_cost = FALLBACK_ANSWER_COST_USD
        return "estimate"


#: Attachment formats zotero.org opens in its reader. Others (and notes) link to the
#: item's details page, which lists the item's files.
READER_FORMATS = frozenset({"pdf"})


def reader_url(item_url: str, attachment_key: str) -> str:
    """The attachment open in zotero.org's reader, from its parent item's link.

    zotero.org redirects `/groups/<id>/items/<item>/attachment/<key>/reader` to the
    same path with the group's name in it, so the link needs only the group id.
    """
    return f"{item_url.rstrip('/')}/attachment/{attachment_key}/reader"


def source_json(source: Source, formats: Mapping[str, str] | None = None) -> dict[str, Any]:
    """A cited source as the client shows it: no passage text, plus its locator.

    `formats` maps attachment keys to their document format; a source from a PDF
    links straight to the file in the reader, any other to its item.
    """
    data = source.model_dump(exclude={"excerpts", "distance"})
    data["locator"] = source.locator
    key = source.attachment_key
    if source.zotero_url and key and (formats or {}).get(key) in READER_FORMATS:
        data["zotero_url"] = reader_url(source.zotero_url, key)
    return data


def total_cost(answer: Answer) -> float:
    """What the question cost, as providers reported it; unpriced parts count as 0."""
    usage = answer.result.usage
    parts = [usage.answer_cost, usage.rerank_cost, usage.embedding_cost]
    if answer.result.condensed is not None:
        parts.append(answer.result.condensed.cost)
    return round(sum(part or 0.0 for part in parts), 8)


def usage_json(answer: Answer) -> dict[str, Any]:
    usage = answer.result.usage
    condensed = answer.result.condensed
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "condense_tokens": (condensed.prompt_tokens + condensed.completion_tokens)
        if condensed
        else 0,
        "elapsed_s": round(usage.elapsed_s, 2),
        "total_cost": total_cost(answer),
        "cost_source": answer.cost_source,
        "reranked": answer.result.reranked,
    }
