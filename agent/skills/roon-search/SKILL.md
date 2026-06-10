---
name: roon-search
description: >
  Browse and search the Roon music library one level at a time. Roon text
  search fuzzy-matches specific titles and names in the following categories:
  Tracks, Albums, Artists, Playlists, Composers, Works. For vague or
  genre-based requests, either use your own knowledge or use web_search
  to retrieve needed information.
requires_tool: roon_search
---

## Output notes

Each result item is shown as `(list_index)[S:reference] title | group | extra_info`. Group may or may not be included, and corresponds to parent album/playlist. Extra info is generally artist/metadata. The `S:`-prefixed reference (e.g. `S:10da3`) is a **search reference** — use it with `roon_search drill_down_reference` to navigate deeper, or pass it to `roon_action` to play/queue.

Category groups like `(3)[10da3] Tracks | 30 Results` mean there are 30 track items one level deeper. Drill down into the category to see them.

## Presenting results

Always use `<list ref="res_NNNNN"/>` to present Roon search results. *Never* list Roon items manually — the system handles formatting, numbering, and collapsible display. You can add a title: `<list ref="res_NNNNN" title="Tracks on Abbey Road"/>`.

Result handles come from two sources:
- **Current request**: each search result includes a `[Result handle: res_NNNNN]` line in the tool output.
- **Previous requests**: the search history section in your context lists handles from earlier searches.

When drill-down updates an existing search, the original handle is updated to point to the new results.

## Execution guidance

- **Search strings must be concrete** — use actual titles and names in the search string, rather than vague descriptions.
- *Never* include category names (e.g., "album", "playlist") in the search string unless it's explicitly obvious it should be included (for example, if the user included it in quotes).
- One level at a time: call repeatedly to navigate depth.
- References remain valid across searches. Perform all searches first, then act on references from any of them.
- A new search returns Roon's best-match specific item at the top, followed by category entries (`Tracks | N Results`, `Albums | N Results`, etc.), each containing matching items of that category. The top item can be of any category (track, album, artist, playlist, composer, or work). Drilling into category entries retrieves the contained items.
- An artist appearing as a top result looks like `<name> | N albums` (e.g. `Pat Benatar | 9 Albums`); this is a curated subset. The full album list for the artist is contained within the Albums category of the search results.
- If the top result isn't what you're looking for, this does not necessarily mean the item isn't somewhere in the library. Drill into relevant categories to try and find the item, or retry the search with a more specific search string. If you cannot surface the item given reasonable effort, report it as not found rather than continuing to cycle.
- When multiple versions or variations of a requested item appear, choose the **closest match** to what the user specified.
