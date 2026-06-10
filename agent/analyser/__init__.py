"""LLM-based conversation-quality analyser.

Reads completed conversation logs from ``data_dir()/logs/conversation/``,
classifies failure modes against a fixed taxonomy via LiteLLM, and
writes per-conversation ``analysis.yaml`` files plus aggregated
``metrics.jsonl`` rows.

Enabled at runtime via ``ENABLE_PASSIVE_ANALYSER=true``. The scheduled
loop runs in a daemon thread spawned by the agent at startup; the
on-demand "Scan & Analyse" button in the web UI invokes the same
``run_scan`` / ``run_single_conversation_analysis`` entry points
in-process via ``asyncio.to_thread``.
"""
