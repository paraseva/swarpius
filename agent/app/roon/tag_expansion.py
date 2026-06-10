from __future__ import annotations

import re
from typing import Any, Callable, Optional

# ── Result tag expansion ─────────────────────────────────────────

_NON_DISPLAY_FIRST_ITEMS_FOR_TAG = {
    "Play Album", "Play Artist", "Play Playlist",
    "Play Composer", "Play Work",
    "Start Radio", "Shuffle",
}

_LIST_TAG_PATTERN = re.compile(
    r"<list\s*(?P<attrs>[^/]*?)\s*/>",
    re.IGNORECASE,
)
_ATTR_PATTERN = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def _parse_list_tag_attrs(attrs_str: str) -> dict[str, Optional[str]]:
    """Parse ref and title attributes from a ``<list .../>`` tag."""
    attrs: dict[str, Optional[str]] = {"ref": None, "title": None}
    for m in _ATTR_PATTERN.finditer(attrs_str):
        key = m.group(1).lower()
        if key in attrs:
            attrs[key] = m.group(2)
    return attrs


def _get_first_item_title_for_tag(groups: list) -> Optional[str]:
    """Get the title of the first item for indexing decisions."""
    if not groups:
        return None
    first = groups[0]
    if isinstance(first, dict):
        if "items" in first:
            sub = first.get("items", [])
            if sub and isinstance(sub[0], dict):
                return sub[0].get("title")
        return first.get("title")
    return None


def _flatten_groups(groups: list) -> list[dict]:
    """Flatten grouped results into a flat item list."""
    items: list[dict] = []
    for entry in groups:
        if isinstance(entry, dict) and "items" in entry:
            sub = entry.get("items", [])
            if isinstance(sub, list):
                items.extend(sub)
            else:
                items.append(entry)
        else:
            items.append(entry)
    return items


def transform_queue_items_to_result_shape(raw_items: list, ref_map=None) -> list:
    """Map raw Roon queue items to the result item dict shape.

    Each raw item has queue_item_id, one_line, two_line, three_line.
    Output dicts have title, extra_info, group, reference — compatible
    with _compact_item and _format_display_item.

    If *ref_map* (a ``QueueReferenceMap``) is provided, pre-assigned
    references are used.  Otherwise random hex references are minted
    per call (legacy behaviour for tests).
    """
    import secrets

    result = []
    for item in raw_items:
        two_line = item.get("two_line", {})
        three_line = item.get("three_line", {})
        one_line = item.get("one_line", {})
        title = two_line.get("line1") or one_line.get("line1", "")
        extra_info = two_line.get("line2", "")
        group = three_line.get("line3", "")
        queue_item_id = item.get("queue_item_id", 0)
        if ref_map is not None:
            reference = ref_map.get_ref(queue_item_id) or secrets.token_hex(3)[:5]
        else:
            reference = secrets.token_hex(3)[:5]
        result.append({
            "title": title,
            "extra_info": extra_info,
            "group": group,
            "reference": f"Q:{reference}",
            "queue_item_id": queue_item_id,
        })
    return result


def _format_display_item(item: Any, index: int, strip_prefix: bool = False) -> str:
    """Format a single item for user-facing display."""
    if not isinstance(item, dict):
        return f"{index}. {item}"
    title = item.get("title", "")
    if strip_prefix:
        from app.roon.compact_formatting import _strip_track_prefix
        title = _strip_track_prefix(title)
    group = item.get("group", "")
    extra = item.get("extra_info", "")
    parts = [title]
    if group and group != "-":
        parts.append(group)
    if extra:
        parts.append(extra)
    return f"{index}. {' — '.join(parts)}"


def _format_results_as_list(
    groups: list,
    title: Optional[str] = None,
) -> str:
    """Format search result groups as ``<list>`` block(s).

    Multi-disc albums (d-t title pattern) produce nested ``<list>`` blocks
    — one outer block for the album, one inner block per disc.
    Single-disc numbered titles have prefixes stripped.
    """
    from collections import defaultdict

    from app.roon.compact_formatting import (
        _MULTI_DISC_PREFIX_RE,
        _SINGLE_DISC_NUMBER_RE,
        _detect_single_disc_numbered,
    )

    flat_items = _flatten_groups(groups)
    if not flat_items:
        return ""

    first_title = _get_first_item_title_for_tag(groups)
    has_action = first_title in _NON_DISPLAY_FIRST_ITEMS_FOR_TAG
    if has_action:
        flat_items = flat_items[1:]
    if not flat_items:
        return ""

    # Detect multi-disc: all items match "d-t Title"
    is_multi_disc = all(
        isinstance(item, dict) and _MULTI_DISC_PREFIX_RE.match(item.get("title", ""))
        for item in flat_items
    )

    if is_multi_disc:
        discs: defaultdict[int, list[tuple[int, dict]]] = defaultdict(list)
        for item in flat_items:
            m = _MULTI_DISC_PREFIX_RE.match(item.get("title", ""))
            if m:
                disc_num = int(m.group(1))
                track_num = int(m.group(2))
                stripped = dict(item)
                stripped["title"] = item["title"][m.end():]
                discs[disc_num].append((track_num, stripped))

        inner_blocks: list[str] = []
        total_tracks = 0
        for disc_num in sorted(discs):
            tracks = discs[disc_num]
            count = len(tracks)
            total_tracks += count
            lines = [_format_display_item(t, track_num) for track_num, t in tracks]
            body = "\n".join(lines)
            count_word = "track" if count == 1 else "tracks"
            inner_blocks.append(
                f"<list><summary>Disc {disc_num} ({count} {count_word})</summary>\n\n"
                f"{body}\n</list>"
            )

        disc_count = len(discs)
        label = title or "Search results"
        outer_summary = f"{label} ({total_tracks} tracks, {disc_count} discs)"
        inner = "\n\n".join(inner_blocks)
        return f"<list><summary>{outer_summary}</summary>\n\n{inner}\n\n</list>"

    # Single-disc-numbered: use extracted track number as the index so
    # gaps in Roon's response stay visible (matches compact_formatting).
    if _detect_single_disc_numbered(flat_items):
        lines: list[str] = []
        for item in flat_items:
            m = _SINGLE_DISC_NUMBER_RE.match(item.get("title", ""))
            track_num = int(m.group(1)) if m else 0
            lines.append(_format_display_item(item, track_num, strip_prefix=True))
    else:
        lines = []
        for idx, item in enumerate(flat_items, 1):
            lines.append(_format_display_item(item, idx, strip_prefix=False))

    if not lines:
        return ""

    count = len(lines)
    count_label = f"{count} item" if count == 1 else f"{count} items"
    summary = f"{title} ({count_label})" if title else f"Search results ({count_label})"
    body = "\n".join(lines)
    return f"<list><summary>{summary}</summary>\n\n{body}\n</list>"


def expand_list_tags(
    text: str,
    result_store: dict[str, Any],
) -> str:
    """Replace ``<list ref="..." .../>`` tags with formatted ``<list>`` blocks.

    *result_store* maps handles (``res_NNNNN``) to cached group payloads.
    The ``ref`` attribute is required — tags without it are stripped.
    """

    def _replace(match: re.Match) -> str:
        attrs = _parse_list_tag_attrs(match.group("attrs"))
        ref = attrs["ref"]
        if not ref:
            return ""
        groups = result_store.get(ref)
        if not groups or not isinstance(groups, list):
            return ""
        return _format_results_as_list(groups, title=attrs["title"])

    return _LIST_TAG_PATTERN.sub(_replace, text)


# ── Queue tag expansion ──────────────────────────────────────────

_QUEUE_TAG_PATTERN = re.compile(
    r'<queue\s+zone="(?P<zone>[^"]*?)"\s*/>',
    re.IGNORECASE,
)


def expand_queue_tags(
    text: str,
    queue_display_cache: dict[str, str],
    resolve_zone: Callable[[str], str],
) -> str:
    """Replace ``<queue zone="..." />`` tags with cached ``<list>`` blocks.

    *queue_display_cache* maps resolved zone display names to pre-formatted
    ``<list>`` blocks populated by the roon_status tool during this request.
    *resolve_zone* handles alias, group name, and case-insensitive lookup.
    """

    def _replace(match: re.Match) -> str:
        zone_attr = match.group("zone")
        if not zone_attr or not zone_attr.strip():
            return '[Queue zone not specified]'
        try:
            resolved = resolve_zone(zone_attr)
        except Exception:
            return f'[Queue for "{zone_attr}" not available]'
        cached = queue_display_cache.get(resolved)
        if cached is not None:
            return cached
        return f'[Queue for "{zone_attr}" not available]'

    return _QUEUE_TAG_PATTERN.sub(_replace, text)
