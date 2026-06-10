"""Generic backend reachability poller.

Generalises the old TTS-only loop: every *pollable* backend (SearXNG,
TTS) is re-probed on a timer and its status persisted; non-pollable
backends (Brave/Tavily — a real probe spends a query) are never touched.
"""
import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import requests  # noqa: E402

from app.runtime.backend_health import _poll_once, _run_loop  # noqa: E402
from app.settings import get_settings, reset_settings_for_tests  # noqa: E402
from app.settings.validation import ConfigValidator  # noqa: E402


class TestPollOnce(unittest.TestCase):
    def setUp(self):
        reset_settings_for_tests()

    def tearDown(self):
        reset_settings_for_tests()

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "WEB_SEARCH_PROVIDER": "searxng",
            "SEARXNG_URL": "http://s:8080",
        },
        clear=True,
    )
    def test_pollable_searxng_probed_persisted_and_emits_on_transition(self):
        validator = ConfigValidator()
        calls = []
        resp = MagicMock(status_code=200)
        with patch.object(requests, "get", return_value=resp):
            _poll_once(get_settings(), validator,
                       on_change=lambda: calls.append(1))
        web = next(b for b in validator.current().backends
                   if b.backend == "web-search")
        self.assertTrue(web.ok)
        self.assertEqual(len(calls), 1)  # None -> True is a transition

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "WEB_SEARCH_PROVIDER": "searxng",
            "SEARXNG_URL": "http://s:8080",
        },
        clear=True,
    )
    def test_no_emit_when_status_steady(self):
        validator = ConfigValidator()
        resp = MagicMock(status_code=200)
        with patch.object(requests, "get", return_value=resp):
            _poll_once(get_settings(), validator)  # first add (True)
            calls = []
            _poll_once(get_settings(), validator,
                       on_change=lambda: calls.append(1))  # steady
        self.assertEqual(calls, [])

    @patch.dict(
        os.environ,
        {
            "LLM_MODEL": "anthropic/claude-x",
            "WEB_SEARCH_PROVIDER": "brave",
            "BRAVE_API_KEY": "k",
        },
        clear=True,
    )
    def test_non_pollable_brave_never_probed(self):
        """A real Brave probe spends a query, so the loop must skip it."""
        validator = ConfigValidator()
        with patch.object(requests, "get") as mock_get:
            _poll_once(get_settings(), validator)
        self.assertEqual(validator.current().backends, [])
        mock_get.assert_not_called()


class TestRunLoop(unittest.TestCase):
    def test_polls_then_exits_on_stop_event(self):
        stop = threading.Event()
        ticks = []

        def fake_poll(settings, validator, *, on_change=None):
            ticks.append(1)
            stop.set()  # exit after one tick via stop_event.wait()

        with patch("app.runtime.backend_health._poll_once",
                   side_effect=fake_poll), \
             patch("app.settings.get_settings", return_value=object()), \
             patch("app.settings.validation.get_validator",
                   return_value=object()):
            _run_loop(stop_event=stop, on_change=None, interval_seconds=60)
        self.assertEqual(ticks, [1])


if __name__ == "__main__":
    unittest.main()
