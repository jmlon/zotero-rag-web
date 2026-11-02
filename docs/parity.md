# Parity: the PostgreSQL store answers like the CLI's Chroma store (M2.5)

PRD §12 requires that the hosted retrieval stack, PostgreSQL with pgvector and BM25
computed in SQL, answer as well as the CLI. Measured on 2026-11-02 with the CLI's own
`zotero-rag eval`, run twice on identical inputs. Only the store differed.

## Setup

- **Library:** the CLI's `kg` index folder (12 papers), question set `kg-v3`
  (36 questions, 33 with an answering quote).
- **Chunks:** the folder's `openai/text-embedding-3-small` variant from
  `compare-embeddings`, at 1500/300 (759 chunks). The `kg` folder's main store uses
  qwen3-embedding-8b, which is too wide for pgvector (S4), and no 1000/200 variant
  exists for this model. Parity compares stores, so the chunking does not matter.
  The same chunks and vectors were loaded into both stores.
- **Pipeline:** the CLI's best configuration: hybrid retrieval, top 7,
  `voyageai/rerank-3-lite` (fanout 4), `openai/gpt-6-luna` answering, and the
  `google/gemini-3.5-flash-lite` judge with the passages in view.
- **Runs:** `zotero-rag eval` on the folder's Chroma copy, then
  `zotero-rag eval --store postgresql://…` on the same chunks in Postgres.

## Result

| | Chroma (CLI) | PostgreSQL (web) |
|---|---|---|
| Mean score | 4.78 | 4.75 |
| Grades | 32 perfect, 3 good, 1 no answer | 31 perfect, 4 good, 1 no answer |
| Right paper retrieved | 36 / 36 | 36 / 36 |
| Answering passage retrieved | 32 / 33 | 32 / 33 |
| Answer cites a source | 100% | 100% |
| Point coverage | 96.1% | 94.2% |
| Reranked | 36 / 36 | 36 / 36 |

- **Retrieval is the same.** The paper hit and the passage hit agree on every one of
  the 36 questions, and the retrieved sources are identical for 35. In the remaining
  one, Postgres retrieved one extra source; both found the answering passage. Both
  stores use approximate (HNSW) vector search, so the 112-deep candidate lists can
  differ at the margin.
- **The score difference is answering and grading variation, not retrieval.** The
  one question whose grade changed (`iga-2024-3`, 5 → 4) had identical sources in
  both runs. 0.03 is well within the 0.25 seen between identical CLI runs.
- **The PRD's acceptance thresholds are met:** mean score ≥ 4.6 (4.75) and passage
  hit ≥ 31/33 (32/33).

Cost of the two runs: about $0.10, of which the judge was $0.08.
