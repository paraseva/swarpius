"""Regression tests for ToolRegistry.get_result_entries.

Prior to the fix the dispatch method caught every Exception from
input_schema.model_validate and silently returned None, with no log.
A malformed tool call (LLM produced arguments the schema rejected)
would disappear — no result entries stored, no trace. The fix
narrows the except to pydantic.ValidationError and logs a warning
so the malformed call is visible without crashing the request.
"""

import unittest
from typing import Any

from pydantic import BaseModel

from app.llm.tool_registry import ToolRegistry


class _SchemaA(BaseModel):
    query: str
    limit: int = 10


class _ToolWithEntries:
    def get_result_entries(self, parsed_input: _SchemaA, output: Any):
        return [{"query": parsed_input.query, "output": output}]


class _ToolWithoutEntries:
    pass


async def _noop(inp: _SchemaA) -> BaseModel:
    return _SchemaA(query=inp.query)


class TestGetResultEntriesDispatch(unittest.TestCase):

    def _make_registry(self, tool_instance) -> ToolRegistry:
        reg = ToolRegistry()
        reg.register(
            name="t",
            description="test tool",
            input_schema=_SchemaA,
            execute=_noop,
            tool_instance=tool_instance,
        )
        return reg

    def test_valid_args_returns_entries(self) -> None:
        reg = self._make_registry(_ToolWithEntries())
        entries = reg.get_result_entries("t", {"query": "jazz"}, output="raw")
        self.assertEqual(entries, [{"query": "jazz", "output": "raw"}])

    def test_invalid_args_returns_none(self) -> None:
        """Contract preserved: malformed arguments still yield None so
        callers don't blow up on a bad tool call.
        """
        reg = self._make_registry(_ToolWithEntries())
        entries = reg.get_result_entries("t", {"limit": "nope"}, output="raw")
        self.assertIsNone(entries)

    def test_invalid_args_logs_a_warning(self) -> None:
        """A malformed call must be visible in logs at WARNING level, so
        it shows up in default configs.
        """
        reg = self._make_registry(_ToolWithEntries())
        with self.assertLogs("swarpius.tool_registry", level="WARNING") as cap:
            reg.get_result_entries("t", {"limit": "nope"}, output="raw")
        combined = "\n".join(cap.output)
        self.assertIn("t", combined, "log should name the tool")
        self.assertTrue(
            "validation" in combined.lower() or "invalid" in combined.lower(),
            f"log should mention validation/invalid args; got {combined!r}",
        )

    def test_tool_without_method_returns_none(self) -> None:
        """Existing contract preserved: tools that don't implement
        get_result_entries return None without error.
        """
        reg = self._make_registry(_ToolWithoutEntries())
        entries = reg.get_result_entries("t", {"query": "jazz"}, output="raw")
        self.assertIsNone(entries)

    def test_unregistered_tool_returns_none(self) -> None:
        reg = ToolRegistry()
        entries = reg.get_result_entries("missing", {"query": "jazz"}, output="raw")
        self.assertIsNone(entries)


if __name__ == "__main__":
    unittest.main()
