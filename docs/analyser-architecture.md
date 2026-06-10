# Analyser architecture

Design and internals for Swarpius's LLM-driven conversation-quality
analyser. For **operational usage** (env vars, how to enable it, the
UI buttons, the CLI) see [`analyser.md`](./analyser.md) — this doc
covers the *why* behind those mechanics.

## Motivation

Manual testing involves: run a scenario, analyse logs. This is thorough but doesn't scale — it requires dedicated time, coordination, and only catches issues in formal test scenarios. Real usage surfaces different patterns.

Passive continuous analysis inverts this: use Swarpius normally, and let an automated process analyse completed conversations for issues in the background. Issues are flagged, metrics are tracked, and the impact of code changes becomes measurable.

## Architecture

```
 Swarpius (normal usage)
    │
    ▼
 Conversation logs written to:
 $SWARPIUS_DATA_DIR/logs/conversation/<date>/<cNN>/<request-id>/
    │
    ▼
 Analyser (reads logs, calls an LLM via LiteLLM, writes results)
    │
    ├── Scans for unanalysed conversations (no analysis.yaml present)
    ├── Checks staleness: last request in cNN > threshold ago
    ├── Reads logs for qualifying conversations
    ├── Sends analysis-guide.md + log data to the LLM
    ├── Classifies findings against failure mode taxonomy
    ├── Writes analysis.yaml to <cNN>/ directory
    └── Appends to metrics.jsonl
```

The analyser runs in-process inside the agent (`agent/analyser/` —
sibling of `app/`, `roon/`, `tools/`, `tts/`). Three entry surfaces
share one code path: a daemon-thread background loop, the web UI's
"Scan & Analyse" / "Re-Analyse" buttons, and a CLI. See
[`analyser.md`](./analyser.md) for the file layout, runtime
configuration, and which env vars drive which surface — this doc
focuses on the analyser's internal design and the constraints behind
the choices.

### Analysis guide — the core deliverable

Each analysis invocation starts with zero context about Swarpius. The [**analysis guide**](../agent/analyser/analysis-guide.md) is a self-contained document that gives the analyser model everything it needs:

- What Swarpius is and what its tools do
- What context the coordinator sees per request (Zone Status, Execution Trace, etc.)
- The failure mode taxonomy
- The shape of the analyser payload (what's in each section, what to look for)
- Severity calibration guidance
- The output JSON schema

This single document is the knowledge transfer mechanism: the analyser reads it and includes it as the system prompt in the LLM call.

## Model selection design

Model precedence, env vars, and the API-key resolution flow are
documented in [`analyser.md`](./analyser.md#configuration). The
notes below are about *why* the analyser behaves the way it does
once a model is resolved.

### Per-model tuning

The analyser reads `agent/model_profiles.yaml` via `app.llm.model_profiles` so per-model quirks (`top_p`, provider-specific generation params) carry through. **Temperature is a special case**: the analyser pins it to `0` regardless of what the profile sets, in order to encourage classification to be more deterministic. Two profile escape hatches are honoured:

- `temperature: null` — the model deprecated the parameter; the analyser omits it entirely from the call.
- `temperature_lock: true` — the model rejects any temperature override (e.g. GPT-5 requires the API default); the profile's temperature value carries through unchanged.

The temperature pinning happens only on the analyser's LiteLLM call. The agent's coordinator still uses whatever its own profile dictates, so this behaviour doesn't bleed into runtime.

### Why a strong model

Analysis is nuanced classification, not data extraction:

- The 19-mode taxonomy includes suppression rules (don't flag FM-11 confabulation if the claim traces back to injected context like Zone Status; check `registered_skills` before flagging a capability denial as confabulation; etc.).
- The model is asked to write a `detail` evidence trace *first* and then commit to a failure mode and severity. Weaker models tend to commit early and rationalise backwards.
- The revocation channel exists because even Opus occasionally writes a finding it then reconsiders — weaker models get this wrong far more often, in both directions.
- Severity calibration ("when in doubt, go lower") is a judgement call.

Smaller or faster models can be configured for cost / latency, but expect more false positives and noisier severity. Analysis runs offline against completed conversations, so latency rarely matters in practice; the default trades cost for classification quality.

## Trigger Mechanism

### When to analyse

A conversation `cNN` is eligible for analysis when:

1. **No `analysis.yaml` exists** in the `<cNN>/` directory
2. **The conversation is stale** — the most recent `request.json` timestamp within any `<request-id>/` subdirectory is older than the staleness threshold

The staleness threshold should be generous (default: **60 minutes**). The diagnostic agent can revisit earlier conversations (c03 → c04 → c03) when classifying new requests, so we need confidence that the conversation won't receive more. Since analysis timing isn't critical, erring on the side of longer thresholds is acceptable.

### History preservation on re-analysis

When a conversation is re-analysed (either via the UI's "Re-Analyse"
button or `python -m analyser.analyse -c <cNN>`), the previous
`analysis.yaml` isn't overwritten in place. `write_analysis` first
snapshots the existing file into `analysis-history.yaml` (adding
`superseded_at` and any pending `feedback`), then atomically replaces
`analysis.yaml` with the new result. The history file is rotated at
`ANALYSIS_HISTORY_MAX_ENTRIES` (default 20), so older versions
eventually drop off. The frontend's "Analysis History" section
surfaces every retained version. This design lets operators correct
analyses without losing the diff between attempts.

### Scope

By default, only analyses conversations from today and yesterday. Older logs are considered historical and won't be picked up automatically.

### Scan serialisation

The analyser takes a cross-platform non-blocking lock on `$DATA_DIR/analysis/scan.lock` before each scan cycle, via the `filelock` package (uses `fcntl.flock` on POSIX and `msvcrt.locking` on Windows). The on-demand "Scan & Analyse" path in the main agent shares the same lock, so a user-triggered scan and the scheduled loop can never double-process the same unanalysed conversations. If the lock is already held, the on-demand path exits with code 75 (`EX_TEMPFAIL`) and the frontend surfaces that as `status="busy"`; the scheduled loop just logs "another scan in progress" and waits for the next tick.

**Deployment topology caveats.** File locks are kernel-local, and advisory file locks on cross-OS bind mounts are notoriously unreliable. The lock is reliable when both scanners run under the same kernel:

- **All-Docker** (analyser + agent both containerised on a named volume): lock works.
- **All-native** (both run from the host's Python, same OS): lock works.
- **Mixed — Linux container + Windows-native host**: the in-container `flock` runs against a Windows bind mount where locking is best-effort, and it can't coordinate with the host-side `msvcrt.locking` regardless. Run both in the same environment, or stop one while using the other.

## What Gets Analysed

For each qualifying conversation, the analyser reads:

| File | Purpose |
|------|---------|
| `<cNN>/conversation_summary.json` | Topic, request list, conversation metadata |
| `<cNN>/context_snapshot.json` | Persona, default zone, coordinator model, registered skills (becomes the "Coordinator configuration" block) |
| `<request-id>/request.json` | User input, timestamp |
| `<request-id>/outcome.json` | Status, total steps, chat response, timing, per-request token usage |
| `<request-id>/tool_executions/NN_<skill>.json` | Tool inputs, outputs, timing — every tool call the coordinator made |
| `<request-id>/prompts/coordinator_system.txt` | The full system prompt the coordinator saw — embedded verbatim in the analyser payload |

Note: `<request-id>/coordinator_steps/` is written by the agent but is not consumed by the analyser. The information the analyser needs (the system prompt + each tool call's input/output) lives in the other files above.

## Analysis Process

For each request within a conversation:

1. **Determine user intent** — what did the user ask for? Parse the input for: target action (play, search, status, chat, etc.), target items, qualifiers (zone, category, item type).

2. **Map expected tool flow** — given the intent, what tools should be called and in what order? (Step counts legitimately vary by model, so the analyser flags identifiable redundancy rather than raw delta — see FM-12 in the guide.)

3. **Compare against actual flow** — what tools were actually called? What parameters? What order? Are any steps clearly redundant or unproductive?

4. **Check tool outputs against response** — does the final chat response accurately reflect what the tools actually returned?

5. **Classify any discrepancies** — map each issue to a failure mode from the taxonomy in `analysis-guide.md`.

6. **Assess severity per finding** — Low / Medium / High based on user impact.

## Output Format

### Per-conversation: `analysis.yaml`

Written to `$SWARPIUS_DATA_DIR/logs/conversation/<date>/<cNN>/analysis.yaml`. Example below:

```yaml
analysed_at: "2026-03-29T14:30:00Z"
git_ref: "8bc725a"
conversation_id: "c03"
date: "2026-03-29"
topic: "Playing jazz albums"
requests_analysed: 4
total_tool_calls: 9
total_steps: 11
avg_steps_per_request: 2.75
findings:
  - id: "a3f9"
    request_id: "rq-c03-0012"
    failure_mode: "FM-12"
    failure_name: "Excessive steps"
    severity: "low"
    summary: "Simple play request took 5 steps (expected ~3)"
    detail: |
      Steps: roon_search → roon_search (same query, Tracks category)
      → roon_action → roon_status (unnecessary) → text. The second search
      and status call were unnecessary.
revoked_findings:
  - id: "1c2e"
    reason: "Initial trace flagged the 'currently playing' line as confabulation, but Zone Status was injected and explicitly showed the track — grounded, not fabricated."
    original_finding:
      id: "1c2e"
      request_id: "rq-c03-0014"
      failure_mode: "FM-11"
      failure_name: "Confabulation"
      severity: "medium"
      summary: "Reported the currently playing track with no roon_status call."
      detail: |
        Response named the track without a corresponding tool call in this
        request.
notes: |
  Overall conversation flow was reasonable. One request had unnecessary
  extra steps but all requests completed successfully. One would-be
  confabulation finding revoked after re-checking Zone Status.
```

The analyser populates `revoked_findings` when it retracts a finding it started writing. Each entry references the finding's `id` and carries a `reason`. `_apply_revocations` in `analyse.py` enriches each matched revocation with the full `original_finding` (so metrics and the frontend don't have to cross-reference) and filters the entry out of `findings` before persisting. See [Design principles](#design-principles) below for why the channel exists.

### Metrics append: `metrics.jsonl`

One line appended to `$SWARPIUS_DATA_DIR/analysis/metrics.jsonl` per analysed conversation.

```json
{"analysed_at":"2026-03-29T14:30:00Z","git_ref":"8bc725a","conversation_id":"c03","date":"2026-03-29","requests":4,"steps":11,"avg_steps":2.75,"findings_by_mode":{"FM-12":1},"findings_by_severity":{"low":1},"finding_count":1,"revoked_count":1,"revoked_by_mode":{"FM-11":1}}
```

Each line is self-contained. The `git_ref` pins the analysis to a code version. To compare metrics between code versions:

```bash
# All findings for a specific commit range
grep -E '"git_ref":"(abc1234|def5678)"' "$SWARPIUS_DATA_DIR/analysis/metrics.jsonl" | jq .

# Count findings by failure mode across all analyses
cat "$SWARPIUS_DATA_DIR/analysis/metrics.jsonl" | jq -s '[.[].findings_by_mode | to_entries[]] | group_by(.key) | map({mode: .[0].key, count: [.[].value] | add})'
```

## Metrics

### What we track

**Per-conversation (in analysis.yaml):**
- Requests analysed, total tool calls, total steps
- Average steps per request
- List of findings with `id`, failure mode, severity, summary, detail
- List of revoked findings — entries the analyser wrote and then retracted, each enriched with the original finding so the reason and original metadata are preserved
- Notes — free-text overall assessment
- Git ref at time of analysis

**Aggregated (from metrics.jsonl):**
- Finding count by failure mode over time
- Finding count by severity over time
- Revocation count and per-failure-mode revocation breakdown
- Average steps per request over time
- Percentage of conversations with issues
- All of the above filterable by git ref range

### How to use metrics

**Measuring impact of a change:**

After merging a fix (e.g., improved search category guidance in the system prompt):

1. Note the git ref before and after the change
2. Continue using Swarpius normally for a period
3. Filter metrics.jsonl by git ref to compare:
   - Did `FM-05` (incorrect search params) findings decrease?
   - Did any other failure modes increase (regression)?
   - Did average steps per request change?

**Identifying persistent issues:**

Periodically review which failure modes appear most frequently. If `FM-08` (failed context reference) keeps appearing, that's a signal to invest in improving context/search_history visibility.

**Tracking quality over time:**

A weekly summary (total conversations, total findings, breakdown by mode and severity) gives a high-level quality trend. This can be generated on-demand from metrics.jsonl — no need for a dashboard.

## Analysis Guide Design

The analysis guide (`analysis-guide.md`) is the self-contained document that gives the analyser model all the domain knowledge it needs. It's used as the system prompt for the `analyse.py` LLM call.

### Design principles

- **Self-contained**: a model reading only this document and the conversation payload can produce a useful analysis. No dependency on CLAUDE.md, the codebase, or prior context.
- **Grounded in failure modes**: findings reference the FM-NN taxonomy directly, not ad-hoc categories.
- **Evidence-based**: instructs the model to cite specific payload data (step numbers, tool names, parameters) for every finding.
- **Calibrated for false positives**: a missed issue is better than noise. The guide includes per-FM nuance to suppress common false-positive triggers (e.g. "what's playing" answers being misread as confabulation when Zone Status was in the injected context).
- **Revocation channel**: rather than fight the model's tendency to reconsider mid-write, the schema includes a `revoked_findings` list the model can populate when it retracts a finding it already started. `_apply_revocations` filters them before persisting.
- **Structured output**: a single JSON object matching the analysis schema, which `analyse.py` serialises to `analysis.yaml`.

### Contents (in order)

1. **Scope of analysis** — what counts as a finding (Swarpius behaviour) vs context (external system events)
2. **How Swarpius works** — the coordinator loop and the available tools
3. **Coordinator context (injected fresh each request)** — the dynamic sections of the system prompt (Zone Status, Execution Trace, etc.) so the analyser can attribute claims correctly
4. **Failure mode taxonomy** — the 19 modes grouped by category, each with detection guidance and any suppression nuance
5. **Coordinator configuration** — how to read the persona / model / registered-skills block at the top of the payload
6. **Payload structure** — the exact shape of what the analyser receives, in the order it sees it
7. **Severity calibration** — what constitutes low/medium/high
8. **Output format** — the JSON schema, including `id` per finding and the `revoked_findings` list

### What it deliberately omits

- Full CLAUDE.md (too much noise, includes setup/dev instructions irrelevant to analysis)
- Tool source code (analysis is about behaviour, not implementation)
- Swarpius architecture details beyond what's needed to interpret the payload

### Calibration

The guide may be refined over time as operators dispute findings via the analysis browser. Operator feedback is stored per-conversation in `feedback.yaml`; lessons that apply generally, rather than to specific setups, are aggregated into `lessons-learned.md` and may be fed back into the guide when a pattern recurs. The failure-mode taxonomy can also evolve; new modes may be added if real patterns emerge that don't fit existing ones.
