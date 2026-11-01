# Spike results

Findings from the spikes in PLAN.md, and what they change in the design.

## S1 and S3: the Zotero group API (2026-11-01)

Run by `spikes/s1_s3_group_api.py` against the test group `AI_EDU_UPB`
(id 6743389), with a read/write key of the test account. The script created a
collection, a filed paper with tags, an unfiled paper, a child note and an uploaded
PDF. It then edited, trashed and deleted them, and finally removed everything it had
created. The one item already in the group, "A Test Book", was only read.
**All 22 checks passed.** The raw log is `spikes/out/s1_s3_*.json` (not committed).

The checks used `zotero-rag-core` 0.5.0's own fetchers and planner, so they test the
code the indexer will run, not just the API.

### S1: what the incremental indexer can rely on

| Question | Observed |
|---|---|
| Whole-library fetch (`fetch_library_items`) | Returns parents, child notes, attachments and unfiled items in one paginated call. |
| Membership | Each item's own `collections` field is enough (`library_membership`); unfiled items map to `[]`. |
| Planning a group library | The plan indexes the PDF and the note, and skips items with no readable attachment. Type, year, tags and folder all arrive. |
| File download | `file()` on a group attachment returns the PDF bytes. |
| Library version | Advances with every write (5 → 11 for six creates, then one per edit or delete). It works as the sync cursor. |
| `items?since=v` | Returns exactly the items edited after `v`. Editing a parent does **not** bump its children's versions, and items untouched since `v` are not returned. Nothing changed means an empty list. |
| Moving to the trash | The item leaves `/items`, does **not** appear in `items?since`, and is **not** listed in `/deleted`. Only `/items/trash` shows it. This confirms that `fetch_removed_item_keys` must ask both, as it does. |
| Deleting a parent permanently | Its note and attachment are deleted with it, and `/deleted?since` lists all three keys. |
| Deleting a collection | Listed under `collections` in `/deleted?since`. |

Consequences for the indexer (PRD §6.5):

- **One sync per run is enough.** Read the version first, then fetch:
  - `items?since` → upsert,
  - `fetch_removed_item_keys(since)` → delete, which covers both trashed and deleted items,
  - all collections (cheap) → rewrite the tree.
- **A changed attachment arrives without its parent.** Since editing a parent never
  re-sends its children, and vice versa, the indexer must fetch missing parents by key
  (`fetch_items_by_key`), as planned.
- **A trashed item counts as removed.** If someone restores it from the trash, its
  version changes, so it comes back through `items?since` and is re-indexed. That
  costs one re-embedding, which is acceptable.

### S3: citation links

| Form | Example | Result |
|---|---|---|
| API `links.alternate` | `https://www.zotero.org/groups/ai_edu_upb/items/7KARSWB6` | Redirects to the canonical page. |
| Numeric id only | `https://www.zotero.org/groups/6743389/items/VI2Z26FA` | Redirects to the canonical page. |
| Canonical | `https://www.zotero.org/groups/6743389/ai_edu_upb/items/VI2Z26FA` | 200. |
| Right id, wrong slug | `https://www.zotero.org/groups/6743389/renamed_slug/items/VI2Z26FA` | Redirects to the canonical page. |

- **The numeric group id is authoritative and the slug is ignored.** The API's link
  carries only the slug, which changes if the group is renamed, and the stored link
  would then go stale: renaming a group bumps no item versions, so nothing
  re-syncs. **Decision:** the web app builds citation links itself, as
  `https://www.zotero.org/groups/{ZOTERO_GROUP_ID}/items/{item_key}`, rather than
  storing `links.alternate`. This needs a small option in the core's `citation_url`
  (a URL template, or a group id), to be added in M2. `links.alternate` remains the
  right fallback for the CLI.
- **Who can open the links** depends on the group's settings, not on the link.
  The page HTML holds no item data (the web library renders in the browser), so a
  signed-out request cannot show visibility. The group's API record can:
  `AI_EDU_UPB` is `PublicClosed` with `libraryReading: all`, so anyone can open its
  items. In a private group, only signed-in members could. The runbook (M5) should
  tell operators this.
- Attachments and notes have their own links, but citations always point at the
  parent item, which is what readers want.

### Not yet covered

- **Group libraries in the CLI.** `index --collection` on a group with
  `zotero_library_type: group` has not been run end to end. The web indexer does
  not use that path.
- **Rate limits and very large first syncs.** The test group is tiny. A first sync of
  about 1,000 items is about 10 paginated requests (100 items each), plus one file
  download per attachment. Measure it in M2.
- **S2 and S5** are still open (they need Railway).

## S4: Postgres full-text search vs BM25 (2026-11-01)

Run by `spikes/s4_fts_vs_bm25.py` and `spikes/s4b_sql_bm25.py` on the CLI's `kg`
index folder: 12 papers, 1,167 chunks, and the `kg-v3` golden set (36 questions, 33
with a verbatim answering passage). Each run used a throwaway pgvector 0.8.3
container. "Passage" means every expected quote is in the retrieved text, the eval's
own rule.

### Which chunks match: identical

For all 36 questions, Postgres full-text search matched exactly the chunks BM25
matches, as designed (both use `retrieval.tokenize`).

### How they rank: Postgres's ranking is not usable

| Keyword half | Passage in keyword top 7 / 28 / 112 | Passage in **fused** top 7 / 28 |
|---|---|---|
| BM25 (in memory, the CLI's) | 28 / 29 / 33 | **29** / 33 |
| Postgres `ts_rank_cd` | 7 / 14 / 25 | **18** / 31 |
| none (vector only) | – | 26 / 31 |

The fused rows use the folder's own vectors (qwen3-embedding-8b) for the vector half,
which is identical in both, so the difference is the keyword half's alone.
**`ts_rank_cd` makes hybrid retrieval worse than no keyword search at all.**
It has no inverse document frequency, and the `simple` configuration keeps every
word, so common words like "the" and "what" decide the order. BM25 discounts them.

### BM25 computed in SQL: exact, but slow at the top of the range

BM25 needs only what Postgres already has: term counts in each chunk's `tsvector`, and
document frequencies from `ts_stat` (a small `term_df` table refreshed after each
sync). `s4b` computes rank_bm25's exact BM25Okapi formula in SQL.

| | ~1.2k chunks | ~99k chunks (the library copied 85×) |
|---|---|---|
| Ranking vs in-memory BM25 | identical top 7 for 36/36; 100% overlap at 28 and 112 | – |
| SQL BM25, per search | 20 ms | 2.4 s median, 3.2 s max |
| SQL BM25, candidates matched only on words in < half the chunks | 18 ms (still identical) | 1.9 s |
| Statistics refresh after a sync | – | 38 s |
| In-memory BM25, per search | ~1 ms | 300 ms median |
| In-memory BM25, build / memory | – | 12 s / ~1 GB |

The SQL version's cost is unpacking the `tsvector` of every candidate chunk. Almost
every chunk contains some word of the question. A postings table
(`term, chunk_id, tf`, indexed by term) would avoid the unpacking, at the price of
about 10M rows at 100k chunks. That is not measured.

The PRD's sizing is *up to* ~100k chunks (1,000 papers at ~100 chunks each). A
library of a few hundred papers is 20–50k chunks, where both exact options are
roughly 2–5× faster than the 99k figures.

### Also found: wide embedding models cannot use the vector index

Writing the folder's real 4,096-dimension vectors to `PgStore` failed with
`column cannot have more than 2000 dimensions for hnsw index`. pgvector's HNSW takes
at most 2,000 dimensions for `vector` and 4,000 for `halfvec`. The web app's default,
`text-embedding-3-small` (1,536), is fine. qwen3-embedding-8b (4,096) and
other wide models need either an exact scan without an index, or a model that offers
fewer dimensions. Settings validation should reject them with a clear message
instead of failing on the first write.

### Decisions taken (2026-11-01)

1. **Keyword ranking: BM25 computed in SQL** (zotero-rag-core 0.5.1). A
   `term_df` table and corpus totals are refreshed after each sync; a search
   refreshes stale statistics first. Re-running S4 with it: 100% overlap with
   in-memory BM25 at 7, 28 and 112, and the same passage hits, both keyword-only
   (28 / 29 / 33) and fused (**29** / 33). Latency is as measured above. If a
   library ever approaches 100k chunks, a postings table is the next step.
2. **Embedding width: rejected at startup** when over 2,000 dimensions. The core
   now refuses such vectors with a clear message; the web app's settings
   validation should reject the model before that (PLAN M1).
