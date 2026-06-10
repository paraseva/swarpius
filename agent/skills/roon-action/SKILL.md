---
name: roon-action
description: >
  Control Roon playback: play/queue/shuffle/start radio for media, transport (pause, stop,
  next, previous), playback settings (shuffle, repeat, seek, auto-radio), volume, mute,
  mute all, standby, and play from a specific queue position.
  Library actions require a `reference` from a prior roon_search result.
  For zone grouping, use the roon_config tool instead.
requires_tool: roon_action
---

## Execution guidance

- Library actions require the `reference` field from `roon_search` results. Without a valid reference, the action fails.
- References remain valid across searches. Perform all searches first, then act on references from any of them.
- Batch references from the same search into one `roon_action` call.
- `intended_item_category` is a hint that helps disambiguate when an item could be more than one thing (e.g. if the *track* "Thriller" is required vs the *album*, set to `track`). The hint is optional — use `auto` (the default) when the intended category is unambiguous, or if you are unsure of intent. For mixed scenarios (e.g. a combination of albums and tracks), set per-item via `intended_category` on the item itself; per-item values override the request-level `intended_item_category`.
- When multiple versions or variations of a requested item appear, choose the **closest match** to what the user specified.
- Only specify the `zone` argument if the user request explicitly names one. Without `zone`, actions operate on the user's default zone (or group).

## Library actions

`Play Now`, `Add Next`, `Queue`, `Shuffle`, `Start Radio` — all require media item reference(s) from roon_search.

### Action × category — dispatch summary

The following table summarises how each action operates on different Roon item categories.

| Action        | # items accepted  | track / album / playlist / work     | artist / composer                                            |
|---------------|-------------------|-------------------------------------|--------------------------------------------------------------|
| `Play Now`    | Multiple          | plays first item, queues remaining  | not supported — use `Shuffle` instead                        |
| `Add Next`    | 1                 | inserts next in queue               | not supported                                                |
| `Queue`       | Multiple          | appends to queue                    | not supported                                                |
| `Shuffle`     | Multiple          | randomises across its tracks        | shuffles tracks by this artist/composer only                 |
| `Start Radio` | 1                 | seeds a radio from the item         | seeds a radio from the artist — can drift to similar artists |

### Shuffle details

Shuffle first expands specified albums, playlists and works into their tracks, combines with any individual tracks supplied, then randomises the resulting pool. The `count` argument (if provided) controls how many tracks play:

- **No `count`** — all expanded tracks play in random order.
- **`count=N`** — plays exactly N randomly selected tracks from the expanded pool. Use this for any "give me N random tracks from X, Y and Z" request.

**Mixing artists/composers is not supported.** A `Shuffle` call containing more than one artist/composer reference — or an artist/composer mixed with tracks, albums, playlists, or works — is rejected. To shuffle across multiple artists/composers, drill into Albums for each one and pass the album references to a single `Shuffle` call instead.

## Error / notice handling from the dispatcher

When a request can't proceed, the dispatcher returns structured entries in `output.errors[*]` that indicate the nature of the problem(s). There are two kinds:
- Actionable errors detailing suggested corrective action — retry by following the guidance;
- Informational notices (e.g. if some item(s) were unavailable) — the other items in the same call still dispatch normally; report which specific items were unavailable to the user, but no retry is needed.

## Queue actions

- **play_from_here**: jump to a specific track in the queue. Pass the `Q:`-prefixed reference from the queue listing as `queue_ref` (e.g. `queue_ref="Q:a3f7c"`). The reference is resolved to the Roon queue item ID automatically. If there is already a queue listing in your execution trace, use the references from there without re-fetching the queue, as it may have changed since the user saw it.

Queue manipulation is limited by the Roon API. The only queue actions available are: `play_from_here` (jump to a position), `Queue` (append from library), and `Add Next` (insert next from library). Removing individual items, reordering, inserting at specific positions, and clearing the queue are not possible due to Roon API limitations. If a user requests an unsupported queue operation, explain that Roon's API doesn't currently support it.

## Playback settings

- **set_shuffle**, **set_repeat**, **seek**: self-explanatory.
- **set_auto_radio**: toggle whether Roon plays radio after the queue ends. Requires `auto_radio` (true/false) and `zone`.

## Advanced controls

- **mute_all**: mute every output across all zones at once.
- **unmute_all**: unmute every output across all zones at once.
- **standby**, **convenience_switch**: put an output into standby or switch its source input.
