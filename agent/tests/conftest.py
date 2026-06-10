"""Shared test fixtures — keeps agent/logs/ clean during test runs."""

import atexit
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values, load_dotenv

# Redirect SWARPIUS_DATA_DIR to a per-session temp dir BEFORE anything
# else imports app modules — data_dir() is read at module import time
# in some places. Without this, a test constructing RuntimeState() can
# trigger a save (via zone reconciliation) that overwrites the user's
# real zone_aliases.json / group_names.json / zone_group_ids.json.
# Temp dir is created fresh per session and torn down at exit.
_AGENT_ROOT = Path(__file__).resolve().parent.parent
_test_env = _AGENT_ROOT / ".env.test"
_env_file = _AGENT_ROOT / ".env"

# Resolve the user's real data dir BEFORE we overwrite the env var —
# needed by live tests to find the saved Roon auth token. Mirrors the
# precedence in app.data_paths.data_dir(): explicit env > .env.test
# > .env > default agent/data/. Use dotenv_values so parsing doesn't
# mutate os.environ.
_shell_data_dir = os.environ.get("SWARPIUS_DATA_DIR", "")
_env_test_vals = dotenv_values(_test_env)
_env_vals = dotenv_values(_env_file)
_raw_real_data_dir = (
    _shell_data_dir
    or _env_test_vals.get("SWARPIUS_DATA_DIR", "")
    or _env_vals.get("SWARPIUS_DATA_DIR", "")
)
if _raw_real_data_dir:
    _real_path = Path(_raw_real_data_dir)
    _REAL_DATA_DIR = _real_path if _real_path.is_absolute() else _AGENT_ROOT / _real_path
else:
    _REAL_DATA_DIR = _AGENT_ROOT / "data"

_TEST_DATA_DIR = tempfile.mkdtemp(prefix="swarpius-test-data-")
os.environ["SWARPIUS_DATA_DIR"] = _TEST_DATA_DIR
(Path(_TEST_DATA_DIR) / "config").mkdir(parents=True, exist_ok=True)

# Tear down at process exit. atexit fires even if pytest crashes, which a
# session-scoped fixture would not — important since the temp dir
# sometimes lands inside the project tree if TMPDIR is unset.
atexit.register(shutil.rmtree, _TEST_DATA_DIR, ignore_errors=True)

# Load test-specific env first (uses roon.core hostname for sandbox
# compatibility), then fall back to agent/.env for any unset vars.
# ``override=False`` so neither dotenv file can stomp the temp data dir.
load_dotenv(_test_env, override=False)
load_dotenv(_env_file, override=False)
# Extra safety: dotenv with override=False shouldn't touch
# SWARPIUS_DATA_DIR but make sure regardless.
os.environ["SWARPIUS_DATA_DIR"] = _TEST_DATA_DIR

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

import pytest  # noqa: E402

from app.runtime.request_context import clear_request_id, set_request_id  # noqa: E402
from app.runtime.request_logger import NullRequestLogger  # noqa: E402
from app.runtime.server_logger import (  # noqa: E402
    NullServerLogger,
    ServerLogger,
    set_server_logger,
)


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Force a fresh ``Settings`` load for every test so env-mutating
    tests (``patch.dict(os.environ, ...)``) see their changes reflected
    on the next ``get_settings()`` call. The locked-at-startup invariant
    that production code relies on stays intact within a single test.

    The validator singleton is reset for the same reason — tests that
    exercise the validator shouldn't leak state between cases."""
    from app.settings import reset_settings_for_tests
    from app.settings.validation import reset_validator_for_tests
    reset_settings_for_tests()
    reset_validator_for_tests()
    yield
    reset_settings_for_tests()
    reset_validator_for_tests()


@pytest.fixture(autouse=True)
def _configure_loggers(request, monkeypatch):
    """Suppress disk writes for offline tests; enable server logs for live Roon tests."""
    monkeypatch.setattr(
        "app.coordinator.request_flow.RequestLogger",
        lambda request_id, **kwargs: NullRequestLogger(request_id),
    )
    is_live = any(m.name == "live_roon" for m in request.node.iter_markers())
    if is_live:
        _configure_loggers._test_seq = getattr(_configure_loggers, "_test_seq", 0) + 1
        test_request_id = f"rq-t01-{_configure_loggers._test_seq:04d}"
        logger = ServerLogger()
        set_server_logger(logger)
        set_request_id(test_request_id)
        logger.set_request_id(test_request_id)
        yield
        clear_request_id()
        logger.set_request_id(None)
    else:
        set_server_logger(NullServerLogger())
        yield


# ── Shared live Roon connection ─────────────────────────────────────
#
# All live_roon tests share a single RoonConnection for the session.
# Roon only allows one authenticated connection per set of credentials,
# so creating per-file connections causes conflicts.

_live_roon_connection = None


def get_live_roon():
    """Return the shared RoonConnection for live tests.

    Called from setUpClass / setup_class in live test files instead of
    each file creating its own connection.
    """
    return _live_roon_connection


@pytest.fixture(scope="session", autouse=True)
def _shared_roon_connection(request):
    """Create a single RoonConnection shared across all live_roon tests.

    Live tests register against Roon as a distinct extension
    (``swarpius_test``, "Swarpius Test") with its own persistent token
    files under the user's real ``agent/data/config/`` directory.
    This means:

      * Tests never share auth state with a running production agent,
        so the two can run side-by-side without one kicking the other
        off Roon.
      * The token persists across test sessions — re-auth happens at
        most once per Roon-side invalidation, not on every run.
      * The user sees one "Swarpius Test" entry in Roon Settings,
        not a fresh one accumulating per test session.
    """
    global _live_roon_connection

    has_live = any(
        item.get_closest_marker("live_roon")
        for item in request.session.items
    )
    if not has_live:
        yield
        return

    from roon_core.connection import RoonConnection

    core_url = os.environ.get("ROON_CORE_URL", "")
    zone = os.environ.get("DEFAULT_ROON_ZONE", "")
    host, port = None, None
    if core_url:
        parsed = urlparse(core_url)
        host = parsed.hostname
        port = parsed.port

    test_app_info = {
        "extension_id": "swarpius_test",
        "display_name": "Swarpius Test",
        "display_version": "1.0.0",
        "publisher": "Paraseva Ltd",
        "email": "hello@paraseva.ai",
    }
    test_config_dir = _REAL_DATA_DIR / "config"
    test_config_dir.mkdir(parents=True, exist_ok=True)

    _live_roon_connection = RoonConnection(
        default_zone=zone or None,
        roon_core_host=host,
        roon_core_port=port,
        app_info=test_app_info,
        core_id_path=test_config_dir / "roon_test_core_id",
        token_path=test_config_dir / "roon_test_core_token",
    )
    yield _live_roon_connection
    _live_roon_connection = None
