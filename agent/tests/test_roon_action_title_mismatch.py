"""Integration tests for the title/reference-mismatch check in roon_action.

When the coordinator submits an item where:
  - the reference *is* valid (resolves successfully via Roon), AND
  - the submitted title doesn't match the stored title in the result
    store,

we don't know which signal to trust (right title + wrong-but-valid ref,
or right ref + wrong title), so the tool refuses to guess and surfaces
a clear error to the coordinator. The coordinator can then re-search
or ask the user to disambiguate.

Distinct from the reference-recovery path (``test_roon_action_reference_recovery.py``)
which fires when the reference *doesn't* resolve at all.
"""

import asyncio
import unittest
from typing import Any, Dict

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

_ALBUM_ACTIONS = ["Play Now", "Add Next", "Queue", "Start Radio"]


def _store_group(items):
    return [{"group": "-", "items": items}]


def _run_shuffle(tool: RoonActionTool, item: RoonCoreItemSummarySchema):
    params = RoonActionToolInputSchema(action="Shuffle", items=[item])
    return asyncio.run(tool.run_async(params))


def _make_tool(
    registrations: Dict[str, str],
    result_store: Dict[str, Any],
) -> tuple[RoonActionTool, BrowseFake]:
    fake = BrowseFake()
    for ref_id, title in registrations.items():
        fake.register_item(ref_id, title, action_titles=_ALBUM_ACTIONS)
    tool = make_action_tool(fake, result_store=result_store)
    return tool, fake


class TestTitleMatchesStoredProceeds(unittest.TestCase):
    """When the submitted title matches the stored title for the
    submitted reference, the action proceeds normally — this is the
    happy path, no regression vs current behaviour."""

    def test_case_insensitive_title_match_proceeds(self):
        store = {
            "res_00001": _store_group([
                {"title": "Abbey Road", "reference": "S:80bf1"},
            ]),
        }
        tool, _fake = _make_tool({"80bf1": "Abbey Road"}, store)

        result = _run_shuffle(
            tool,
            RoonCoreItemSummarySchema(title="ABBEY ROAD", reference="S:80bf1"),
        )

        self.assertIn("SUCCESSFUL", result.result)

    def test_dropped_remastered_suffix_proceeds(self):
        """LLMs commonly drop trailing '(Remastered)' / '(Live)' /
        '(2024 Remaster)' suffixes when transcribing titles. The
        check should tolerate this rather than block the action."""
        store = {
            "res_00001": _store_group([
                {"title": "Abbey Road (Remastered)", "reference": "S:80bf1"},
            ]),
        }
        tool, _fake = _make_tool({"80bf1": "Abbey Road (Remastered)"}, store)

        result = _run_shuffle(
            tool,
            RoonCoreItemSummarySchema(title="Abbey Road", reference="S:80bf1"),
        )

        self.assertIn("SUCCESSFUL", result.result)


class TestTitleMismatchesStoredFails(unittest.TestCase):
    """When the submitted title genuinely doesn't match the stored
    title, the action fails with a clear coordinator-facing error
    rather than playing the wrong thing."""

    def test_mismatch_when_submitted_title_not_in_store(self):
        """LLM submits 'Abbey Road' but reference S:80bf1 is actually
        for 'Let It Be' in the result store, AND 'Abbey Road' doesn't
        appear elsewhere in the store (i.e. the LLM hallucinated/
        mistranscribed the title). Error tells the coordinator both
        sides: what the ref points to, and that their submitted title
        wasn't seen anywhere recent."""
        store = {
            "res_00001": _store_group([
                {"title": "Let It Be", "reference": "S:80bf1"},
            ]),
        }
        tool, _fake = _make_tool({"80bf1": "Let It Be"}, store)

        result = _run_shuffle(
            tool,
            RoonCoreItemSummarySchema(title="Abbey Road", reference="S:80bf1"),
        )

        self.assertIn("FAILED", result.result)
        self.assertIsNotNone(result.errors)
        combined = "; ".join(e.error for e in result.errors)
        # Both titles + the reference should be in the message
        self.assertIn("Abbey Road", combined)
        self.assertIn("Let It Be", combined)
        self.assertIn("S:80bf1", combined)
        # And it should explicitly say the submitted title wasn't seen
        self.assertIn("doesn't appear", combined)

    def test_mismatch_with_unique_alternate_reference(self):
        """LLM submits ('Abbey Road', S:80bf1) but the store has both
        ('Let It Be', S:80bf1) AND ('Abbey Road', S:abc12). The error
        should present both interpretations: the ref's actual title
        AND the alternate ref the title corresponds to, so the LLM
        can pick the right pair without re-searching."""
        store = {
            "res_00001": _store_group([
                {"title": "Let It Be", "reference": "S:80bf1"},
                {"title": "Abbey Road", "reference": "S:abc12"},
            ]),
        }
        tool, _fake = _make_tool(
            {"80bf1": "Let It Be", "abc12": "Abbey Road"}, store,
        )

        result = _run_shuffle(
            tool,
            RoonCoreItemSummarySchema(title="Abbey Road", reference="S:80bf1"),
        )

        self.assertIn("FAILED", result.result)
        combined = "; ".join(e.error for e in result.errors)
        # Both refs and both titles appear, paired
        self.assertIn("S:80bf1", combined)
        self.assertIn("Let It Be", combined)
        self.assertIn("Abbey Road", combined)
        self.assertIn("S:abc12", combined)

    def test_mismatch_with_multiple_alternate_references(self):
        """Submitted title appears multiple times in the store under
        different references. List all candidates so the LLM can
        choose."""
        store = {
            "res_00001": _store_group([
                {"title": "Let It Be", "reference": "S:80bf1"},
                {"title": "Greatest Hits", "reference": "S:aaa11"},
                {"title": "Greatest Hits", "reference": "S:bbb22"},
            ]),
        }
        tool, _fake = _make_tool(
            {
                "80bf1": "Let It Be",
                "aaa11": "Greatest Hits",
                "bbb22": "Greatest Hits",
            },
            store,
        )

        result = _run_shuffle(
            tool,
            RoonCoreItemSummarySchema(title="Greatest Hits", reference="S:80bf1"),
        )

        self.assertIn("FAILED", result.result)
        combined = "; ".join(e.error for e in result.errors)
        # Both candidate refs for the title should appear
        self.assertIn("S:aaa11", combined)
        self.assertIn("S:bbb22", combined)
        # Plus the submitted ref's actual title
        self.assertIn("Let It Be", combined)
        # And a hint that there are multiple
        self.assertIn("multiple", combined.lower())

    def test_mismatch_skips_roon_calls_entirely(self):
        """Mismatch is detected from the result store before any Roon
        call — neither get_media_actions nor browse_core should fire
        when titles disagree. Saves a round-trip on the unhappy path
        and avoids any side-effects on the Roon session."""
        store = {
            "res_00001": _store_group([
                {"title": "Let It Be", "reference": "S:80bf1"},
            ]),
        }
        tool, fake = _make_tool({"80bf1": "Let It Be"}, store)

        _run_shuffle(
            tool,
            RoonCoreItemSummarySchema(title="Abbey Road", reference="S:80bf1"),
        )

        self.assertEqual(fake.get_media_actions_calls, [])
        self.assertEqual(fake.browse_calls, [])


class TestReferenceNotInResultStore(unittest.TestCase):
    """If the (valid) reference isn't in the result store at all, we
    can't compare titles, so the check is skipped and the action
    proceeds. This preserves the current behaviour for any path where
    the result store doesn't have the item — e.g. the store has been
    cleared, or the reference came from outside a search context."""

    def test_reference_not_in_store_proceeds_without_check(self):
        store = {
            "res_00001": _store_group([
                {"title": "Some Other Album", "reference": "S:99999"},
            ]),
        }
        tool, _fake = _make_tool({"80bf1": "Abbey Road"}, store)

        result = _run_shuffle(
            tool,
            RoonCoreItemSummarySchema(title="Abbey Road", reference="S:80bf1"),
        )

        self.assertIn("SUCCESSFUL", result.result)
        self.assertIsNone(result.error)

    def test_empty_result_store_proceeds_without_check(self):
        tool, _fake = _make_tool({"80bf1": "Abbey Road"}, {})

        result = _run_shuffle(
            tool,
            RoonCoreItemSummarySchema(title="Abbey Road", reference="S:80bf1"),
        )

        self.assertIn("SUCCESSFUL", result.result)


class TestBatchPartialSuccess(unittest.TestCase):
    """In a multi-item batch, a single mismatched item should produce
    a per-item error while the others proceed successfully — matching
    the existing per-item-error reporting pattern."""

    def test_one_mismatch_among_several_succeeds_for_others(self):
        """Queue action with three items, one of which has a
        title/reference mismatch — the two correct ones should
        proceed and the mismatch surfaces as a per-item error.
        Uses Queue rather than Shuffle to avoid the multi-item-
        Shuffle track-expansion path (irrelevant to this test)."""
        store = {
            "res_00001": _store_group([
                {"title": "Abbey Road", "reference": "S:80bf1"},
                # Stored title is "Let It Be" — LLM later submits this
                # ref against "Abbey Road" which is the mismatch.
                {"title": "Let It Be", "reference": "S:99999"},
                {"title": "Help!", "reference": "S:11111"},
            ]),
        }
        tool, _fake = _make_tool(
            {
                "80bf1": "Abbey Road",
                "99999": "Let It Be",
                "11111": "Help!",
            },
            store,
        )
        params = RoonActionToolInputSchema(
            action="Queue",
            items=[
                RoonCoreItemSummarySchema(title="Abbey Road", reference="S:80bf1"),
                RoonCoreItemSummarySchema(title="Abbey Road", reference="S:99999"),
                RoonCoreItemSummarySchema(title="Help!", reference="S:11111"),
            ],
        )
        output = asyncio.run(tool.run_async(params))

        # 2/3 succeed; the mismatch surfaces as a per-item error.
        # Title/reference mismatch routes to the "other" structured-
        # error bucket (refs=[], error text carries the detail).
        self.assertIn("PARTIAL SUCCESS", output.result)
        self.assertIn("2/3", output.result)
        self.assertIsNotNone(output.errors)
        combined = "; ".join(e.error for e in output.errors)
        # The mismatched item: submitted ref + both interpretations
        self.assertIn("S:99999", combined)
        self.assertIn("Abbey Road", combined)
        self.assertIn("Let It Be", combined)
        # The richer error also includes S:80bf1 as the alternate ref
        # for "Abbey Road" — this is the whole point: the LLM can
        # pick the right pair from the message alone.
        self.assertIn("S:80bf1", combined)
        # The third item ("Help!", S:11111) succeeded, so it shouldn't
        # appear anywhere in the errors
        self.assertNotIn("Help!", combined)
        self.assertNotIn("S:11111", combined)


class TestShuffleAllOrNothingOnMismatch(unittest.TestCase):
    """Shuffle treats its inputs as a single combined pool — the
    randomised selection only makes sense when every input
    contributes. If any input is rejected by the title/reference
    check, no Roon dispatch happens at all: a partial pool would
    produce an unrepresentative sample, and the coordinator's
    retry would Play Now the replacement over the top, wasting
    dispatches and chopping audio. Distinct from Queue/Play Now,
    where each item is independent and partial success is fine
    (see ``TestBatchPartialSuccess``).
    """

    def test_one_mismatch_blocks_dispatch_for_all(self):
        store = {
            "res_00001": _store_group([
                {"title": "Abbey Road", "reference": "S:80bf1"},
                # Stored title is "Let It Be" — LLM submits this ref
                # against "Killing Machine" so the check rejects it.
                {"title": "Let It Be", "reference": "S:99999"},
                {"title": "Help!", "reference": "S:11111"},
            ]),
        }
        tool, fake = _make_tool(
            {
                "80bf1": "Abbey Road",
                "99999": "Let It Be",
                "11111": "Help!",
            },
            store,
        )
        params = RoonActionToolInputSchema(
            action="Shuffle",
            items=[
                RoonCoreItemSummarySchema(title="Abbey Road", reference="S:80bf1"),
                RoonCoreItemSummarySchema(title="Killing Machine", reference="S:99999"),
                RoonCoreItemSummarySchema(title="Help!", reference="S:11111"),
            ],
            count=10,
        )
        output = asyncio.run(tool.run_async(params))

        self.assertIn("FAILED", output.result)
        self.assertEqual(
            fake.dispatched_actions, [],
            "Mismatched input must block all dispatches, not just its own",
        )
        self.assertIsNotNone(output.errors)
        combined = "; ".join(e.error for e in output.errors)
        self.assertIn("S:99999", combined)
        self.assertIn("Killing Machine", combined)


if __name__ == "__main__":
    unittest.main()
