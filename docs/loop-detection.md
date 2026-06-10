# Coordinator Loop Detection

## Current implementation

Loop detection is implemented in `tool_loop.py` as a simple consecutive-call checker. After each tool execution step, `_detect_loop()` compares the last two tool calls — if they have identical tool names and arguments, a system message is injected:

> "You already tried this exact approach and got the same result. Try a different approach or respond to the user."

This gives the model a chance to break out of the loop. If it continues repeating, the hard step limit (default 12, configurable per model via `model_profiles.yaml`) terminates the loop unconditionally.

A separate soft nudge is injected at a configurable step (default 8, also overridable per model via `soft_nudge_step` in `model_profiles.yaml`) to encourage the model to wrap up if it's taking many steps, regardless of whether looping is detected.

## Why this is sufficient

The simple two-call checker catches the most common failure mode: the model retrying an identical search or action that already failed. More sophisticated detection (output hashing, sequence matching, lookback windows) was considered but not implemented because:

- Native tool-calling models rarely produce long repetitive sequences — they typically either fix their approach after one failure or get stuck on the same call
- The soft nudge and hard limit provide additional safety nets
- In practice (observed across extended periods of Sonnet testing), the max step count reached was 8 out of 12, with an average of 2.4 steps per request

## Future considerations

If a model is observed producing varied-but-unproductive sequences (e.g. searching for slightly different terms that all return empty), Level 2 detection (same tool + same output) could be added. The current architecture makes this straightforward — `ToolExecution` already records both arguments and results.
