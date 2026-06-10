# Category reconciliation

Here we describe how `roon_action` reconciles a caller's `intended_item_category` against the shape of the resolved Roon item — what happens for each (intent × ref-category) pair, and where the validation / correction logic lives.

## Why it exists

Swarpius submits a `roon_action` call with two related signals:

1. The **action verb** — one of `Play Now`, `Add Next`, `Queue`, `Shuffle`, `Start Radio` (the verb-only `LIBRARY_ACTIONS` set; the coordinator picks a verb regardless of category).
2. The **intended category** of the target item, carried by the request-level `intended_item_category` field.

Both are LLM choices and both can be wrong. The reconciler runs inside `roon_connection.get_media_actions` (via the `reconcile_intended_category` delegate) and decides whether to:

- **Auto-correct** the navigation (rare — only the album/playlist track-↔-container directions today)
- **Fail loud** with a `CategoryCorrectionFailed` exception that the action tool formats into a directive error for the LLM
- **Pass** — no mismatch, walk continues normally

The reconciler is the safety net that prevents an `artist`-intended request from silently shuffling an album, or `intent="work"` from quietly resolving an album-shaped result.

> **Note:** `Play Artist`, `Play Composer`, `Play Album`, etc. are **Roon browse-tree gateway item titles** (the first item Roon shows when you drill into a result), *not* action verbs. The reconciler reads them to detect the resolved item's category; the agent never issues a "Play Artist" action.

## The six categories Roon search exposes

| Category | What it represents | Typical drill page (L1) | Terminal action_list signature |
|---|---|---|---|
| **Track** | Single recording (leaf) | direct `action_list` | `{Play Now, Add Next, Queue, Start Radio}` |
| **Album** | Recording as a tracklist | `Play Album` gateway + tracks | `{Play Now, Add Next, Queue, Start Radio}` |
| **Playlist** | User-curated tracklist | `Play Playlist` gateway + tracks | `{Play Now, Shuffle, Add Next, Queue, Start Radio}` |
| **Artist** | A performer | `Play Artist` gateway + albums | `{Shuffle, Start Radio}` |
| **Composer** | A composer of works (classical) | `Play Composer` gateway + compositions | `{Shuffle, Start Radio}` |
| **Work** | A composition (classical) | `Play Work` gateway + recordings | `{Play Now, Add Next, Queue, Start Radio}` |

(The "gateway" column lists the title of the first item on the drill page — a Roon navigation item, not an agent action.)

Two structural families:

- **Container family** (Album, Playlist, Work): drill yields a `Play X` gateway plus content; drilling the gateway reaches a terminal `action_list` with playable per-item actions.
- **Persona family** (Artist, Composer): drill yields a `Play X` gateway plus a child listing (albums for artists, compositions for composers); drilling the gateway reaches an `action_list` with **only** `{Shuffle, Start Radio}` — no Play Now / Queue, because there's no single thing to play.

Track is the leaf (no gateway). Roon also surfaces top-level "category-group" handles like `Albums | 8 Results` for drill-down navigation; these are not media items and shouldn't be passed to `roon_action`.

## The reconciliation maps

In `agent/roon_core/category_reconciler.py`:

```python
GATEWAY_CATEGORY_MAP = {
    "Play Album": "album",
    "Play Playlist": "playlist",
    "Play Artist": "artist",
    "Play Composer": "composer",
    "Play Work": "work",
}
_CATEGORY_TO_GATEWAY = {
    "album": "Play Album",
    "playlist": "Play Playlist",
    "composer": "Play Composer",
    "work": "Play Work",
}
_CATEGORY_NAMES = {
    "album": "Albums", "playlist": "Playlists", "artist": "Artists",
    "composer": "Composers", "work": "Works",
}
_PERSONA_ACTION_SIGNATURE = {"Shuffle", "Start Radio"}
_PERSONA_INTENTS = frozenset({"artist", "composer"})
```

`GATEWAY_CATEGORY_MAP` is public because `browse.py`'s `_pick_drill_target` imports it to decide whether `items[0]` is a gateway before drilling. The maps are the only place that needs editing to add a new category — the dispatch logic reads them dynamically.

## Reconciliation matrix

Each cell is what happens when the LLM's `intended_item_category` (column) is paired with a ref whose actual category (row) is the column's mismatch. `auto` is omitted because it makes no change.

| Resolved → / Intent ↓ | Track ref | Album ref | Playlist ref | Artist ref | Composer ref | Work ref |
|---|---|---|---|---|---|---|
| **`auto`** | pass | pass | pass | pass | pass | pass |
| **`track`** | pass | sibling-track search at gateway → corrects if title matches a track | sibling-track search at gateway → corrects if title matches | sibling search at the `Play Artist` gateway → no track match → pass | sibling search → no match → pass | sibling-track search at gateway → corrects if title matches |
| **`album`** | category re-search → drill Albums → strict-titled match | pass | sibling search may try | gateway-mismatch fall-through (no auto-correction; subsequent `_pick_drill_target` halts → "actions not found" generic error) | gateway-mismatch fall-through | gateway-mismatch fall-through |
| **`playlist`** | category re-search → drill Playlists → strict-titled match | sibling search | pass | fall-through | fall-through | fall-through |
| **`artist`** *(validate-only)* | **fail loud** at action_list (signature ≠ persona) | **fail loud** at gateway | **fail loud** at gateway | pass | **fail loud** at gateway | **fail loud** at gateway |
| **`composer`** *(validate-only)* | **fail loud** at action_list | **fail loud** at gateway | **fail loud** at gateway | **fail loud** at gateway | pass | **fail loud** at gateway |
| **`work`** *(validate-only)* | category re-search → drill Works → strict-titled match | **fail loud** at gateway | **fail loud** at gateway | **fail loud** at gateway | **fail loud** at gateway | pass |

Validate-only intents (artist / composer / work) never auto-correct because the LLM's title is typically lifted from a different-category item (e.g. an artist credit string scraped from an album's `extra_info`), so re-searching by it wouldn't surface the right item anyway. (The `work` row's Track cell is the one exception — work intent does run the track-→-container re-search before the gateway validation applies.)

## Two reconciliation directions

### Direction A — permissive correction (`_correct_via_*`)

For container intents (album / playlist) and the limited Work track-→-container path, the reconciler tries to repair a mismatch:

- **`_correct_via_gateway_siblings`** — at a `Play X` gateway level whose category doesn't match the intent, look for a sibling whose normalised title equals the ref's identity title. If found, drill into it. Useful when the LLM grabbed an album ref but said `intent="track"`; the album drill also exposes its tracks as siblings.
- **`_correct_via_category_search`** — at a track action_list (or a single-child wrapper around one) when a container was intended, re-search the original recipe's `search_string` on the recovery session, drill into the matching category (Albums / Playlists / Composers / Works), find the strict-titled match, and navigate to its container level. Useful for "play the album X" when the LLM grabbed a track titled X.

### Direction B — validate-only (`_validate_*_intent`)

For persona intents (artist / composer) and work intent, the reconciler does **not** correct — it raises `CategoryCorrectionFailed` immediately when the shape doesn't match.

- `_validate_persona_intent(ref, items, list_hint, intended_category)` — fails when `items[0]` is a non-persona gateway (`Play Album` / `Play Playlist` / `Play Work`) or when the terminal `action_list` titles aren't a subset of `{Shuffle, Start Radio}`. Passes (returns `None`) at the persona's own gateway and intermediate disambiguation levels so the walk progresses.
- `_validate_work_intent(ref, items)` — fails when `items[0]` is any non-Work gateway. Work shares its terminal signature with album, so there's no action_list-level discriminator — only the gateway title can tell them apart.

The error is then formatted by `_format_category_correction_error` in `tools/roon_action.py` into an actionable directive for Swarpius ("Item X is not an artist. Drill into the 'Artists' category from the same search…").

## What sets each intent

`intended_item_category` is a loose `str` (`IntendedItemCategory = str`), narrowed for the LLM through its schema **description** rather than a `Literal`, so internal code can carry values the LLM isn't told to pick.

| Intent | Set by |
|---|---|
| `auto` | Default value of the `intended_item_category` field |
| `track` / `album` / `playlist` | LLM via `intended_item_category` — the three values advertised in the schema description |
| `artist` / `composer` / `work` | Accepted by the same loose `str` field but **not** advertised in the description; treated as validate-only intents (`_VALIDATE_ONLY_INTENTS`) when present |
| `track` (internal stamp) | When `roon_action` expands a container into its child tracks (or a probe correction lands on a track action_list), each child is stamped `intended_category="track"` via `model_copy` so a track pulled out of an album isn't reconciled back up to `album`. This is the only place the code itself sets the category. |

Keeping the field a loose `str` (advertising only `track`/`album`/`playlist`) keeps the LLM-facing surface tight while leaving room for the validate-only categories and the internal `track` stamp.

## See also

- `agent/roon_core/category_reconciler.py` — `CategoryReconciler.reconcile` and the `_validate_persona_intent` / `_validate_work_intent` / `_correct_via_gateway_siblings` / `_correct_via_category_search` methods, plus the maps
- `agent/roon_core/browse.py` — `reconcile_intended_category` (the thin delegate that constructs `CategoryReconciler` and calls `reconcile`) and `_pick_drill_target` (which imports `GATEWAY_CATEGORY_MAP`)
- `agent/tools/roon_action.py` — `_format_category_correction_error`, `_VALIDATE_ONLY_INTENTS`, and the runtime persona-shape handling (`_classify_result_shape`)
- `agent/app/exceptions.py` — `CategoryCorrectionFailed`
- `docs/how-roon-browse-works.md` — Roon's stateful browse model that this layer sits on
