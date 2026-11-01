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
- **S2, S4 and S5** are still open.
