"""Tests for the canonical .env reader/writer module.

Settings page work depends on these primitives:
- resolve_env_path() — where .env lives (bundle vs source/Docker)
- ensure_env_file_exists() — first-run template copy in a bundle
- read_env_file() — parse current values for the UI form
- write_env_file() — persist UI form values, preserving comments
- reload_env_into_process() — pick up out-of-band edits
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.settings.env_file import (
    ensure_env_file_exists,
    env_editable,
    read_env_file,
    read_managed_env,
    reload_env_into_process,
    resolve_env_path,
    resolve_env_path_for_display,
    resolve_env_template_path,
    write_env_file,
)


class TestResolveEnvPath(unittest.TestCase):
    """In source/Docker mode, .env lives at <agent>/.env. In bundle mode
    it lives in the per-user data directory."""

    def test_source_mode_returns_agent_root_env(self):
        with patch("app.settings.env_file._running_from_bundle", return_value=False):
            from app.data_paths import AGENT_ROOT
            self.assertEqual(resolve_env_path(), AGENT_ROOT / ".env")

    def test_bundle_mode_returns_data_dir_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.settings.env_file._running_from_bundle", return_value=True), \
                 patch.dict(os.environ, {"SWARPIUS_DATA_DIR": tmp}):
                self.assertEqual(resolve_env_path(), Path(tmp) / ".env")


class TestEnsureEnvFileExists(unittest.TestCase):
    def test_source_mode_is_noop_when_env_missing(self):
        """Source devs are expected to create .env themselves; we don't
        auto-create it for them (that's a bundle-only convenience)."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_env = Path(tmp) / ".env"
            with patch("app.settings.env_file._running_from_bundle", return_value=False), \
                 patch("app.settings.env_file.resolve_env_path", return_value=fake_env):
                env_path, created = ensure_env_file_exists()
                self.assertEqual(env_path, fake_env)
                self.assertFalse(created)
                self.assertFalse(fake_env.exists())

    def test_bundle_mode_copies_template_on_first_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            fake_env = data_dir / ".env"
            fake_template = Path(tmp) / ".env.template"
            fake_template.write_text("LLM_MODEL=\nLLM_API_KEY_ANTHROPIC=\n")

            with patch("app.settings.env_file._running_from_bundle", return_value=True), \
                 patch("app.settings.env_file.resolve_env_path", return_value=fake_env), \
                 patch("app.settings.env_file.resolve_env_template_path", return_value=fake_template):
                env_path, created = ensure_env_file_exists()
                self.assertEqual(env_path, fake_env)
                self.assertTrue(created)
                self.assertTrue(fake_env.exists())
                self.assertIn("LLM_MODEL=", fake_env.read_text())

    def test_bundle_mode_is_noop_when_env_already_exists(self):
        """Don't clobber an existing .env on subsequent runs."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_env = Path(tmp) / ".env"
            fake_env.write_text("LLM_MODEL=user-edit-not-from-template\n")

            with patch("app.settings.env_file._running_from_bundle", return_value=True), \
                 patch("app.settings.env_file.resolve_env_path", return_value=fake_env):
                env_path, created = ensure_env_file_exists()
                self.assertFalse(created)
                # User content preserved
                self.assertIn("user-edit-not-from-template", fake_env.read_text())

    def test_bundle_mode_handles_missing_template_gracefully(self):
        """If the bundled template can't be found, we don't crash; the
        Settings UI will let the user populate from scratch."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_env = Path(tmp) / ".env"
            with patch("app.settings.env_file._running_from_bundle", return_value=True), \
                 patch("app.settings.env_file.resolve_env_path", return_value=fake_env), \
                 patch("app.settings.env_file.resolve_env_template_path", return_value=None):
                env_path, created = ensure_env_file_exists()
                self.assertFalse(created)
                self.assertFalse(fake_env.exists())


class TestReadEnvFile(unittest.TestCase):
    def test_returns_empty_dict_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_env = Path(tmp) / ".env"
            with patch("app.settings.env_file.resolve_env_path", return_value=fake_env):
                self.assertEqual(read_env_file(), {})

    def test_parses_key_value_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_env = Path(tmp) / ".env"
            fake_env.write_text(
                'LLM_MODEL="anthropic/claude-sonnet-4-6"\n'
                'LLM_API_KEY_ANTHROPIC="sk-ant-abc"\n'
                'EMPTY_VAL=\n',
            )
            with patch("app.settings.env_file.resolve_env_path", return_value=fake_env):
                vals = read_env_file()
                self.assertEqual(vals["LLM_MODEL"], "anthropic/claude-sonnet-4-6")
                self.assertEqual(vals["LLM_API_KEY_ANTHROPIC"], "sk-ant-abc")
                # Present-but-empty keys stay distinguishable from absent ones
                self.assertIn("EMPTY_VAL", vals)

    def test_read_does_not_touch_os_environ(self):
        """read_env_file uses dotenv_values, not load_dotenv — pure
        parse, no side effects on the process environment."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_env = Path(tmp) / ".env"
            fake_env.write_text('LLM_MODEL="anthropic/claude-foo"\n')
            with patch("app.settings.env_file.resolve_env_path", return_value=fake_env):
                pre = os.environ.get("LLM_MODEL")
                read_env_file()
                post = os.environ.get("LLM_MODEL")
                self.assertEqual(pre, post)


class TestWriteEnvFile(unittest.TestCase):
    def test_creates_file_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_env = Path(tmp) / ".env"
            with patch("app.settings.env_file.resolve_env_path", return_value=fake_env):
                write_env_file({"LLM_MODEL": "anthropic/claude-x"})
                self.assertTrue(fake_env.exists())
                self.assertIn("LLM_MODEL", fake_env.read_text())

    def test_preserves_comments_when_updating(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_env = Path(tmp) / ".env"
            fake_env.write_text(
                "# This is the model setting\n"
                'LLM_MODEL="old-value"\n'
                "# This is the API key section\n"
                'LLM_API_KEY_ANTHROPIC="sk-ant-old"\n',
            )
            with patch("app.settings.env_file.resolve_env_path", return_value=fake_env):
                write_env_file({"LLM_MODEL": "anthropic/claude-new"})
                content = fake_env.read_text()
                self.assertIn("# This is the model setting", content)
                self.assertIn("# This is the API key section", content)
                self.assertIn("anthropic/claude-new", content)
                self.assertNotIn("old-value", content)
                # Other keys untouched
                self.assertIn("sk-ant-old", content)

    def test_appends_new_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_env = Path(tmp) / ".env"
            fake_env.write_text('LLM_MODEL="anthropic/claude-x"\n')
            with patch("app.settings.env_file.resolve_env_path", return_value=fake_env):
                write_env_file({"NEW_KEY": "new-value"})
                content = fake_env.read_text()
                self.assertIn("LLM_MODEL", content)
                self.assertIn("NEW_KEY", content)
                self.assertIn("new-value", content)

    def test_empty_value_removes_key(self):
        """Sending an empty string for a key removes it from the file
        entirely — matches the convention 'unset = absent line'."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_env = Path(tmp) / ".env"
            fake_env.write_text(
                'LLM_MODEL="anthropic/claude-x"\n'
                'LLM_API_KEY_ANTHROPIC="sk-ant-abc"\n',
            )
            with patch("app.settings.env_file.resolve_env_path", return_value=fake_env):
                write_env_file({"LLM_API_KEY_ANTHROPIC": ""})
                content = fake_env.read_text()
                self.assertIn("LLM_MODEL", content)
                self.assertNotIn("sk-ant-abc", content)

    def test_none_value_removes_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_env = Path(tmp) / ".env"
            fake_env.write_text('LLM_MODEL="anthropic/claude-x"\n')
            with patch("app.settings.env_file.resolve_env_path", return_value=fake_env):
                write_env_file({"LLM_MODEL": None})
                self.assertNotIn("anthropic/claude-x", fake_env.read_text())


class TestReloadEnvIntoProcess(unittest.TestCase):
    def test_reload_overrides_existing_env_values(self):
        """The 'Reload .env' UI button must pick up out-of-band edits
        even when the values are already present in os.environ."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_env = Path(tmp) / ".env"
            fake_env.write_text('TEST_RELOAD_VAR="new-value"\n')
            with patch("app.settings.env_file.resolve_env_path", return_value=fake_env), \
                 patch.dict(os.environ, {"TEST_RELOAD_VAR": "stale-value"}):
                self.assertEqual(os.environ["TEST_RELOAD_VAR"], "stale-value")
                reload_env_into_process()
                self.assertEqual(os.environ["TEST_RELOAD_VAR"], "new-value")

    def test_reload_handles_missing_file_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_env = Path(tmp) / ".env"
            with patch("app.settings.env_file.resolve_env_path", return_value=fake_env):
                # Should not raise
                result = reload_env_into_process()
                self.assertEqual(result, fake_env)


class TestDockerModeFallback(unittest.TestCase):
    """Docker can't bind-mount the host's .env without breaking the
    read-only rootfs threat model. The Settings UI falls back to
    showing os.environ (populated by Compose's env_file: injection)
    in read-only mode."""

    def test_env_editable_true_outside_docker(self):
        with patch("app.settings.env_file._running_in_docker", return_value=False):
            self.assertTrue(env_editable())

    def test_env_editable_false_in_docker(self):
        with patch("app.settings.env_file._running_in_docker", return_value=True):
            self.assertFalse(env_editable())

    def test_read_managed_env_uses_file_outside_docker(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_env = Path(tmp) / ".env"
            fake_env.write_text('LLM_MODEL="from-file"\n')
            with patch("app.settings.env_file._running_in_docker", return_value=False), \
                 patch("app.settings.env_file.resolve_env_path", return_value=fake_env), \
                 patch.dict(os.environ, {"LLM_MODEL": "from-environ"}, clear=True):
                # File is authoritative — os.environ ignored.
                vals = read_managed_env({"LLM_MODEL"})
                self.assertEqual(vals["LLM_MODEL"], "from-file")

    def test_read_managed_env_uses_os_environ_in_docker(self):
        with patch("app.settings.env_file._running_in_docker", return_value=True), \
             patch.dict(os.environ, {
                 "LLM_MODEL": "from-environ",
                 "PATH": "/usr/bin",
             }, clear=True):
            vals = read_managed_env({"LLM_MODEL"})
            self.assertEqual(vals["LLM_MODEL"], "from-environ")
            # Keys outside the managed set don't leak.
            self.assertNotIn("PATH", vals)

    def test_read_managed_env_in_docker_omits_absent_keys(self):
        with patch("app.settings.env_file._running_in_docker", return_value=True), \
             patch.dict(os.environ, {"LLM_MODEL": "x"}, clear=True):
            vals = read_managed_env({"LLM_MODEL", "ABSENT_KEY"})
            self.assertEqual(vals, {"LLM_MODEL": "x"})

    def test_resolve_env_path_for_display_in_docker(self):
        with patch("app.settings.env_file._running_in_docker", return_value=True):
            # Host-side path (relative to compose file), not /app/.env.
            self.assertEqual(resolve_env_path_for_display(), "agent/.env")

    def test_resolve_env_path_for_display_outside_docker(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_env = Path(tmp) / ".env"
            with patch("app.settings.env_file._running_in_docker", return_value=False), \
                 patch("app.settings.env_file.resolve_env_path", return_value=fake_env):
                self.assertEqual(resolve_env_path_for_display(), str(fake_env))


class TestResolveEnvTemplatePath(unittest.TestCase):
    def test_finds_bundled_template_via_meipass(self):
        with tempfile.TemporaryDirectory() as tmp:
            meipass = Path(tmp)
            (meipass / ".env.template").write_text("TEMPLATE\n")
            with patch.object(sys, "_MEIPASS", str(meipass), create=True):
                result = resolve_env_template_path()
                self.assertEqual(result, meipass / ".env.template")

    def test_falls_back_to_agent_root_template(self):
        from app.data_paths import AGENT_ROOT
        # We don't patch _MEIPASS here, so the bundle path returns None
        # and we fall through to the source-layout check. The agent's
        # real .env.template is committed, so this should always succeed.
        result = resolve_env_template_path()
        self.assertEqual(result, AGENT_ROOT / ".env.template")


if __name__ == "__main__":
    unittest.main()
