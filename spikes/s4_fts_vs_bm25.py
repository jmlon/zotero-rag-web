"""Spike S4 (PLAN.md): Postgres full-text search vs BM25 as the keyword half of hybrid.

Which chunks the two match is identical by construction (both see the tokens of
`retrieval.tokenize`, which zotero-rag-core's tests check). What can differ is the
*order*: `ts_rank_cd` is not BM25. This measures whether that order costs anything,
on a real library and its golden set, at the depths the query pipeline uses:

- keyword only: does each search put the right paper, and the passage that answers
  the question, within its first k results?
- fused: the vector half is the same for both (from the index folder's own Chroma
  store), so any difference after reciprocal-rank fusion is the keyword half's.

Reads an index folder (default: the `kg` folder) and its `questions.yaml`, and
starts a throwaway pgvector container. The fused comparison embeds each question
once with the folder's embedding model (36 questions: a fraction of a cent);
`--no-embed` skips it.

    ../zotero-rag/.venv/bin/python spikes/s4_fts_vs_bm25.py [INDEX_FOLDER] [--no-embed]
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from testcontainers.postgres import PostgresContainer

from zotero_rag.config import load_config, load_env
from zotero_rag_core.embedding import build_embeddings
from zotero_rag_core.evaluation import EvalQuestion, evidence_found, load_questions
from zotero_rag_core.retrieval import (
    BM25Searcher,
    candidate_depth,
    reciprocal_rank_fusion,
)
from zotero_rag_core.store import StoredChunk, VectorStoreError
from zotero_rag_core.stores.chroma import CHROMA_DIRNAME, ChromaStore
from zotero_rag_core.stores.pg import PgFullTextSearcher, PgStore

HERE = Path(__file__).parent
OUT = HERE / "out"
DEFAULT_FOLDER = Path.home() / "Downloads/Papers/AI-KG/AI/KG"
#: top_k 7 with a reranker fanout of 4 retrieves 28 candidates, and hybrid runs each
#: retriever candidate_depth(28) = 112 deep before fusing: the depths that matter.
DEPTHS = (7, 28, 112)
FUSED_CUTS = (7, 28)

Search = Callable[[str, int], list[StoredChunk]]


def item_rank(chunks: Sequence[StoredChunk], question: EvalQuestion) -> int | None:
    """1-based rank of the first chunk from an expected item, or None."""
    expected = set(question.expected_items)
    for rank, chunk in enumerate(chunks, 1):
        if chunk.metadata.get("item_key") in expected:
            return rank
    return None


def passage_hit(chunks: Sequence[StoredChunk], question: EvalQuestion) -> bool | None:
    """Every expected quote is in the chunks: the eval's own passage-hit rule."""
    if not question.expected_evidence:
        return None
    texts = [chunk.text for chunk in chunks]
    return all(evidence_found(evidence, texts) for evidence in question.expected_evidence)


def score(results: dict[str, list[StoredChunk]], questions: list[EvalQuestion], cut: int) -> dict[str, Any]:
    ranks = [item_rank(results[q.id][:cut], q) for q in questions]
    passages = [passage_hit(results[q.id][:cut], q) for q in questions]
    with_evidence = [hit for hit in passages if hit is not None]
    return {
        "item_hit": sum(rank is not None for rank in ranks),
        "item_mrr": round(mean(1 / rank if rank else 0.0 for rank in ranks), 3),
        "passage_hit": sum(with_evidence),
        "with_evidence": len(with_evidence),
    }


def main() -> int:
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    folder = Path(args[0]) if args else DEFAULT_FOLDER
    embed = "--no-embed" not in sys.argv
    OUT.mkdir(exist_ok=True)

    chroma = ChromaStore(folder / CHROMA_DIRNAME)
    chunks = chroma.all_chunks()
    questions = load_questions(folder / "questions.yaml")
    print(f"{folder}: {len(chunks)} chunks, {len(questions)} questions, "
          f"dimension {chroma.derived_dimension()}")
    report: dict[str, Any] = {"folder": str(folder), "chunks": len(chunks),
                              "questions": len(questions)}

    container = PostgresContainer("pgvector/pgvector:pg17", driver=None)
    container.start()
    try:
        store = PgStore.from_url(container.get_connection_url(), create=True)

        # The folder's real vectors: does the HNSW index take this width?
        width = chroma.derived_dimension() or 0
        raw = chroma._collection.get(ids=[chunks[0].id], include=["embeddings"])  # noqa: SLF001
        try:
            store.add([chunks[0].id], [list(map(float, raw["embeddings"][0]))],
                      [chunks[0].text], [chunks[0].metadata])
            report["real_vectors"] = f"{width}-dimension vectors accepted"
        except (VectorStoreError, ValueError) as exc:
            report["real_vectors"] = f"{width}-dimension vectors refused: {str(exc).splitlines()[0]}"
        print("real vectors:", report["real_vectors"])
        store.reset()
        store.delete_ids([chunks[0].id])

        # Keyword search ignores vectors, so a placeholder keeps the index happy.
        store.add([c.id for c in chunks], [[1.0, 0.0, 0.0]] * len(chunks),
                  [c.text for c in chunks], [c.metadata for c in chunks])
        searchers: dict[str, Search] = {
            "bm25": BM25Searcher(chunks).search,
            "postgres": PgFullTextSearcher(store).search,
        }

        deepest = max(DEPTHS)
        keyword = {name: {q.id: search(q.question, deepest) for q in questions}
                   for name, search in searchers.items()}

        # Same matches, as the core's tests promise?
        unlimited = len(chunks)
        mismatched = [
            q.id for q in questions
            if {c.id for c in searchers["bm25"](q.question, unlimited)}
            != {c.id for c in searchers["postgres"](q.question, unlimited)}
        ]
        report["matched_sets_differ"] = mismatched
        print("questions whose matched chunks differ:", mismatched or "none")

        report["keyword_only"] = {
            name: {cut: score(results, questions, cut) for cut in DEPTHS}
            for name, results in keyword.items()
        }
        report["overlap"] = {
            cut: round(mean(
                len({c.id for c in keyword["bm25"][q.id][:cut]}
                    & {c.id for c in keyword["postgres"][q.id][:cut]})
                / max(1, min(cut, len(keyword["bm25"][q.id])))
                for q in questions), 3)
            for cut in DEPTHS
        }

        if embed:
            load_env_from(folder)
            config = load_config(folder)
            embeddings = build_embeddings(config)
            vectors = embeddings.embed_documents([q.question for q in questions])
            fused: dict[str, dict[str, Any]] = {}
            for name in searchers:
                results: dict[str, list[StoredChunk]] = {}
                for q, vector in zip(questions, vectors, strict=True):
                    depth = candidate_depth(max(FUSED_CUTS))
                    vector_hits = chroma.query(vector, depth)
                    results[q.id] = reciprocal_rank_fusion(
                        [vector_hits, keyword[name][q.id][:depth]], max(FUSED_CUTS)
                    )
                fused[name] = {cut: score(results, questions, cut) for cut in FUSED_CUTS}
            vector_only = {q.id: chroma.query(v, max(FUSED_CUTS)) for q, v in zip(questions, vectors, strict=True)}
            fused["vector only"] = {cut: score(vector_only, questions, cut) for cut in FUSED_CUTS}
            report["fused"] = fused
    finally:
        container.stop()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = OUT / f"s4_{stamp}.json"
    path.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k in
                      ("keyword_only", "overlap", "fused")}, indent=1))
    print(f"\nwritten to {path}")
    return 0


def load_env_from(folder: Path) -> None:
    """The CLI's own `.env` layering, as if run from the index folder."""
    cwd = Path.cwd()
    try:
        os.chdir(folder)
        load_env()
    finally:
        os.chdir(cwd)


if __name__ == "__main__":
    sys.exit(main())
