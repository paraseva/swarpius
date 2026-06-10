"""Integration tests for the title-based reference recovery in roon_action.

When the coordinator submits an item with a mistyped reference
(e.g. ``S:3d7cc`` for "Time Out" when the real ref is ``S:3d8cc``),
the tool should attempt to recover the intended item via the submitted
title before raising "Reference not found".

Four outcomes, surfaced distinctly to the coordinator:

- Unique title match → action proceeds, result notes "reference mismatch,
  unique title match".
- Multi-title fuzzy winner → action proceeds, result notes "reference
  mismatch, disambiguated title by closest reference".
- Multi-title ambiguous tie → action fails with "ambiguous title,
  reference tied".
- No title match → existing behaviour ("unknown reference, no title
  match").
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
    """Wrap a flat item list as a single roon_search-style group."""
    return [{"group": "-", "items": items}]


def _run_shuffle(
    tool: RoonActionTool,
    item: RoonCoreItemSummarySchema,
) -> Any:
    params = RoonActionToolInputSchema(action="Shuffle", items=[item])
    return asyncio.run(tool.run_async(params))


# Album-shaped action list — matches what real Roon albums/tracks expose.
_ALBUM_ACTIONS = ["Play Now", "Add Next", "Queue", "Start Radio"]


class TestReferenceRecoveryInAction(unittest.TestCase):
    def _make_tool(
        self,
        registrations: Dict[str, str],
        result_store: Dict[str, Any],
    ) -> tuple[RoonActionTool, BrowseFake]:
        """*registrations* maps ref_id (without S: prefix) → title."""
        fake = BrowseFake()
        for ref_id, title in registrations.items():
            fake.register_item(ref_id, title, action_titles=_ALBUM_ACTIONS)
        tool = make_action_tool(fake, result_store=result_store)
        return tool, fake

    # ── Path 1: unique title match ───────────────────────────────────

    def test_unique_title_match_recovers_and_succeeds(self):
        """Mistyped ref, real ref exists in store under the same title →
        action completes successfully."""
        store = {
            "res_00001": _store_group([
                {"title": "Time Out", "reference": "S:3d8cc"},
                {"title": "Abbey Road", "reference": "S:80bf1"},
            ]),
        }
        tool, fake = self._make_tool(
            registrations={"3d8cc": "Time Out", "80bf1": "Abbey Road"},
            result_store=store,
        )

        result = _run_shuffle(
            tool,
            RoonCoreItemSummarySchema(title="Time Out", reference="S:3d7cc"),
        )

        self.assertIn("SUCCESSFUL", result.result)
        self.assertIsNone(result.error)
        # Connection should have been called with the *recovered* ref.
        self.assertIn("S:3d8cc", fake.get_media_actions_calls)

    def test_unique_title_match_annotates_result_with_recovery_note(self):
        """The coordinator should see that a reference mismatch was
        recovered, not just silent success."""
        store = {
            "res_00001": _store_group([
                {"title": "Time Out", "reference": "S:3d8cc"},
            ]),
        }
        tool, _fake = self._make_tool(
            registrations={"3d8cc": "Time Out"},
            result_store=store,
        )

        result = _run_shuffle(
            tool,
            RoonCoreItemSummarySchema(title="Time Out", reference="S:3d7cc"),
        )

        self.assertIn(
            "reference mismatch, unique title match",
            result.result.lower(),
        )

    # ── Path 2: multi-title fuzzy winner ─────────────────────────────

    def test_multi_title_fuzzy_winner_recovers_and_succeeds(self):
        store = {
            "res_00001": _store_group([
                {"title": "Greatest Hits", "reference": "S:11fde"},
                {"title": "Greatest Hits", "reference": "S:99999"},
            ]),
        }
        # S:11fdf: distance 1 from S:11fde, distance 5 from S:99999.
        tool, fake = self._make_tool(
            registrations={"11fde": "Greatest Hits", "99999": "Greatest Hits"},
            result_store=store,
        )

        result = _run_shuffle(
            tool,
            RoonCoreItemSummarySchema(title="Greatest Hits", reference="S:11fdf"),
        )

        self.assertIn("SUCCESSFUL", result.result)
        self.assertIn("S:11fde", fake.get_media_actions_calls)

    def test_multi_title_fuzzy_winner_annotates_result(self):
        store = {
            "res_00001": _store_group([
                {"title": "Greatest Hits", "reference": "S:11fde"},
                {"title": "Greatest Hits", "reference": "S:99999"},
            ]),
        }
        tool, _fake = self._make_tool(
            registrations={"11fde": "Greatest Hits", "99999": "Greatest Hits"},
            result_store=store,
        )

        result = _run_shuffle(
            tool,
            RoonCoreItemSummarySchema(title="Greatest Hits", reference="S:11fdf"),
        )

        self.assertIn(
            "reference mismatch, disambiguated title by closest reference",
            result.result.lower(),
        )

    # ── Path 3: ambiguous tie ────────────────────────────────────────

    def test_multi_title_tied_distance_fails_with_ambiguity(self):
        """Two title matches equidistant from the submitted reference —
        the tool refuses to guess and surfaces a clear ambiguity error."""
        store = {
            "res_00001": _store_group([
                {"title": "Greatest Hits", "reference": "S:a1234"},
                {"title": "Greatest Hits", "reference": "S:a1235"},
            ]),
        }
        tool, _fake = self._make_tool(
            registrations={"a1234": "Greatest Hits", "a1235": "Greatest Hits"},
            result_store=store,
        )

        # S:a1236 is distance 1 from both.
        result = _run_shuffle(
            tool,
            RoonCoreItemSummarySchema(title="Greatest Hits", reference="S:a1236"),
        )

        # FAILED, not SUCCESSFUL.
        self.assertIn("FAILED", result.result)
        self.assertIsNotNone(result.errors)
        combined = "; ".join(e.error for e in result.errors)
        self.assertIn("ambiguous title", combined.lower())
        self.assertIn("reference tied", combined.lower())
        # Both candidate refs should be named so the coordinator can
        # re-search / clarify.
        self.assertIn("S:a1234", combined)
        self.assertIn("S:a1235", combined)

    # ── Path 4: no title match ───────────────────────────────────────

    def test_no_title_match_fails_with_clear_message(self):
        store = {
            "res_00001": _store_group([
                {"title": "Abbey Road", "reference": "S:80bf1"},
            ]),
        }
        tool, _fake = self._make_tool(
            registrations={"80bf1": "Abbey Road"},
            result_store=store,
        )

        result = _run_shuffle(
            tool,
            RoonCoreItemSummarySchema(title="Not In Library", reference="S:zzzzz"),
        )

        self.assertIn("FAILED", result.result)
        self.assertIsNotNone(result.errors)
        combined = "; ".join(e.error for e in result.errors)
        # Message should flag both the reference miss and the title miss,
        # so the coordinator knows the fallback was attempted.
        self.assertIn("unknown reference", combined.lower())
        self.assertIn("no title match", combined.lower())

    # ── Recovery should not fire when reference resolves ─────────────

    def test_no_recovery_when_reference_resolves(self):
        """Sanity check: if the submitted reference is good, the tool
        goes straight through the normal path without touching the
        recovery logic."""
        store = {
            "res_00001": _store_group([
                {"title": "Time Out", "reference": "S:3d8cc"},
                {"title": "Time Out", "reference": "S:3d9cc"},  # ambiguous!
            ]),
        }
        tool, fake = self._make_tool(
            registrations={"3d8cc": "Time Out", "3d9cc": "Time Out"},
            result_store=store,
        )

        # Submitted ref is valid — recovery must not kick in and
        # produce an ambiguity error despite the duplicate titles.
        result = _run_shuffle(
            tool,
            RoonCoreItemSummarySchema(title="Time Out", reference="S:3d8cc"),
        )

        self.assertIn("SUCCESSFUL", result.result)
        self.assertIsNone(result.error)
        self.assertIn("S:3d8cc", fake.get_media_actions_calls)


if __name__ == "__main__":
    unittest.main()
