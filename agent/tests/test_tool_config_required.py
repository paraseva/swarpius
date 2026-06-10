"""Contract: ``resolve_zone`` (and other config dependencies a tool
genuinely needs) must be injected at construction.

If a caller forgets, construction fails loudly rather than the tool
silently falling through to zone-name-as-literal-string, which would
mask the operator bug.
"""

import unittest

from tools.roon_action import RoonActionTool, RoonActionToolConfig
from tools.roon_status import RoonStatusTool, RoonStatusToolConfig


class TestRoonActionResolveZoneRequired(unittest.TestCase):
    def test_construction_without_resolve_zone_fails(self):
        """Constructing RoonActionTool with a config that omits
        resolve_zone should fail at the validation / construction
        boundary, not at first-use with silent fallback."""
        with self.assertRaises((ValueError, TypeError)):
            RoonActionToolConfig(roon_connection=object())  # no resolve_zone

    def test_construction_with_resolve_zone_succeeds(self):
        """Explicit injection is the supported path."""
        def resolve(name: str) -> str:
            return name.title()

        config = RoonActionToolConfig(
            roon_connection=object(),
            resolve_zone=resolve,
        )
        tool = RoonActionTool(config)
        # Positive pin: when zone is given, the callback is used
        self.assertEqual(tool._resolve("kitchen"), "Kitchen")


class TestRoonStatusResolveZoneRequired(unittest.TestCase):
    def test_construction_without_resolve_zone_fails(self):
        with self.assertRaises((ValueError, TypeError)):
            RoonStatusToolConfig(roon_connection=object())  # no resolve_zone

    def test_construction_with_resolve_zone_succeeds(self):
        def resolve(name: str) -> str:
            return name.title()

        config = RoonStatusToolConfig(
            roon_connection=object(),
            resolve_zone=resolve,
        )
        tool = RoonStatusTool(config)
        # Positive pin
        self.assertEqual(tool._resolve("kitchen"), "Kitchen")


if __name__ == "__main__":
    unittest.main()
