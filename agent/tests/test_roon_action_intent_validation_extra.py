"""Tests for composer + work intent validation in roon_action.

Composer is the persona-family analogue of Artist: the resolved item
must terminate at the ``{Shuffle, Start Radio}`` action_list signature
or the action is rejected. The ``Play Composer`` action exists as the
ergonomic LLM-facing affordance (mirrors ``Play Artist``).

Work is the container-family analogue of Album/Playlist for classical
compositions. Its terminal action_list signature is shared with album
(``{Play Now, Add Next, Queue, Start Radio}``), so validation has to
fire at the gateway level — only the ``Play Work`` gateway title
distinguishes a Work from an Album. ``intended_item_category="work"``
is internal-only (no ``Play Work`` action exposed to the LLM).
"""

import asyncio
import unittest
from typing import Any, Dict, List

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from roon_core.schemas import RoonCoreItemSummarySchema  # noqa: E402

try:
    from tests._browse_fake import BrowseFake, make_action_tool
except ModuleNotFoundError:
    from _browse_fake import BrowseFake, make_action_tool

from tools.roon_action import (  # noqa: E402
    RoonActionTool,
    RoonActionToolInputSchema,
)


def _store_group(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"group": "-", "items": items}]


def _make_tool_with_action_list(
    ref_id: str,
    title: str,
    action_titles: List[str],
    result_store: Dict[str, Any],
) -> tuple[RoonActionTool, BrowseFake]:
    fake = BrowseFake()
    fake.register_item(ref_id, title, action_titles=action_titles)
    tool = make_action_tool(fake, result_store=result_store)
    return tool, fake


def _make_tool_with_gateway(
    ref_id: str,
    title: str,
    gateway: str,
    result_store: Dict[str, Any],
) -> tuple[RoonActionTool, BrowseFake]:
    """Register a ref whose drill yields a gateway level. Gateway-into
    yields the standard album/work action_list."""
    fake = BrowseFake()
    fake.register_item(ref_id, title, gateway=gateway)
    tool = make_action_tool(fake, result_store=result_store)
    return tool, fake


# ----------------------------------------------------------------------
# Play Composer tests
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# Work intent tests (request-level intended_item_category)
# ----------------------------------------------------------------------


class TestWorkIntentOnWork(unittest.TestCase):
    """Action with ``intended_item_category="work"`` against a real
    work ref proceeds — gateway is Play Work, action_list contains
    the requested action."""

    def test_play_now_with_work_intent_on_work_ref_succeeds(self):
        store = {
            "res_00001": _store_group([
                {
                    "title": "Piano Concerto No. 20 in D minor, K. 466",
                    "reference": "S:w0w0w",
                },
            ]),
        }
        tool, _fake = _make_tool_with_gateway(
            "w0w0w", "Piano Concerto No. 20 in D minor, K. 466",
            "Play Work", store,
        )
        params = RoonActionToolInputSchema(
            action="Play Now",
            items=[RoonCoreItemSummarySchema(
                title="Piano Concerto No. 20 in D minor, K. 466",
                reference="S:w0w0w",
            )],
            intended_item_category="work",
        )

        output = asyncio.run(tool.run_async(params))

        self.assertIn("SUCCESSFUL", output.result)
        self.assertIsNone(output.errors)


class TestWorkIntentOnNonWork(unittest.TestCase):
    """``intended_item_category="work"`` against a non-work ref
    (album / track / etc.): rejected at the gateway level with a
    directive error pointing the coordinator at the Works category."""

    def test_play_now_with_work_intent_on_album_ref_fails(self):
        store = {
            "res_00001": _store_group([
                {
                    "title": "Mozart: Violin Concertos",
                    "reference": "S:a1a1a",
                },
            ]),
        }
        tool, _fake = _make_tool_with_gateway(
            "a1a1a", "Mozart: Violin Concertos",
            "Play Album", store,
        )
        params = RoonActionToolInputSchema(
            action="Play Now",
            items=[RoonCoreItemSummarySchema(
                title="Mozart: Violin Concertos", reference="S:a1a1a",
            )],
            intended_item_category="work",
        )

        output = asyncio.run(tool.run_async(params))

        self.assertIn("FAILED", output.result)
        self.assertIsNotNone(output.errors)
        combined = "; ".join(e.error for e in output.errors)
        self.assertIn("not a work", combined.lower())
        self.assertIn("S:a1a1a", combined)
        self.assertIn("Works", combined)


if __name__ == "__main__":
    unittest.main()
