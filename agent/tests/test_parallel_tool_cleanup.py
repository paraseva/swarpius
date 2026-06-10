"""Contract tests for the explicit-session_key browse API.

  C1: pop_levels is not a valid roon_search operation.
  C2: drill_down raises ValueError when session_key is None.
  C3: compile_output raises ValueError when session_key is None.
  C4: BrowseSessionManager has no active_session_key attribute.
"""

import unittest

from pydantic import ValidationError

from roon_core.browse_session import BrowseSessionManager, SearchRecipe
from roon_core.schemas import RoonCoreItemSchema
from tools.roon_search import RoonSearchToolInputSchema


class TestPopLevelsRemoved(unittest.TestCase):
    """C1: pop_levels must not be a valid operation."""

    def test_pop_levels_rejected_by_schema(self):
        with self.assertRaises(ValidationError):
            RoonSearchToolInputSchema(
                operation="pop_levels",
                pop_levels=1,
            )

    def test_valid_operations_still_accepted(self):
        # new_search
        schema = RoonSearchToolInputSchema(
            operation="new_search",
            search_string="Beatles",
        )
        self.assertEqual(schema.operation, "new_search")

        # drill_down_reference
        schema = RoonSearchToolInputSchema(
            operation="drill_down_reference",
            reference="abc12",
        )
        self.assertEqual(schema.operation, "drill_down_reference")


class TestDrillDownRequiresSessionKey(unittest.TestCase):
    """C2: drill_down raises ValueError when session_key is None."""

    def test_drill_down_raises_without_session_key(self):
        # We only need to test the session_key guard, not a full Roon
        # connection. Import the mixin and create a minimal instance.
        from roon_core.browse import RoonBrowseMixin

        class MinimalBrowse(RoonBrowseMixin):
            def __init__(self):
                self.session_manager = BrowseSessionManager()
                self.api = None
                self.current_list = None

        browse = MinimalBrowse()
        item = RoonCoreItemSchema(
            title="Test",
            item_key="ik-1",
        )
        with self.assertRaises(ValueError) as ctx:
            browse.drill_down(drilldown_item=item, session_key=None)
        self.assertIn("session_key", str(ctx.exception).lower())


class TestCompileOutputRequiresSessionKey(unittest.TestCase):
    """C3: compile_output raises ValueError when session_key is None."""

    def test_compile_output_raises_without_session_key(self):
        from roon_core.browse import RoonBrowseMixin
        from roon_core.schemas import RoonCoreListSchema, RoonCoreResultsSchema

        class MinimalBrowse(RoonBrowseMixin):
            def __init__(self):
                self.session_manager = BrowseSessionManager()
                self.api = None
                self.current_list = RoonCoreResultsSchema(
                    items=[RoonCoreItemSchema(title="Test", item_key="ik-1")],
                    list=RoonCoreListSchema(count=1, title="Results"),
                )

        browse = MinimalBrowse()
        with self.assertRaises(ValueError) as ctx:
            browse.compile_output(
                recipe=SearchRecipe(search_string="test"),
                session_key=None,
            )
        self.assertIn("session_key", str(ctx.exception).lower())


class TestActiveSessionKeyRemoved(unittest.TestCase):
    """C4: BrowseSessionManager must not expose active_session_key."""

    def test_no_active_session_key_attribute(self):
        mgr = BrowseSessionManager()
        self.assertFalse(
            hasattr(mgr, "active_session_key"),
            "BrowseSessionManager should not have active_session_key",
        )

    def test_no_active_session_key_after_new_session(self):
        mgr = BrowseSessionManager()
        mgr.new_search_session()
        self.assertFalse(
            hasattr(mgr, "active_session_key"),
            "active_session_key should not exist even after creating a session",
        )

    def test_action_and_recovery_keys_still_exist(self):
        """Removing active_session_key must not affect the fixed keys."""
        mgr = BrowseSessionManager()
        self.assertEqual(mgr.action_session_key, "action")
        self.assertEqual(mgr.recovery_session_key, "recovery")


if __name__ == "__main__":
    unittest.main()
