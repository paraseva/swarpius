import asyncio
import unittest

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from tools.result_fetch import (  # noqa: E402
    ResultFetchTool,
    ResultFetchToolConfig,
    ResultFetchToolInputSchema,
)


class TestResultFetchTool(unittest.TestCase):
    def test_shared_empty_store_reference_is_preserved(self):
        shared_store = {}
        tool = ResultFetchTool(config=ResultFetchToolConfig(result_store=shared_store))
        shared_store["res_00010"] = ["first", "second"]

        output = asyncio.run(
            tool.run_async(
                ResultFetchToolInputSchema(result_handle="res_00010"),
            ),
        )
        self.assertEqual(output.result, "Cached list retrieved")
        self.assertEqual(output.items, ["first", "second"])

    def test_returns_all_items(self):
        tool = ResultFetchTool(
            config=ResultFetchToolConfig(result_store={"res_00001": ["a", "b", "c", "d"]}),
        )
        output = asyncio.run(
            tool.run_async(
                ResultFetchToolInputSchema(result_handle="res_00001"),
            ),
        )
        self.assertEqual(output.total_count, 4)
        self.assertEqual(output.items, ["a", "b", "c", "d"])

    def test_missing_handle_returns_error(self):
        tool = ResultFetchTool(config=ResultFetchToolConfig(result_store={}))
        output = asyncio.run(
            tool.run_async(
                ResultFetchToolInputSchema(result_handle="res_99999"),
            ),
        )
        self.assertEqual(output.result, "Result handle not found")
        self.assertIsNotNone(output.error)

    def test_result_label_includes_search_history_description(self):
        """result_fetch output should include the source label from search history."""
        from dataclasses import dataclass

        @dataclass
        class FakeEntry:
            result_handle: str
            description: str

        history = [FakeEntry(result_handle="res_00001", description='"Dark Side of the Moon"')]
        tool = ResultFetchTool(
            config=ResultFetchToolConfig(
                result_store={"res_00001": ["track1", "track2"]},
                search_history=history,
            ),
        )
        output = asyncio.run(
            tool.run_async(ResultFetchToolInputSchema(result_handle="res_00001")),
        )
        self.assertEqual(output.result, 'List for: "Dark Side of the Moon"')

    def test_result_label_falls_back_without_history(self):
        """Without search history, result_fetch should use the generic label."""
        tool = ResultFetchTool(
            config=ResultFetchToolConfig(
                result_store={"res_00001": ["a"]},
            ),
        )
        output = asyncio.run(
            tool.run_async(ResultFetchToolInputSchema(result_handle="res_00001")),
        )
        self.assertEqual(output.result, "Cached list retrieved")

    def test_result_label_falls_back_for_unknown_handle(self):
        """If the handle isn't in search history, use the generic label."""
        from dataclasses import dataclass

        @dataclass
        class FakeEntry:
            result_handle: str
            description: str

        history = [FakeEntry(result_handle="res_00099", description='"something else"')]
        tool = ResultFetchTool(
            config=ResultFetchToolConfig(
                result_store={"res_00001": ["a"]},
                search_history=history,
            ),
        )
        output = asyncio.run(
            tool.run_async(ResultFetchToolInputSchema(result_handle="res_00001")),
        )
        self.assertEqual(output.result, "Cached list retrieved")


if __name__ == "__main__":
    unittest.main()
