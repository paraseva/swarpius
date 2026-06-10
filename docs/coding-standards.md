# Coding Standards

Criteria used to review code and test changes in this repo. Each criterion is a lens, not a checklist item — one piece of code can fail on several, and some criteria overlap deliberately (different facets of the same quality).

Contributors: the criterion names and the "what it means" column are your standard. The "what violating looks like" column and the anchoring examples are how reviewers — human and automated — apply it.

**When criteria conflict:** correctness always wins. Consistency loses to correctness. Simplicity loses to correctness; don't sacrifice it to consistency.

## Code

| Criterion | What it means | What violating looks like |
|---|---|---|
| **Correctness** | Logic is sound; edge cases and failure paths considered. | Off-by-one, mishandled None, silently swallowed errors, race conditions, unreachable branches. |
| **Consistency** | Similar things are done the same way across the codebase. | Two modules solving the same problem with different patterns; mixed naming schemes; env reads done three different ways. |
| **Readability / self-documenting** | A reader new to the file can follow the flow without hunting. | Comments explaining *what* the code does (instead of *why*); long functions with unclear structure; cryptic names that need context to decode. |
| **Good naming** | Names reflect purpose and are honest about behaviour. | Misleading type/module names; names that imply one thing and do another; generic names where specific ones would help (`data`, `handler`, `process`). |
| **Maintainability** | Future changes can be made safely without rippling. | Scattered state; implicit contracts between modules; changes to one file reliably require changes to unrelated files. |
| **Understandability / simplicity** | Each piece does one thing, and that thing is obvious. | Functions with multiple responsibilities; deeply nested conditionals; clever tricks that obscure intent. |
| **Intuitiveness** | Behaviour matches what the name and signature suggest. | Functions with side effects not implied by their name; APIs that surprise the caller. |
| **Separation of concerns** | Each module owns a clear responsibility; boundaries are clean. | Business logic in I/O layers; persistence concerns leaking into domain models; modules that reach across their boundaries. |
| **Proper scoping** | Visibility and lifetime match intent. | Module-level state that should be per-request; internals exposed that shouldn't be; public APIs masquerading as implementation detail. |
| **Testability** | Code can be exercised in isolation by simple tests. | Hard-wired singletons; time or env read deep inside business logic; I/O not injectable. |
| **Easily evolved** | Adding a new variant requires changing few, local things. | Switch statements on a type that appear in ten places; new features require editing an enum and five call sites. |
| **No dead code / no speculative abstraction** | Every piece earns its keep; abstractions exist because a concrete need exists today. | Unused imports, unreferenced functions, branches that can never fire, generic factories with one concrete implementation. Matches Swarpius's stated ethos ("don't design for hypothetical future requirements"). |
| **Honest surface** | Settings, flags, and API shapes do what they claim. | Env vars that quietly do nothing (the former `MAX_CONCURRENT_LLM_REQUESTS`); parameters named `retries` that are ignored; methods with names that imply guarantees they don't provide. |
| **Errors at boundaries, not inside trusted code** | Validation happens where untrusted input enters; internal code trusts its inputs. | `isinstance` checks scattered through domain logic; defensive `if x is None` in functions whose callers can't legally pass None; retry/except blocks around operations that can't actually fail. |
| **Comments — necessary, concise, present-focused** | A comment earns its place only by explaining a non-obvious *why* in a short line. Default to none; lean on names and structure to carry meaning. Historical references (bug numbers, dates, "this used to do X", PR refs) are allowed *only* when the history explains a constraint that still matters — an unfixed bug, a past incident that justifies a counter-intuitive choice that would otherwise look removable. They are never a substitute for git blame, commit messages, or the CHANGELOG. Applies equally to tests. **Keep/remove test:** if deleting this comment, would a future contributor inadvertently re-do something the comment is preventing? If no → remove. If yes → keep, and make the comment as short as it can be while still warning them. Functional markers (`# noqa`, `# type: ignore`, `# pragma:`, license headers) and Pydantic `Field(description=...)` are not comments in this sense — they're functional and stay. | Multi-paragraph preambles narrating what a function or block does; docstrings on props/params that just restate the name; `Regression: cNN YYYY-MM-DD` / `see c03 2026-05-07` / `added for the X flow` / `fixed in v1.2.3` references whose only purpose is recording history rather than explaining a current constraint; long block comments where a rename or a smaller function would have served better. |

## Tests

| Criterion | What it means | What violating looks like |
|---|---|---|
| **Tests behaviour, not implementation** | A test survives refactoring that doesn't change observable behaviour. | Asserts on internal method call order; asserts on private state; mocks of internal collaborators. |
| **Tests real logic, not plumbing with pre-configured fakes** | The test exercises the thing under test, not a mock returning the expected answer. Fakes should stub the API boundary (the actual external call) and inherit/use real production logic above it. | Test sets up fakes that encode the expected outcome, then asserts the outcome — the production code is never actually run through its branches. Fake class re-implements internal helpers (`_nav_drill`, `get_media_actions`, `reconcile_intended_category`, etc.) with simplified logic the test author chose, so production branches are bypassed and the test is a tautology against the fake's own logic. |
| **Efficient, minimal overlap** | Each test has a distinct reason to exist. | Three tests with near-identical setup asserting the same behaviour; whole files duplicating coverage from other files. |
| **Reliable / deterministic / not flaky** | Same inputs produce the same result on every run. | Tests that depend on wall-clock time, network, ordering across files, or uninitialised env. Three facets of the same quality, but they fail in different ways — keep them distinct. |
| **Isolated** | Tests don't depend on each other and don't share mutable state. | Test B only passes after test A runs; shared module-level fixtures that persist across tests. |
| **Filesystem isolation** | Tests never write to real data paths or mutate user state. | Writes to `agent/data/...` instead of a temp dir; leaves lock files, logs, or DB state behind. |
| **Mocks at boundaries only** | Real collaborators are exercised; only external I/O (network, LLM, Roon API) is mocked. | Mocked internal helpers, mocked Pydantic models, mocked domain objects. |
| **Covers error paths, not just the happy path** | Failure branches are exercised too. | Every test uses valid inputs that take the success path; error-handling code has no tests. |
| **Fails for a specific reason (high diagnosticity)** | A broken test points at the defect, not "something in module X." | Broad asserts that break on any change; test names that describe setup rather than expectation. |
| **Maintainable / readable / simple / intuitive** | Same criteria as production code — tests are read by humans too. | Opaque fixture trees; tests that require reading three helper files to understand; cryptic parametrise expressions. |
| **High value** | The test catches a real defect or pins down non-obvious behaviour. | Tests that assert `2 + 2 == 4` equivalents; tests of generated code; tests of framework behaviour we don't own. |

## Anchoring examples

### Tests real logic, not plumbing with pre-configured fakes

Bad — the fake answers the question itself; production code never runs:

```python
class _FakeRoonConnection:
    def __init__(self, default_zone):
        self.target_zone = default_zone
    def get_default_zone(self):
        return self.target_zone  # never exercises _resolve_default_zone

# Test asserts target_zone == "Living Room" — but it's just asserting
# that the fake returns what the fake was told to return.
```

Good — fake stubs only the network boundary; real production logic is on the call path:

```python
class _FakeRoonConnection(RoonZoneMixin):     # real mixin = real logic
    def __init__(self, default_zone):
        self.api = _FakeApi()                 # api.zones is the stub
        self._default_zone_name = default_zone
        self._preferred_output_id = None
        self._resolve_default_zone()          # real method runs

# Now popping a zone from api.zones triggers real offline-resolution
# behaviour — the test exercises production, not the fake.
```

### Comments — necessary, concise, present-focused

Bad — narrates what the next line does, carries dated context whose only purpose is recording history:

```python
# Added 2026-05-12 for the c03 bug where the coordinator would
# sometimes set the default zone to the transfer target. We now
# check if zone is the same as target_zone before assigning.
if zone != self.target_zone:
    self._preferred_output_id = output_id
```

Good — explains a non-obvious *why* in present tense:

```python
# A Transfer Zone action moves playback but must not silently
# reassign the user's default — they may want to transfer to a
# temporary zone without losing their preference.
if zone != self.target_zone:
    self._preferred_output_id = output_id
```

Better still — if the surrounding function name and structure make the *why* obvious, no comment at all.

History is allowed when it still matters — when removing it would mislead the next contributor:

```python
# Workaround for litellm#1234: streaming responses occasionally
# emit a tool-call delta with empty `arguments`.
if delta.tool_calls and not delta.tool_calls[0].function.arguments:
    continue
```

The pointer to the bug tells the next reader *why* the awkward branch exists and *when it can go*.
