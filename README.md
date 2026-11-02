# zotero-rag-web

A web chat for a team's Zotero **group library**. Ask a question in English or
Spanish, and get an answer cited back to the item and page it came from, with a link
to the item on zotero.org.

It is built on [`zotero-rag-core`](https://github.com/jmlon/zotero-rag/tree/main/packages/zotero-rag-core),
the library behind the `zotero-rag` CLI: the same retrieval, reranking and cited
answering, measured with the same evaluation. The chunks live in PostgreSQL with
pgvector, and the app is one Docker image plus that database.

**Status: M1, the scaffold.** The server, database schema and page shell run; the
indexer (M2), the chat API (M3) and the chat UI (M4) are next. See [PLAN.md](PLAN.md)
for the milestones and [PRD.md](PRD.md) for the design.

## Run it locally

With Docker:

```bash
cp .env.example .env          # credentials are not needed yet for M1
docker compose up --build     # database, migration, then the app
open http://localhost:8000
```

`http://localhost:8000/healthz` reports whether the server and the database are up.

Without Docker, for development, with a PostgreSQL + pgvector of your own in
`DATABASE_URL`:

```bash
uv sync --all-groups
uv run zrw migrate
uv run uvicorn zotero_rag_web.app:create_app --factory --reload --port 8000
cd web && npm install && npm run dev      # http://localhost:5173, API proxied to :8000
```

## Configuration

Environment variables only; every one is in [PRD.md §7](PRD.md) and
[.env.example](.env.example). `uv run zrw config` prints the effective settings with
credentials hidden. The embedding model must return at most 2,000 dimensions
(pgvector's index limit); a wider one is refused at startup.

## The `zrw` command

| Command | What it does |
|---|---|
| `zrw migrate` | Create or upgrade the database schema. Safe on every deploy. |
| `zrw config` | Show the effective settings, credentials hidden. |
| `zrw version` | Show the version. |

## Development

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest -q                 # starts a throwaway pgvector container through Docker
cd web && npm run lint && npm run typecheck && npm test
```

Database tests use `ZRW_TEST_PG_URL` if it is set (a server they create and drop
databases on), and otherwise start a `pgvector/pgvector:pg17` container. Without
either they are skipped; CI sets `ZRW_REQUIRE_PG=1` to make that a failure.

`spikes/` holds the experiments behind the design decisions, run in the CLI's
environment; their results are in [docs/spikes.md](docs/spikes.md).

## License

MIT
