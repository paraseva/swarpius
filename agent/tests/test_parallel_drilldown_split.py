"""Parallel sibling drill-down must not share one Roon browse session.

References that came from the same parent search carry the same
``roon_session_key``. When several are drilled concurrently they would share a
single Roon browse cursor and corrupt each other (duplicated track lists / the
wrong track). The fix: a contended drill is leased its own session and
re-establishes there.

These run real ``RoonSearchTool`` / browse logic over a stateful-cursor fake
(``tests/_stateful_browse_fake.py``), so the only thing stubbed is the Roon
socket. Concurrency is simulated deterministically by marking the parent
session in-use before the contended drill (the live Roon test validates the
real race end to end).
"""

from __future__ import annotations

from tests._stateful_browse_fake import StatefulBrowseFake, node
from tools.roon_search import (
    RoonSearchTool,
    RoonSearchToolConfig,
    RoonSearchToolInputSchema,
)


def _album(title: str) -> node:
    return node(
        title, "Artist", image_key=f"img-{title}", hint="list",
        children=[
            node("Play Album", "", hint="action_list"),
            node(f"{title} Track 1", "Artist", hint="action_list"),
            node(f"{title} Track 2", "Artist", hint="action_list"),
        ],
    )


def _build_fake(max_sessions: int = 16) -> StatefulBrowseFake:
    fake = StatefulBrowseFake(max_sessions=max_sessions)
    fake.install_search("artist", [
        node("Artist", "5 Albums", hint="list", children=[
            node("Play Artist", "", hint="action_list"),
            _album("Album A"),
            _album("Album B"),
            _album("Album C"),
        ]),
    ])
    return fake


def _tool(fake: StatefulBrowseFake) -> RoonSearchTool:
    return RoonSearchTool(RoonSearchToolConfig(roon_connection=fake))


def _new_search(tool: RoonSearchTool, query: str):
    return tool.run(RoonSearchToolInputSchema(operation="new_search", search_string=query))


def _drill(tool: RoonSearchTool, reference: str):
    return tool.run(
        RoonSearchToolInputSchema(operation="drill_down_reference", reference=reference),
    )


def _ref_for(output, title: str) -> str:
    for group in output.groups:
        for item in group.items:
            if item.title == title:
                return item.reference
    raise AssertionError(f"no reference for {title!r} in {_titles(output)}")


def _titles(output) -> list[str]:
    return [item.title for group in output.groups for item in group.items]


def _drill_to_albums(tool: RoonSearchTool):
    """Search → drill the artist → return (album-list output, parent session)."""
    search = _new_search(tool, "artist")
    albums = _drill(tool, _ref_for(search, "Artist"))
    return albums, albums.session_key


def test_uncontended_sibling_drill_uses_parent_session_and_returns_correct_tracks():
    """Sanity check on the fake + the fast path: with no contention a drill
    stays on the parent session and returns the right album's tracks."""
    fake = _build_fake()
    tool = _tool(fake)

    albums, parent_session = _drill_to_albums(tool)
    result = _drill(tool, _ref_for(albums, "Album B"))

    assert result.session_key == parent_session
    assert "Album B Track 1" in _titles(result)
    assert "Album A Track 1" not in _titles(result)


def test_contended_sibling_drill_splits_to_its_own_session_and_stays_correct():
    """The fix's contract: a drill whose parent session is already in use by a
    concurrent operation runs on a *different* session, and still returns its
    own album's tracks."""
    fake = _build_fake()
    tool = _tool(fake)

    albums, parent_session = _drill_to_albums(tool)
    album_b = _ref_for(albums, "Album B")

    # A concurrent sibling drill is already holding the parent session.
    fake.session_manager._in_use.add(parent_session)

    result = _drill(tool, album_b)

    assert result.session_key != parent_session
    assert "Album B Track 1" in _titles(result)
    assert "Album A Track 1" not in _titles(result)


def test_reference_recovers_after_its_session_is_recycled():
    """Lifetime decouple: recycling a ref's session slot invalidates its cached
    binding but keeps the ref, so it re-establishes from its recipe and still
    drills the right album (it would have been a 'not found' before)."""
    fake = _build_fake(max_sessions=2)
    tool = _tool(fake)

    albums, _ = _drill_to_albums(tool)
    album_b = _ref_for(albums, "Album B")

    # Exhaust the 2-slot pool so the album refs' session slot is recycled.
    fake.session_manager.new_search_session()
    fake.session_manager.new_search_session()

    result = _drill(tool, album_b)

    assert "Album B Track 1" in _titles(result)
    assert "Album A Track 1" not in _titles(result)
