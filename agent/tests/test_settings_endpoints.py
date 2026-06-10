"""Tests for the Settings UI WS handlers.

These cover the read / save / reload handlers in app/settings_endpoints.
The provider-specific test handler (settings-test-*) has its own
test file when that lands.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.settings import reset_settings_for_tests
from app.settings.endpoints import (
    MANAGED_ENV_KEYS,
    SECRET_FIELDS,
    _valid_env_key,
    handle_read,
    handle_reload,
    handle_save,
)


class TestHandleRead(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fake_env = Path(self._tmp.name) / ".env"
        reset_settings_for_tests()

    def tearDown(self):
        self._tmp.cleanup()
        reset_settings_for_tests()

    def test_returns_current_env_values(self):
        self.fake_env.write_text(
            'LLM_MODEL="anthropic/claude-x"\n'
            'LLM_API_KEY_ANTHROPIC="sk-ant-abc"\n',
        )
        with patch("app.settings.env_file.resolve_env_path", return_value=self.fake_env), \
             patch.dict(os.environ, {
                 "LLM_MODEL": "anthropic/claude-x",
                 "LLM_API_KEY_ANTHROPIC": "sk-ant-abc",
             }, clear=True):
            result = handle_read({})
            self.assertTrue(result["ok"])
            self.assertEqual(result["values"]["LLM_MODEL"], "anthropic/claude-x")
            self.assertEqual(
                result["values"]["LLM_API_KEY_ANTHROPIC"], "sk-ant-abc",
            )
            self.assertEqual(result["env_path"], str(self.fake_env))
            self.assertEqual(result["config_missing"], [])
            # Source/bundle mode is editable; the UI relies on this
            # flag to enable inputs and the Save & Validate button.
            self.assertTrue(result["editable"])
            self.assertIsNone(result["editing_disabled_reason"])

    def test_reports_config_missing_when_incomplete(self):
        self.fake_env.write_text("# empty\n")
        with patch("app.settings.env_file.resolve_env_path", return_value=self.fake_env), \
             patch.dict(os.environ, {}, clear=True):
            result = handle_read({})
            self.assertEqual(result["config_missing"], ["LLM_MODEL"])

    def test_includes_secret_fields_list(self):
        with patch("app.settings.env_file.resolve_env_path", return_value=self.fake_env):
            result = handle_read({})
            self.assertIn("LLM_API_KEY_ANTHROPIC", result["secret_fields"])
            self.assertIn("BRAVE_API_KEY", result["secret_fields"])
            # Non-secrets shouldn't be in the list
            self.assertNotIn("LLM_MODEL", result["secret_fields"])


class TestHandleSave(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fake_env = Path(self._tmp.name) / ".env"
        reset_settings_for_tests()

    def tearDown(self):
        self._tmp.cleanup()
        reset_settings_for_tests()

    def test_writes_new_values_to_env(self):
        with patch("app.settings.env_file.resolve_env_path", return_value=self.fake_env), \
             patch.dict(os.environ, {}, clear=True):
            result = handle_save({"updates": {
                "LLM_MODEL": "anthropic/claude-x",
                "LLM_API_KEY_ANTHROPIC": "sk-ant-new",
            }})
            self.assertTrue(result["ok"])
            content = self.fake_env.read_text()
            self.assertIn("anthropic/claude-x", content)
            self.assertIn("sk-ant-new", content)

    def test_save_updates_config_missing_state(self):
        with patch("app.settings.env_file.resolve_env_path", return_value=self.fake_env), \
             patch.dict(os.environ, {}, clear=True):
            # Before save: missing LLM_MODEL
            result = handle_save({"updates": {
                "LLM_MODEL": "anthropic/claude-x",
                "LLM_API_KEY_ANTHROPIC": "sk-ant-new",
            }})
            self.assertEqual(result["config_missing"], [])

    def test_save_reloads_env_into_process(self):
        """Settings cache must invalidate so subsequent get_settings()
        calls see the new values without an explicit restart."""
        with patch("app.settings.env_file.resolve_env_path", return_value=self.fake_env), \
             patch.dict(os.environ, {}, clear=True):
            handle_save({"updates": {
                "LLM_MODEL": "anthropic/claude-newer",
                "LLM_API_KEY_ANTHROPIC": "sk-ant-zzz",
            }})
            self.assertEqual(os.environ["LLM_MODEL"], "anthropic/claude-newer")

    def test_returns_updated_keys(self):
        with patch("app.settings.env_file.resolve_env_path", return_value=self.fake_env), \
             patch.dict(os.environ, {}, clear=True):
            result = handle_save({"updates": {
                "LLM_MODEL": "x/y",
                "ZZZ": "value",
            }})
            self.assertEqual(result["updated_keys"], ["LLM_MODEL", "ZZZ"])

    def test_rejects_invalid_env_key_names(self):
        """SQL-injection-style keys and unicode shenanigans get rejected
        before they reach the file."""
        with patch("app.settings.env_file.resolve_env_path", return_value=self.fake_env):
            result = handle_save({"updates": {
                "good_key": "ok",
                "bad-key-with-hyphen": "rejected",
                "another bad key": "rejected",
                "123_LEADING_DIGIT": "rejected",
            }})
            self.assertFalse(result["ok"])
            self.assertIn("bad-key-with-hyphen", result["invalid_keys"])
            self.assertIn("another bad key", result["invalid_keys"])
            self.assertIn("123_LEADING_DIGIT", result["invalid_keys"])
            self.assertNotIn("good_key", result.get("invalid_keys", []))

    def test_rejects_missing_updates_payload(self):
        result = handle_save({})
        self.assertFalse(result["ok"])
        self.assertIn("updates", result["error"])

    def test_rejects_non_dict_updates(self):
        result = handle_save({"updates": "not a dict"})
        self.assertFalse(result["ok"])

    def test_empty_value_removes_key(self):
        self.fake_env.write_text('LLM_MODEL="anthropic/claude-x"\nKEEPME="value"\n')
        with patch("app.settings.env_file.resolve_env_path", return_value=self.fake_env), \
             patch.dict(os.environ, {}, clear=True):
            handle_save({"updates": {"LLM_MODEL": ""}})
            content = self.fake_env.read_text()
            self.assertNotIn("anthropic/claude-x", content)
            self.assertIn("KEEPME", content)


class TestHandleReload(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fake_env = Path(self._tmp.name) / ".env"
        reset_settings_for_tests()

    def tearDown(self):
        self._tmp.cleanup()
        reset_settings_for_tests()

    def test_reload_picks_up_out_of_band_edits(self):
        self.fake_env.write_text('LLM_MODEL="anthropic/claude-fresh"\n')
        with patch("app.settings.env_file.resolve_env_path", return_value=self.fake_env), \
             patch.dict(os.environ, {"LLM_MODEL": "anthropic/claude-stale"}):
            result = handle_reload({})
            self.assertTrue(result["ok"])
            self.assertEqual(os.environ["LLM_MODEL"], "anthropic/claude-fresh")

    def test_reload_returns_current_config_missing(self):
        self.fake_env.write_text("# empty\n")
        with patch("app.settings.env_file.resolve_env_path", return_value=self.fake_env), \
             patch.dict(os.environ, {}, clear=True):
            result = handle_reload({})
            self.assertEqual(result["config_missing"], ["LLM_MODEL"])


class TestValidEnvKey(unittest.TestCase):
    def test_accepts_conventional_names(self):
        for name in ("LLM_MODEL", "_LEADING_UNDER", "X1", "FOO_BAR_BAZ"):
            self.assertTrue(_valid_env_key(name), name)

    def test_rejects_invalid_names(self):
        for name in (
            "",                       # empty
            "1LEADING_DIGIT",         # starts with digit
            "has-hyphen",
            "has space",
            "has.dot",
            "has/slash",
            "naïve",                  # non-ASCII letter
            None,                     # non-string
            42,                       # non-string
        ):
            self.assertFalse(_valid_env_key(name), repr(name))


class TestSecretFields(unittest.TestCase):
    def test_contains_known_provider_key_names(self):
        for name in (
            "LLM_API_KEY_ANTHROPIC",
            "LLM_API_KEY_OPENAI",
            "BRAVE_API_KEY",
            "TAVILY_API_KEY",
        ):
            self.assertIn(name, SECRET_FIELDS)

    def test_excludes_non_secret_settings(self):
        for name in (
            "LLM_MODEL",
            "ENABLE_PASSIVE_ANALYSER",
            "ANALYSER_INTERVAL_MINUTES",
            "SWARPIUS_DATA_DIR",
        ):
            self.assertNotIn(name, SECRET_FIELDS)


class TestDockerMode(unittest.TestCase):
    """In Docker the host .env isn't mounted into the container — the
    Settings UI falls back to displaying os.environ (populated by
    Compose's ``env_file:`` injection) and disables Save / Reload."""

    def setUp(self):
        reset_settings_for_tests()

    def tearDown(self):
        reset_settings_for_tests()

    def test_read_reports_editable_false_and_reason(self):
        with patch("app.settings.env_file._running_in_docker", return_value=True), \
             patch.dict(os.environ, {
                 "LLM_MODEL": "anthropic/claude-x",
             }, clear=True):
            result = handle_read({})
            self.assertTrue(result["ok"])
            self.assertFalse(result["editable"])
            self.assertIsNotNone(result["editing_disabled_reason"])
            reason = result["editing_disabled_reason"]
            self.assertIn("agent/.env", reason)
            self.assertIn("Restart", reason)
            # Banner renders backtick-wrapped path as <code>; error
            # responses strip the markers (test below).
            self.assertIn("`agent/.env`", reason)

    def test_save_error_strips_backticks_for_plain_text_rendering(self):
        with patch("app.settings.env_file._running_in_docker", return_value=True):
            result = handle_save({"updates": {"LLM_MODEL": "x/y"}})
            # Backticks would render literally in a plain-text error
            # toast — the error response strips them.
            self.assertNotIn("`", result["error"])
            self.assertIn("agent/.env", result["error"])

    def test_read_returns_os_environ_filtered_by_managed_keys(self):
        with patch("app.settings.env_file._running_in_docker", return_value=True), \
             patch.dict(os.environ, {
                 "LLM_MODEL": "anthropic/claude-x",
                 "LLM_API_KEY_ANTHROPIC": "sk-ant-abc",
                 # An unmanaged key that should NOT leak into the response
                 "PATH": "/usr/bin:/bin",
             }, clear=True):
            result = handle_read({})
            self.assertEqual(result["values"]["LLM_MODEL"], "anthropic/claude-x")
            self.assertEqual(
                result["values"]["LLM_API_KEY_ANTHROPIC"], "sk-ant-abc",
            )
            self.assertNotIn("PATH", result["values"])

    def test_read_returns_host_side_env_path(self):
        """env_path is shown in the UI; in Docker the in-container path
        (``/app/.env``) would be misleading, so we return the host
        path the user actually edits."""
        with patch("app.settings.env_file._running_in_docker", return_value=True), \
             patch.dict(os.environ, {}, clear=True):
            result = handle_read({})
            self.assertEqual(result["env_path"], "agent/.env")

    def test_save_is_rejected(self):
        with patch("app.settings.env_file._running_in_docker", return_value=True):
            result = handle_save({"updates": {"LLM_MODEL": "x/y"}})
            self.assertFalse(result["ok"])
            # Error message names the file to edit + the restart step
            # so a stale UI can still surface a useful error.
            self.assertIn("agent/.env", result["error"])
            self.assertIn("restart", result["error"].lower())

    def test_empty_updates_save_succeeds_in_docker(self):
        """Restart button sends ``{updates: {}, restart: true}``; the
        dispatch layer only fires the restart on ok:true."""
        with patch("app.settings.env_file._running_in_docker", return_value=True), \
             patch.dict(os.environ, {}, clear=True):
            result = handle_save({"updates": {}})
            self.assertTrue(result["ok"])

    def test_empty_updates_save_does_not_touch_env_file(self):
        """Bypassing the editability guard is only safe if the write
        path is also skipped — a stray touch() would fail on Docker's
        read-only rootfs (or create a phantom file)."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_env = Path(tmp) / ".env"
            with patch("app.settings.env_file._running_in_docker", return_value=True), \
                 patch("app.settings.env_file.resolve_env_path", return_value=fake_env), \
                 patch.dict(os.environ, {}, clear=True):
                result = handle_save({"updates": {}})
                self.assertTrue(result["ok"])
                self.assertFalse(fake_env.exists())

    def test_reload_is_rejected(self):
        with patch("app.settings.env_file._running_in_docker", return_value=True):
            result = handle_reload({})
            self.assertFalse(result["ok"])
            self.assertIn("agent/.env", result["error"])


class TestManagedEnvKeys(unittest.TestCase):
    def test_covers_every_tab_in_the_ui(self):
        # Sanity: each tab's key set is represented. The full list
        # lives in endpoints.MANAGED_ENV_KEYS; this just guards the
        # canonical-keys-per-tab from silent regressions.
        for name in (
            "LLM_MODEL",                # Models
            "ROON_CORE_URL",            # Roon
            "WEB_SEARCH_PROVIDER",      # Web search
            "TTS_URL",                  # Speech
            "ANALYSER_INTERVAL_MINUTES",  # Analyser
            "LLM_PERSONA",              # Persona
        ):
            self.assertIn(name, MANAGED_ENV_KEYS)

    def test_excludes_secret_fields(self):
        """Secrets live in SECRET_FIELDS; MANAGED_ENV_KEYS is the
        non-secret companion set. The read endpoint unions them."""
        self.assertEqual(MANAGED_ENV_KEYS & SECRET_FIELDS, frozenset())


if __name__ == "__main__":
    unittest.main()
