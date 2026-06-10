"""Regression test: parallel drill-downs on a shared RoonConnection must
produce outputs partitioned strictly by session — no cross-contamination.

Background (rq-c11-0001 on 2026-04-17): three concurrent drill_down calls
for playlists "Favourites 2", "1980", "1981" produced displayed lists where
two of the three contained tracks from a different playlist, because
RoonConnection.current_list was shared instance state. Whichever thread
wrote to self.current_list last won, and subsequent compile_output calls
from other threads read the winner's data instead of their own.

The fix must make browse sessions self-contained so drill + compile for
session A can never read data written by session B's drill. This test
exercises the observable behaviour (correct per-session outputs) without
asserting on how that's implemented.
"""

from __future__ import annotations

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List
from unittest.mock import mock_open, patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from roon_core.browse_session import SearchRecipe  # noqa: E402
from roon_core.connection import RoonConnection  # noqa: E402
from roon_core.schemas import RoonCoreItemSchema  # noqa: E402

# ── Fake Roon API ────────────────────────────────────────────────

def _item_dict(title: str, item_key: str, hint: str = "action_list") -> Dict[str, Any]:
    """Build the dict shape that browse_load returns for one item."""
    return {
        "title": title,
        "subtitle": "",
        "image_key": None,
        "item_key": item_key,
        "hint": hint,
    }


class _FakeRoonApi:
    """Stateful in-memory stand-in for the python-roonapi RoonApi.

    Simulates per-session browse state: each multi_session_key has its
    own "current list". ``browse_browse`` sets the session's current
    list based on the item_key drilled into; ``browse_load`` returns
    that list. The internal ``_current_per_session`` dict is guarded
    by a ``Lock`` so the fake itself remains correct under concurrent
    access — the lock protects the *fake's* state, not production's.
    """

    def __init__(
        self,
        items_by_session_and_key: Dict[tuple, List[Dict[str, Any]]],
    ) -> None:
        self._items_by_key = items_by_session_and_key
        self._current_per_session: Dict[str, List[Dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self.zones: Dict[str, Dict[str, Any]] = {
            "zone-1": {
                "display_name": "TestZone",
                "zone_id": "zone-1",
                "outputs": [{"output_id": "out-1", "display_name": "TestZone"}],
            },
        }
        # Mark parallel_browse.install() as already done so it doesn't try to
        # patch our fake. (It checks this flag first.)
        self._parallel_browse_installed = True
        self._roonsocket = object()  # sentinel — only id()'d by event subscription

    def register_state_callback(self, *args, **kwargs):
        pass

    def register_queue_callback(self, *args, **kwargs):
        pass

    def browse_browse(self, opts: Dict[str, Any]) -> Dict[str, Any]:
        session_key = opts.get("multi_session_key", "")
        if "pop_all" in opts:
            items = self._items_by_key.get((session_key, "_root_"), [])
        else:
            item_key = opts.get("item_key", "")
            items = self._items_by_key.get((session_key, item_key), [])
        with self._lock:
            self._current_per_session[session_key] = items
        return {"list": {"count": len(items), "title": "Test", "level": 1}}

    def browse_load(self, opts: Dict[str, Any]) -> Dict[str, Any]:
        session_key = opts.get("multi_session_key", "")
        with self._lock:
            items = list(self._current_per_session.get(session_key, []))
        return {
            "items": items,
            "list": {"count": len(items), "title": "Test", "level": 1},
        }


# ── Test fixture: a RoonConnection wired to a fake API ───────────

class _ConnectionFixture:
    """Construct a RoonConnection whose api is a FakeRoonApi."""

    def __init__(
        self,
        items_by_session_and_key: Dict[tuple, List[Dict[str, Any]]],
    ) -> None:
        self.fake_api = _FakeRoonApi(items_by_session_and_key)
        with patch("builtins.open", mock_open(read_data="{}")), \
             patch("roon_core.connection.RoonApi", return_value=self.fake_api), \
             patch(
                 "roon_core.connection.RoonConnection._get_id_and_token",
                 return_value={"core_id": "core-x", "token": "tok-x"},
             ), \
             patch(
                 "roon_core.connection.RoonConnection._lookup_known_core",
                 return_value=("127.0.0.1", 9330),
             ), \
             patch("roon_core.connection.RoonConnection._perform_auth"):
            self.conn = RoonConnection(
                default_zone="TestZone",
                roon_core_host="127.0.0.1",
                roon_core_port=9330,
            )


# ── Tests ────────────────────────────────────────────────────────

class TestParallelDrillSessionIsolation(unittest.TestCase):
    """Parallel drill_down + compile_output calls must not leak state
    across sessions."""

    def _build_playlist_fixture(self) -> _ConnectionFixture:
        # Three sessions, three distinct playlists. Each session's root
        # search returns a single-item container; drilling that container
        # returns the playlist's tracks.
        items = {
            ("s-A", "_root_"): [_item_dict("1980 Playlist", "101:0", "list")],
            ("s-A", "101:0"): [
                _item_dict("Don't Stand So Close to Me", "201:1"),
                _item_dict("Super Trouper",              "201:2"),
                _item_dict("D.I.S.C.O.",                 "201:3"),
            ],
            ("s-B", "_root_"): [_item_dict("1981 Playlist", "102:0", "list")],
            ("s-B", "102:0"): [
                _item_dict("Stand and Deliver",          "202:1"),
                _item_dict("Prince Charming",            "202:2"),
                _item_dict("Making Your Mind Up",        "202:3"),
            ],
            ("s-C", "_root_"): [_item_dict("Favourites 2", "103:0", "list")],
            ("s-C", "103:0"): [
                _item_dict("More Than This",             "203:1"),
                _item_dict("Head Over Heels",            "203:2"),
                _item_dict("Jaded",                      "203:3"),
            ],
        }
        return _ConnectionFixture(items)

    def _seed_session(self, conn, session_key: str) -> RoonCoreItemSchema:
        """Mint a session and drill down to the container item.
        Returns the container item, ready to drill into for the actual test."""
        sm = conn.session_manager
        sm._session_depth[session_key] = 0

        conn.browse_core(
            aux={"pop_all": True, "input": "seed"},
            session_key=session_key,
        )
        return sm.get_current_list(session_key).items[0]

    def _compile_titles(self, conn, session_key: str) -> List[str]:
        recipe = SearchRecipe(search_string="seed")
        groups = conn.compile_output(recipe=recipe, session_key=session_key)
        titles: List[str] = []
        for group in groups:
            for item in group.items:
                titles.append(item.title)
        return titles

    def test_interleaved_drills_preserve_per_session_output(self):
        """Drills and compiles for three sessions interleaved. Each session's
        compiled output must reflect *its own* drilled items, regardless of
        what other sessions drilled afterwards.

        Real-world manifestation: three parallel drill_down calls share a
        single RoonConnection. Between the time one thread drills and the
        time it reads its data back for compile, another thread's drill
        can stomp the shared state. This test reproduces the equivalent
        sequential interleaving — if the data is session-scoped, order
        shouldn't matter.
        """
        fixture = self._build_playlist_fixture()
        conn = fixture.conn

        # Set up the three sessions by navigating each to its container.
        seed_A = self._seed_session(conn, "s-A")
        seed_B = self._seed_session(conn, "s-B")
        seed_C = self._seed_session(conn, "s-C")
        recipe = SearchRecipe(search_string="seed")

        # Drill each session down into its playlist, one after the other.
        conn.drill_down(seed_A, recipe=recipe, session_key="s-A")
        conn.drill_down(seed_B, recipe=recipe, session_key="s-B")
        conn.drill_down(seed_C, recipe=recipe, session_key="s-C")

        # Now compile the outputs. Each session's output should contain
        # that session's items — not whatever was drilled last.
        titles_A = self._compile_titles(conn, "s-A")
        titles_B = self._compile_titles(conn, "s-B")
        titles_C = self._compile_titles(conn, "s-C")

        self.assertEqual(
            titles_A,
            ["Don't Stand So Close to Me", "Super Trouper", "D.I.S.C.O."],
            "Session A compile_output returned wrong items — sessions aren't isolated",
        )
        self.assertEqual(
            titles_B,
            ["Stand and Deliver", "Prince Charming", "Making Your Mind Up"],
            "Session B compile_output returned wrong items — sessions aren't isolated",
        )
        self.assertEqual(
            titles_C,
            ["More Than This", "Head Over Heels", "Jaded"],
            "Session C compile_output returned wrong items — sessions aren't isolated",
        )

    def test_parallel_drills_do_not_leak_items_between_sessions(self):
        """Concurrent drill+compile cycles from three threads. The
        ``Barrier`` provides deterministic synchronisation: every thread
        completes its drill *before* any thread starts compiling. If
        production used shared (non-per-session) state, the last drill
        would disrupt the others and at least two of the three compiles
        would return the wrong session's items — caught deterministically
        on a single iteration, no timing dependency.
        """
        expected_by_session = {
            "s-A": ["Don't Stand So Close to Me", "Super Trouper", "D.I.S.C.O."],
            "s-B": ["Stand and Deliver", "Prince Charming", "Making Your Mind Up"],
            "s-C": ["More Than This", "Head Over Heels", "Jaded"],
        }

        fixture = self._build_playlist_fixture()
        conn = fixture.conn
        seeds = {
            sk: self._seed_session(conn, sk) for sk in expected_by_session
        }
        barrier = threading.Barrier(len(seeds))

        def _do_drill(session_key: str, container_item: RoonCoreItemSchema):
            recipe = SearchRecipe(search_string="seed")
            conn.drill_down(
                drilldown_item=container_item,
                recipe=recipe,
                session_key=session_key,
            )
            barrier.wait()
            return session_key, self._compile_titles(conn, session_key)

        outputs: Dict[str, List[str]] = {}
        with ThreadPoolExecutor(max_workers=len(seeds)) as pool:
            futures = [
                pool.submit(_do_drill, sk, item)
                for sk, item in seeds.items()
            ]
            for fut in as_completed(futures):
                sk, titles = fut.result()
                outputs[sk] = titles

        for sk, expected_titles in expected_by_session.items():
            self.assertEqual(
                outputs.get(sk, []), expected_titles,
                f"Session {sk} compile_output returned wrong items — "
                f"sessions aren't isolated under concurrent drills",
            )


if __name__ == "__main__":
    unittest.main()
