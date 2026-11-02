# zotero-rag-web — Product requirements

A web chat for a small team to ask questions of the Zotero **group library** they share,
and get answers cited back to the item and page they came from.

It is a separate project from the `zotero-rag` CLI (`../zotero-rag`). It reuses that
project's tested pipeline: Zotero access, attachment planning, extraction, chunking,
hybrid retrieval, reranking and cited answering. To do that, those parts are extracted
into a shared library package, `zotero-rag-core` (§9). The web app adds the hosted parts
the CLI never needed: a shared Postgres store, a scheduled indexer, multi-turn
conversations, a browser UI and a one-image deployment.

Status: draft for review, 2026-11-01.

## 1. Goals

1. **Ask the whole library.** Anyone on the team opens a URL and asks questions in
   English or Spanish about everything in the group library, indexed from the root.
2. **Trustworthy answers.** Every claim is cited, `[n]`, to an item and a page or
   section. Each citation links to the item on zotero.org. When the library has nothing
   relevant, the app says so (the same contract as `zotero-rag query`, PRD.md §8.1 of
   the CLI).
3. **Follow-up questions.** A conversation keeps context: "and what does the second
   paper say about latency?" works.
4. **Narrow the search.** A question can be limited to a collection subtree, a
   publication-year range and/or a set of tags.
5. **Easy to deploy and maintain.** One Docker image, one managed Postgres, and settings
   from environment variables. It is first-class on Railway and portable without code
   changes to Render, DigitalOcean App Platform and Google Cloud Run.
6. **Same answer quality as the CLI.** With the CLI's measured best configuration, the
   hosted retrieval stack must score within noise of the CLI on the CLI's question sets
   (§12).

### 1.1 Non-goals for v1

- **User accounts and authentication.** v1 is open to anyone with the URL (§8). The
  data model is ready for OAuth (§11.2).
- **More than one library per deployment.** One deployment serves one Zotero group.
  A second group needs a second deployment.
- **Settings in the UI.** Models, `top_k`, the reranker and chunking are set by the
  operator through environment variables (§7).
- **Writing to Zotero.** The web app only reads. Creating Markdown transcripts
  (`zotero-rag convert`) and `shrink-storage` stay in the CLI. The web indexer *uses*
  transcripts that already exist in the library.
- **Evaluation inside the app.** `eval` and `generate-questions` stay in the CLI. They
  can point at the hosted database (§12).
- **OCR, linked-URL fetching, linked files.** Same limits as the CLI (README
  "Limitations").

### 1.2 Planned for later releases

- OAuth sign-in (Google, Microsoft 365, GitHub, …), with each anonymous browser's
  history attached to the account on first sign-in (§11.2).
- Shareable links to a conversation.
- Showing the cited passage inline, and opening the PDF at the cited page.
- A settings/admin page, once there is authentication to protect it.
- A "Sync now" button and near-real-time sync.
- Filters by author and item type.

## 2. Users and usage

- **Who:** a research group, lab or small consultancy of about 2 to 30 people who
  share one Zotero group library.
- **Library size:** under 1,000 items. Sizing (§10.4) assumes up to about 100k chunks.
- **Load:** at most a few concurrent users. A question takes about 3–6 s end to end
  (CLI measurement: 3.7 s with the reranker).
- **Languages:** questions and papers in English and Spanish, freely mixed. The UI
  ships in both (§6.6).

## 3. Decisions taken

| Topic | Decision | Why |
|---|---|---|
| Vector DB | **Postgres + pgvector** | The containers stay stateless on all four platforms, and every platform offers managed Postgres. One database holds vectors, keyword search, the collection tree, conversations and usage. Chroma would need a second stateful service with a volume. |
| Keyword half of hybrid | **BM25 computed in SQL** over a tsvector of BM25's own tokens (S4) | Replaces the in-memory BM25 that the CLI rebuilds at every start (`retrieval.py` `KeywordIndex`). Parity is verified by eval (§12). |
| Library | **One Zotero group**, from the root, including unfiled items | One deployment serves one team. |
| Frontend | **React (Vite, TypeScript) SPA** served by **FastAPI** from the same container | The richest chat UX, and the JSON/SSE API can be reused later. |
| Sync | **Scheduled incremental index job** (Railway cron) | There is no indexing inside web requests. |
| Conversations | **Multi-turn**, with the question condensed before retrieval | Natural follow-ups. |
| History | **Server-side, per browser** (anonymous cookie id) | Private by default, and survives a cache clear on other devices once auth arrives. |
| Access | **Open URL** in v1, protected by rate limits and a spend cap (§8) | Your call. The guards keep cost bounded. |
| Filters | Collection subtree, year range, tags | |
| Settings | Environment variables only | |
| Code reuse | **Extract `zotero-rag-core`** from the CLI repo | One source of truth for the RAG logic. |
| Indexed content | Attachments + Zotero notes, preferring existing Markdown transcripts | The same `attachment_preference` as the CLI. |
| Citations | Open the item on zotero.org | |
| UI languages | English + Spanish | |
| First platform | Railway | |

## 4. User experience

### 4.1 Layout (desktop)

```
┌──────────────────────┬──────────────────────────────────────────────────────┐
│ + New conversation   │  Filters: [Collection: Methods ▾] [2015–2024] [#RAG] │
│                      ├──────────────────────────────────────────────────────┤
│ Today                │  You: ¿Qué es retrieval-augmented generation?        │
│  · RAG evaluation    │                                                      │
│  · Abstracts         │  Assistant: RAG combina un recuperador con un        │
│ Last week            │  generador … [1] … [2]                               │
│  · Latency in SDN    │                                                      │
│                      │  Sources                                             │
│                      │   [1] Lewis et al. (2020) Retrieval-Augmented …, p. 3│
│                      │   [2] Gao et al. (2024) RAG survey, § Methods        │
│                      ├──────────────────────────────────────────────────────┤
│                      │  [ Ask a question…                          ] (Send) │
│ EN | ES              │  Library: 812 items · synced 2h ago                  │
└──────────────────────┴──────────────────────────────────────────────────────┘
```

On a phone the conversation list collapses into a drawer, and the filters collapse into
a single "Filters (2)" button.

### 4.2 Asking

- **Enter** sends the question and **Shift+Enter** starts a new line. The maximum
  length is 2,000 characters.
- While the answer is prepared, a status line shows *Searching the library…*, then
  *Ranking passages…*, then the answer streams in as Markdown.
- A **Stop** button cancels the stream. The partial answer is kept and marked as
  stopped.
- When the answer is complete, **Sources** lists only the cited passages, numbered as
  in the text. This is the same rule as `QueryResult.sources` with `cited=True`.
- Each `[n]` marker in the answer is a chip. Hovering or tapping it shows the title,
  authors, year and locator. Clicking it opens the item on zotero.org in a new tab.
- **Copy** copies the answer with its sources as Markdown, in the same format as the
  TUI's Ctrl+L (`tui/export.py`).
- **Retry** re-asks the last question, for example after an error.

### 4.3 Conversations

- A new conversation starts empty, with no filters (the whole library).
- The title is the first question, truncated to 60 characters. It can be renamed.
- The list is grouped by Today / Previous 7 days / Older, and a conversation can be
  deleted.
- Each conversation keeps its filters. Changing the filters mid-conversation applies to
  the next question, and the change is shown as a divider line in the thread
  ("Filters changed: Collection = Methods").
- Conversations belong to the browser (§8.2). Another browser does not see them.

### 4.4 Follow-ups

- The last **6 turns** (configurable) of the conversation are context.
- Before retrieval, a follow-up is rewritten into a standalone question (§5.3). The
  rewritten question is stored, and the user can view it under "Searched for: …" on
  each answer. This shows *why* those sources came back.
- The first question of a conversation is never rewritten, so it costs no extra call.

### 4.5 Filters

| Filter | Control | Semantics |
|---|---|---|
| Collection | A tree picker over the group's collections, plus "Whole library" | The item is in the chosen collection or any collection below it. This is the TUI's subtree rule (`snapshot.subtree_keys`). |
| Year | A two-handled range, bounded by the library's min/max year, plus "include undated" (on by default) | `year BETWEEN a AND b`, plus items without a parseable year if checked |
| Tags | A multi-select with autocomplete from the library's tags | The item has **any** of the chosen tags |

Filters combine with AND. If a filtered search finds nothing, the answer says so and
offers "Search the whole library instead" as a one-click retry.

### 4.6 Library status

The footer shows the item count, the chunk count and when the last sync finished. If
the last sync failed, or is more than 2 days old, a warning badge appears. Its tooltip
gives the time of the last successful sync.

### 4.7 Errors

All errors are shown in the user's UI language and come from server error codes
(§6.4), never raw exception text:

| Situation | Message (EN) |
|---|---|
| Rate limit hit | You've asked a lot of questions in a short time. Try again in N minutes. |
| Daily spend cap reached | The team's daily question budget is used up. It resets at 00:00 UTC. |
| Model/provider failure | The language model didn't respond. Retry? |
| Index empty / never synced | The library hasn't been indexed yet. |
| Embedding model mismatch | The index is being rebuilt; questions are paused. |

## 5. Answering pipeline

### 5.1 Single question (first turn)

This is the CLI's `querying._prepare` → answer → `_finish`, unchanged in order:

1. Embed the question (`embedding_model`, default `openai/text-embedding-3-small`).
2. **Retrieve wide.** Vector kNN and full-text search, each `candidate_depth(top_k ×
   fanout)` deep and each restricted by the filters. They are fused with reciprocal
   rank fusion (`retrieval.reciprocal_rank_fusion`, k = 60).
3. **Rerank.** `voyageai/rerank-3-lite` picks the best `top_k` (7) from `top_k ×
   reranker_fanout` (28) candidates. If the reranker fails, the app falls back to the
   fused order (`reranking.apply_rerank`).
4. Number the sources and build the context (`collect_sources`, `build_context`).
5. Stream the answer from `llm_model` (`openai/gpt-6-luna`) with the CLI's system prompt
   (`querying.SYSTEM_PROMPT`), extended for history (§5.3).
6. Parse the citations and keep only the cited sources. Record tokens, cost and latency.

If nothing is retrieved, the app makes no LLM call and returns the "I don't know"
answer (`NO_CONTEXT_ANSWER`).

These defaults are the CLI's best measured configuration (CLI README, "A second study"):
score 4.83/5, 33/33 passage hit, about $0.33 per 1,000 queries.

### 5.2 Retrieval in Postgres

Both retrievers are implemented in the core (`zotero_rag_core.stores.pg`, built in M0).

- **Vector:** `PgStore.query` orders by cosine distance on an HNSW index over
  `embedding::vector(N)`. Index folders' Chroma uses squared L2, not cosine.
  For the unit-length vectors embedding models return, both rank identically, but
  the distance values are not comparable across the two stores.
- **Keyword:** `PgFullTextSearcher` matches through a `tsv` column built from the
  core's own `retrieval.tokenize` output, and ranks with **BM25 computed in SQL**
  (core 0.5.1). Postgres's own `ts_rank_cd` was measured and rejected (S4,
  docs/spikes.md): without inverse document frequency, it made hybrid retrieval
  worse than no keyword search. The SQL version returns exactly what the CLI's
  in-memory BM25 returns. It needs a `term_df` table, refreshed after each sync.
  No `unaccent` extension is needed.
- **Filters** become a `WHERE` on the joined `items` row, in both retrievers, *before*
  the limit. This is the same guarantee as the CLI's scoped search, where both
  retrievers apply the scope before their own cut. Filtered HNSW uses pgvector ≥ 0.8
  iterative scans (`hnsw.iterative_scan = relaxed_order`), so a narrow filter still
  returns `depth` rows. A test forces the HNSW path; without the iterative scan it
  returned 0 of 10 rows.

### 5.3 Follow-ups

For turn *n* > 1:

1. **Condense.** One call to `condense_model` (by default the same as `llm_model`) with
   the last *H* turns (questions + answers, citation markers stripped) and the new
   question. It returns a standalone question in the language of the new question. The
   prompt says to return the question unchanged if it is already standalone.
2. Retrieve and rerank with the **standalone** question (steps 1–4 above).
3. **Answer.** The messages are: the system prompt, then the last *H* turns as plain
   user/assistant messages with citation markers stripped, then a final user message
   with the numbered context passages and the **original** question. The prompt adds
   one rule: *"Earlier turns are conversation context only. Cite only the numbered
   passages in this message."* Citation numbers are therefore always local to one
   answer. Older answers' `[n]` refer to their own source lists, which are kept with
   each message.

The cost per follow-up is one extra small LLM call (about +30–50% of the answer cost,
measured in M3). An operator can disable condensing (`CONDENSE=false`), in which case
the raw question is used for retrieval.

### 5.4 What `zotero-rag-core` must provide for this

- `stream_answer_question(...)` accepting `history: list[Turn]` and a `standalone`
  question distinct from the displayed one (§9.2).
- A `Filters` object replacing `Scope` (collection keys + year range + tags), pushed
  down to the store.
- Store and keyword search as protocols, so the Postgres implementations plug in.

## 6. Architecture

```
                  ┌──────────────────────── Railway project ────────────────────────┐
 Browser ──HTTPS──▶  web  (Docker image: FastAPI + built React assets)              │
   React SPA      │   /api/*  JSON + SSE          ─┐                                │
                  │   /*      static SPA            │  psycopg pool                 │
                  │                                 ▼                               │
                  │                        Postgres 17 + pgvector ◀── indexer (cron)│
                  │                                 ▲          same image,          │
                  └─────────────────────────────────┼──────────  `zrw index`  ──────┘
                                                    │
         OpenRouter (embed, rerank, chat) ◀─────────┴────────▶ Zotero Web API (read)
```

- **One image, two process types.** `web` runs `uvicorn zotero_rag_web.app:app`.
  `indexer` runs `zrw index` on a schedule and exits. Both read the same env vars.
- **Stateless containers.** All state is in Postgres. Attachments are downloaded to a
  temp dir per run and discarded. There is no `cache/` directory: transcripts make most
  re-downloads unnecessary, and a <1k-item library re-downloads cheaply.
- **No background work in `web`.** Indexing is never triggered from a request in v1.

### 6.1 Backend

- Python 3.12, FastAPI, Uvicorn. psycopg 3 with a connection pool. SQLAlchemy Core for
  queries and Alembic for migrations (§10).
- `pydantic-settings` for configuration (§7).
- The core pipeline is synchronous (langchain/httpx). A question runs in a worker
  thread, and tokens are passed to the async SSE generator through
  `loop.call_soon_threadsafe` → `asyncio.Queue`. This needs no rewrite of the core to
  async, and is ample for the expected load.
- The CLI's `zotero_rag.tui.session.QuerySession` is the model for "open once, ask
  many": the embedder, reranker, chat client and store pool are built at startup and
  shared.

### 6.2 Frontend

- React 19 + TypeScript + Vite, built in a multi-stage Dockerfile and served by
  FastAPI `StaticFiles` with an SPA fallback.
- Libraries: `react-markdown` + `remark-gfm` for answers, TanStack Query for API
  state, `react-i18next` for i18n, and a small component kit (Radix primitives +
  Tailwind). No global state library.
- SSE is consumed with `fetch` + `ReadableStream`, because `EventSource` cannot POST.

### 6.3 API

All routes are under `/api`, JSON, and same-origin (no CORS).

| Method & path | Purpose |
|---|---|
| `GET  /api/library` | Library name, item/chunk counts, year bounds, last sync status |
| `GET  /api/collections` | The collection tree (key, name, parent, item count) |
| `GET  /api/tags?q=` | Tag autocomplete (top 20 by item count) |
| `GET  /api/conversations` | This browser's conversations (id, title, updated_at) |
| `POST /api/conversations` | Create (optional filters) |
| `GET  /api/conversations/{id}` | Messages with sources and filters |
| `PATCH /api/conversations/{id}` | Rename, change filters |
| `DELETE /api/conversations/{id}` | Delete |
| `POST /api/conversations/{id}/messages` | Ask. Responds with `text/event-stream` |
| `GET  /healthz` | Liveness + DB reachable |

The SSE events for `POST …/messages`:

```
event: status   data: {"stage": "condensing" | "retrieving" | "reranking" | "answering"}
event: query    data: {"standalone": "…"}                 # follow-ups only
event: token    data: {"text": "…"}
event: done     data: {"message_id": "…", "answer": "…", "sources": [Source…],
                       "usage": {...}, "reranked": true}
event: error    data: {"code": "rate_limited", "retry_after_s": 1200}
```

`Source` is the core's `querying.Source` serialized (number, item_key, title, authors,
year, locator, zotero_url, cited).

### 6.4 Error codes

`rate_limited`, `budget_exhausted`, `llm_failed`, `index_empty`, `index_mismatch`,
`question_too_long`, `not_found`, `internal`. The UI maps each to a localized message.

### 6.5 Indexer

`zrw index` performs one incremental sync of the whole group:

1. Take a Postgres **advisory lock**. If another run holds it, log and exit 0.
2. Read `library.last_version`. Fetch the collections, the items changed since that
   version (`?since=`), and the deletions since that version (`/deleted?since=`).
   On the first run, fetch everything.
3. Upsert `collections` and `items` (title, authors, year, tags, collection keys,
   web URL). **Metadata-only changes never re-embed.** This is the CLI's
   `refresh_collection_keys` idea, extended to tags and year.
4. Plan the attachments with the core's `build_plan` (attachment preference,
   transcripts preferred, notes on). Embed new attachments and those with a changed
   version, in 4 workers (`index_plan`, `workers=4`). One attachment's failure never
   aborts the run.
5. **Deletions:** items deleted or trashed in Zotero are removed, with their chunks.
   Safety valve: if the deletions would remove more than `PRUNE_MAX_FRACTION` (20%)
   of the indexed items, the run stops and records an error instead. An operator can
   then run `zrw index --allow-mass-prune`.
6. Store the new `last_version`, write an `index_runs` row (stats, failures,
   duration), and release the lock.

Other commands: `zrw index --full` (re-plan everything and ignore `since`),
`zrw reindex --repair` (re-embed the stored text with a new embedding model, which is
the CLI's `--repair`), `zrw reindex --rechunk` (re-chunk from source after a chunk
size change), `zrw status`, and `zrw migrate`.

The fingerprint (`fingerprint.StoreFingerprint`: embedding model, dimension, chunking)
is kept in `library.fingerprint`. If `web` finds that the configured model does not
match it, it refuses questions with `index_mismatch` instead of mixing vector spaces.
This is the CLI's existing guarantee.

### 6.6 Internationalization

- UI strings live in `web/src/locales/{en,es}.json`. The language is detected from
  `navigator.language`, can be overridden with the EN | ES toggle, and is remembered
  in `localStorage`.
- Dates and numbers use `Intl`.
- Answers follow the language of the question (system prompt), independent of the UI
  language.

## 7. Configuration

All configuration comes from environment variables, read by `pydantic-settings`. The
CLI's `config.yaml` model lists (`embedding_models`, `rerankers` with prices) ship as
package data in the core.

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | — | Required. Railway injects it from the Postgres service. |
| `ZOTERO_GROUP_ID` | — | Required. Numeric group id. |
| `ZOTERO_API_KEY` | — | Required. **Read-only** key with access to the group. |
| `OPENROUTER_API_KEY` | — | Required. Use a key with an OpenRouter credit limit (§8.3). |
| `LLM_MODEL` | `openai/gpt-6-luna` | |
| `CONDENSE_MODEL` | = `LLM_MODEL` | |
| `CONDENSE` | `true` | |
| `HISTORY_TURNS` | `6` | |
| `EMBEDDING_MODEL` | `openai/text-embedding-3-small` | Changing it requires `zrw reindex --repair`. At most 2,000 dimensions (pgvector's index limit); a wider model is rejected at startup. |
| `RERANKER` | `voyageai/rerank-3-lite` | `none` disables. |
| `TOP_K` / `RERANKER_FANOUT` | `7` / `4` | |
| `RETRIEVAL` | `hybrid` | `vector` disables full-text search. |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `1000` / `200` | Changing them requires `zrw reindex --rechunk`. |
| `ATTACHMENT_PREFERENCE` | `markdown,pdf,docx,odt,epub,html,txt` | |
| `INDEX_NOTES` | `true` | |
| `INDEX_WORKERS` | `4` | |
| `PRUNE_MAX_FRACTION` | `0.2` | |
| `RATE_LIMIT_PER_BROWSER_HOUR` | `30` | |
| `RATE_LIMIT_PER_IP_HOUR` | `60` | |
| `DAILY_SPEND_CAP_USD` | `5.00` | `0` disables. |
| `MAX_QUESTION_CHARS` | `2000` | |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json` | |
| `COOKIE_SECURE` | `true` | `false` for local http dev. |

## 8. Access, abuse and cost controls (v1, no auth)

### 8.1 Exposure

The URL is open. To keep it out of search engines, the app sends
`X-Robots-Tag: noindex`, serves `robots.txt` with `Disallow: /`, and has no
public-facing marketing page. The team treats the URL as semi-private. Platform-level
protection can be added in front without code changes, for example Cloudflare Access
or a Railway private network plus a proxy.

### 8.2 Browser identity

On the first request the server sets `zrw_bid`: a random UUIDv4, `HttpOnly`,
`Secure`, `SameSite=Lax`, valid for 400 days, and signed with `SECRET_KEY` (an
itsdangerous signer) so it cannot be forged into another browser's id. Conversations
are scoped to this id. **This is privacy-by-obscurity, not security.** Anyone with the
URL can use the app, but cannot list another browser's conversations.

### 8.3 Spend protection

- **Per-browser and per-IP rate limits** use a sliding window counted in Postgres
  (from `messages.created_at`). There is no Redis.
- **Global daily spend cap.** The sum of `messages.usage.total_cost` for today (UTC),
  plus a flat estimate per in-flight question. When it reaches the cap, questions get
  `budget_exhausted` until midnight UTC.
- **Backstop:** the OpenRouter key is created with its own credit limit. The runbook
  requires this.
- Input limits: question length, and at most 200 messages per conversation.

## 9. Code reuse: `zotero-rag-core`

### 9.1 Packaging

- The CLI repo becomes a **uv workspace** with two packages:
  - `packages/zotero-rag-core` → import name `zotero_rag_core`, with no CLI or TUI
    dependencies (no typer, rich, textual or chromadb in its base install).
  - the existing `zotero-rag` CLI, which depends on the core.
- Extras on the core: `[chroma]` (the existing Chroma store, used by the CLI),
  `[pgvector]` (the Postgres store and full-text search, used by the web app and
  optionally by the CLI), and `[convert]` (firecrawl-anydoc).
- The web project depends on it as a git dependency pinned to a tag:
  `zotero-rag-core = { git = "https://github.com/jmlon/zotero-rag", subdirectory =
  "packages/zotero-rag-core", tag = "core-v0.5.0" }`. It can move to PyPI later.

### 9.2 What moves, and what changes

| Module (today in `src/zotero_rag/`) | Destination | Change |
|---|---|---|
| `zotero_api`, `planning`, `metadata`, `documents`, `download`, `extraction`, `pdf_text`, `html_text`, `office`, `chunking`, `embedding`, `llm`, `reranking`, `fingerprint`, `errors`, `provenance` | core | Add group-library support. Without a username, take the citation URL from the item's `links.alternate.href` (the CLI keeps its username-based URLs for user libraries). Expose item tags, item type and integer year to planning. |
| `markdown.py` | split | Reading transcripts (`body`) → core. Producing them (anydoc) → core `[convert]` extra. |
| `vectorstore.py` | core | Becomes a `ChunkStore` **protocol** + `ChromaStore` (`[chroma]`). `PgStore` added (`[pgvector]`). |
| `retrieval.py` | core | `KeywordIndex` becomes a `KeywordSearcher` protocol. `BM25Searcher` (current) and `PgFullTextSearcher`. `retrieve()` takes a searcher and `Filters`. |
| `querying.py` | core | `Scope` → `Filters`. Add `history` and `standalone_question` parameters. The rest is unchanged. |
| `config.py` | split | Pydantic models for settings and model catalogues → core. YAML/index-folder loading stays in the CLI. |
| `indexer.py` | core | Uses the `ChunkStore` protocol. The "touch the store only from the calling thread" rule is kept. |
| `evaluation`, `generation`, `experiments`, `embedding_comparison` | core (or CLI) | They are needed only by the CLI, but they should work against any `ChunkStore`, so that `eval` can be run against the hosted Postgres (§12). |
| `cli.py`, `tui/`, `scaffold`, `index_folder`, `shrink*`, `conversion`, `converter_run`, `quality`, `snapshot` | stay in CLI | |

**The rule: no behaviour change for the CLI.** The CLI's test suite passes unchanged
(apart from imports) at every step of the extraction.

**Status: done** (M0, branch `core-extraction` of `zotero-rag`). Imports were rewritten
rather than shimmed, since nothing outside the repo imports the CLI's modules.

## 10. Data model (Postgres)

### 10.1 Tables

The store's own tables come from the core:
`zotero_rag_core/stores/pg_schema.sql`, which the web app's migration 0001 applies
verbatim. The web app adds its own columns to them in later migrations, and its own
tables:

```sql
-- From the core (pg_schema.sql), abridged:
chunk_store    (id smallint pk = 1, fingerprint jsonb)          -- how the store was built
items          (key text pk, item_type text, title text, authors text,
                year text, year_int int,      -- 0 = undated; NULL = not recorded
                doi text, zotero_url text,    -- from the API's links.alternate
                collection_keys text[], tags text[])           -- direct membership
attachments    (key text pk, item_key text references items on delete cascade,
                version bigint, document_format text, collection_name text)
chunks         (id text pk,                      -- <attachment_key>:<locator>:<chunk_index>
                attachment_key text references attachments on delete cascade,
                page int, chunk_index int, locator_kind text, locator_label text,
                embedding_model text, text text,
                embedding vector,                -- no fixed width; see §10.3
                tsv tsvector,                    -- from retrieval.tokenize (§5.2)
                extra jsonb)

-- Added by the web app:
items        + version bigint, date text, updated_at timestamptz
attachments  + content_type text, status text, error text, indexed_at timestamptz
library        (id smallint pk default 1, group_id bigint, name text, web_url text,
                last_version bigint, last_sync_at timestamptz, last_sync_status text)
collections    (key text pk, parent_key text null, name text, path text,
                item_count int)
index_runs     (id bigserial pk, started_at, finished_at, trigger text, status text,
                stats jsonb, failures jsonb)
browsers       (id uuid pk, created_at, last_seen_at, user_id uuid null)  -- user_id: §11.2
conversations  (id uuid pk, browser_id uuid references browsers, title text,
                filters jsonb, created_at, updated_at)
messages       (id uuid pk, conversation_id uuid references conversations on delete cascade,
                role text check (role in ('user','assistant','event')),
                content text, standalone_query text null, sources jsonb null,
                usage jsonb null, settings jsonb null, status text, created_at)
```

Indexes: HNSW on `(embedding::vector(N))` (`vector_cosine_ops`, created by `PgStore`
once N is known), GIN on `chunks.tsv`, btree on `chunks.attachment_key` and
`attachments.item_key`, GIN on `items.tags` and `items.collection_keys`, btree on
`items.year_int` (all from the core), and `messages(conversation_id, created_at)`.

### 10.2 Collection filter

The UI sends one collection key. The server expands it to its subtree with a recursive
CTE over `collections` (or from the cached tree), and filters with
`items.collection_keys && :subtree`. Moving an item between collections updates only
`items`.

### 10.3 Embedding dimension

The `embedding` column has no fixed width. `PgStore` creates the HNSW index for the
width of the first vectors written, and that index then refuses vectors of any other
width, so two models' vectors cannot be mixed by accident. A model change with a
different width is handled by `zrw reindex --repair`, which calls the core's
`repair_embeddings`: it reads the stored text, `reset()`s the chunks and the index,
and re-embeds. No migration is needed.

### 10.4 Sizing

Assume 1,000 items × about 100 chunks = 100k chunks. With 1536-dimension float4
vectors that is about 600 MB of vectors, about 200 MB of text, and the HNSW index on
top. A 1–2 GB Postgres is enough. If storage matters, `halfvec(1536)` halves the
vector size with negligible recall loss. That is an M2 decision, made with eval.

The initial embedding run costs about 100k × 250 tokens = 25M tokens, roughly $0.50
with text-embedding-3-small.

### 10.5 Retention and backups

Conversations are kept until deleted. Railway Postgres backups are enabled (runbook).
The vectors are reproducible from Zotero. The conversations are the only data that
cannot be regenerated.

## 11. Future-proofing

### 11.1 Other platforms

Nothing is Railway-specific beyond `railway.json`. The plan ships notes for:

- **Render:** `render.yaml` with a web service, a cron job and Postgres.
- **DigitalOcean App Platform:** an app spec with a service, a scheduled job and a
  managed PG with the pgvector extension.
- **Cloud Run:** a service and a Cloud Run Job triggered by Cloud Scheduler, with
  Cloud SQL PG through the connector.

### 11.2 Authentication

- An `auth` layer resolves a request to a `principal` (the browser id today, a user
  id later). Every query filters by principal, never by the cookie directly.
- Later: a `users` table, OAuth/OIDC (Authlib) with Google, Microsoft and GitHub,
  and an allow-list of emails or domains. On first sign-in, the current
  `browsers.user_id` is set, so prior anonymous history is adopted.
- The settings page and "Sync now" unlock with an admin role.

## 12. Quality: measuring parity

**Measured (2026-11-02, docs/parity.md):** the same retrieval as the CLI on 36/36
questions (paper and passage hits), score 4.75 vs the CLI's 4.78 on the same chunks.

- The core gains a `[pgvector]` `ChunkStore`. The CLI's `eval` can target it with
  `zotero-rag eval --store postgres://…`, so the **same question sets** (`kg-v3`,
  `questions/*.yaml`) are graded against the hosted retrieval stack.
- Acceptance (M2): with the default configuration, on the `kg-v3` library and set, the
  Postgres stack reaches a mean score ≥ 4.6 (CLI: 4.83) and a passage hit ≥ 31/33
  (CLI: 33/33). With the reranker removed, the hybrid stack must beat vector-only on
  "cited right paper". This proves the full-text search contributes, as BM25 did.
- If full-text search under-performs BM25, the fallback is to keep `BM25Searcher` in
  process. It is built lazily per instance from the chunk text and refreshed when
  `library.last_sync_at` changes. This is acceptable at 100k chunks.
- **Multi-turn check:** a hand-written set of 10 two- and three-turn dialogues in
  `tests/fixtures/dialogues.yaml`. Its acceptance is manual review in M3: the
  standalone question names the referent, and the answer cites the right paper.

## 13. Acceptance criteria for v1

1. A fresh Railway deployment, following the README runbook, indexes a <1k-item group
   from the root and answers a question with working zotero.org citation links. This
   takes ≤ 30 minutes of operator time, excluding the indexing duration.
2. The scheduled indexer picks up a new item, a moved item, a tag change, a replaced
   PDF and a deleted item within one run. Moves and tag changes cause no re-embedding.
3. The time to the first token for a question is ≤ 3 s at p50, and the full answer
   ≤ 8 s at p95, on Railway with default settings.
4. Follow-up questions resolve references (§12 dialogue set). The "Searched for" line
   shows the standalone question.
5. Filters (collection subtree, year, tags) restrict retrieval. A test proves that no
   out-of-filter chunk is ever returned, by the vector retriever or the keyword
   retriever.
6. The rate limits and the daily spend cap block further questions, with localized
   messages.
7. The UI is fully usable in English and Spanish, on desktop and on a 375 px wide
   phone, and passes axe-core with no serious violations.
8. Quality parity (§12).
9. Redeploying `web` loses no conversations, and running two `indexer` jobs at once
   is safe (the advisory lock).

## 14. Risks and open questions

| Risk / question | Mitigation / owner |
|---|---|
| Postgres FTS ranking is weaker than BM25 → hybrid gain lost | **Happened (S4)**: fixed by BM25 in SQL, with identical results. Its latency grows with the library (2.4 s at ~100k chunks), so a postings table is the next step if a library gets that large. |
| Railway's pgvector image is older than 0.8 (no iterative scan) | Spike S2. Otherwise use a custom `pgvector/pgvector:pg17` image service. |
| The open URL leaks and costs money | Spend cap + OpenRouter key limit. Cloudflare Access can be added without code. |
| `links.alternate.href` format for groups | **Resolved (S3, docs/spikes.md):** it holds only the group slug, which a rename breaks. Citations are built from the numeric group id instead. |
| Long first index exceeds a cron run's patience | `zrw index` is resumable (per-attachment commits). Run the first index as a one-off. |
| Core extraction destabilises the CLI | No behaviour change. The CLI test suite gates every step. |
| Repo name and GitHub location of `zotero-rag-web` | **Open: confirm.** |
| Licence (CLI is MIT) | Assumed MIT. **Open: confirm.** |
