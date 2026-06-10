import unittest

from usage_metrics import UsageTracker, collect_usage_metrics


class _FakeInput:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, mode="json"):
        _ = mode
        return {"payload": self.payload}


class _FakeOutput:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, mode="json"):
        _ = mode
        return {"payload": self.payload}


class _FakeAgent:
    def __init__(self, usage):
        self.last_response = {"usage": usage}


class TestUsageCollection(unittest.TestCase):
    def test_collect_usage_prefers_provider_fields(self):
        agent = _FakeAgent({"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20})
        usage = collect_usage_metrics(
            agent=agent,
            agent_input=_FakeInput("hello"),
            agent_output=_FakeOutput("world"),
        )
        self.assertEqual(usage["input_tokens"], 12)
        self.assertEqual(usage["output_tokens"], 8)
        self.assertEqual(usage["total_tokens"], 20)
        self.assertEqual(usage["source"], "provider")

    def test_collect_usage_falls_back_to_estimates(self):
        usage = collect_usage_metrics(
            agent=object(),
            agent_input=_FakeInput("a" * 40),
            agent_output=_FakeOutput("b" * 20),
        )
        self.assertGreater(usage["input_tokens"], 0)
        self.assertGreater(usage["output_tokens"], 0)
        self.assertEqual(usage["total_tokens"], usage["input_tokens"] + usage["output_tokens"])
        self.assertEqual(usage["source"], "estimated_prompt")


class TestUsageTracker(unittest.TestCase):
    def test_record_updates_session_and_tpm(self):
        tracker = UsageTracker(window_seconds=60)
        first = tracker.record(
            agent_name="Coordinator Agent",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            source="provider",
        )
        second = tracker.record(
            agent_name="Report Agent",
            input_tokens=30,
            output_tokens=20,
            total_tokens=50,
            source="provider",
        )
        self.assertEqual(first["session_totals"]["total_tokens"], 150)
        self.assertEqual(second["session_totals"]["total_tokens"], 200)
        self.assertEqual(second["tokens_per_minute"]["input_tokens"], 130)
        self.assertEqual(second["tokens_per_minute"]["output_tokens"], 70)
        self.assertEqual(second["tokens_per_minute"]["total_tokens"], 200)
        self.assertEqual(second["requests_per_minute"]["request_count"], 2)


if __name__ == "__main__":
    unittest.main()
