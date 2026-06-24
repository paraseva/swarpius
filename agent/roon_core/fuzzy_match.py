"""Pure-function fuzzy matching helpers for Roon browse results.

Extracted from ``RoonBrowseMixin``. The functions here have no
self/state dependencies — they take items + identities/strings and
return scores or filtered/sorted lists. Used by ``browse.py``'s
``resolve_reference``, ``compile_output``, and
``_correct_via_category_search``.
"""

from __future__ import annotations

import re
from itertools import compress
from typing import List, Optional, Tuple

from thefuzz import fuzz

from roon_core.browse_session import ItemIdentity
from roon_core.schemas import RoonCoreItemSchema

_NON_ALPHA_EDGES_RE = re.compile(r"^[^a-zA-Z]+|[^a-zA-Z]+$")


def normalise_title(title: str) -> str:
    """Strip non-alphabetic leading/trailing chars and lower-case.

    ``"1. Rivers of Babylon"``  → ``"rivers of babylon"``
    ``"Rivers Of Babylon"``     → ``"rivers of babylon"``
    ``"(Remix) Track Name!!!"`` → ``"remix) track name"``
    """
    return _NON_ALPHA_EDGES_RE.sub("", title).lower()


def fuzzy_match_and_sort(
    items: List[RoonCoreItemSchema],
    sort_strings: List[str],
    threshold: int = 50,
    field_to_match: str = "title",
) -> List[RoonCoreItemSchema]:
    """Filter *items* whose ``field_to_match`` scores above *threshold*
    against the joined *sort_strings*; return survivors descending by
    score."""
    if not items:
        return []

    sort_strings = [s.strip().lower() for s in sort_strings]
    sort_string = " ".join(sort_strings)

    fuzz_ratios: List[float] = [
        fuzz.WRatio(getattr(item, field_to_match, "").lower(), sort_string)
        for item in items
    ]
    retain = [x > threshold for x in fuzz_ratios]
    items = list(compress(items, retain))
    fuzz_ratios = list(compress(fuzz_ratios, retain))

    sorted_tuples = sorted(
        zip(fuzz_ratios, items), key=lambda x: x[0], reverse=True,
    )
    return [x for _, x in sorted_tuples]


def fuzzy_find(
    items: List[RoonCoreItemSchema],
    identity: ItemIdentity,
    threshold: int = 75,
) -> Optional[RoonCoreItemSchema]:
    """Find the best-matching item by semantic identity (title +
    subtitle + hint + image_key). Scoring weights title 0.7, subtitle
    0.3; hint and image_key are exact-match filters when present on both
    sides (image_key — a stable artwork id — separates same-title,
    same-subtitle releases). Returns the highest scorer above *threshold*,
    or None.

    Note: the default threshold combined with the 70/30 weighting
    means an identity without subtitle can never score above 70 — a
    constraint pinned by ``test_browse_fuzzy_match.test_default_threshold_requires_subtitle``.
    Production callers always supply identities built from real Roon
    items, which carry subtitles.
    """
    candidates: List[Tuple[float, RoonCoreItemSchema]] = []
    for item in items:
        title_score = fuzz.WRatio(item.title.lower(), identity.title.lower())

        subtitle_score = 0.0
        if identity.subtitle and item.subtitle:
            subtitle_score = fuzz.WRatio(
                item.subtitle.lower(), identity.subtitle.lower(),
            )

        if identity.hint and item.hint and identity.hint != item.hint:
            continue

        if (
            identity.image_key
            and item.image_key
            and identity.image_key != item.image_key
        ):
            continue

        score = title_score * 0.7 + subtitle_score * 0.3
        if score >= threshold:
            candidates.append((score, item))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]
