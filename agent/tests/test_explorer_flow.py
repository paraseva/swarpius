"""Dispatch test for the Roon API Explorer handler.

The fake stubs the Roon API boundary (``browse_browse`` / ``browse_load``)
only; the real ``handle_explorer`` runs through its branches and is the
thing under test.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any

from app.io.explorer_flow import (
    EXPLORER_HIERARCHY,
    EXPLORER_SESSION_KEY,
    LOAD_COUNT,
    handle_explorer,
)


class _FakeRoonApi:
    def __init__(self) -> None:
        self.browse_calls: list[dict[str, Any]] = []
        self.load_calls: list[dict[str, Any]] = []
        self.browse_return: Any = {"action": "list"}
        self.load_return: Any = {"items": [{"item_key": "k1", "title": "T1"}]}

    def browse_browse(self, opts: dict[str, Any]) -> Any:
        self.browse_calls.append(opts)
        return self.browse_return

    def browse_load(self, opts: dict[str, Any]) -> Any:
        self.load_calls.append(opts)
        return self.load_return


def _runtime_with(api: _FakeRoonApi) -> SimpleNamespace:
    return SimpleNamespace(roon_connection=SimpleNamespace(api=api))


class TestExplorerHandler(unittest.IsolatedAsyncioTestCase):

    async def test_search_action_passes_input_and_pops_all(self) -> None:
        api = _FakeRoonApi()
        loop = asyncio.get_running_loop()

        result = await handle_explorer(
            {"action": "search", "input": "Judas Priest"},
            _runtime_with(api), loop,
        )

        self.assertEqual(len(api.browse_calls), 1)
        self.assertEqual(api.browse_calls[0], {
            "hierarchy": EXPLORER_HIERARCHY,
            "input": "Judas Priest",
            "multi_session_key": EXPLORER_SESSION_KEY,
            "pop_all": True,
        })
        self.assertEqual(api.load_calls[0], {
            "hierarchy": EXPLORER_HIERARCHY,
            "multi_session_key": EXPLORER_SESSION_KEY,
            "offset": 0,
            "count": LOAD_COUNT,
        })
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["action"], "search")
        self.assertEqual(result["browse"], api.browse_return)
        self.assertEqual(result["load"], api.load_return)

    async def test_navigate_action_passes_item_key(self) -> None:
        api = _FakeRoonApi()
        loop = asyncio.get_running_loop()

        await handle_explorer(
            {"action": "navigate", "item_key": "abc123"},
            _runtime_with(api), loop,
        )

        self.assertEqual(api.browse_calls[0], {
            "hierarchy": EXPLORER_HIERARCHY,
            "item_key": "abc123",
            "multi_session_key": EXPLORER_SESSION_KEY,
        })

    async def test_up_action_pops_one_level(self) -> None:
        api = _FakeRoonApi()
        loop = asyncio.get_running_loop()

        await handle_explorer({"action": "up"}, _runtime_with(api), loop)

        self.assertEqual(api.browse_calls[0], {
            "hierarchy": EXPLORER_HIERARCHY,
            "pop_levels": 1,
            "multi_session_key": EXPLORER_SESSION_KEY,
        })

    async def test_unknown_action_raises(self) -> None:
        api = _FakeRoonApi()
        loop = asyncio.get_running_loop()

        with self.assertRaises(ValueError):
            await handle_explorer(
                {"action": "delete-everything"}, _runtime_with(api), loop,
            )
        self.assertEqual(api.browse_calls, [])

    async def test_search_without_input_raises(self) -> None:
        api = _FakeRoonApi()
        loop = asyncio.get_running_loop()

        with self.assertRaises(ValueError):
            await handle_explorer(
                {"action": "search", "input": ""}, _runtime_with(api), loop,
            )
        self.assertEqual(api.browse_calls, [])

    async def test_navigate_without_item_key_raises(self) -> None:
        api = _FakeRoonApi()
        loop = asyncio.get_running_loop()

        with self.assertRaises(ValueError):
            await handle_explorer(
                {"action": "navigate"}, _runtime_with(api), loop,
            )
        self.assertEqual(api.browse_calls, [])

    async def test_no_roon_connection_raises(self) -> None:
        loop = asyncio.get_running_loop()
        runtime = SimpleNamespace(roon_connection=None)

        with self.assertRaises(RuntimeError):
            await handle_explorer({"action": "up"}, runtime, loop)
