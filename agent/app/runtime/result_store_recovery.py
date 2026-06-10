"""Title-based reference recovery for mistyped references.

When the coordinator submits an item to a tool with a reference that
fails to resolve (typical cause: LLM transcription slip on the 5-char
hex ID — e.g. ``S:3d8cc`` → ``S:3d7cc``), we try to recover via the
submitted ``title``:

- 0 items in the result store match the title → give up.
- 1 match → use it.
- 2+ matches → pick the candidate whose reference is Levenshtein-closest
  to the submitted (mistyped) reference, but only if it is *strictly*
  closer than the runner-up; otherwise report ambiguity.

Scope: the whole result store. Coordinators can refer back to any prior
search within a request, and references are unique within a session, so
scanning everything is correct and cheap (a few hundred items max, and
this only fires on a reference miss in the first place).

Observability: all non-``UNIQUE_TITLE`` (and ``FUZZY_WINNER``) outcomes
are distinct enum values so callers can surface what happened to the
coordinator and the analyser can count how often the LLM mistypes refs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional


class RecoveryOutcome(Enum):
    UNIQUE_TITLE = "unique_title"
    FUZZY_WINNER = "fuzzy_winner"
    AMBIGUOUS_TIE = "ambiguous_tie"
    NO_MATCH = "no_match"


@dataclass
class ReferenceCandidateMatch:
    """An item recovered from the result store whose title matches."""

    reference: str
    title: str
    handle: str
    group: Optional[str] = None
    distance: int = 0
    """Levenshtein distance to the submitted (mistyped) reference.

    Only meaningful when there is more than one title match; zero
    otherwise."""


@dataclass
class UniqueTitleMatch:
    outcome: RecoveryOutcome = field(default=RecoveryOutcome.UNIQUE_TITLE, init=False)
    candidate: ReferenceCandidateMatch = field(default=None)  # type: ignore[assignment]


@dataclass
class FuzzyTitleWinner:
    outcome: RecoveryOutcome = field(default=RecoveryOutcome.FUZZY_WINNER, init=False)
    candidate: ReferenceCandidateMatch = field(default=None)  # type: ignore[assignment]
    runner_up_distance: int = 0


@dataclass
class AmbiguousTitleTie:
    outcome: RecoveryOutcome = field(default=RecoveryOutcome.AMBIGUOUS_TIE, init=False)
    tied_candidates: List[ReferenceCandidateMatch] = field(default_factory=list)


@dataclass
class NoTitleMatch:
    outcome: RecoveryOutcome = field(default=RecoveryOutcome.NO_MATCH, init=False)


RecoveryResult = (
    UniqueTitleMatch
    | FuzzyTitleWinner
    | AmbiguousTitleTie
    | NoTitleMatch
)


def levenshtein(a: str, b: str) -> int:
    """Classical edit distance. Strings here are 5-8 char hex IDs, so the
    O(m*n) table is trivially cheap."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    prev = list(range(len(b) + 1))
    curr = [0] * (len(b) + 1)
    for i, ca in enumerate(a, start=1):
        curr[0] = i
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr[j] = min(
                prev[j] + 1,        # deletion
                curr[j - 1] + 1,    # insertion
                prev[j - 1] + cost, # substitution
            )
        prev, curr = curr, prev
    return prev[len(b)]


def _iter_items(payload: Any) -> Iterable[dict]:
    """Yield item dicts from a result_store payload.

    Supported shapes:
      - list of group dicts: ``[{"group": ..., "items": [...]}, ...]``
        (roon_search output)
      - flat list of item dicts
      - anything else is ignored (e.g. web_search payloads, strings).
    """
    if not isinstance(payload, list):
        return
    for entry in payload:
        if isinstance(entry, dict) and isinstance(entry.get("items"), list):
            for item in entry["items"]:
                if isinstance(item, dict):
                    yield item
            continue
        if isinstance(entry, dict):
            yield entry


def _collect_candidates(
    result_store: Dict[str, Any],
    title: str,
) -> List[ReferenceCandidateMatch]:
    """Scan all handles for items whose normalised title matches
    (case-insensitive, trimmed, track-number prefix and trailing
    parens dropped) and that carry an ``S:`` reference."""
    needle = _normalise_title(title)
    if not needle:
        return []
    matches: List[ReferenceCandidateMatch] = []
    for handle, payload in result_store.items():
        for item in _iter_items(payload):
            reference = item.get("reference")
            item_title = item.get("title")
            if not reference or not item_title:
                continue
            if not isinstance(reference, str) or not reference.startswith("S:"):
                continue
            if _normalise_title(item_title) != needle:
                continue
            matches.append(ReferenceCandidateMatch(
                reference=reference,
                title=item_title,
                handle=handle,
                group=item.get("group"),
            ))
    return matches


# Trailing parenthesised / bracketed suffix patterns commonly added by
# streaming services (e.g. "(Remastered)", "[Live]", "(2024 Remaster)",
# "(Bonus Track)"). Stripped before fuzzy comparison so an LLM that
# transcribes "Title" against a stored "Title (Remastered)" still
# matches. Leading parens stay — some real titles begin with them
# (e.g. "(I Can't Get No) Satisfaction").
_TRAILING_SUFFIX_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")

# Leading "N. " or "d-t " track-number prefix Roon attaches to items
# in album drill-downs. The compact formatter strips these for display
# but the result store keeps raw titles, so the LLM submits the
# stripped form while the store holds the prefixed form — without
# this normalisation the two never match.
_LEADING_TRACK_NUMBER_RE = re.compile(r"^(?:\d+\.|\d+-\d+)\s+")


def _normalise_title(title: str) -> str:
    """Lowercase, strip whitespace, drop leading track-number prefix,
    drop trailing parenthesised / bracketed suffixes."""
    s = title.strip().casefold()
    s = _LEADING_TRACK_NUMBER_RE.sub("", s, count=1)
    while True:
        stripped = _TRAILING_SUFFIX_RE.sub("", s)
        if stripped == s:
            break
        s = stripped
    return s


def titles_match(submitted: str, stored: str, threshold: float = 0.85) -> bool:
    """Fuzzy comparison of two titles.

    Tolerates the small transcription variations LLMs make — case,
    whitespace, dropped trailing parenthesised suffixes — without
    accepting genuinely different titles.

    Returns ``False`` if either input is empty (after normalisation),
    so missing data never silently matches.
    """
    if not submitted or not stored:
        return False
    a = _normalise_title(submitted)
    b = _normalise_title(stored)
    if not a or not b:
        return False
    if a == b:
        return True
    distance = levenshtein(a, b)
    longer = max(len(a), len(b))
    similarity = 1 - (distance / longer)
    return similarity >= threshold


def lookup_title_for_reference(
    result_store: Dict[str, Any],
    reference: str,
) -> Optional[str]:
    """Inverse of ``recover_reference``: given a (presumed-valid)
    reference, find the title that was associated with it in the
    result store. Returns ``None`` if the reference doesn't appear.

    Used by the title/reference-mismatch check in ``roon_action`` —
    after a reference resolves successfully, we compare the submitted
    title against the stored title to detect cases where the LLM has
    paired a valid reference with a title for a different item (or
    vice versa).
    """
    for _handle, payload in result_store.items():
        for item in _iter_items(payload):
            ref = item.get("reference")
            title = item.get("title")
            if ref == reference and isinstance(title, str):
                return title
    return None


_CATEGORY_GATEWAY_TITLE_RE = re.compile(r"^[A-Z][a-z]+s$")
_CATEGORY_GATEWAY_EXTRA_INFO_RE = re.compile(r"^\d+ Results?$")


def lookup_category_gateway_for_reference(
    result_store: Dict[str, Any],
    reference: str,
) -> Optional[dict]:
    """If ``reference`` resolves to a category-gateway item in the
    store (e.g. ``Tracks | 87 Results``), return the stored item
    dict; ``None`` otherwise.

    Fingerprint: ``title`` matches ``^[A-Z][a-z]+s$``; ``extra_info``
    matches ``^\\d+ Results?$``. Both must match — single-field
    matches don't trigger. The roon_action pre-flight uses this to
    reject gateway refs before any Roon call.
    """
    for _handle, payload in result_store.items():
        for item in _iter_items(payload):
            if item.get("reference") != reference:
                continue
            title = item.get("title")
            extra_info = item.get("extra_info")
            if not isinstance(title, str) or not isinstance(extra_info, str):
                return None
            if (
                _CATEGORY_GATEWAY_TITLE_RE.match(title)
                and _CATEGORY_GATEWAY_EXTRA_INFO_RE.match(extra_info)
            ):
                return item
            return None
    return None


def lookup_references_for_title(
    result_store: Dict[str, Any],
    title: str,
) -> List[str]:
    """Find all references in the result store whose stored title
    fuzzy-matches the submitted title (via ``titles_match``).
    Returns an empty list if no match. Order follows result-store
    iteration; duplicates by reference are dropped.

    Used by the title/reference-mismatch error message in
    ``roon_action`` — when a title and its paired reference disagree,
    we show the LLM both interpretations: which title the reference
    points to (via ``lookup_title_for_reference``) *and* which
    reference(s) the title points to (this function), so the LLM
    can pick the right pair without having to re-search.
    """
    matches: List[str] = []
    seen: set[str] = set()
    for _handle, payload in result_store.items():
        for item in _iter_items(payload):
            ref = item.get("reference")
            stored = item.get("title")
            if not isinstance(ref, str) or not isinstance(stored, str):
                continue
            if ref in seen:
                continue
            if titles_match(title, stored):
                matches.append(ref)
                seen.add(ref)
    return matches


def recover_reference(
    result_store: Dict[str, Any],
    title: str,
    typoed_reference: str,
) -> RecoveryResult:
    """Attempt to recover the intended reference from the submitted title.

    See module docstring for the resolution rules.
    """
    candidates = _collect_candidates(result_store, title)

    if not candidates:
        return NoTitleMatch()

    if len(candidates) == 1:
        return UniqueTitleMatch(candidate=candidates[0])

    for candidate in candidates:
        candidate.distance = levenshtein(candidate.reference, typoed_reference)
    candidates.sort(key=lambda c: c.distance)

    best = candidates[0]
    runner_up = candidates[1]
    if best.distance == runner_up.distance:
        tied = [c for c in candidates if c.distance == best.distance]
        return AmbiguousTitleTie(tied_candidates=tied)
    return FuzzyTitleWinner(candidate=best, runner_up_distance=runner_up.distance)
