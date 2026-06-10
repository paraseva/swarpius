"""Smoke-import the top-level entrypoints.

These tests catch the class of bug where a module-level or
function-level ``from X import Y`` references a name that no longer
exists — module-level imports that the unit-test suite doesn't load
can still break Docker / source startup. A bare ``import agent``
exercises every module-level import; the second test exercises the
public path helpers.
"""

from __future__ import annotations

import importlib
import unittest

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()


class TestEntrypointImports(unittest.TestCase):
    def test_agent_module_imports(self):
        importlib.import_module("agent")

    def test_roon_core_auth_path_helpers_resolve(self):
        from roon_core.auth import default_core_id_path, default_token_path

        # Helpers must return a usable Path; we don't care about the
        # specific value, only that they don't blow up.
        self.assertTrue(str(default_core_id_path()))
        self.assertTrue(str(default_token_path()))


if __name__ == "__main__":
    unittest.main()
