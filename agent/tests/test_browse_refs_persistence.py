"""Browse-session persistence: the ref pool survives a restart (manifest
group B), so "act on what I just found" still resolves directly.

The pool is restored as-is. The headline test is the one that catches a
forgotten ``_session_depth``: a restored ref must resolve on the *fast
path* (its cached item_key) without falling back to the semantic rewalk
(a re-search). The fake's ``browse_core`` records every call, so a rewalk
shows up as a ``pop_all`` + ``input`` search; the fast path makes none.

The round-trip goes through ``RuntimeState.attach_roon_persistence`` (the
production path), so it does not depend on how the Roon-side state is
partitioned into participants.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.io.state_db import StateDb  # noqa: E402
from app.runtime.persistence import PersistenceManager  # noqa: E402
from app.runtime.state import RuntimeState  # noqa: E402
from roon_core.schemas import (  # noqa: E402
    RoonCoreItemSchema,
    RoonCoreListSchema,
    RoonCoreResultsSchema,
)
from tests._browse_fake import BrowseFake  # noqa: E402


class TestBrowseRefsPersistence(unittest.TestCase):

    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="swarpius-test-db-"))
        self.db = StateDb(self._dir / "state.db")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def _save(self, fake: BrowseFake) -> None:
        runtime = RuntimeState()
        runtime.roon_connection = fake
        manager = PersistenceManager(self.db)
        runtime.attach_roon_persistence(manager)
        manager.commit()

    def _restore_into(self, fake: BrowseFake) -> None:
        runtime = RuntimeState()
        runtime.roon_connection = fake
        manager = PersistenceManager(self.db)
        runtime.attach_roon_persistence(manager)

    def test_restored_ref_resolves_on_fast_path_without_rewalk(self):
        original = BrowseFake()
        original.register_track("t1", "Blue Train")
        self._save(original)

        restored = BrowseFake()  # fresh process: a different session prefix
        self._restore_into(restored)

        restored.browse_aux_calls.clear()
        ref = restored.resolve_reference("S:t1")
        self.assertIsNotNone(ref, "restored ref should resolve")
        self.assertEqual(ref.cached_item_key, "item-t1")
        searches = [
            aux for aux in restored.browse_aux_calls
            if aux.get("pop_all") and "input" in aux
        ]
        self.assertEqual(
            searches, [],
            "restored ref resolved via a rewalk (re-search) instead of the fast path",
        )

    def test_refs_survive_with_intact_identity_and_keys(self):
        original = BrowseFake()
        original.register_album("a1", "Kind of Blue", ["So What"])
        self._save(original)

        restored = BrowseFake()
        self._restore_into(restored)

        ref = restored.session_manager.get_ref("a1")
        self.assertIsNotNone(ref)
        self.assertEqual(ref.identity.title, "Kind of Blue")
        self.assertEqual(ref.cached_item_key, "item-a1")
        self.assertTrue(restored.session_manager.is_key_live(ref))

    def test_new_sessions_continue_namespace_after_restart(self):
        original = BrowseFake()
        original.session_manager.new_search_session()
        self._save(original)
        # What the original would mint next.
        expected_next = original.session_manager.new_search_session()

        restored = BrowseFake()
        self._restore_into(restored)
        self.assertEqual(restored.session_manager.new_search_session(), expected_next)

    def test_pagination_current_list_survives_restart(self):
        original = BrowseFake()
        session_key = original.session_manager.new_search_session()
        original.session_manager.set_current_list(
            session_key,
            RoonCoreResultsSchema(
                items=[
                    RoonCoreItemSchema(title="So What", item_key="k1", hint="action_list"),
                ],
                list=RoonCoreListSchema(count=1, hint="list", title="Kind of Blue"),
            ),
        )
        self._save(original)

        restored = BrowseFake()
        self._restore_into(restored)
        current = restored.session_manager.get_current_list(session_key)
        self.assertIsNotNone(current)
        self.assertEqual([item.title for item in current.items], ["So What"])


if __name__ == "__main__":
    unittest.main()
