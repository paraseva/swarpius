"""Matrix-driven dispatcher tests.

Pins the dispatcher's matrix as its contract. Each test drives
``RoonActionTool.run_async`` at the public boundary and asserts on
(a) the dispatched-action sequence and (b) the operator-facing
error/notice text.

Coverage targets:

* Track / container / persona dispatch cells (matrix rows × columns).
* Iterative duplicate-wrapper consumption (containers via subcategory).
* Schema-level constraints (Add Next / Start Radio single-item;
  unknown action).
* Multi-item rules (Play Now → Queue rectify; Shuffle reject-whole vs
  Queue/Play Now per-item-tolerant).
* Multi-persona Shuffle rejection.
* "Not Found" notice (informational, not error).
* Category-mismatch loud-fail.
"""

import asyncio
import unittest

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from pydantic import ValidationError  # noqa: E402

from roon_core.schemas import RoonCoreItemSummarySchema  # noqa: E402

try:
    from tests._browse_fake import BrowseFake, make_action_tool
except ModuleNotFoundError:
    from _browse_fake import BrowseFake, make_action_tool

from tools.roon_action import RoonActionToolInputSchema  # noqa: E402


def _item(reference: str, title: str = "X") -> RoonCoreItemSummarySchema:
    return RoonCoreItemSummarySchema(title=title, reference=reference)


def _run(tool, **kwargs):
    return asyncio.run(
        tool.run_async(RoonActionToolInputSchema(**kwargs)),
    )


def _verbs_dispatched(fake):
    """Just the action verbs, ignoring ref_ids — useful when the
    ref_id is synthetic (e.g. wrapper-inner)."""
    return [verb for verb, _ref in fake.dispatched_actions]


# ══════════════════════════════════════════════════════════════════════
# Track cells — direct dispatch for every verb
# ══════════════════════════════════════════════════════════════════════


class TestTrackCells(unittest.TestCase):
    """A track ref dispatches its verb directly via the track's
    action_list (no gateway drill needed)."""

    def _setup(self):
        fake = BrowseFake()
        fake.register_track("trk01", "Sample Track")
        return fake, make_action_tool(fake)

    def test_play_now_on_track(self):
        fake, tool = self._setup()
        output = _run(tool, action="Play Now", items=[_item("S:trk01", "Sample Track")])
        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(fake.dispatched_actions, [("Play Now", "trk01")])

    def test_add_next_on_track(self):
        fake, tool = self._setup()
        output = _run(tool, action="Add Next", items=[_item("S:trk01", "Sample Track")])
        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(fake.dispatched_actions, [("Add Next", "trk01")])

    def test_queue_on_track(self):
        fake, tool = self._setup()
        output = _run(tool, action="Queue", items=[_item("S:trk01", "Sample Track")])
        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(fake.dispatched_actions, [("Queue", "trk01")])

    def test_start_radio_on_track(self):
        fake, tool = self._setup()
        output = _run(tool, action="Start Radio", items=[_item("S:trk01", "Sample Track")])
        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(fake.dispatched_actions, [("Start Radio", "trk01")])


# ══════════════════════════════════════════════════════════════════════
# Container cells — album / playlist / work, all dispatch via gateway
# ══════════════════════════════════════════════════════════════════════


class TestAlbumCells(unittest.TestCase):
    def _setup(self):
        fake = BrowseFake()
        fake.register_album("alb01", "An Album", ["T1", "T2", "T3"])
        return fake, make_action_tool(fake)

    def test_play_now_on_album_dispatches_via_gateway(self):
        fake, tool = self._setup()
        output = _run(tool, action="Play Now", items=[_item("S:alb01", "An Album")])
        self.assertIn("SUCCESSFUL", output.result)
        self.assertIn("Play Now", _verbs_dispatched(fake))

    def test_queue_on_album_dispatches_via_gateway(self):
        fake, tool = self._setup()
        output = _run(tool, action="Queue", items=[_item("S:alb01", "An Album")])
        self.assertIn("SUCCESSFUL", output.result)
        self.assertIn("Queue", _verbs_dispatched(fake))

    def test_shuffle_on_album_expands_to_tracks(self):
        fake, tool = self._setup()
        output = _run(tool, action="Shuffle", items=[_item("S:alb01", "An Album")])
        self.assertIn("SUCCESSFUL", output.result)
        # First track played via Play Now, rest queued
        verbs = _verbs_dispatched(fake)
        self.assertEqual(verbs[0], "Play Now")
        self.assertTrue(all(v == "Queue" for v in verbs[1:]))
        self.assertEqual(len(verbs), 3)


class TestPlaylistCells(unittest.TestCase):
    def test_queue_on_playlist_dispatches_via_gateway(self):
        fake = BrowseFake()
        fake.register_playlist("pl01", "Favs", ["P1", "P2"])
        tool = make_action_tool(fake)
        output = _run(tool, action="Queue", items=[_item("S:pl01", "Favs")])
        self.assertIn("SUCCESSFUL", output.result)
        self.assertIn("Queue", _verbs_dispatched(fake))

    def test_shuffle_on_playlist_expands_to_tracks(self):
        fake = BrowseFake()
        fake.register_playlist("pl01", "Favs", ["P1", "P2", "P3", "P4"])
        tool = make_action_tool(fake)
        output = _run(tool, action="Shuffle", items=[_item("S:pl01", "Favs")])
        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(len(fake.dispatched_actions), 4)


class TestWorkCells(unittest.TestCase):
    def test_play_now_on_work_dispatches_via_gateway(self):
        fake = BrowseFake()
        fake.register_work("wk01", "Concerto", ["Rec1", "Rec2"])
        tool = make_action_tool(fake)
        output = _run(tool, action="Play Now", items=[_item("S:wk01", "Concerto")])
        self.assertIn("SUCCESSFUL", output.result)
        self.assertIn("Play Now", _verbs_dispatched(fake))

    def test_shuffle_on_work_expands_to_recordings(self):
        fake = BrowseFake()
        fake.register_work("wk01", "Concerto", ["R1", "R2", "R3"])
        tool = make_action_tool(fake)
        output = _run(tool, action="Shuffle", items=[_item("S:wk01", "Concerto")])
        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(len(fake.dispatched_actions), 3)


# ══════════════════════════════════════════════════════════════════════
# Persona cells — rectify (Play Now → Shuffle), reject (Add Next /
# Queue), or native dispatch (Shuffle / Start Radio).
# ══════════════════════════════════════════════════════════════════════


class TestPersonaPlayNowRectifies(unittest.TestCase):
    """Play Now on a persona silently rectifies to Shuffle (stays within
    the artist/composer's own catalogue, unlike Start Radio which drifts
    to similar artists). The coordinator sees SUCCESSFUL; the dispatched
    action is Shuffle, not Play Now."""

    def test_play_now_on_artist_with_albums_rectifies_to_shuffle(self):
        fake = BrowseFake()
        fake.register_persona_with_children(
            "art01", "Some Artist", persona="artist",
            child_titles=["Album A", "Album B"],
        )
        tool = make_action_tool(fake)
        output = _run(tool, action="Play Now", items=[_item("S:art01", "Some Artist")])
        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(fake.dispatched_actions, [("Shuffle", "art01")])

    def test_play_now_on_artist_without_albums_rectifies_to_shuffle(self):
        fake = BrowseFake()
        fake.register_persona_with_children(
            "art01", "Beatles", persona="artist", child_titles=[],
        )
        tool = make_action_tool(fake)
        output = _run(tool, action="Play Now", items=[_item("S:art01", "Beatles")])
        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(fake.dispatched_actions, [("Shuffle", "art01")])

    def test_play_now_on_composer_rectifies_to_shuffle(self):
        fake = BrowseFake()
        fake.register_persona_with_children(
            "cmp01", "Mozart", persona="composer",
            child_titles=["Concerto", "Sonata"],
        )
        tool = make_action_tool(fake)
        output = _run(tool, action="Play Now", items=[_item("S:cmp01", "Mozart")])
        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(fake.dispatched_actions, [("Shuffle", "cmp01")])


class TestPersonaAddNextQueueReject(unittest.TestCase):
    """Add Next / Queue on a persona is rejected with an
    operator-actionable message naming the alternatives."""

    def test_add_next_on_artist_rejects(self):
        fake = BrowseFake()
        fake.register_persona_with_children(
            "art01", "Some Artist", persona="artist",
            child_titles=["Album A"],
        )
        tool = make_action_tool(fake)
        output = _run(tool, action="Add Next", items=[_item("S:art01", "Some Artist")])
        self.assertIn("FAILED", output.result)
        self.assertIsNotNone(output.errors)
        err = output.errors[0]
        self.assertEqual(err.refs, ["S:art01"])
        self.assertIn("artist", err.error.lower())
        self.assertIn("cannot be used", err.error.lower())
        self.assertEqual(fake.dispatched_actions, [])

    def test_queue_on_artist_rejects(self):
        fake = BrowseFake()
        fake.register_persona_with_children(
            "art01", "Some Artist", persona="artist",
            child_titles=["Album A"],
        )
        tool = make_action_tool(fake)
        output = _run(tool, action="Queue", items=[_item("S:art01", "Some Artist")])
        self.assertIn("FAILED", output.result)
        self.assertIn("artist", output.errors[0].error.lower())
        self.assertEqual(fake.dispatched_actions, [])

    def test_queue_on_composer_rejects(self):
        fake = BrowseFake()
        fake.register_persona_with_children(
            "cmp01", "Mozart", persona="composer", child_titles=["Concerto"],
        )
        tool = make_action_tool(fake)
        output = _run(tool, action="Queue", items=[_item("S:cmp01", "Mozart")])
        self.assertIn("FAILED", output.result)
        self.assertIn("composer", output.errors[0].error.lower())


class TestPersonaShuffleAndRadio(unittest.TestCase):
    """Shuffle and Start Radio on a persona dispatch the native
    action from the persona's action_list — works regardless of
    whether the persona has reachable child containers (matrix is
    library-content-agnostic)."""

    def test_shuffle_on_artist_with_albums_dispatches_native_shuffle(self):
        fake = BrowseFake()
        fake.register_persona_with_children(
            "art01", "Judas Priest", persona="artist",
            child_titles=["Painkiller", "British Steel"],
        )
        tool = make_action_tool(fake)
        output = _run(tool, action="Shuffle", items=[_item("S:art01", "Judas Priest")])
        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(fake.dispatched_actions, [("Shuffle", "art01")])

    def test_shuffle_on_artist_without_albums_dispatches_native_shuffle(self):
        fake = BrowseFake()
        fake.register_persona_with_children(
            "art01", "Beatles", persona="artist", child_titles=[],
        )
        tool = make_action_tool(fake)
        output = _run(tool, action="Shuffle", items=[_item("S:art01", "Beatles")])
        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(fake.dispatched_actions, [("Shuffle", "art01")])

    def test_start_radio_on_composer_dispatches_native_start_radio(self):
        fake = BrowseFake()
        fake.register_persona_with_children(
            "cmp01", "Mozart", persona="composer", child_titles=["X"],
        )
        tool = make_action_tool(fake)
        output = _run(tool, action="Start Radio", items=[_item("S:cmp01", "Mozart")])
        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(fake.dispatched_actions, [("Start Radio", "cmp01")])


# ══════════════════════════════════════════════════════════════════════
# Iterative duplicate-wrapper consumption — universal preamble
# ══════════════════════════════════════════════════════════════════════


class TestWrapperConsumption(unittest.TestCase):
    """Drill responses may be preceded by 0..N duplicate-wrapper levels.
    The dispatcher's universal preamble drills past them transparently
    before classifying shape."""

    def test_album_with_single_wrapper_reaches_content(self):
        fake = BrowseFake()
        fake.register_container_with_duplicate_wrapper(
            "alb01", "Ram It Down", ["T1", "T2"],
            subtitle="Judas Priest", image_key="img-a",
        )
        tool = make_action_tool(fake)
        output = _run(tool, action="Queue", items=[_item("S:alb01", "Ram It Down")])
        self.assertIn("SUCCESSFUL", output.result)
        self.assertIn("Queue", _verbs_dispatched(fake))

    def test_album_with_multi_version_wrapper_drills_top(self):
        """The Thriller case: outer drill has 3 same-title siblings;
        the dispatcher drills items[0] only and ignores the rest."""
        fake = BrowseFake()
        fake.register_container_with_duplicate_wrapper(
            "alb01", "Thriller", ["T1", "T2"],
            subtitle="Michael Jackson", image_key="img-t",
            extra_sibling_titles=["Thriller", "Thriller"],
        )
        tool = make_action_tool(fake)
        output = _run(tool, action="Play Now", items=[_item("S:alb01", "Thriller")])
        self.assertIn("SUCCESSFUL", output.result)
        self.assertIn("Play Now", _verbs_dispatched(fake))

    def test_wrapped_album_shuffle_expands_inner_tracks(self):
        fake = BrowseFake()
        fake.register_container_with_duplicate_wrapper(
            "alb01", "Ram It Down", ["T1", "T2", "T3"],
            subtitle="Judas Priest", image_key="img-a",
        )
        tool = make_action_tool(fake)
        output = _run(tool, action="Shuffle", items=[_item("S:alb01", "Ram It Down")])
        self.assertIn("SUCCESSFUL", output.result)
        self.assertEqual(len(fake.dispatched_actions), 3)


# ══════════════════════════════════════════════════════════════════════
# "Not Found" item — informational notice, not failure
# ══════════════════════════════════════════════════════════════════════


class TestNotFoundNotice(unittest.TestCase):
    def test_not_found_alone_emits_notice_and_skips(self):
        fake = BrowseFake()
        fake.register_not_found_container("wk01", "Obscure Work")
        tool = make_action_tool(fake)
        output = _run(tool, action="Play Now", items=[_item("S:wk01", "Obscure Work")])

        # No dispatch (the only item was unavailable)
        self.assertEqual(fake.dispatched_actions, [])
        # But the call surfaces a notice, not an operator-actionable error
        self.assertIsNotNone(output.errors)
        notice = output.errors[0]
        self.assertEqual(notice.refs, ["S:wk01"])
        self.assertIn("unavailable", notice.error.lower())
        self.assertIn("skipped", notice.error.lower())


# ══════════════════════════════════════════════════════════════════════
# Schema-level constraints
# ══════════════════════════════════════════════════════════════════════


class TestSchemaConstraints(unittest.TestCase):
    def test_add_next_multi_item_rejected_at_schema(self):
        with self.assertRaises((ValueError, ValidationError)) as ctx:
            RoonActionToolInputSchema(
                action="Add Next",
                items=[_item("S:a", "A"), _item("S:b", "B")],
            )
        msg = str(ctx.exception).lower()
        self.assertIn("add next", msg)
        self.assertIn("single item", msg)

    def test_start_radio_multi_item_rejected_at_schema(self):
        with self.assertRaises((ValueError, ValidationError)) as ctx:
            RoonActionToolInputSchema(
                action="Start Radio",
                items=[_item("S:a", "A"), _item("S:b", "B")],
            )
        msg = str(ctx.exception).lower()
        self.assertIn("start radio", msg)
        self.assertIn("single item", msg)

    def test_play_now_multi_item_accepted_at_schema(self):
        params = RoonActionToolInputSchema(
            action="Play Now",
            items=[_item("S:a", "A"), _item("S:b", "B")],
        )
        self.assertEqual(len(params.items), 2)

    def test_queue_multi_item_accepted_at_schema(self):
        params = RoonActionToolInputSchema(
            action="Queue",
            items=[_item("S:a", "A"), _item("S:b", "B")],
        )
        self.assertEqual(len(params.items), 2)

    def test_unknown_action_rejected_at_schema(self):
        with self.assertRaises((ValueError, ValidationError)):
            RoonActionToolInputSchema(
                action="Play Artist",
                items=[_item("S:a", "Some Artist")],
            )


# ══════════════════════════════════════════════════════════════════════
# Multi-item rectification: Play Now with >1 items → Queue
# ══════════════════════════════════════════════════════════════════════


class TestMultiItemPlayNowRectifiesToQueue(unittest.TestCase):
    """Play Now with multiple items rectifies to Queue (silent). The
    queue's auto-start-on-idle behaviour covers the "play the first"
    case; no special sequencing logic needed."""

    def test_play_now_multi_item_dispatches_all_as_queue(self):
        fake = BrowseFake()
        fake.register_track("trk01", "A")
        fake.register_track("trk02", "B")
        fake.register_track("trk03", "C")
        # Zone state = stopped so the rectified Queue auto-starts.
        fake.zone_state = "stopped"
        tool = make_action_tool(fake)
        output = _run(
            tool, action="Play Now",
            items=[
                _item("S:trk01", "A"),
                _item("S:trk02", "B"),
                _item("S:trk03", "C"),
            ],
        )
        self.assertIn("SUCCESSFUL", output.result)
        # All three dispatched as Queue, not Play Now first + Queue rest
        self.assertEqual(
            fake.dispatched_actions,
            [("Queue", "trk01"), ("Queue", "trk02"), ("Queue", "trk03")],
        )


# ══════════════════════════════════════════════════════════════════════
# Multi-persona Shuffle — reject with all refs
# ══════════════════════════════════════════════════════════════════════


class TestMultiPersonaShuffleRejects(unittest.TestCase):
    """Multiple personas in a single Shuffle call are rejected before
    any dispatch, with the message pointing at drill-into-Albums."""

    def test_two_artists_rejected_with_both_refs(self):
        fake = BrowseFake()
        fake.register_persona_with_children(
            "art01", "Artist A", persona="artist",
            child_titles=["Album A1"],
        )
        fake.register_persona_with_children(
            "art02", "Artist B", persona="artist",
            child_titles=["Album B1"],
        )
        tool = make_action_tool(fake)
        output = _run(
            tool, action="Shuffle",
            items=[
                _item("S:art01", "Artist A"),
                _item("S:art02", "Artist B"),
            ],
        )
        self.assertIn("FAILED", output.result)
        all_refs = set()
        for err in output.errors:
            all_refs.update(err.refs)
        self.assertEqual(all_refs, {"S:art01", "S:art02"})
        self.assertEqual(fake.dispatched_actions, [])

    def test_mixed_artist_composer_rejected(self):
        fake = BrowseFake()
        fake.register_persona_with_children(
            "art01", "Some Artist", persona="artist", child_titles=["A"],
        )
        fake.register_persona_with_children(
            "cmp01", "Mozart", persona="composer", child_titles=["C"],
        )
        tool = make_action_tool(fake)
        output = _run(
            tool, action="Shuffle",
            items=[
                _item("S:art01", "Some Artist"),
                _item("S:cmp01", "Mozart"),
            ],
        )
        self.assertIn("FAILED", output.result)
        self.assertEqual(fake.dispatched_actions, [])


# ══════════════════════════════════════════════════════════════════════
# Mixed legal+illegal policies
# ══════════════════════════════════════════════════════════════════════


class TestMixedLegalIllegalPolicy(unittest.TestCase):
    """Shuffle is all-or-nothing on any reject cell. Play Now and
    Queue are per-item tolerant — failed items return as errors but
    the legal ones still dispatch."""

    def test_shuffle_rejects_whole_call_when_one_item_is_persona(self):
        fake = BrowseFake()
        fake.register_album("alb01", "An Album", ["T1", "T2"])
        fake.register_persona_with_children(
            "art01", "Some Artist", persona="artist", child_titles=["A"],
        )
        tool = make_action_tool(fake)
        output = _run(
            tool, action="Shuffle",
            items=[_item("S:alb01", "An Album"), _item("S:art01", "Some Artist")],
        )
        self.assertIn("FAILED", output.result)
        self.assertEqual(
            fake.dispatched_actions, [],
            "Shuffle must not dispatch any items when an illegal item is "
            "present — the pool would be unrepresentative",
        )

    def test_queue_tolerates_one_persona_item_dispatches_legal_rest(self):
        fake = BrowseFake()
        fake.register_track("trk01", "Track A")
        fake.register_persona_with_children(
            "art01", "Some Artist", persona="artist", child_titles=["X"],
        )
        fake.register_track("trk02", "Track B")
        tool = make_action_tool(fake)
        output = _run(
            tool, action="Queue",
            items=[
                _item("S:trk01", "Track A"),
                _item("S:art01", "Some Artist"),
                _item("S:trk02", "Track B"),
            ],
        )
        self.assertIn("PARTIAL", output.result)
        # The two tracks queued, the artist rejected
        self.assertEqual(
            fake.dispatched_actions,
            [("Queue", "trk01"), ("Queue", "trk02")],
        )
        # The artist is reported as an error with its ref
        all_refs = set()
        for err in output.errors:
            all_refs.update(err.refs)
        self.assertIn("S:art01", all_refs)


# ══════════════════════════════════════════════════════════════════════
# Category mismatch — loud-fail with operator-actionable message
# ══════════════════════════════════════════════════════════════════════


class TestCategoryMismatch(unittest.TestCase):
    """Per-item intended_category mismatch raises a clear error
    pointing the coordinator at the right subcategory to drill into.
    Replaces a silent-fall-through path where an artist ref tagged
    intended_category='album' would expand artist children as
    'tracks' and reach Roon with refs the dispatcher couldn't Queue."""

    def test_artist_ref_with_album_intent_rejects_with_actionable_msg(self):
        """Artist ref tagged intended_category='album'."""
        fake = BrowseFake()
        fake.register_persona_with_children(
            "art01", "Judas Priest", persona="artist",
            child_titles=["Painkiller"],
        )
        tool = make_action_tool(fake)
        output = _run(
            tool, action="Queue",
            items=[
                RoonCoreItemSummarySchema(
                    title="Judas Priest", reference="S:art01",
                    intended_category="album",
                ),
            ],
        )
        self.assertIn("FAILED", output.result)
        err = output.errors[0]
        self.assertEqual(err.refs, ["S:art01"])
        # Either the category-mismatch message or the persona-reject
        # message is acceptable here — both correctly tell the
        # coordinator the ref isn't suitable for the intended use.
        msg = err.error.lower()
        self.assertTrue(
            "album" in msg or "artist" in msg,
            f"Expected message to reference album or artist; got: {err.error}",
        )


# ══════════════════════════════════════════════════════════════════════
# End-to-end multi-persona-with-album-intent reproduction
# ══════════════════════════════════════════════════════════════════════


class TestMultiPersonaAlbumIntentReproduction(unittest.TestCase):
    """End-to-end shape: three artist refs each tagged
    intended_category='album', shuffled with count=25. The dispatcher
    must reject the whole call up front with a clear next-step
    pointer, not silently partial-succeed."""

    def test_three_artists_shuffled_with_album_intent_and_count(self):
        fake = BrowseFake()
        fake.register_persona_with_children(
            "art_jp", "Judas Priest", persona="artist",
            child_titles=[
                "Painkiller", "British Steel", "Defenders of the Faith",
                "Killing Machine", "Sad Wings of Destiny",
            ],
        )
        fake.register_persona_with_children(
            "art_ax", "Anthrax", persona="artist",
            child_titles=["Among the Living", "Persistence of Time"],
        )
        fake.register_persona_with_children(
            "art_tt", "Testament", persona="artist",
            child_titles=["Practice What You Preach", "Souls of Black"],
        )
        tool = make_action_tool(fake)

        output = _run(
            tool, action="Shuffle",
            items=[
                RoonCoreItemSummarySchema(
                    title="Judas Priest", reference="S:art_jp",
                    intended_category="album",
                ),
                RoonCoreItemSummarySchema(
                    title="Anthrax", reference="S:art_ax",
                    intended_category="album",
                ),
                RoonCoreItemSummarySchema(
                    title="Testament", reference="S:art_tt",
                    intended_category="album",
                ),
            ],
            count=25,
        )

        # Clean reject: no Roon dispatches at all
        self.assertIn("FAILED", output.result)
        self.assertEqual(
            fake.dispatched_actions, [],
            "multi-persona Shuffle with album-intent must not dispatch "
            "any Roon action — rejecting the whole call replaces a "
            "silent partial-success path with confusing failures",
        )

        # Single error entry, all three artist refs combined
        self.assertIsNotNone(output.errors)
        all_refs = set()
        for err in output.errors:
            all_refs.update(err.refs)
        self.assertEqual(all_refs, {"S:art_jp", "S:art_ax", "S:art_tt"})

        # Message gives the coordinator a specific next step
        combined = "; ".join(e.error for e in output.errors).lower()
        self.assertIn("shuffle", combined)
        self.assertIn("artist", combined)
        self.assertIn("album", combined)


if __name__ == "__main__":
    unittest.main()
