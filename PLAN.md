# zotero-rag-web — Implementation plan

This plan implements `PRD.md`. The work spans two repositories:

- **`zotero-rag`** (`../zotero-rag`, the CLI) — M0 extracts `zotero-rag-core` there.
- **`zotero-rag-web`** (this folder) — M1 to M6.

Each milestone ends in a working, tested state and its own tag. Section numbers (§) refer
to `PRD.md`.

## Spikes (before or alongside M0, each about ½ day)

| # | Question | How to answer it | Decides |
|---|---|---|---|
| S1 | Does the group API give us everything we need? | Using pyzotero with `library_type="group"`: list collections, `items(since=v)`, `deleted(since=v)`, `file()` for a group attachment, and the `Last-Modified-Version` header. | §6.5 incremental sync |
| S2 | Which pgvector version runs on Railway? (Must be ≥ 0.8 for iterative scans; `unaccent` is no longer needed.) | Deploy Railway's pgvector template. Run `SELECT extversion FROM pg_extension` and `SET hnsw.iterative_scan = relaxed_order`. | Template vs a custom `pgvector/pgvector:pg17` service |
| S3 | What do group item web URLs look like? | Print `links.alternate.href` for 3 group items, and open them while signed in and signed out. | Citation URL source |
| S4 | Postgres FTS vs BM25 *ranking* | Which chunks match is identical by construction (tested in M0). Copy the CLI's `kg` store into a local pg (`ChromaStore.all_chunks()` → `PgStore.add`), and compare the keyword-only top-20 *order* with `BM25Searcher.search` on the `kg-v3` questions. | Early warning for §12 |
| S5 | Railway cron semantics | A cron service whose run sleeps 20 min on a 15-min schedule: is the next run skipped? Are there logs per run? Is a timeout enforced? | Index schedule, first-run procedure |

The findings go in `docs/spikes.md`.

**S1, S3 and S4 are done** (2026-11-01); see `docs/spikes.md`. S4 replaced Postgres's
ranking with BM25 computed in SQL (core 0.5.1), so the web app depends on
`core-v0.5.1`. One change
follows from them: in M2, give the core's `citation_url` a group-id link form, so
citations survive a group rename.

---

## M0 — Extract `zotero-rag-core` (in `../zotero-rag`)

Goal: the CLI runs on a workspace package with **no behaviour change**, and the core
exposes the seams the web app needs. Tag `core-v0.5.0`.

> **Status (2026-11-01): built**, on branch `core-extraction` of `zotero-rag`, one
> commit per step (M0.1+M0.2 together). 1,258 tests pass, including the Postgres tests
> against a real pgvector 0.8.3. The branch is not merged or tagged yet.
> Where the build differs from the steps below:
>
> - **No re-export shims.** Imports in the CLI, tests and notebook were rewritten
>   instead, since nothing outside the repo imports the CLI's modules.
> - **`markdown.py`**: only `body` moved to the core. Conversion stays in the CLI,
>   because HTML transcripts are stamped with the *CLI's* version and that stamp
>   decides when they are redone. There is no `[convert]` extra.
> - **Logging**: moved modules log under `zotero_rag_core.*`, so `-v` now raises
>   both package loggers (`zotero_rag.PACKAGE_LOGGERS`).
> - **Names**: `vectorstore.VectorStore` → `stores.chroma.ChromaStore`;
>   `KeywordIndex` → `BM25Searcher` (the `keyword_index=` parameter name is kept);
>   the citation URL stays in `PlannedAttachment.zotero_url` (no separate `web_url`).
> - **Filters** carry a year as `year_int` (0 = undated) and `tags` in chunk metadata;
>   chunks indexed before M0 lack them and fail year/tag filters until re-indexed.
> - **Follow-ups**: the condense call's cost is on `QueryResult.condensed`, not in
>   `Usage`, so `query --json` output is unchanged.
> - **Removed items**: `fetch_removed_item_keys` asks both `/deleted` and
>   `/items/trash`, since a trashed item is in neither `/items` nor `/deleted`.
> - **Postgres store**: no `unaccent`; `tsv` is built from `retrieval.tokenize`; the
>   embedding column has no fixed width (see PRD §10.3); distances are cosine.
> - **`--store`** is also read from `ZOTERO_RAG_STORE`; only `index` creates tables.

### M0.1 Workspace skeleton

- Root `pyproject.toml` → `[tool.uv.workspace] members = ["packages/*"]`. The CLI stays
  at the root (`src/zotero_rag`) and depends on `zotero-rag-core = { workspace = true }`.
- `packages/zotero-rag-core/pyproject.toml`: the base dependencies are `httpx`,
  `pydantic`, `pyzotero`, `pypdf`, `langchain-core`, `langchain-openai`,
  `langchain-text-splitters`, `tiktoken`, `rank-bm25` and `pyyaml`. The extras are
  `chroma = ["chromadb"]`, `pgvector = ["psycopg[binary,pool]", "pgvector"]` and
  `convert = ["firecrawl-anydoc>=0.2.4,<0.3"]`.
- `ruff`/`mypy`/`pytest` configs run over both packages. CI runs both suites.

### M0.2 Move modules (mechanical, one PR)

- Move the modules listed in PRD §9.2 to `packages/zotero-rag-core/src/zotero_rag_core/`.
- In the CLI, keep **re-export shims** for one release (`zotero_rag/querying.py`:
  `from zotero_rag_core.querying import *`) so imports, tests and notebooks keep
  working. They are removed in M0.7.
- Split `config.py`: models (`GlobalConfig`, `IndexConfig`, `EmbeddingModelSpec`,
  rerankers, …) go to the core, and YAML/`.env` loading stays in the CLI. The packaged
  `data/config.yaml` catalogues go to the core as package data.
- Split `markdown.py`: transcript *reading* (`body`, the provenance parse) goes to the
  core base, and `convert` goes to `zotero_rag_core.convert` (the `[convert]` extra).
- Gate: `uv run pytest -q && uv run ruff check . && uv run mypy` passes, with only
  import changes in `tests/`.

### M0.3 Store protocol

- `zotero_rag_core/store.py`: a `ChunkStore` `Protocol` with exactly the methods the
  indexer, querying, evaluation and generation use today: `add`, `upsert`, `query`,
  `get_by_attachment`, `get_by_ids`, `all_chunks`, `delete_by_attachment`,
  `delete_ids`, `attachment_keys`, `attachment_version`, `has_attachment`,
  `collection_keys_by_attachment`, `set_collection_keys`, `stale_ids`,
  `read_fingerprint`, `write_fingerprint`, `derived_dimension`, `count`, `reset`.
- `vectorstore.VectorStore` → `zotero_rag_core.stores.chroma.ChromaStore`, keeping an
  alias for the old name.
- Type every consumer against `ChunkStore`.

### M0.4 Filters replace Scope

- `Filters(collection_keys: frozenset[str] | None, year_min: int | None, year_max:
  int | None, include_undated: bool = True, tags: frozenset[str] | None)`.
- `ChunkStore.query(embedding, top_k, filters)`. The Chroma implementation translates
  collection keys exactly as `scope_where` does today. It translates year and tags only
  if the chunk metadata carries them (new metadata fields `year_int`, `tags`, written by
  new index runs; a store without them ignores those filters and logs a warning).
- `in_scope` → `Filters.matches(metadata)`.
- The TUI builds `Filters(collection_keys=…)` where it built a `Scope`. Behaviour is
  identical.

### M0.5 Keyword searcher protocol

- `KeywordSearcher` protocol: `search(question, depth, filters) -> list[StoredChunk]`.
- `KeywordIndex` → `BM25Searcher` (unchanged logic).
- `retrieve(store, embedding, question, top_k, mode, searcher, filters)`.

### M0.6 Group libraries and richer item metadata

- `zotero_api.build_client` honours `zotero_library_type: group`. Library-wide
  fetchers: `fetch_all_items(zot, since=None)`, `fetch_deleted(zot, since)` and
  `library_version(zot)`.
- `planning.PlannedAttachment` gains `item_type`, `tags: list[str]`, `year_int: int |
  None` and `web_url` (from `links.alternate.href`, falling back to
  `metadata.zotero_url`).
- `build_plan` accepts a whole library (all items) as well as `(collection, items)`
  pairs, and plans unfiled items.
- The CLI keeps working for user libraries. The README "group libraries are not
  supported" limitation is lifted for `index`/`query`.

### M0.7 History-aware querying

- `querying.Turn(question: str, answer: str)`.
- `condense_question(history, question, chat) -> str`, with the prompt in
  `querying.CONDENSE_PROMPT` (PRD §5.3). It returns `question` unchanged when
  `history` is empty, making no call.
- `stream_answer_question(..., history: list[Turn] = (), retrieval_question: str |
  None = None)`. Retrieval and reranking use `retrieval_question or question`. The
  messages are the system prompt + history (markers stripped by `strip_citations()`) +
  the context + the original question. With an empty history the messages are
  byte-identical to today's (a test asserts it).
- `Usage` gains `condense_cost` and `condense_tokens`.
- Unit tests: marker stripping; condensing is skipped on the first turn; the history
  ordering and truncation to `history_turns`.

### M0.8 Postgres store (`[pgvector]`)

This is built in the core so that the CLI's `eval` can target it (PRD §12). The full
schema is owned by the web app's migrations. The core ships a
`zotero_rag_core.stores.pg` module that assumes the `items`/`attachments`/`chunks`
tables from PRD §10 exist.

- `PgStore(pool)` implements `ChunkStore`. `add` writes the `chunks` rows, upserting
  `attachments`. Item metadata is written by a separate `upsert_items()`, because the
  indexer must write it independently of embeddings.
- `PgFullTextSearcher(pool)` implements `KeywordSearcher` (§M2.3 query).
- `zotero_rag_core.stores.pg.schema.sql`: a reference DDL used by the core's tests and
  by the CLI's `--store postgres://` path. The web app's Alembic migration 0001 is
  generated to match it, and a test compares them.
- Tests use `testcontainers[postgres]` with the `pgvector/pgvector:pg17` image. They
  are marked `@pytest.mark.pg` and skipped when Docker is absent.

### M0.9 CLI `--store`

- `zotero-rag index|query|eval|generate-questions --store postgres://…` uses `PgStore`
  and `PgFullTextSearcher` instead of the folder's Chroma. This is what makes the
  parity eval (M2) and later "eval in production" possible without any eval code in
  the web app.

**M0 done when:** the CLI's suite is green with the shims removed, `pytest -m pg` is
green, the `zotero-rag query` output is unchanged on an existing index folder, and the
tag `core-v0.5.0` is pushed.

---

## M1 — `zotero-rag-web` scaffold

> **Status (2026-11-02): done.** `docker compose up` serves the page shell, `/healthz`
> is green, and `zrw migrate` creates the schema (11 tables). Where it differs from
> the steps below:
>
> - **The model catalogue is not in the core** (it is in the CLI's `config.yaml`), so
>   `settings.py` keeps its own table of known embedding widths, with
>   `EMBEDDING_DIMENSIONS` for any other model.
> - **zotero-rag is now public**, so builds fetch the core's git tag without credentials.
> - **Logging**: the app writes one JSON access line per request, with its request id,
>   and silences uvicorn's own access line.
> - **`zrw`** has `migrate`, `config` and `version`. The rest arrives with the indexer in M2.
> - **Railway files** (`railway.json`, `railway.indexer.json`) move to M5, with the
>   deployment.

### Layout

```
zotero-rag-web/
  pyproject.toml            # uv project; depends on zotero-rag-core[pgvector] @ git tag
  Dockerfile                # multi-stage: node build → python runtime
  railway.json              # web service
  railway.indexer.json      # cron service (same image)
  docker-compose.yml        # local: pgvector/pgvector:pg17 + web
  .env.example
  alembic.ini
  migrations/               # Alembic; 0001_initial matches core schema.sql
  src/zotero_rag_web/
    __init__.py
    settings.py             # pydantic-settings (PRD §7)
    db.py                   # psycopg pool, SQLAlchemy Core tables
    app.py                  # FastAPI factory, static SPA mount, middleware
    identity.py             # signed browser-id cookie → principal (PRD §8.2, §11.2)
    limits.py               # rate limits + spend cap (PRD §8.3)
    api/
      library.py            # /api/library, /api/collections, /api/tags
      conversations.py      # CRUD
      messages.py           # POST … SSE
      errors.py             # error codes (PRD §6.4)
    chat/
      service.py            # QuerySession-like: builds embedder/reranker/chat once
      streaming.py          # worker thread → asyncio.Queue → SSE
      filters.py            # API filters → core Filters (subtree expansion)
    indexing/
      sync.py               # incremental group sync (PRD §6.5)
      lock.py               # pg advisory lock
    cli.py                  # `zrw` (typer): index, reindex, status, migrate
  web/                      # React app (Vite)
    src/{api,components,routes,locales,hooks}/
  tests/
  docs/{runbook.md,deploy-render.md,deploy-do.md,deploy-cloudrun.md,spikes.md}
  PRD.md  PLAN.md  README.md
```

### Tasks

1. `uv init`. Dependencies: `zotero-rag-core[pgvector]` (git tag), `fastapi`,
   `uvicorn[standard]`, `pydantic-settings`, `sqlalchemy>=2`, `alembic`,
   `psycopg[binary,pool]`, `itsdangerous`, `typer`, `structlog`. Dev dependencies:
   `pytest`, `pytest-asyncio`, `httpx`, `testcontainers[postgres]`, `ruff`, `mypy`.
2. `settings.py` with every variable from PRD §7, with validation (for example
   `CHUNK_OVERLAP < CHUNK_SIZE`, and a known embedding model of **at most 2,000
   dimensions**, from the core's catalogue; S4 found wider ones cannot be indexed).
3. Alembic `0001_initial`: the core's `pg_schema.sql` verbatim (via
   `zotero_rag_core.stores.pg.schema_sql()`, core 0.5.1), then the web app's own columns,
   tables and indexes from PRD §10. No vector width is needed at migration time.
4. `app.py`: `/healthz`, a static mount of `web/dist` with an `index.html` fallback,
   JSON logging, request ids, `X-Robots-Tag`, and `robots.txt`.
5. `docker-compose.yml` for local dev. `Dockerfile` (node:22-alpine build → python:3.12-slim
   + uv, non-root user, `PORT` honoured).
6. CI (GitHub Actions): ruff, mypy, pytest (with a pg service container), `npm run
   lint && npm test && npm run build`, and a Docker build.

**Done when:** `docker compose up` serves an empty SPA shell, `/healthz` is green,
and `zrw migrate` creates the schema.

---

## M2 — Indexer and retrieval on Postgres

> **Status (2026-11-02): done.** The live check (`scripts/live_sync_check.py`) passed
> 11/11 against the test group with real embeddings: index, keyword and vector search,
> a move and re-tag with nothing re-embedded, and the removal guard.
> 51 tests run the sync against a fake Zotero, real PDFs and a real database, and
> cover new, moved, re-tagged, replaced and deleted items. Parity with the CLI is
> measured: same retrieval on 36/36 questions, score 4.75 vs 4.78 (`docs/parity.md`).
> Where it differs from the steps below:
>
> - **Whole-library listing, not `since=`.** When the version has changed, the sync lists
>   everything (~30 requests for 1,000 items). Choosing among an item's attachments
>   needs all of them, and Zotero never re-sends an edited item's children (S1).
>   Removals are then "indexed but no longer listed", which covers trashed and
>   deleted items alike; `fetch_removed_item_keys` is not needed.
> - **A probe embedding** checks the key and the model's width before any download,
>   and **the cursor stays while any attachment failed**, so failures are retried.
>   Both came from the first live run (a rejected key passed as an "ok" run).
> - **A different chunking warns, as in the CLI**; only a different embedding model
>   stops the sync.
> - Citations link by group id (S3), set on the plan, so no core change was needed.
> - M2.2/M2.3 are the core's (0.5.1); M2.4's property test is the core's grid test.

### M2.1 `zrw index` (PRD §6.5)

- `indexing/sync.py`:
  1. `with advisory_lock(conn, key=0x5a52)`. If the lock is not acquired, log and
     exit 0.
  2. `v0 = library.last_version`. Fetch the collections (always all of them, since
     they are cheap), the items `since=v0`, and the deletions `since=v0`. Record
     `v1` from the response header.
  3. Upsert `collections`. Upsert `items` from the item payloads (title, authors, year
     int, tags, collection_keys, web_url, item_type, version).
  4. `plan = build_plan(all_changed_items + parents needed, preference, notes)`. The
     planner needs the parent items for attachments and the attachments for parents
     whose metadata changed, so the missing ones are fetched by key (`items(itemKey=…)`,
     50 per call).
  5. `index_plan(plan, PgStore, download=temp_downloader, embed, …, workers=N)`. It
     commits per attachment, so a crash resumes where it stopped.
  6. Deletions: compute the affected items and apply `PRUNE_MAX_FRACTION`. Delete
     the items, which cascades to attachments and chunks.
  7. Update `library.last_version = v1` **only if the run had no fatal error**. Write
     the `index_runs` row.
- `zrw index --full` re-plans everything (`v0 = 0`). Unchanged attachments are skipped
  by version, as in the CLI's `needs_reindex`.
- `zrw reindex --repair` (new embedding model: re-embed from `chunks.text` into a new
  column, then swap it in a transaction) and `--rechunk` (re-download and rebuild).
- `zrw status` prints counts, the last 5 runs and the fingerprint.
- At the end of every sync, call `PgStore.refresh_keyword_stats()` (about 40 s at
  100k chunks). Otherwise the first question after a sync pays for it.

### M2.2 Vector and keyword queries

Already built in the core in M0.8: `PgStore.query` (cosine, HNSW with iterative scan,
filters before the cut) and `PgFullTextSearcher.search` (tokens from
`retrieval.tokenize`, `ts_rank_cd`, the same filters). `retrieve()` combines them
unchanged. What remains for M2 is choosing `ef_search` and `vector` vs `halfvec` on the
real library, during the parity eval (M2.5).

### M2.4 Filters

- `chat/filters.py`: API `{collection, year_min, year_max, include_undated, tags}` →
  core `Filters`. The collection is expanded to its subtree with a recursive CTE.
- The SQL is the core's (`stores.pg.filters_sql`). Undated items have `year_int = 0`,
  so the indexer must write 0, never NULL, for an item without a year.
- The property test is already in the core (`tests/test_pg_store.py` runs every
  combination of conditions against both retrievers). The web app tests only the
  API → `Filters` translation and the subtree expansion.

### M2.5 Parity eval (PRD §12)

- Index the `kg` test library (a group copy, or the user library through the CLI
  `--store`) into pg.
- `zotero-rag eval --store $DATABASE_URL` with the default config. Record the result
  in `docs/parity.md` next to the CLI baseline (4.83, 33/33).
- If the result falls below the thresholds: run `--retrieval vector` to isolate the
  keyword half, then decide between tuning (`ts_rank_cd` normalization, weights) and
  the in-process BM25 fallback.
- Decide `vector` vs `halfvec` on the same run.

**Done when:** a real group is indexed from the root, a re-run with no Zotero changes
does zero embedding calls, PRD §13 item 2 passes against a scratch group, and parity
is recorded.

---

## M3 — Chat API

> **Status (2026-11-02): done.** 69 tests, plus a live check (`scripts/live_chat_check.py`,
> 9/9) with the real models: a cited answer, a follow-up condensed correctly
> ("What problem can that cause…" became a question naming ECMP), a Spanish question
> answered in Spanish, and a collection filter that excluded the answer. Where it
> differs from the steps below:
>
> - **Stop is the client closing the request**, with no `DELETE …/stream`. The request
>   middleware became plain ASGI so the disconnect reaches the stream; a real-server
>   test checks the partial answer is saved as `stopped`.
> - **Streamed answers are priced from their tokens.** Found live: OpenRouter reports a
>   call's cost only when it is not streamed, so every answer counted as free and the
>   spend cap could never trip. The answering model's list price is fetched at startup,
>   with a conservative flat estimate if it cannot be. The CLI's terminal UI streams
>   too, and has the same blind spot in its usage figures (a core fix, later).
> - Refusals before any cost are JSON errors, not SSE events; the stream only carries
>   errors that happen while answering (`llm_failed`, `internal`).

### M3.1 Identity

- Middleware: read and verify `zrw_bid`. If it is missing or invalid, mint a new id,
  upsert `browsers` and set the cookie. `request.state.principal = BrowserPrincipal(id)`.
- Every conversation query is `WHERE browser_id = :principal`. A wrong id gives 404,
  not 403.

### M3.2 Conversations CRUD (PRD §6.3)

These are straightforward. Pydantic schemas are shared via OpenAPI, and the TypeScript
types are generated with `openapi-typescript` in `web/`.

### M3.3 Ask (SSE)

`POST /api/conversations/{id}/messages {question}`:

1. Validate: length, the conversation exists and is owned, and the message count is
   below 200.
2. `limits.check(principal, ip)` → `rate_limited` / `budget_exhausted`.
3. `fingerprint` check → `index_mismatch`. An empty index → `index_empty`.
4. Insert the user message. Insert the assistant message with `status='streaming'`.
5. Start a worker thread:
   `service.ask(question, history, filters, emit)` → `condense` (emits `status`,
   `query`) → `stream_answer_question` with `on_token=emit_token` and stage callbacks.
6. The async generator drains the queue into SSE. A heartbeat comment every 15 s
   keeps proxies from closing the connection.
7. On completion: update the assistant message (the content, the cited sources only,
   usage, `standalone_query`, a settings snapshot, `status='done'`) and emit `done`.
8. If the client disconnects or presses Stop (`DELETE …/messages/{id}/stream`, or an
   abort detected via `request.is_disconnected()`), set a cancel flag. The worker
   stops forwarding tokens after the current chunk, and the message is saved with
   `status='stopped'` and the partial text.
9. On error: `status='error'`, emit `error` with a code, and log the exception with
   the request id.

`service.py` holds the long-lived clients (`build_embeddings`, `build_chat`,
`build_reranker`, `PgStore`, `PgFullTextSearcher`), created at app startup in the
`lifespan` handler.

### M3.4 Limits

- Rate: `SELECT count(*) FROM messages m JOIN conversations c … WHERE c.browser_id =
  :b AND m.role='user' AND m.created_at > now() - interval '1 hour'`. A per-IP limit
  uses an `ip_hash` column on user messages (a salted SHA-256, never the raw IP).
- Spend: `SELECT coalesce(sum((usage->>'total_cost')::numeric),0) FROM messages WHERE
  created_at >= date_trunc('day', now() at time zone 'utc')`, plus
  `in_flight × estimate`.
- Unit tests use frozen time.

### M3.5 Multi-turn check

- `tests/fixtures/dialogues.yaml` holds 10 dialogues. `scripts/run_dialogues.py`
  prints each standalone question, the answer and the sources for manual review. The
  results are recorded in `docs/dialogues.md`. Measure the condense cost and latency
  overhead (PRD §5.3).

**Done when:** the whole flow works with `curl -N`, the API tests are green (with
the LLM, embedder and reranker faked through the core's injected seams, as the CLI's
tests do), and the dialogue review is recorded.

---

## M4 — Web UI

> **Status (2026-11-02): done.** 16 unit tests (Vitest) and 6 browser tests (Playwright
> with axe, desktop and phone) against `tests/e2e/server.py`: the real backend and
> database with fake models. Where it differs from the steps below:
>
> - **No Tailwind; native controls for the filters** (a `<select>` of the collection
>   tree, two year fields, a tag field with server autocomplete). The only component
>   library is Radix Popover, for the citation cards.
> - **Hand-written API types** instead of generated ones; the API is small.
> - **Found by the browser tests, all fixed:** a hover-then-click on a citation chip
>   closed its card (the click handler read a ref inside a deferred React update);
>   the card unmounted when the saved answer replaced the streamed one (now one keyed
>   list, so the component survives the handover); after Stop, a reload could show an
>   empty "streaming" answer (the client now waits until the server has saved it);
>   and the composer floated mid-page when the filter panel was closed (named grid
>   areas).
> - One ~200 kB (gzipped) bundle; splitting would add requests before the first answer.

1. **Shell:** a sidebar (conversation list, new conversation, language toggle) and a
   main pane (filter bar, thread, composer, status footer). The sidebar becomes a
   drawer under 768 px.
2. **API layer:** generated types; TanStack Query hooks for the library,
   conversations and messages; `useAskStream()` wrapping fetch + ReadableStream SSE
   parsing, with an `AbortController` for Stop.
3. **Thread:** user bubble; assistant Markdown (`react-markdown` + `remark-gfm`, with
   raw HTML disabled); `[n]` markers parsed into `<CitationChip>` (a Radix HoverCard /
   Popover on tap); a Sources list; a "Searched for: …" disclosure; Copy (the TUI
   `export.py` Markdown format, ported); Retry; filter-change dividers; stopped/error
   states.
4. **Filters:** a collection tree picker (Radix Popover + tree), a year range slider
   with "include undated", and a tags combobox with server autocomplete. Changes
   PATCH the conversation.
5. **Empty states:** a first-run welcome with 3 example questions in the UI language;
   "library not indexed yet"; "no results with these filters → search whole library".
6. **i18n:** `en.json`, `es.json`, and a CI check that the key sets are identical.
7. **Quality:** Vitest + Testing Library for the SSE parser, citation parsing and the
   filters; Playwright smoke tests (ask, follow-up, filter, stop, language switch)
   against the compose stack with fake model endpoints; axe-core in Playwright.

**Done when:** PRD §13 items 4, 5 and 7 pass in Playwright, and the build output is
served by FastAPI from the Docker image.

---

## M5 — Railway deployment and runbook

1. **Services** (one Railway project):
   - `postgres`: the pgvector template, or a `pgvector/pgvector:pg17` image with a
     volume (per S2). Backups enabled.
   - `web`: Dockerfile build. `railway.json`: `startCommand = "uvicorn
     zotero_rag_web.app:app --host 0.0.0.0 --port $PORT"`, `preDeployCommand = "zrw
     migrate"`, `healthcheckPath = "/healthz"`. `DATABASE_URL` is referenced from the
     postgres service.
   - `indexer`: the same repo and image. `railway.indexer.json`: `startCommand = "zrw
     index"`, `cronSchedule = "0 */6 * * *"` (every 6 h; adjustable), no healthcheck,
     restart policy `NEVER`.
   - Shared variables: Zotero, OpenRouter, models, `SECRET_KEY`.
2. **`docs/runbook.md`:**
   - First deployment: create a read-only Zotero key for the group, and an OpenRouter
     key with a credit limit. Deploy. Run the first index as a one-off (`railway run
     zrw index` or a manual cron trigger), and watch `zrw status`.
   - Changing a model (`EMBEDDING_MODEL` → `zrw reindex --repair`; `LLM_MODEL` → just
     redeploy).
   - Rotating keys, restoring a backup, reading logs, raising the spend cap, and
     what to do when a sync fails.
3. **Portability docs:** `docs/deploy-render.md` (with a `render.yaml`),
   `docs/deploy-do.md` (an app spec) and `docs/deploy-cloudrun.md` (gcloud commands:
   service + job + scheduler + Cloud SQL). These are written and smoke-tested once, on
   at least Render, before v1.0.
4. **Load check:** `scripts/load.py` runs 5 concurrent askers × 10 questions against
   staging and records the p50/p95 for PRD §13 item 3.

**Done when:** PRD §13 item 1 is reproduced by someone following only the runbook.

---

## M6 — Hardening and v1.0

- Structured logs per question: request id, principal hash, stage timings, tokens,
  cost, reranked, sources count. No question text above DEBUG.
- `/api/library` warning states (a stale or failed sync) are wired into the UI badge.
- Security review: cookie signing, SQL parameterization, Markdown XSS (raw HTML off,
  link `rel="noopener noreferrer"`, only http(s) links), and that error payloads never
  include exception text.
- Deleting a conversation removes its messages (cascade). A data-export note goes in
  the README.
- README: what it is, a screenshot, the quick start (compose), deploy (runbook link)
  and configuration (PRD §7 table).
- Tag `v1.0.0`.

---

## Dependency graph and rough effort

```
S1–S5 ─┐
       ├─ M0 (core extraction, 4–6 d) ── M2 (indexer+retrieval, 4–5 d) ── M3 (chat API, 3–4 d) ──┐
M1 (scaffold, 1–2 d) ─────────────────┘                                                         ├─ M5 (deploy, 2 d) ── M6 (1–2 d)
                                            M4 (UI, 5–7 d; can start on mocked API after M3.2) ─┘
```

That is about 4–5 weeks for one developer. M4 can run in parallel with M2 and M3 if a
second person takes the frontend, working against the OpenAPI schema and a mock SSE
server.

## Testing strategy summary

| Layer | Tool | Network |
|---|---|---|
| Core logic (planning, chunking, RRF, condensing, filters) | pytest, as the CLI's suite does today | none (injected seams) |
| Postgres store and FTS | pytest + testcontainers pgvector | local Docker |
| API | pytest + httpx AsyncClient, with fake embed/chat/rerank | none |
| UI units | Vitest + Testing Library | none |
| E2E | Playwright against compose, with a fake OpenRouter (a tiny FastAPI stub) | none |
| Quality | CLI `eval --store postgres://` on the `kg-v3` set; dialogue review | real (manual, costs cents) |
