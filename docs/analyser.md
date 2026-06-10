# Conversation analyser

Operator-facing reference for Swarpius's LLM-driven conversation-
quality analyser. For **design rationale** — why temperature is pinned
to 0, why classification favours stronger models, how the failure-mode
taxonomy is structured, the analysis-guide design principles — see
[`analyser-architecture.md`](./analyser-architecture.md).

The analyser is an LLM-driven retrospective quality check on completed
conversations. It reads conversation logs, sends them to an LLM along
with a domain-specific guide, and produces structured findings against
a 19-mode failure taxonomy. The output drives the analysis-browser
panels in the web UI (per-conversation findings, severity breakdowns,
cross-conversation metrics with date / model / git-ref filtering).

It runs **inside the agent process** — there's no separate analyser
service, container, or venv. Toggle it on with `ENABLE_PASSIVE_ANALYSER="true"`
in `agent/.env`.

## When to enable it

Analysis costs LLM tokens — each conversation analysed is one (or one
per batch) extra API call. Three reasonable defaults:

- **Off (the default):** you read live cost / token telemetry in the
  session bar and per-request panels, but don't track conversation
  quality. Fine for casual use.
- **On with the background loop:** flip `ENABLE_PASSIVE_ANALYSER="true"`
  and the agent scans for unanalysed conversations every 30 minutes
  (configurable). Useful for tuning prompts, comparing models, or
  evaluating the effect of code changes.
- **On-demand only:** keep `ENABLE_PASSIVE_ANALYSER="false"` and click
  "Scan & Analyse" / "Re-Analyse" in the analysis-browser UI when
  you want them. Same code path, no background API spend.

## What it produces

| File | Where | What |
|---|---|---|
| `analysis.yaml` | `<data_dir>/logs/conversation/<date>/<cNN>/` | Per-conversation findings, severity, model used |
| `analysis-history.yaml` | same dir | Snapshot of prior `analysis.yaml` versions (rotated at 20) so re-analyses don't overwrite history |
| `feedback.yaml` | same dir | Operator-provided rebuttals / lessons; consumed on the next scan |
| `lessons-learned.md` | `<data_dir>/analysis/` | Aggregated lessons distilled from feedback |
| `metrics.jsonl` | `<data_dir>/analysis/` | Append-only roll-up of every analysis result; powers the Metrics tab |

`<data_dir>` resolves via `SWARPIUS_DATA_DIR` (default `agent/data/`).

## Configuration

All knobs live in `agent/.env`. Full env-var table is in
[`agent/README.md`](../agent/README.md#environment); the
analyser-relevant ones are:

| Var | Default | Notes |
|---|---|---|
| `ENABLE_PASSIVE_ANALYSER` | `false` | Master toggle for the background loop |
| `ANALYSER_INTERVAL_MINUTES` | `30` | Loop scan period |
| `ANALYSER_STALENESS_MINUTES` | `60` | Minimum idle time before a conversation is analysable |
| `ANALYSER_BATCH_SIZE` | `5` | Conversations batched per LLM call |
| `LLM_MODEL_ANALYSER` | unset | Override model (otherwise falls back to `LLM_MODEL`) |
| `ANALYSIS_HISTORY_MAX_ENTRIES` | `20` | History rotation depth |

`LLM_API_KEY_<PROVIDER>` for whichever provider the analyser model
resolves to is required (same key the agent uses if the providers
match).

## How the surfaces interact

- **Background loop** (when `ENABLE_PASSIVE_ANALYSER="true"`): a daemon
  thread in the agent process scans every `ANALYSER_INTERVAL_MINUTES`
  for conversations older than `ANALYSER_STALENESS_MINUTES` and lacking
  an `analysis.yaml`. Batches them via `ANALYSER_BATCH_SIZE`. Holds
  the scan lock for the duration of each pass.
- **"Scan & Analyse" button** (analysis-browser → conversations tab):
  immediate one-off scan over today's / yesterday's unanalysed
  conversations, same batch path as the loop. Holds the same scan
  lock — clicks during a running scan return `status=busy` rather
  than racing.
- **"Re-Analyse" button** (per-conversation card): immediate single-
  conversation reanalysis under the scan lock. Prior `analysis.yaml`
  is rotated into `analysis-history.yaml` first; doesn't use batching.

All three paths share `filelock`-based serialisation. They can't
double-process the same conversation.

## Metrics tab

The Metrics tab in the analysis browser reads `metrics.jsonl`, which
is appended after each analysis pass. It supports:

- Date-range filtering (`--after` / `--before` equivalent in the UI)
- Filter by git ref (commit short hash recorded at analysis time)
- Filter by coordinator model
- Side-by-side git-ref comparison: "what changed between commit X and Y?"

The comparison view is the primary workflow for measuring the impact
of code or prompt changes — note the git ref before and after a fix,
accumulate analyses under both refs, then compare.

## CLI (advanced / debugging)

Most users never need this — the env flag and UI buttons cover the
normal cases. But for ad-hoc scripting, one-off scans, or debugging,
the analyser is still invocable from the agent's venv:

```bash
cd agent
source .venv/bin/activate

# One-shot scan of all stale conversations
python -m analyser.analyse

# Continuous loop (equivalent to ENABLE_PASSIVE_ANALYSER=true but in
# the foreground — useful for watching it work)
python -m analyser.analyse --loop --interval 15

# Single conversation
python -m analyser.analyse -c c01
python -m analyser.analyse -c 2026-03-28/c01

# Override model / batch size
python -m analyser.analyse --model anthropic/claude-opus-4-7 --batch-size 3
```

CLI flags: `--conversation` / `-c`, `--loop`, `--interval`,
`--staleness`, `--model`, `--batch-size`, `--verbose`. All have
the same semantics as the corresponding env vars; flags win when
both are set.

Metrics collection is also exposed:

```bash
python -m analyser.metrics collect          # backfill metrics.jsonl
python -m analyser.metrics summary --after 2026-03-28
python -m analyser.metrics summary --compare 09385c0 4d724ea
```

The CLI shares the same scan lock as the in-process background loop,
so running both at once is safe — the second one to acquire the lock
exits with status `busy`.

## File reference

| Path | Purpose |
|---|---|
| `agent/analyser/analyse.py` | Main entry: scan loop, batching, single-conv mode, CLI |
| `agent/analyser/llm_layer.py` | LiteLLM wrapper + JSON response extraction |
| `agent/analyser/metrics.py` | `metrics.jsonl` collection + summary commands |
| `agent/analyser/feedback.py` | `feedback.yaml` / `lessons-learned.md` reader/writer |
| `agent/analyser/analysis-guide.md` | Domain knowledge + 19-mode failure taxonomy sent to the analyser LLM |
| `docs/analyser-architecture.md` | Architecture / design reference for the analysis system |
