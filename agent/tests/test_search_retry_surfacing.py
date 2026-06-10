"""Tests that Roon search retries surface through to tool output and logging.

Production retry logic lives in ``RoonBrowseMixin.browse_core``: when
the Core returns a transient 'No Results' it loops up to
``ROON_SEARCH_RETRY_LIMIT`` times, accumulating retry notes and an
attempt count on the result. The retry/attempts fields must be visible
on the tool output (so per-request logs see them) but excluded from
``model_dump`` / ``model_dump_json`` (so they don't leak to the LLM).

Both test classes inherit ``RoonBrowseMixin`` and stub only the
``_browse_core_once`` API boundary — the real ``browse_core`` retry
loop runs.
"""

import asyncio
import os
import unittest
from typing import List
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from roon_core.browse import RoonBrowseMixin  # noqa: E402
from roon_core.browse_session import BrowseSessionManager  # noqa: E402
from roon_core.schemas import (  # noqa: E402
    RoonCoreItemSchema,
    RoonCoreListSchema,
    RoonCoreResultsSchema,
)


def _no_results() -> RoonCoreResultsSchema:
    return RoonCoreResultsSchema(
        items=[RoonCoreItemSchema(title="No Results")],
        list=RoonCoreListSchema(count=1),
    )


def _real_results() -> RoonCoreResultsSchema:
    return RoonCoreResultsSchema(
        items=[
            RoonCoreItemSchema(title="Heatseeker", subtitle="AC/DC"),
            RoonCoreItemSchema(title="Back in Black", subtitle="AC/DC"),
        ],
        list=RoonCoreListSchema(count=2),
    )


class _RetryFake(RoonBrowseMixin):
    """``RoonBrowseMixin``-inheriting fake that scripts per-attempt
    responses for ``_browse_core_once`` (the API boundary inside
    ``browse_core``'s retry loop). Production retry logic runs
    unchanged on top.
    """

    def __init__(self, responses: List[RoonCoreResultsSchema]) -> None:
        self.session_manager = BrowseSessionManager()
        self._responses = list(responses)
        self._call_count = 0

    def _build_browse_opts(self, zone, session_key):
        # Cross-mixin dep stub — real implementation lives on
        # ``RoonZoneMixin`` and feeds an opaque output_id into the API
        # request, which the fake never makes.
        _ = (zone, session_key)
        return {}

    def _browse_core_once(self, aux, zone, session_key, max_items=None):
        _ = (aux, zone, session_key, max_items)
        idx = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        return self._responses[idx]


class TestBrowseCoreRetrySurfacing(unittest.TestCase):
    """``browse_core`` sets ``search_attempts`` and ``search_retry_notes``
    on its result, accumulating notes across retries."""

    @patch.dict(os.environ, {"ROON_SEARCH_RETRY_LIMIT": "2", "ROON_SEARCH_RETRY_DELAY": "0"})
    def test_successful_first_attempt_has_attempt_1(self):
        conn = _RetryFake([_real_results()])

        sk = conn.session_manager.new_search_session()
        result = conn.browse_core(
            {"pop_all": True, "input": "AC/DC"}, session_key=sk,
        )

        self.assertEqual(result.search_attempts, 1)
        self.assertIsNone(result.search_retry_notes)
        self.assertEqual(len(result.items), 2)

    @patch.dict(os.environ, {"ROON_SEARCH_RETRY_LIMIT": "2", "ROON_SEARCH_RETRY_DELAY": "0"})
    def test_retry_then_success_has_attempt_2(self):
        conn = _RetryFake([_no_results(), _real_results()])

        sk = conn.session_manager.new_search_session()
        result = conn.browse_core(
            {"pop_all": True, "input": "Heatseeker AC/DC"}, session_key=sk,
        )

        self.assertEqual(result.search_attempts, 2)
        self.assertIsNotNone(result.search_retry_notes)
        self.assertEqual(len(result.search_retry_notes), 1)
        self.assertIn("Heatseeker AC/DC", result.search_retry_notes[0])
        self.assertEqual(len(result.items), 2)

    @patch.dict(os.environ, {"ROON_SEARCH_RETRY_LIMIT": "2", "ROON_SEARCH_RETRY_DELAY": "0"})
    def test_all_retries_exhausted_has_max_attempts(self):
        conn = _RetryFake([_no_results()])

        sk = conn.session_manager.new_search_session()
        result = conn.browse_core(
            {"pop_all": True, "input": "nonexistent"}, session_key=sk,
        )

        self.assertEqual(result.search_attempts, 3)
        self.assertEqual(len(result.search_retry_notes), 2)

    @patch.dict(os.environ, {"ROON_SEARCH_RETRY_LIMIT": "2", "ROON_SEARCH_RETRY_DELAY": "0"})
    def test_non_search_operations_have_attempt_1(self):
        conn = _RetryFake([_real_results()])

        sk = conn.session_manager.new_search_session()
        result = conn.browse_core({"item_key": "abc123"}, session_key=sk)

        self.assertEqual(result.search_attempts, 1)
        self.assertIsNone(result.search_retry_notes)

    @patch.dict(os.environ, {"ROON_SEARCH_RETRY_LIMIT": "2", "ROON_SEARCH_RETRY_DELAY": "0"})
    def test_retry_fields_excluded_from_serialization(self):
        conn = _RetryFake([_no_results(), _real_results()])

        sk = conn.session_manager.new_search_session()
        result = conn.browse_core(
            {"pop_all": True, "input": "test"}, session_key=sk,
        )
        dumped = result.model_dump(mode="json")

        self.assertEqual(result.search_attempts, 2)
        self.assertNotIn("search_attempts", dumped)
        self.assertNotIn("search_retry_notes", dumped)


class TestSearchToolRetrySurfacing(unittest.TestCase):
    """``RoonSearchTool`` propagates retry info from ``browse_core`` to
    its output, excluded from LLM-facing serialization. Production
    ``browse_core`` retry logic and ``compile_output`` both run on the
    call path — only the per-attempt API boundary is stubbed.
    """

    def _make_tool(self, responses: List[RoonCoreResultsSchema]):
        from tools.roon_search import (
            RoonSearchTool,
            RoonSearchToolConfig,
        )
        conn = _RetryFake(responses)
        tool = RoonSearchTool(RoonSearchToolConfig(roon_connection=conn))
        return tool, conn

    @patch.dict(os.environ, {"ROON_SEARCH_RETRY_LIMIT": "2", "ROON_SEARCH_RETRY_DELAY": "0"})
    def test_tool_output_carries_retry_info(self):
        from tools.roon_search import RoonSearchToolInputSchema
        # First attempt returns 'No Results', second returns real items.
        # Production retry counts attempts and accumulates notes.
        tool, _conn = self._make_tool([_no_results(), _real_results()])
        params = RoonSearchToolInputSchema(
            operation="new_search", search_string="Heatseeker AC/DC",
        )

        output = asyncio.run(tool.run_async(params))

        self.assertEqual(output.search_attempts, 2)
        self.assertIsNotNone(output.search_retry_notes)
        self.assertEqual(len(output.search_retry_notes), 1)
        self.assertIn("Heatseeker AC/DC", output.search_retry_notes[0])

        # LLM-facing serialization excludes the retry fields.
        dumped = output.model_dump(mode="json")
        self.assertNotIn("search_attempts", dumped)
        self.assertNotIn("search_retry_notes", dumped)
        dumped_json = output.model_dump_json()
        self.assertNotIn("search_attempts", dumped_json)

    @patch.dict(os.environ, {"ROON_SEARCH_RETRY_LIMIT": "2", "ROON_SEARCH_RETRY_DELAY": "0"})
    def test_tool_output_no_retry(self):
        from tools.roon_search import RoonSearchToolInputSchema
        tool, _conn = self._make_tool([_real_results()])
        params = RoonSearchToolInputSchema(
            operation="new_search", search_string="AC/DC",
        )

        output = asyncio.run(tool.run_async(params))

        self.assertEqual(output.search_attempts, 1)
        self.assertIsNone(output.search_retry_notes)
