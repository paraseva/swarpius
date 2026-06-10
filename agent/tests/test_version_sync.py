"""``agent/VERSION`` is the single source of truth for the app version, and
``web-client/package.json``'s ``version`` mirrors it (the web build reads its
displayed version from there). They must stay equal — drift would ship a
mismatched version — so this fails CI on any divergence. See the release
checklist in CONTRIBUTING.md.
"""
import json
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENT_VERSION = _REPO_ROOT / "agent" / "VERSION"
_WEB_PACKAGE_JSON = _REPO_ROOT / "web-client" / "package.json"


@unittest.skipUnless(
    _WEB_PACKAGE_JSON.exists(),
    "web-client/package.json not present (e.g. agent-only checkout)",
)
class TestVersionInSync(unittest.TestCase):
    def test_agent_version_matches_web_package_json(self):
        agent_version = _AGENT_VERSION.read_text(encoding="utf-8").strip()
        web_version = json.loads(
            _WEB_PACKAGE_JSON.read_text(encoding="utf-8")
        )["version"]
        self.assertEqual(
            agent_version,
            web_version,
            f"agent/VERSION ({agent_version}) != web-client/package.json "
            f"version ({web_version}) — bump both together "
            f"(see the release checklist in CONTRIBUTING.md).",
        )


if __name__ == "__main__":
    unittest.main()
