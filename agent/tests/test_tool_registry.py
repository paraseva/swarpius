"""Tests for ToolRegistry: registration, schema generation, and dispatch."""

import asyncio
import unittest

from pydantic import BaseModel, Field

from app.llm.tool_registry import ToolRegistry


class _AddInput(BaseModel):
    a: int = Field(..., description="First number")
    b: int = Field(..., description="Second number")


class _AddOutput(BaseModel):
    result: int


async def _add_execute(params: _AddInput) -> _AddOutput:
    return _AddOutput(result=params.a + params.b)


class TestToolRegistration(unittest.TestCase):
    def test_register_and_get(self):
        reg = ToolRegistry()
        reg.register("add", "Add two numbers", _AddInput, _add_execute)
        self.assertIn("add", reg)
        self.assertEqual(len(reg), 1)
        self.assertEqual(reg.tool_names, ["add"])
        tool = reg.get("add")
        self.assertEqual(tool.name, "add")
        self.assertEqual(tool.description, "Add two numbers")

    def test_get_missing_returns_none(self):
        reg = ToolRegistry()
        self.assertIsNone(reg.get("nonexistent"))

    def test_contains(self):
        reg = ToolRegistry()
        reg.register("add", "Add two numbers", _AddInput, _add_execute)
        self.assertIn("add", reg)
        self.assertNotIn("subtract", reg)


class TestToolSchemaGeneration(unittest.TestCase):
    def test_to_tool_schemas_format(self):
        reg = ToolRegistry()
        reg.register("add", "Add two numbers", _AddInput, _add_execute)
        schemas = reg.to_tool_schemas()
        self.assertEqual(len(schemas), 1)
        schema = schemas[0]
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "add")
        self.assertEqual(schema["function"]["description"], "Add two numbers")
        params = schema["function"]["parameters"]
        self.assertIn("properties", params)
        self.assertIn("a", params["properties"])
        self.assertIn("b", params["properties"])

    def test_schema_is_valid_llm_tool_shape(self):
        """The generated schema should have the minimum fields an LLM
        tool-calling API expects: type=function, a function.name, a
        function.description, and a parameters object with properties.
        Specifically NOT pinned: Pydantic-generated metadata (e.g.
        'title') that varies with Pydantic versions and doesn't affect
        tool-call behaviour."""
        reg = ToolRegistry()
        reg.register("add", "Add", _AddInput, _add_execute)
        schema = reg.to_tool_schemas()[0]
        self.assertEqual(schema["type"], "function")
        self.assertIn("name", schema["function"])
        self.assertIn("description", schema["function"])
        params = schema["function"]["parameters"]
        self.assertIn("properties", params)
        self.assertIn("a", params["properties"])
        self.assertIn("b", params["properties"])


class TestToolExecution(unittest.TestCase):
    def test_execute_valid_args(self):
        reg = ToolRegistry()
        reg.register("add", "Add", _AddInput, _add_execute)
        output = asyncio.run(reg.execute("add", {"a": 3, "b": 4}))
        self.assertEqual(output.result, 7)

    def test_execute_unknown_tool_raises(self):
        reg = ToolRegistry()
        with self.assertRaises(KeyError):
            asyncio.run(reg.execute("nope", {}))

    def test_execute_invalid_args_raises(self):
        reg = ToolRegistry()
        reg.register("add", "Add", _AddInput, _add_execute)
        with self.assertRaises(ValueError):
            asyncio.run(reg.execute("add", {"x": 1}))


if __name__ == "__main__":
    unittest.main()
