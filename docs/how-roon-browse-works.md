# How Roon Browse Works

This is a reference document for how Roon's Browse API behaves and how Swarpius builds stable references on top of it. The behaviour described below has been verified empirically against a live Roon Core; the public Roon API docs cover the surface but not all of the navigation semantics that matter at scale.

## 1. The Roon Browse API Model

Roon's browse API is **stateful and session-based**. You navigate a tree hierarchy by issuing browse commands that change the cursor position within a session. Think of it like a filesystem where `cd` changes your working directory.

### Sessions (multi_session_key)

Each browse session is identified by a `multi_session_key` string. Sessions are isolated from each other — navigating in one session does not affect another. This is critical for supporting concurrent searches.

Swarpius creates a new session for each user-initiated search, plus a dedicated `recovery` session used for re-executing searches without disturbing active sessions. See [Stable Reference System](#2-stable-reference-system) below for further details.

### Item Keys (`prefix:position` format)

Every item in browse results has an `item_key` in the format `prefix:position` (e.g. `435:3`).

- **Position (suffix)** is the item's index in the current result list. It is **stable** — the same item always appears at the same position within a given list.
- **Prefix** is a session-specific counter that **changes on every navigation event** (drill-down, pop, new search). It functions like a generation counter.

**Key behaviour:**
- Drilling from level N into item `131:7` produces a new level N+1 with items `132:0`, `132:1`, etc.
- Popping back to level N restores the original items at level N (with their original keys like `131:7`).
- Drilling into the same item `131:7` again produces level N+1 with **new** prefixes: `133:0`, `133:1`, etc.
- **Implication:** prior-level item keys survive a drill-down (they're restored on pop-back), but a pop discards the keys at the level(s) above the new cursor — re-entering produces fresh prefixes. Only position suffixes are stable across navigation.

### Browse Hierarchy

After performing a search with input text, level 0 shows **category groups**: Artists, Albums, Tracks, Composers, Works, Playlists. Each category can be drilled into to see its items.

Example hierarchy for search "Favourites" drilled into Playlists:
```
Level 0 (search root):  [Artists(0), Albums(1), Tracks(2), Playlists(3), ...]
Level 1 (Playlists):    [Favourites(0), Favourites 2(1), ...]
Level 2 (tracks):       [Play Playlist(0), Track A(1), Track B(2), ...]
```

### Browse Hierarchies (The `hierarchy` Parameter)

Roon's `browse_browse` API takes a `hierarchy` parameter that selects which top-level surface to query. See [`node-roon-api-browse/lib.js`](https://github.com/RoonLabs/node-roon-api-browse/blob/master/lib.js) for the full enumeration. Swarpius uses `'search'` exclusively — a unified view across the user's local library and any connected streaming services. The other hierarchies are described below for reference; none are currently used.

| Hierarchy | Accepts `input`? | What it exposes |
|---|---|---|
| `search` | Yes | Unified search across local library + streaming providers. Returns category groups (Artists, Albums, Tracks, Composers, Works, Playlists) where each has results. **Currently used by Swarpius.** |
| `browse` | No (input ignored) | Roon's main desktop-UI surface. Root has Library / Playlists / My Live Radio / Genres / \<Provider\> / Settings. Useful for discovery-style navigation if/when we need provider-specific or local-only paths. |
| `internet_radio` | Yes, but filters favourites only | User's saved radio stations. Returns "No Results" when there are no favourites, even for a search with matching stations available in Roon's catalogue. **Not a discovery surface.** |
| `settings` | N/A | Minimal — Profile selection and Display Settings. Not useful for an agent. |

**Live radio caveat:** The Roon desktop app's search results include a "Live Radio" section alongside Artists / Albums / etc. when a query matches a station name. The public `browse_browse` API does not appear to expose this — none of `search`, `browse`, or `internet_radio` returns matching stations for a search input. Live-radio discovery appears to use an internal path not exposed by the public API. Playing an already-favourited station would work via `internet_radio`, but discovery is a known gap.

### Navigation Operations

| Operation | Effect |
|---|---|
| `browse_browse({pop_all: true, input: "text"})` | Reset session to root, perform search. Destroys all existing state on this session. |
| `browse_browse({item_key: "K"})` | Drill down into item K. Pushes current level onto the stack. |
| `browse_browse({pop_levels: N})` | Pop N levels up. Restores items at each level (with original keys). **Does NOT gracefully clamp large values** — `pop_levels: 100` on a depth-1 session misbehaves (leaves cursor at wrong level). Always pop the exact tracked depth. |
| `browse_load({offset, count})` | Load items at the current level (pagination). Does not change position. |

### Concurrent Browse Dispatch

Roon's session model promises isolation — `multi_session_key` separates the per-session cursors — but the third-party `python-roonapi` library has a wire-layer flaw that breaks that promise under concurrent calls.

The library's stock `_request()` method polls a shared `_results` dict every 50ms with no per-request locking. When two browse calls are in flight concurrently, the second response can arrive before the first thread has read its own response, and the threads race for `_results.popitem()`. The losing call gets `None` back; we've seen this manifest as `browse_load` intermittently returning empty.

`agent/roon_core/parallel_browse.py` patches the `RoonApi` instance to replace this with `Future`-based request-response correlation:

- Each browse request registers a `Future` keyed by its `request_id` before sending
- A patched `on_message` handler resolves the matching `Future` when the response arrives on the websocket thread
- Callers block on their own `Future` — no polling, no cross-thread interference
- The socket is read dynamically (`api._roonsocket`) on every call, so the patch survives library-level reconnections

The patch is installed once per `RoonApi` instance (idempotent) and is what makes the per-search session pool described below safe under genuine concurrency.

### Action Execution

When you drill into an item (e.g. a track), you eventually reach an `action_list` level containing items like "Play Now", "Queue", "Add Next". Drilling into one of these action items **executes the action** in Roon.

**Critical: action auto-pop behaviour**

After executing an action via `browse_browse({item_key: action_key})`, Roon automatically pops the browse session:

- The action drill goes **one level deeper** (into the action item)
- Roon then **auto-pops back two levels** — past the action menu, back to the item list that contained the track

Example: if you're at the action menu (depth 3) and execute "Play Now":
1. `browse_browse({item_key: play_now_key})` — drills to depth 4
2. Roon auto-pops to depth 2 (the track list), not depth 3 (the action menu)
3. Net effect from action menu: **-1 level**

This is consistent across Play Now and Queue actions on tracks. The net -1 must be accounted for in depth tracking — the tracked depth must be decremented by 1 after each action execution, before any subsequent pop-to-root.

### Sources / Versions Level

When drilling into a track or album, Roon often inserts an intermediate level that lists the available sources or versions of that item before exposing the gateway / action_list. This level has a distinctive shape:

- `list_hint = None` (not an `action_list`, not a category list)
- `list_title` equals the item's own title
- Children all share the same title (the item's title), differing only in source / version metadata

The number of children depends on how many sources or versions exist:

```
N=1 (single source — typical for tracks):

Level N:    [..., 'Track X' (5), ...]
Level N+1:  ['Track X' (0)]                   ← sources level, 1 child (hint=action_list)
Level N+2:  ['Play Now', 'Add Next', ...]     ← terminal action_list
```

```
N≥2 (multi-version album — Thriller in local + streaming):

Level N:    [..., 'Thriller' (5), ...]
Level N+1:  ['Thriller', 'Thriller', 'Thriller']   ← versions level, 3 children (hint=list)
Level N+2:  ['Play Album', track1, track2, ...]    ← gateway + tracks for the chosen version
Level N+3:  ['Play Now', 'Add Next', ...]          ← terminal action_list
```

Same structural level in both cases — only the count and the children's `hint` differ. Children have `hint='action_list'` when each leads directly to an action menu (track sources) and `hint='list'` when each leads to a deeper sub-tree (album versions).

The level may not appear at all for single-version albums; Roon collapses it in some cases. Code that walks the browse tree must handle both: drill past N=1 sources transparently, and pick a version when N≥2 (`drill_down()` handles the N=1 case via `_duplicate_found`; `_pick_drill_target`'s uniform-hint group handles the N≥2 case).

## 2. Stable Reference System

### Problem

The LLM needs to reference items across multiple searches and tool calls. But Roon's item keys are ephemeral — they change whenever you navigate. We need a way to persistently identify items and find them again.

### Solution: `StableReference` + Position Paths

Each item discovered through browsing gets a `StableReference` containing:

| Field | Purpose |
|---|---|
| `ref_id` | 5-character hex string (e.g. `0eb70`). Shown to the LLM. |
| `identity` | Semantic fingerprint: title, subtitle, hint, image_key |
| `recipe` | How to re-find this item: search_string, category, parent_chain |
| `cached_item_key` | Last known Roon item_key (may be stale) |
| `roon_session_key` | Which session this item was found on |
| `item_key_path` | List of position suffixes from root to this item |

The `item_key_path` is the key idea. It stores only position suffixes (the stable part of keys), forming a path from the search root to the item:

```
Search "Favourites" → Playlists(position 3) → Favourites(position 0) → Track A(position 5)
item_key_path = ["3", "0", "5"]
```

### How Paths Are Built

When `drill_down()` is called, each result item gets an `item_key_path` that records the position path from root:

1. Start with the parent item's path
2. Append the parent item's position suffix
3. Each child item gets `parent_path + [child_position]`

Example: drilling into Playlists (position 3) which contains Favourites (position 0):
- Playlists item has path `["3"]` (set when it was at root level)
- After drilling, Favourites gets path `["3", "0"]`

### Reference Resolution (finding an item again)

`resolve_reference(ref_id)` uses a two-tier strategy:

**Tier 1: Position Walk (fast path)**

1. Look up the reference's session key
2. Check if the session still exists (`is_key_live` — the session key is in `_session_depth` and the ref still has a `cached_item_key`)
3. Pop the session to root using `pop_levels: current_depth`
4. Walk the `item_key_path`: at each level, load items, find the key matching the stored position suffix, drill into it
5. At the final level, refresh `cached_item_key` with the fresh key at the target position

This is fast (~50-100ms) because it reuses the existing session and only does position lookups.

**Tier 2: Semantic Recovery (fallback)**

When the position walk fails (session destroyed, browse state corrupted):

1. Use the dedicated `recovery` session
2. Re-execute the original search (`recipe.search_string`)
3. If `recipe.category` exists, drill into that category
4. Walk `recipe.parent_chain` by fuzzy-matching item titles at each level
5. Fuzzy-find the target item itself
6. Update the reference with fresh keys and the recovery session

This is slow (~1-1.5s per item, 3+ Roon API calls) but resilient. It should be a genuine fallback, not a regular code path. **If semantic recovery fires during normal operation, it indicates a problem with the fast path.**

### Session Pool

Search-session keys aren't minted without bound. `BrowseSessionManager` maintains a fixed pool of `max_sessions` slots (default 16) and assigns each new search to a slot via `counter % max_sessions`. The session key is `s-<random_prefix>-<slot_hex>` — the random prefix is unique to the manager instance, so we don't collide with cached state from a previous Swarpius process.

When a slot is reused, all `StableReference`s pointing to that slot are purged from the in-memory ref store: their `cached_item_key`s would be stale against the recycled session. Affected refs fail their Tier 1 resolve and either fall through to Tier 2 (semantic recovery) or return a "reference expired" error.

The 16-slot ceiling means up to 16 concurrent search histories are addressable — practically ample for the per-conversation context the LLM operates with, and the Core never accumulates unbounded session state.

## 3. Open limitations

### No TTL on session pool

The 16-slot round-robin pool bounds session count, but slot reuse is purely counter-driven — there's no idle-time eviction. A session that hasn't been touched for hours stays live until its slot comes round again. Long-lived idle sessions don't cost memory locally (the per-session footprint is just `_session_depth` + cached current-list), but they keep references to stale Roon Core state. Idle TTL eviction would be a small refinement; not currently a problem in practice.
