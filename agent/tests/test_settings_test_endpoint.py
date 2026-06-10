"""Tests for the per-provider API-key / connectivity validator.

We mock ``requests.get`` so no real HTTP calls happen. The tests
verify our dispatch + status-code interpretation + error-shape
uniformity — the underlying provider APIs themselves aren't our
problem.
"""

# Import requests *before* installing the common test stubs — the stub
# module installs a minimal `requests` shim if one isn't already
# present, and we need the real package here for `requests.exceptions`.
import unittest
from unittest.mock import MagicMock, patch

import requests  # noqa: F401  (imported for side effect — populate sys.modules)

try:
    from tests.stub_modules import install_common_test_stubs
except ModuleNotFoundError:
    from stub_modules import install_common_test_stubs

install_common_test_stubs()

from app.settings.test_endpoint import handle_test


def _ok_response(status=200, json_data=None):
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.json.return_value = json_data or {}
    return r


class TestDispatch(unittest.TestCase):
    def test_missing_provider_returns_error(self):
        result = handle_test({})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "other")

    def test_unknown_provider_with_no_key_returns_error(self):
        result = handle_test({"provider": "made-up"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["provider"], "made-up")
        self.assertEqual(result["error_kind"], "auth_failed")

    def test_unknown_provider_with_key_saves_as_not_validated(self):
        """LiteLLM supports many providers we don't have free auth
        checks for. Rather than failing the test, we save the key and
        mark it not_validated so the user can still trial it."""
        result = handle_test({"provider": "openrouter", "api_key": "sk-or-x"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["not_validated"])
        self.assertIn("first chat request", result["detail"].lower())

    def test_provider_is_normalised_to_lowercase(self):
        with patch("app.settings.test_endpoint.requests.get", return_value=_ok_response()):
            result = handle_test({"provider": "ANTHROPIC", "api_key": "sk-x"})
            self.assertTrue(result["ok"])


class TestAnthropic(unittest.TestCase):
    @patch("app.settings.test_endpoint.requests.get")
    def test_200_returns_ok(self, mock_get):
        mock_get.return_value = _ok_response(200)
        result = handle_test({"provider": "anthropic", "api_key": "sk-ant-abc"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "anthropic")
        self.assertIn("valid", result["detail"].lower())

    @patch("app.settings.test_endpoint.requests.get")
    def test_401_returns_auth_failed(self, mock_get):
        mock_get.return_value = _ok_response(401)
        result = handle_test({"provider": "anthropic", "api_key": "bad"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "auth_failed")

    @patch("app.settings.test_endpoint.requests.get")
    def test_500_returns_other_error(self, mock_get):
        mock_get.return_value = _ok_response(500)
        result = handle_test({"provider": "anthropic", "api_key": "sk-ant-x"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "other")

    def test_empty_api_key_short_circuits(self):
        result = handle_test({"provider": "anthropic", "api_key": ""})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "auth_failed")

    @patch("app.settings.test_endpoint.requests.get")
    def test_includes_correct_auth_headers(self, mock_get):
        mock_get.return_value = _ok_response(200)
        handle_test({"provider": "anthropic", "api_key": "sk-ant-abc"})
        call_kwargs = mock_get.call_args.kwargs
        self.assertEqual(call_kwargs["headers"]["x-api-key"], "sk-ant-abc")
        self.assertIn("anthropic-version", call_kwargs["headers"])


class TestAnthropicModelTuple(unittest.TestCase):
    """When ``model`` is in the payload, the endpoint switches to the
    full provider/model/key tuple check so per-row Test buttons report
    both auth and model-not-found outcomes."""

    @patch("app.settings.validation.requests.get")
    def test_alias_matches_dated_snapshot(self, mock_get):
        mock_get.return_value = _ok_response(
            200, {"data": [{"id": "claude-haiku-4-5-20251001"}]},
        )
        result = handle_test({
            "provider": "anthropic",
            "model": "claude-haiku-4-5",
            "api_key": "sk-ant-x",
        })
        self.assertTrue(result["ok"])

    @patch("app.settings.validation.requests.get")
    def test_unknown_model_returns_not_found(self, mock_get):
        mock_get.return_value = _ok_response(
            200, {"data": [{"id": "claude-other"}]},
        )
        result = handle_test({
            "provider": "anthropic",
            "model": "claude-typo",
            "api_key": "sk-ant-x",
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "not_found")


class TestOpenAI(unittest.TestCase):
    @patch("app.settings.test_endpoint.requests.get")
    def test_200_returns_ok(self, mock_get):
        mock_get.return_value = _ok_response(200)
        result = handle_test({"provider": "openai", "api_key": "sk-abc"})
        self.assertTrue(result["ok"])

    @patch("app.settings.test_endpoint.requests.get")
    def test_401_returns_auth_failed(self, mock_get):
        mock_get.return_value = _ok_response(401)
        result = handle_test({"provider": "openai", "api_key": "bad"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "auth_failed")

    @patch("app.settings.test_endpoint.requests.get")
    def test_uses_bearer_auth(self, mock_get):
        mock_get.return_value = _ok_response(200)
        handle_test({"provider": "openai", "api_key": "sk-abc"})
        headers = mock_get.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer sk-abc")


class TestGemini(unittest.TestCase):
    @patch("app.settings.test_endpoint.requests.get")
    def test_200_returns_ok(self, mock_get):
        mock_get.return_value = _ok_response(200)
        result = handle_test({"provider": "gemini", "api_key": "AIza123"})
        self.assertTrue(result["ok"])

    @patch("app.settings.test_endpoint.requests.get")
    def test_key_passed_as_query_param(self, mock_get):
        mock_get.return_value = _ok_response(200)
        handle_test({"provider": "gemini", "api_key": "AIza123"})
        self.assertEqual(mock_get.call_args.kwargs["params"]["key"], "AIza123")


class TestOllama(unittest.TestCase):
    @patch("app.settings.test_endpoint.requests.get")
    def test_200_with_models_returns_count(self, mock_get):
        mock_get.return_value = _ok_response(
            200, {"models": [{"name": "llama3"}, {"name": "gemma3"}]},
        )
        result = handle_test({"provider": "ollama", "url": "http://localhost:11434"})
        self.assertTrue(result["ok"])
        self.assertIn("2", result["detail"])

    @patch("app.settings.test_endpoint.requests.get")
    def test_default_url_when_unspecified(self, mock_get):
        mock_get.return_value = _ok_response(200, {"models": []})
        handle_test({"provider": "ollama"})
        self.assertIn("11434", mock_get.call_args.args[0])

    @patch("app.settings.test_endpoint.requests.get")
    def test_trailing_slash_normalised(self, mock_get):
        mock_get.return_value = _ok_response(200, {"models": []})
        handle_test({"provider": "ollama", "url": "http://host:1234/"})
        url = mock_get.call_args.args[0]
        self.assertEqual(url, "http://host:1234/api/tags")

    @patch("app.settings.test_endpoint.requests.get")
    def test_ollama_chat_provider_alias_works(self, mock_get):
        mock_get.return_value = _ok_response(200, {"models": []})
        result = handle_test({"provider": "ollama_chat", "url": "http://x:1"})
        self.assertTrue(result["ok"])


class TestSearXNG(unittest.TestCase):
    @patch("app.settings.test_endpoint.requests.get")
    def test_200_returns_ok(self, mock_get):
        mock_get.return_value = _ok_response(200)
        result = handle_test({"provider": "searxng", "url": "http://localhost:8888"})
        self.assertTrue(result["ok"])

    def test_empty_url_short_circuits(self):
        result = handle_test({"provider": "searxng", "url": ""})
        self.assertFalse(result["ok"])


class TestBrave(unittest.TestCase):
    def test_returns_not_validated_with_ok(self):
        result = handle_test({"provider": "brave", "api_key": "BSA-x"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["not_validated"])
        self.assertIn("auth check", result["detail"].lower())

    def test_empty_key_fails(self):
        result = handle_test({"provider": "brave", "api_key": ""})
        self.assertFalse(result["ok"])


class TestTavily(unittest.TestCase):
    def test_returns_not_validated_with_ok(self):
        result = handle_test({"provider": "tavily", "api_key": "tvly-x"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["not_validated"])


class TestTts(unittest.TestCase):
    """``provider=tts`` does a real TCP connect to the configured
    ``host:port``. Anything that answers SYN-ACK is reachable; we
    don't try the full F5-TTS handshake as a healthcheck."""

    def test_reachable_when_socket_accepts(self):
        import socket

        class _DummyConn:
            def __enter__(self): return self
            def __exit__(self, *_): pass

        with patch.object(socket, "create_connection", return_value=_DummyConn()):
            result = handle_test({"provider": "tts", "url": "localhost:9998"})
        self.assertTrue(result["ok"])
        self.assertIn("localhost:9998", result["detail"])

    def test_refused_returns_network_error(self):
        import socket
        with patch.object(
            socket, "create_connection",
            side_effect=ConnectionRefusedError("refused"),
        ):
            result = handle_test({"provider": "tts", "url": "localhost:9998"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "network")

    def test_http_scheme_silently_stripped(self):
        """Any leading scheme is normalised — the user shouldn't see a
        parse error when the .env still has http://."""
        import socket

        class _DummyConn:
            def __enter__(self): return self
            def __exit__(self, *_): pass

        with patch.object(socket, "create_connection", return_value=_DummyConn()):
            result = handle_test({"provider": "tts", "url": "http://localhost:9998"})
        self.assertTrue(result["ok"])
        self.assertIn("localhost:9998", result["detail"])

    def test_empty_url_rejected(self):
        result = handle_test({"provider": "tts", "url": ""})
        self.assertFalse(result["ok"])


class TestNetworkErrors(unittest.TestCase):
    @patch("app.settings.test_endpoint.requests.get")
    def test_timeout_returns_network_error(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout()
        result = handle_test({"provider": "anthropic", "api_key": "sk-x"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "network")
        self.assertIn("timed out", result["detail"].lower())

    @patch("app.settings.test_endpoint.requests.get")
    def test_unexpected_exception_caught(self, mock_get):
        mock_get.side_effect = RuntimeError("totally unexpected")
        result = handle_test({"provider": "anthropic", "api_key": "sk-x"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "other")


if __name__ == "__main__":
    unittest.main()
