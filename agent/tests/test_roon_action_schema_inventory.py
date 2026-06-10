"""Inventory of legal (action, params) combinations for
RoonActionToolInputSchema, plus the known rejection rules — pins
every case behind a single list so regressions are easy to spot.
"""

import unittest

from roon_core.schemas import RoonCoreItemSummarySchema
from tools.roon_action import RoonActionToolInputSchema


def _item() -> RoonCoreItemSummarySchema:
    return RoonCoreItemSummarySchema(
        title="Track",
        reference="abc12",
        extra_info="Artist",
        group="-",
    )


# Every action in the AllActions Literal, with the minimal params needed
# to pass the current validator. The table is the inventory — adding or
# removing an action here is the only place the test expects changes.
LEGAL_CONSTRUCTIONS: list[tuple[str, dict]] = [
    # Library actions — need items; zone optional
    ("Play Now",      {"items": [_item()], "zone": "Kitchen"}),
    ("Add Next",      {"items": [_item()], "zone": "Kitchen"}),
    ("Queue",         {"items": [_item()], "zone": "Kitchen"}),
    ("Shuffle",       {"items": [_item()], "zone": "Kitchen"}),
    ("Start Radio",   {"items": [_item()], "zone": "Kitchen"}),
    # Transport actions — zone only
    ("play",          {"zone": "Kitchen"}),
    ("pause",         {"zone": "Kitchen"}),
    ("resume",        {"zone": "Kitchen"}),
    ("stop",          {"zone": "Kitchen"}),
    ("next",          {"zone": "Kitchen"}),
    ("previous",      {"zone": "Kitchen"}),
    # Playback settings — each needs its specific param
    ("set_shuffle",   {"shuffle": True, "zone": "Kitchen"}),
    ("set_repeat",    {"repeat": "loop", "zone": "Kitchen"}),
    ("seek",          {"seconds": 30, "zone": "Kitchen"}),
    ("set_auto_radio", {"auto_radio": True, "zone": "Kitchen"}),
    # Advanced controls — need zone or output; volume/delta per action
    ("get_volume",    {"zone": "Kitchen"}),
    ("set_volume",    {"volume": 50, "zone": "Kitchen"}),
    ("change_volume", {"delta": -5, "zone": "Kitchen"}),
    ("mute",          {"zone": "Kitchen"}),
    ("unmute",        {"zone": "Kitchen"}),
    ("standby",       {"zone": "Kitchen"}),
    ("convenience_switch", {"zone": "Kitchen"}),
    # Group-wide — neither zone nor output required
    ("pause_all",     {}),
    ("mute_all",      {}),
    ("unmute_all",    {}),
    # Queue controls
    ("play_from_here", {"queue_item_id": 42, "zone": "Kitchen"}),
]


# Known illegal combinations the current validator rejects. Tightens the
# contract so the refactor is forced to preserve each rejection — or to
# consciously relax one.
ILLEGAL_CONSTRUCTIONS: list[tuple[str, dict, str]] = [
    # (action, kwargs, substring of expected error message)
    ("Play Now",      {"zone": "Kitchen"},
     "'items' must be provided"),
    ("Queue",         {},
     "'items' must be provided"),
    ("Add Next",      {"items": [_item(), _item()], "zone": "Kitchen"},
     "Add Next only accepts a single item"),
    ("Start Radio",   {"items": [_item(), _item()], "zone": "Kitchen"},
     "Start Radio only accepts a single item"),
    ("set_shuffle",   {"zone": "Kitchen"},
     "shuffle must be provided"),
    ("set_repeat",    {"zone": "Kitchen"},
     "repeat must be provided"),
    ("seek",          {"zone": "Kitchen"},
     "seconds must be provided"),
    ("set_auto_radio", {"zone": "Kitchen"},
     "auto_radio must be provided"),
    ("set_volume",    {"zone": "Kitchen"},
     "volume must be provided"),
    ("change_volume", {"zone": "Kitchen"},
     "delta must be provided"),
    ("play_from_here", {"zone": "Kitchen"},
     "queue_item_id or queue_ref must be provided"),
    # Advanced actions needing zone-or-output
    ("get_volume",    {},
     "zone or output must be provided"),
    ("set_volume",    {"volume": 50},
     "zone or output must be provided"),
    ("mute",          {},
     "zone or output must be provided"),
    ("unmute",        {},
     "zone or output must be provided"),
    ("standby",       {},
     "zone or output must be provided"),
    ("convenience_switch", {},
     "zone or output must be provided"),
]


class TestLegalConstructions(unittest.TestCase):
    """Every action listed in AllActions must have a minimal legal
    construction. Adding a new action requires adding it to
    LEGAL_CONSTRUCTIONS — this test flags the omission."""

    def test_every_action_constructs_cleanly(self):
        for action, kwargs in LEGAL_CONSTRUCTIONS:
            with self.subTest(action=action):
                schema = RoonActionToolInputSchema(action=action, **kwargs)
                self.assertEqual(schema.action, action)

    def test_inventory_covers_every_action_literal(self):
        """The inventory's action set must equal the set declared in
        AllActions — catches refactors that add or drop enum values."""
        from typing import get_args

        from tools.roon_action import AllActions

        declared = set(get_args(AllActions))
        covered = {action for action, _ in LEGAL_CONSTRUCTIONS}
        missing = declared - covered
        extra = covered - declared
        self.assertFalse(
            missing,
            f"Actions in AllActions but not in LEGAL_CONSTRUCTIONS: {missing}",
        )
        self.assertFalse(
            extra,
            f"Actions in LEGAL_CONSTRUCTIONS but not in AllActions: {extra}",
        )


class TestIllegalConstructions(unittest.TestCase):
    """Every illegal (action, params) combination is rejected. The
    test accepts either ``ValueError`` or
    ``pydantic.ValidationError`` since either is a legitimate
    rejection mechanism."""

    def test_every_known_illegal_is_rejected(self):
        from pydantic import ValidationError
        for action, kwargs, expected_snippet in ILLEGAL_CONSTRUCTIONS:
            with self.subTest(action=action, kwargs=kwargs):
                with self.assertRaises((ValueError, ValidationError)) as ctx:
                    RoonActionToolInputSchema(action=action, **kwargs)
                # Check the error message contains the expected hint —
                # survives across refactors unless the message itself is
                # intentionally rewritten.
                self.assertIn(
                    expected_snippet, str(ctx.exception),
                    f"Expected '{expected_snippet}' in error for {action} {kwargs}",
                )


if __name__ == "__main__":
    unittest.main()
