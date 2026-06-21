"""``render_banner`` produces the startup summary printed once
after ``runtime.ensure_initialised`` completes in CLI mode.

Pins the contract that:
  * Every section appears even when its underlying value is
    "off" / "disabled" / "(default)" — operator wants to *see*
    that TTS is intentionally disabled, not infer it from
    absence.
  * Truthiness branches: diagnostic agent on/off, prompt
    caching on/off, web search backend or "disabled", TTS URL
    or "disabled", parallel tools off / "on (max N)".
  * Labels are aligned in a two-column layout for skim-ability.
"""

from __future__ import annotations

import unittest

from app.cli.startup_banner import copyright_notice, render_banner


def _facts(**overrides):
    base = {
        "roon_core": "Kitchen Core (192.168.1.100:9100)",
        "roon_profile": "Default",
        "default_zone": "Living Room",
        "coordinator_model": "anthropic/claude-sonnet-4-6",
        "coordinator_pattern": "sonnet",
        "arbiter_model": "anthropic/claude-haiku-4-5",
        "diagnostic_model": "anthropic/claude-haiku-4-5",
        "diagnostic_enabled": False,
        "prompt_caching": True,
        "web_search": "brave (auto-detected)",
        "tts_url": None,
        "parallel_tools": False,
        "parallel_max": 5,
        "log_file": "/var/log/swarpius.log",
    }
    base.update(overrides)
    return base


class TestRenderBanner(unittest.TestCase):
    def test_all_sections_present(self) -> None:
        out = render_banner(_facts())
        for required in (
            "Swarpius",
            "Roon Core",
            "Kitchen Core",
            "192.168.1.100:9100",
            "Roon profile",
            "Default",
            "Default zone",
            "Living Room",
            "Coordinator",
            "anthropic/claude-sonnet-4-6",
            "Arbiter",
            "Diagnostic",
            "Prompt caching",
            "Web search",
            "TTS",
            "Parallel tools",
            "Logs",
            "/var/log/swarpius.log",
        ):
            self.assertIn(required, out, f"missing: {required!r}")

    def test_coordinator_pattern_shown_when_present(self) -> None:
        out = render_banner(_facts(coordinator_pattern="sonnet"))
        self.assertIn("[sonnet]", out)

    def test_diagnostic_off_label(self) -> None:
        out = render_banner(_facts(diagnostic_enabled=False))
        self.assertIn("off", out)

    def test_diagnostic_on_label(self) -> None:
        out = render_banner(_facts(diagnostic_enabled=True))
        diag_line = [line for line in out.splitlines() if "Diagnostic" in line and "mode" in line][0]
        self.assertIn("on", diag_line)
        self.assertNotIn("off", diag_line)

    def test_caching_on_off(self) -> None:
        on_out = render_banner(_facts(prompt_caching=True))
        off_out = render_banner(_facts(prompt_caching=False))
        on_line = [line for line in on_out.splitlines() if "Prompt caching" in line][0]
        off_line = [line for line in off_out.splitlines() if "Prompt caching" in line][0]
        self.assertIn("on", on_line)
        self.assertIn("off", off_line)

    def test_web_search_disabled(self) -> None:
        out = render_banner(_facts(web_search=None))
        line = [line for line in out.splitlines() if "Web search" in line][0]
        self.assertIn("disabled", line)

    def test_tts_url_shown_when_set(self) -> None:
        out = render_banner(_facts(tts_url="http://localhost:9998"))
        self.assertIn("http://localhost:9998", out)

    def test_tts_disabled_when_unset(self) -> None:
        out = render_banner(_facts(tts_url=None))
        line = [line for line in out.splitlines() if line.lstrip().startswith("TTS")][0]
        self.assertIn("disabled", line)

    def test_parallel_tools_off(self) -> None:
        out = render_banner(_facts(parallel_tools=False))
        line = [line for line in out.splitlines() if "Parallel tools" in line][0]
        self.assertIn("off", line)
        self.assertNotIn("max", line)

    def test_parallel_tools_on_shows_cap(self) -> None:
        out = render_banner(_facts(parallel_tools=True, parallel_max=5))
        line = [line for line in out.splitlines() if "Parallel tools" in line][0]
        self.assertIn("on", line)
        self.assertIn("max 5", line)

    def test_parallel_tools_on_unlimited(self) -> None:
        """``parallel_max=None`` represents unlimited (env value
        ``0`` or absent under PARALLEL_TOOLS=true)."""
        out = render_banner(_facts(parallel_tools=True, parallel_max=None))
        line = [line for line in out.splitlines() if "Parallel tools" in line][0]
        self.assertIn("on", line)
        self.assertIn("unlimited", line)

class TestCopyrightNotice(unittest.TestCase):
    """The startup copyright/trademark line shown in both CLI and WS
    modes. Pins the legally-meaningful bits: the ™ claim, the © symbol,
    the registered entity, and the year."""

    def test_carries_trademark_copyright_and_entity(self) -> None:
        out = copyright_notice()
        self.assertIn("Swarpius", out)
        self.assertIn("™", out)
        self.assertIn("©", out)
        self.assertIn("2026", out)
        self.assertIn("Paraseva Ltd", out)


class TestCollectBannerFacts(unittest.TestCase):
    """Smoke-test that the runtime-introspection glue actually
    runs end-to-end. Pre-fix the import path for
    ``is_diagnostic_agent_enabled`` was wrong (lives in
    ``diagnostic_agent``, not ``runtime_state``) — never caught
    because the unit tests above bypass ``collect_banner_facts``
    by feeding ``render_banner`` a hardcoded facts dict."""

    def test_collect_returns_all_expected_keys(self) -> None:
        from unittest.mock import MagicMock

        from app.cli.startup_banner import collect_banner_facts

        runtime = MagicMock()
        # Roon address lives on RoonConnection, not on the RoonApi
        # instance — port specifically isn't on RoonApi.
        runtime.roon_connection.roon_core_host = "10.0.0.1"
        runtime.roon_connection.roon_core_port = 9100
        runtime.roon_connection.api.core_name = "Test Core"
        runtime.roon_connection.get_default_zone.return_value = "Living Room"
        runtime.llm_client.model = "anthropic/claude-sonnet-4-6"
        runtime.arbiter_client.model = "anthropic/claude-haiku-4-5"
        runtime.diagnostic_client.model = "anthropic/claude-haiku-4-5"
        runtime.resolved_profile.matched_pattern = "sonnet"
        runtime.tool_registry.get.return_value = None  # web_search unregistered

        settings = MagicMock()
        settings.roon_profile_name = "Default"
        settings.tts_url_cli = ""
        settings.parallel_tools = False
        settings.roon_max_parallel = 5

        facts = collect_banner_facts(runtime, settings, log_file="/tmp/x.log")

        # Pin the host:port rendering — earlier bug rendered
        # "(192.168.1.47:)" with a trailing colon because port
        # was looked up on RoonApi (where it doesn't exist).
        self.assertIn("10.0.0.1:9100", facts["roon_core"])
        self.assertNotIn("10.0.0.1:)", facts["roon_core"])

        expected_keys = {
            "roon_core", "roon_profile", "default_zone",
            "coordinator_model", "coordinator_pattern",
            "arbiter_model", "diagnostic_model",
            "diagnostic_enabled", "prompt_caching",
            "web_search", "tts_url",
            "parallel_tools", "parallel_max",
            "log_file",
        }
        self.assertEqual(set(facts.keys()), expected_keys)
        # And the result must round-trip through render_banner
        # without raising — catches any field type mismatches.
        from app.cli.startup_banner import render_banner
        render_banner(facts)


if __name__ == "__main__":
    unittest.main()
