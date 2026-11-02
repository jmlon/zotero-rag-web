# Running zotero-rag-web locally with Docker (Linux/macOS)

## 1. Prerequisites
- git
- Docker with Compose v2.24 or later: `docker compose version`
  (on Linux, your user must be in the `docker` group, or prefix commands with `sudo`)
- Ports 8000 (the app) and 5433 (its database) free

## 2. Get the code
```sh
git clone https://github.com/jmlon/zotero-rag-web.git
cd zotero-rag-web
```

## 3. Configure
```sh
cp .env.example .env
```
Edit `.env` and fill in:
- `ZOTERO_GROUP_ID`: the number in the group's address, e.g. `.../groups/6743389/...`
- `ZOTERO_API_KEY`: a read-only key with access to that group (zotero.org/settings/keys)
- `OPENROUTER_API_KEY`: give the key a credit limit
- `SECRET_KEY`: any long random string: `openssl rand -hex 32`

Leave `DATABASE_URL` as it is; Docker Compose sets its own.

## 4. Start
```sh
docker compose up --build -d     # database, schema migration, then the app
docker compose logs -f web       # optional: follow the app's log (Ctrl-C to stop following)
```

## 5. Index the library
The first run downloads and embeds every PDF, which costs a few cents through OpenRouter.
```sh
docker compose exec web zrw index    # prints a summary: look for "Sync ok"
docker compose exec web zrw status   # what is indexed
```
Re-run `zrw index` after the library changes; it only processes what changed.
Add `-v` to see each file and request as it goes, or `--json` for the full report.

## 6. Use it
Open http://localhost:8000

## 7. Stop
```sh
docker compose down       # keeps the index and conversations
docker compose down -v    # deletes them too
```
