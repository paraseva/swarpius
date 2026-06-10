"""Compact formatting utilities for Roon result items.

Converts cached result items (dicts with title/reference/extra_info) into
compact one-line strings for LLM context.  Handles action-item filtering,
track-number stripping, and multi-disc grouping.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, List, Optional

# Pattern: "N. Title" — Roon's single-disc track-number form. The dot is
# required: it's what distinguishes a track-number prefix from a title
# that happens to start with a digit (e.g. "2 Minutes to Midnight").
_SINGLE_DISC_PREFIX_RE = re.compile(r"^\d+\.\s")

# Same shape as _SINGLE_DISC_PREFIX_RE but captures the leading number,
# used by _detect_single_disc_numbered and as the source for the (idx)
# value rendered alongside each track.
_SINGLE_DISC_NUMBER_RE = re.compile(r"^(\d+)\.\s")

# Pattern: "d-t Title" where d=disc, t=track (multi-disc format from Roon).
_MULTI_DISC_PREFIX_RE = re.compile(r"^(\d+)-(\d+)\s")


def _strip_track_prefix(title: str) -> str:
    """Strip leading 'N. ' or 'N ' track number prefix from a title."""
    return _SINGLE_DISC_PREFIX_RE.sub("", title)


def _compact_item(item: Any, index: Optional[int] = None, strip_title_prefix: bool = False) -> str:
    """Format a single item dict as a compact one-line string.

    Handles RoonCoreItemSummarySchema-like dicts with title/reference/extra_info.
    *index* is an optional position prefix.
    """
    if not isinstance(item, dict):
        return str(item)
    ref = item.get("reference", "")
    title = item.get("title", "")
    group = item.get("group", "")
    extra = item.get("extra_info", "")
    if ref and title:
        if strip_title_prefix:
            title = _strip_track_prefix(title)
        prefix = f"({index}) " if index is not None else ""
        parts = [f"{prefix}[{ref}] {title}"]
        if group and group != "-":
            parts.append(group)
        if extra:
            parts.append(extra)
        return " | ".join(parts)
    return str(item)


_ACTION_ITEMS = {
    "Play Now", "Add Next", "Queue", "Start Radio", "Shuffle",
    "Play Album", "Play Artist", "Play Playlist",
    "Play Composer", "Play Work",
}


def _is_action_item(item: Any) -> bool:
    """Return True if the item is a Roon browse action item."""
    if isinstance(item, dict):
        return item.get("title", "") in _ACTION_ITEMS
    return False


def _flatten_and_filter(payload: List[Any]) -> List[Any]:
    """Flatten grouped entries and strip action items."""
    items: List[Any] = []
    for entry in payload:
        if isinstance(entry, dict) and "items" in entry:
            sub_items = entry.get("items", [])
            if isinstance(sub_items, list):
                for sub in sub_items:
                    if not _is_action_item(sub):
                        items.append(sub)
            else:
                if not _is_action_item(entry):
                    items.append(entry)
        else:
            if not _is_action_item(entry):
                items.append(entry)
    return items


def _detect_multi_disc(items: List[Any]) -> bool:
    """Return True if ALL items have the d-t multi-disc title pattern."""
    if not items:
        return False
    return all(
        isinstance(item, dict)
        and _MULTI_DISC_PREFIX_RE.match(item.get("title", ""))
        for item in items
    )


def _detect_single_disc_numbered(items: List[Any]) -> bool:
    """Return True when *every* item's title carries a leading ``N. ``
    track-number prefix AND the numbers form a strictly increasing
    sequence.

    The dot in the regex is what guards "2 Minutes to Midnight" from
    being misread as track 2 — real titles starting with a digit don't
    use ``"N. "`` form. Strictly-increasing handles partial drill-ins
    (e.g. tracks 3, 5, 6, 14, 15) without requiring the sequence to
    start at 1.
    """
    if not items:
        return False
    numbers: List[int] = []
    for item in items:
        if not isinstance(item, dict):
            return False
        m = _SINGLE_DISC_NUMBER_RE.match(item.get("title", ""))
        if not m:
            return False
        numbers.append(int(m.group(1)))
    return all(b > a for a, b in zip(numbers, numbers[1:]))


def _compact_multi_disc(items: List[Any]) -> List[str]:
    """Group items by disc, add headers, strip d-t prefix from each
    title, and render each track with its *extracted* track number as
    the (idx) value — so gaps in Roon's response stay visible."""
    discs: defaultdict[int, List[tuple[int, Any]]] = defaultdict(list)
    for item in items:
        m = _MULTI_DISC_PREFIX_RE.match(item.get("title", ""))
        if m:
            disc_num = int(m.group(1))
            track_num = int(m.group(2))
            stripped = item.copy()
            stripped["title"] = item["title"][m.end():]
            discs[disc_num].append((track_num, stripped))

    result: List[str] = []
    for disc_num in sorted(discs):
        tracks = discs[disc_num]
        count = len(tracks)
        result.append(f"[Disc {disc_num}] ({count} track{'s' if count != 1 else ''})")
        for track_num, track in tracks:
            result.append(_compact_item(track, track_num))
    return result


def _compact_items(payload: List[Any]) -> List[Any]:
    """Convert cached result items to compact one-line format.

    If items are RoonCoreResultsGroupSchema dicts (with group/items keys),
    flatten all sub-items. Otherwise compact each item directly.
    Action items (Play Now, Add Next, Queue, Start Radio, Play Album, etc.)
    are stripped — the coordinator uses roon_action for playback controls,
    not browse drill-down.

    Multi-disc albums (titles matching 'd-t Title') are grouped by disc with
    [Disc N] headers and per-disc numbering. Single-disc albums with 'N. '
    prefixes have the prefix stripped to avoid double numbering.
    """
    items = _flatten_and_filter(payload)

    if _detect_multi_disc(items):
        return _compact_multi_disc(items)

    if _detect_single_disc_numbered(items):
        result: List[str] = []
        for item in items:
            m = _SINGLE_DISC_NUMBER_RE.match(item.get("title", ""))
            track_num = int(m.group(1)) if m else 0
            result.append(_compact_item(item, track_num, strip_title_prefix=True))
        return result

    result = []
    for idx, item in enumerate(items, 1):
        result.append(_compact_item(item, idx, strip_title_prefix=False))
    return result
