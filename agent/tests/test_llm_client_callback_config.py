"""Regression test for litellm.success_callback / failure_callback
disruption in LLMClient.completion.

Prior to the fix, every call to LLMClient.completion reset
litellm.success_callback and litellm.failure_callback to empty lists.
Any other code in the process (observability plugin, future telemetry
hook) that registered a callback between two agent LLM calls would
silently lose it on the next call.

The contract we want: callbacks are touched at most once per process,
not per call.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from app.llm.client import LLMClient


def _fake_litellm_module() -> SimpleNamespace:
    """Build a stand-in litellm module with only the attributes
    LLMClient.completion touches.
    """

    response = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content="hi", tool_calls=None),
        )],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            prompt_tokens_details=None,
        ),
    )

    async def fake_acompletion(**kwargs):
        return response

    return SimpleNamespace(
        acompletion=fake_acompletion,
        completion_cost=lambda **kw: 0.0,
        success_callback=[],
        failure_callback=[],
    )


class TestLiteLLMCallbackConfig(unittest.TestCase):

    def setUp(self) -> None:
        """Reset the one-shot flag so tests are order-independent."""
        import app.llm.client as llm_client
        llm_client._LITELLM_CONFIGURED = False

    def test_first_call_clears_preexisting_callbacks(self) -> None:
        """The first completion in a process starts from a clean slate —
        LiteLLM's own default callbacks (or any accidental holdover from
        imports) are cleared so the agent doesn't inherit surprises.
        """
        fake = _fake_litellm_module()
        fake.success_callback = ["inherited_from_somewhere"]
        fake.failure_callback = ["inherited_from_somewhere"]

        with mock.patch.dict("sys.modules", {"litellm": fake}):
            client = LLMClient(model="anthropic/claude-sonnet-4-6", api_key="x")
            asyncio.run(client.completion(messages=[{"role": "user", "content": "hi"}]))

            self.assertEqual(fake.success_callback, [])
            self.assertEqual(fake.failure_callback, [])

    def test_callbacks_survive_a_second_completion_call(self) -> None:
        """The observable guarantee: if a plugin registers a callback
        after the first LLM call, the second call must not wipe it.
        """
        fake = _fake_litellm_module()

        with mock.patch.dict("sys.modules", {"litellm": fake}):
            client = LLMClient(model="anthropic/claude-sonnet-4-6", api_key="x")

            asyncio.run(client.completion(messages=[{"role": "user", "content": "hi"}]))

            sentinel = object()
            fake.success_callback.append(sentinel)
            fake.failure_callback.append(sentinel)

            asyncio.run(client.completion(messages=[{"role": "user", "content": "hi"}]))

            self.assertIn(
                sentinel, fake.success_callback,
                "Second completion call wiped a success_callback set between calls",
            )
            self.assertIn(
                sentinel, fake.failure_callback,
                "Second completion call wiped a failure_callback set between calls",
            )


if __name__ == "__main__":
    unittest.main()
