---
name: roon-status
description: >
  Read-only Roon state: zone status with seek position, queue track listing with references.
requires_tool: roon_status
---

## When to use

Zone status (what's playing, volume, shuffle/repeat, available zones) is already available in your system context under **Zone Status** — you do not need to call this tool for basic status queries. Treat the Zone Status block as the live source of truth on every turn — including which zone is marked `[DEFAULT ZONE]`. State can change between turns from the UI or another Roon client; don't rely on what was true earlier in the conversation. Use this tool when you need:

- **Seek position** — `get_zones_status` includes current playback position (not in context)
- **Queue track listing** — `get_queue_status` returns the full numbered track list with `Q:`-prefixed references
- **Fresh status after performing an action** — to confirm the result of a playback control or config change

## Operations

- `get_zones_status`: compact status including seek position, now playing, shuffle/repeat, volume per output. Omit `zone` for all zones; specify zone name(s) for specific zone(s).
- `get_queue_status`: queue track listing. Returns zone name(s) and numbered track lists with `Q:`-prefixed references. Omit `zone` to fetch all zones' queues at once; specify zone name(s) for specific queue(s).

Volume is always reported for every output — either a numeric value (with muted state) or `"not controllable (fixed)"` for fixed-volume devices. There is never an absent volume field.

## Queue data

When the user asks to see queues, always fetch fresh data — do not reuse queue data from earlier requests. When acting on queue contents (play from here, etc.), use the track references from the most recent queue fetch in the conversation trace.

Queue items carry `Q:`-prefixed references (e.g. `Q:a3f7c`). Queue references can only be used with `play_from_here`.

## Presenting queue results

Always use `<queue zone="zone name"/>` to present queue results. *Never* list queue items manually — the system handles formatting, numbering, and collapsible display. Use the zone name from the tool output: `<queue zone="Headphones"/>`.
