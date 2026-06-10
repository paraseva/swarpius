"""Tests for redact_secrets — strips API keys and bearer tokens.

The agent's tool-loop error path logs LiteLLM exceptions to ``LOG_FILE``
and emits the same text to the WS ``errors`` and ``llm-diagnostics``
channels. LiteLLM exception strings can carry partial keys from
provider HTTP error responses (URL query params, Authorization
headers, x-api-key headers). This helper redacts them at the
chokepoint before any emit/log.
"""

from __future__ import annotations

import unittest

from app.io.redact import redact_secrets


class TestRedactSecrets(unittest.TestCase):

    def test_none_returned_unchanged(self):
        self.assertIsNone(redact_secrets(None))

    def test_empty_returned_unchanged(self):
        self.assertEqual(redact_secrets(""), "")

    def test_plain_text_unchanged(self):
        self.assertEqual(
            redact_secrets("Connection refused to api.example.com"),
            "Connection refused to api.example.com",
        )

    def test_openai_key_redacted(self):
        text = "401 Unauthorized: api_key=sk-proj-GLZYTBC0123abcDEFRSTuvw_xyz123"
        result = redact_secrets(text)
        self.assertNotIn("sk-proj-GLZYTBC", result)

    def test_anthropic_key_redacted(self):
        text = "AuthError: header x-api-key='sk-ant-api03-zuYtO0123abcDEFRSTuvw_xyz123'"
        result = redact_secrets(text)
        self.assertNotIn("sk-ant-api03-zuYtO", result)

    def test_google_key_redacted(self):
        text = (
            "GET https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.0-flash:generateContent?key=AIzaSyC2H1qF55abcDEFRSTuvw_xyz1234567 401"
        )
        result = redact_secrets(text)
        self.assertNotIn("AIzaSyC2H1qF55", result)

    def test_bearer_token_redacted(self):
        text = "Authorization: Bearer abc123def456ghijklmnopqrstuv"
        result = redact_secrets(text)
        self.assertNotIn("abc123def456ghijklmnopqrstuv", result)

    def test_url_key_param_redacted(self):
        text = "Failed: https://api.example.com/v1/foo?key=AIzaSyABCDEF12345abcDEFGHIJK_xyz9876543"
        result = redact_secrets(text)
        self.assertNotIn("AIzaSyABCDEF12345abcDEFGHIJK", result)

    def test_redaction_preserves_surrounding_context(self):
        text = "401 Unauthorized: invalid key sk-ant-api03-zuYtO0123abcDEFRSTuvw at endpoint /v1/messages"
        result = redact_secrets(text)
        self.assertIn("401 Unauthorized", result)
        self.assertIn("/v1/messages", result)
        self.assertNotIn("sk-ant-api03-zuYtO", result)

    def test_multiple_secrets_all_redacted(self):
        text = (
            "Mixed: sk-proj-xxxabc123XYZ_DEFRSTUV "
            "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 "
            "?key=AIzaSyABCDEF12345abcDEFGHIJK_xyz9876543"
        )
        result = redact_secrets(text)
        self.assertNotIn("sk-proj-xxxabc", result)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", result)
        self.assertNotIn("AIzaSyABCDEF12345abcDEFGHIJK", result)

    def test_short_lookalike_not_redacted(self):
        # 'sk-foo' (only 6 chars after sk-) shouldn't trigger
        text = "Reference: sk-foo is a placeholder"
        result = redact_secrets(text)
        self.assertEqual(result, text)
