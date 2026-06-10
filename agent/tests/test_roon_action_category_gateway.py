"""Pre-flight rejection of category-gateway references in roon_action.

A category gateway (e.g. ``Tracks | 87 Results`` from a search response)
is a navigation entry — it lists matching items when drilled, but isn't
playable itself. The dispatcher rejects it before any Roon call so the
coordinator gets a clear ``"drill into categories"`` error rather than
silently shuffling 87 tracks (Shuffle path) or producing a generic
"actions not found" error (other verbs).

Fingerprint at the result-store layer: ``title`` is a single capitalised
word ending in 's'; ``extra_info`` matches ``^\\d+ Results?$``. Both
must match — single-field matches don't trigger.
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

_GATEWAY_CATEGORIES = [
    ("Tracks", "87 Results"),
    ("Albums", "48 Results"),
    ("Artists", "57 Results"),
    ("Composers", "23 Results"),
    ("Works", "20 Results"),
    ("Playlists", "12 Results"),
]

_SINGLE_ITEM_VERBS = ("Add Next", "Start Radio")
_MULTI_ITEM_VERBS = ("Play Now", "Queue", "Shuffle")


def _store_group(items: List[dict]) -> List[dict]:
    return [{"group": "-", "items": items}]


def _store_with_gateway(title: str, extra_info: str, reference: str) -> Dict[str, Any]:
    return {
        "res_00001": _store_group([
            {
                "title": title,
                "group": "-",
                "extra_info": extra_info,
                "reference": reference,
                "intended_category": None,
            },
        ]),
    }


def _make_tool(result_store: Dict[str, Any]) -> tuple[RoonActionTool, BrowseFake]:
    fake = BrowseFake()
    tool = make_action_tool(fake, result_store=result_store)
    return tool, fake


def _run(tool: RoonActionTool, action: str, item: RoonCoreItemSummarySchema):
    params = RoonActionToolInputSchema(action=action, items=[item])
    return asyncio.run(tool.run_async(params))


class TestCategoryGatewayRejected(unittest.TestCase):
    """A category-gateway reference produces a per-item rejection
    naming the ref and quoting the stored title, on every library
    verb. No Roon dispatch happens — the pre-flight catches before
    any browse_core call."""

    def test_every_gateway_every_verb_rejected(self):
        ref_id = "S:28c03"
        for category, extra_info in _GATEWAY_CATEGORIES:
            store = _store_with_gateway(category, extra_info, ref_id)
            stored_title_render = f"{category} | {extra_info}"
            for verb in _SINGLE_ITEM_VERBS + _MULTI_ITEM_VERBS:
                with self.subTest(category=category, verb=verb):
                    tool, fake = _make_tool(store)
                    output = _run(
                        tool, verb,
                        RoonCoreItemSummarySchema(
                            title=stored_title_render,
                            reference=ref_id,
                        ),
                    )

                    self.assertIn("FAILED", output.result)
                    self.assertEqual(
                        fake.browse_aux_calls, [],
                        f"Pre-flight must reject before any Roon call ({verb} on {category})",
                    )
                    self.assertIsNotNone(output.errors)
                    combined = "; ".join(e.error for e in output.errors)
                    self.assertIn(ref_id, combined)
                    self.assertIn(stored_title_render, combined)
                    self.assertIn("category listing", combined)
                    self.assertIn("Drill into categories", combined)


class TestStoredTitleQuotedInError(unittest.TestCase):
    """The error message quotes the *stored* title (rendered as
    ``<title> | <extra_info>``) rather than whatever title the
    coordinator submitted — so a coordinator who submitted the
    bare ``"Tracks"`` still sees ``"Tracks | 87 Results"`` and
    can correlate against the search response."""

    def test_submitted_title_differs_from_stored(self):
        store = _store_with_gateway("Tracks", "87 Results", "S:28c03")
        tool, _fake = _make_tool(store)
        output = _run(
            tool, "Shuffle",
            RoonCoreItemSummarySchema(title="Tracks", reference="S:28c03"),
        )

        self.assertIn("FAILED", output.result)
        combined = "; ".join(e.error for e in output.errors)
        self.assertIn("Tracks | 87 Results", combined)


class TestPreFlightOrderingBeforeMismatch(unittest.TestCase):
    """The gateway check fires before the title-mismatch check —
    so a gateway ref submitted with a mangled title still produces
    the gateway error (the more useful one), not the mismatch
    error."""

    def test_gateway_wins_over_mismatch(self):
        store = _store_with_gateway("Albums", "48 Results", "S:567a3")
        tool, _fake = _make_tool(store)
        output = _run(
            tool, "Play Now",
            RoonCoreItemSummarySchema(
                title="Greatest Hits",
                reference="S:567a3",
            ),
        )

        self.assertIn("FAILED", output.result)
        combined = "; ".join(e.error for e in output.errors)
        self.assertIn("category listing", combined)
        self.assertNotIn("Title/reference mismatch", combined)


class TestShuffleAllOrNothingOnGateway(unittest.TestCase):
    """Mirrors the title-mismatch all-or-nothing rule for Shuffle:
    a single gateway item in the input blocks the whole call rather
    than producing a partial pool. Distinct from Play Now / Queue,
    which tolerate per-item failures."""

    def test_one_gateway_blocks_shuffle_dispatch(self):
        store = {
            "res_00001": _store_group([
                {
                    "title": "Abbey Road", "group": "-",
                    "extra_info": "The Beatles", "reference": "S:80bf1",
                    "intended_category": None,
                },
                {
                    "title": "Tracks", "group": "-",
                    "extra_info": "87 Results", "reference": "S:28c03",
                    "intended_category": None,
                },
            ]),
        }
        tool, fake = _make_tool(store)
        fake.register_item("80bf1", "Abbey Road", action_titles=[
            "Play Now", "Add Next", "Queue", "Start Radio",
        ])
        params = RoonActionToolInputSchema(
            action="Shuffle",
            items=[
                RoonCoreItemSummarySchema(title="Abbey Road", reference="S:80bf1"),
                RoonCoreItemSummarySchema(
                    title="Tracks | 87 Results", reference="S:28c03",
                ),
            ],
        )
        output = asyncio.run(tool.run_async(params))

        self.assertIn("FAILED", output.result)
        self.assertEqual(
            fake.action_dispatches, [],
            "Gateway in a Shuffle input must block all dispatches",
        )
        combined = "; ".join(e.error for e in output.errors)
        self.assertIn("S:28c03", combined)
        self.assertIn("category listing", combined)


class TestQueueTolerantOfGateway(unittest.TestCase):
    """Queue / Play Now tolerate per-item failures — a gateway in
    the input rejects that item but the others dispatch normally."""

    def test_queue_dispatches_other_items_around_gateway(self):
        store = {
            "res_00001": _store_group([
                {
                    "title": "Abbey Road", "group": "-",
                    "extra_info": "The Beatles", "reference": "S:80bf1",
                    "intended_category": None,
                },
                {
                    "title": "Tracks", "group": "-",
                    "extra_info": "87 Results", "reference": "S:28c03",
                    "intended_category": None,
                },
                {
                    "title": "Help!", "group": "-",
                    "extra_info": "The Beatles", "reference": "S:11111",
                    "intended_category": None,
                },
            ]),
        }
        tool, fake = _make_tool(store)
        for ref_id, title in (("80bf1", "Abbey Road"), ("11111", "Help!")):
            fake.register_item(ref_id, title, action_titles=[
                "Play Now", "Add Next", "Queue", "Start Radio",
            ])

        params = RoonActionToolInputSchema(
            action="Queue",
            items=[
                RoonCoreItemSummarySchema(title="Abbey Road", reference="S:80bf1"),
                RoonCoreItemSummarySchema(
                    title="Tracks | 87 Results", reference="S:28c03",
                ),
                RoonCoreItemSummarySchema(title="Help!", reference="S:11111"),
            ],
        )
        output = asyncio.run(tool.run_async(params))

        self.assertIn("PARTIAL SUCCESS", output.result)
        self.assertIn("2/3", output.result)
        combined = "; ".join(e.error for e in output.errors)
        self.assertIn("S:28c03", combined)
        self.assertIn("category listing", combined)
        self.assertNotIn("S:80bf1", combined)
        self.assertNotIn("S:11111", combined)


class TestFalsePositives(unittest.TestCase):
    """Items that match one half of the fingerprint but not the
    other must NOT trigger the gateway rejection."""

    def test_title_matches_but_extra_info_does_not(self):
        """An album coincidentally titled 'Tracks' with a normal
        artist subtitle is a real playable item."""
        store = {
            "res_00001": _store_group([
                {
                    "title": "Tracks", "group": "-",
                    "extra_info": "The Drummers Of Burundi",
                    "reference": "S:80bf1",
                    "intended_category": None,
                },
            ]),
        }
        tool, fake = _make_tool(store)
        fake.register_item("80bf1", "Tracks", action_titles=[
            "Play Now", "Add Next", "Queue", "Start Radio",
        ])

        output = _run(
            tool, "Play Now",
            RoonCoreItemSummarySchema(title="Tracks", reference="S:80bf1"),
        )

        self.assertIn("SUCCESSFUL", output.result)

    def test_extra_info_matches_but_title_does_not(self):
        """An item whose extra_info coincidentally reads like '5 Results'
        but whose title isn't a category label is a real playable item."""
        store = {
            "res_00001": _store_group([
                {
                    "title": "Greatest Hits", "group": "-",
                    "extra_info": "5 Results",
                    "reference": "S:80bf1",
                    "intended_category": None,
                },
            ]),
        }
        tool, fake = _make_tool(store)
        fake.register_item("80bf1", "Greatest Hits", action_titles=[
            "Play Now", "Add Next", "Queue", "Start Radio",
        ])

        output = _run(
            tool, "Play Now",
            RoonCoreItemSummarySchema(title="Greatest Hits", reference="S:80bf1"),
        )

        self.assertIn("SUCCESSFUL", output.result)


class TestReferenceNotInResultStore(unittest.TestCase):
    """When the reference isn't in the store, there's no stored item
    to fingerprint against — the gateway check is skipped and the
    request proceeds to the existing reference-recovery / probe path.
    Mirrors the existing title-mismatch ``ReferenceNotInResultStore``
    contract."""

    def test_unknown_reference_proceeds_to_normal_path(self):
        tool, fake = _make_tool({})
        fake.register_item("80bf1", "Abbey Road", action_titles=[
            "Play Now", "Add Next", "Queue", "Start Radio",
        ])

        output = _run(
            tool, "Play Now",
            RoonCoreItemSummarySchema(title="Abbey Road", reference="S:80bf1"),
        )

        self.assertIn("SUCCESSFUL", output.result)


if __name__ == "__main__":
    unittest.main()
