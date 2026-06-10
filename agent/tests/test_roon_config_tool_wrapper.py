"""Direct coverage of ``RoonConfigTool.run_async`` — the exception-
to-output mapping branch (lines 122-126) and the missing-handler
guard. ``perform_config_action`` dispatch is covered indirectly via
``test_perform_config_action_dispatch.py``; this file pins the
tool-wrapper contract: any exception out of the handler must surface
on the output schema's ``error`` field rather than escape.
"""

from __future__ import annotations

import unittest

from tools.roon_config import (
    RoonConfigTool,
    RoonConfigToolConfig,
    RoonConfigToolInputSchema,
)


class TestRoonConfigToolWrapper(unittest.IsolatedAsyncioTestCase):

    async def test_handler_exception_returned_as_error_output(self) -> None:
        """A raise from the dispatch layer must be caught and reported
        on the output schema (both ``result`` and ``error`` carry the
        message), not propagate out of the tool."""
        def _raises(*_a, **_k):
            raise RuntimeError("zone not found: Bathroom")

        tool = RoonConfigTool(
            RoonConfigToolConfig(perform_config_action=_raises),
        )
        out = await tool.run_async(
            RoonConfigToolInputSchema(action="Get Default Zone"),
        )
        self.assertEqual(out.error, "zone not found: Bathroom")
        self.assertEqual(out.result, "zone not found: Bathroom")

    async def test_missing_handler_surfaces_configuration_error(self) -> None:
        """When ``perform_config_action`` is not wired up, the tool
        raises ``ToolConfigurationError`` internally — the generic
        exception clause converts it to the same error-output shape."""
        tool = RoonConfigTool(
            RoonConfigToolConfig(perform_config_action=None),
        )
        out = await tool.run_async(
            RoonConfigToolInputSchema(action="Get Default Zone"),
        )
        self.assertIsNotNone(out.error)
        self.assertIn("not available", out.error or "")


if __name__ == "__main__":
    unittest.main()
