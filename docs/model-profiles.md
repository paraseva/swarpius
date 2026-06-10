# Model Profiles

Swarpius uses a YAML configuration file to tune LLM behaviour per model and provider. This lets you adjust temperature, sampling parameters, coordinator loop limits, and provider-specific flags without changing code.

## How it works

When Swarpius makes an LLM call, it matches the full model string (e.g. `ollama_chat/gemma4:26b`) against the profiles in `agent/model_profiles.yaml`. The first matching profile is applied. If no profile matches, the defaults are used.

The model string comes from the `LLM_MODEL` env var (or a per-agent override like `LLM_MODEL_ARBITER`), which is always in `provider/model` format.

## Configuration file

The config file lives at `agent/model_profiles.yaml`. It has two sections:

### Defaults

Applied when no profile pattern matches:

```yaml
defaults:
  temperature: 0.0
```

### Profiles

Each profile has a `pattern` (regex matched against the full model string) and any combination of tuning parameters:

```yaml
profiles:
  - pattern: "claude.*sonnet"
    max_coordinator_steps: 12
    soft_nudge_step: 8

  - pattern: "ollama"
    generation_params:
      think: false
```

**Order matters** — first match wins. Put specific profiles (provider + model) before general ones (model only or provider only).

### Available fields

| Field | Type | Description |
|---|---|---|
| `pattern` | string (regex) | Matched case-insensitively against the full `provider/model` string |
| `temperature` | float | Sampling temperature (default: 0.0) |
| `temperature_lock` | bool | Set to `true` for models that reject any temperature override at the API (e.g. GPT-5 requires the API default). When `true`, the profile's `temperature` value carries through unchanged — including in the passive analyser, which otherwise pins temperature to 0 for determinism. Default: `false`. |
| `top_p` | float | Nucleus sampling threshold |
| `max_coordinator_steps` | int | Maximum tool-loop iterations before forcing a response (default: 12) |
| `soft_nudge_step` | int | Step at which the model is nudged to wrap up (default: 8) |
| `generation_params` | dict | Arbitrary key-value pairs passed through to the LLM call (e.g. `think`, `top_k`). Provider-specific flags go here. |

## Customising tuning on an installed build

> Running from source? Just edit `agent/model_profiles.yaml` directly — the rest of this section does not apply to you.

The installed (packaged) app carries its own `model_profiles.yaml` inside the application, and that copy is replaced every time you update — so changes made to it would be lost. Instead, create your own copy in Swarpius's data folder. That copy is left untouched by updates and takes priority over the built-in settings.

To do this:

1. **Open your data folder.** Its location depends on your system:

   | OS | Folder |
   |---|---|
   | Windows | `%LOCALAPPDATA%\Swarpius\` |
   | macOS | `~/Library/Application Support/Swarpius/` |
   | Linux | `~/.local/share/swarpius/` |

2. **Create a file named `model_profiles.yaml`** in that folder. Any plain-text editor (Notepad, TextEdit, and so on) will do.

3. **Add only the setting you want to change.** There is no need to copy the whole built-in file — anything you leave out keeps its normal value. For example, to stop sending the `temperature` setting to a model whose provider has dropped support for it:

   ```yaml
   profiles:
     - pattern: "anthropic/claude-opus-4-9"
       temperature: null
   ```

4. **Restart Swarpius.** Your file now overrides the built-in settings: the `profiles` entries you list are matched first, and any `defaults` you set replace the built-in defaults.

## Pattern examples

Patterns are Python regex, matched case-insensitively against the full model string.

| Pattern | Matches | Doesn't match |
|---|---|---|
| `ollama.*gemma4` | `ollama_chat/gemma4:26b`, `ollama/gemma4` | `hosted_vllm/gemma4:26b` |
| `gemma4` | `ollama_chat/gemma4:26b`, `hosted_vllm/gemma4` | `ollama/gemma3:27b` |
| `ollama` | `ollama/anything`, `ollama_chat/anything` | `anthropic/claude-sonnet-4-6` |
| `anthropic/` | `anthropic/claude-sonnet-4-6` | `ollama/anything` |
| `claude.*sonnet` | `anthropic/claude-sonnet-4-6` | `anthropic/claude-haiku-4-5` |

## Coordinator loop limits

The `max_coordinator_steps` and `soft_nudge_step` fields control how many tool-loop iterations Swarpius gets per request. If a profile doesn't set these, the defaults apply (12 and 8 respectively).

Larger models that can use more steps productively can be given higher limits:

```yaml
  - pattern: "my-big-model"
    max_coordinator_steps: 16
    soft_nudge_step: 11
```

Smaller models that tend to spin their wheels should keep lower limits. The defaults are intentionally conservative.

## Released defaults

The default `model_profiles.yaml` ships profiles for the models we've tested:

- **Sonnet** (`claude.*sonnet`): `temperature 0.0`, 12 steps / nudge 8 — makes the defaults explicit at the profile layer.
- **Gemini Pro** (`gemini-2.5-pro`, `gemini-3.*-pro`): `temperature 0.7`, 20 steps / nudge 15 — these models use terser search strings and drill down more, so they get extra step headroom.
- **GPT-5** (`gpt-5`): `temperature 1.0` with `temperature_lock: true` (the family rejects other values), 20 steps / nudge 15.
- **Opus 4.7+** (`claude.*opus-4-([7-9]|[0-9][0-9])`): `temperature: null` — the model deprecated `temperature`, so it's omitted from the request.
- **Ollama (catch-all)** (`ollama`): `think: false` — thinking mode leaks `<think>` blocks into the reply and adds latency with no benefit for the tool-calling loop.

Anthropic models that don't match the Sonnet or Opus patterns fall through to the defaults — there's no generic Anthropic catch-all profile.

## Adding profiles for new providers

If you're running a model we haven't tested, add a profile entry with the tuning that works for your setup:

```yaml
  - pattern: "hosted_vllm/my-model"
    temperature: 0.3
    top_p: 0.9
```

The config file accepts any `generation_params` keys — they're passed straight through to LiteLLM, which handles translation to the provider's API.

## Thinking / reasoning modes

Most current LLMs offer some form of "thinking" or "extended reasoning" mode. It is tempting to enable these for a tool-calling loop, but in Swarpius's setup **most thinking modes are either ineffective, broken, or visibly degraded**. Only one is safe to turn on today.

| Provider | Param you might add | Outcome |
|---|---|---|
| OpenAI gpt-5 / o-series | `reasoning_effort: minimal\|low\|medium\|high` | **Safe.** Reasoning happens server-side; we only ever receive the final answer. Latency and `output_tokens` go up, but tool-calling still works correctly. |
| Anthropic Claude 4.x | `thinking: {type: enabled, budget_tokens: N}` | **Don't enable.** First call works, but the next tool-loop step will fail with an opaque API error because the response's thinking blocks aren't threaded back into the conversation. The error surfaces mid-conversation, not at config time. |
| Ollama | `think: true` (overriding the default `false`) | **Don't enable.** The model's internal reasoning leaks into the chat panel as if it were the answer (`<think>...</think>` text appears in the user-visible response). |
| Gemini 2.5 / 3 | `thinking_config: {...}` | **Don't enable.** Same root cause as Anthropic — the thinking-block half of the response is dropped, leading to broken or degraded behaviour depending on the request shape. |

In practice this means: feel free to add `reasoning_effort: minimal` to the GPT-5 profile if you want; leave the other providers alone. If you have a use case that genuinely needs Anthropic interleaved thinking or Ollama's `<think>` mode, that requires code changes in `agent/app/llm/client.py` to extract and re-send thinking content.

## Critical directive markers

Skill definition files (`skills/*/SKILL.md`) support `<!-- critical -->` / `<!-- /critical -->` markers around important directives. Marked sections are extracted into a separate "Key Rules" context provider, positioned for recency bias in the system prompt. The infrastructure is built and available if a model benefits from directive prioritisation; current recommended models work well without it.

## Tested models

Our experiments have covered Anthropic's Claude family (Haiku 4.5, Sonnet 4.6, Opus 4.6 and 4.7), frontier non-Anthropic models (Google Gemini 2.5 Pro, Gemini 3.1 Pro Preview, OpenAI GPT-5.4, and GPT-5.5), and two local Ollama Gemma models. The short version: Sonnet remains the model we'd actually run Swarpius on. Gemini Pro and GPT-5 are viable and handle the full request range, but Sonnet is overall better for Swarpius — more efficient, more thoughtful, and its persona expressions are richer and more realistic. Haiku is a workable budget step down for simpler traffic; Gemma 4 may handle simple single-intent requests but falls apart on anything that requires reflecting on multiple prior tool results; Gemma 3 can't drive the tool-calling loop at all. A general overview based on our testing is provided below:

| Model | Provider | Quality | Notes |
|---|---|---|---|
| **Sonnet 4.6** | `anthropic/claude-sonnet-4-6` | Excellent | Excellent tool-calling reliability across single- and multi-source requests. **Recommended for Swarpius.** |
| **Gemini Pro** | `gemini/gemini-2.5-pro`, `gemini/gemini-3.1-pro-preview` | Very good | Viable for Swarpius, but tends to have less "personality" and often requires more steps to achieve the same thing. Functional, but Sonnet is preferred — more efficient and richer in persona. |
| **GPT-5** | `openai/gpt-5.4`, `openai/gpt-5.5` | Very good | Viable for Swarpius. Similar profile to Gemini. |
| **Haiku 4.5** | `anthropic/claude-haiku-4-5` | Good | Sits between Gemma 4 and Sonnet. Handles most requests gracefully; can degrade on deeper multi-source, multi-step flows and tends to make more mistakes. Fast and very cheap. Well suited for the diagnostic and arbiter agents. |
| **Opus** | `anthropic/claude-opus-4-*` | Excellent | Overkill for the tool-calling loop. Better suited for the passive analyser. |
| **Gemma 4 26B** | `ollama_chat/gemma4:26b` | Partial | Fine for single-intent requests ("play Starbreaker by Judas Priest", "what's playing?", "pause"). Multi-source requests — e.g. "play tracks from this playlist, that album, and these three songs" — tend to fail: the model struggles with analysing execution traces and planning subsequent moves. Free/local. |
| **Gemma 3 27B** | `ollama/gemma3:27b-it-qat` | Not viable | Cannot handle native tool calling at all. |

### Why the gap?

There are two distinct skills to separate. **Function-calling as a primitive** — emitting a well-formed tool call when one's needed — has improved sharply in open-weight models; Gemma 4's function-call accuracy is roughly an order of magnitude better than Gemma 3's on Google's own benchmarks. **Agentic multi-step tool use** — observing tool outputs, reflecting on what came back, planning the next call across many turns — is the harder skill, and the gap there tracks scale and post-training emphasis more than openness per se. Frontier labs invest heavily in post-training traces of agent loops, and the largest open-weight models (DeepSeek V4, Kimi K2.6, Qwen 3.6 Plus) have closed much of that gap on public agent benchmarks. What stays decisive for Swarpius — almost entirely a multi-turn tool-use loop — is the gap at the *locally-runnable* size class: a Gemma-4-26B on a workstation GPU still falls short on multi-source requests where reflecting on prior tool results matters. However, the plug-any-LiteLLM-provider architecture enables the use of the largest open-weight models, which are now genuinely viable substitutes if you have the hardware.

For typical usage, Sonnet costs fractions of a penny per interaction. Haiku is roughly an order of magnitude cheaper again. Gemma 4 is free (hardware cost only) but only within its capability envelope.
