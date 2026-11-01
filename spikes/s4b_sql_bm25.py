"""Spike S4b: BM25 computed in SQL, after S4 showed ts_rank_cd ranks badly.

`ts_rank_cd` has no inverse document frequency, so in a corpus searched without
stop words, words like "the" and "what" decide the order. BM25 needs only what a
tsvector column already holds plus document frequencies (`ts_stat`), so this
computes rank_bm25's exact BM25Okapi in SQL and checks:

1. parity: does it rank like `BM25Searcher`, on the `kg` library and its questions?
2. latency: how long does one search take at ~1k chunks and at ~100k chunks
   (the library copied until it is that size, under fresh ids)?

    ../zotero-rag/.venv/bin/python spikes/s4b_sql_bm25.py [INDEX_FOLDER]
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

import psycopg
from testcontainers.postgres import PostgresContainer

from zotero_rag_core.evaluation import load_questions
from zotero_rag_core.retrieval import BM25Searcher, tokenize
from zotero_rag_core.stores.chroma import CHROMA_DIRNAME, ChromaStore
from zotero_rag_core.stores.pg import PgStore

HERE = Path(__file__).parent
OUT = HERE / "out"
DEFAULT_FOLDER = Path.home() / "Downloads/Papers/AI-KG/AI/KG"
DEPTH = 112
SCALE_TARGET = 100_000

# rank_bm25.BM25Okapi's defaults.
K1, B, EPSILON = 1.5, 0.75, 0.25

STATS_SQL = """
DROP TABLE IF EXISTS term_df;
CREATE TABLE term_df AS SELECT word, ndoc FROM ts_stat('SELECT tsv FROM chunks');
CREATE UNIQUE INDEX ON term_df (word);
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS n_tokens integer;
UPDATE chunks SET n_tokens = (SELECT coalesce(sum(cardinality(positions)), 0) FROM unnest(tsv));
"""

# BM25Okapi: idf = ln(N - df + 0.5) - ln(df + 0.5); a negative idf becomes
# EPSILON * (average idf over the whole vocabulary). Query tokens count with their
# multiplicity, as rank_bm25 sums over the query's token list.
SEARCH_SQL = """
WITH q(term) AS (SELECT unnest(%(tokens)s::text[])),
idf AS (
    SELECT q.term,
           CASE WHEN ln((%(n)s - d.ndoc + 0.5) / (d.ndoc + 0.5)) < 0
                THEN %(eps_idf)s ELSE ln((%(n)s - d.ndoc + 0.5) / (d.ndoc + 0.5)) END AS idf
    FROM q JOIN term_df d ON d.word = q.term
),
matched AS (
    SELECT c.id, c.tsv, c.n_tokens FROM chunks c
    WHERE c.tsv @@ to_tsquery('simple', %(query)s)
),
tf AS (
    SELECT m.id, m.n_tokens, u.lexeme, cardinality(u.positions) AS f
    FROM matched m, unnest(m.tsv) u
    WHERE u.lexeme = ANY(%(tokens)s::text[])
)
SELECT tf.id,
       sum(idf.idf * tf.f * (%(k1)s + 1)
           / (tf.f + %(k1)s * (1 - %(b)s + %(b)s * tf.n_tokens / %(avgdl)s))) AS score
FROM tf JOIN idf ON idf.term = tf.lexeme
GROUP BY tf.id
ORDER BY score DESC, tf.id
LIMIT %(limit)s
"""


class SqlBm25:
    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self.conn = conn
        conn.execute(STATS_SQL)
        self.n, self.avgdl = conn.execute("SELECT count(*), avg(n_tokens) FROM chunks").fetchone()
        n = float(self.n)
        average_idf = conn.execute(
            "SELECT avg(ln((%s - ndoc + 0.5) / (ndoc + 0.5))) FROM term_df", (n,)
        ).fetchone()[0]
        self.eps_idf = EPSILON * float(average_idf)
        conn.commit()

    def search(self, question: str, limit: int, informative: bool = False) -> list[str]:
        tokens = tokenize(question)
        if not tokens:
            return []
        matching = sorted(set(tokens))
        if informative:
            # Terms in fewer than half the chunks: the only ones with a positive idf.
            rare = [row[0] for row in self.conn.execute(
                "SELECT word FROM term_df WHERE word = ANY(%s) AND ndoc < %s / 2.0",
                (matching, float(self.n))).fetchall()]
            matching = rare or matching
        rows = self.conn.execute(SEARCH_SQL, {
            "tokens": tokens, "query": " | ".join(matching), "n": float(self.n),
            "avgdl": float(self.avgdl), "eps_idf": self.eps_idf, "k1": K1, "b": B,
            "limit": limit,
        }).fetchall()
        return [row[0] for row in rows]


def overlap(a: list[str], b: list[str], cut: int) -> float:
    return len(set(a[:cut]) & set(b[:cut])) / max(1, min(cut, len(a), len(b)))


def main() -> int:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FOLDER
    OUT.mkdir(exist_ok=True)
    chunks = ChromaStore(folder / CHROMA_DIRNAME).all_chunks()
    questions = load_questions(folder / "questions.yaml")
    report: dict[str, Any] = {"chunks": len(chunks), "questions": len(questions)}

    container = PostgresContainer("pgvector/pgvector:pg17", driver=None)
    container.start()
    try:
        url = container.get_connection_url()
        store = PgStore.from_url(url, create=True)
        store.add([c.id for c in chunks], [[1.0, 0.0, 0.0]] * len(chunks),
                  [c.text for c in chunks], [c.metadata for c in chunks])

        with psycopg.connect(url) as conn:
            # 1. Parity with the in-memory BM25.
            sql = SqlBm25(conn)
            memory = BM25Searcher(chunks)
            per_question = []
            for q in questions:
                want = [c.id for c in memory.search(q.question, DEPTH)]
                got = sql.search(q.question, DEPTH)
                per_question.append({
                    "id": q.id,
                    "same_top_7": want[:7] == got[:7],
                    "overlap_7": overlap(want, got, 7),
                    "overlap_28": overlap(want, got, 28),
                    "overlap_112": overlap(want, got, 112),
                })
            report["parity"] = {
                "identical_top_7": sum(p["same_top_7"] for p in per_question),
                "mean_overlap": {cut: round(mean(p[f"overlap_{cut}"] for p in per_question), 3)
                                 for cut in (7, 28, 112)},
                "worst": sorted(per_question, key=lambda p: p["overlap_28"])[:3],
            }
            print("parity:", json.dumps(report["parity"], indent=1))

            # 2. Latency at the library's size, then at ~100k chunks.
            def timed(informative: bool = False) -> dict[str, float]:
                times = []
                for q in questions:
                    start = time.perf_counter()
                    sql.search(q.question, DEPTH, informative)
                    times.append((time.perf_counter() - start) * 1000)
                return {"median_ms": round(median(times), 1), "max_ms": round(max(times), 1)}

            def parity_of_informative() -> dict[str, Any]:
                rows = []
                for q in questions:
                    want = [c.id for c in memory.search(q.question, DEPTH)]
                    got = sql.search(q.question, DEPTH, informative=True)
                    rows.append((want[:7] == got[:7], overlap(want, got, 28), overlap(want, got, 112)))
                return {"identical_top_7": sum(r[0] for r in rows),
                        "mean_overlap_28": round(mean(r[1] for r in rows), 3),
                        "mean_overlap_112": round(mean(r[2] for r in rows), 3)}

            report["informative_parity"] = parity_of_informative()
            print("informative-terms parity:", report["informative_parity"])
            report["latency"] = {len(chunks): timed()}
            report["latency_informative"] = {len(chunks): timed(True)}
            copies = SCALE_TARGET // len(chunks)
            print(f"copying the library {copies}x ...")
            conn.execute(
                "INSERT INTO chunks (id, attachment_key, page, chunk_index, locator_kind, "
                "locator_label, embedding_model, text, embedding, tsv, extra) "
                "SELECT c.id || '#' || g, c.attachment_key, c.page, c.chunk_index, c.locator_kind, "
                "c.locator_label, c.embedding_model, c.text, c.embedding, c.tsv, c.extra "
                "FROM chunks c, generate_series(1, %s) g", (copies - 1,))
            conn.commit()
            start = time.perf_counter()
            sql = SqlBm25(conn)
            report["stats_refresh_s_at_scale"] = round(time.perf_counter() - start, 1)
            report["latency"][int(sql.n)] = timed()
            report["latency_informative"][int(sql.n)] = timed(True)

            import gc
            import tracemalloc

            loaded = store.all_chunks()
            gc.collect()
            tracemalloc.start()
            start = time.perf_counter()
            big = BM25Searcher(loaded)
            report["in_memory_build_s_at_scale"] = round(time.perf_counter() - start, 1)
            report["in_memory_index_mb_at_scale"] = round(tracemalloc.get_traced_memory()[0] / 2**20)
            tracemalloc.stop()
            del loaded
            times = []
            for q in questions:
                start = time.perf_counter()
                big.search(q.question, DEPTH)
                times.append((time.perf_counter() - start) * 1000)
            report["in_memory_latency_at_scale"] = {"median_ms": round(median(times), 1),
                                                   "max_ms": round(max(times), 1)}
            print("latency:", json.dumps({k: report[k] for k in report if "latency" in k or "_s_" in k}, indent=1))
    finally:
        container.stop()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (OUT / f"s4b_{stamp}.json").write_text(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
