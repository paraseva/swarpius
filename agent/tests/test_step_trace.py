"""Tests for the `_step_trace` helper in request_flow.py.

`_step_trace` builds the per-step tool-output trace entry that feeds
the execution-trace context provider. These tests cover per-skill
compaction: result_fetch keeps full items; roon_search flattens to
compact_items strings; roon_status restructures queue output.
"""

import unittest

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.coordinator.request_flow import _step_trace  # noqa: E402
from tools.result_fetch import (  # noqa: E402
    ResultFetchToolInputSchema,
    ResultFetchToolOutputSchema,
)

# ------------------------------------------------------------------ #
#  Stubs for _resolve_cached_result_handle tests                     #
# ------------------------------------------------------------------ #

def _make_test_registry():
    """Build a registry with tool instances for compact_trace dispatch."""
    from app.llm.tool_registry import ToolRegistry
    from tools.roon_search import RoonSearchTool, RoonSearchToolConfig, RoonSearchToolInputSchema
    from tools.roon_status import RoonStatusTool, RoonStatusToolConfig, RoonStatusToolInputSchema

    async def _noop(params):
        pass

    reg = ToolRegistry()
    reg.register("roon_search", "Search", RoonSearchToolInputSchema, _noop,
                 tool_instance=RoonSearchTool(RoonSearchToolConfig()))
    reg.register("result_fetch", "Fetch", ResultFetchToolInputSchema, _noop,
                 tool_instance=type("_FetchStub", (), {"compact_trace": lambda self, o: o.model_dump(mode="json")})())
    reg.register("roon_status", "Status", RoonStatusToolInputSchema, _noop,
                 tool_instance=RoonStatusTool(RoonStatusToolConfig(resolve_zone=lambda z: z)))
    return reg


class _RuntimeStub:
    """Minimal stub: result_store + last_result_handle + tool_registry."""

    def __init__(self, result_store, last_result_handle=None):
        self.result_store = result_store
        self.last_result_handle = last_result_handle
        self.store_calls = 0
        self.tool_registry = _make_test_registry()

    def store_result_handle(self, payload):
        _ = payload
        self.store_calls += 1
        return "res_99999"


# ------------------------------------------------------------------ #
#  Tests: _step_trace (pure function, no deps)                       #
# ------------------------------------------------------------------ #

class TestStepTrace(unittest.TestCase):
    def test_keeps_full_result_fetch_items(self):
        runtime = _RuntimeStub(result_store={}, last_result_handle=None)
        tool_output = ResultFetchToolOutputSchema(
            result_handle="res_00012",
            result="Cached list retrieved",
            items=[f"item-{i}" for i in range(20)],
            total_count=20,
        )

        trace_step = _step_trace(
            step=1,
            global_step=1,
            selected_skill="result_fetch",
            tool_params=None,
            tool_output=tool_output,
            runtime_state=runtime,
            note=None,
        )

        self.assertEqual(len(trace_step["tool_output"]["items"]), 20)
        self.assertEqual(runtime.store_calls, 0)

    def test_roon_search_compacts_to_items_list(self):
        """roon_search trace output uses compact_items format:
        description + flat list of '(N) [ref] title | extra' strings."""
        from roon_core.schemas import RoonCoreItemSummarySchema, RoonCoreResultsGroupSchema
        from tools.roon_search import RoonSearchToolOutputSchema

        runtime = _RuntimeStub(result_store={}, last_result_handle=None)
        tool_output = RoonSearchToolOutputSchema(
            description="Search results for 'jazz'.",
            groups=[RoonCoreResultsGroupSchema(
                group="-",
                items=[
                    RoonCoreItemSummarySchema(
                        title="Jazz Album", reference="ja1",
                        extra_info="Artist A", group="-",
                    ),
                    RoonCoreItemSummarySchema(
                        title="Jazz Track", reference="jt1",
                        extra_info="Artist B", group="-",
                    ),
                ],
            )],
        )

        trace_step = _step_trace(
            step=1, global_step=1,
            selected_skill="roon_search",
            tool_params=None,
            tool_output=tool_output,
            runtime_state=runtime,
        )

        out = trace_step["tool_output"]
        self.assertEqual(out["description"], "Search results for 'jazz'.")
        self.assertEqual(len(out["items"]), 2)
        self.assertEqual(out["items"][0], "(1) [ja1] Jazz Album | Artist A")
        self.assertEqual(out["items"][1], "(2) [jt1] Jazz Track | Artist B")

    def test_roon_status_queue_restructured(self):
        """roon_status get_queue_status trace output is restructured
        from flat text to per-zone arrays."""
        from tools.roon_status import RoonStatusToolOutputSchema

        runtime = _RuntimeStub(result_store={}, last_result_handle=None)
        tool_output = RoonStatusToolOutputSchema(
            operation="get_queue_status",
            result=(
                "Queue for Headphones (2 tracks)\n\n"
                "(1) [abc12] Track A | Album A | Artist A\n"
                "(2) [def34] Track B | Album B | Artist B"
            ),
        )

        trace_step = _step_trace(
            step=1, global_step=1,
            selected_skill="roon_status",
            tool_params=None,
            tool_output=tool_output,
            runtime_state=runtime,
        )

        out = trace_step["tool_output"]
        self.assertEqual(len(out["queues"]), 1)
        self.assertEqual(out["queues"][0]["zone"], "Headphones")
        self.assertEqual(len(out["queues"][0]["items"]), 2)

    def test_roon_status_non_queue_uses_default_compaction(self):
        """roon_status with get_zones_status uses default compaction,
        not queue restructuring."""
        from tools.roon_status import RoonStatusToolOutputSchema

        runtime = _RuntimeStub(result_store={}, last_result_handle=None)
        tool_output = RoonStatusToolOutputSchema(
            operation="get_zones_status",
            result="Playing: Some Track",
        )

        trace_step = _step_trace(
            step=1, global_step=1,
            selected_skill="roon_status",
            tool_params=None,
            tool_output=tool_output,
            runtime_state=runtime,
        )

        out = trace_step["tool_output"]
        # Default compaction keeps the dict structure
        self.assertIn("operation", out)
        self.assertIn("result", out)
        # Must NOT have queue structure
        self.assertNotIn("queues", out)


if __name__ == "__main__":
    unittest.main()
