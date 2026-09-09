# Motily Technical Spec v0.1 (Repository-Local Contract)

Status: Phase 1 contracts only.

This document is **not** a reconstruction of any prior or external technical
specification — no such document existed anywhere in this repository when
Phase 1 began. It instead records, from this point forward, what this
codebase actually implements and guarantees, so later phases have one
repository-local source of truth to build on and patch against.

## System Purpose

Một Tí Lý is an AI-assisted pipeline for planning and structuring short-form
science explainer videos hosted by "Tí". The pipeline is intended to take a
topic from idea discovery through research, feasibility, narrative design,
and scripting, with human-approval gates between stages.

## Approved Phase 1 Data Contracts

Defined under `app/models/` and `app/config/`:

- `GlobalConfig` ([app/config/loader.py](../app/config/loader.py)) — brand,
  audience, character, science, content, visual, production, and
  optimization constraints, plus a list of immutable rules. Loaded from
  [app/config/global.yaml](../app/config/global.yaml).
- `Project`, `TemplateVersions` ([app/models/project.py](../app/models/project.py))
- `IdeaCandidate`, `ABT` ([app/models/idea.py](../app/models/idea.py))
- `ResearchR0`, `ResearchPackage`, `Claim`, `Source`
  ([app/models/research.py](../app/models/research.py))
- `FeasibilityReport`, `SubEvaluation`, `ProductionEvaluation`
  ([app/models/feasibility.py](../app/models/feasibility.py))
- `NarrativePlan`, `SCQA`, `QuestionLadderNode`
  ([app/models/narrative.py](../app/models/narrative.py))
- `PackagingPrototype` ([app/models/packaging.py](../app/models/packaging.py))
- `ScriptPlan`, `ScriptBeat`, `ScriptLine`, `ScriptVerificationReport`
  ([app/models/script.py](../app/models/script.py))
- `HumanApproval` ([app/models/approval.py](../app/models/approval.py))
- `ModuleRun` ([app/models/module_run.py](../app/models/module_run.py)) —
  audit-log metadata only, added in Phase 2
- Shared enums, `GateEvaluation`, and `MotilyModel`
  ([app/models/common.py](../app/models/common.py))

All core contracts reject unknown fields (`extra="forbid"`) and validate on
construction. No field silently falls back to a default where the contract
requires a real value.

## Current Module Boundaries

- `app/config/` owns loading and validating `GlobalConfig`. It performs no
  mutation and no I/O beyond reading its own YAML file.
- `app/models/` owns typed data contracts only: no LLM calls, no provider
  APIs, no orchestration, no persistence, no state-transition execution.
- `tests/` covers construction, validation, and serialization round-trips
  for every contract above.

Nothing outside these two packages exists yet. There is no CLI, no
database, no agents, and no network access anywhere in this repository.

## Source-of-Truth Ownership

- **`ResearchPackage` is the factual source of truth.** Claims and their
  confidence/status/sources live only here.
- **`NarrativePlan` is the story-architecture source of truth.** It
  sequences claims into a question ladder; it does not originate new facts.
- **`ScriptPlan` is the spoken-text source of truth.** Its lines carry the
  final words and reference claim IDs; it does not originate new facts.
- **`GlobalConfig` is the immutable brand-constraint source of truth.** It
  is conceptually read-only: nothing in this repository mutates it at
  runtime.

## Amendment Process

Future phase specifications must be **appended to this document only from
user-approved patches** (the same PATCH workflow used for Phase 1 and Phase
1.1). No agent may add to, redesign, or expand this specification on its
own initiative.

## Phase 2 — State Machine + SQLite Persistence (approved)

Adds two infrastructure packages. Neither changes the Phase 1 domain
contracts' meaning; both operate on them from the outside.

### `app/workflow/` — deterministic state transitions

`app/workflow/states.py` defines `TRANSITIONS`, the approved `ProjectState`
adjacency graph (11 forward edges, 15 recovery/return edges). `MVP_COMPLETE`
and `ARCHIVED` are terminal: no outgoing edges. `app/workflow/transitions.py`
exposes `can_transition(current, target) -> bool` and
`validate_transition(current, target) -> None`
(raises `InvalidStateTransitionError` from `app/workflow/errors.py`).

This graph validates **topology only** — whether a target state is a
structurally allowed destination. It does not and must not evaluate whether
an upstream artifact (e.g. a `FeasibilityReport`) earned that transition;
that judgment belongs to a future orchestrator.

### `app/storage/` — SQLite persistence

Engine: **SQLAlchemy 2.x** (`Mapped`/`mapped_column` declarative style),
chosen over SQLModel specifically so ORM rows stay structurally separate
from the Pydantic domain contracts — SQLModel's table models double as
Pydantic models, which would blur the boundary this codebase deliberately
keeps sharp. `app/storage/orm.py` defines the row classes; each repository
module (`projects.py`, `artifacts.py`, `approvals.py`, `module_runs.py`)
explicitly translates between an ORM row and its corresponding Pydantic
model. The Pydantic models remain the only domain contracts.

Three tables are first-class and queryable: `projects`, `approvals`,
`module_runs`. The eight module-output contract types (`IdeaCandidate`,
`ResearchR0`, `FeasibilityReport`, `ResearchPackage`, `NarrativePlan`,
`PackagingPrototype`, `ScriptPlan`, `ScriptVerificationReport`) share one
`artifacts` table, keyed by `(project_id, artifact_type)`, storing each as a
validated Pydantic JSON payload rather than being normalized into their own
tables — consistent with the source-of-truth ownership above:
`ResearchPackage`, `NarrativePlan`, and `ScriptPlan` are never merged into
one document, each is stored and retrieved as its own artifact.
`save_artifact`/`get_artifact` upsert and re-validate through the caller's
expected Pydantic class; there is no version history of superseded
artifacts in Phase 2.

Project state updates (`update_project_state`) always pass through
`validate_transition` first; a rejected transition leaves the stored state
untouched. There is no public method that writes an arbitrary project state
without going through the graph. (Project *creation* is not a transition —
it has no prior state — but is constrained to start in `NEW_PROJECT`, the
graph's only state with no incoming edges.)

Default database file: `data/motily.db` (created on first
`init_database()` call; gitignored). Tests always use an isolated temporary
database file, never this default path.

**Concurrency**: single-user, local execution only. No distributed workers,
no multi-process write coordination beyond what SQLite itself provides.
This is an explicit MVP assumption, not an oversight.

Nothing in `app/workflow/` or `app/storage/` executes an LLM call, agent,
prompt, or orchestrator. Phase 2 is infrastructure only.

## Phase 3 — LLM Provider Abstraction + Structured Output (approved)

Adds `app/llm/`: the provider-independent infrastructure a future content
engine will call through. **No content engine exists yet** — nothing in
this repository generates an idea, researches a topic, or writes a script.
**No remote LLM provider is required or implemented yet** — only a
deterministic in-memory fake, used for tests.

### The boundary

```
Engine  ->  StructuredGenerator  ->  LLMProvider  ->  concrete provider
```

`app/llm/provider.py` defines `LLMProvider` as a `typing.Protocol` with one
method, `generate(request: LLMRequest) -> LLMResponse`. A future engine
depends only on this interface and on `generate_structured()` — never on a
concrete SDK call (`anthropic.messages.create`, `openai.responses.create`,
etc.) directly. Provider-specific details must never appear above the
`LLMProvider` boundary; nothing in Phase 3 adds a concrete remote provider,
so that boundary is proven only by `app/llm/fake.py`'s `FakeLLMProvider`
today.

### Contracts (`app/llm/models.py`)

`LLMRequest` and `LLMResponse` are provider-independent — no field is
specific to one vendor's API shape; provider-specific extras, if ever
needed, belong in each model's `metadata: dict[str, str]`, never as a new
top-level field. `TokenUsage` guards against negative counts and a
contradictory total. `ValidationFailure` records one failed structured
attempt (attempt number, error type, message, raw text) — deliberately
nothing resembling private chain-of-thought. `StructuredGenerationResult[T]`
carries the validated object alongside `attempts`, the final `LLMResponse`
(model/provider/token usage), and every recorded `ValidationFailure`, so
that traceability is never thrown away.

### Structured generation (`app/llm/structured.py`)

`generate_structured(provider, request, output_model, max_retries=2)` calls
`provider.generate()`, parses the response text as JSON, and validates it
through `output_model.model_validate()`. A single outer ```` ``` ```` or
```` ```json ```` fence wrapping the *entire* response is stripped
defensively; JSON embedded in surrounding prose is never scraped out — a
response like `"Here you go:\n{...}"` fails validation rather than being
silently repaired. On a JSON-parse or schema-validation failure, a
*correction* request is built from the original task plus the schema
(`output_model.model_json_schema()`) plus a concise description of what
went wrong — never an accumulating conversation, never a request to
explain the failure, never a leaked stack trace.

**Validation failures and provider failures are kept strictly separate.**
If `provider.generate()` itself raises (e.g. `LLMProviderError` — a
network/API/provider-side failure), that exception propagates immediately
out of `generate_structured()` uncaught: Phase 3 implements no transport
retry policy. Only JSON/schema validation failures are retried, and only up
to the bound described below.

`generate_structured()` returns metadata; it never writes to the database
and never creates a `ModuleRun` record itself. A future orchestrator owns
persisting that metadata into `ModuleRun` — see Phase 2 above.

### Configuration (`app/llm/config.py`)

`LLMSettings` is deliberately minimal (`provider`, `default_model`,
`max_structured_retries`, optional default temperature/max-tokens) and has
**no API key field**, because Phase 3 ships no concrete provider to consume
one. When a real provider is added, its key must be read from an
environment variable at call time — never hard-coded, logged, persisted to
SQLite, or placed in `ModuleRun`/audit metadata.

Nothing in `app/llm/` imports `app/storage/`, adds a business prompt, or
introduces an agent/multi-agent abstraction.

## Phase 4 — Idea Engine Vertical Slice (approved)

Adds `app/engines/`: the first real content engine, proving the full path
across every prior phase. **Exactly one content engine exists** — Idea
Engine. **No research, feasibility, narrative, or script engine exists.**
**No autonomous orchestrator exists** — `IdeaEngine.run()` is called
directly, once, by a caller (a test today; a future orchestrator later).
**No remote LLM provider exists** — `FakeLLMProvider` still drives every
test.

### The slice

```
Project (IDEA_DISCOVERY)
  -> IdeaEngine.run(IdeaEngineInput)
  -> build_system_prompt(GlobalConfig) + build_user_prompt(input)
  -> generate_structured(provider, request, IdeaCandidate)
  -> save_artifact(project_id, "idea_candidate", idea)
  -> update_artifact_reference(project_id, "idea_candidate_id", idea.id)
  -> update_project_state(project_id, IDEA_REVIEW)
  -> save_module_run(status=SUCCESS)
  -> IdeaEngineResult
```

`app/engines/idea/` holds the engine (`engine.py`), its input/output
contracts (`models.py`), and its business prompt (`prompt.py`).
`app/engines/errors.py` holds `EngineStateError`, shared by design across
future engines — it is one exception class, not a `BaseEngine` framework;
no such framework exists anywhere in this codebase.

### Precondition and failure semantics

`IdeaEngine.run()` only executes when `project.state == IDEA_DISCOVERY`;
any other state raises `EngineStateError` before anything else happens — no
`ModuleRun` is created, no provider is called, nothing is written. Once a
`ModuleRun(status=RUNNING)` is persisted, the engine proceeds in this fixed
order: generate → persist the `IdeaCandidate` artifact → point
`Project.idea_candidate_id` at it → transition the project to
`IDEA_REVIEW` → finalize `ModuleRun` as `SUCCESS`. If any step from
generation through the state transition fails, the engine records
`ModuleRun(status=FAILED, error_message=<concise external message>)` and
re-raises the original error unchanged — the project is never left
pointing at a non-existent artifact, and it never reaches `IDEA_REVIEW`
unless the artifact was already durably persisted. Each repository call is
its own SQLite transaction (per Phase 2); safety here comes from strict
ordering — persist-before-reference, reference-before-transition — not
from a cross-repository unit-of-work, which was deliberately not built.

`IdeaEngine` reaches `IDEA_REVIEW` only. It never creates a
`HumanApproval` and never advances further — that stays a future, separate
human action.

### Dependencies

`IdeaEngine` takes a SQLAlchemy `Engine`, an `LLMProvider`, a validated
`GlobalConfig`, and `LLMSettings` (model name, temperature, retry bound —
never a vendor name in the business payload). The three repository
modules (`project_repo`, `artifact_repo`, `module_run_repo`) are
constructor-injected, defaulting to the real Phase 2 modules, so tests can
substitute a stub to simulate a storage failure without touching SQLite
directly.

### Artifact type

The Idea Engine stores its output under the string artifact type
`"idea_candidate"`, centralized as `IDEA_CANDIDATE_ARTIFACT_TYPE` in
`app/engines/idea/models.py`. Phase 2's `artifacts` table already keys on
a plain string; a closed `ArtifactType` enum was deliberately not
introduced here, since Phase 2's `save_artifact`/`get_artifact` are locked
and a real enum would need to apply consistently everywhere they're called
to be worth the type — not just from this one engine.

## Phase 5 — Human Approval Bridge + R0 Research Vertical Slice (approved)

Adds `app/review/`, `app/research/`, and `app/engines/research_r0/`,
extending the real workflow one stage further:

```
IDEA_REVIEW -> approve_idea() -> HumanApproval -> R0_RESEARCH
  -> R0ResearchEngine -> ResearchRetriever -> ResearchR0 -> FEASIBILITY
```

**R0 is discovery research, not final verification.** Its prompt says so
explicitly and forbids claiming an exhaustive literature review or
definitive historical truth. **No Feasibility Engine exists** — R0 always
advances to `FEASIBILITY` regardless of its own `recommendation`
(`CONTINUE`/`REFRAME`/`REJECT`); a future Feasibility Engine, not R0, is
the stage that acts on that judgment. **No autonomous orchestrator
exists** — `approve_idea`/`revise_idea`/`R0ResearchEngine.run()` are each
called directly by a caller. **No real search provider exists** — only
`FakeResearchRetriever`, exactly mirroring `FakeLLMProvider`'s role for
the LLM layer.

### Human approval (`app/review/`)

`approve_idea(db_engine, project_id, feedback=None)` and
`revise_idea(db_engine, project_id, feedback)` are plain, deterministic
functions in `service.py` — no LLM call, no `BaseReview`/agent framework.
Both require `project.state == IDEA_REVIEW`
(`ReviewStateError` otherwise); `approve_idea` additionally requires a
valid `idea_candidate_id` pointing at a real, matching `IdeaCandidate`
artifact (`MissingReviewArtifactError` otherwise). On success,
`approve_idea` persists `HumanApproval(status=APPROVED)` and transitions
`IDEA_REVIEW -> R0_RESEARCH`; `revise_idea` persists
`HumanApproval(status=REVISE)` and transitions `IDEA_REVIEW ->
IDEA_DISCOVERY` without ever re-running the Idea Engine automatically.
**`IDEA_REVIEW -> ARCHIVED` (a reject action) is intentionally not
implemented** — the locked state graph has no such edge, and Phase 5 does
not add one.

Ordering: all preconditions are validated first, then the approval is
persisted, then the state transition runs. If the transition step were to
fail after the approval was already persisted, that error propagates
as-is — no rollback is fabricated (Phase 2's repository functions are
each their own SQLite transaction; there is no cross-repository
unit-of-work).

### Research retrieval (`app/research/`)

`ResearchRetriever` is a `typing.Protocol` with one method,
`search(query: ResearchQuery) -> ResearchSearchResponse`. No engine calls
Google/Bing/Tavily/SerpAPI/a browser directly — only through this
interface. `RetrievedSource` reports what was found (title, URL, snippet,
source name, publication date); it does not classify evidence quality —
that judgment belongs to the research engine consuming it.
`FakeResearchRetriever` (the only implementation in Phase 5) returns a
fixed sequence of responses/exceptions and records every query received,
exactly mirroring `FakeLLMProvider`.

### R0 Research Engine (`app/engines/research_r0/`)

`R0ResearchEngine.run()` requires `project.state == R0_RESEARCH` and a
valid, matching `IdeaCandidate` artifact (`EngineStateError` /
`MissingIdeaArtifactError` otherwise, before any `ModuleRun`, retriever
call, or LLM call). Search queries are built deterministically —
`app/engines/research_r0/queries.py`, no LLM call rewrites them — from
`idea.topic`, `idea.central_question`, `idea.physics_core`, and up to one
of `idea.research_questions`, capped at `MAX_R0_SEARCH_QUERIES = 4` total
and deduplicated. Retrieved sources are deduplicated by URL before being
handed to the LLM. Generation targets the locked `ResearchR0` directly
through `generate_structured()` — no competing schema.

**Source hallucination guard**: `ResearchR0.candidate_sources` (a flat
list of URL strings, per the locked Phase 1 schema — see Spec Deviations)
must be a subset of the URLs actually retrieved. This is a *business*
validator, not a JSON/schema one, so it runs *after* `generate_structured`
returns a Pydantic-valid result — Phase 3's structured layer is
unmodified. If the check fails, the engine issues exactly one bounded
correction call (`generate_structured(..., max_retries=0)`, i.e. one more
provider call with no further retry of its own) telling the model which
URLs were not supplied and which URLs it may use. If the corrected output
still fails the check, the run fails cleanly with
`ResearchSourceHallucinationError` — hallucinated sources are never
silently dropped or repaired.

`ResearchR0.idea_id` is handled differently: it is a structural
reference the engine already knows with certainty (`idea.id`), not
LLM-owned content, so after a Pydantic-valid `ResearchR0` comes back the
engine deterministically sets `idea_id = idea.id` regardless of what the
model produced. This is not a hallucination guard — it removes a field
the model was never meaningfully positioned to originate correctly in the
first place.

### Artifact type

R0's output is stored under `"research_r0"`, centralized as
`RESEARCH_R0_ARTIFACT_TYPE` in `app/engines/research_r0/models.py`, the
same string-constant pattern as Phase 4's `IDEA_CANDIDATE_ARTIFACT_TYPE`.

## Phase 6 — Feasibility Engine + Human Decision (approved)

Adds `app/engines/feasibility/` and extends `app/review/`:

```
FEASIBILITY -> FeasibilityEngine -> FeasibilityReport persisted
  -> project STAYS in FEASIBILITY -> explicit human decision
     PASS    -> R1_RESEARCH
     REFRAME -> IDEA_DISCOVERY
     REJECT  -> ARCHIVED (terminal)
```

**The Feasibility Engine never routes state.** It evaluates and persists a
`FeasibilityReport`, updates `Project.feasibility_id`, and stops — the
project remains in `FEASIBILITY` until a human calls
`decide_feasibility()`. **No R1 Research Engine exists yet** — a `PASS`
decision only transitions the project to `R1_RESEARCH`; nothing executes
there. **No autonomous orchestrator exists** — both the engine and the
decision are invoked directly by a caller.

### Feasibility Engine

Preconditions (checked before any `ModuleRun`, LLM call, or write):
`project.state == FEASIBILITY`, plus a valid, matching `IdeaCandidate`
*and* a valid, matching `ResearchR0` (`EngineStateError` /
`MissingIdeaArtifactError` / `MissingResearchArtifactError`). It evaluates
exactly five locked axes — `audience`, `science`, `narrative`, `visual`,
`production` — through `generate_structured(..., FeasibilityReport)`; no
sixth axis, no numeric thresholds.

**Overall status is never trusted from the model.** The locked
`FeasibilityReport.status` field is deterministically re-derived in
`app/engines/feasibility/status.py` from the five axis statuses (any
`REJECT` axis → overall `REJECT`; else any `REFRAME` axis → overall
`REFRAME`; else `PASS`) and overwrites whatever the LLM produced — the
same pattern Phase 5 uses for `ResearchR0.idea_id`.

### Human Feasibility decision (`app/review/service.py`)

`decide_feasibility(db_engine, project_id, decision: GateStatus, feedback=None)`
requires `project.state == FEASIBILITY` and a valid, matching
`FeasibilityReport` (`ReviewStateError` / `MissingReviewArtifactError`).
**`decision` must equal the stored report's `status` exactly** —
`FeasibilityDecisionMismatchError` otherwise. Phase 6 has no override
feature; a human cannot approve `R1_RESEARCH` against a `REJECT` report,
for example. `REFRAME` additionally requires non-blank `feedback`. On
success: `HumanApproval` is persisted (`APPROVED`/`REVISE`/`REJECTED`
matching the decision), then the project transitions accordingly. On
`REFRAME`, `idea_candidate_id`/`research_r0_id`/`feasibility_id` are never
cleared — they remain the most recent completed artifacts; a future
`IdeaEngine` re-run replaces them, not this action.

### Artifact type

Stored under `"feasibility_report"`, centralized as
`FEASIBILITY_REPORT_ARTIFACT_TYPE` in `app/engines/feasibility/models.py`.

## Phase 7 — R1 Deep Research Engine (approved)

Adds `app/engines/research_r1/`, the fourth content engine:

```
R1_RESEARCH -> R1ResearchEngine -> deterministic queries (<=10)
  -> ResearchRetriever -> generate_structured(..., ResearchPackage)
  -> business validation (URL/id integrity, evidence-by-status)
  -> persist "research_r1" -> transition to NARRATIVE
```

**`ResearchPackage` is the factual source of truth for everything
downstream.** Phase 7 still reasons over snippet-level retrieved evidence
only — no full webpage or PDF fetching exists, and the prompt explicitly
forbids the model from claiming otherwise. **No real retriever provider
exists** — only `FakeResearchRetriever`. **No Narrative Engine exists
yet** — the transition to `NARRATIVE` only changes project state; nothing
executes there. **No autonomous orchestrator exists.**

### Preconditions

`project.state == R1_RESEARCH`, plus a valid, matching `IdeaCandidate`,
`ResearchR0`, *and* `FeasibilityReport` — and, uniquely to R1, the stored
`FeasibilityReport.status` must be `PASS`. All of this is checked before
any `ModuleRun`, retriever call, or LLM call
(`EngineStateError` / `MissingIdeaArtifactError` /
`MissingResearchR0ArtifactError` / `MissingFeasibilityArtifactError` /
`FeasibilityNotPassedError`).

### Query planning (`queries.py`)

Deterministic, bounded at `MAX_R1_SEARCH_QUERIES = 10`, deduplicated —
never an LLM-driven or recursive search loop. Core-mechanism and
authoritative-source queries always fire; dispute queries fire per R0
`major_risks` entry, a misconception query fires only when the idea's own
`primary_payoff` is `REVERSAL`, and a historical/timeline query is
included at lowest priority, surviving only if budget remains after
higher-priority categories and the cap.

### Business validation (`validation.py`)

Runs after `generate_structured` returns a Pydantic-valid
`ResearchPackage` — Phase 3's structured layer is untouched. One pass
checks, together: every `Source.url` is in the retrieved evidence set (no
invented sources), every `Claim.source_ids` entry resolves to a real
`Source.source_id` and vice versa, `claim_id`/`source_id` values are each
unique, and `SAFE`/`QUALIFIED`/`DISPUTED` claims each have at least one
source (`UNCERTAIN`/`PROHIBITED` may have none). On any violation, the
engine issues exactly one bounded correction call
(`generate_structured(..., max_retries=0)`) listing every problem
together; if the corrected output still fails, `R1BusinessValidationError`
carries the full remaining issue list and the run fails cleanly — nothing
is silently dropped or repaired.

`ResearchPackage.central_question` is always deterministically overridden
to `IdeaCandidate.central_question` after generation, regardless of what
the model wrote — no correction call is spent on this alone, the same
pattern as R0's `idea_id` override (Phase 5).

### Simplification boundary — a documented locked-schema accommodation

`ResearchPackage.simplification_boundary` is a locked, plain `str` field
(Phase 1). The approved research process calls for four distinct
components (`safe_model`, `allowed_simplifications`,
`omitted_complexity`, `dangerous_oversimplifications`). Rather than adding
new fields to a locked contract, the prompt requires the model to write
all four as clearly labeled parts within that one string. See Phase 7's
Spec Deviations in `docs/CHANGELOG.md` for the full reasoning.

### Artifact type

Stored under `"research_r1"`, centralized as `RESEARCH_R1_ARTIFACT_TYPE`
in `app/engines/research_r1/models.py`.

## Phase 8 — Narrative Engine (approved)

Adds `app/engines/narrative/`, the fifth content engine, and extends
`app/review/`:

```
NARRATIVE -> NarrativeEngine -> NarrativePlan persisted
  -> transition to NARRATIVE_REVIEW -> explicit human review
     REVISE          -> NARRATIVE
     BACK_TO_RESEARCH -> R1_RESEARCH
     APPROVE          -> NOT IMPLEMENTED (see gap below)
```

**`ResearchPackage` remains the factual source of truth** — the Narrative
Engine may not invent a claim; every factual element it uses must trace
back to a `claim_id` in the `ResearchPackage` loaded from
`Project.research_r1_id`. **`NarrativePlan` owns story architecture only**
— questions, transitions, curiosity framing, Tí's reactions. **No Script
Engine and no Packaging P0 exist yet.** **No autonomous orchestrator
exists** — `NarrativeEngine.run()` and every review action are invoked
directly by a caller.

### Packaging P0 state-graph gap (read this before touching narrative approval)

The locked `ProjectState` graph (`app/workflow/states.py`, Phase 2) gives
`NARRATIVE_REVIEW` exactly one forward edge: directly to `SCRIPT`. There is
no `PACKAGING_P0` state anywhere in the locked enum, even though the
approved architecture places Packaging P0 between Narrative and Script.
Implementing a narrative "approve" action in Phase 8 would therefore have
had no honest target except `SCRIPT` — silently skipping a pipeline stage
that doesn't exist yet.

**Per this phase's explicit instructions, that transition was not
implemented.** `app/review/service.py` defines `approve_narrative()` with
the intended future signature, but it unconditionally raises
`NarrativeApprovalNotAvailableError` and touches nothing — no database
call, no state mutation. `revise_narrative()` (`NARRATIVE_REVIEW ->
NARRATIVE`) and `send_narrative_back_to_research()` (`NARRATIVE_REVIEW ->
R1_RESEARCH`) both use existing, valid locked edges and are fully
implemented. This gap must be resolved by a future phase — either by
adding a state (a locked-contract change requiring explicit approval) or
by routing packaging some other approved way — before narrative approval
can exist.

### Preconditions

`project.state == NARRATIVE`, plus a valid, matching `IdeaCandidate` and
`ResearchPackage` (via `Project.research_r1_id`) — checked before any
`ModuleRun`, LLM call, or write (`EngineStateError` /
`MissingIdeaArtifactError` / `MissingResearchPackageArtifactError`).
`ResearchR0`/`FeasibilityReport` are not loaded — they are not needed and
must never become an alternate source of factual truth.

### Business validation (`validation.py`)

Runs after `generate_structured` returns a Pydantic-valid `NarrativePlan`.
Two independent checks: **question-ladder structure** (unique node ids,
every `creates_next_question` reference resolves to a real node with no
self-reference, exactly one final node with `creates_next_question =
null`, and the ladder forms one connected linear chain covering every
node — no branches, no cycles, no orphans) and **claim referential
integrity** (every `claim_id` referenced in `question_ladder` or
`claim_ids_used` must exist in the `ResearchPackage`, and must never be a
`PROHIBITED` claim). On any violation, the engine issues exactly one
bounded correction call; if the corrected output still fails,
`NarrativeBusinessValidationError` carries the full remaining issue list
and the run fails cleanly. Causal quality of the ladder (does answering Qn
genuinely motivate Qn+1) is a prompting concern the deterministic
validator cannot check — that responsibility stays with the prompt.

`NarrativePlan.central_question` is always deterministically overridden
to `IdeaCandidate.central_question` after generation, the same pattern as
R0's `idea_id` and R1's `central_question` overrides.

### Artifact type

Stored under `"narrative_plan"`, centralized as
`NARRATIVE_PLAN_ARTIFACT_TYPE` in `app/engines/narrative/models.py`.

## Phase 9 — Packaging P0 + State Contract Migration (approved)

**This phase was explicitly authorized to change locked contracts**, in
exactly the three ways below — nothing else was touched.

### Authorized contract migrations

1. **`ProjectState`** (`app/models/common.py`) gained exactly one new
   member: `PACKAGING_P0`. No `PACKAGING_REVIEW`, `PACKAGING_P1`,
   `PUBLISH`, or any other state was added.
2. **`Project`** (`app/models/project.py`) gained exactly one new field:
   `packaging_prototype_id: UUID | None = None` — confirmed absent, and
   no semantically equivalent field existed under a different name, before
   adding it. Phase 2's artifact-reference whitelist and SQLite
   `ProjectRow` schema (`app/storage/projects.py`,
   `app/storage/orm.py`) were extended to carry it.
3. **`PackagingPrototype`** (`app/models/packaging.py`) gained exactly one
   new field: `id: UUID = Field(default_factory=uuid4)` — confirmed
   absent (Case B from the phase's compatibility check) before adding it,
   matching the identical pattern every other artifact model already
   uses. No other `PackagingPrototype` field was touched.

### State graph migration

The locked transition graph (`app/workflow/states.py`) changed exactly as
authorized:

```
NARRATIVE_REVIEW: was {SCRIPT, NARRATIVE, R1_RESEARCH, ARCHIVED}
                  now {PACKAGING_P0, NARRATIVE, R1_RESEARCH, ARCHIVED}

PACKAGING_P0 (new): {SCRIPT, NARRATIVE, R1_RESEARCH}
```

The direct `NARRATIVE_REVIEW → SCRIPT` edge is gone — both routes were
never active simultaneously. `PACKAGING_P0` intentionally has **no** edge
to `IDEA_DISCOVERY` or `ARCHIVED`: recovery from packaging stays local
(back to `NARRATIVE` for a framing problem, or `R1_RESEARCH` for a
factual gap), per the phase's explicit "keep recovery local" instruction.
Every other edge in the graph — all of Phases 2 through 8's transitions —
is byte-for-byte unchanged; this was verified with dedicated tests, not
just visual inspection.

### Narrative approval activated

Phase 8's `approve_narrative()` stub (which always raised
`NarrativeApprovalNotAvailableError`) is now a real, deterministic review
action: requires `NARRATIVE_REVIEW` and a valid, matching `NarrativePlan`;
persists `HumanApproval(APPROVED)`; transitions to `PACKAGING_P0`.
**`NarrativeApprovalNotAvailableError` was removed** — it had no
remaining legitimate use once the gap it existed to document was closed.

### Packaging P0 Engine (`app/engines/packaging_p0/`)

Tests whether the approved narrative can be expressed as a strong, honest
viewer promise before script writing — not final title/thumbnail
production. Preconditions require `PACKAGING_P0` plus valid, matching
`IdeaCandidate`, `ResearchPackage` (factual truth), and `NarrativePlan`
(payoff ownership). Returns exactly one recommended `PackagingPrototype`
via `generate_structured` — never a menu of options. Promise integrity
(is the promise actually deliverable by the research and narrative) stays
LLM-evaluated per the approved scope — Phase 9 does not build a second
LLM critic or a fake deterministic claim-extraction engine for this.

Deterministic business validation covers only what's genuinely
structural: `promise`/`title_direction`/`thumbnail_conflict`/
`viewer_expectation` must each be non-blank (one bounded correction
attempt, `PackagingBusinessValidationError` if still invalid).
**`risk_of_misleading == HIGH` is never a validation failure** — it is a
legitimate, honestly-reported outcome. The engine persists a HIGH-risk
prototype exactly like any other, and — critically — **never transitions
project state itself**, even on success; the project stays in
`PACKAGING_P0` awaiting an explicit human decision. That decision, not
the engine, is where HIGH risk actually gets blocked.

### Human Packaging P0 review (`app/review/service.py`)

`approve_packaging_p0`: requires `PACKAGING_P0` + a valid, matching
`PackagingPrototype`; additionally requires `risk_of_misleading != HIGH`
(`PackagingRiskTooHighError` otherwise, before any approval is persisted)
transitions to `SCRIPT` on success. `revise_packaging_p0`
(`PACKAGING_P0 → NARRATIVE`) and `send_packaging_back_to_research`
(`PACKAGING_P0 → R1_RESEARCH`) both require a valid `PackagingPrototype`
artifact *and* non-blank feedback — unlike the narrative-stage backward
actions, the spec explicitly required artifact verification here too.
None of the three auto-runs any engine.

### Artifact type

Stored under `"packaging_prototype"`, centralized as
`PACKAGING_PROTOTYPE_ARTIFACT_TYPE` in `app/engines/packaging_p0/models.py`.

### Database compatibility note

`ProjectState` is persisted as plain text, so existing rows with any
pre-Phase-9 state value remain readable without any migration — the enum
gaining a member doesn't affect already-stored text. The new
`packaging_prototype_id` column, however, only exists in databases
created (or re-created) after this migration; **a local SQLite database
file created before Phase 9 will not have this column and must be
recreated** (there is still no migration framework, deliberately, per
Phase 2 and this phase's own scope — `init_database()` remains a
fresh-schema tool, not an ALTER-TABLE tool). All test databases are
created fresh per test via `tmp_path`, so this only affects a real local
`data/motily.db` a person may have created by hand.

### What still doesn't exist (as of Phase 9)

No Script Engine, no Packaging P1, no thumbnail image generation, no
title A/B testing, no autonomous orchestrator, no real LLM or search
provider.

## Phase 10 — Script Engine (approved)

Adds `app/engines/script/`, the seventh content engine:

```
SCRIPT -> ScriptEngine -> generate_structured(..., ScriptPlan)
  -> business validation (structure, narrative-node integrity/order,
     claim integrity, duration sanity)
  -> persist "script_plan" -> transition to SCRIPT_VERIFICATION
```

**`ScriptPlan` owns final spoken wording only.** It does not own factual
truth (`ResearchPackage`), story architecture (`NarrativePlan`), or the
viewer promise (`PackagingPrototype`) — the Script Engine must respect all
three. **Script Verification is NOT implemented yet** — the transition to
`SCRIPT_VERIFICATION` only changes project state; nothing executes there.
No Voice or Visual module exists. No autonomous orchestrator exists.

### Compatibility findings (no migration needed or authorized this phase)

All six required checks were read directly from the locked files before
any code was written:

1. `ScriptPlan.id` — already exists.
2. `ScriptBeat.narrative_node` — required, non-nullable `str`, no
   opening/ending sentinel anywhere in the locked contract. **Resolution:**
   every beat, including ones serving an opening or closing narrative
   role, must reference a real `QuestionLadderNode.id` — the Question
   Ladder already spans the full hook-to-resolution arc, so no invented
   `"OPENING"`-style id is needed. This was reasoned through as an
   implementation detail, not a genuine contract ambiguity.
3. `ScriptBeat.micro_hook` — plain `str | None`, not a structured/enum
   type. Left unmodified, per the explicit instruction; the six
   conceptual hook types (QUESTION, CONTRADICTION, ESCALATION, REVEAL,
   CHARACTER, INCOMPLETE_CAUSAL_CHAIN) are enforced as prompt guidance
   only.
4. `ScriptLine.emotion` — plain `str | None`, no enum. Left unmodified;
   guided by prompt examples (curious, skeptical, excited, serious,
   deadpan, panic, low-energy) rather than a closed type.
5. `Project.script_plan_id` — already exists (one of Phase 1's original
   six reference fields).
6. `SCRIPT → SCRIPT_VERIFICATION` — already a valid locked edge from
   Phase 2, untouched by Phase 9.

No locked contract was changed in Phase 10.

### Preconditions

`project.state == SCRIPT`, plus valid, matching `IdeaCandidate`,
`ResearchPackage`, `NarrativePlan`, and `PackagingPrototype` — all four
checked before any `ModuleRun`, LLM call, or write.

### Business validation (`validation.py`)

Runs after `generate_structured` returns a Pydantic-valid `ScriptPlan`.
One pass checks, together: `beats` non-empty and every beat has at least
one line; `beat_id`/`line_id` uniqueness (globally, across the whole
plan); every `ScriptBeat.narrative_node` names a real
`QuestionLadderNode.id`; first appearances of each narrative node follow
the approved ladder order (walked via `creates_next_question`, not list
order) without regressing — repeating a node is fine, jumping backward is
not; every `ScriptLine.claim_ids` entry exists in `ResearchPackage.claims`
and is never `PROHIBITED`; `estimated_duration_seconds` falls in
`[420, 660]` (target 480–600, modest variance tolerated — Pydantic's own
`<= 0` hard-reject is a separate, stricter, always-on check on the locked
model itself). On any violation, one bounded correction call is issued
listing every problem together; if still invalid,
`ScriptBusinessValidationError` carries the full remaining list.

### Voice contract

Tí's voice is enforced entirely through the prompt, not through schema
validation (there is no deterministic way to check "does this sound
conversational"): conversational character voice (not teacher/lecturer/
MC/documentary narrator), direct `"bạn"` address rather than `"mọi
người"`, strategic `"Tí"` self-reference, short spoken TTS-readable
sentences, light censored profanity only, and the allowed
`ScriptLineFunction` set exactly as locked (no invented functions).

### What Phase 10 deliberately does not check

Semantic quality is out of scope for deterministic validation here:
whether a micro-hook genuinely emerges from story logic, whether
qualification wording for a `QUALIFIED` claim is adequate, and whether
the script actually pays off the `PackagingPrototype` promise are all
prompt-enforced, not code-verified — per the explicit instruction not to
build a second LLM critic, an agent debate, or fake keyword matching.
Claim *verification* (as opposed to claim *reference* integrity) is
explicitly deferred to a future Phase 11.

### Artifact type

Stored under `"script_plan"`, centralized as `SCRIPT_PLAN_ARTIFACT_TYPE`
in `app/engines/script/models.py`.

## Phase 11 — Script Verification + Final Script Review (approved)

**Completes the original core MVP pipeline.** Adds
`app/engines/script_verification/`, the eighth and final content engine of
the core MVP, plus the last human gate:

```
SCRIPT_VERIFICATION -> ScriptVerificationEngine
  -> generate_structured(..., ScriptVerificationReport)
  -> deterministic status normalization (always applied)
  -> claim-reference defense-in-depth check (one bounded correction attempt)
  -> persist "script_verification_report" (no state transition)

Human resolution (app/review/service.py), from SCRIPT_VERIFICATION:
  accept_script_verification (PASS required) -> SCRIPT_REVIEW
  send_script_for_rewrite                    -> SCRIPT
  send_script_back_to_narrative              -> NARRATIVE
  send_script_back_to_research                -> R1_RESEARCH

Final Script Review (app/review/service.py), from SCRIPT_REVIEW:
  approve_final_script (PASS required) -> MVP_COMPLETE
  revise_final_script                  -> SCRIPT
  send_final_script_back_to_narrative  -> NARRATIVE
  reject_final_script                  -> ARCHIVED
```

**`ScriptVerificationEngine` is an auditor, not a co-author.** It never
modifies or re-saves the `ScriptPlan` it reviews; it only produces an
independent `ScriptVerificationReport` judging whether the exact,
already-approved script is scientifically and narratively safe to publish.
Reaching `MVP_COMPLETE` is the terminal success state of the core MVP as
originally scoped — no Voice/Visual module, no Timing/Assembly, no
Packaging P1, no YouTube publishing, no autonomous orchestrator, and no
real LLM/search provider exist yet or were added this phase.

### Compatibility findings (no migration needed or authorized this phase)

All seven required checks were read directly from the locked files before
any code was written; every one came back "already exists as expected, or
is an implementation detail with an obvious non-migrating resolution" — no
locked contract was changed in Phase 11:

1. `ScriptVerificationReport` (`app/models/script.py`) has **no `id`
   field** — confirmed absent, unlike every other artifact model. **Not
   added.** Instead, the report is stored and retrieved by
   `(project_id, artifact_type)` alone; `model_copy(update=...)` is safe to
   use on it for status normalization since it has no `model_validator`,
   unlike `ModuleRun`.
2. `Project` (`app/models/project.py`) has **no verification-report
   reference field** — confirmed absent. **Not added.** The human-review
   layer looks the current report up by artifact type directly
   (`_get_current_script_verification_report`), with no `id` to
   cross-check against — the same intentional pattern as finding 1, not a
   gap.
3. `ScriptVerificationReport.unsupported_lines` /
   `overstated_lines` / `dangerous_simplifications` are plain `list[str]`,
   not structured line/claim references. Referential-integrity validation
   in the style of the Script Engine's `claim_ids` check therefore has no
   structural target on the report itself. **Resolution:** the one
   genuine deterministic check available is a defense-in-depth pre-check
   on the *input* `ScriptPlan` (independent of `ScriptEngine`'s own,
   already-locked validation), combined with a plain substring-containment
   check of whether the LLM's own `unsupported_lines` already names each
   known-bad `line_id` — never a regex extraction of new structure from
   free text, per the explicit prohibition on that.
4. `app/workflow/states.py` — `SCRIPT_VERIFICATION` already had exactly
   `{SCRIPT_REVIEW, SCRIPT, NARRATIVE, R1_RESEARCH}` and `SCRIPT_REVIEW`
   already had exactly `{MVP_COMPLETE, SCRIPT, NARRATIVE, ARCHIVED}` —
   both added by Phase 1's original graph, both exactly matching the
   topology this phase needed. No graph change.
5. `app/storage/artifacts.py` — `save_artifact`/`get_artifact` were
   already fully generic (no artifact-type allowlist) and already handle
   an id-less model gracefully (`artifact_id = getattr(artifact_model,
   "id", None)`). No change needed to support finding 1.
6. `ProjectState.SCRIPT_REVIEW` and `ProjectState.MVP_COMPLETE`
   (`app/models/common.py`) already existed from Phase 1, unused until
   this phase activated them.
7. `ScriptPlan` immutability during verification required no new lock
   field or flag — it is enforced entirely by omission: `
   ScriptVerificationEngine` simply never calls `save_artifact` for
   `ScriptPlan`, verified by a dedicated test asserting the stored
   `ScriptPlan` is byte-for-byte unchanged after a verification run.

### Preconditions

`project.state == SCRIPT_VERIFICATION`, plus valid, matching
`ResearchPackage`, `NarrativePlan`, `PackagingPrototype`, and `ScriptPlan`
— all four checked before any `ModuleRun`, LLM call, or write. Unlike the
Script Engine, this engine does **not** load `IdeaCandidate` — nothing in
verification's scope needs it.

### Deterministic status normalization (`validation.py`)

`normalize_status` runs on every generation (initial and, if triggered,
corrected) and always enforces internal consistency between
`ScriptVerificationReport.status` and whether the three issue collections
are empty — an inconsistent LLM-supplied status is never merely accepted
or rejected, it is corrected: `PASS` iff all three lists are empty;
if issues exist but the model inconsistently reported `PASS`, the status is
raised to the milder `REFRAME` (never fabricating a `REJECT` judgment the
model never made); if the model already reported `REFRAME` or `REJECT`,
that severity choice is kept exactly as given — deterministic code
corrects internal consistency, it does not second-guess a severity
judgment the LLM actually made.

### Claim-reference defense in depth (`validation.py`, `engine.py`)

`find_deterministic_claim_issues` independently re-checks every
`ScriptLine.claim_ids` entry in the `ScriptPlan` under review against
`ResearchPackage.claims`, exactly mirroring the referential-integrity rule
`ScriptEngine` (Phase 10, locked) already enforces at generation time —
this is deliberate redundancy, not a Phase 10 regression, since a script
being verified may in principle not have come from `ScriptEngine`'s own
current logic. Any line_id whose `claim_ids` reference an unknown or
`PROHIBITED` claim is collected. `find_unacknowledged_claim_issues` then
checks whether the LLM's own `unsupported_lines` already names each one
(plain substring containment). If any are unacknowledged, exactly one
bounded correction call is issued naming them; if still unacknowledged,
`ScriptVerificationBusinessError` is raised — the one genuine business
failure path this engine has.

### What Phase 11 deliberately does not check deterministically

Everything else the verifier judges — whether a line is actually
factually unsupported, whether `QUALIFIED`/`DISPUTED` wording preserves
the required qualification, whether a simplification crosses into
dangerous territory, whether the script still honors the approved
`NarrativePlan` and `PackagingPrototype` — is LLM-evaluated per the
approved scope and enforced entirely through `prompt.py`, not through
code. Phase 11 does not build a second LLM critic, an agent debate, or
regex/keyword-based fact-checking. The engine faithfully persists
whatever the LLM legitimately reports in `overstated_lines`,
`dangerous_simplifications`, and `recommended_rewrites` — it does not
filter or reinterpret that semantic judgment.

### Human resolution and Final Script Review (`app/review/service.py`)

Two gates, both following the established deterministic, no-LLM,
no-auto-run pattern:

- **`accept_script_verification`** (`SCRIPT_VERIFICATION -> SCRIPT_REVIEW`)
  requires a valid, matching `ScriptPlan` artifact *and* the current
  `ScriptVerificationReport.status == PASS` — blocked by the new
  `ScriptVerificationNotPassedError` otherwise, mirroring
  `PackagingRiskTooHighError`'s pattern of blocking forward progress on a
  legitimate, honestly-reported non-PASS outcome rather than treating it
  as an application error. `send_script_for_rewrite` (`-> SCRIPT`),
  `send_script_back_to_narrative` (`-> NARRATIVE`), and
  `send_script_back_to_research` (`-> R1_RESEARCH`) each require only the
  state check plus non-blank feedback, matching every prior backward/
  redirect review action's pattern.
- **`approve_final_script`** (`SCRIPT_REVIEW -> MVP_COMPLETE`) — the final
  human sign-off completing the core MVP — carries the identical
  artifact-plus-`PASS`-status guard as `accept_script_verification`.
  `revise_final_script` (`-> SCRIPT`) and
  `send_final_script_back_to_narrative` (`-> NARRATIVE`) require non-blank
  feedback. `reject_final_script` (`-> ARCHIVED`) uniquely takes
  *optional* feedback — unlike every other action in this phase, a
  rejection needs no further routing information.

None of the eight actions auto-runs any engine.

### Artifact type

Stored under `"script_verification_report"`, centralized as
`SCRIPT_VERIFICATION_REPORT_ARTIFACT_TYPE` in
`app/engines/script_verification/models.py`.

### Core MVP status

With Phase 11 complete, the originally scoped core MVP pipeline — idea
through verified, human-approved script — is fully implemented end to
end: `NEW_PROJECT -> ... -> SCRIPT_VERIFICATION -> SCRIPT_REVIEW ->
MVP_COMPLETE`, with every state reachable via a real engine or a real
human-review action, and every recovery edge in the locked graph backed by
a real, tested function. What still doesn't exist, unchanged from Phase
9/10: Voice Engine, Visual Engine, Timing/Assembly, Packaging P1, YouTube
publishing, analytics, the feedback loop, an autonomous orchestrator, real
LLM/search providers, multi-agent debate, and automatic template
mutation.

## Phase 12 — Core MVP Architecture Audit + End-to-End Integration (approved)

**Not a feature phase.** Verifies that Phases 1-11's implemented
architecture actually composes into one coherent system, end to end,
using only the already-implemented public engine/review operations — no
new orchestrator. Full findings, the state-graph/engine-boundary/
provider/agent-framework audits, the human-gate and persistence-integrity
proofs, and the transaction partial-progress matrix all live in
[docs/CORE_MVP_AUDIT_v0.1.md](CORE_MVP_AUDIT_v0.1.md), not duplicated
here. This section records only what changed in the implementation.

### Two real defects found and fixed

The adversarial stale-artifact tests this phase was designed to write
(patch sections 25-27) found two genuine human-gate bypasses, both now
fixed in `app/review/service.py`:

1. **Stale `ScriptVerificationReport`** could accept a *rewritten* script
   that was never itself verified: `accept_script_verification` and
   `approve_final_script` only checked "does a report exist with
   `status == PASS`", never whether that report was generated for the
   `ScriptPlan` currently referenced by the project. Fixed by
   `_verify_script_verification_is_fresh`, which cross-checks the most
   recent successful `script_verification_engine` `ModuleRun`'s recorded
   `input_ids[3]` (the `ScriptPlan.id` it actually verified) against
   `project.script_plan_id`, raising the new `StaleScriptVerificationError`
   on mismatch.
2. **Stale `PackagingPrototype`** could bypass a fresh Packaging P0 gate
   after a narrative revision: `approve_packaging_p0` had the identical
   gap. Fixed by the analogous `_verify_packaging_is_fresh`, cross-checking
   the most recent successful `packaging_p0_engine` `ModuleRun`'s
   `input_ids[2]` (`NarrativePlan.id`) against `project.narrative_plan_id`,
   raising the new `StalePackagingPrototypeError`.

Both fixes are read-only cross-checks against data the system already
persists (`ModuleRun.input_ids`) — **no locked contract, `ProjectState`,
state graph, or repository schema was changed.** Full reproduction,
fix design, and regression-protocol details: `CORE_MVP_AUDIT_v0.1.md`,
section 9.

### No other code changes

Every other Phase 12 deliverable is either a test (`tests/integration/`)
or documentation. No engine, no domain model, and no other review
function was modified. The known transaction partial-progress
characteristic (independent `Session.begin()` blocks per repository call,
present since Phase 4) was deliberately left unchanged, per this phase's
explicit instruction — it is documented, not fixed.

### Regression

559 tests pass (540 prior + 19 new integration tests). Fixing the two
defects required updating two pre-existing `test_review_service.py`
fixture-builder helpers to also persist a plausible `ModuleRun` (matching
what a real engine run leaves behind) — every original assertion in those
tests is unchanged; nothing was weakened.

### Core MVP architecture verdict

`READY_FOR_PRODUCTION_LAYER` (Voice/Visual/Timing/Packaging P1/publishing),
conditioned on the two section-9 fixes already being in place. See
`CORE_MVP_AUDIT_v0.1.md`, section 12, for the full recommendation.

## Phase 13 — Voice Planning Engine (approved)

**Begins the production layer.** Adds `app/engines/voice_plan/`, the ninth
engine overall and the first outside the core MVP:

```
MVP_COMPLETE -> VoicePlanningEngine
  -> load+verify current ScriptPlan
  -> generate_structured(..., VoicePlan)
  -> business validation (line coverage/order/adjacency)
  -> persist "voice_plan" (no state transition)
```

**`VoicePlan` owns delivery-performance metadata only.** It does not own
wording, claims, line order, or line count — those remain `ScriptPlan`'s
alone. The engine never re-saves or modifies `ScriptPlan`, confirmed by a
dedicated test and by direct inspection (exactly one `save_artifact` call
in the whole engine, targeting `voice_plan`). **No audio is generated.**
No Voice Renderer, no TTS provider (ElevenLabs/OpenAI/Azure/local), no
voice cloning, no Visual Engine, no Timing/Assembly, and no orchestrator
exist yet or were added this phase.

### Compatibility findings (no migration needed or authorized this phase)

All 8 required checks were read directly from the locked files before any
code was written:

1. `VoicePlan`/`VoiceBeat`/`VoiceLine`/`VoiceChunk` — confirmed absent
   (repo-wide grep, zero matches). Created fresh this phase, as
   authorized.
2. `Project.voice_plan_id` — confirmed absent. **Not added** — the
   artifact is retrieved by `(project_id, "voice_plan")` alone, following
   the `ScriptVerificationReport` precedent from Phase 11.
3. `ScriptLine.pause_after` — already `PauseIntent` (NONE/SHORT/MEDIUM/
   LONG), unchanged since Phase 1.1. `VoicePlan` does not duplicate it —
   future rendering reads it directly off the (immutable) `ScriptPlan` via
   the shared `line_id`; the recommended `VoiceChunk` shape itself
   (patch section 25) carries no pause-timing field, confirming this
   reading of "preserve, don't replace with milliseconds."
4. `ScriptLine.emotion` — confirmed plain `str | None`, not an enum;
   used as prompt input only.
5. `ScriptLine.function` — confirmed `ScriptLineFunction`, a type alias
   for the locked `NarrativeFunction` enum; used for prompt-only delivery
   *tendencies*, never a deterministic mapping.
6. No production-layer `ProjectState` exists (14 states, ending at
   `MVP_COMPLETE`/`ARCHIVED`) — **not added**. `VoicePlanningEngine` is
   artifact-driven: its precondition is `project.state == MVP_COMPLETE`
   exactly, and it never transitions state.
7. `app/storage/artifacts.py` — already fully generic (no allowlist);
   needed zero changes to support a new `"voice_plan"` artifact type.
8. `ScriptLine` has no `intent` field (the patch names it as "where
   available" in its field list) — confirmed absent; the prompt uses only
   the fields that actually exist (`text`, `function`, `emotion`,
   `emphasis`, `pause_after`).

No locked contract was changed. New, freestanding additions only: four
enums (`VoiceState`, `Pace`, `Energy`, `MusicState` — `app/models/common.py`,
matching the established "every enum lives in common.py" convention) and
one new domain file (`app/models/voice.py`, matching every other
artifact-bearing domain's placement).

### Representation decision: chunks only

`VoiceChunk` (a coherent performance unit — one or more adjacent
`ScriptLine`s sharing one delivery treatment) is the single representation
used; no separate `VoiceLinePlan` was added. A chunk's `line_ids` list
already gives full per-line traceability, so a second line-level model
would be redundant — per the patch's own "choose the simplest
representation that preserves line-level traceability" instruction.
`VoicePlan = {id, script_plan_id, chunks: list[VoiceChunk]}`, exactly the
patch's own recommended shape.

### Business validation (`validation.py`)

One deterministic check unifies coverage, no-duplicates, global order, and
chunk adjacency: the VoicePlan's chunks, flattened in chunk order, must
equal the ScriptPlan's own line-id sequence exactly. Any coverage gap,
extra/unknown line, duplicate, reordering, or non-contiguous chunk breaks
that equality — proven by dedicated tests for each failure shape (missing
line, extra line, duplicate line, order violation, non-contiguous chunk).
Unknown-line and missing-line issues are reported specifically (for a
clearer correction prompt) before the unified order check runs. Structural
bounds (`chunks` non-empty, each chunk's `line_ids` non-empty,
`1 <= take_count <= 3`) are enforced by Pydantic directly, not this
module, per the patch's "prefer Pydantic validation" instruction.

### Freshness

`VoicePlan.script_plan_id` and `ModuleRun.input_ids[0]` both record the
exact `ScriptPlan.id` a plan was generated from — unlike Phase 11's
`ScriptVerificationReport`, this is a brand-new model, so freshness can be
checked directly (`voice_plan.script_plan_id == project.script_plan_id`)
without the `ModuleRun`-indirection Phase 12 needed for the locked
`ScriptVerificationReport`. Per the patch's explicit scope, only
*detectability* is proven (a dedicated test) — no downstream enforcement
blocking a stale `VoicePlan` was built this phase.

### Preconditions

`project.state == MVP_COMPLETE` exactly, plus a valid, matching
`ScriptPlan`. No other upstream artifact (`NarrativePlan`,
`PackagingPrototype`, `ResearchPackage`) is loaded — voice planning is not
factual verification, and `ScriptPlan`'s own fields already carry
everything the prompt needs.

### Artifact type

Stored under `"voice_plan"`, centralized as `VOICE_PLAN_ARTIFACT_TYPE` in
`app/engines/voice_plan/models.py`.

### What Phase 13 deliberately does not do

No real TTS/audio generation, no voice cloning, no music/SFX generation
(only a `music_state`/`sfx_opportunity` *plan*), no exact timestamps or
millisecond timing, no Visual Engine, no Timing/Assembly, no Packaging P1,
no orchestrator, no new `ProjectState`, and no `Project.voice_plan_id`
reference field.

## Phase 14 — Visual Planning Engine (approved)

Adds `app/engines/visual_plan/`, the tenth engine overall and the second
in the production layer:

```
MVP_COMPLETE -> VisualPlanningEngine
  -> load+verify NarrativePlan/ScriptPlan/VoicePlan/ResearchPackage
  -> verify VoicePlan freshness against the current ScriptPlan
     (StaleVoicePlanError if stale -- no LLM call, no ModuleRun, no write)
  -> generate_structured(..., VisualPlan)
  -> business validation (coverage/order/adjacency, narrative-node
     alignment, evidence-source integrity, C3 budget)
  -> persist "visual_plan" (no state transition)
```

**`VisualPlan` owns visual representation strategy only.** It answers
"what should the viewer see while this part of the story is spoken?" --
it never rewrites narration, alters a factual claim, changes narrative
order, generates a final image prompt, creates an actual asset, invents
historical evidence, or invents a diagram unsupported by the explanation.
**No image or video is generated.** No image-prompt generator, no
animation renderer, no footage downloader, no web image search, no
CapCut automation exist yet or were added this phase.

### Compatibility findings (no migration needed or authorized this phase)

All 9 required checks were read directly from the locked files before any
code was written:

1. `VisualPlan`/`VisualBeat` — confirmed absent (repo-wide grep). Created
   fresh this phase, as authorized.
2. No visual-domain model existed under any name — confirmed absent.
3. `Project.visual_plan_id` — confirmed absent. **Not added** — retrieved
   by `(project_id, "visual_plan")` alone, following the
   `ScriptVerificationReport`/`VoicePlan` precedent.
4. `app/storage/artifacts.py` — already fully generic; zero changes
   needed to support a new `"visual_plan"` artifact type.
5. `NarrativePlan.question_ladder[].id` — confirmed the exact id space
   `ScriptBeat.narrative_node` already draws from; reused directly as the
   only valid `VisualBeat.narrative_node` values, per the patch's
   instruction not to invent OPENING/ENDING sentinels.
6. `ScriptBeat` — confirmed fields (`beat_id`, `narrative_node`,
   `narrative_function`, `lines`, `micro_hook`), unchanged since Phase 1.
7. `ScriptLine.visual_opportunity` — confirmed plain `str | None`, not a
   structured type; used as prompt input only.
8. `VoicePlan`/`VoiceChunk` (Phase 13) — confirmed fields fresh; `chunks`
   provide `energy`/`pace`/`music_state` used as rhythm *context* only
   (no forced 1:1 `VoiceChunk` <-> `VisualBeat` mapping, per the patch's
   explicit instruction).
9. No production-layer `ProjectState` exists (still 14 states, ending at
   `MVP_COMPLETE`/`ARCHIVED`) — **not added**. Precondition is
   `project.state == MVP_COMPLETE` exactly, matching Phase 13's engine;
   never transitions state.

No locked contract was changed. New, freestanding additions only: five
enums (`VisualLevel`, `VisualFunction`, `VisualMediaType`,
`ComplexityClass`, `VisualTiState` -- `app/models/common.py`, matching the
established "every enum lives in common.py" convention) and one new
domain file (`app/models/visual.py`).

### ResearchPackage: treated as effectively mandatory, not optional

The patch's own input-loading framing (section 4) lists `ResearchPackage`
as optional/"if useful", alongside `PackagingPrototype`. However, the
patch's business-validation list (section 44, items H/I/J) makes
evidence-source-id validation an unconditional required check -- and that
check is only meaningful with `ResearchPackage.sources` available to
validate against. Rather than leaving `EVIDENCE_MEDIA`'s "valid source id"
requirement structurally unenforceable on some runs, this phase always
loads and verifies `ResearchPackage` (a `MissingResearchPackageArtifactError`
precondition, exactly like `NarrativePlan`/`ScriptPlan`/`VoicePlan`).
`PackagingPrototype` remains genuinely unused -- Visual Planning does not
need the viewer-promise contract to decide what the viewer sees per beat.

### Visual grammar (L1/L2/L3) and media routing

Implemented entirely as prompt semantics, per the patch's own instruction
not to make L1's "avoid people/text" guidance "an overly rigid business
validator": `VisualLevel` (`L1_ESTABLISH`/`L2_ACTION`/`L3_RELATIONSHIP`),
`VisualFunction` (`STORY`/`EVIDENCE`/`MECHANISM`/`METAPHOR`/`EMPHASIS`),
and the 7-value `VisualMediaType` router are all locked, closed enums with
their full semantics (L2's attention-path principle, L3's PRIMARY/
SECONDARY/CONTEXT hierarchy via `primary_focus`/`secondary_elements`/
`context_elements`, the Evidence Rule, the movement principle, the
historical-causality guard) carried entirely in `prompt.py`, not enforced
by code beyond what's genuinely structural.

### Business validation (`validation.py`)

Line coverage/order/adjacency reuses Phase 13's VoicePlan philosophy
exactly: the VisualPlan's flattened `script_line_ids`, across all beats in
beat order, must equal the ScriptPlan's own line-id sequence. New to this
phase: every beat's `narrative_node` must name a real
`NarrativePlan.question_ladder` id, and a beat may only cover ScriptLines
that all resolve back to that same single narrative node (checked by
deriving each covered line's actual `ScriptBeat.narrative_node` and
requiring exactly one distinct value that matches the beat's own
declaration); `EVIDENCE_MEDIA` beats require at least one
`evidence_source_id` that exists in `ResearchPackage.sources`, and no
other media type may declare one at all; and, for any plan of 5 or more
beats, `C3` beats may not exceed 20% of the total (using integer
cross-multiplication, not floating-point division, to avoid any rounding
ambiguity at the exact boundary). Structural bounds (`beats`/
`script_line_ids` non-empty, `beat_id`/`narrative_node`/`concept`/
`primary_focus` non-blank) are enforced by Pydantic directly.

### Freshness

Two layers, per the patch's explicit distinction between a precondition
gate and detectable metadata:

- **Precondition gate** (`_verify_voice_plan_is_fresh`): before any LLM
  call, `ModuleRun`, or write, the current `VoicePlan.script_plan_id` must
  equal `Project.script_plan_id` exactly, or `StaleVoicePlanError` is
  raised. This is the one precondition in the whole codebase that blocks
  purely on a cross-artifact mismatch rather than a missing/invalid
  artifact -- a genuine production-integrity gate, not a "missing
  artifact" case.
- **Detectable metadata only** (`VisualPlan.script_plan_id`/
  `voice_plan_id` plus `ModuleRun.input_ids`): proves a stale `VisualPlan`
  is identifiable after either upstream artifact changes, without building
  any downstream enforcement -- per the patch's explicit scope for this
  phase.

### Preconditions

`project.state == MVP_COMPLETE` exactly, plus valid, matching
`NarrativePlan`, `ScriptPlan`, `VoicePlan` (existence check), and
`ResearchPackage` -- in that order, with the VoicePlan freshness gate
immediately after the VoicePlan existence check and before
`ResearchPackage` is even loaded.

### Artifact type

Stored under `"visual_plan"`, centralized as `VISUAL_PLAN_ARTIFACT_TYPE`
in `app/engines/visual_plan/models.py`.

### What Phase 14 deliberately does not do

No image generation, no video generation, no image-prompt generation, no
frame-by-frame storyboard, no footage downloading, no web image search, no
CapCut automation, no Timing/Assembly, no Packaging P1, no TTS/audio
rendering, no orchestrator, no new `ProjectState`, and no
`Project.visual_plan_id` reference field.

## Phase 15 — Timing / Assembly Planning Engine (approved)

Adds `app/engines/assembly_plan/`, the eleventh engine overall and the
third in the production layer:

```
MVP_COMPLETE -> AssemblyPlanningEngine
  -> load+verify ScriptPlan/VoicePlan/VisualPlan
  -> verify VoicePlan freshness against the current ScriptPlan
     (StaleVoicePlanError if stale)
  -> verify VisualPlan freshness against the current ScriptPlan AND the
     current VoicePlan (StaleVisualPlanError if either is stale)
  -> generate_structured(..., AssemblyPlan)
  -> deterministic normalization (plan-level ids, every segment's
     voice_chunk_ids)
  -> business validation (visual-beat coverage/order, script-line
     coverage, voice-chunk overlap, timeline contiguity, duration
     tolerance)
  -> persist "assembly_plan" (no state transition)
```

**`AssemblyPlan` owns temporal/assembly strategy only.** It answers "how
should the already-approved script, voice delivery, and visual strategy
be assembled into one coherent timeline?" -- estimated timing, segment
sequence, voice/visual alignment, and transition intent, never spoken
wording, visual concept creation, factual truth, or final rendered media.
**No CapCut automation, no XML/EDL export, no actual timeline file, no
media rendering** exist yet or were added this phase; timing here is
estimated planning metadata, never final render timing.

### Compatibility findings (no migration needed or authorized this phase)

All 10 required checks were read directly from the locked files before
any code was written:

1. `AssemblyPlan`/`TimingPlan` — confirmed absent (repo-wide grep).
   Created fresh this phase, as authorized.
2. `Project.assembly_plan_id` — confirmed absent. **Not added** --
   retrieved by `(project_id, "assembly_plan")` alone, following the
   `VoicePlan`/`VisualPlan` precedent.
3. `app/storage/artifacts.py` — already fully generic; zero changes
   needed to support a new `"assembly_plan"` artifact type.
4. `VoicePlan` fields — confirmed fresh: `{id, script_plan_id, chunks}`.
5. `VoiceChunk` fields — confirmed fresh: `{chunk_id, line_ids,
   voice_state, pace, energy, take_count, music_state, sfx_opportunity,
   notes}`.
6. `VisualPlan` fields — confirmed fresh: `{id, script_plan_id,
   voice_plan_id, beats, reusable_assets, notes}`.
7. `VisualBeat` fields — confirmed fresh: all 16 fields from Phase 14,
   unchanged.
8. `ScriptPlan.estimated_duration_seconds` — confirmed `int`, must be
   `> 0`; used directly as the duration-tolerance anchor (section 20),
   with no independent hard band reintroduced (per the patch's explicit
   instruction not to add a second 480-600-style rule).
9. `ScriptPlan`/`ScriptBeat`/`ScriptLine` ordering — confirmed:
   `[line for beat in script_plan.beats for line in beat.lines]` is the
   canonical line order, exactly as Phases 13/14 already relied on.
10. No production-layer `ProjectState` exists (still 14 states, ending at
    `MVP_COMPLETE`/`ARCHIVED`) — **not added**. Precondition is
    `project.state == MVP_COMPLETE` exactly; never transitions state.

No locked contract was changed. New, freestanding additions only: one
enum (`TransitionIntent` -- `app/models/common.py`, matching the
established "every enum lives in common.py" convention) and one new
domain file (`app/models/assembly.py`).

### One VisualBeat = one AssemblySegment (hard rule, not a preference)

The patch frames this as a preference with an explicit "STOP and report
before weakening this rule" guard if a legitimate need to split a beat is
discovered. No such need was discovered: every `AssemblySegment` maps to
exactly one `VisualBeat`, in `VisualPlan.beats`' own order, with
`script_line_ids` copied exactly from that beat. This single mapping,
enforced as one unified list-equality check against `VisualPlan.beats`'
order (the same philosophy Phase 13/14 used for `ScriptLine` coverage,
applied one level up), simultaneously proves valid beat ids, exactly-once
coverage, and correct order. Script-line coverage is still independently
re-verified against `ScriptPlan` directly, as a second, redundant check.

### Deterministic normalization

Per the patch's explicit instruction not to spend a business-correction
call on a relationship the engine can derive safely,
`normalize_assembly_plan()` runs on **every** generation attempt (initial
and corrected), before validation, and overwrites exactly these fields
from real upstream artifacts: `AssemblyPlan.script_plan_id`,
`voice_plan_id`, `visual_plan_id`, and every `AssemblySegment.
voice_chunk_ids` (recomputed as every `VoiceChunk` whose `line_ids`
intersect the segment's `script_line_ids`, in `VoicePlan.chunks`' own
order). Because validation only ever sees an already-normalized plan, a
wrong id or a wrong `voice_chunk_ids` guess can never itself trigger a
correction call or an exhaustion failure -- only genuinely LLM-owned
problems (segment/beat mapping, script-line coverage, or timing) can.

### Timing model

`start_seconds`/`end_seconds` are Pydantic-enforced non-negative with
`end > start` per segment (a pure single-object structural constraint,
handled the same way Phase 13/14 handled comparable bounds). Business
validation enforces, with a `1e-3` second floating-point tolerance: the
first segment starts at `0`; each next segment's `start_seconds` equals
the previous segment's `end_seconds` exactly (no gaps, no overlaps); and
`estimated_total_duration_seconds` equals the final segment's
`end_seconds`. Separately, the plan's total duration must stay within
`max(30, 10%)` seconds of `ScriptPlan.estimated_duration_seconds` --
verified at the exact boundary in both directions (e.g. a 500s script
tolerates 450-550s exactly, rejecting 449s/551s).

### Freshness

Three hard precondition gates, all checked before any LLM call,
`ModuleRun`, or write: `VoicePlan.script_plan_id == Project.
script_plan_id` (else `StaleVoicePlanError`), and
`VisualPlan.script_plan_id == Project.script_plan_id` **and**
`VisualPlan.voice_plan_id == <current VoicePlan>.id` (else
`StaleVisualPlanError` for either mismatch). `AssemblyPlan.script_plan_id`/
`voice_plan_id`/`visual_plan_id` plus `ModuleRun.input_ids` then record
all three ids used, proving a stale `AssemblyPlan` is detectable after any
of the three upstream artifacts changes -- detection only, no downstream
enforcement built this phase, per the patch's explicit scope.

### Preconditions

`project.state == MVP_COMPLETE` exactly, plus valid, matching `ScriptPlan`,
`VoicePlan` (existence), and `VisualPlan` (existence) -- in that order,
with the two freshness gates immediately after each plan's existence
check. `NarrativePlan` and `ResearchPackage` are never loaded -- no
business-validation rule in this phase depends on either, and the patch
explicitly forbids loading `ResearchPackage` merely for timing decisions.

### Artifact type

Stored under `"assembly_plan"`, centralized as
`ASSEMBLY_PLAN_ARTIFACT_TYPE` in `app/engines/assembly_plan/models.py`.

### What Phase 15 deliberately does not do

No CapCut automation, no XML/EDL export, no actual timeline file
generation, no audio rendering, no TTS, no image generation, no video
generation, no asset downloading, no Packaging P1, no publishing, no
analytics, no feedback loop, no orchestrator, no new `ProjectState`, and
no `Project.assembly_plan_id` reference field.

## Phase 16 — Packaging P1 / Final Packaging Engine (approved)

Adds `app/engines/packaging_p1/`, the twelfth engine overall and the
fourth in the production layer -- the final packaging reasoning pass,
made only after the script, voice, visual, and assembly plans are all
known:

```
MVP_COMPLETE -> PackagingP1Engine
  -> load+verify ScriptPlan
  -> load+verify VoicePlan -> verify VoicePlan fresh vs. current ScriptPlan
  -> load+verify VisualPlan -> verify VisualPlan fresh vs. current
     ScriptPlan AND VoicePlan
  -> load+verify AssemblyPlan -> verify AssemblyPlan fresh vs. current
     ScriptPlan, VoicePlan, AND VisualPlan
  -> load+verify the currently-referenced PackagingPrototype (P0),
     NarrativePlan, ResearchPackage
  -> generate_structured(..., FinalPackagingPlan)
  -> deterministic normalization (the four upstream-reference ids)
  -> business validation (every required field non-blank)
  -> persist "packaging_p1" (no state transition)
```

**`FinalPackagingPlan` owns the final title, thumbnail creative
direction, and final promise statement only.** Packaging P0 already
answered "is there a strong and honest viewer promise worth scripting?";
Packaging P1 answers "now that the video is fully planned, what should
the final title and thumbnail promise actually be?" P1 may sharpen,
simplify, or dramatize P0's wording, but may not silently turn the video
into a different promise. **No thumbnail image is rendered and nothing is
published** -- `thumbnail_concept` is creative direction for a future
rendering pass, never an image-generation prompt.

### Compatibility findings (no migration needed or authorized this phase)

All 10 required checks were read directly from the locked files before
any code was written:

1. `FinalPackagingPlan`/`PackagingP1` — confirmed absent (repo-wide
   grep). Created fresh this phase, as authorized.
2. `Project.packaging_p1_id` — confirmed absent. **Not added** --
   retrieved by `(project_id, "packaging_p1")` alone, following the
   `VoicePlan`/`VisualPlan`/`AssemblyPlan` precedent.
3. `app/storage/artifacts.py` — already fully generic; zero changes
   needed to support a new `"packaging_p1"` artifact type.
4. `PackagingPrototype` fields — confirmed fresh: `{id, promise,
   title_direction, thumbnail_conflict, viewer_expectation,
   risk_of_misleading}`.
5. `ScriptPlan` fields — confirmed fresh; used for opening/reveal/ending
   line highlights and overall structure (beat count, estimated
   duration), not a full line dump.
6. `NarrativePlan.opening`/`ending` — confirmed fresh, unchanged since
   Phase 1.
7. `VisualPlan`/`VisualBeat` fields — confirmed fresh, unchanged since
   Phase 14; used for thumbnail-feasibility context (dominant concepts,
   Tí states, reuse keys).
8. `AssemblyPlan`/`AssemblySegment` fields — confirmed fresh, unchanged
   since Phase 15; used for opening-sequence and transition-timing
   context.
9. No production-layer `ProjectState` exists (still 14 states, ending at
   `MVP_COMPLETE`/`ARCHIVED`) — **not added**. Precondition is
   `project.state == MVP_COMPLETE` exactly; never transitions state.
10. No existing package/thumbnail/title model overlaps this role --
    `PackagingPrototype` (P0) is explicitly the pre-script gate, not a
    final-packaging contract.

No locked contract was changed, and no new enum was needed --
`RiskLevel` (LOW/MEDIUM/HIGH) is reused directly from `app/models/
common.py`, exactly as Packaging P0 already uses it. One new domain file:
`app/models/packaging_p1.py`.

### Deliberate deviation: non-blank checks are business-level, not Pydantic-level

Every other production-layer engine (Phase 13-15) enforced its required-
text fields via a Pydantic `model_validator`, so a blank LLM response
triggers `generate_structured`'s own JSON/schema retry, not the engine's
business-correction step. Phase 16 does the opposite on purpose:
`FinalPackagingPlan`'s `title`/`thumbnail_concept`/`final_promise`/
`expected_payoff`/`viewer_expectation`/`rationale`/`thumbnail_text` carry
**no** Pydantic non-blank constraint, so a schema-valid-but-blank
response reaches `validate_final_packaging_plan` and is treated as a
genuine business-correction case. This follows the patch's own section 26
heading ("BUSINESS VALIDATION") and its explicit test scenario (a
schema-valid, blank-field response must exercise the one-bounded-
correction path, not the structured-retry path) -- something impossible
to prove if blank-ness were caught at the Pydantic layer instead.

### Freshness

Three hard precondition gates, chained transitively and all checked
before any LLM call, `ModuleRun`, or write: `VoicePlan.script_plan_id ==
Project.script_plan_id` (else `StaleVoicePlanError`);
`VisualPlan.script_plan_id == Project.script_plan_id` **and**
`VisualPlan.voice_plan_id == <current VoicePlan>.id` (else
`StaleVisualPlanError`); `AssemblyPlan.script_plan_id == Project.
script_plan_id` **and** `AssemblyPlan.voice_plan_id == <current
VoicePlan>.id` **and** `AssemblyPlan.visual_plan_id == <current
VisualPlan>.id` (else `StaleAssemblyPlanError`). `VoicePlan` is loaded
purely to support this chain -- `FinalPackagingPlan` never references it
directly, and it is not part of `ModuleRun.input_ids` either. Separately,
`PackagingPrototype` is verified as the project's actual, currently-
referenced artifact (the standard id-match pattern every engine uses),
guarding against ever building final packaging on a stale or
accidentally-supplied P0. `FinalPackagingPlan.packaging_prototype_id`/
`script_plan_id`/`visual_plan_id`/`assembly_plan_id` plus `ModuleRun.
input_ids` then record all ids used, proving a stale `FinalPackagingPlan`
is detectable after any of script/visual/assembly regenerates, or P0
changes -- detection only, no downstream enforcement, per the patch's
explicit scope.

### Deterministic normalization

`normalize_final_packaging_plan()` runs on every generation attempt
(initial and corrected), before validation, and overwrites
`packaging_prototype_id`, `script_plan_id`, `visual_plan_id`, and
`assembly_plan_id` from the real loaded artifacts -- a wrong id the LLM
emits can never itself trigger a correction call, matching the identical
pattern Phase 15 established for `AssemblyPlan`.

### Preconditions

`project.state == MVP_COMPLETE` exactly, plus valid, matching
`ScriptPlan`, `VoicePlan` (existence, freshness-chain only),
`VisualPlan`, `AssemblyPlan`, `PackagingPrototype`, `NarrativePlan`, and
`ResearchPackage` -- in that order, with each freshness gate immediately
after its plan's existence check. `ModuleRun.input_ids` records
`[packaging_prototype_id, script_plan_id, visual_plan_id,
assembly_plan_id, research_package_id]` -- `research_package_id` is
included for provenance (the patch explicitly permits this); `voice_plan_id`
and `narrative_plan_id` are deliberately excluded, since the patch names
only the first four as required and `VoicePlan` is never itself a
`FinalPackagingPlan` reference.

### Artifact type

Stored under `"packaging_p1"`, centralized as `PACKAGING_P1_ARTIFACT_TYPE`
in `app/engines/packaging_p1/models.py`.

### What Phase 16 deliberately does not do

No thumbnail image rendering, no image-generation prompt, no Photoshop/
Canva automation, no YouTube publishing, no title A/B experiments, no
live CTR analytics, no packaging feedback loop, no TTS, no image/video
generation, no CapCut automation, no orchestrator, no new `ProjectState`,
and no `Project.packaging_p1_id` reference field. A HIGH-risk
`FinalPackagingPlan` is still persisted for inspection, exactly like
Packaging P0's HIGH-risk `PackagingPrototype` -- no publish gate exists
yet to block it from being used.

## Phase 17 — TTS Provider Abstraction + Voice Renderer (approved)

Adds `app/audio/` (a provider-independent TTS abstraction, mirroring
`app/llm/`) and `app/renderers/voice/` (the **first execution/rendering
component**, and the first real filesystem I/O in the runtime path):

```
MVP_COMPLETE -> VoiceRenderer
  -> load+verify ScriptPlan
  -> load+verify VoicePlan -> verify VoicePlan fresh vs. current ScriptPlan
  -> for every VoiceChunk, for every take (1..take_count):
       synthesize via TTSProvider (retry only a provider-side failure,
       up to a bounded attempt count) -> write bytes via AudioFileStore
       (no retry on a write failure)
  -> build VoiceRenderManifest -> validate its own integrity (defense in
     depth; a failure here is an internal bug, not an LLM-correctable one)
  -> persist "voice_render_manifest" (no state transition)
```

**This is not a twelfth engine.** Every engine through Phase 16 reasons
about content via an LLM; the Voice Renderer executes a plan that
already exists and reasons about nothing. It lives under `app/renderers/`,
a new top-level package, specifically so the reasoning/planning layer
(`app/engines/`) and the execution/rendering layer stay visibly and
structurally separate as more renderers (visual, assembly) are added
later.

### Compatibility findings (no migration needed or authorized this phase)

All 10 required checks were read directly from the locked files before
any code was written:

1. No TTS/audio provider abstraction existed anywhere in the repository
   (repo-wide grep) — created fresh this phase, as authorized.
2. `Project` has no audio-related reference field (`voice_render_manifest_id`
   or similar) — confirmed absent. **Not added** — retrieved by
   `(project_id, "voice_render_manifest")` alone, following every
   production-layer artifact's precedent.
3. `app/storage/artifacts.py` — already fully generic; zero changes
   needed to support a new `"voice_render_manifest"` artifact type. Audio
   bytes are never stored here or anywhere in SQLite — only the manifest's
   metadata (including each take's relative file path) is persisted.
4. `VoicePlan`/`VoiceChunk` fields — confirmed fresh, unchanged since
   Phase 13: `chunk_id`, `line_ids`, `voice_state`, `pace`, `energy`,
   `take_count` (1-3), `music_state`, `sfx_opportunity`.
5. `ScriptPlan`/`ScriptLine` fields — confirmed fresh; `ScriptLine.text`
   is the only source of spoken words, read verbatim.
6. `ModuleRun` — confirmed fresh and generic enough to log a render run
   with no schema change (`input_ids = [script_plan_id, voice_plan_id]`).
7. No production-layer `ProjectState` exists (still 14 states, ending at
   `MVP_COMPLETE`/`ARCHIVED`) — **not added**. Precondition is
   `project.state == MVP_COMPLETE` exactly; never transitions state.
8. `app/storage/database.py`'s `DEFAULT_DB_PATH = Path("data/motily.db")`
   is the only existing filesystem-path precedent — mirrored by
   `AudioFileStore`, which likewise takes a root `Path` and creates
   parent directories as needed.
9. No existing enum covered an audio container format — `AudioFormat`
   (`WAV`/`MP3`) added fresh to `app/models/common.py`, defaulting to
   `WAV` as a lossless rendering intermediate.
10. No existing code path performed real filesystem writes in the
    runtime path (`app/storage/database.py` only creates a directory for
    the SQLite file itself) — `AudioFileStore` is the first.

One new enum (`AudioFormat`), one new top-level package (`app/audio/`),
one new domain file (`app/models/audio.py`), and one new top-level
rendering package (`app/renderers/voice/`). No locked contract was
changed.

### `app/audio/`: a provider-independent TTS layer

Mirrors `app/llm/` field-for-field: `TTSRequest` (`text`, `voice_id`,
`voice_state`, `pace`, `energy`, `output_format`, `metadata`,
`provider_options`) / `TTSResponse` (`audio_bytes`, `provider`, `model`,
`audio_format`, `duration_seconds`, `provider_request_id`, `metadata`),
a `TTSProvider` Protocol with a single `synthesize()` method, a
`FakeTTSProvider` (fixed response/exception sequence, records every
request, exposes `call_count`), and `TTSSettings` (`provider`,
`voice_id`, `output_format` default `WAV`, `max_provider_retries` default
1, `default_model`) — no API key field, same rationale as `LLMSettings`.
**`TTSResponse.audio_bytes` is never persisted and never JSON-serialized**
— it is read once by the renderer, written to a file, and discarded; only
the resulting relative file path is ever recorded. Errors are two
deliberately separate hierarchies: `TTSError` -> `TTSProviderError`
(retryable) / `TTSOutputError`, and — NOT a `TTSError` subclass —
`AudioStorageError` -> `AudioPathError` / `AudioWriteError`, so a provider
failure and a filesystem failure can never be confused by a caller
handling one but not the other.

`AudioFileStore(root)` provides one operation, `write(relative_path,
data) -> Path`: an atomic write (temp file, then `Path.replace`) so a
failed write never leaves a partial file at the final path, with
best-effort temp-file cleanup on failure. `relative_path` is validated
before any filesystem access — blank, absolute (POSIX `/...`, Windows
drive-letter `C:...`, or UNC `\\server\share`), or containing a `..`
segment (after normalizing backslashes to forward slashes) all raise
`AudioPathError` with zero filesystem side effects.

### `app/models/audio.py`: the render manifest

`RenderedVoiceTake` (`render_job_id`, `chunk_id`, `take_number` 1-3,
`line_ids`, `file_path`, `duration_seconds`, `provider_request_id`,
`music_state`, `sfx_opportunity`) and `VoiceRenderManifest` (`id`,
`script_plan_id`, `voice_plan_id`, `provider`, `voice_id`,
`output_format`, `renders`, `created_at`) — metadata only. `file_path` is
always the *relative* path string the renderer itself constructed (the
same string passed to `AudioFileStore.write`), never the absolute `Path`
`write()` returns — the manifest stays portable across machines/output
roots.

### Architectural separation: `app/renderers/` vs. `app/engines/`

`app/renderers/voice/` has no `prompt.py` and no dependency on `app.llm`,
**direct or transitive** — proven by two tests: a source-level scan for
any real `import app.llm` / `from app.llm` statement anywhere under
`app/renderers/`, and a fresh-subprocess check that importing
`app.renderers.voice` never adds `app.llm`/`app.llm.*` to `sys.modules`.
This second check must run in a subprocess rather than in-process,
because another test module earlier in the same pytest run may have
already imported `app.llm` for unrelated reasons, which would make an
in-process `sys.modules` check meaningless.

This zero-dependency rule has one concrete consequence: every engine's
`models.py` (e.g. `app.engines.script.models`,
`app.engines.voice_plan.models`) imports `app.llm.models.TokenUsage` for
its own `*Result` wrapper type, which means importing even just that
module's `*_ARTIFACT_TYPE` string constant would transitively pull in
`app.llm`. `app/renderers/voice/renderer.py` therefore **duplicates**
`SCRIPT_PLAN_ARTIFACT_TYPE`/`VOICE_PLAN_ARTIFACT_TYPE` as local string
literals rather than importing them — a deliberate, commented exception
to the "import the constant, don't duplicate it" convention every
production-layer engine otherwise follows, justified because these are
plain, effectively-immutable storage keys (not reasoning logic) and the
architectural boundary is the harder constraint.

The renderer's own errors (`RendererStateError`,
`MissingScriptPlanArtifactError`, `MissingVoicePlanArtifactError`,
`StaleVoicePlanError`, `VoiceRenderManifestIntegrityError`) are likewise
package-local rather than importing `app.engines.errors.EngineStateError`
— consistent with "each package owns its own error hierarchy" (every
engine already redefines `MissingScriptPlanArtifactError` locally rather
than sharing one), and it additionally keeps `app/renderers/` free of any
import from `app/engines/` at all.

### Freshness

One direct-dependency gate, checked before any provider call, `ModuleRun`,
or file write: `VoicePlan.script_plan_id == Project.script_plan_id`, else
`StaleVoicePlanError`. Unlike Phase 16's transitively-chained gate, the
Voice Renderer has exactly one upstream planning artifact (`VoicePlan`)
and one root artifact it must agree with (`ScriptPlan`) — there is nothing
further to chain through yet.

### Provider retries vs. write retries — deliberately different policies

`_synthesize_with_retry` allows up to `1 + TTSSettings.max_provider_retries`
total attempts per take, and retries **only** `TTSProviderError`
(a `TTSOutputError` or any other exception propagates immediately,
unretried) — mirroring `generate_structured`'s "only the retryable
failure class is retried" principle from `app/llm/structured.py`. Once a
provider call succeeds, `AudioFileStore.write()` is called exactly once,
**outside** the retry loop: a write failure is never a reason to
re-invoke the provider (the audio was already successfully generated),
and it is proven by a test that queues exactly one provider response for
a single-take plan whose write is forced to fail — if the renderer ever
retried the provider afterward, `FakeTTSProvider` would raise its own
"exhausted" error instead of the expected `AudioWriteError`.

A mid-run failure (provider exhaustion, a write error, or a manifest
integrity failure) does **not** roll back files already written by
earlier takes in the same run — they are left on disk as harmless
orphans, and only the `VoiceRenderManifest` artifact is withheld (so nothing
downstream can ever reference an incomplete render). This is deliberate,
documented behavior, not a known defect: cleanup of orphaned render files
is out of scope for Phase 17.

### Deterministic construction, not generation

There is no LLM output to validate against a schema here — every
`RenderedVoiceTake` is built directly from the corresponding `VoiceChunk`
and the provider's own `TTSResponse`, with no ambiguity to correct. So
`validate_voice_render_manifest()` runs once, after the whole manifest is
built, purely as defense-in-depth against an internal construction bug:
it checks the manifest's own `script_plan_id`/`voice_plan_id` against the
current inputs, chunk coverage (no missing/unknown chunk), exact take
coverage per chunk (via a set-equality check on take numbers, **plus** a
separate duplicate-take-number check — a chunk with actual takes `[1, 1]`
against an expected `{1, 2}` would otherwise pass a naive set comparison,
exactly the reasoning Phase 13's `validate_voice_plan` already
established), `line_ids` exactness per take, and file-path
safety/uniqueness. Any issue raises `VoiceRenderManifestIntegrityError`
directly — there is no correction-retry step, since there is no LLM call
to retry.

### Text ownership and delivery-metadata passthrough

A chunk's synthesis text is the **verbatim** newline-joined (`"\n"`)
concatenation of its lines' `ScriptLine.text`, in `chunk.line_ids` order
— the renderer never rewrites, trims, or reformats a word of the locked
script. `voice_state`/`pace`/`energy` are copied from the `VoiceChunk`
onto the `TTSRequest` completely unchanged — there is no vendor-specific
mapping table in Phase 17 (a real provider integration would own that
translation itself, behind the same `TTSProvider.synthesize()` call).

### Preconditions

`project.state == MVP_COMPLETE` exactly, plus valid, matching
`ScriptPlan` and a fresh `VoicePlan`. `ModuleRun.input_ids` records
`[script_plan_id, voice_plan_id]`.

### Artifact type

Stored under `"voice_render_manifest"`, centralized as
`VOICE_RENDER_MANIFEST_ARTIFACT_TYPE` in
`app/renderers/voice/models.py`. Re-running the renderer for the same
project **upserts** the manifest artifact (matching `save_artifact`'s
existing upsert-only convention) and writes takes to the same
deterministic `{render_job_id}.{ext}` relative paths, so a rerender
overwrites both the manifest and every audio file in place.

### What Phase 17 deliberately does not do

No real TTS vendor integration (no ElevenLabs, no OpenAI TTS, no Gemini
audio, no Azure TTS, no local neural TTS, no voice cloning, no model
downloading, no microphone recording) — `FakeTTSProvider` is the only
implementation. No music rendering, no SFX generation, no Visual
Renderer, no CapCut automation, no publishing, no analytics, no
orchestrator, no multi-agent framework, and no new `ProjectState` or
`Project` reference field. Orphaned audio files from a failed run are
never cleaned up automatically (see above).

## Phase 18 — Gemini TTS Provider Integration (approved)

Adds `app/audio/providers/gemini.py`: `GeminiTTSProvider`, the **first
real** `TTSProvider` implementation. This is a provider-adapter phase
only — `TTSProvider`, `TTSRequest`, `TTSResponse`, `TTSSettings`, and
`VoiceRenderer` are all **unchanged**, proving that Phase 17's boundary
was drawn correctly: a second, real provider slots in without touching
any of them.

```
VoiceRenderer -> TTSProvider
                   |- FakeTTSProvider   (Phase 17, unchanged)
                   \- GeminiTTSProvider (Phase 18, new)
                        -> google.genai.Client.models.generate_content(...)
                        -> raw PCM bytes (+ mime_type)
                        -> wrap as WAV (stdlib `wave`, in-memory)
                        -> TTSResponse
```

### Compatibility findings (no migration needed or authorized this phase)

All 10 required checks were read directly from the locked files (and,
for the SDK itself, from a freshly-installed package via live
introspection, not from memory) before any code was written:

1. `TTSProvider` Protocol — unchanged: one method, `synthesize(request) ->
   TTSResponse`, `@runtime_checkable`.
2. `TTSRequest` fields — unchanged: `text`, `voice_id`, `voice_state`,
   `pace`, `energy`, `output_format`, `metadata`, `provider_options`.
3. `TTSResponse` fields — unchanged: `audio_bytes`, `provider`, `model`,
   `audio_format`, `duration_seconds`, `provider_request_id`, `metadata`.
4. `TTSSettings` — unchanged: `provider`, `voice_id`, `output_format`,
   `max_provider_retries`, `default_model`; still no API key field.
5. `AudioFormat` — unchanged: `WAV`/`MP3`.
6. `VoiceState`/`Pace`/`Energy` — unchanged: 8/3/3 fixed values, since
   Phase 13.
7. `VoiceRenderer`'s assumption about provider output: it writes
   `response.audio_bytes` directly to a `.{output_format}`-suffixed file
   with no validation of its own -- so `GeminiTTSProvider` must hand back
   a genuinely complete WAV container, never raw PCM.
8. `google-genai` — confirmed absent (no install, no declaration).
   Installed fresh (`google-genai==2.22.0`) and its actual shape
   introspected live: `genai.Client(api_key=...)`,
   `client.models.generate_content(model=, contents=, config=)`,
   `genai_types.GenerateContentConfig(response_modalities=["AUDIO"],
   speech_config=...)`, response path
   `response.candidates[0].content.parts[0].inline_data.{data,mime_type}`
   plus `response.response_id`, and the error family
   `google.genai.errors.APIError` (base of `ClientError`/`ServerError`).
9. Dependency file: only `pyproject.toml` (PEP 621, hatchling). Added
   `google-genai>=2.0,<3.0` there, matching the existing
   floor-and-ceiling-on-major-version convention already used for
   `pydantic`/`SQLAlchemy`.
10. Environment/secret conventions: none existed anywhere in `app/`
    before this phase (no `os.environ`/`dotenv` usage) — `LLMSettings`
    and `TTSSettings` were deliberately designed with no API key field
    for exactly this reason. Phase 18 establishes the first one: plain
    `os.environ.get(...)`, no new dependency for it (no `python-dotenv`).

No locked contract was changed. One new subpackage,
`app/audio/providers/`.

### Two-layer configuration

`TTSSettings` (renderer-facing, provider-agnostic: `voice_id`,
`output_format`, `max_provider_retries`) and `GeminiTTSConfig`
(Gemini-adapter-facing only: `model`, `api_key_env`, `sample_rate_hz`,
`channels`, `sample_width_bytes`) are deliberately separate objects at
two different layers with no overlapping fields. `VoiceRenderer` only
ever sees `TTSSettings`; `GeminiTTSConfig` is passed directly to
`GeminiTTSProvider`'s constructor and never seen by the renderer.
`GeminiTTSConfig` deliberately has **no `voice_id` field** — voice
selection stays entirely a per-request concern
(`TTSRequest.voice_id` -> Gemini's `PrebuiltVoiceConfig.voice_name`,
copied through byte-for-byte, never lowercased or remapped), matching
`GeminiTTSConfig`'s only job: fixed, model/audio-format-level settings
that don't vary per request.

### Secret loading

`GeminiTTSProvider.__init__` reads the API key from
`os.environ[config.api_key_env]` (default `"GEMINI_API_KEY"`) **only**
when no `client` is injected, and does so eagerly (at construction time,
before returning), so a missing key fails immediately with
`GeminiTTSConfigurationError` and **zero network calls** — no
`genai.Client(...)` is ever constructed if the key is absent. When a
`client` is injected (the deterministic-test path), the environment is
never consulted at all. The key itself never appears in a log line, an
exception message, `ModuleRun` metadata, or anywhere persisted — only the
*name* of the environment variable it was supposed to come from does.

### Deterministic style mapping (VoiceState/Pace/Energy -> director's notes)

Three small, fixed, non-extreme lookup dictionaries
(`voice_state_direction`, `pace_direction`, `energy_direction` in
`app/audio/providers/gemini.py`) turn a `TTSRequest`'s
`voice_state`/`pace`/`energy` into natural-language direction, joined
into one "DIRECTOR'S NOTES" block placed before the transcript in the
prompt sent to Gemini. This is a lookup, never a computation, so the same
triple always produces the exact same instruction text. Naturalness over
extreme expressiveness is enforced by wording alone (e.g. HIGH energy ->
"high energy **without shouting or exaggeration**"; EXCITED -> "clearly
excited but controlled; **avoid shouting**") -- there is no numeric
speed/pitch parameter anywhere in this mapping, matching the spec's
explicit "do not convert to vendor numeric speed values" instruction.

### Text ownership

The transcript is never rewritten, paraphrased, translated, or
normalized: `request.text` is inserted byte-for-byte after a fixed
`"TRANSCRIPT:\n"` marker, with an explicit instruction ("Read ONLY the
transcript below. Do not add, remove, paraphrase, translate, or comment
on it.") immediately before it. Gemini is used purely as a speech
synthesizer, never as a second reasoning/rewriting pass -- exactly one
`generate_content` call per `synthesize()` call, with no LLM call of any
kind beforehand.

### PCM -> WAV wrapping and duration

Gemini's response carries raw PCM in `inline_data.data`, with
`inline_data.mime_type` sometimes carrying an authoritative sample rate
(e.g. `"audio/L16;codec=pcm;rate=24000"`). The adapter parses that rate
when present and falls back to `GeminiTTSConfig.sample_rate_hz`
otherwise -- channels and sample width have no per-response SDK signal,
so they always come from config. `_wrap_pcm_as_wav` uses only the
stdlib (`wave` + `io.BytesIO`, no `ffmpeg`, no `pydub`) to build a real,
complete WAV container in memory, and computes
`duration_seconds = frame_count / sample_rate` where `frame_count =
len(pcm_bytes) // (channels * sample_width_bytes)` -- a byte length that
doesn't divide evenly into whole frames raises `TTSOutputError` rather
than ever producing a corrupt WAV file.

### Error translation -- a narrow, documented boundary

`GeminiTTSProvider.synthesize()` catches exactly two exception families
and translates both to `TTSProviderError`: `google.genai.errors.APIError`
(the API itself responded with an error -- covers both its `ClientError`
and `ServerError` subclasses) and `httpx.HTTPError` (a transport-level
failure -- connection refused, timeout -- that never reached the API at
all). Nothing else is caught here; a genuine programming bug (e.g. a
`TypeError`) propagates unchanged, visible during development, per the
spec's explicit instruction not to blindly catch every `Exception`. A
malformed or missing response shape (no candidates, no content parts, no
inline audio data, empty audio, non-frame-aligned PCM) is handled
separately, deterministically, in `_extract_pcm_bytes`/
`_wrap_pcm_as_wav`, and translated to `TTSOutputError` instead --
`IndexError`/`AttributeError` never leak out as if they were normal
control flow. A third, Gemini-specific error,
`GeminiUnsupportedFormatError` (a `TTSOutputError` subclass), is raised
immediately -- before any network call -- when `request.output_format`
is `MP3`, since Phase 18 supports WAV only.

### No nested retry

`GeminiTTSProvider` performs **no retry of its own** -- `VoiceRenderer`
already owns the `TTSProviderError` retry policy (Phase 17), and
retrying inside the adapter too would silently multiply the effective
attempt count. Proven by a test asserting an exact call count through the
full renderer (one failure plus one success = exactly 2 SDK calls, not 3
or 4) and by a direct-call test asserting exactly one SDK call when
`synthesize()` fails outright.

### `GeminiTTSConfigurationError` is not a `TTSProviderError`

A missing API key means a client was never even constructed -- no
invocation was attempted, so nothing was retryable. `GeminiTTSConfigurationError`
therefore subclasses the generic `TTSError` base directly, **not**
`TTSProviderError`, so `VoiceRenderer`'s retry loop (which only catches
`TTSProviderError`) never wastes attempts retrying a failure that can
never succeed no matter how many times it's repeated.

### Client injection

`GeminiTTSProvider(config, client=...)` accepts an injected client
(anything exposing `.models.generate_content(...)`, matching the real
SDK's shape) for fully deterministic, offline tests -- no live network
call, and no `GEMINI_API_KEY` required, anywhere in the default test
suite. The live-network path (`client=None`, the real
`genai.Client(...)`) is exercised only by the opt-in live smoke test
below.

### Live smoke test (opt-in only)

`tests/test_gemini_tts_live_smoke.py` makes one real Gemini API call
synthesizing a short Vietnamese sample. It is gated by **both** a custom
pytest marker (`live_tts`, registered in `pyproject.toml`) and an
environment variable (`RUN_LIVE_GEMINI_TTS=1`), and skips cleanly -- no
error, no `GEMINI_API_KEY` requirement -- unless a human opts in on
purpose. The default `pytest` invocation makes zero live calls, requires
no secret, and needs no network access.

### Manual voice-evaluation utility (`scripts/evaluate_gemini_voices.py`)

Renders the same short Vietnamese sample once per voice in a small,
explicit list (default: `Puck, Achird, Zubenelgenubi, Iapetus`), writing
one WAV file per voice for a human to listen to. **No automated
naturalness scoring and no LLM-based ranking exist anywhere in this
script or elsewhere in the repository** -- it prints file paths and
stops; a human decides the canonical Tí voice later, entirely outside
this codebase's business logic. Requires an explicit voice list (never
loops over an exhaustive, dynamically-discovered voice catalog) and
keeps the sample short, to avoid unnecessary API quota usage.

**Canonical Tí voice = `Puck`** -- selected by a human after listening to
all four renders from this shortlist. Puck sounded the most natural in
Vietnamese; Achird, Zubenelgenubi, and Iapetus were rejected for sounding
more foreign/Western-accented. The selection is recorded as
`CANONICAL_TI_VOICE_ID = "Puck"` in `app/audio/config.py`, purely as an
application-level production preference: `GeminiTTSProvider` remains
fully voice-configurable via `TTSRequest.voice_id`, and the
`TTSProvider`/`TTSRequest`/`TTSResponse`/`TTSSettings` contracts are
unchanged. No voice cloning is involved -- `Puck` is one of Gemini's
existing prebuilt voices, selected, not created.

### Vietnamese language handling

The transcript is sent to Gemini exactly as `VoiceRenderer` resolved it
-- real Vietnamese text, diacritics included, never transliterated,
translated, or forced into an English locale. Gemini TTS supports
Vietnamese natively; language selection is left entirely to the model
reading the (Vietnamese) transcript, with no explicit locale parameter
set anywhere in this adapter.

### What Phase 18 deliberately does not do

No second real TTS provider (no ElevenLabs, no OpenAI TTS, no Azure TTS,
no Edge TTS, no local neural TTS), no voice cloning, no `VoiceRenderer`
redesign, no audio post-processing, no loudness normalization, no
silence insertion, no SFX, no music, no Visual Renderer, no publishing,
no orchestrator. No MP3 support for Gemini (rejected explicitly, never
silently mislabeled as WAV). The canonical Tí voice (`Puck`) was chosen
by a human listening decision, made outside this codebase's business
logic -- see the addendum above; no automated audio-quality judging by
any model, Gemini or otherwise, was used to make that choice.

## Phase 19 — Visual Provider Abstraction + Visual Renderer (approved)

Adds `app/visual/` (a provider-independent visual-render abstraction,
mirroring `app/audio/`) and `app/renderers/visual/` (the **second
execution/rendering component**, alongside Phase 17's Voice Renderer):

```
MVP_COMPLETE -> VisualRenderer
  -> load+verify ScriptPlan
  -> load+verify VoicePlan -> verify VoicePlan fresh vs. current ScriptPlan
  -> load+verify VisualPlan -> verify VisualPlan fresh vs. current
     ScriptPlan AND vs. current VoicePlan
  -> for every VisualBeat, in plan order, route deterministically by
     media_type (no LLM decision):
       GENERATED_STILL / DIAGRAM
         -> render via VisualProvider (retry only a provider-side
            failure, up to a bounded attempt count) -> write bytes via
            VisualFileStore (no retry on a write failure)
            -> RenderedVisualAsset
       ASSET_REUSE / TI_STATE
         -> no provider call -> VisualRenderRequirement(status=REUSE_ONLY)
       LIMITED_MOTION / EVIDENCE_MEDIA / AI_HERO_VIDEO
         -> no provider call
            -> VisualRenderRequirement(status=EXTERNAL_REQUIRED)
  -> build VisualRenderManifest -> validate its own integrity (defense in
     depth; a failure here is an internal bug, not an LLM-correctable one)
  -> persist "visual_render_manifest" (no state transition)
```

**This is still not an engine.** Like the Voice Renderer, the Visual
Renderer executes a plan that already exists (`VisualPlan`, Phase 14) and
reasons about nothing. **This phase proves the runtime boundary only**:
`FakeVisualProvider` is the only `VisualProvider` implementation --
no real image generation (Gemini image, Imagen, OpenAI image,
Midjourney, Stable Diffusion, ComfyUI), no real AI video generation, no
real footage download, no web image search, no stock media integration,
no diagram renderer, no CapCut automation, no visual post-processing, no
thumbnail rendering, no publishing, no orchestrator.

### Compatibility findings (no migration needed or authorized this phase)

All 12 required checks were read directly from the locked files before
any code was written:

1. No visual provider abstraction existed anywhere in the repository
   (repo-wide grep for a `VisualProvider`-shaped Protocol) -- created
   fresh this phase, as authorized.
2. No `VisualRenderManifest` model existed anywhere -- created fresh as
   `app/models/visual_render.py`, mirroring `app/models/audio.py`.
3. `Project` (`app/models/project.py`) has no visual-render-related
   reference field (`visual_render_manifest_id` or similar) -- confirmed
   absent, exactly like `voice_render_manifest_id`'s Phase 17 finding.
   **Not added** -- retrieved by `(project_id, "visual_render_manifest")`
   alone.
4. `app/storage/artifacts.py` -- already fully generic; zero changes
   needed to support a new `"visual_render_manifest"` artifact type.
   Visual asset bytes are never stored here or anywhere in SQLite -- only
   the manifest's metadata (including each rendered beat's relative file
   path) is persisted.
5. `app/audio/storage.py`'s `AudioFileStore` (atomic write via temp file
   + `Path.replace`, blank/absolute/traversing relative-path rejection)
   is the direct filesystem-storage precedent -- `VisualFileStore`
   mirrors it field-for-field, including the Windows drive-letter and
   UNC-path rejection cases.
6. `VisualPlan`/`VisualBeat` (`app/models/visual.py`, Phase 14) fields
   confirmed fresh, unchanged: `VisualPlan` (`id`, `script_plan_id`,
   `voice_plan_id`, `beats`, `reusable_assets`, `notes`); `VisualBeat`
   (`beat_id`, `script_line_ids`, `narrative_node`, `visual_level`,
   `visual_function`, `media_type`, `complexity`, `concept`,
   `primary_focus`, `secondary_elements`, `context_elements`,
   `ti_state`, `evidence_source_ids`, `motion_intent`, `reuse_key`,
   `notes`).
7. `VisualBeat.media_type: VisualMediaType` (`app/models/common.py`,
   Phase 14) confirmed fresh, exactly seven values: `ASSET_REUSE`,
   `TI_STATE`, `DIAGRAM`, `GENERATED_STILL`, `LIMITED_MOTION`,
   `EVIDENCE_MEDIA`, `AI_HERO_VIDEO` -- **not changed**; this phase's
   media router switches on it read-only.
8. `VisualBeat.complexity: ComplexityClass` (`app/models/common.py`,
   Phase 14) confirmed fresh: `C0`/`C1`/`C2`/`C3` -- **not changed** and
   not consulted by this phase's router at all (production-effort class,
   orthogonal to renderability).
9. `ModuleRun` (`app/models/module_run.py`) -- confirmed fresh and
   generic enough to log a visual render run with no schema change
   (`input_ids = [script_plan_id, voice_plan_id, visual_plan_id]`).
10. No production-layer `ProjectState` exists (still 14 states, ending
    at `MVP_COMPLETE`/`ARCHIVED`) -- **not added**. Precondition is
    `project.state == MVP_COMPLETE` exactly; never transitions state.
11. `pyproject.toml` dependencies confirmed: `pydantic`, `PyYAML`,
    `SQLAlchemy`, `google-genai` -- no image/media library present.
12. Pillow confirmed **not installed** (`import PIL` fails in the
    project's own `.venv`) -- not added this phase. `FakeVisualProvider`
    uses tiny, arbitrary placeholder bytes instead (see below), the same
    approach `FakeTTSProvider` already uses for non-decodable fixture
    audio; codec validity is deferred to a future concrete provider.

Two new enums (`VisualOutputFormat`, `VisualRequirementStatus`), one new
top-level package (`app/visual/`), one new domain file
(`app/models/visual_render.py`), and one new rendering package
(`app/renderers/visual/`). No locked contract was changed;
`VisualPlan`/`VisualBeat` needed zero changes.

### `app/visual/`: a provider-independent visual-render layer

Mirrors `app/audio/` field-for-field, adapted to still images:
`VisualRenderRequest` (`render_job_id`, `beat_id`, `media_type`,
`concept`, `primary_focus`, `secondary_elements`, `context_elements`,
`ti_state`, `motion_intent`, `reuse_key`, `output_format`, `metadata`) /
`VisualRenderResponse` (`asset_bytes`, `provider`, `model`,
`output_format`, `width`, `height`, `provider_request_id`, `metadata`), a
`VisualProvider` Protocol with a single `render()` method, a
`FakeVisualProvider` (fixed response/exception sequence, records every
request, exposes `call_count`), and `VisualSettings` (`provider`,
`output_format` default `PNG`, `max_provider_retries` default 1) -- no
API key field, same rationale as `TTSSettings`/`LLMSettings`.
**`VisualRenderRequest` is deliberately NOT an image-generation prompt**
-- it is a provider-neutral render brief copied verbatim from one
`VisualBeat`; a future concrete provider adapter is responsible for
translating this brief into whatever vendor-specific prompt/parameters
its API needs, exactly as `GeminiTTSProvider` (Phase 18) owns translating
`TTSRequest` into Gemini's own call shape.
**`VisualRenderResponse.asset_bytes` is never persisted and never
JSON-serialized** -- read once by the renderer, written to a file, and
discarded; only the resulting relative file path is ever recorded.
Errors are two deliberately separate hierarchies, exactly mirroring
`app/audio/errors.py`: `VisualError` -> `VisualProviderError`
(retryable) / `VisualOutputError`, and -- NOT a `VisualError` subclass --
`VisualStorageError` -> `VisualPathError` / `VisualWriteError`.

`VisualFileStore(root)` provides one operation, `write(relative_path,
data) -> Path`, identical in design to `AudioFileStore`: an atomic write
(temp file, then `Path.replace`) with best-effort temp-file cleanup on
failure, and the same `relative_path` validation (blank, absolute
POSIX/Windows-drive-letter/UNC, or `..`-containing all rejected with zero
filesystem side effects, backslashes normalized to forward slashes
first).

### Fake bytes, not a fake codec

`FakeVisualProvider` returns tiny, arbitrary placeholder bytes (not a
real decodable PNG/JPG) when a test doesn't override them -- exactly
like `FakeTTSProvider`'s fixture audio (`b"RIFF-fake-audio-bytes"`) is
not a real decodable WAV file. Pillow was confirmed absent (compatibility
finding 12) and was **not** added solely to make fake test bytes
"real" -- `VisualRenderResponse` only validates that `asset_bytes` is
non-empty and that `width`/`height` are positive when supplied, never
that the bytes decode as a valid image. Codec validity is a concrete
provider's own concern (as Gemini TTS's real WAV-container construction
is, in `app/audio/providers/gemini.py`), deferred entirely to whatever
phase adds the first real image provider.

### `app/models/visual_render.py`: the render manifest

`RenderedVisualAsset` (`render_job_id`, `beat_id`, `media_type`,
`file_path`, `width`, `height`, `provider_request_id`) and
`VisualRenderRequirement` (`beat_id`, `media_type`, `status`,
`reference`, `notes`; its own validator rejects `status=RENDERED` by
construction -- a rendered beat belongs in `assets`, never in
`requirements`), plus `VisualRenderManifest` (`id`, `script_plan_id`,
`voice_plan_id`, `visual_plan_id`, `provider`, `output_format`, `assets`,
`requirements`, `created_at`; its own validator requires at least one of
`assets`/`requirements` non-empty) -- metadata only. `file_path` is
always the *relative* path string the renderer itself constructed, never
the absolute `Path` `write()` returns -- the manifest stays portable
across machines/output roots, exactly like `RenderedVoiceTake.file_path`.

Unlike `VoiceRenderManifest.provider` (populated from the first actual
`TTSResponse.provider` seen), `VisualRenderManifest.provider` is always
`VisualSettings.provider` -- the renderer's configured provider name,
not a value derived from a response. This is a deliberate difference: a
`VisualPlan` may legitimately contain **zero** renderable beats (e.g. an
all-reuse/all-external plan), in which case there is no response to read
a provider name from at all, whereas every `VoicePlan` chunk always
produces at least one real take.

### Architectural separation: `app/renderers/` vs. `app/engines/`

`app/renderers/visual/` has no `prompt.py` and no dependency on
`app.llm`, **direct or transitive** -- proven by the same two-test
pattern Phase 17 established: a source-level scan for any real `import
app.llm` / `from app.llm` statement anywhere under `app/renderers/`
(re-run, still clean, and separately re-run against `app/visual/`
itself), and a fresh-subprocess check that importing
`app.renderers.visual` never adds `app.llm`/`app.llm.*` to
`sys.modules`.

For the identical reason Phase 17 documented (every engine's `models.py`
imports `app.llm.models.TokenUsage` for its own `*Result` wrapper type),
`app/renderers/visual/renderer.py` **duplicates**
`SCRIPT_PLAN_ARTIFACT_TYPE`/`VOICE_PLAN_ARTIFACT_TYPE`/
`VISUAL_PLAN_ARTIFACT_TYPE` as local string literals rather than
importing them from `app.engines.script.models` /
`app.engines.voice_plan.models` / `app.engines.visual_plan.models`. The
renderer's own errors (`RendererStateError`,
`MissingScriptPlanArtifactError`, `MissingVoicePlanArtifactError`,
`MissingVisualPlanArtifactError`, `StaleVoicePlanError`,
`StaleVisualPlanError`, `InvalidVisualBeatError`,
`VisualRenderManifestIntegrityError`) are likewise package-local, not
shared with `app/renderers/voice/errors.py` despite several near-identical
names -- consistent with "each package owns its own error hierarchy."

### Media routing (no LLM decision anywhere in this router)

A fixed, deterministic dictionary-based router, switching only on
`VisualBeat.media_type`:

| media_type        | Provider called? | Manifest outcome                  |
|--------------------|-------------------|------------------------------------|
| `GENERATED_STILL`  | Yes               | `RenderedVisualAsset`             |
| `DIAGRAM`          | Yes               | `RenderedVisualAsset`             |
| `ASSET_REUSE`      | No                | `VisualRenderRequirement(REUSE_ONLY)` |
| `TI_STATE`         | No                | `VisualRenderRequirement(REUSE_ONLY)` |
| `LIMITED_MOTION`   | No                | `VisualRenderRequirement(EXTERNAL_REQUIRED)` |
| `EVIDENCE_MEDIA`   | No                | `VisualRenderRequirement(EXTERNAL_REQUIRED)` |
| `AI_HERO_VIDEO`    | No                | `VisualRenderRequirement(EXTERNAL_REQUIRED)` |

`DIAGRAM` being provider-renderable in this Phase 19 fake runtime does
**not** mean a future real image-generation model necessarily owns
diagrams -- the `VisualProvider` abstraction is generic enough that a
later phase could route `DIAGRAM` to a dedicated diagram-rendering
provider instead, behind the exact same Protocol.

### Reuse strategy: `ASSET_REUSE` and `TI_STATE`

Neither ever calls `VisualProvider.render()`. `ASSET_REUSE` requires a
non-blank `beat.reuse_key`; a blank one raises `InvalidVisualBeatError`
-- a business-invalid beat, not a provider or storage failure, and
therefore never retried. `TI_STATE` means reuse/render a known Tí state
asset, **not** generate a new AI image for every state: it prefers
`beat.reuse_key` when present (an explicit asset override), and
otherwise falls back to a deterministic `"ti_state:<enum-value>"`
identifier (e.g. `"ti_state:CURIOUS"`) built from
`VisualTiState`; a `TI_STATE` beat with neither `reuse_key` nor
`ti_state` set also raises `InvalidVisualBeatError`.

### External requirements: `LIMITED_MOTION`, `EVIDENCE_MEDIA`, `AI_HERO_VIDEO`

None of the three ever calls `VisualProvider.render()`; each becomes an
`EXTERNAL_REQUIRED` requirement recording what a future runtime execution
path (not this renderer) must supply. `EVIDENCE_MEDIA` preserves
`beat.evidence_source_ids` (comma-joined) as the requirement's
`reference` -- no web access, no download, ever, anywhere in this
renderer. `LIMITED_MOTION` preserves `beat.motion_intent` in the
requirement's `notes` -- no animation rendering. `AI_HERO_VIDEO` needs no
extra metadata beyond `beat_id`/`media_type` -- no video provider
integration exists to prepare data for yet.

### Preconditions / freshness (two direct-dependency gates, chained)

`project.state == MVP_COMPLETE` exactly, plus valid, matching
`ScriptPlan`, a fresh `VoicePlan` (`VoicePlan.script_plan_id ==
Project.script_plan_id`, else `StaleVoicePlanError` -- identical to
Phase 17's own gate, re-checked here because the Visual Renderer also
depends on `VoicePlan` transitively through `VisualPlan.voice_plan_id`),
and a fresh `VisualPlan` (`VisualPlan.script_plan_id ==
Project.script_plan_id` **and** `VisualPlan.voice_plan_id == (current
VoicePlan).id`, else `StaleVisualPlanError`). All three checks run before
any provider call, `ModuleRun`, or file write. `ModuleRun.input_ids`
records `[script_plan_id, voice_plan_id, visual_plan_id]`.

### Provider retries vs. write retries — deliberately different policies

Identical policy to Phase 17: `_render_with_retry` allows up to `1 +
VisualSettings.max_provider_retries` total attempts per renderable beat,
and retries **only** `VisualProviderError` (any other exception
propagates immediately, unretried). Once a provider call succeeds,
`VisualFileStore.write()` is called exactly once, **outside** the retry
loop -- proven by a test that queues exactly one provider response for a
single-renderable-beat plan whose write is forced to fail; if the
renderer ever retried the provider afterward, `FakeVisualProvider` would
raise its own "exhausted" error instead of the expected
`VisualWriteError`.

A mid-run failure (provider exhaustion, a write error, or a manifest
integrity failure) does **not** roll back asset files already written by
earlier beats in the same run -- they are left on disk as harmless
orphans, and only the `VisualRenderManifest` artifact is withheld. This
is deliberate, documented behavior, not a known defect, exactly mirroring
Phase 17's Voice Renderer -- cleanup of orphaned render files remains out
of scope.

### Deterministic construction, not generation

There is no LLM output to validate against a schema here -- every
`RenderedVisualAsset`/`VisualRenderRequirement` is built directly from
the corresponding `VisualBeat` and (for renderable beats) the provider's
own `VisualRenderResponse`, with no ambiguity to correct. So
`validate_visual_render_manifest()` runs once, after the whole manifest
is built, purely as defense-in-depth: it checks the manifest's own
`script_plan_id`/`voice_plan_id`/`visual_plan_id` against the current
inputs, beat coverage (every `VisualBeat` accounted for exactly once
across `assets` + `requirements` combined -- no missing beat, no
duplicate beat_id anywhere in the manifest), status/media-type pairing
(`RENDERED`-equivalent assets restricted to
`GENERATED_STILL`/`DIAGRAM`, `REUSE_ONLY` restricted to
`ASSET_REUSE`/`TI_STATE`, `EXTERNAL_REQUIRED` restricted to
`LIMITED_MOTION`/`EVIDENCE_MEDIA`/`AI_HERO_VIDEO`), unique
`render_job_id`/`file_path`, and file-path safety. Any issue raises
`VisualRenderManifestIntegrityError` directly -- there is no
correction-retry step, since there is no LLM call to retry.

### Render job identity and output paths

Deterministic, one render job per renderable beat: `render_job_id =
"{beat_id}_R1"` -- no multi-take visual generation in Phase 19 (unlike
Voice Rendering's 1-3 takes per chunk). Output files are written to
`{project_id}/{visual_plan_id}/{render_job_id}.{ext}` under whatever root
`VisualFileStore` was configured with (`{ext}` from
`VisualSettings.output_format`, lowercased) -- the provider never chooses
the final file path, and no user-authored concept text ever appears in a
filename.

### Artifact type

Stored under `"visual_render_manifest"`, centralized as
`VISUAL_RENDER_MANIFEST_ARTIFACT_TYPE` in
`app/renderers/visual/models.py`. Re-running the renderer for the same
project **upserts** the manifest artifact (matching `save_artifact`'s
existing upsert-only convention) and writes assets to the same
deterministic relative paths, so a rerender overwrites both the manifest
and every asset file in place.

### What Phase 19 deliberately does not do

No real image/video vendor integration (no Gemini image, no Imagen, no
OpenAI image generation, no Midjourney, no Stable Diffusion, no
ComfyUI, no real AI video generation) -- `FakeVisualProvider` is the
only implementation. No real footage download, no web image search, no
stock media integration, no dedicated diagram renderer, no CapCut
automation, no visual post-processing, no thumbnail rendering, no
publishing, no orchestrator, no agent framework, and no new
`ProjectState` or `Project` reference field. Orphaned visual asset files
from a failed run are never cleaned up automatically (see above). No
Pillow (or any other image-codec library) dependency was added.

## Phase 20 — Gemini Image Provider Integration (approved; SUPERSEDED by Phase 20.1)

**Corrected by Phase 20.1, below.** A real live evaluation call against
this project's Gemini Developer API key proved the SDK method this
phase chose (`client.models.generate_images`) does not work with that
key at all -- it requires Vertex AI / Gemini Enterprise Agent Platform
credentials. **This was a genuine implementation mistake in this phase,
not a later deprecation** -- Phase 20's compatibility findings below (in
particular finding 8's model-name verification) were real and honestly
reported at the time, but the API surface chosen on top of that finding
was wrong for this project's actual credentials. The section below is
kept as the historical record of what Phase 20 actually built and why;
see **Phase 20.1** at the end of this document for what replaced it,
and read `app/visual/providers/gemini.py`'s own module docstring for the
authoritative current behavior.

Adds `app/visual/providers/gemini.py` -- the first real `VisualProvider`,
alongside the still-unchanged `FakeVisualProvider` -- and
`app/visual/router.py`, the smallest mechanism that lets real production
wiring route `GENERATED_STILL` to Gemini while `DIAGRAM` stays non-Gemini,
**without modifying `VisualRenderer` at all**.

### Compatibility findings (no migration needed or authorized this phase)

All 12 required checks were read directly from the locked files (and,
for the SDK itself, from live introspection of the installed package, not
from memory) before any code was written:

1. `VisualProvider` (`app/visual/provider.py`) -- confirmed unchanged: a
   `typing.Protocol` with a single `render(request) -> response` method.
   `GeminiImageProvider` satisfies it exactly as `FakeVisualProvider`
   does.
2. `VisualRenderRequest` (`app/visual/models.py`) -- confirmed unchanged:
   `render_job_id`, `beat_id`, `media_type`, `concept`, `primary_focus`,
   `secondary_elements`, `context_elements`, `ti_state`, `motion_intent`,
   `reuse_key`, `output_format`, `metadata`. No `visual_level` field
   exists -- **not added** this phase (see below).
3. `VisualRenderResponse` (`app/visual/models.py`) -- confirmed
   unchanged: `asset_bytes`, `provider`, `model`, `output_format`,
   `width`, `height`, `provider_request_id`, `metadata`; validator only
   requires non-empty `asset_bytes`, non-blank `provider`, and positive
   `width`/`height` *when supplied* -- both stay legitimately `None` for
   Gemini's Imagen response, which exposes no dimension metadata.
4. `VisualSettings` (`app/visual/config.py`) -- confirmed unchanged:
   `provider`, `output_format` default `PNG`, `max_provider_retries`
   default 1. No schema change needed to carry `"gemini-image"` as
   `provider`.
5. `VisualRenderer`'s routing logic (`app/renderers/visual/renderer.py`)
   -- confirmed unchanged and, after this phase, **still** unchanged: it
   calls `self._visual_provider.render(request)` for
   `GENERATED_STILL`/`DIAGRAM` beats without ever inspecting which
   concrete provider it was given. Phase 20's entire production-routing
   policy therefore lives one layer up, in what object gets constructed
   and handed to `VisualRenderer.__init__` -- never inside it.
6. `VisualOutputFormat` (`app/models/common.py`, Phase 19) confirmed
   fresh: exactly `PNG`/`JPG` -- **not changed**; Phase 20 supports `PNG`
   only and rejects `JPG` explicitly (see below).
7. `VisualMediaType` (`app/models/common.py`, Phase 14) confirmed fresh:
   exactly seven values -- **not changed**;
   `GeminiImageProvider.render()` accepts only `GENERATED_STILL` and
   rejects the other six, `DIAGRAM` included, before any network call.
8. Gemini SDK dependency confirmed: `google-genai==2.22.0` already
   installed (added Phase 18, version range unchanged in
   `pyproject.toml`). Live introspection of the installed package (not
   memory) found `Models.generate_images(model, prompt, config)` --
   Gemini's dedicated Imagen text-to-image endpoint -- and its own
   docstring's usage example names a real, current model string:
   `model='imagen-3.0-generate-002'`
   (`.venv/Lib/site-packages/google/genai/models.py`). This is the
   verified source for `DEFAULT_GEMINI_IMAGE_MODEL`; no name was guessed
   from memory. `types.GenerateImagesConfig.aspect_ratio`'s own
   docstring further documents its exact five supported values (`"1:1"`,
   `"3:4"`, `"4:3"`, `"9:16"`, `"16:9"`), which
   `GeminiImageConfig.default_aspect_ratio` (default `"16:9"`) validates
   against directly.
9. `GeminiTTSProvider` (`app/audio/providers/gemini.py`, Phase 18)
   confirmed as the config/error-pattern precedent:
   environment-variable-only secret loading, eager client construction
   at `__init__` (a missing key fails immediately, zero network calls),
   a `*ConfigurationError` that deliberately does **not** subclass the
   retryable provider-error class, and a narrow, two-member retryable
   SDK-error tuple (`google.genai.errors.APIError`, `httpx.HTTPError`).
   `GeminiImageProvider` mirrors every one of these policies exactly,
   even though it calls a different SDK method
   (`generate_images` vs. `generate_content`) for a different modality.
10. Pillow confirmed **still absent** (`import PIL` fails in the
    project's own `.venv`) -- not added this phase either. Imagen's
    response exposes no width/height metadata at all (confirmed by
    inspecting `google.genai.types.Image`'s own fields: `gcs_uri`,
    `image_bytes`, `mime_type` -- nothing else), so this isn't even a
    close call: there is no dimension data to decode in the first place.
11. Rendered image dimensions are currently represented as
    `width: int | None` / `height: int | None` on both
    `VisualRenderResponse` (Phase 19) and `RenderedVisualAsset` (Phase
    19) -- both already `Optional`, confirmed to accept `None` cleanly.
    `GeminiImageProvider` always returns `None`/`None`; this is an
    accepted, honest value under the existing contract, not a gap.
12. No production wiring/orchestrator/factory exists anywhere in the
    repository yet (repo-wide search for a real construction call site)
    -- confirmed absent, exactly like Phase 18's identical finding for
    `GeminiTTSProvider`. `MediaTypeVisualProvider` (see below) is
    therefore Phase 20's *only* production-routing artifact; there is no
    running app/CLI entrypoint that assembles it yet, and none is added
    this phase (no orchestrator is in scope).

Two new subpackages (`app/visual/providers/`, containing `gemini.py`) and
one new small composite module (`app/visual/router.py`), plus two new
errors (`VisualProviderUnavailableError`,
`GeminiImageConfigurationError`/`GeminiImageUnsupportedMediaTypeError`/
`GeminiImageUnsupportedOutputFormatError`). No locked contract was
changed; `VisualProvider`/`VisualRenderRequest`/`VisualRenderResponse`/
`VisualSettings`/`VisualRenderer` all needed **zero** changes.

### `GeminiImageProvider`: GENERATED_STILL only, PNG only

`render(request)` checks `request.media_type ==
VisualMediaType.GENERATED_STILL` and `request.output_format ==
VisualOutputFormat.PNG` **before any network call** -- anything else
raises `GeminiImageUnsupportedMediaTypeError` /
`GeminiImageUnsupportedOutputFormatError` (both `VisualOutputError`
subclasses, mirroring `GeminiUnsupportedFormatError`'s Phase 18
precedent: a request-shape rejection, not a provider/network failure, so
never retried). `DIAGRAM` is deliberately excluded even though it is
architecturally provider-renderable per Phase 19's router -- Phase 20's
job is proving Gemini works for stills, not deciding diagrams' eventual
real provider.

PNG is requested explicitly via `GenerateImagesConfig(output_mime_type=
"image/png", number_of_images=1, aspect_ratio=config.default_aspect_ratio,
include_rai_reason=True)` rather than relying on an unstated SDK default
-- "do not lie about format" is satisfied by asking for exactly the
format the response is claimed to be, and then validating the MIME type
the response actually reports (see below). JPG output was evaluated and
rejected explicitly for Phase 20 rather than left ambiguous: supporting
it cleanly would require verifying Imagen's JPEG compression-quality
behavior, which was out of scope to verify this phase.

### Prompt translation (`_build_prompt`) -- the only vendor-specific prompt in the repository

`GeminiImageProvider` is the first and only place allowed to construct a
vendor-specific image-generation prompt, exactly as
`GeminiTTSProvider._build_director_prompt` was Phase 18's only place
allowed to construct vendor-specific director's notes.
`VisualRenderer` remains completely prompt-free, both before and after
this phase (proven by the same two-test pattern Phase 17/19 established
for `app.llm`-freedom, here re-purposed as a plain source-scan asserting
`app/renderers/visual/renderer.py` contains no `gemini`/`google.genai`/
`imagen` substring at all).

The prompt is built from a small, fixed **style-guidance block** encoding
the approved visual language -- painterly editorial 2D, semi-real
simplified anatomy, flat 2-3 tone rendering with soft short tonal
transitions, warm key light and cooler shadows, atmospheric haze
reserved for the background only, not photorealistic, not chibi, not
hard cel-shaded, restrained detail with one clear primary visual focus,
and a preference against embedded text/labels/captions -- followed by
`request.primary_focus`/`request.concept`/`request.secondary_elements`/
`request.context_elements` copied in **verbatim**. The provider wraps
content with style guidance; it never replaces or rewrites it (proven by
a test asserting the exact `request.concept` string, including
Vietnamese diacritics and punctuation, is a substring of the sent
prompt).

`request.metadata`, when non-empty, is appended as a compact
`key=value` line -- the only other request field folded into the
prompt. `request.motion_intent`, when present, is included only as a
**still-composition/framing note** ("no motion is rendered"), never as
an instruction to animate. `request.reuse_key`, when present, is
included only as a brief continuity note. Neither is treated as
authoritative visual content the way `concept`/`primary_focus` are.

**No `visual_level` (L1/L2/L3) tailoring was attempted.**
`VisualRenderRequest` does not carry a `visual_level` field (compat
finding 2) and Phase 20 does not migrate one in solely to enable this --
the spec's own instruction ("do not infer unsupported visual-level
fields if the request contract does not carry them") is followed
literally. Whatever L1/establishing vs. L2/mechanism distinction a
`VisualBeat` was authored with already shows up naturally through its
`concept`/`primary_focus`/`context_elements` text, which the prompt
already includes verbatim.

### Tí handling -- a mood/expression note, not a character sheet

When `request.ti_state` is present, the prompt names Tí as "the
channel's established mouse mascot/character" and adds one of nine fixed
per-`VisualTiState` mood/expression phrases (e.g. `CURIOUS` ->
"a curious expression, leaning in with interest"), mirroring
`voice_state_direction`'s Phase 18 lookup-table pattern exactly. **This
does not solve canonical Tí visual consistency.** There is no reference
image, no character sheet, no pose library, and no conditioning
mechanism beyond this one sentence of text -- two independently
generated stills with the same `ti_state` are not guaranteed, or even
expected, to depict a visually consistent Tí. Solving that requires
either reference-image conditioning or a deterministic Tí
compositing/reuse mechanism, explicitly deferred to a later phase. When
`ti_state` is absent, no Tí-related text is added to the prompt at all.

### Historical safety (Phase 14 causality rule, preserved generically)

Whenever `ti_state` is present, the prompt unconditionally appends "Tí
may only observe or react to anything depicted -- never cause or alter
an event, historical or otherwise." This is deliberately **not**
conditional on detecting whether `concept` describes a historical scene
(that would require scene classification, out of scope) -- the
constraint is harmless and correct to state every time Tí appears in a
frame at all, so it is stated unconditionally rather than selectively.

### Response extraction (`_extract_image_bytes`)

All Gemini response traversal lives in this one private helper, mirroring
`_extract_pcm_bytes`'s Phase 18 precedent. `GenerateImagesResponse
.generated_images` empty, or its first entry's `.image is None` (a
Responsible-AI-filtered result -- the SDK's own `rai_filtered_reason`
field is surfaced in the error message when present), or an empty
`.image.image_bytes`, all raise `VisualOutputError` explicitly -- never a
raw `IndexError`/`AttributeError` leaking through as if it were normal
behavior. `mime_type` is validated **only when the SDK exposes it**: an
explicit, unexpected value (e.g. `image/jpeg` when PNG was requested)
raises `VisualOutputError`; a mime_type the SDK simply didn't populate is
not treated as an error at all (the "if exposed" qualifier in the spec is
read literally, to avoid false-rejecting a genuinely valid image solely
because one optional metadata field was absent).

`width`/`height` are always `None` -- confirmed by inspecting
`google.genai.types.Image`'s own Pydantic fields (`gcs_uri`,
`image_bytes`, `mime_type` only; no dimension field exists anywhere on
the response). `provider_request_id` is always `None` -- confirmed no
documented per-image request-id field exists on
`GenerateImagesResponse` either (only `sdk_http_response.headers`/`body`,
neither of which is a stated request-id carrier). Both are honest,
contract-accepted `None` values, never invented or faked.

### Error translation and retry ownership

Identical policy to `GeminiTTSProvider`: only
`google.genai.errors.APIError` and `httpx.HTTPError` translate to
`VisualProviderError`; every other exception (a genuine bug) stays
visible. `GeminiImageProvider` retries nothing itself --
`VisualRenderer` already owns the `VisualProviderError` retry policy,
and a test proves exactly one SDK call happens per direct `render()`
call regardless of success or failure.

### Production routing without touching `VisualRenderer`

Phase 19's `VisualRenderer` already sends every `GENERATED_STILL`/
`DIAGRAM` beat to whatever single object was injected as
`visual_provider` -- it never asks which concrete provider that is.
Phase 20 exploits this directly: `MediaTypeVisualProvider`
(`app/visual/router.py`) is a small composite satisfying `VisualProvider`
structurally, constructed with a `{VisualMediaType: VisualProvider}`
mapping (e.g. `{VisualMediaType.GENERATED_STILL:
GeminiImageProvider(...)}`), and handed to `VisualRenderer` as its
ordinary `visual_provider` argument -- the exact same constructor
parameter position `FakeVisualProvider` or a single `GeminiImageProvider`
would occupy. `render(request)` looks up `request.media_type` in its
mapping and dispatches to that exact provider; an unmapped media_type
(Phase 20 production wiring deliberately leaves `DIAGRAM` unmapped)
raises `VisualProviderUnavailableError` -- a new error added to
`app/visual/errors.py`, deliberately **not** a `VisualProviderError`
subclass (mirroring `GeminiTTSConfigurationError`'s Phase 18 precedent)
so `VisualRenderer`'s retry loop never wastes attempts on what is a
routing/configuration decision, not a transient failure. There is no
fallback guessing, no LLM involved in the dispatch, and no generic
plugin-registry framework -- just one dict lookup. A dedicated test
proves `app/renderers/visual/renderer.py`'s source contains no
`gemini`/`google.genai`/`imagen` substring at all, confirming this
routing was achieved with **zero** modification to the renderer.

### Live smoke test and manual evaluation utility (opt-in only)

`tests/test_gemini_image_live_smoke.py` generates exactly one small,
non-sensitive still (an empty suspension bridge under a cloudy sky,
consistent with channel style, no copyrighted characters) and is gated
by both a registered `live_image` pytest marker and
`RUN_LIVE_GEMINI_IMAGE=1`; it skips cleanly -- no error, no
`GEMINI_API_KEY` requirement -- unless a human opts in on purpose,
mirroring `test_gemini_tts_live_smoke.py` exactly.
`scripts/evaluate_gemini_image.py` renders three fixed, representative
briefs (an L1 establishing scene, an L2 mechanism/action scene, and a
Tí-present scene) to one PNG file each for a human to look at --
**no automated aesthetic scoring and no LLM-based ranking exist anywhere
in this script**, and it explicitly does not claim to solve Tí character
consistency, mirroring `scripts/evaluate_gemini_voices.py`'s "a human
decides later" policy exactly.

### What Phase 20 deliberately does not do

No diagram renderer or real diagram provider (DIAGRAM stays
architecturally provider-renderable per Phase 19 but has no real
production provider configured -- `VisualProviderUnavailableError` is
the honest result). No AI video generation, no `LIMITED_MOTION`
renderer, no evidence downloader, no `TI_STATE`/`ASSET_REUSE`
generation (both remain reuse-only per Phase 19, untouched), no
thumbnail renderer, no CapCut automation, no publishing, no second real
image provider, no orchestrator, no multi-agent framework, and no new
`ProjectState` or `Project` reference field. Canonical Tí character
consistency across independently generated stills remains unsolved (see
above) -- this is a documented limitation, not a defect. No Pillow (or
OpenCV/ffmpeg/torch) dependency was added.

## Phase 20.1 — Fix Gemini Image Developer API Path (approved)

**Corrective phase only.** Phase 20's implementation was wrong, not
merely outdated: `GeminiImageProvider.render()` called
`client.models.generate_images(model="imagen-3.0-generate-002", ...)`,
and a real live call against this project's actual Gemini Developer API
/ AI Studio key failed immediately with:

```
ValueError: This method is only supported in Gemini Enterprise Agent
Platform mode, not in Gemini Developer API mode.
```

`generate_images` is Google's Imagen API, gated to Vertex AI / Gemini
Enterprise Agent Platform credentials -- it was never reachable with
this project's key, at any point. The installed SDK additionally warns
`generate_images` is deprecated. This phase fixes the mistake; it does
not add any new capability.

### Verification before editing (per this correction's own mandate)

Before any code changed, `client.models.generate_content`'s support for
image output was verified directly against the installed SDK
(`google-genai==2.22.0`), not memory or the old `generate_images`
docstring:

- `google.genai.types.Modality` has an `IMAGE` member ("Indicates the
  model should return images"), confirming `response_modalities=["IMAGE"]`
  is a real, typed, SDK-recognized request shape.
- `GenerateContentConfig.response_modalities` accepts a list including
  `"IMAGE"`.
- `google.genai.types.ImageConfig` ("The image generation configuration
  to be used in GenerateContentConfig") exists specifically for this
  path, with `aspect_ratio` (8 documented values: `"1:1"`, `"2:3"`,
  `"3:2"`, `"3:4"`, `"4:3"`, `"9:16"`, `"16:9"`, `"21:9"`) as its
  Developer-API-supported field; `output_mime_type`/
  `output_compression_quality`/`image_output_options` are each
  explicitly documented "not supported in Gemini API" on this same
  class -- confirming those (Vertex-only) fields must never be set for
  this project's key.
- The response shape (`GenerateContentResponse.candidates[0].content
  .parts[*].inline_data`) is exactly the shape `GeminiTTSProvider`
  already traverses successfully in production (Phase 18) -- no new
  traversal pattern was invented, only a different requested modality.

The specific model string `gemini-2.5-flash-image` (unlike the old
`imagen-3.0-generate-002`, which appeared verbatim in the deprecated
method's own docstring) does **not** appear anywhere in the installed
SDK's source -- the SDK's type system never enumerates valid model
names for any endpoint; a model name's real validity is only ever
confirmed by an actual API call, which section "Real evaluation" below
records. This asymmetry is stated plainly rather than glossed over.

### API and model migration

`GeminiImageProvider.render()` now calls:

```python
response = self._client.models.generate_content(
    model=self._config.model,               # default "gemini-2.5-flash-image"
    contents=prompt,
    config=genai_types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=genai_types.ImageConfig(
            aspect_ratio=self._config.default_aspect_ratio
        ),
    ),
)
```

`DEFAULT_GEMINI_IMAGE_MODEL` changed from `imagen-3.0-generate-002` to
`gemini-2.5-flash-image`, still fully configurable through
`GeminiImageConfig.model` -- no automatic fallback between models exists
anywhere. `_SUPPORTED_ASPECT_RATIOS` was widened from the deprecated
`GenerateImagesConfig`'s 5-value set to `ImageConfig.aspect_ratio`'s own
8-value set (the correct authoritative set for the path now used);
default stays `"16:9"`, valid in both.

### Response extraction, rewritten for `GenerateContentResponse`

`_extract_image_bytes` now mirrors `GeminiTTSProvider._extract_pcm_bytes`
exactly: no candidates, no content parts, or every part lacking
`inline_data` (a text-only response) each raise `VisualOutputError`
explicitly -- never a raw `IndexError`/`AttributeError`. When multiple
parts exist, the **first** part carrying `inline_data` is used and any
preceding text part is ignored (per this correction's explicit
requirement: "ignore textual parts if present," "first valid image part
may be used"). A new `_blocked_reason_suffix` helper surfaces
`response.prompt_feedback.block_reason` in the error message when a
prompt was blocked, so a Responsible-AI rejection doesn't read as an
unexplained empty response. `mime_type` validation is unchanged from
Phase 20: checked against `{"image/png"}` only when the response
actually exposes one (`ImageConfig.output_mime_type` cannot be used to
force PNG in Developer API mode, so this is validation, never a
request). `provider_request_id` now reads `response.response_id` (the
same field `GeminiTTSProvider` already reads) -- populated when the SDK
sets it, `None` otherwise, never invented; `width`/`height` remain
always `None` (Gemini's `inline_data`/`Blob` type carries only
`data`/`display_name`/`mime_type` -- confirmed by inspection, no
dimension field exists at all, so this isn't a gap, there's simply
nothing to read).

### API key precedence -- verified correct, no code change

The live run's console output, "Both GOOGLE_API_KEY and GEMINI_API_KEY
are set. Using GOOGLE_API_KEY.", looked like it might mean the wrong key
was used. It does not. `_build_default_client` already read
`os.environ[config.api_key_env]` (default `"GEMINI_API_KEY"`) explicitly
and passed it as `genai.Client(api_key=...)` before this correction, and
still does. Reading the installed SDK's own source
(`google/genai/_api_client.py`) shows `get_env_api_key()` -- the
function that emits this exact log line -- runs **unconditionally**
inside `BaseApiClient.__init__`, purely so it can log the warning when
both env vars happen to be set; the actual resolution is
`self.api_key = api_key or env_api_key`, so an explicitly-passed,
non-empty `api_key` argument always wins regardless of whether the log
line fired. This was confirmed empirically too: constructing
`genai.Client(api_key=<distinct value>)` with both `GOOGLE_API_KEY` and
`GEMINI_API_KEY` set in the environment to two other, different values
still resolves `client._api_client.api_key` to exactly the explicitly
passed value. A dedicated test
(`test_configured_env_key_passed_explicitly_even_when_google_api_key_differs`)
now proves this directly at the `GeminiImageProvider` level by mocking
`genai.Client` and asserting the captured `api_key` kwarg, rather than
relying solely on reading SDK source.

### What changed vs. what didn't

Changed, entirely inside `app/visual/providers/gemini.py`: the SDK
method called, the default model, the aspect-ratio validation set, the
response-extraction helper, and `provider_request_id` now being
populated when available. Unchanged: `VisualProvider`,
`VisualRenderRequest`, `VisualRenderResponse`, `VisualSettings`,
`VisualRenderer`, `VisualPlan`, `VisualBeat`, `MediaTypeVisualProvider`,
the canonical provider name (`"gemini-image"`), the GENERATED_STILL-only
and PNG-only policies, the narrow SDK-error-to-`VisualProviderError`
translation boundary, and the zero-nested-retry policy.
`tests/test_gemini_image_live_smoke.py` and
`scripts/evaluate_gemini_image.py` needed no edits at all -- both
already depended only on `GeminiImageProvider`/`GeminiImageConfig`'s
public interface, so the correction applies to them automatically.

### Real evaluation (the actual verification of the new model/method)

`scripts/evaluate_gemini_image.py` was run once, live, against this
project's real `GEMINI_API_KEY`, generating the same three fixed briefs
Phase 20 defined (L1 establishing scene, L2 mechanism/action scene,
Tí-present scene) -- no prompt tuning, no extra generations. Results are
recorded in `docs/CHANGELOG.md`'s Phase 20.1 entry and were reported to
the human directly; this section intentionally does not duplicate
per-run output details that belong in session history, not in a
standing architecture spec.

## Phase 20.2 — Cloudflare Workers AI Image Provider (approved)

**Additive only.** Phase 20.1 proved `GeminiImageProvider`'s
`generate_content` path is architecturally correct and accepted by the
live Gemini Developer API; the only remaining blocker is this project's
Google account having zero free-tier quota for
`gemini-2.5-flash-image` (a `429 RESOURCE_EXHAUSTED` billing/quota
condition, recorded in Phase 20.1's live-evaluation results -- not a
code defect). Rather than wait on that account limitation,
Phase 20.2 adds a **second** real `VisualProvider` --
**`CloudflareImageProvider`** (`app/visual/providers/cloudflare.py`),
backed by Cloudflare Workers AI's `@cf/black-forest-labs/flux-1-schnell`
model -- purely to unblock real visual evaluation on a free-tier-
friendly path. `GeminiImageProvider` is not touched, weakened, or
removed; it remains the architecturally-preferred real provider once
billing is resolved.

### Compatibility findings (no migration needed or authorized this phase)

All 12 required checks were re-confirmed directly from the locked files
before any code was written:

1. `VisualProvider` (`app/visual/provider.py`) -- confirmed unchanged: a
   single-method Protocol. `CloudflareImageProvider` satisfies it
   exactly as `FakeVisualProvider`/`GeminiImageProvider` do.
2. `VisualRenderRequest` -- confirmed unchanged; the same
   provider-neutral fields feed both Gemini's and Cloudflare's prompt
   builders.
3. `VisualRenderResponse` -- confirmed unchanged; `width`/`height`
   already `Optional`, exercised the same way Gemini's adapter does.
4. `VisualRenderer` (`app/renderers/visual/renderer.py`) -- confirmed
   unchanged, and **still** unchanged after this phase (a dedicated
   source-scan test asserts no `cloudflare`/`flux` substring anywhere in
   it). It calls `self._visual_provider.render(request)` generically; it
   has no idea a second real vendor now exists.
5. `MediaTypeVisualProvider` (`app/visual/router.py`) -- confirmed
   unchanged; its `{VisualMediaType: VisualProvider}` mapping already
   accepts any object satisfying `VisualProvider`, so
   `CloudflareImageProvider` slots in with zero router changes.
6. `GeminiImageProvider` (`app/visual/providers/gemini.py`) -- confirmed
   unchanged; its own tests re-run clean, proving this addition doesn't
   regress it.
7. `VisualProviderError` hierarchy (`app/visual/errors.py`) -- confirmed
   unchanged and sufficient: `VisualProviderError` (retryable),
   `VisualOutputError` (not retried), `VisualProviderUnavailableError`
   (routing/configuration, not retried) all reused as-is; no new error
   base class was needed.
8. `VisualOutputFormat` (`PNG`/`JPG`) -- confirmed unchanged; Cloudflare
   uses the already-existing `JPG` member, so no enum change was needed
   or authorized.
9. HTTP dependency: `httpx` confirmed already installed (transitively
   via `google-genai`) and already imported directly by both
   `app/audio/providers/gemini.py` and
   `app/visual/providers/gemini.py` for its error types -- reused
   directly for real HTTP calls here rather than adding any SDK. Now
   also declared as an explicit `pyproject.toml` dependency, since this
   module's reliance on it goes beyond `isinstance` checks.
10. `scripts/evaluate_gemini_image.py` (Phase 20) confirmed as the
    direct structural precedent for `scripts/evaluate_cloudflare_image.py`
    -- same three conceptual briefs, same "no automated ranking"
    policy, same UTF-8 console-encoding fix.
11. Secret-loading convention confirmed and reused exactly: an
    environment-variable name is a config field (`account_id_env`/
    `api_token_env`, mirroring `api_key_env`), the actual secret is
    never a config field, and resolution happens eagerly at
    construction time so a missing credential fails before any network
    call.
12. No shared Cloudflare integration existed anywhere in the repository
    (repo-wide search) -- confirmed absent; created fresh this phase.

No new enum, no new top-level package (added to the existing
`app/visual/providers/` subpackage), and no locked contract changed.

### `CloudflareImageProvider`: GENERATED_STILL + JPG only

Mirrors `GeminiImageProvider`'s policy exactly, adapted to Cloudflare's
REST shape: `render(request)` checks `request.media_type ==
VisualMediaType.GENERATED_STILL` and `request.output_format ==
VisualOutputFormat.JPG` **before any HTTP call** -- anything else raises
`CloudflareImageUnsupportedMediaTypeError`/
`CloudflareImageUnsupportedOutputFormatError` (both `VisualOutputError`
subclasses, never retried). JPG, not PNG, because FLUX.1 Schnell's
official examples treat its base64 `result.image` output as JPEG --
this is Cloudflare's documented behavior for this specific model, not
an assumption. Neither `VisualOutputFormat` nor `VisualRenderer` changed
to accommodate this: a caller wiring this provider simply configures
`VisualSettings(output_format=VisualOutputFormat.JPG)`, exactly as the
spec anticipated ("prefer provider-specific evaluation settings ... not
migration of VisualOutputFormat or VisualRenderer").

### Endpoint construction and authentication

Constructed exactly as documented:

```
https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}
Authorization: Bearer {api_token}
```

`{model}` (`@cf/black-forest-labs/flux-1-schnell` by default) is
embedded literally -- its `@` and `/` characters are part of
Cloudflare's own path-segment convention for this endpoint family, not
percent-encoded. `account_id`/`api_token` are resolved eagerly at
`__init__` time from `os.environ[config.account_id_env]`/
`os.environ[config.api_token_env]` (defaults `CLOUDFLARE_ACCOUNT_ID`/
`CLOUDFLARE_API_TOKEN`); either missing raises
`CloudflareImageConfigurationError` before any HTTP call, with the
error message naming only the environment variable, never a value.
Both may instead be supplied directly as constructor arguments
(`account_id=`/`api_token=`), the same convenience an injected `client=`
already offers Gemini's tests -- so tests requiring zero real
credentials simply pass explicit dummy strings alongside a fake HTTP
client, never touching the environment. The token is used exactly once,
to build the `Authorization` header; it is never logged, never included
in any exception message, and never stored on the Pydantic
`CloudflareImageConfig` (which has no secret fields at all).

### HTTP client injection

`self._client` defaults to the `httpx` module itself when no `client` is
injected -- `httpx.post(url, headers=, json=, timeout=)` matches the
exact shape a fake test client needs to expose (`.post(...)` alone), so
no persistent `httpx.Client` lifecycle needs managing for this
low-volume, per-render use. Tests inject a plain object exposing only
`.post(...)`, fully offline and deterministic.

### Response envelope and error translation

`_extract_image_bytes` validates, in order: the response body is valid
JSON; `success` is truthy (a `success: false` envelope -- an API-level
failure reported inside an HTTP 200, e.g. an overloaded model -- raises
`VisualProviderError`, the same retryable category as an HTTP 4xx/5xx,
distinct from a malformed-output problem); `result` is present and is an
object; `result.image` is non-blank; and the base64 payload decodes to
non-empty bytes (`binascii.Error`/`ValueError` from a bad payload is
caught and re-raised as `VisualOutputError`, never left to leak as a raw
low-level exception). An explicit non-2xx HTTP status is checked
separately, immediately after the `client.post(...)` call, and also
raises `VisualProviderError` with a concise, secret-free message.
Network/transport failures (`httpx.HTTPError` and its `ConnectError`/
`TimeoutException` subclasses) raised by `client.post(...)` itself are
caught in the same narrow, documented boundary and translated the same
way -- any other exception (a genuine bug) is left to propagate
unchanged, exactly like both Gemini adapters' policy.

`width`/`height` stay `None` -- Cloudflare's envelope carries no
dimension metadata at all, and Pillow was not added to decode it.
`provider_request_id` reads the `cf-ray` response header when
Cloudflare exposes it (its own standard per-request ray id); `None`
otherwise, never invented. `metadata["mime_type"]` is always
`"image/jpeg"` -- a fixed, documented value (Cloudflare's envelope
itself carries no MIME field), not derived from the response.

### Prompt translation -- independently defined, not shared with Gemini's

`app/visual/providers/cloudflare.py` defines its own
`_build_prompt`/`_STYLE_GUIDANCE`/`_TI_STATE_DIRECTIONS`/
`_ti_state_direction`, structurally distinct module-level objects from
`gemini.py`'s equivalents (proven by a dedicated test asserting the two
functions/constants are not the same object) -- per this phase's own
instruction not to share "a giant prompt string" between vendor
adapters, even though both intentionally encode the identical locked
visual language (painterly editorial 2D, semi-real simplified anatomy,
flat 2-3 tone rendering, warm-light/cool-shadow, background-only haze,
restrained detail, one clear primary focus, no embedded text).
`concept`/`primary_focus`/`secondary_elements`/`context_elements` are
copied verbatim, never invented; a per-`VisualTiState` mood/expression
note represents Tí when `ti_state` is present, paired with the same
"Tí may only observe or react, never cause or alter an event,
historical or otherwise" causality line Gemini's adapter uses. **FLUX
prompt-only generation does not solve canonical Tí character
consistency across independently generated stills any more than
Gemini's does** -- there is no reference-image conditioning, no
character sheet, and no compositing/reuse mechanism here either; this
limitation is restated, not solved, by adding a second provider.

### Retry ownership, media routing, and coexistence with Gemini

Identical policy to `GeminiImageProvider`: zero retries inside the
adapter; `VisualRenderer` owns the `VisualProviderError` retry bound,
proven at both the adapter level (exactly one HTTP call per direct
`render()` invocation, success or failure) and the renderer level (2
calls: 1 transient failure + 1 success). `MediaTypeVisualProvider`
needed no changes to route `GENERATED_STILL` to
`CloudflareImageProvider` while `DIAGRAM` stays unmapped (raising
`VisualProviderUnavailableError`, non-retryable) -- exactly the same
composite object Phase 20.1 built for Gemini, now holding a different
concrete provider (or, in principle, both providers registered as
alternate `GENERATED_STILL` routes for different beats, though this
phase does not wire that combination since only one still-image
provider is active at a time in the evaluation scripts).
`GeminiImageProvider`'s own test suite was re-run unmodified and
continues to pass, confirming this addition regresses nothing.

### What Phase 20.2 deliberately does not do

No quota-management/automatic-retry-on-rate-limit logic (a Cloudflare
rate-limit/quota error is reported honestly as a `VisualProviderError`,
exactly like any other API failure -- never silently absorbed or
retried beyond `VisualRenderer`'s existing bound). No automatic
model/provider switching of any kind -- Gemini stays configured and
correct; Cloudflare is additive, not a replacement or fallback. No
second Cloudflare model, no DIAGRAM provider, no video generation, no
evidence retrieval, no CapCut automation, no publishing, no
orchestrator, no agent framework, and no new `ProjectState` or
`Project` reference field. No Pillow (or OpenCV/ffmpeg/torch)
dependency was added.

### Phase 20.2 human evaluation and role decision (approved; Phase 20.2 now COMPLETE)

`scripts/evaluate_cloudflare_image.py` was run once against real
Cloudflare credentials, generating exactly the 3 pre-existing briefs
(`L1_ESTABLISH`, `L2_MECHANISM`, `TI_PRESENT`) to
`data/cloudflare_image_evaluation/` -- no prompt tuning, no model
switch, no extra variants, matching Phase 20.1's Gemini evaluation
protocol exactly. A human reviewed the 3 stills directly and made an
explicit go/no-go decision per brief:

- **`L1_ESTABLISH`**: **acceptable for production background use.**
- **`L2_MECHANISM`**: visually usable, but **too cinematic/random for
  deterministic mechanism explanation** -- FLUX's prompt-only
  generation does not give the frame-to-frame determinism a mechanism
  explainer needs.
- **`TI_PRESENT`**: **rejected for production mascot use** -- identity/
  style drift from the character's canonical appearance, consistent
  with this document's repeated finding that prompt-only generation
  (Gemini's or FLUX's) does not solve Tí character consistency.

**Resulting architecture decision: `CloudflareImageProvider` is kept,
but is *not* the universal/general-purpose visual provider.** It is
scoped to a **low-cost/free background-and-scene provider** only.

- **Approved role**: background generation, establishing scenes,
  environmental illustration, and generic scene/object stills where
  exact character identity is not required.
- **Not approved for**: canonical Tí mascot generation, character
  consistency, brand-defining character art, diagrams, or any exact
  historical/mechanical relationship requiring deterministic
  precision.

This is a scoping decision, not a code change: `CloudflareImageProvider`
itself, its prompts, and its model are all unchanged by this decision.
Enforcing the restriction (e.g. routing `GENERATED_STILL` beats by
sub-type, or gating which beats may target Cloudflare) is **not**
implemented in Phase 20.2 and is left to a future phase.

**Future intended visual strategy** (direction only, not implemented
yet):

- **AI image provider** (Gemini and/or Cloudflare) -> backgrounds,
  environments, and generic scene imagery only.
- **Tí** -> a separate canonical, reusable, deterministic asset
  mechanism (not prompt-only generation) -- still undesigned.
- **Diagrams** -> a separate deterministic/semi-deterministic
  rendering system -- still undesigned; `DIAGRAM` remains unmapped in
  `MediaTypeVisualProvider` per Phase 20's original decision.
- **Text/labels** -> an editor/compositor layer, not baked into a
  generated image.

No code was changed to record this decision -- `CloudflareImageProvider`
behavior, its prompts, and its model are all exactly as Phase 20.2
shipped them. **Phase 20.2 is now COMPLETE.**

## Phase 21 — Canonical Tí Asset System (approved)

**Contracts, storage, validation, and retrieval only.** Directly
implements the "Tí -> a separate canonical, reusable, deterministic asset
mechanism (not prompt-only generation)" line from Phase 20.2's decision,
above. Explicitly out of scope this phase: image generation for Tí, scene
compositing, diagrams, editor automation, and any change to
`VisualRenderer`, `app/visual/router.py`, `CloudflareImageProvider`, or
`GeminiImageProvider`.

### Why a new top-level package, not `app/visual/`

`app/ti_assets/` is a sibling to `app/audio/`/`app/visual/`, not a
subpackage of either. Two reasons: (1) this phase's explicit instruction
not to wire anything into `VisualRenderer` yet -- keeping the code
physically separate makes "not wired in" verifiable by inspection, not
just by discipline; (2) canonical Tí assets are conceptually closer to a
locked brand-identity store than to a render pipeline -- there is no
"provider" here in the `VisualProvider`/`TTSProvider` sense, because
nothing is generated. `app/ti_assets/` has zero import of `app/visual/`,
`app/llm/`, or any provider module.

### `app/models/ti_assets.py` -- typed contracts

- **`TiAssetType(str, Enum)`**: `STILL` only. A `TiPose`/presentation-
  variant concept was considered and deliberately **not** added --
  nothing in the existing visual grammar (`VisualBeat.ti_state`,
  `VisualTiState`, `VisualMediaType.TI_STATE`) distinguishes a "pose"
  from a "state" today, so a pose concept would be speculative, not
  justified by an existing need. A future phase can add e.g.
  `LIMITED_MOTION` here without changing `TiAsset`'s shape.
- **`TiState(str, Enum)`**: `NEUTRAL`/`CURIOUS`/`SKEPTICAL`/`EXCITED`/
  `SERIOUS`/`DEADPAN`/`PANIC`/`LOW_ENERGY` -- exactly `VoiceState`'s
  8-member vocabulary (`app/models/common.py`), copied rather than
  imported/reused directly, because canonical-asset identity and vocal
  delivery are different domains that happen to share a vocabulary size
  right now, not two names for the same concept. **This is deliberately
  a different vocabulary from `VisualTiState`** (Phase 14's 9-member
  enum: `NEUTRAL`/`CURIOUS`/`SKEPTICAL`/`CONFUSED`/`SURPRISED`/`PANIC`/
  `SMUG`/`DEADPAN`/`EXCITED`, used by `VisualBeat.ti_state` and both real
  `VisualProvider` prompt builders). Phase 21's instruction was explicit:
  "align with the existing voice-state system where practical" -- not
  with `VisualTiState`. This produces a real, recorded mismatch: a
  `VisualBeat` with `media_type=TI_STATE` carries a `VisualTiState`
  value today, but `TiAssetRetriever.get_asset` takes a `TiState`. **This
  is an open decision, not resolved by this phase** (see "Open decisions
  and unresolved gaps" below) -- reconciling the two enums (or
  deliberately keeping them separate, with an explicit mapping table
  when integration happens) is left to whichever future phase actually
  wires canonical assets into `VisualRenderer`.
- **`REQUIRED_TI_STATES`**: `frozenset(TiState)` -- every defined
  `TiState` is required for a set to be "complete." Because the MVP
  vocabulary is deliberately small and closed (8 members, no more
  invented), "required" and "all defined members" collapse to the same
  set; this is intentional, not a placeholder for a future subset.
- **`TiAsset`**: `id`, `asset_set_id`, `state`, `asset_type`,
  `relative_path`, `output_format` (reuses `VisualOutputFormat` --
  `PNG`/`JPG` -- no new format enum), `mime_type`, `width`, `height`,
  `transparent_background`, `notes`. No image-bytes field exists on this
  model at all (structurally proven by
  `test_ti_asset_has_no_bytes_field`, which asserts no field annotation
  is `bytes`). Its `@model_validator(mode="after")` enforces, entirely
  without I/O:
  - `width`/`height` both `> 0`.
  - `mime_type` matches `output_format` exactly via a shared lookup table
    (`TI_ASSET_MIME_BY_FORMAT`: `PNG` -> `"image/png"`, `JPG` ->
    `"image/jpeg"`).
  - `relative_path`'s extension matches `output_format` via
    `TI_ASSET_EXTENSION_BY_FORMAT` (`PNG` -> `"png"`, `JPG` -> `"jpg"`) --
    the same `.jpg` convention `CloudflareImageProvider`'s evaluation
    script already uses, reused via a shared dict rather than redefined,
    so the two subsystems cannot silently drift.
  - `transparent_background=True` is rejected unless `output_format` is
    `PNG` -- JPG has no alpha channel, so pairing it with a transparency
    requirement is a contradiction caught at construction time, not
    discovered later by a compositor.
- **`TiAssetSet`**: `id`, `version`, `is_active`, `assets: list[TiAsset]`,
  `notes`, `created_at`. Its `@model_validator(mode="after")` enforces,
  again entirely without I/O:
  - every asset's `asset_set_id` equals the set's own `id`.
  - no two assets share a `TiState` (duplicate-state rejection).
  - every `TiState` in `REQUIRED_TI_STATES` is covered by some asset
    (completeness rejection, naming every missing state in the error).
  - `created_at` is timezone-aware; `version` is non-blank; `assets` is
    non-empty.

  Enforcing duplicate-state and completeness **inside the Pydantic model
  itself**, rather than as a separate call a caller might forget to make,
  is the deliberate design choice satisfying "no fuzzy or LLM-based
  selection" and "canonical asset set completeness" as *structural*
  guarantees: an incomplete or conflicting `TiAssetSet` cannot be
  constructed in memory at all, so it can never reach storage.

### `app/ti_assets/` -- storage, lookup, validation, retrieval

- **`storage.py`** (`TiAssetFileStore`): atomic `write` (temp file +
  rename, identical technique to `VisualFileStore`/`AudioFileStore`),
  plus `exists`/`resolve` -- the read-side operations a pure
  render-output store never needed, but canonical-asset retrieval does.
  `build_relative_path(asset_set_id, version, state, output_format)` is
  the single place the `{asset_set_id}/{version}/{state}.{ext}` naming
  convention from this phase's brief is implemented (e.g.
  `3fae.../v1/NEUTRAL.png`) -- nowhere else constructs this path by hand.
  Path-safety validation (reject blank, absolute, or `..`-containing
  paths, normalizing backslashes first) is the same proven logic
  `VisualFileStore`/`AudioFileStore` already use, copied rather than
  imported to keep `app/ti_assets/` free of any `app/visual/` dependency.
- **`lookup.py`**: `index_assets_by_state`/`get_asset_by_state` --
  `TiState -> TiAsset` by exact key match only, using a plain `dict`.
  Never returns `None`; a missing state raises
  `TiAssetStateNotFoundError` explicitly. Deliberately independent of
  `TiAssetSet`'s own completeness guarantee, so this module's own tests
  can exercise "missing state" directly with a partial list, rather than
  relying on an otherwise-unreachable code path.
- **`validation.py`**: `validate_files_exist(asset_set, file_store)` --
  the *only* validation rule in this phase that genuinely requires disk
  access (duplicate-state and completeness need none, so they live in
  the Pydantic model instead, per above). Raises
  `TiAssetMissingFileError` naming every state whose file is absent.
- **`errors.py`**: two hierarchies, mirroring `app/visual/errors.py`'s
  split -- `TiAssetError` (`TiAssetStateNotFoundError`,
  `TiAssetMissingFileError`) for domain/validation failures, and a
  separate `TiAssetStorageError` (`TiAssetPathError`,
  `TiAssetWriteError`) for filesystem failures. No shared base, so a
  caller can never accidentally catch one class of failure while meaning
  the other.
- **`retriever.py`** -- **the future integration boundary this phase was
  asked to define**:
  - `TiAssetRetriever`, a `@runtime_checkable` `Protocol` with
    `get_active_asset_set() -> TiAssetSet`,
    `get_asset(state: TiState) -> TiAsset`,
    `list_available_states() -> list[TiState]`, and
    `resolve_path(asset: TiAsset) -> Path`. This plays exactly the role
    `VisualProvider` plays for `VisualRenderer`: a future compositor or
    `VisualRenderer` integration depends on this Protocol type, never on
    `SqliteTiAssetRetriever`, SQLAlchemy, or `TiAssetFileStore` directly.
  - `SqliteTiAssetRetriever` is the one concrete implementation Phase 21
    ships, wrapping a SQLAlchemy `Engine` (via
    `app/storage/ti_assets.py`) and a `TiAssetFileStore`.
    `get_asset(state)` looks the state up in the active set, then
    **re-confirms the file exists on disk** before returning --
    catching metadata/filesystem drift (a row claims a path, but the
    file was moved/deleted) rather than handing back a reference to
    nothing. `list_available_states()` returns a deterministically
    sorted list (by enum value); `resolve_path()` delegates to
    `TiAssetFileStore.resolve()`.

  No provider call, no LLM call, and no caching/memoization layer exists
  here -- every call re-reads SQLite and re-checks the filesystem,
  intentionally, so "no silent fallback to random generated mascot
  imagery" holds even if the underlying data changes between calls.

### Storage: `TiAssetSetRow` and `app/storage/ti_assets.py`

`TiAssetSetRow` (`app/storage/orm.py`) has a **composite primary key**,
`(asset_set_id, version)` -- unlike `ArtifactRow`'s upsert-keep-only-
latest pattern, Phase 21 requires explicit version history: activating a
new version must not destroy the previous one. Each row stores one fully
validated `TiAssetSet` as a JSON payload (`payload_json`, via
`model_dump_json()`/`model_validate_json()`, identical technique to
`ArtifactRow`) plus a **dedicated `is_active` column**.

`is_active` is deliberately duplicated -- once as a plain field inside
the JSON payload (part of the domain model), and once as its own
queryable column. The column, not the JSON, is authoritative for reads:
`app/storage/ti_assets.py`'s `_to_domain` parses the JSON and then
overwrites `is_active` with the column's value
(`asset_set.model_copy(update={"is_active": row.is_active})`). This
detail exists because of a real bug caught during this phase's own
testing -- see "A bug this phase's own tests caught" below.

`app/storage/ti_assets.py` provides:

- `save_ti_asset_set(engine, asset_set)` -- insert-or-update the
  `(asset_set_id, version)` row. If `asset_set.is_active` is `True`,
  every other row in the entire table is set inactive **first, in the
  same transaction**, via one `UPDATE ti_asset_sets SET is_active =
  false` with no `WHERE` clause -- the single place the "at most one
  active canonical set" invariant is enforced.
- `get_ti_asset_set(engine, asset_set_id, version)` /
  `list_ti_asset_sets(engine)` -- direct lookup/listing, raising
  `TiAssetSetNotFoundError` (added to `app/storage/errors.py`, alongside
  the existing `ArtifactNotFoundError`) rather than returning `None`.
- `get_active_ti_asset_set(engine)` -- queries `is_active = true`.
  Raises `TiAssetSetNotFoundError` if no row is active (never guesses
  the most recent version instead), and `MultipleActiveTiAssetSetsError`
  (also new in `app/storage/errors.py`) as a defensive check if more than
  one row is ever found active -- `save_ti_asset_set`/
  `activate_ti_asset_set` are written so this should be unreachable in
  normal use, but the check exists rather than assuming the invariant
  holds forever.
- `activate_ti_asset_set(engine, asset_set_id, version)` -- the same
  atomic clear-then-set pattern as `save_ti_asset_set`, for flipping an
  already-stored version active without re-submitting its full payload.

A corrupt stored payload raises `TiAssetSetValidationError` (new in
`app/storage/errors.py`), mirroring `ArtifactValidationError` exactly.

#### A bug this phase's own tests caught

The first version of `_to_domain` reconstructed a `TiAssetSet` purely
from `row.payload_json`. `activate_ti_asset_set`'s bulk
`UPDATE ... SET is_active = false` only touches the `is_active` *column*
-- it does not, and structurally cannot cheaply, rewrite every other
row's `payload_json` blob too. The result: after activating version
`v2`, reading back version `v1` returned a `TiAssetSet` whose
`is_active` field still read `True`, straight from the stale JSON, even
though the column correctly said `False`.
`test_activating_one_version_deactivates_all_others` caught this
immediately. Fixed by making the column authoritative on every read
(`_to_domain` applies `model_copy(update={"is_active": row.is_active})`
after JSON parsing) rather than trying to keep two representations of
the same fact in permanent sync.

### Retrieval API and "no silent fallback"

Every public entry point in this phase -- `get_active_ti_asset_set`,
`get_ti_asset_set`, `TiAssetRetriever.get_asset`,
`lookup.get_asset_by_state` -- either returns the exact requested value
or raises a specific, named exception. None of them return `None`,
return a default/placeholder `TiAsset`, or fall back to any form of
AI-generated mascot imagery. This is the literal implementation of
"deterministic identity" and "no silent fallback" from this phase's
brief, not merely a design intention: every "missing" scenario this
phase anticipated (no active set at all; a state absent from the active
set; a file missing from disk despite being registered) is a dedicated,
tested, named error.

### What Phase 21 deliberately does not do

No image generation for Tí (no provider call of any kind from
`app/ti_assets/`). No scene compositing -- no code anywhere combines a
`TiAsset` with a background image. No diagram system. No editor/
compositor automation. No change to `VisualRenderer`,
`app/visual/router.py`, `MediaTypeVisualProvider`,
`CloudflareImageProvider`, or `GeminiImageProvider` -- confirmed by
running their full pre-existing test suites unmodified alongside this
phase's new tests. `VisualMediaType.TI_STATE` beats still resolve to the
same `"ti_state:<VisualTiState value>"` placeholder reference they did
before this phase (`app/renderers/visual/renderer.py`'s
`_reuse_only_requirement`) -- nothing consumes `TiAssetRetriever` yet.
No populated real Tí asset files are part of this phase; only the system
that would store, validate, and retrieve them once a human supplies them.

### Open decisions and unresolved gaps (recorded, not resolved, this phase)

1. **`TiState` vs. `VisualTiState`**: two different visual-expression
   vocabularies (8 vs. 9 members, different membership) now exist side
   by side, with no mapping between them. A future phase must decide
   whether to unify them, add an explicit mapping table, or keep them
   permanently separate on the grounds that "canonical asset identity"
   and "prompt-generation mood" are genuinely different concepts.
2. **Multiple asset-set lineages**: `TiAssetSetRow`'s schema technically
   supports more than one `asset_set_id` lineage (e.g. a second
   character), but nothing in Phase 21 exercises or tests that -- there
   is exactly one Tí, and the MVP only ever expects one active lineage
   at a time.
3. **Populating the first real canonical asset set** -- the actual
   artist-produced Tí image files for all 8 states -- is not part of
   this phase. `app/ti_assets/` has no ingestion script, no CLI, and no
   default/seed data; a human must supply the files and register them
   (via `save_ti_asset_set`) before any future retriever call can
   succeed.
4. **Wiring `TiAssetRetriever` into production rendering** (resolving a
   `TI_STATE` beat's reference through it, likely inside a future
   compositor rather than `VisualRenderer` itself) is intentionally left
   to a later phase, per this phase's explicit instructions.

## Phase 21.1 — Tí State Mapping + Canonical Asset Ingestion (approved)

Closes open decisions 1 and 3 from Phase 21, above -- **still contracts/
storage/retrieval only**. No compositing, no image generation for Tí, no
change to `CloudflareImageProvider`/`GeminiImageProvider`, no diagrams, no
LLM calls.

### Part A: `VisualTiState -> TiState` -- an explicit bridge, not a merge

Phase 21's open decision #1 asked whether `VisualTiState` (Phase 14,
narrative/visual-planning mood -- what a generated still's prompt should
convey) and `TiState` (Phase 21, canonical brand-asset identity -- which
fixed file a compositor reuses) should be unified. This phase's explicit
instruction was to keep them **two separate enums** -- they describe
related but genuinely different things, and collapsing them would force
one vocabulary to serve two purposes it wasn't designed for. What was
missing was the explicit translation between them, added here as
`app/ti_assets/visual_state_mapping.py`:

```
VisualTiState.NEUTRAL   -> TiState.NEUTRAL
VisualTiState.CURIOUS   -> TiState.CURIOUS
VisualTiState.SKEPTICAL -> TiState.SKEPTICAL
VisualTiState.EXCITED   -> TiState.EXCITED
VisualTiState.DEADPAN   -> TiState.DEADPAN
VisualTiState.PANIC     -> TiState.PANIC
VisualTiState.CONFUSED  -> TiState.CURIOUS   (canonical decision)
VisualTiState.SURPRISED -> TiState.EXCITED   (canonical decision)
VisualTiState.SMUG      -> TiState.SKEPTICAL (canonical decision)
```

`VISUAL_TI_STATE_TO_TI_STATE` is a `MappingProxyType` -- read-only at the
Python level, not just by convention (`test_mapping_table_is_read_only`
asserts item assignment raises `TypeError`). `to_ti_state(visual_state)`
is the only intended entry point; it raises `ValueError` for anything not
a key in the table -- there is no `.get(..., TiState.NEUTRAL)`-style
default anywhere in this module. A module-level check
(`if VISUAL_TI_STATE_TO_TI_STATE.keys() != set(VisualTiState): raise
RuntimeError(...)`) runs at import time, so an incomplete table is a
load-time failure, not something a caller could discover only after
hitting the one unmapped value in production -- the same "structural
guarantee over runtime hope" pattern `TiAssetSet`'s own
duplicate/completeness validator already established in Phase 21.

`TiState.SERIOUS` and `TiState.LOW_ENERGY` are **not** mapping targets --
documented explicitly, both in this module's docstring and in tests
(`test_serious_and_low_energy_have_no_visual_ti_state_source`), as
canonical assets a future compositor or editor may select directly (e.g.
from an authored beat's own explicit state choice), never derived from a
`VisualTiState` value. This is intentional: nothing in `VisualTiState`'s
9-member vocabulary expresses "serious" or "low energy" today, and
inventing a forced mapping onto one of them (e.g. `DEADPAN -> SERIOUS`)
would be exactly the kind of fuzzy, unrequested inference this phase's
brief explicitly prohibited.

This mapping is not wired into `app/renderers/visual/renderer.py` or
`app/visual/router.py` -- `_reuse_only_requirement`'s
`"ti_state:<VisualTiState value>"` reference construction is completely
unchanged. A future phase resolving that reference into a canonical asset
would call `to_ti_state()` on the beat's `VisualTiState`, then
`TiAssetRetriever.get_asset()` on the result; Phase 21.1 only makes that
call *possible*, deterministic, and fully tested -- it does not make it.

### Part B: canonical asset ingestion

Phase 21's open decision #3 was that no ingestion path existed at all --
`save_ti_asset_set` could only be called by hand-constructing a full
`TiAssetSet` in Python. This phase adds the deterministic path a human
actually uses.

#### Dependency decision: Pillow evaluated, not added

Before writing any image-inspection code, the environment and
`pyproject.toml` were both checked: Pillow was not installed and not
declared. Per this phase's explicit instruction ("if not available, first
report that fact and use Pillow only if adding it is justified"), that
absence was reported, and the justification question was then answered
directly: Phase 21.1's entire validation surface is (1) confirm the file
is a structurally valid PNG, (2) read its width/height, and (3) determine
whether it carries an alpha channel or `tRNS`-based transparency. All
three live in a PNG's signature and its first few chunks (`IHDR`,
optionally `tRNS`) -- none require decoding a single pixel of `IDAT`
data. Python's stdlib `struct` module (already used elsewhere in this
codebase for binary parsing) is sufficient for exactly this, so the
**zero-new-dependency** option was chosen over adding Pillow -- the same
reasoning `CloudflareImageProvider` (Phase 20.2) applied when it reused
`httpx` directly rather than adding a vendor SDK for a REST-only need.
OpenCV was never considered a candidate (explicitly excluded by this
phase's instructions regardless).

`app/ti_assets/png_metadata.py`'s `read_png_metadata(data) ->
PngMetadata` implements exactly this narrow surface -- explicitly *not* a
general-purpose PNG/image library, and documented as such so a future
reader doesn't reach for it expecting pixel access. `has_alpha` is `True`
unconditionally for color types 4 (grayscale+alpha) and 6
(truecolor+alpha), or conditionally for color types 0/2/3 (grayscale/
truecolor/palette) only when a `tRNS` chunk is present in the file.
Malformed input (bad signature, truncated chunk, missing `IHDR`, or
non-positive width/height) raises `TiAssetInvalidImageError` (new in
`app/ti_assets/errors.py`) rather than guessing or returning partial
metadata. Tests exercise this against synthetic, structurally-valid PNGs
built entirely from stdlib (`tests/_png_helpers.py`, using `struct` and
`zlib` -- no Pillow, no real image file, no fabricated Tí artwork) for
every relevant color-type/tRNS combination, plus garbage bytes, a
truncated chunk header, a missing `IHDR`, and zero width/height.

#### `ingest_ti_asset_set` -- validate everything, then mutate nothing halfway

`app/ti_assets/ingest.py`'s `ingest_ti_asset_set(engine, file_store,
source_dir, version, notes=None, activate=False)` enforces this phase's
MVP policy exactly, with no exceptions carved out:

- PNG only; transparent background (alpha or `tRNS`) required.
- All 8 exact filenames required -- `REQUIRED_FILENAMES` maps each
  `TiState` to exactly `f"{state.value}.png"` (`NEUTRAL.png` through
  `LOW_ENERGY.png`). No case-insensitive matching is attempted deliberately
  (though note: on an NTFS/Windows filesystem, "wrong case" is not even a
  distinguishable input -- the filesystem itself treats `panic.png` and
  `PANIC.png` as the same path; the real guarantee this code provides is
  that no name *other* than the exact 8 is ever inferred as a match, which
  `test_wrong_filename_is_treated_as_missing` proves with a genuinely
  different filename, not a case variant).
- No extra-state inference from any other filename present in the
  directory -- unrecognized files are silently ignored, never interpreted.
- No AI generation, no automatic repair, no silent missing-file fallback.

Order of operations:

1. **Version-conflict check first**: `list_ti_asset_sets(engine)` is
   scanned for any existing `TiAssetSet` whose `version` string matches
   the requested one, *before* the source directory is even opened. A
   match raises `TiAssetIngestionError` immediately -- re-ingesting an
   already-used version label is a conflict, never a silent overwrite and
   never given a second, different `asset_set_id` under the same label.
2. **Full source-directory validation**: every one of the 8 required
   files is checked -- existence, valid PNG (via `read_png_metadata`),
   and alpha/tRNS transparency -- with **every problem collected and
   reported together** in one `TiAssetIngestionError`, not just the
   first one found (`test_reports_every_problem_not_just_the_first`).
   Nothing on disk or in the database has been touched yet at this point.
3. **Copy**: only once validation fully passes, each file's already-
   validated bytes are written via `TiAssetFileStore.write` under a
   freshly-minted `uuid4()` `asset_set_id`, using the existing
   `build_relative_path(asset_set_id, version, state, output_format)`
   convention unchanged from Phase 21.
4. **Construct**: a `TiAsset` per state (from the real
   `PngMetadata.width`/`height`, `transparent_background=True` always,
   since step 2 already required it) and one `TiAssetSet` wrapping them
   -- re-validated structurally by `TiAssetSet`'s own constructor
   (completeness/no-duplicates), cheap insurance on top of step 2's
   checks.
5. **Persist**: `save_ti_asset_set(engine, asset_set)` -- the existing
   Phase 21 repository function, unmodified. `activate` is threaded
   straight into `TiAssetSet.is_active`, so the existing atomic
   deactivate-then-set behavior handles "make this the sole active set"
   with no new logic required here.

**Atomicity**: a validation failure (step 2) never reaches step 3, 4, or
5 -- no file is copied and no database row is written. A filesystem
failure partway through step 3 (e.g. disk full on the 5th of 8 files)
propagates immediately; step 5 (`save_ti_asset_set`, the only place a
database row is created) is never reached, so **an incomplete set can
never be stored, active or inactive**.

**Orphan files -- documented honestly, not hidden**: if step 3 fails
partway through, the files it already copied before the failure are
**not** removed. This phase's own "no automatic repair" policy applies
to its own failure path: writing a cleanup routine would itself be a form
of automatic repair. This is safe -- a fresh `uuid4()` is minted on every
call, so a retried ingestion's paths can never collide with a previous
failed attempt's orphans -- but it is not tidy, and a human running the
CLI against a failing disk should expect to manually remove
`file_store.root / <old asset_set_id>` afterward.

#### `scripts/ingest_ti_assets.py`

The human-facing entry point:

```
python scripts/ingest_ti_assets.py --source <dir> --version v1
python scripts/ingest_ti_assets.py --source <dir> --version v1 --activate
python scripts/ingest_ti_assets.py --source <dir> --version v2 --notes "redesigned eyes" --activate
```

Mirrors `scripts/evaluate_cloudflare_image.py`'s conventions exactly:
`argparse`, a UTF-8 stream reconfigure (Tí's diacritic breaks a Windows
console's default codepage otherwise), a clear one-line failure message
to stderr with exit code 1 on any `TiAssetError`, exit 0 with a short
summary on success. `--db`/`--asset-root` default to
`app.storage.database.DEFAULT_DB_PATH`/
`app.ti_assets.storage.DEFAULT_TI_ASSET_ROOT` respectively, so a bare
`--source`/`--version` targets the same database and asset root the rest
of the application uses by default. The script fabricates nothing: run
against an incomplete or missing source directory, it fails with the
same `TiAssetIngestionError` message `ingest_ti_asset_set` itself would
raise, naming every missing/invalid file.

### What Phase 21.1 deliberately does not do

No scene compositing -- nothing here combines a `TiAsset` with a
background image. No image generation for Tí, by any provider or any
other means -- `ingest_ti_asset_set` only ever reads bytes a human
already placed on disk; it never calls a network API, an LLM, or a
generation library. No change to `CloudflareImageProvider`,
`GeminiImageProvider`, `VisualRenderer`, or `app/visual/router.py` --
confirmed by re-running their full pre-existing test suites unmodified.
No diagrams. No automatic retry or repair of a bad source file -- a
human must fix the file and re-run the command. No cleanup of orphaned
files from a failed ingestion (see above). No real Tí artwork is created,
fabricated, or committed by this phase -- test fixtures use synthetic,
visually-meaningless PNGs built entirely from `struct`/`zlib`
(`tests/_png_helpers.py`), and the CLI has no seed data of its own.

### Remaining before Phase 22

A human must supply the first real, artist-produced set of 8 Tí PNGs
(`NEUTRAL.png` through `LOW_ENERGY.png`, each with genuine alpha
transparency) and run `scripts/ingest_ti_assets.py --activate` against
them -- nothing in this repository can perform that step. Wiring
`TiAssetRetriever` into `VisualRenderer` or a future compositor (resolving
a `TI_STATE` beat's `VisualTiState` through `to_ti_state()` and then
`SqliteTiAssetRetriever.get_asset()`) remains explicitly out of scope for
this phase and is left to whichever future phase implements compositing.

By the time Phase 22 started, this had already happened outside this
repository's own tooling: a real 8-PNG `v1` set is stored under
`data/ti_assets/<asset_set_id>/v1/` and marked active in `data/motily.db`.
Phase 22 treats that as a given, pre-existing fact -- it neither performs
nor re-verifies the ingestion step; it only ever reads through
`TiAssetRetriever`, exactly as designed.

## Phase 22 — Deterministic Tí Compositor (approved)

Builds the thing Phase 21/21.1 deliberately stopped short of: an actual
compositor that combines one canonical `TiAsset` with an existing
background image. Still **not** wired into `VisualRenderer`,
`app/visual/router.py`, or either real `VisualProvider` -- that remains
explicitly out of scope, left to a future phase. No AI generation of Tí
by any means, no diagrams, no LLM-based placement, no computer-vision or
saliency placement. `CloudflareImageProvider`/`GeminiImageProvider` are
untouched, confirmed by their full pre-existing test suites still passing
unmodified.

### A new, separate architecture boundary: `app/ti_compositor/`

Mirrors the precedent `app/visual/provider.py` set for `VisualRenderer`
and `app/ti_assets/retriever.py` set for a future compositor: a small
package of typed contracts plus one concrete implementation, depending on
`TiAssetRetriever` the same way a `VisualProvider` consumer depends on
that `Protocol` -- never on SQLite or filesystem details directly, and
never on a concrete `VisualProvider`.

`app/ti_compositor/models.py` -- dependency-free (no Pillow, no
filesystem I/O) contracts:

- **`TiAnchor`** (`str, Enum`) -- the fixed MVP placement vocabulary:
  `BOTTOM_LEFT`, `BOTTOM_CENTER`, `BOTTOM_RIGHT`, `CENTER_LEFT`, `CENTER`,
  `CENTER_RIGHT`. No other anchor exists, and none is inferred.
- **`TiScalePolicy`** -- one field, `relative_height: float`, validated
  `0 < relative_height <= 1` at construction time (a `model_validator`,
  the same "structural guarantee" pattern used throughout
  `app/models/ti_assets.py`). `0.20` means Tí's rendered height is 20% of
  the background's height; width always follows from Tí's own asset
  aspect ratio, never specified independently.
- **`TiPlacement`** -- `anchor: TiAnchor`, `scale: TiScalePolicy`,
  `margin: int = 0` (validated `>= 0`), `offset_x: int = 0`,
  `offset_y: int = 0`. `margin` is inward padding applied only on the
  edge-anchored axis/axes of `anchor` (e.g. `BOTTOM_LEFT`'s bottom and
  left edges; `CENTER` ignores it entirely on both axes, since it has no
  edge to pad from). `offset_x`/`offset_y` are an unrestricted pixel nudge
  applied after anchor+margin -- deliberately allowed to push the result
  out-of-frame, since that is exactly the case the compositor's
  documented out-of-bounds policy (below) exists to handle.
- **`TiCompositeRequest`** -- `background_path: Path`, `output_path:
  Path`, `placement: TiPlacement`, and **exactly one** of
  `visual_ti_state: VisualTiState | None` or `ti_state: TiState | None`.
  A `model_validator` rejects both-set and neither-set with a `ValueError`
  naming both fields' values -- there is no default state and no
  "prefer one if both given" behavior. `background_path`'s suffix must be
  `.png`/`.jpg`/`.jpeg` (case-insensitive); `output_path`'s suffix must be
  `.png` -- both checked here, at request-construction time, before
  `TiCompositor` ever touches a file.
- **`TiCompositeResult`** -- `output_path`, `resolved_ti_state`,
  `source_visual_ti_state` (echoed back, `None` if the request used
  `ti_state` directly), `background_width`/`background_height`,
  `ti_rendered_width`/`ti_rendered_height`, `requested_x`/`requested_y`
  (the pre-clamp position), `placement_x`/`placement_y` (the actual,
  post-clamp position used), and `clamped: bool`. Enough detail to assert
  exact placement/scale behavior in tests without re-opening the output
  image.

`app/ti_compositor/errors.py` -- its own hierarchy, not a subclass of
`TiAssetError` or `VisualError` (a compositing-step problem is a
different kind of problem from asset retrieval or provider rendering,
even though `TiCompositor` depends on `TiAssetRetriever`):
`TiCompositorError` (base), `TiCompositeBackgroundError` (missing/
unsupported/corrupt background), `TiCompositeAssetError` (the retriever's
own existence check passed but the resolved Tí file's bytes don't decode
as an image), `TiCompositeBoundsError` (see the out-of-bounds policy
below), `TiCompositeWriteError` (output write failure). `TiCompositor`
never wraps or swallows `TiAssetRetriever`'s own errors
(`TiAssetSetNotFoundError`, `TiAssetStateNotFoundError`,
`TiAssetMissingFileError`) -- a missing active set or missing state asset
propagates unchanged, exactly as it does from `SqliteTiAssetRetriever`
directly.

### Dependency decision: Pillow added, deliberately reversing Phase 21.1's call

Phase 21.1 evaluated Pillow and explicitly did not add it, because its
entire validation surface (valid-PNG check, width/height, alpha/`tRNS`
presence) lives in a PNG's header bytes and needs zero pixel decoding --
`struct` was sufficient. Phase 22's job is fundamentally different: it
must decode JPEG *and* PNG pixel data, resize a raster image preserving
aspect ratio, and alpha-composite one decoded image onto another. None of
that is reachable from header bytes alone, and hand-writing a JPEG/PNG
codec in the stdlib would be a far larger and riskier undertaking than
adding a well-established imaging library. Pillow (`>=11.0,<13.0` in
`pyproject.toml`) was added for exactly this need, and only this need --
`app/ti_assets/png_metadata.py` is untouched and remains Pillow-free, and
neither `app/visual/providers/cloudflare.py` nor
`app/visual/providers/gemini.py` imports Pillow (both files' existing
"no Pillow import" tests still pass unmodified).

Note on Windows/Python 3.14: this environment's venv is CPython 3.14,
for which Pillow's own PyPI classifiers only ship a prebuilt wheel from
Pillow 11 onward (10.x attempts to build from source and fails without
system zlib headers). `pyproject.toml`'s `Pillow>=11.0,<13.0` reflects
that constraint directly rather than pinning an arbitrary range.

### `TiCompositor.composite()` -- the compositing pipeline

`app/ti_compositor/compositor.py`'s `TiCompositor(retriever:
TiAssetRetriever)` has exactly one public method:

1. **Resolve state**: `ti_state` is used as-is; `visual_ti_state` is
   passed through Phase 21.1's existing `to_ti_state()` unchanged. No
   fuzzy inference either way.
2. **Retrieve the canonical asset**: `retriever.get_asset(ti_state)` then
   `retriever.resolve_path(asset)` -- the exact same two calls a
   hand-written integration would make; `TiCompositor` adds no caching,
   no retry, and no fallback around them.
3. **Load background**: `Path.is_file()` checked first (a clear
   `TiCompositeBackgroundError` if not, rather than letting Pillow's own
   exception surface as the first error), suffix checked against
   `{.png, .jpg, .jpeg}`, then `Image.open(...).load()` -- any
   `UnidentifiedImageError`/`OSError` is translated to
   `TiCompositeBackgroundError`. The result is converted to `RGB`: **any
   alpha channel a background PNG happens to carry is intentionally
   dropped** -- Phase 22 treats the background as the final, fully opaque
   canvas, never a further-composable transparent layer.
4. **Load the Tí asset** the same way, converted to `RGBA` (translating
   decode failures to `TiCompositeAssetError` instead).
5. **Scale**: `compute_scaled_size(ti_size, background_height,
   relative_height)` computes `target_height = round(background_height *
   relative_height)` and `target_width` from Tí's own aspect ratio (both
   floored at 1px so a pathologically small `relative_height` can never
   produce a zero-size image), then `Image.resize(target_size,
   Image.LANCZOS)`.
6. **Position**: `compute_anchor_position(...)` computes the requested
   (pre-clamp) top-left pixel per the anchor/margin/offset semantics
   above; `clamp_position(...)` applies the out-of-bounds policy (below)
   to produce the final position and a `clamped` flag.
7. **Composite**: `background.copy()`, then `composed.paste(ti_resized,
   (x, y), ti_resized)` -- Pillow's mask-based paste, using the Tí image's
   own alpha channel as its own mask. Because the background canvas is
   always fully opaque (step 3), this is a correct "straight over
   opaque-base" composite: fully-opaque Tí pixels fully overwrite the
   background, fully-transparent Tí pixels leave the background pixel
   completely untouched (no white/black fringe baked in), and partially-
   transparent edge pixels blend proportionally.
8. **Write**: `output_path.parent.mkdir(parents=True, exist_ok=True)`
   then `image.save(output_path, format="PNG")`, translating any `OSError`
   to `TiCompositeWriteError`. Output is unconditionally PNG (enforced
   earlier by `TiCompositeRequest`'s suffix check, not merely a default)
   -- the requirement's "PNG preferred for deterministic lossless
   composition" is read here as a hard requirement precisely so
   "deterministic output for identical inputs" isn't left fragile to a
   lossy JPEG re-encode varying across `libjpeg` versions.

Output dimensions always equal background dimensions -- the composited
image is `background.copy()` with Tí pasted into it, never resized or
cropped as a whole.

### Out-of-bounds policy: clamp first, fail only when clamping cannot work

Phase 22's brief required picking one policy and documenting it, not
offering both as a runtime option. This compositor **clamps**:

`TiScalePolicy` already guarantees Tí's rendered *height* can never
exceed the background's height (`0 < relative_height <= 1`). Given that,
`clamp_position(background_size, ti_size, position)`:

- Raises `TiCompositeBoundsError` immediately, before attempting any
  clamp, if the rendered Tí **width** exceeds the background's width --
  this can happen when Tí's own aspect ratio is wide relative to a narrow
  background even though its height fits. In that case no position,
  clamped or not, could ever place the full asset in-frame, so failing
  loudly is the only choice that isn't a silently broken/cropped
  composite.
- Otherwise, clamps `x` into `[0, background_width - ti_width]` and `y`
  into `[0, background_height - ti_height]` -- guaranteeing the full
  rendered Tí image always lands entirely within the frame regardless of
  how large an `offset_x`/`offset_y` a caller supplies. `clamped` is
  `True` iff this actually moved the position from what
  `compute_anchor_position()` requested.

This means an intentionally large offset (e.g. "nudge Tí 10,000px past
the right edge") never silently vanishes or gets cut off mid-image --
it lands flush against the edge instead, and the caller can tell it
happened via `TiCompositeResult.clamped`.

### `scripts/evaluate_ti_compositor.py` -- human evaluation, no scoring

Same spirit as `scripts/evaluate_gemini_image.py`/
`scripts/evaluate_cloudflare_image.py`: no automated aesthetic/quality
scoring, no LLM judge, a human looks and decides. Composites exactly the
3 cases the phase brief specified --

```
TiState.NEUTRAL -> TiAnchor.BOTTOM_LEFT   -> neutral_bottom_left.png
TiState.CURIOUS -> TiAnchor.BOTTOM_RIGHT  -> curious_bottom_right.png
TiState.PANIC   -> TiAnchor.CENTER_RIGHT  -> panic_center_right.png
```

-- from the active `v1` `TiAssetSet` (real, human-supplied canonical
assets, not synthetic test fixtures), at `relative_height=0.35` with a
24px margin, onto `data/cloudflare_image_evaluation/L1_ESTABLISH.jpg`.
That file was chosen deliberately over generating a new background: it is
the one existing local image this repository already had that (a) is a
plausible video-scene background, and (b) does not already have Tí
composited into it (unlike `TI_PRESENT.jpg` in the same directory, a
Phase 20.2 `GENERATED_STILL` brief that already asked for Tí to be
present in the AI-generated scene itself). Per this phase's explicit
instruction, if no such existing background had been found, the script
would report that and stop rather than generating a new one -- that branch
exists in the script (`--background` not found -> stderr message, exit
1) but was not needed here. Output lands in
`data/ti_compositor_evaluation/`.

### What Phase 22 deliberately does not do

Not wired into `VisualRenderer`, `app/visual/router.py`, or either real
`VisualProvider` -- `VisualMediaType.TI_STATE` beats still resolve to the
same `"ti_state:<VisualTiState value>"` placeholder reference as before
this phase; resolving that reference through `TiCompositor` is left to a
future phase. No diagrams. No LLM-based or computer-vision/saliency-based
placement -- only the fixed 6-anchor vocabulary. No new Tí artwork is
generated, fabricated, or committed by this phase -- it only ever reads
the already-active, human-supplied `v1` asset set through
`TiAssetRetriever`. No change to `CloudflareImageProvider` or
`GeminiImageProvider`, confirmed by their full pre-existing test suites
passing unmodified. No video/motion compositing -- one still background,
one still Tí asset, one still output.

## Phase 23 — VisualRenderer ↔ Tí Compositor Integration (approved)

Wires Phase 22's `TiCompositor` into `VisualRenderer` -- the integration
Phase 22 explicitly deferred. `TI_STATE`'s old placeholder-only handling
(`_reuse_only_requirement`'s `"ti_state:<VisualTiState value>"` string,
never resolved to anything real) is replaced with deterministic canonical
resolution/compositing. No LLM placement, no diagrams, no video
compositing, and no change to `CloudflareImageProvider`/
`GeminiImageProvider` -- both files' full pre-existing test suites still
pass unmodified.

### Integration boundary: one injected, optional dependency

`VisualRenderer.__init__` gains exactly one new keyword-only parameter,
`ti_compositor: TiCompositor | None = None`, matching this phase's
preferred shape literally. `VisualRenderer` never constructs a
`SqliteTiAssetRetriever`, a `TiAssetFileStore`, or its own database engine
for Tí assets -- every canonical-asset operation goes through the
injected `TiCompositor`, either its existing `composite()` (Phase 22,
unchanged) or its one new addition:

```python
# app/ti_compositor/compositor.py
@property
def retriever(self) -> TiAssetRetriever:
    return self._retriever
```

This read-only property is the only change `TiCompositor` itself needed.
It exists because Phase 23's `STANDALONE` mode (below) needs canonical-
asset identity/metadata -- `TiAssetRetriever.get_asset()`/
`get_active_asset_set()` -- without loading, resizing, or compositing any
image bytes, which `composite()` alone cannot provide. Exposing the
retriever this way keeps `VisualRenderer` depending on exactly one thing
(`TiCompositor`), not two (`TiCompositor` and a separately-injected
`TiAssetRetriever`), while adding zero new Pillow/filesystem/SQLite
surface to `VisualRenderer` itself. `TiCompositor.composite()`'s own
behavior, tests, and Phase 22 out-of-bounds/alpha-compositing policies are
completely unchanged.

### Two explicit, caller-selected TI_STATE modes

`app/renderers/visual/models.py` adds the minimum typed contract needed
to make a `TI_STATE` beat's mode an explicit caller decision, never
inferred from beat content:

```python
class TiStateRenderMode(str, Enum):
    STANDALONE = "STANDALONE"
    COMPOSITE = "COMPOSITE"

class TiStateSource(MotilyModel):
    mode: TiStateRenderMode = TiStateRenderMode.STANDALONE
    background_path: Path | None = None
    placement: TiPlacement | None = None
    # model_validator: STANDALONE + background_path set is rejected

class VisualRendererInput(MotilyModel):
    project_id: UUID
    ti_state_sources: dict[str, TiStateSource] = Field(default_factory=dict)
```

A `TI_STATE` beat with no entry in `ti_state_sources` (keyed by
`beat_id`) defaults to `STANDALONE` -- the same "never invent a
background" default an explicit `STANDALONE` entry would give, satisfying
this phase's "if no background exists for a TI_STATE beat, preserve a
deterministic canonical-asset reference result rather than failing or
generating imagery" requirement without the caller having to do anything
extra for the common case.

`VisualRenderer._render_ti_state_beat` is the single dispatch point,
called from `_render_all` for every `beat.media_type is
VisualMediaType.TI_STATE` (a new, dedicated branch -- `TI_STATE` is
deliberately absent from `_RENDERABLE_MEDIA_TYPES`/
`_REUSE_ONLY_MEDIA_TYPES`/`_EXTERNAL_REQUIRED_MEDIA_TYPES`, since unlike
every other media type its outcome type (asset vs. requirement) is
decided per beat, not by media_type alone):

1. `beat.ti_state is None` -> `InvalidVisualBeatError` (a `TI_STATE` beat
   must have one -- no fallback to a `reuse_key`, see below).
2. `self._ti_compositor is None` -> `TiCompositorNotConfiguredError`
   immediately -- there is no placeholder fallback and no provider
   fallback available to a misconfigured renderer.
3. `resolved_ti_state = to_ti_state(beat.ti_state)` -- Phase 21.1's
   existing mapping, called exactly once, used for both modes below.
4. Mode `STANDALONE` -> `_standalone_ti_state_requirement`.
5. Mode `COMPOSITE` with `background_path is None` ->
   `TiStateMissingBackgroundError`, before any compositing is attempted.
6. Mode `COMPOSITE` with a background -> `_composite_ti_state_asset`.

#### Mode A: STANDALONE

```python
asset = self._ti_compositor.retriever.get_asset(resolved_ti_state)
active_set = self._ti_compositor.retriever.get_active_asset_set()
return VisualRenderRequirement(
    beat_id=beat.beat_id,
    media_type=VisualMediaType.TI_STATE,
    status=VisualRequirementStatus.CANONICAL_ASSET_READY,  # new status
    reference=asset.relative_path,
    resolved_ti_state=resolved_ti_state,
    notes=f"Standalone canonical Tí asset from TiAssetSet {active_set.id} "
          f"version {active_set.version!r}; no background supplied for this beat",
)
```

No compositing, no file written -- the same "no provider call, no bytes"
shape `REUSE_ONLY` already had, except the reference now points at a real,
existing canonical file instead of an unresolved placeholder string.
`reference` is `asset.relative_path`, which already self-encodes asset-set
id, version, and state via `app/ti_assets/storage.py`'s
`{asset_set_id}/{version}/{state}.ext` convention -- so "canonical asset
identity/version" (this phase's requirement #4) needed no new fields of
its own; the existing path convention already carries it. The one field
this phase does add for identity is `resolved_ti_state` (see below) --
typed, not buried in free text, so a caller can match on it directly.

#### Mode B: COMPOSITE

```python
render_job_id = f"{beat.beat_id}_R1"
relative_path = f"{project_id}/{visual_plan_id}/{render_job_id}.png"   # always .png
output_path = self._visual_store.root / relative_path
request = TiCompositeRequest(
    background_path=source.background_path,
    output_path=output_path,
    placement=source.placement or _DEFAULT_TI_PLACEMENT,
    ti_state=resolved_ti_state,
)
result = self._ti_compositor.composite(request)
return RenderedVisualAsset(
    render_job_id=render_job_id, beat_id=beat.beat_id,
    media_type=VisualMediaType.TI_STATE, file_path=relative_path,
    width=result.background_width, height=result.background_height,
    resolved_ti_state=resolved_ti_state,
    notes=f"Composited canonical Tí onto background {source.background_path}",
)
```

`output_path` is computed as `self._visual_store.root / relative_path` --
reusing `VisualFileStore`'s own `root` property rather than reaching
around it to build a brand-new file store, and letting `TiCompositor`'s
own (Phase 22) file-write logic write the PNG directly at that path.
`VisualFileStore.write()` itself is not used for this one write, since
`TiCompositor.composite()` already owns writing its own output -- calling
both would mean either double-writing or first routing the composited
bytes back through an unnecessary read; using the store's `root` for path
computation only keeps `VisualFileStore` the single source of truth for
*where* a visual asset lives, without forcing a redundant byte copy.
`_DEFAULT_TI_PLACEMENT` (`BOTTOM_RIGHT` anchor, `relative_height=0.35`,
zero margin) is applied whenever `source.placement` is omitted -- not
configurable per-project in this phase; a caller needing something else
supplies an explicit `TiPlacement`.

Output is unconditionally `.png`, regardless of this renderer's own
`VisualSettings.output_format` -- `TiCompositeRequest` itself enforces
PNG-only output (Phase 22's "deterministic lossless composition" policy),
so hard-coding `.png` here keeps `RenderedVisualAsset.file_path` truthful
rather than silently mismatched against a `JPG`-configured renderer.

No retry: `TiCompositor.composite()` is a deterministic local operation
(pixel decode/resize/composite/PNG-encode), not a network call -- there is
nothing transient to retry, and `_render_with_retry`'s budget stays
reserved for `VisualProviderError` exactly as before. This also means a
`TI_STATE` beat, in either mode, contributes `0` to
`provider_call_count` -- satisfying "must not introduce provider
retries" structurally, not just by convention.

### One removed behavior: TI_STATE no longer honors reuse_key

Pre-Phase-21, a `TI_STATE` beat with an authored `reuse_key` would use
that key as its `REUSE_ONLY` reference instead of a `"ti_state:<value>"`
placeholder -- a workaround for the fact that no canonical asset existed
yet to resolve to. Phase 23 removes this branch entirely:
`_reuse_only_requirement` now only handles `ASSET_REUSE` (a `TI_STATE`
beat never reaches it -- `_render_all` routes `TI_STATE` to
`_render_ti_state_beat` directly). A real canonical asset now always
exists for every `TiState`, so an arbitrary `reuse_key` placeholder has no
remaining purpose, and silently preferring it over canonical resolution
would undermine this phase's entire point. `ASSET_REUSE` itself is
completely unaffected -- it keeps its own `reuse_key -> REUSE_ONLY`
behavior unchanged, and its tests were not touched.

### Manifest contract: minimal, additive, no redesign

Per this phase's explicit "avoid broad redesign of `VisualRenderManifest`
unless necessary" instruction, exactly two small optional fields were
added, both defaulting to `None` and both validated to only ever be set
for `media_type is TI_STATE`:

- `RenderedVisualAsset.resolved_ti_state: TiState | None = None` (plus a
  `notes: str | None = None` field, mirroring
  `VisualRenderRequirement.notes`, used to record which background a
  composite came from).
- `VisualRenderRequirement.resolved_ti_state: TiState | None = None`.

And one new enum member, `VisualRequirementStatus.CANONICAL_ASSET_READY`
(joining `RENDERED`/`EXTERNAL_REQUIRED`/`REUSE_ONLY`) -- the explicit
result type this phase's requirement #2 asked for rather than reusing
`REUSE_ONLY` (which would have conflated "an existing, arbitrary asset
should be reused" with "a specific, canonical, resolved asset is ready").
`app/renderers/visual/validation.py` was updated in lockstep:
`_REUSE_ONLY_MEDIA_TYPES` now only contains `ASSET_REUSE`;
`_CANONICAL_ASSET_READY_MEDIA_TYPES` (only `TI_STATE`) gates the new
status; `_ASSET_ALLOWED_MEDIA_TYPES` (the old `_RENDERABLE_MEDIA_TYPES`
plus `TI_STATE`) gates `manifest.assets`, since a `COMPOSITE`-mode
`TI_STATE` asset is real and valid there despite never being
provider-rendered. `VisualRendererResult` gains
`canonical_asset_ready_count: int = 0` alongside its existing counts.
No other shape change: `VisualRenderManifest` itself (its own fields,
its own validators) is completely untouched.

### Freshness / lifecycle: fully preserved

`ModuleRun` RUNNING -> SUCCESS/FAILED lifecycle, the `StaleVoicePlanError`/
`StaleVisualPlanError` freshness gates (checked before any provider call,
`TiCompositor` call, `ModuleRun`, or file write -- unchanged position in
`run()`), manifest persistence/upsert-on-rerun semantics, and the
deterministic `{project_id}/{visual_plan_id}/{render_job_id}` path
convention are all exactly as Phase 19 left them. A `TI_STATE` failure
(missing compositor, missing background, missing active set, missing
canonical file) is recorded as a `FAILED` `ModuleRun` and re-raised
unchanged, via the exact same broad `except Exception` block every other
renderer failure already goes through -- no special-casing needed.

### Errors

- `TiCompositorNotConfiguredError` (new, `app/renderers/visual/errors.py`)
  -- a `TI_STATE` beat with `ti_compositor=None`.
- `TiStateMissingBackgroundError` (new, same file) -- `COMPOSITE` mode
  requested with no `background_path`.
- `TiAssetSetNotFoundError`/`TiAssetMissingFileError` (Phase 21, unchanged)
  -- propagate directly from `TiAssetRetriever.get_asset()`/
  `get_active_asset_set()`, in both modes; never wrapped, never
  substituted with a placeholder or AI-generated image.
- `InvalidVisualBeatError` (Phase 19, unchanged) -- a `TI_STATE` beat with
  no `ti_state` at all.

### `scripts/evaluate_visual_renderer_ti_state_integration.py`

Unlike `scripts/evaluate_ti_compositor.py` (Phase 22, drives
`TiCompositor` directly), this script drives the real
`VisualRenderer.run()` path end to end, to prove the wiring itself works.
It builds one minimal, disposable evaluation project (a dedicated SQLite
file, `data/visual_renderer_ti_state_evaluation/eval.db`, recreated fresh
every run -- never `data/motily.db`) through every review gate to
`MVP_COMPLETE`, with exactly one `TI_STATE` beat, then runs
`VisualRenderer.run()` twice against it:

1. Default input (no `ti_state_sources` entry) -> `STANDALONE` result.
2. An explicit `TiStateSource(mode=COMPOSITE, background_path=...)`
   pointing at the existing
   `data/cloudflare_image_evaluation/L1_ESTABLISH.jpg` -> `COMPOSITE`
   result.

The real active `v1` `TiAssetSet` (from `data/motily.db`) is used for
both -- its metadata (not the PNG bytes, which stay on shared disk at
`data/ti_assets/`) is copied into the disposable evaluation database via
`save_ti_asset_set` so `TiAssetRetriever` can resolve it there too,
without ever touching or depending on real project data. `FakeVisualProvider([])`
is used for the underlying `VisualProvider` -- any call to it at all
would raise immediately, so a clean run through both modes is itself proof
that no AI/provider call ever happened for `TI_STATE`. Both runs print
`provider_call_count == 0`. Output lands in
`data/visual_renderer_ti_state_evaluation/`.

### What Phase 23 deliberately does not do

No LLM-based or computer-vision/saliency-based placement (unchanged from
Phase 22 -- only the fixed 6-anchor vocabulary, now reachable via
`TiStateSource.placement`). No diagrams. No video/motion compositing. No
change to `CloudflareImageProvider` or `GeminiImageProvider`, confirmed by
their full pre-existing test suites passing unmodified. No per-project
configuration of `_DEFAULT_TI_PLACEMENT` -- a caller wanting a different
default placement supplies an explicit `TiPlacement` per beat via
`TiStateSource`, there is no renderer-wide override yet. No automatic
`STANDALONE`-to-`COMPOSITE` upgrade or vice versa -- the mode is exactly
what the caller's `VisualRendererInput.ti_state_sources` says, every time,
for every rerun.

## Phase 24 — Beat-to-Beat Visual Composition (approved)

Lets a `TI_STATE` `COMPOSITE` beat use the *rendered output of another
`VisualBeat` in the same `VisualPlan`* as its background, removing the
requirement that every composite background already exist as a file the
caller manually supplies via `background_path`. This is explicit,
deterministic dependency resolution the renderer computes from the plan's
own beats -- never AI reasoning, never computer-vision/saliency
placement, never inferred from authored list position. Cloudflare/Gemini
remain generic scene/background providers only (unchanged, confirmed by
their full pre-existing test suites passing unmodified); canonical Tí
remains deterministic; `TiCompositor` itself is completely unchanged; no
diagram-specific renderer, no arbitrary N-layer composition, no text
overlays, no motion/video, no LLM dependency inference.

### Contract: TiStateSource gains a second, mutually exclusive background source

```python
class TiStateSource(MotilyModel):
    mode: TiStateRenderMode = TiStateRenderMode.STANDALONE
    background_path: Path | None = None
    background_beat_id: str | None = None   # new, Phase 24
    placement: TiPlacement | None = None

    # model_validator, replacing Phase 23's STANDALONE-only check:
    #   STANDALONE: both background_path and background_beat_id must be None
    #   COMPOSITE:  exactly one of background_path/background_beat_id must
    #               be set -- both set, or neither set, is a ValueError
    #               (a pydantic ValidationError at construction time)
```

This tightens Phase 23's contract rather than replacing it:
`background_path` alone still behaves exactly as before (PATH mode).
`background_beat_id` alone is the new BEAT_OUTPUT mode. Unlike Phase 23 --
where a `COMPOSITE` `TiStateSource` with no `background_path` only failed
later, at render time (`TiStateMissingBackgroundError`) -- Phase 24's
"neither supplied" case now fails immediately at `TiStateSource`
construction, since there are now two possible sources to check instead
of one. `TiStateMissingBackgroundError` is retired (removed from
`app/renderers/visual/errors.py`) as unreachable dead code; the two
Phase 23 tests that asserted it were updated in place to assert the
`ValidationError` instead. No new enum was introduced for "PATH vs.
BEAT_OUTPUT" -- which field is non-`None` already says which source a
`TiStateSource` uses, and adding a third field just to name that would be
over-modeling a fact the two optional fields already encode.

### A deterministic beat dependency graph, built before anything renders

`app/renderers/visual/renderer.py` adds two small, pure module-level
functions, called once at the top of `_render_all`, before the render
loop, before any provider call, before the `ModuleRun` is marked
`RUNNING`'s work begins, and before any file is written:

```python
def _build_background_dependency_edges(
    visual_plan: VisualPlan, renderer_input: VisualRendererInput
) -> dict[str, set[str]]:
    """dependency beat_id -> set of dependent beat_ids, derived ONLY from
    TiStateSource.background_beat_id. Raises SelfReferentialBackgroundBeatError
    or UnknownBackgroundBeatError immediately on an invalid reference."""

def _topological_beat_order(
    beats: list[VisualBeat], edges: dict[str, set[str]]
) -> list[str]:
    """Kahn's algorithm: among all currently-render-ready beats (in-degree
    zero), always picks the one with the smallest authored index next.
    Raises BackgroundBeatCycleError if no beat is ever ready while beats
    remain."""
```

`_build_background_dependency_edges` walks every `TI_STATE` beat's
`TiStateSource` exactly once: a `background_beat_id` naming the beat
itself is a self-reference (`SelfReferentialBackgroundBeatError`); one
naming a `beat_id` absent from `visual_plan.beats` is unknown
(`UnknownBackgroundBeatError`); no fuzzy matching, no "nearest previous
beat" heuristic, no list-position assumption -- an edge is created only
for an exact, existing `beat_id`.

`_topological_beat_order` is the smallest deterministic dependency
planner that satisfies three requirements simultaneously: (1) a
dependency always precedes its dependent; (2) the authored
`VisualPlan.beats` order is preserved *exactly* wherever the dependency
graph does not force a beat later -- achieved by always breaking ties, at
every step, toward whichever ready beat has the smallest authored index,
rather than an arbitrary or insertion-order tiebreak; (3) a cycle -- a
direct 2-beat mutual reference or a longer chain -- is detected and
raised (`BackgroundBeatCycleError`) before a single beat renders, not
discovered mid-render. This is plain Kahn's algorithm with one specific
tiebreak rule, not a general workflow/orchestrator framework: no retries,
no parallelism, no external scheduling, nothing beyond ordering a fixed,
already-known list of beat ids.

### Resolution: reuse the same render pass's own result, never regenerate

`_render_ti_state_beat` gains a `rendered_assets_by_beat_id: dict[str,
RenderedVisualAsset]` parameter, populated by `_render_all` as each beat
in topological order actually renders (`GENERATED_STILL`/`DIAGRAM` via a
`VisualProvider`, or `TI_STATE` `COMPOSITE` via `TiCompositor` -- the only
media-type outcomes that ever produce a `RenderedVisualAsset`). A new
`_resolve_composite_background_path` helper picks the background path:

```python
def _resolve_composite_background_path(self, beat, source, rendered_assets_by_beat_id) -> Path:
    if source.background_path is not None:
        return source.background_path                      # Phase 23, unchanged

    background_asset = rendered_assets_by_beat_id.get(source.background_beat_id)
    if background_asset is None:
        raise BackgroundBeatNotRenderedError(...)           # requirement, not an asset
    resolved_path = self._visual_store.root / background_asset.file_path
    if not resolved_path.is_file():
        raise BackgroundBeatAssetMissingError(...)          # asset recorded, file missing
    return resolved_path
```

Because `_topological_beat_order` guarantees a `background_beat_id`'s
beat renders before its dependent, `rendered_assets_by_beat_id` already
holds the entry whenever the reference is valid and that beat actually
produced an asset. The resolved path is then handed to
`TiCompositeRequest.background_path` exactly as an explicit
`background_path` always was -- `_composite_ti_state_asset` itself does
not know or care which of the two sources produced the path it received.
Nothing is copied: no temp file, no re-encoded duplicate, no mutation of
the source `RenderedVisualAsset` or its bytes on disk. A
`background_beat_id` naming a beat that rendered to a *requirement*
instead of an asset (`REUSE_ONLY`, `EXTERNAL_REQUIRED`, or a standalone
`CANONICAL_ASSET_READY` `TI_STATE` beat) raises
`BackgroundBeatNotRenderedError`; one whose `RenderedVisualAsset` exists
but whose file is missing on disk raises `BackgroundBeatAssetMissingError`.
Neither ever falls back to `STANDALONE`, generates a replacement
background, or calls a `VisualProvider` -- matching every other Phase 19/
23 TI_STATE failure's "fail explicitly, never substitute" policy exactly.

A `TI_STATE` `COMPOSITE` beat may itself serve as another `TI_STATE`
`COMPOSITE` beat's `background_beat_id`, since it produces a real
`RenderedVisualAsset` (a composited PNG) just like a `GENERATED_STILL`/
`DIAGRAM` beat does -- acyclic chains of any length are allowed; a cycle
among them is caught by `_topological_beat_order` before either renders.

### Zero extra provider calls

A `GENERATED_STILL`/`DIAGRAM` beat used as a background still makes
exactly one `VisualProvider` call, precisely as it would if no beat
depended on it. The dependent `TI_STATE` `COMPOSITE` beat consuming that
output makes zero -- `TI_STATE` is still structurally absent from every
provider-routing set in `app/renderers/visual/renderer.py`, so there is
no code path by which resolving a `background_beat_id` could ever reach
`VisualProvider.render()`.

### Manifest: both assets remain first-class, at their existing deterministic paths

No manifest schema change. A background beat's own `RenderedVisualAsset`
(e.g. `V1_R1.jpg`) and the dependent `TI_STATE` beat's composited
`RenderedVisualAsset` (e.g. `V2_R1.png`) both land in
`manifest.assets`, each still at its existing
`{project_id}/{visual_plan_id}/{render_job_id}.<ext>` path -- nothing is
replaced, hidden, or merged into a single entry.

### Freshness / lifecycle: fully preserved

`ModuleRun` RUNNING -> SUCCESS/FAILED lifecycle, the `StaleVoicePlanError`/
`StaleVisualPlanError` freshness gates, manifest persistence/upsert
semantics, and provider retry ownership are all unchanged in position and
behavior. Dependency-graph construction and topological ordering happen
inside `_render_all`, after the `ModuleRun` row is already saved as
`RUNNING` -- so an unknown-beat/self-reference/cycle failure is caught by
the same broad `except Exception` block every other renderer failure
already goes through, recorded as `FAILED`, and re-raised unchanged; no
special-casing needed. Identical plans, canonical assets, provider
responses, source files, and renderer input still produce identical
dependency ordering and identical composited output bytes on every rerun.

### Errors

- `UnknownBackgroundBeatError` (new) -- `background_beat_id` names no beat
  in the current `VisualPlan`.
- `SelfReferentialBackgroundBeatError` (new) -- a beat names itself as its
  own `background_beat_id`.
- `BackgroundBeatCycleError` (new) -- `background_beat_id` references form
  a cycle, direct or indirect.
- `BackgroundBeatNotRenderedError` (new) -- the referenced beat resolved to
  a `VisualRenderRequirement`, not a `RenderedVisualAsset`, in this render
  pass.
- `BackgroundBeatAssetMissingError` (new) -- the referenced
  `RenderedVisualAsset` exists, but its `file_path` does not resolve to a
  real file on disk.
- `TiStateMissingBackgroundError` (Phase 23) -- **removed**, superseded by
  `TiStateSource`'s own construction-time validation of the "neither
  source supplied" case.
- `TiCompositorNotConfiguredError`, `InvalidVisualBeatError`,
  `TiAssetSetNotFoundError`, `TiAssetMissingFileError` (Phase 19/21/23,
  unchanged) -- unaffected by this phase.

### `scripts/evaluate_visual_renderer_beat_composition.py`

Drives the real `VisualRenderer.run()` path end to end against one
minimal, disposable evaluation project (its own dedicated SQLite file
under `data/visual_renderer_beat_composition_evaluation/`, recreated
fresh every run -- never `data/motily.db`) with a two-beat `VisualPlan`:

1. `V2` -- `TI_STATE`, `COMPOSITE`, `background_beat_id="V1"` -- authored
   *first* in the plan's beat list.
2. `V1` -- `GENERATED_STILL`, rendered via `FakeVisualProvider` with a
   real, Pillow-decodable fixture image (never a live API call) --
   authored *second*.

Authoring `V2` before `V1` is deliberate: it proves the renderer's
dependency ordering is real (`V1` actually renders first, despite its
later list position) rather than an accident of iterating
`visual_plan.beats` in file order. The real active `v1` `TiAssetSet` (from
`data/motily.db`) is used, its metadata copied into the disposable
evaluation database exactly as Phase 23's script does. The script asserts
and prints: `V1` renders before `V2`; `V2` resolves `V1`'s output
automatically with no `background_path` manually supplied; `V2` produces
a real composited PNG; `provider_call_count == 1` for the whole run (only
`V1`'s single call; zero extra for `V2`); and both `V1`'s and `V2`'s
`RenderedVisualAsset`s appear in the final manifest. Output lands in
`data/visual_renderer_beat_composition_evaluation/`.

### What Phase 24 deliberately does not do

No diagram-specific deterministic renderer. No arbitrary N-layer
composition (a `TI_STATE` beat still composites exactly one canonical Tí
asset onto exactly one background, sourced from exactly one of
`background_path`/`background_beat_id`). No text overlays. No motion/
video. No automatic/computer-vision/saliency-based visual placement
(unchanged from Phase 22/23 -- only the fixed 6-anchor vocabulary). No LLM
dependency inference -- every dependency edge comes from an explicit,
caller-authored `background_beat_id`, never guessed from concept text,
`narrative_node`, or any other beat field. No change to
`CloudflareImageProvider` or `GeminiImageProvider`. No redesign of
`VisualPlan` or `VisualRenderManifest`.

## Phase 25 — Deterministic Diagram Renderer (approved)

Replaces `DIAGRAM`'s provider-based handling with a new, fully local,
deterministic `DiagramRenderer` -- `DIAGRAM` beats no longer depend on AI
image generation at all, and can now produce precise physics/mechanism
visuals a generic AI image model cannot reliably draw (exact angles,
exact arrow directions, exact labeled positions). This is structural
rendering, not AI reasoning: a `DiagramSpec` declares exactly what to
draw and exactly where, and `DiagramRenderer` draws exactly that --
nothing here infers layout, content, or placement. Canonical Tí
(Phase 21-23) and AI scene/background generation (Cloudflare/Gemini)
remain completely separate and unchanged, confirmed by their full
pre-existing test suites (including the "no cloudflare/gemini reference in
renderer.py" source-purity checks) passing unmodified.

### Target shape

```
VisualBeat (DIAGRAM)
    -> deterministic diagram spec (DiagramSpec)
    -> DiagramRenderer
    -> RenderedVisualAsset (.png)
```

All non-`DIAGRAM` media types are unaffected -- `GENERATED_STILL` still
calls a `VisualProvider`; `ASSET_REUSE`/`TI_STATE`/`LIMITED_MOTION`/
`EVIDENCE_MEDIA`/`AI_HERO_VIDEO` route exactly as before Phase 25. No
provider call is ever made for a `DIAGRAM` beat.

### Minimum typed contract: `app/models/diagram.py`

Pure pydantic, zero Pillow dependency -- lives in `app/models/` (not the
Pillow-based `app/diagram_renderer/` package) for the same reason
`app/models/ti_assets.py`'s `TiState` does: `VisualBeat.diagram_spec`
must reference it without `VisualBeat` (or anything importing it)
depending on a rendering-layer package.

```python
class DiagramElementType(str, Enum):
    LINE = "LINE"
    ARROW = "ARROW"
    RECTANGLE = "RECTANGLE"
    ELLIPSE = "ELLIPSE"
    POLYLINE = "POLYLINE"
    TEXT_LABEL = "TEXT_LABEL"
    ARC = "ARC"
    DOT = "DOT"

class DiagramPoint(MotilyModel):
    x: float  # normalized [0.0, 1.0]
    y: float  # normalized [0.0, 1.0]

class DiagramStyle(MotilyModel):
    stroke_color: str = "#000000"
    stroke_width: int = 2
    fill_color: str | None = None
    line_style: LineStyle = LineStyle.SOLID   # SOLID | DASHED
    font_size: int = 16
    text_color: str = "#000000"
    arrowhead_size: int = 10

# One concrete model per DiagramElementType member, each with its own
# `type: Literal[DiagramElementType.X]` discriminator field:
#   DiagramLine(start, end, style)               -- start != end enforced
#   DiagramArrow(start, end, style)               -- start != end enforced
#   DiagramRectangle(top_left, bottom_right, style)  -- positive area enforced
#   DiagramEllipse(center, radius_x, radius_y, style)
#   DiagramPolyline(points: list[.., min_length=2], closed, style)
#   DiagramTextLabel(position, text, align, style)   -- non-blank text enforced
#   DiagramArc(center, radius_x, radius_y,
#              start_angle_degrees, end_angle_degrees, style)
#   DiagramDot(center, radius, style)
# every element also carries an optional `element_id: str | None`.

DiagramElement = Annotated[
    Union[DiagramLine, DiagramArrow, DiagramRectangle, DiagramEllipse,
          DiagramPolyline, DiagramTextLabel, DiagramArc, DiagramDot],
    Field(discriminator="type"),
]

class DiagramCanvas(MotilyModel):
    width: int
    height: int
    background_color: str | None = "#FFFFFF"  # None -> transparent PNG

class DiagramSpec(MotilyModel):
    canvas: DiagramCanvas
    elements: list[DiagramElement] = Field(min_length=1)
```

`DiagramElement`'s `type`-discriminated union is pydantic's own
enforcement of "unsupported element subtype" (Phase 25 requirement) --
any `type` value outside `DiagramElementType`'s 8 members fails
validation immediately, with no manual dispatch needed at parse time.
`DOT` was included (Phase 25's "optionally" suggestion) because it
materially simplifies a point-mass/pivot marker without authoring a full
`ELLIPSE`. No `DiagramValidationError`-style wrapper exception exists for
any of this -- every structural rule above is a plain pydantic
`model_validator` raising `ValueError`, exactly how `TiCompositeRequest`/
`TiPlacement`/`VisualBeat` already validate themselves; adding a
dedicated exception class here would only wrap the same
`pydantic.ValidationError` a caller already has to handle.

### `VisualBeat.diagram_spec`: no new cross-field model validator

```python
class VisualBeat(MotilyModel):
    ...
    diagram_spec: DiagramSpec | None = None
```

Deliberately NOT enforced via a `VisualBeat`-level `model_validator`
conditioned on `media_type` (unlike, say, `RenderedVisualAsset.
resolved_ti_state`'s existing media_type-gated validator). `VisualBeat`
already has other per-media-type fields with no cross-field validator at
all -- `ti_state`, `reuse_key`, `motion_intent`, `evidence_source_ids` --
each required-or-forbidden only where it is actually consumed
(`app/renderers/visual/renderer.py`'s TI_STATE/ASSET_REUSE/DIAGRAM
routing), not exhaustively in the shared model. `diagram_spec` follows
that same established precedent instead of introducing a new one: see
"Structural validation" below for where the check actually lives.

### Coordinate system

Every `DiagramPoint.x`/`.y` is a normalized float in `[0.0, 1.0]`,
resolution-independent -- validated strictly by `DiagramPoint`'s own
`model_validator` (outside that range is a hard `ValueError`, not clamped
or silently accepted). `ELLIPSE`/`ARC` radii and `DOT`'s radius are
likewise normalized fractions of the relevant canvas dimension, bounded
to `(0.0, 1.0]` (exclusive zero -- a zero-size shape has no rendering
meaning). `DiagramCanvas.width`/`height` are the only pixel-space values
in the entire contract; `DiagramRenderer` is the sole place a normalized
coordinate is ever converted to a pixel position
(`round(value * canvas.width_or_height)`), so identical normalized input
against a different `DiagramCanvas` size scales predictably with zero
hidden auto-layout.

### Rendering engine: `app/diagram_renderer/`

A new package, structurally mirroring `app/ti_compositor/`'s
architecture-boundary role: depends only on the `DiagramSpec` it is
given, never on `VisualProvider`, a concrete provider, or
`app/renderers/visual/renderer.py` (which consumes it the other way,
exactly like it consumes `TiCompositor`).

```python
class DiagramRenderer:
    def render(self, spec: DiagramSpec, output_path: Path) -> DiagramRenderResult: ...
```

Implemented with `PIL.Image`/`PIL.ImageDraw` -- already a hard project
dependency since Phase 22 (`Pillow>=11.0,<13.0`, `pyproject.toml`), so
Phase 25 adds no new dependency. No heavyweight graphics stack, no SVG
engine, no charting library: every element type dispatches to a small,
direct Pillow call (`draw.line`, `draw.polygon` for arrowheads,
`draw.rectangle`, `draw.ellipse`, `draw.arc`, `draw.text`). `LineStyle.
DASHED` is implemented by hand (Pillow has no native dashed-line primitive)
as a sequence of short solid segments along the line at a fixed 10px-on/
6px-off pixel cadence -- deterministic, not randomized or content-aware.

Unlike `TiCompositor`, `DiagramRenderer` takes **no constructor
argument** -- there is no external state to inject (no database, no
file-backed asset set, no "unconfigured" failure mode), so
`VisualRenderer` instantiates a default one itself
(`diagram_renderer: DiagramRenderer | None = None`, defaulting internally
when omitted) rather than requiring every caller to construct and pass
one, the way `ti_compositor=None` requires for `TiCompositor`.

#### Background/output policy (documented explicitly, per requirement #5)

- Output format: **PNG only**, always -- `DiagramRenderResult`/
  `DiagramRenderer.render()` never produce any other container, matching
  `TiCompositor`'s Phase 22 PNG-only policy for the identical
  "deterministic lossless raster output" reason.
- Background: `DiagramCanvas.background_color` decides, with exactly two
  outcomes and no third option:
  - a hex string (default `"#FFFFFF"`, opaque white) -> the canvas is
    created as Pillow mode `"RGB"`, filled with that color before any
    element is drawn.
  - `None` -> the canvas is created as Pillow mode `"RGBA"`, every pixel
    starting fully transparent (alpha `0`); each drawn element keeps its
    own opacity on top of that.

#### Font (documented explicitly, per requirement #6)

Every `TEXT_LABEL` uses Pillow's own bundled bitmap default font via
`PIL.ImageFont.load_default(size=element.style.font_size)` -- the sized
variant, available since Pillow 10.1 (confirmed present on this project's
pinned `Pillow>=11.0`). No external font file is ever downloaded,
bundled, or read from the host system: fully offline, licensing-free, at
the cost of a fixed, plain typeface with no bold/italic variant.
Horizontal alignment (`TextAlign.LEFT`/`CENTER`/`RIGHT`) maps directly to
Pillow's own `anchor` parameter (`"la"`/`"ma"`/`"ra"`) -- no manual text-
width measurement needed.

### Minimum styling support

`DiagramStyle`: `stroke_color`, `stroke_width` (int, pixels, `> 0`),
`fill_color` (optional), `line_style` (`SOLID`/`DASHED`), `font_size`
(int, pixels, `> 0`), `text_color`, `arrowhead_size` (int, pixels, `> 0`).
No gradients, no textures, no external font downloads, no per-element
font family. Colors are plain strings (a `"#RRGGBB"` hex value or any
Pillow-recognized name) rather than a dedicated color model, since Pillow
itself accepts either directly.

### Validation rules

All structural/rendering validation only -- never "is the physics
correct?" semantic validation:

| Rule | Enforced by |
|---|---|
| DIAGRAM beat missing diagram_spec | `_validate_diagram_specs` (renderer) -> `MissingDiagramSpecError` |
| non-DIAGRAM beat carrying diagram_spec | `_validate_diagram_specs` (renderer) -> `UnexpectedDiagramSpecError` |
| empty element list | `DiagramSpec` (`Field(min_length=1)`) |
| invalid canvas dimensions | `DiagramCanvas` model_validator (`width`/`height > 0`) |
| coordinates outside bounds | `DiagramPoint` model_validator (`[0.0, 1.0]`) |
| invalid/blank text labels | `DiagramTextLabel` model_validator (`non_blank`) |
| zero-length arrow/line | `DiagramLine`/`DiagramArrow` model_validator |
| arc angle validity | `DiagramArc` model_validator (nonzero sweep, `<= 360` degrees) |
| unsupported element subtype | `DiagramElement`'s discriminated union itself |
| duplicate element ids | `DiagramSpec` model_validator |

The first two rows are the only ones enforced outside
`app/models/diagram.py` -- see "`VisualBeat.diagram_spec`" above for why
they live in the renderer instead of a `VisualBeat` model validator.

### `VisualRenderer` integration

`DIAGRAM` is removed from `_PROVIDER_RENDERABLE_MEDIA_TYPES` (renamed
from Phase 19's `_RENDERABLE_MEDIA_TYPES`, which held `{GENERATED_STILL,
DIAGRAM}`) -- it now has its own dedicated branch in `_render_all`,
exactly parallel to `TI_STATE`'s Phase 23 branch:

```python
elif beat.media_type is VisualMediaType.DIAGRAM:
    diagram_asset = self._render_diagram_beat(project_id, visual_plan.id, beat)
    assets.append(diagram_asset)
    rendered_assets_by_beat_id[beat.beat_id] = diagram_asset
```

`_render_diagram_beat` never calls a `VisualProvider`, is never retried
(`DiagramRenderer` is a deterministic local operation, like
`TiCompositor` -- there is nothing transient to retry), and always
produces `.png` output regardless of `VisualSettings.output_format` (same
"keep `file_path` truthful" reasoning as `TI_STATE` `COMPOSITE`'s
Phase 23 PNG-only output). `_validate_diagram_specs` runs at the top of
`_render_all`, before the dependency graph is even built and before any
beat renders -- an unknown-beat-shape violation fails before any
provider call, `TiCompositor`/`DiagramRenderer` call, or file write.

`assets.append`/`rendered_assets_by_beat_id[beat.beat_id] = diagram_asset`
means a `DIAGRAM` beat is persisted in the manifest exactly like every
other rendered asset, AND becomes usable as a Phase 24
`background_beat_id` source -- no special-casing needed, since Phase 24's
machinery only cares whether a beat produced a real `RenderedVisualAsset`
in the current pass, not which renderer produced it.

A `DiagramRenderError` propagates unchanged out of `_render_diagram_beat`,
through `_render_all`, into `run()`'s existing broad `except Exception`
block -- recorded as a `FAILED` `ModuleRun`, re-raised, never a silent
fallback to a generic `GENERATED_STILL` call or any other substitution.

### Errors (`app/diagram_renderer/errors.py`, `app/renderers/visual/errors.py`)

- `DiagramRenderError` (`app/diagram_renderer/errors.py`) -- a drawing
  dispatch reaching an unhandled element type (unreachable given
  `DiagramElement`'s discriminated union, guarded defensively) or a
  filesystem failure writing the output PNG.
- `MissingDiagramSpecError` / `UnexpectedDiagramSpecError`
  (`app/renderers/visual/errors.py`, new) -- the two `VisualBeat.
  diagram_spec`-vs-`media_type` structural checks (see "Validation rules"
  above).

No broader exception hierarchy was built -- exactly these three new
classes, per the phase's own "narrowest reasonable errors" instruction.

### `scripts/evaluate_diagram_renderer.py`

Mirrors `scripts/evaluate_ti_compositor.py`'s shape: drives the real
`DiagramRenderer` path directly (no `VisualRenderer`, no project graph
needed -- `DiagramRenderer` has no dependency on either). Renders exactly
3 hand-authored diagrams to `data/diagram_renderer_evaluation/`:

1. `FORCE_BLOCK` -- a block on a ground line with three labeled force
   arrows (`Fg`/gravity down, `N`/normal up, `F`/applied right).
2. `SUN_STICK_SHADOW` -- a vertical stick on the ground, a sun (an
   `ELLIPSE` plus several ray `LINE`s) in one corner, a dashed sun-ray
   line to the stick's tip, and the resulting shadow (`LINE`, `DASHED`)
   along the ground.
3. `CIRCLE_ANGLE_RELATION` -- a circle (`ELLIPSE` on a square canvas), two
   radii (`LINE`), an angle-marking `ARC`, and a `theta` `TEXT_LABEL`.

Between the three, every one of the 8 `DiagramElementType` members is
exercised at least once. No live image API call, no AI generation --
every spec is authored inline in the script itself. Each diagram is
rendered twice per run and the two output byte streams compared, so the
script proves determinism inline rather than by convention alone.

### What Phase 25 deliberately does not do

No arbitrary SVG import. No charting library (matplotlib/plotly/d3-style
declarative charts) -- `DiagramElement`'s 8 primitives are hand-authored
shapes, not a data-visualization grammar. No LaTeX/math typesetting --
`TEXT_LABEL` is plain Pillow-rendered text only (`"theta"`, not `θ`, is
this spec's own convention for exactly that reason). No auto layout of
any kind -- every coordinate in a `DiagramSpec` is authored explicitly;
`DiagramRenderer` never infers, adjusts, or collision-resolves a
position. No automatic label collision resolution. No animation, no
motion graphics -- still PNG output only. No diagram + Tí combined
multi-layer authoring -- `DiagramSpec` and `TiCompositeRequest` remain
two entirely separate contracts with no shared element/composition model
between them (a future phase could add that; Phase 25 does not). No text
overlay mechanism outside `TEXT_LABEL` itself -- there is no separate
caption/subtitle layer this phase adds anywhere. No change to
`CloudflareImageProvider`/`GeminiImageProvider`, confirmed by their full
pre-existing test suites, including the renderer-source purity checks
(`"cloudflare" not in renderer.py`, `"gemini" not in renderer.py`),
passing unmodified.

## Phase 26 — Deterministic Visual Layer Composition (approved)

Introduces a small deterministic layer-composition system, sitting ABOVE
the three existing visual-generation systems (`GENERATED_STILL`'s
`VisualProvider`, `TI_STATE`'s `TiCompositor`, `DIAGRAM`'s
`DiagramRenderer`) without modifying, replacing, or depending on any of
them. This phase creates no visual content of its own -- it only composes
content those systems already produced:

```
source assets
    |
    v
LayerCompositionSpec
    |
    v
VisualLayerCompositor
    |
    v
final PNG frame
```

### Architecture boundary: `app/layer_compositor/`

A new, standalone package, structurally identical in role to
`app/ti_compositor/` (Phase 22) and `app/diagram_renderer/` (Phase 25):
zero dependency on `VisualProvider`, `TiCompositor`, `DiagramRenderer`, a
database, a `VisualRenderManifest`, or `app/renderers/visual/renderer.py`
-- `app/renderers/visual/renderer.py` depends on it, never the reverse.
`VisualLayerCompositor` is NOT wired into `VisualRenderer`'s existing
rendering paths; it is reached only through a new, additive `COMPOSITION`
media type and its own dedicated routing branch (see below) -- no
existing rendering behavior was replaced.

### MVP layer types: BACKGROUND / OVERLAY only

```python
class LayerSourceType(str, Enum):
    BACKGROUND = "BACKGROUND"
    OVERLAY = "OVERLAY"
```

No semantic "Tí layer" or "diagram layer" concept exists anywhere in this
package -- every layer is a plain raster image regardless of what
produced it; `app/renderers/visual/renderer.py` is the only place that
knows a given file happened to come from a `TI_STATE` or `DIAGRAM` beat.

### Layer source contract: concrete paths only

```python
class BackgroundLayer(MotilyModel):
    source_type: Literal[LayerSourceType.BACKGROUND] = LayerSourceType.BACKGROUND
    id: str
    source_path: Path       # already resolved -- no beat_id, no manifest lookup
    notes: str | None = None
    # no anchor / scale / z_index / margin / offset fields at all

class OverlayLayer(MotilyModel):
    source_type: Literal[LayerSourceType.OVERLAY] = LayerSourceType.OVERLAY
    id: str
    source_path: Path       # already resolved
    z_index: int = 0
    anchor: LayerAnchor = LayerAnchor.CENTER
    scale: LayerScale = Field(default_factory=LayerScale)
    margin: int = 0
    offset_x: int = 0
    offset_y: int = 0
    notes: str | None = None

VisualLayer = Annotated[Union[BackgroundLayer, OverlayLayer], Field(discriminator="source_type")]
```

`VisualLayerCompositor.compose()` never queries a database, a
`VisualRenderManifest`, a `VisualPlan`, or a provider -- every
`source_path` must already be a concrete, existing file by the time it
reaches this package. Turning a beat_id reference into that concrete path
is the caller's job (`app/renderers/visual/renderer.py`'s
`_resolve_composition_layer_path`, see below) -- exactly the same
division of labor Phase 24 already established for
`TiStateSource.background_beat_id` vs. `TiCompositeRequest.
background_path`.

### Canvas policy

Exactly one `BackgroundLayer` is required per `LayerCompositionSpec` --
zero or multiple is a `pydantic.ValidationError` (`LayerCompositionSpec`'s
own `model_validator`). The background image's own pixel dimensions
become `LayerCompositionResult.canvas_width`/`canvas_height` -- no
auto-sizing from overlays, ever. Output is PNG-only
(`LayerCompositionSpec.output_path` must end in `.png`, validated the
same way).

### Placement: a 9-position anchor vocabulary

```python
class LayerAnchor(str, Enum):
    TOP_LEFT = "TOP_LEFT"; TOP_CENTER = "TOP_CENTER"; TOP_RIGHT = "TOP_RIGHT"
    CENTER_LEFT = "CENTER_LEFT"; CENTER = "CENTER"; CENTER_RIGHT = "CENTER_RIGHT"
    BOTTOM_LEFT = "BOTTOM_LEFT"; BOTTOM_CENTER = "BOTTOM_CENTER"; BOTTOM_RIGHT = "BOTTOM_RIGHT"
```

A superset of `app/ti_compositor/models.py`'s 6-member `TiAnchor` (adds
the three `TOP_*` members) -- deliberately a separate enum, since this
package has no dependency on `app/ti_compositor/` and the two vocabularies
are free to diverge. `margin` (inward padding on edge-anchored axes) and
`offset_x`/`offset_y` (an unrestricted post-anchor pixel nudge) carry the
exact same semantics `TiPlacement` already established in Phase 22.

### Scaling: `LayerScale`

```python
class LayerScale(MotilyModel):
    relative_height: float | None = None   # 0 < value <= 1
    relative_width: float | None = None    # 0 < value <= 1
    # model_validator: at most one may be set; both None -> native size
```

Aspect ratio is always preserved -- `compute_scaled_size()` derives the
missing dimension from the overlay's own native aspect ratio, exactly
mirroring `app/ti_compositor/compositor.py`'s Phase 22
`compute_scaled_size()`, generalized to support scaling by width as an
alternative to height.

### Layer order / z-order: one policy, documented

Overlays draw in ascending `z_index` order; **ties are broken by authored
`LayerCompositionSpec.layers` list order** (Python's own stable
`sorted()` preserves this for free -- no duplicate-`z_index` rejection,
per the phase's "pick one policy" instruction). The `BackgroundLayer`
always draws first, beneath every overlay -- not by convention but
structurally, since `BackgroundLayer` has no `z_index` field to even
compete with one. No z-order is ever inferred from `LayerSourceType` or
any other semantic field.

### Transparency / bounds

Background is loaded and `.convert("RGB")`-forced (any alpha a source PNG
happens to carry is dropped -- it is the final opaque canvas, exactly
`TiCompositor`'s Phase 22 policy). Every overlay is `.convert("RGBA")`-
forced before compositing, so an opaque JPEG overlay's implicit full
alpha covers the layers beneath it completely, and a genuinely
transparent PNG overlay's alpha correctly reveals them -- one alpha-
compositing code path (`Image.paste(overlay, position, overlay)`) handles
both. No blend modes beyond normal alpha compositing.

Bounds policy is identical to `TiCompositor`'s Phase 22 policy: an
in-frame-capable requested position is clamped to stay fully on-canvas
(`LayerGeometry.clamped` records whether that happened); a scaled overlay
whose width OR height outright exceeds the canvas raises
`LayerCompositionBoundsError` -- no position, clamped or not, could ever
fit it, so failing loudly is the only option that isn't a silent crop.

### Contracts: final shape

`LayerSourceType`, `LayerAnchor`, `LayerScale`, `BackgroundLayer`/
`OverlayLayer` (as `VisualLayer`), `LayerCompositionSpec` (`layers`,
`output_path`), `LayerGeometry` (`layer_id`, `x`, `y`, `width`, `height`,
`clamped`), `LayerCompositionResult` (`output_path`, `canvas_width`,
`canvas_height`, `layers: list[LayerGeometry]`, plus a
`clamped_layer_ids` convenience property). Names match the phase's own
suggested shape exactly, with `VisualLayer` implemented as a
`source_type`-discriminated union rather than one flexible model with
runtime "must be default for BACKGROUND" checks -- pydantic itself
enforces that shape instead.

### Validation

Structural/rendering validation only, no visual-quality scoring:

| Rule | Enforced by |
|---|---|
| zero backgrounds / multiple backgrounds | `LayerCompositionSpec` model_validator |
| blank layer id | `BackgroundLayer`/`OverlayLayer` model_validator (`non_blank`) |
| duplicate layer id | `LayerCompositionSpec` model_validator |
| invalid scale (out of bounds, both width/height) | `LayerScale` model_validator |
| invalid output extension | `LayerCompositionSpec` model_validator |
| missing source file | `VisualLayerCompositor._load_image` -> `LayerCompositionBackgroundError`/`LayerCompositionOverlayError` |
| unsupported raster format | same (suffix check: `.png`/`.jpg`/`.jpeg` only) |
| corrupt input image | same (Pillow decode failure caught) |
| oversized overlay | `clamp_position()` -> `LayerCompositionBoundsError` |
| write failure | `_write_output` -> `LayerCompositionWriteError` |

### Rendering backend

Pillow only -- already an established dependency (Phase 22); no new
graphics dependency added. Pipeline exactly as specified: validate spec
(pydantic, at construction) -> load background -> canvas = background
size -> stable-sort overlays by z_index -> decode each overlay -> scale
preserving aspect -> compute anchor position -> clamp or fail -> alpha
composite -> write PNG -> return exact applied geometry
(`LayerCompositionResult`). Identical inputs produce byte-identical
output under this project's pinned Pillow version, verified by a
render-twice-and-compare-bytes test.

### `VisualRenderer` integration

The smallest contract change that supports the target four-beat pattern
turned out to be exactly one new enum member,
`VisualMediaType.COMPOSITION` -- not a broad `VisualPlan` redesign.
`VisualBeat`/`VisualPlan` themselves are otherwise completely unchanged;
`COMPOSITION` is deliberately a rendering-layer-only concept, not exposed
in `app/engines/visual_plan/prompt.py`'s LLM-facing media-type
descriptions (unchanged this phase -- confirmed by
`tests/test_visual_plan_prompt.py`'s existing "all seven media types
present" test, which asserts presence, not an exact/exhaustive set, so it
needed no update).

`app/renderers/visual/models.py` gains the renderer-input, pre-resolution
counterpart to `VisualLayer`:

```python
class BackgroundLayerSource(MotilyModel):
    source_type: Literal[LayerSourceType.BACKGROUND] = LayerSourceType.BACKGROUND
    id: str
    source_path: Path | None = None
    source_beat_id: str | None = None   # exactly one of the two required
    notes: str | None = None

class OverlayLayerSource(MotilyModel):
    source_type: Literal[LayerSourceType.OVERLAY] = LayerSourceType.OVERLAY
    id: str
    source_path: Path | None = None
    source_beat_id: str | None = None   # exactly one of the two required
    z_index: int = 0
    anchor: LayerAnchor = LayerAnchor.CENTER
    scale: LayerScale = Field(default_factory=LayerScale)
    margin: int = 0
    offset_x: int = 0
    offset_y: int = 0
    notes: str | None = None

class CompositionSpec(MotilyModel):
    layers: list[CompositionLayerSource] = Field(min_length=1)
    # model_validator: exactly one BACKGROUND layer; no duplicate ids

class VisualRendererInput(MotilyModel):
    ...
    composition_specs: dict[str, CompositionSpec] = Field(default_factory=dict)
```

Unlike `ti_state_sources`, there is no default: a `COMPOSITION` beat with
no `composition_specs` entry is a hard failure
(`MissingCompositionSpecError`), checked up front by
`_validate_composition_specs`, mirroring Phase 25's
`_validate_diagram_specs` timing.

### Beat dependency integration: Phase 24's engine, generalized

Per this phase's explicit "do not build a second dependency engine"
instruction, `_build_background_dependency_edges` is generalized rather
than duplicated:

```python
def _iter_beat_to_beat_references(visual_plan, renderer_input):
    for beat in visual_plan.beats:
        if beat.media_type is VisualMediaType.TI_STATE:
            source = renderer_input.ti_state_sources.get(beat.beat_id)
            if source and source.mode is COMPOSITE and source.background_beat_id:
                yield beat.beat_id, source.background_beat_id
        elif beat.media_type is VisualMediaType.COMPOSITION:
            composition_spec = renderer_input.composition_specs.get(beat.beat_id)
            if composition_spec:
                for layer_source in composition_spec.layers:
                    if layer_source.source_beat_id is not None:
                        yield beat.beat_id, layer_source.source_beat_id
```

`_build_background_dependency_edges` and `_topological_beat_order`
themselves are otherwise textually unchanged from Phase 24 -- they
consume `_iter_beat_to_beat_references`'s output generically, with no
idea which media type produced which reference. `UnknownBackgroundBeatError`/
`SelfReferentialBackgroundBeatError`/`BackgroundBeatCycleError` are reused
as-is (docstrings broadened to describe both use cases) rather than
introducing parallel `COMPOSITION`-specific names for the identical
graph-validation logic -- a deliberate reuse decision, not an oversight
(see "Unresolved decisions" in the phase report). A referenced beat may
be authored anywhere in the `VisualPlan`; the topological sort (unchanged
algorithm) still renders it first and preserves authored order everywhere
else.

### Resolution: broader than TI_STATE's own background rule, on purpose

`_resolve_composition_layer_path` is NOT a thin wrapper around
`_resolve_composite_background_path` (TI_STATE's Phase 24 resolver) --
it is deliberately broader, because the phase's own target four-beat
pattern requires it:

```
V3 TI_STATE STANDALONE -> canonical Tí asset (a CANONICAL_ASSET_READY
                            requirement, NOT a RenderedVisualAsset)
V4 composition target: ... overlay = V3
```

Resolution order for a `source_beat_id`:
1. `rendered_assets_by_beat_id` (a real `RenderedVisualAsset` from this
   pass -- `GENERATED_STILL`, `DIAGRAM`, `TI_STATE` `COMPOSITE`, or
   another `COMPOSITION`) -> resolved via `VisualFileStore.root /
   file_path`, existence-checked, `BackgroundBeatAssetMissingError` if
   the file is gone (reused from Phase 24 -- the same failure mode, same
   meaning, regardless of which beat type is asking).
2. Else `requirements_by_beat_id` (a NEW render-pass-wide tracking dict,
   populated for every requirement any beat produces, not just
   `TI_STATE`'s): if it is `CANONICAL_ASSET_READY`, resolved via
   `self._ti_compositor.retriever.get_asset(requirement.resolved_ti_state)`
   + `.resolve_path()` -- the exact same retriever the original `TI_STATE`
   beat used, never a second lookup path. `TiAssetRetriever.get_asset()`
   already existence-checks the file itself (Phase 21), so a missing
   canonical file surfaces as the existing `TiAssetMissingFileError`,
   unwrapped.
3. Otherwise -- `REUSE_ONLY`, `EXTERNAL_REQUIRED`, or no requirement/asset
   at all -- `CompositionSourceNotRenderedError`.

This is a deliberate, spec-driven divergence from Phase 24's TI_STATE-
background rule (which still forbids a requirement-based source for
`background_beat_id`), not an inconsistency: the two rules answer
different questions ("can TI_STATE's own background be a requirement?"
vs. "can a COMPOSITION overlay be a requirement?") and Phase 26's own
worked example requires the second to be "yes."

### Provider behavior

No new provider behavior. `GENERATED_STILL`/`DIAGRAM`/`TI_STATE` beats
consumed as composition layers call a provider exactly as they would on
their own; `COMPOSITION` itself contributes zero, always -- it is
structurally absent from `_PROVIDER_RENDERABLE_MEDIA_TYPES` and never
reaches `VisualProvider.render()` by any code path.

### Manifest behavior

`app/renderers/visual/validation.py`'s `_RENDERABLE_MEDIA_TYPES` gains
`COMPOSITION` (alongside `GENERATED_STILL`/`DIAGRAM`) -- "a real rendered
file is a valid `manifest.assets` entry for this media_type," not "goes
through a provider." Every source beat's own asset/requirement remains a
first-class manifest entry alongside the new composed-frame asset; the
composite's `RenderedVisualAsset.notes` records which source beat_ids it
was built from (`"Composited from source beat(s) ['V1', 'V2', 'V3'] via
VisualLayerCompositor; no VisualProvider call."`).

### Errors

- `MissingCompositionSpecError` (new) -- a `COMPOSITION` beat with no
  `composition_specs` entry.
- `CompositionSourceNotRenderedError` (new) -- a layer's `source_beat_id`
  resolved to neither a `RenderedVisualAsset` nor a `CANONICAL_ASSET_READY`
  requirement.
- `UnknownBackgroundBeatError`/`SelfReferentialBackgroundBeatError`/
  `BackgroundBeatCycleError` (Phase 24, reused/generalized) -- graph-level
  failures shared with `TI_STATE`.
- `BackgroundBeatAssetMissingError` (Phase 24, reused) -- a resolved
  `RenderedVisualAsset`'s file is missing on disk.
- `LayerCompositionBackgroundError`/`LayerCompositionOverlayError`/
  `LayerCompositionBoundsError`/`LayerCompositionWriteError`
  (`app/layer_compositor/errors.py`, new) -- propagate unchanged through
  `_render_composition_beat`'s `except LayerCompositionError: raise`.
- `TiAssetMissingFileError` (Phase 21, unchanged) -- propagates unchanged
  when a resolved canonical Tí file is missing.

### `scripts/evaluate_visual_layer_composition.py`

Drives the real `VisualRenderer.run()` path against one disposable
four-beat project, authored in SCRAMBLED order (`V4` `COMPOSITION`
first, `V3` `TI_STATE` `STANDALONE`, `V2` `DIAGRAM`, `V1`
`GENERATED_STILL` last) -- the exact reverse of rendering order,
deliberately, mirroring `scripts/evaluate_visual_renderer_beat_
composition.py`'s (Phase 24) proof pattern. `V1`'s provider output comes
from `FakeVisualProvider` with a real decodable fixture image (never a
live API call); `V2` renders via the real local `DiagramRenderer`; `V3`
resolves the real active `v1` canonical `TiAssetSet` via the real
`TiCompositor`'s retriever, `STANDALONE` mode; `V4`'s `CompositionSpec`
combines all three via the real `VisualLayerCompositor`. Asserts and
prints: `V1`/`V2` render before `V4` despite authored order; total
provider calls = 1; the manifest contains `V1`/`V2`/`V4` as assets and
`V3` as a requirement. Output lands in
`data/visual_layer_composition_evaluation/`.

### What Phase 26 deliberately does not do

No motion/video -- still PNG output only. No arbitrary transforms or
rotation -- only axis-aligned scale-and-place. No Photoshop-style blend
modes -- normal alpha compositing only. No masking beyond a source
image's own alpha channel -- no separate mask layer concept. No arbitrary
SVG. No automatic/computer-vision/saliency-based layout -- the fixed
9-anchor vocabulary only. No semantic collision avoidance between
overlays. No text layout system -- `TEXT_LABEL` (Phase 25's `DiagramSpec`)
remains the only way to put text in a frame; `VisualLayerCompositor`
itself never renders text. No CapCut/editor integration. No change to
`CloudflareImageProvider`/`GeminiImageProvider`, confirmed by their full
pre-existing test suites (including the renderer-source purity checks)
passing unmodified. No change to canonical Tí assets or `TiCompositor`'s
own behavior. No change to `DiagramRenderer`'s own behavior.

## Phase 27 — Deterministic Timeline Assembly (approved)

Turns the existing voice, visual, and assembly-planning artifacts into
one deterministic executable static-video timeline: exactly what visual
frame is shown, when it starts, when it ends, which narration/voice
chunk plays, which audio cue applies, and which static transition/cut
occurs. No video is rendered yet -- no ffmpeg call, no CapCut automation,
no motion/video generation, no visual-provider change, no change to Tí/
diagram/layer-compositor behavior, no LLM timing inference.

### Architecture boundary: `app/renderers/timeline/`

A new package under `app/renderers/` (not `app/engines/` -- this is
execution against already-locked plans, not LLM reasoning against one),
structurally parallel to `app/renderers/visual/` and
`app/renderers/voice/`: `errors.py`, `models.py` (thin
`TimelineBuilderInput`/`Result` wrappers), `validation.py`
(defense-in-depth, pure, I/O-free), `builder.py` (`TimelineBuilder`, the
one stateful class). The business-output schema itself,
`TimelineManifest`, lives in `app/models/timeline.py` -- the same split
`app/models/visual_render.py`/`app/renderers/visual/models.py` already
established. Confirmed, via a fresh-subprocess `sys.modules` check
(mirroring every prior renderer's identical test), zero `app.llm`
dependency, direct or transitive.

### Timebase (requirement #2)

Integer milliseconds, explicitly chosen over two alternatives:
`AssemblyPlan`'s own float seconds (Phase 15) would accumulate rounding
drift across dozens of concatenated segments; frames-per-second belongs
to a future final-encoding phase, which will convert these millisecond
boundaries to frame numbers against whatever FPS it renders at -- not the
reverse. Every `TimelineSegment` has `start_ms`/`end_ms`/`duration_ms`
(`start_ms >= 0`, `end_ms > start_ms`, `duration_ms == end_ms -
start_ms`, all enforced by `TimelineSegment`'s own `model_validator`); the
timeline always starts at 0 (enforced by
`validate_timeline_manifest`'s contiguity check, see below).

### Voice-driven primary timing (requirement #3)

```python
def _resolve_audio_duration_ms(take: RenderedVoiceTake, audio_path: Path) -> int:
    if take.duration_seconds is not None:
        return round(take.duration_seconds * 1000)
    if audio_path.suffix.lower() != ".wav":
        raise UnresolvableAudioDurationError(...)
    return _measure_wav_duration_ms(audio_path)  # frames / framerate, via stdlib `wave`
```

`RenderedVoiceTake.duration_seconds` (Phase 17) is used directly when a
provider already recorded it. When absent, `_measure_wav_duration_ms`
reads ONLY the WAV file's header (`wave.getnframes()`/`getframerate()`,
stdlib, zero new dependency) -- never decodes audio samples, never
estimates from text length. This is sufficient for the entire current
architecture: `GeminiTTSProvider` (Phase 18) is hard-coded to WAV-only
output, so every real rendered voice asset in this project is
measurable this way. A non-WAV file with no recorded duration fails
explicitly (`UnresolvableAudioDurationError`) rather than guessing.

### Script/voice/visual alignment (requirement #4)

Reused entirely from `AssemblyPlan` (Phase 15) -- no new contract field
was needed, and none was added. `app/engines/assembly_plan/validation.py`
already guarantees, before an `AssemblyPlan` is ever persisted:

- `normalize_assembly_plan()` overwrites every segment's `voice_chunk_ids`
  to be exactly the `VoiceChunk`s whose `line_ids` overlap that segment's
  `script_line_ids` -- deterministic, never fuzzy.
- `validate_assembly_plan()`'s `_visual_beat_coverage_issues` guarantees
  exactly one `AssemblySegment` per `VisualBeat`, in `VisualPlan.beats`'
  own order (Phase 15's "one AssemblySegment maps to exactly one
  VisualBeat" hard rule).

`TimelineBuilder` therefore only needs to RESOLVE real timing/assets for
relationships `AssemblyPlan` already established -- it never re-derives
beat/chunk coverage itself (though it does defensively re-check that
every referenced id still exists in the current `VoicePlan`/`VisualPlan`,
raising `UnknownVoiceChunkReferenceError`/`UnknownVisualBeatReferenceError`
if not, since it loads a persisted artifact independently rather than
receiving it fresh off an engine's own validation pass).

"Multiple narration chunks intentionally sharing one visual" (requirement
#6) falls out of this for free: one `AssemblySegment.voice_chunk_ids` may
already list more than one `VoiceChunk` (Phase 13 chunks routinely span
several `ScriptLine`s), so `TimelineBuilder` simply resolves ALL of them
into that one segment's `narration: list[TimelineAudioRef]`, concatenated
in list order, summed into that segment's own `duration_ms`, under the
segment's single `TimelineVisualRef`.

### Static visual selection (requirement #5) -- never triggers rendering

```python
class TimelineVisualSourceStatus(str, Enum):
    RENDERED = "RENDERED"       # a real RenderedVisualAsset
    REQUIREMENT = "REQUIREMENT" # a VisualRenderRequirement (no file yet)

class TimelineVisualRef(MotilyModel):
    visual_beat_id: str
    media_type: VisualMediaType
    status: TimelineVisualSourceStatus
    file_path: str | None = None   # RENDERED only
    reference: str | None = None   # REQUIREMENT only
    resolved_ti_state: TiState | None = None
    width: int | None = None
    height: int | None = None
```

`_resolve_visual_ref` looks up `visual_beat_id` in
`VisualRenderManifest.assets` first (any of `GENERATED_STILL`/`DIAGRAM`/
`TI_STATE` `COMPOSITE`/`COMPOSITION` -- whatever actually produced a real
file, Phases 19-26 unchanged); if absent, falls back to
`VisualRenderManifest.requirements` (a standalone canonical Tí reference,
an `ASSET_REUSE` `reuse_key`, or an `EXTERNAL_REQUIRED` placeholder,
`resolved_ti_state` passed through when present) -- this is the
"standalone canonical Tí reference ... assembly contract explicitly
permits editor-side placement" case the requirement anticipates,
satisfied structurally rather than by a special case. If NEITHER exists
for a referenced beat (a `VisualRenderManifest` coverage gap that should
be unreachable given `VisualRenderer`'s own coverage guarantee, but
guarded defensively), `MissingVisualReferenceError` is raised. Nothing
here ever calls `VisualRenderer`, `TiCompositor`, `DiagramRenderer`, or
`VisualLayerCompositor` -- "VisualRenderer must run before timeline
assembly" is enforced by construction, not merely documented.

### Visual duration behavior (requirement #6)

A static visual's `TimelineVisualRef` covers its whole owning segment's
`[start_ms, end_ms)` by construction -- there is no independent
"visual duration" field to keep in sync, since one `TimelineSegment` IS
the unit that carries both. Reuse of the same visual reference across
what would otherwise be separate segments is not a scenario Phase 27's
locked `AssemblyPlan` contract can produce (one beat maps to exactly one
segment); the closest real equivalent -- multiple *narration* chunks
sharing one visual within a single segment -- is handled as described
above, and never duplicates image bytes (only the existing
`file_path`/`reference` string is copied into each
`TimelineVisualRef`/`TimelineAudioRef`, exactly like every prior
manifest's own path-not-bytes convention).

### Audio tracks (requirement #7) and existing audio-planning integration (#8)

NARRATION is `TimelineSegment.narration: list[TimelineAudioRef]` --
exact rendered file, exact resolved duration, explicit position (implied
by its owning segment's `start_ms`/`end_ms`). MUSIC and SFX are
deliberately NOT modeled as fake audio files or a parallel track
structure: Phase 13's `MusicState` (`BED`/`DUCK`/`LIFT`) and
`sfx_opportunity` are semantic cues, not real assets, so they become
`TimelineCue` events instead (see below) -- exactly the requirement's own
"store them as timeline automation/cue events, not fake audio files"
instruction. No audio mixing happens in this phase.

### Cue/event track (requirement #9)

```python
class TimelineCueType(str, Enum):
    MUSIC_BED_START = "MUSIC_BED_START"
    MUSIC_DUCK = "MUSIC_DUCK"
    MUSIC_LIFT = "MUSIC_LIFT"
    MUSIC_BED_END = "MUSIC_BED_END"
    SFX_TRIGGER = "SFX_TRIGGER"
    CUT = "CUT"            # defined, never emitted by this builder
    STATIC_HOLD = "STATIC_HOLD"  # defined, never emitted by this builder

class TimelineCue(MotilyModel):
    timestamp_ms: int
    cue_type: TimelineCueType
    reference: str | None = None
    segment_id: str | None = None
```

Music-state cue derivation rule (documented explicitly, since
`MusicState` has no explicit "off" member to hang a clean start/end
boundary on): a cue fires whenever a segment's `music_state` differs
from the immediately preceding segment's (or this is the first segment
at all) -- `BED`→`MUSIC_BED_START`, `DUCK`→`MUSIC_DUCK`,
`LIFT`→`MUSIC_LIFT` -- and exactly one closing `MUSIC_BED_END` cue fires
at the timeline's very end if music was used at all. SFX cue derivation:
each resolved `RenderedVoiceTake.sfx_opportunity` (Phase 13/17, carried
through unchanged) that is non-`None` becomes one `SFX_TRIGGER` cue at
its owning segment's `start_ms`, `reference` set to the opportunity text
verbatim. `CUT`/`STATIC_HOLD` are defined in the enum (matching the
requirement's own example list) for a future consumer that wants one
flat timestamped event stream instead of walking every segment's
`transition_in`/`out`, but THIS builder never emits them -- doing so
would duplicate information already fully captured per-segment (see
below), and Phase 27 has no second source of transition truth to justify
a redundant representation. No audio DSP, no beat syncing, no automatic
song discovery.

### Transition policy (requirement #10)

`AssemblyPlan`'s own `TransitionIntent` (Phase 15) has 5 members --
richer than a static MVP timeline can execute (`MATCH`/`PUSH` in
particular imply motion). Every `TimelineSegment` carries BOTH the
original, full-fidelity value and a deterministically downgraded one:

```python
_TRANSITION_TYPE_BY_INTENT = {
    TransitionIntent.CUT: TimelineTransitionType.CUT,
    TransitionIntent.NONE: TimelineTransitionType.HOLD,
    TransitionIntent.DISSOLVE: TimelineTransitionType.CROSSFADE,
    TransitionIntent.MATCH: TimelineTransitionType.CUT,
    TransitionIntent.PUSH: TimelineTransitionType.CUT,
}
```

`TimelineSegment.transition_in`/`out` (the restricted, MVP-safe
`TimelineTransitionType`) and `.source_transition_in`/`out` (the original
`TransitionIntent`, never discarded) are both always present. `CROSSFADE`
is reachable exactly per the requirement's own permission clause ("only
if the existing AssemblyPlan already clearly requires it") -- `DISSOLVE`
already exists in `AssemblyPlan`'s locked contract and is a crossfade in
every meaningful sense, so mapping it there needed no new assumption.
`MATCH`/`PUSH` are downgraded to `CUT` rather than invented a new
`TimelineTransitionType` member for (which the requirement's own "do not
add zoom/pan/shake/motion graphics/Ken Burns" instruction forecloses) --
documented as a Phase 27 MVP limitation, not silently misrepresented.

### Validation (requirement #11)

Split the same way `app/renderers/visual/validation.py` already
established: LOCAL, single-object invariants live in `app/models/
timeline.py`'s own `model_validator`s (negative timestamps, zero/negative
duration, `duration_ms` consistency, blank ids, `RENDERED`/`REQUIREMENT`
field consistency, `TimelineManifest.total_duration_ms` matching the
final segment's `end_ms`); CROSS-OBJECT invariants spanning the whole
`segments`/`cues` collections live in the separate, pure, I/O-free
`validate_timeline_manifest()` (duplicate `segment_id`, contiguity/no-
gaps-or-overlaps, first segment starting at 0, cue timestamps within
`[0, total_duration_ms]`); and I/O-dependent checks (missing rendered
voice/visual file, missing rendered take, unknown chunk/beat id) happen
at RESOLUTION time inside `TimelineBuilder._build_timeline` itself,
raising immediately rather than being deferred to a post-hoc pure
function that has no filesystem access. No aesthetic/timing-quality
validation anywhere.

### Timeline output / persistence (requirement #12)

```python
class TimelineManifest(MotilyModel):
    id: UUID
    project_id: UUID
    script_plan_id: UUID
    voice_plan_id: UUID
    visual_plan_id: UUID
    voice_render_manifest_id: UUID
    visual_render_manifest_id: UUID
    assembly_plan_id: UUID
    total_duration_ms: int
    segments: list[TimelineSegment]
    cues: list[TimelineCue]
    created_at: datetime
```

Persisted via the exact same `save_artifact`/`get_artifact` convention
every prior manifest uses (`TIMELINE_MANIFEST_ARTIFACT_TYPE =
"timeline_manifest"`), upserted per-project like every other artifact.
`project_id` IS included on `TimelineManifest` itself (unlike
`VisualRenderManifest`/`VoiceRenderManifest`, which omit it) because the
requirement explicitly asked for it; no field was added to the `Project`
model itself (no `timeline_manifest_id` reference) -- that would be the
kind of Project-level change the requirement explicitly guards against,
and every prior renderer already confirms it isn't needed for artifact
retrieval (project_id + artifact_type is already the storage key).

### Freshness (requirement #13)

A six-artifact chain, each verified against the ones before it, ALL
before any file is read or `ModuleRun` is created:

```
ScriptPlan (via Project.script_plan_id)
  -> VoicePlan.script_plan_id
    -> VisualPlan.script_plan_id / .voice_plan_id
      -> AssemblyPlan.script_plan_id / .voice_plan_id / .visual_plan_id
        -> VoiceRenderManifest.script_plan_id / .voice_plan_id
        -> VisualRenderManifest.script_plan_id / .voice_plan_id / .visual_plan_id
```

Each link reuses the exact freshness-gate PATTERN Phases 19/23 already
established (a private `_verify_*_is_fresh` method, checked immediately
after loading, before any subsequent load) -- six new, package-local
`Stale*Error` classes, not shared with `app/renderers/visual/errors.py`
or `app/renderers/voice/errors.py`, per this codebase's "local to this
package" errors.py convention.

### `ModuleRun` lifecycle (requirement #14)

Identical shape to every prior renderer: RUNNING -> SUCCESS/FAILED, no
project-state transition (`Project.state` stays `MVP_COMPLETE`), no
`Project` reference field added. No retry loop of any kind --
`TimelineBuilder` calls no provider, no LLM, nothing network-facing or
transient; every failure is either a freshness/reference/validation
error (immediately fatal, correctly) or a filesystem read (also
immediately fatal -- there is nothing to usefully retry).

### Determinism (requirement #15)

Identical `ScriptPlan`/`VoicePlan`/`VisualPlan`/`AssemblyPlan`/
`VoiceRenderManifest`/`VisualRenderManifest` input produces identical
`TimelineSegment`/`TimelineCue` business content on every rerun --
confirmed by a repeated-build test comparing every business field
(segment timing, narration chunk ids, visual references, cue timestamps/
types) while allowing only `TimelineManifest.id`/`created_at` to differ,
mirroring every prior manifest's own rerun-determinism test shape.

### `scripts/evaluate_timeline_builder.py`

Drives the real `VisualRenderer.run()` path (Phases 19-26, unmodified) to
produce three real static frames -- `GENERATED_STILL` via
`FakeVisualProvider` (a real decodable fixture, never a live API),
`DIAGRAM` via the real local `DiagramRenderer`, and `COMPOSITION` (the
real local `VisualLayerCompositor`) combining both -- then drives the
real `TimelineBuilder.run()` path against a hand-authored `AssemblyPlan`
and a `VoiceRenderManifest` pointing at three real local WAV fixtures
(written via the same stdlib `wave` module the builder itself reads with)
-- no live TTS API. Prints a readable segment table
(`segment_id`/`start_ms`/`end_ms`/`duration_ms`/`voice_ref`/`visual_ref`),
total duration, and every cue event, and persists the `TimelineManifest`
via the builder's own normal `run()` path. Output lands in
`data/timeline_builder_evaluation/`.

### What Phase 27 deliberately does not do

No ffmpeg encoding of any kind -- `TimelineManifest` is metadata only, no
pixel or audio byte is ever read into memory or mixed. No CapCut/editor
project generation, no XML/EDL export. No audio mixing -- cues describe
automation intent only. No music discovery -- MUSIC has no asset
resolution mechanism at all yet, deliberately. No motion graphics, no
subtitles, no publishing/upload, no automatic pacing optimization (every
timestamp comes directly from real audio duration, never adjusted for
"better" pacing). No LLM timing inference anywhere -- confirmed by the
same fresh-subprocess `app.llm` absence check every prior renderer
uses.

## Phase 28 — Deterministic Local Video Encoder (approved)

Consumes Phase 27's `TimelineManifest` and encodes the first real,
locally playable MP4:

```
TimelineManifest
    |
    v
local video encoder
    |
    v
MP4
```

Execution only -- no AI provider call, no LLM call, no change to
canonical Tí/`DiagramRenderer`/`VisualLayerCompositor`, no `TimelineManifest`
redesign, no CapCut automation, no publishing.

### Architecture boundary: two packages, one dependency direction

`app/video_encoder/` (standalone, mirrors `app/layer_compositor/`'s role
exactly): `models.py`, `errors.py`, `encoder.py`
(`VideoEncoder`+`FFmpegCommandBuilder`), `storage.py` (`VideoFileStore`,
a bare `.root` holder -- no `write()` method, since `ffmpeg` itself
writes the output file directly, unlike every prior renderer's
"read bytes into memory, then `store.write()`" shape). Zero database,
artifact, or provider access -- every path it touches is already
concrete when handed to it.

`app/renderers/video/` (mirrors `app/renderers/timeline/`'s role):
`VideoRenderer` loads a `TimelineManifest` (plus the plan/manifest chain
it was built from), verifies freshness, resolves every `TimelineSegment`
into a `VideoSegmentInput`, and delegates to `VideoEncoder`. "TimelineBuilder
describes timing; VideoEncoder(+VideoRenderer) executes it" -- the two
packages never merge responsibilities.

### FFmpeg dependency strategy

System/local `ffmpeg`/`ffprobe` executables only, via `subprocess` with
explicit argument lists -- never `shell=True`, never a raw interpolated
command string (`FFmpegCommandBuilder.build()` returns a plain
`list[str]`; `VideoEncoder`'s default runner calls
`subprocess.run(command, capture_output=True, text=True)` on it
directly). No `ffmpeg-python`, no `moviepy`, no OpenCV, no other media
framework. Resolution order: `VideoEncodingSettings.ffmpeg_path`/
`ffprobe_path` when explicitly configured (existence-checked --
`FFmpegNotFoundError`/`FFprobeNotFoundError` if the configured path
doesn't exist), else `shutil.which("ffmpeg"/"ffprobe")` (same errors if
not found on `PATH`). Never downloads or installs a binary
automatically. This development environment has `ffmpeg 9.0-full_build`
on `PATH`, confirmed via `ffmpeg -version`/`ffprobe -version` before any
code was written, and used directly for both the opt-in integration test
and the human evaluation script (see below).

### Input: never triggers upstream rendering

`VideoRenderer` never calls `VisualRenderer`, `VoiceRenderer`,
`TimelineBuilder`, or any provider -- it only loads an already-persisted
`TimelineManifest` and resolves its existing references. A
`TimelineSegment` whose visual is a `VisualRenderRequirement` (never
rendered to a file -- e.g. a standalone canonical Tí reference) cannot be
encoded and fails explicitly (`UnrenderedVisualSegmentError`) rather than
being rendered on the fly or silently skipped.

### MVP output / canvas policy (requirement #4)

MP4 container, H.264 video, AAC audio, `yuv420p` pixel format, constant
30fps (all `VideoEncodingSettings` defaults). Canvas policy, picked and
documented explicitly per the requirement's own preference: **the first
`TimelineSegment`'s own resolved image establishes output width/height**
(`VideoRenderer._resolve_canvas_size`, reading the file once via Pillow,
already a project dependency since Phase 22); every later segment's
image must match those exact dimensions, or `VideoDimensionMismatchError`
fails the whole request before `ffmpeg` ever runs
(`VideoEncoder._validate_visual`). There is no hardcoded 1920x1080
default and no resize/upscale/downscale filter applied to compensate for
a mismatch -- if the timeline's own visuals happen to already be
1920x1080, that is what gets used, purely as a consequence of this
policy.

### Video timing (requirement #5)

`TimelineManifest`'s own integer-millisecond timing is authoritative and
untouched: each segment's resolved image is held for exactly its
`duration_ms` (`-loop 1 -t <duration> -i <path>` per segment, concatenated
in timeline order via the `concat` filter). `CUT` produces an
instantaneous visual replacement by construction -- there is no
cross-fade frame blending anywhere in the filter graph for it. No timing
inference, no text-length estimation, no beat detection anywhere in this
package.

### Transition support (requirement #6)

MVP encoder executes `CUT` and `HOLD` identically (both are, at this
phase's fidelity, the same instantaneous replacement -- there is no
executable difference between "no explicit transition" and "an explicit
cut" once rendered as a static hold-then-cut sequence).
`TimelineTransitionType.CROSSFADE` (reachable via Phase 27's own
`DISSOLVE` -> `CROSSFADE` downgrade mapping) is REJECTED explicitly,
per the requirement's own preferred MVP:

```python
if TimelineTransitionType.CROSSFADE in (segment.transition_in, segment.transition_out):
    raise UnsupportedVideoTransitionError(...)
```

Checked first, before any file I/O validation, so a `CROSSFADE` segment
never even reaches the visual/narration checks. Real cross-fade execution
(actual frame blending across a fixed duration) is explicitly deferred to
a future motion-lite phase (Phase 29) -- documented here and in the
error's own docstring, never silently approximated as a cut.

### Narration audio / assembly policy (requirements #7-8)

NARRATION only in Phase 28 -- no MUSIC file, no SFX file, no ducking DSP,
no gain automation; MUSIC/SFX stay `TimelineManifest` cue metadata only,
untouched. Per segment, narration clips play back-to-back from the
segment's start:

```
[clip1:a][clip2:a]...concat=n=k:v=0:a=1[asegNraw]   (k > 1)
                       -- or --
[clip1:a]anull[asegNraw]                             (k == 1, a labeled passthrough)

[asegNraw]apad=whole_dur=<segment.duration_ms/1000>[asegN]
```

then all `N` segments' padded audio streams are concatenated into one
`[aout]`, exactly mirroring the video chain's own per-segment ->
concat-all shape. `apad=whole_dur=X` pads with silence up to `X` seconds
total IF the real audio is shorter -- it never trims if already at or
past `X`, which is exactly why real narration duration is re-verified
(via stdlib `wave`, see below) to be `<= duration_ms` BEFORE the command
is ever built: if it weren't, `apad` alone could not enforce the
"never truncate" contract on its own, so validation does that job instead
(`NarrationDurationOverflowError`, raised before any `ffmpeg` invocation).
No playback-speed change, no time-stretching, no loudness normalization
anywhere in this phase.

Multiple narration refs per segment (Phase 27's own multi-chunk-per-
segment shape) are fully supported -- the `concat` branch above handles
`k > 1` directly; `k == 1` uses `anull` purely to give the single input a
consistently-named filter-graph label, not to apply any actual effect.

### Static visual encoding / reused visuals (requirements #9-10)

Every segment's raster (PNG or JPG/JPEG, validated by suffix) is looped
for its own exact duration via its own `-loop 1 -t ... -i` input -- no
zoom, no pan, no shake, no interpolation animation; `scale=W:H,fps=...,
format=yuv420p,setsar=1` per input normalizes format/rate/aspect
(idempotent when the input is already exactly `W:H`, which validation
already guarantees) before the `concat` filter joins them in timeline
order, so the final frame sequence matches `TimelineManifest.segments`
order exactly. Reusing the same image path across segments needs no
special-casing at all: each segment's `-loop`/`-i` input simply names the
same existing file again -- `ffmpeg` reads it twice, no byte is ever
duplicated on disk, and each segment's own timing boundary is fully
preserved regardless (`test_repeated_visual_produces_two_separate_loop_inputs`
proves this at the command-construction level directly).

### File validation (requirement #11)

Split exactly like every prior renderer's own validation architecture:
LOCAL/structural checks live in `app/video_encoder/models.py`'s own
`model_validator`s (`Field(min_length=1)` segments, non-positive fps/
width/height/duration rejected, `.mp4`-only `output_path`); I/O-dependent
checks (file existence, format, dimensions, narration duration, output
writability) happen in `VideoEncoder._validate`/`encode`, ALL before
`ffmpeg` is ever invoked. `_validate_transition` runs first (cheapest,
no I/O), then `_validate_visual` (Pillow open + dimension check) and
`_validate_narration` (stdlib `wave` header read + overflow sum) per
segment. `_resolve_executable` and the output directory `mkdir` happen
after full segment validation but still before `FFmpegCommandBuilder.build`
is ever called.

### Command construction (requirement #12)

`FFmpegCommandBuilder.build(request, ffmpeg_executable) -> list[str]` is
a pure function -- no filesystem access, no subprocess, fully inspectable
in a test by indexing into the returned list (executable, `-loop`/`-i`
image inputs in order, `-i` audio inputs in order, the exact
`-filter_complex` string, `-map [vout] -map [aout]`, `-r`/`-c:v`/
`-pix_fmt`/`-c:a`, the final bounding `-t`, and the output path as the
last element). Never `shell=True`; every path -- including ones
containing spaces or Windows-style backslashes -- is its own argv
element, never concatenated into a shell string.

### Temporary files (requirement #13)

None needed, by design: the `concat` FILTER (used inside one
`-filter_complex` graph) handles every concatenation in-memory within the
single `ffmpeg` invocation, unlike the alternative concat DEMUXER (which
would require an on-disk list-file). There is therefore no dedicated work
directory, no deterministic temp-filename scheme, and no cleanup policy
to document -- there is nothing to clean up.

### Output artifact contract (requirements #14-15)

```python
class EncodedVideoAsset(MotilyModel):   # app/models/video.py
    id: UUID
    project_id: UUID
    timeline_manifest_id: UUID
    file_path: str          # relative to VideoFileStore.root
    container: str = "mp4"
    video_codec: str
    audio_codec: str
    width: int
    height: int
    fps: int
    duration_ms: int         # == TimelineManifest.total_duration_ms, always
    file_size_bytes: int
    created_at: datetime
```

MP4 bytes are never stored in SQLite -- only this metadata, via the
existing `save_artifact`/`get_artifact` convention
(`ENCODED_VIDEO_ASSET_ARTIFACT_TYPE = "encoded_video_asset"`). No field
was added to the `Project` model itself. `TimelineManifest` remains the
sole source of timing truth; `EncodedVideoAsset.timeline_manifest_id`
only references it.

### Freshness (requirement #16)

A freshness chain one link deeper than Phase 27's own `TimelineBuilder`:
ScriptPlan -> VoicePlan -> VisualPlan -> AssemblyPlan (identical checks,
reused pattern) -> TimelineManifest, verified two ways:

1. `timeline_manifest.script_plan_id`/`.voice_plan_id`/`.visual_plan_id`/
   `.assembly_plan_id` all match the CURRENT plan chain (same shape as
   every prior Stale*Error).
2. `timeline_manifest.voice_render_manifest_id`/`.visual_render_manifest_id`
   match the id of the CURRENTLY stored `VoiceRenderManifest`/
   `VisualRenderManifest` -- a check unique to this phase, since a plan
   can stay identical while its RENDERED assets are regenerated (a
   re-render replaces the stored manifest's `id` via upsert); without
   this check, `VideoRenderer` could silently encode from a `TimelineManifest`
   whose recorded file paths/durations no longer match what
   `VisualRenderer`/`VoiceRenderer` most recently produced.

Any mismatch raises `StaleTimelineManifestError` before any file is read
or `ModuleRun` is created. `TimelineBuilder` is never invoked
automatically to fix a stale manifest -- the caller must rerun it.

### `ModuleRun` lifecycle (requirement #17)

Identical shape to every prior renderer: RUNNING -> SUCCESS/FAILED, no
project-state transition, no `Project` reference field added. No retry
loop around deterministic validation, and no automatic retry of an
`ffmpeg` process failure either -- `VideoEncodingProcessError` captures
the exact return code and (tail-truncated) stderr and propagates
immediately; this phase has no notion of a transient encoding failure
worth retrying.

### Failure behavior (requirement #18)

| Failure | Error |
|---|---|
| ffmpeg executable missing | `FFmpegNotFoundError` |
| ffprobe executable missing | `FFprobeNotFoundError` |
| missing visual file | `MissingVisualFileError` |
| unsupported visual format / corrupt image | `UnsupportedVisualFormatError` |
| visual dimension mismatch | `VideoDimensionMismatchError` |
| missing narration file | `MissingNarrationFileError` |
| unsupported narration format / corrupt WAV | `UnsupportedNarrationFormatError` |
| narration longer than authored timing | `NarrationDurationOverflowError` |
| unsupported transition (CROSSFADE) | `UnsupportedVideoTransitionError` |
| ffmpeg non-zero exit | `VideoEncodingProcessError` |
| output file not produced | `VideoOutputNotProducedError` |
| zero-byte / ffprobe-unverifiable output | `VideoOutputCorruptError` |
| output directory not creatable | `OutputPathError` |
| (renderer layer) stale upstream chain | `StaleTimelineManifestError` |
| (renderer layer) unrendered visual segment | `UnrenderedVisualSegmentError` |

Never: calls a provider as fallback, regenerates imagery or audio, or
changes timeline timing silently -- every failure above is raised and
propagated unchanged, recorded as a `FAILED` `ModuleRun` by
`VideoRenderer.run()`'s existing broad `except Exception` block.

### Determinism (requirement #19)

Given identical `VideoEncodeRequest` inputs (segments, settings, resolved
executable path), `FFmpegCommandBuilder.build()` always returns an
identical argv list -- confirmed by a direct equality test
(`test_determinism_identical_request_produces_identical_command`).
Business output timing (`EncodedVideoAsset.duration_ms`,
width/height/fps/codecs) is always deterministic, since `duration_ms` is
taken directly from `VideoEncodeRequest.total_duration_ms` (itself a pure
sum of segment durations) rather than measured from the encoded
container. Byte-identical MP4 output across different `ffmpeg`
versions/environments is explicitly NOT required or guaranteed --
container metadata, muxing details, and encoder internals can legitimately
vary; only command construction and business timing are held to strict
determinism.

### `scripts/evaluate_video_encoder.py`

Extends `scripts/evaluate_timeline_builder.py`'s own three-frame project
(`GENERATED_STILL` via `FakeVisualProvider`, a local `DIAGRAM` overlay, a
`COMPOSITION` frame combining both) with an all-`CUT`/`HOLD`
`AssemblyPlan` -- that script's own `AssemblyPlan` uses one `DISSOLVE`
transition, which is exactly what Phase 28 rejects, so this script
re-authors it before building the timeline. It also re-saves `VisualPlan`
with `V2`'s `DIAGRAM` canvas resized to match `V1`'s `GENERATED_STILL`
background exactly (`640x360`) -- Phase 27's own evaluation only needed
timeline-level correctness, not per-visual canvas consistency, so this
was the one fixture adjustment Phase 28's stricter policy required.
Drives the real `VisualRenderer`, `TimelineBuilder`, and `VideoRenderer`
in sequence, producing exactly one MP4 at
`data/video_encoder_evaluation/motily_phase28_preview.mp4` (the
intermediate file `VideoRenderer` writes at its own deterministic
`{project_id}/{timeline_id}/video.mp4` path is moved, not copied, to
this fixed name, and the now-empty intermediate directories are removed,
so exactly one MP4 exists in the evaluation output tree). Prints path,
file size, `ffprobe`-reported codecs/dimensions/duration alongside the
`EncodedVideoAsset`'s own and the `TimelineManifest`'s own duration (all
three confirmed identical), and the local `ffmpeg` version string.

### What Phase 28 deliberately does not do

No music mixing, no SFX mixing, no ducking DSP, no subtitles, no motion
graphics, no CROSSFADE execution (rejected explicitly, deferred to Phase
29), no CapCut export, no publishing, no YouTube upload, no thumbnails/
packaging export. No change to `CloudflareImageProvider`/
`GeminiImageProvider`, canonical Tí, `TiCompositor`, `DiagramRenderer`, or
`VisualLayerCompositor` -- this phase only reads their already-rendered
output files. No broad `TimelineManifest` redesign -- `EncodedVideoAsset`
only adds a new artifact type that references it by id.

## Phase 29 — Deterministic Motion-Lite Video Execution (approved)

Extends Phase 28's local `ffmpeg` execution with real `CROSSFADE`
execution (closing Phase 28's one hard blocker) and a deliberately tiny,
deterministic per-segment camera-motion vocabulary. Still pure execution
-- no AI provider call, no LLM call, no content analysis, no CV/saliency,
no `TimelineManifest` redesign beyond one backward-compatible field, no
character/diagram animation, no generative video, no CapCut automation,
no publishing.

```
TimelineManifest (+ per-segment visual_motion, Phase 29)
    |
    v
local video encoder (+ xfade for CROSSFADE, zoompan/crop for motion)
    |
    v
MP4
```

### Architecture: no new package, minimally extended contracts

No new top-level package -- `app/video_encoder/` and `app/renderers/video/`
keep their exact Phase 28 shape and dependency direction.
`FFmpegCommandBuilder` remains a pure command-construction function.
Three small, backward-compatible contract additions:

1. `app/models/timeline.py`: `VisualMotionType` enum (`STATIC`,
   `SLOW_ZOOM_IN`, `SLOW_ZOOM_OUT`, `PAN_LEFT`, `PAN_RIGHT`) and
   `TimelineSegment.visual_motion: VisualMotionType = VisualMotionType.STATIC`
   -- a missing field in an old serialized `TimelineManifest` picks up the
   `STATIC` default (`MotilyModel`'s `extra="forbid"` only blocks
   *unexpected* fields, never a *missing* one with a default), so this is
   fully backward compatible with every `TimelineManifest` built before
   this phase.
2. `app/renderers/timeline/models.py`: `TimelineBuilderInput.visual_motions:
   dict[str, VisualMotionType] = {}` -- keyed by `AssemblySegment.segment_id`,
   mirroring `VisualRendererInput.ti_state_sources`/`.composition_specs`'s
   own explicit per-item-override shape exactly. An unknown key raises
   `UnknownMotionSegmentReferenceError` (`app/renderers/timeline/errors.py`),
   checked up front in `TimelineBuilder._build_timeline`, before any
   segment is constructed -- the same timing every other unknown-reference
   check in that method already uses.
3. `app/video_encoder/models.py`: `VideoEncodingSettings.crossfade_duration_ms
   = 300` and `VideoSegmentInput.motion: VisualMotionType = VisualMotionType.STATIC`
   (carried through unchanged from `TimelineSegment.visual_motion` by
   `VideoRenderer._resolve_segment_input`).

Placement rationale (requirement #21): motion-lite is a *presentation
execution* decision, not a narrative or scientific one, so it lives on
`TimelineSegment` (the execution layer) rather than on `ScriptPlan`/
`VisualPlan`/`AssemblyPlan` (the content/planning layers) -- exactly the
same reasoning that already put `TimelineTransitionType` on
`TimelineSegment` rather than upstream.

### `CROSSFADE` execution (requirements #2-4)

`_validate_transition` no longer rejects `CROSSFADE` -- it is now a
purely defensive, practically unreachable guard against a hypothetical
future `TimelineTransitionType` member this encoder does not know how to
execute (mirroring how `UnsupportedMotionTypeError` guards
`VisualMotionType`). The real decision of whether segment *i* joins
segment *i+1* as a crossfade is made by ONE field only:

```python
segments[i].transition_out is TimelineTransitionType.CROSSFADE
```

`transition_in` is **never** consulted for a join decision anywhere in
this encoder -- not on any segment, including the first. Symmetrically,
the LAST segment's own `transition_out` is never consulted either, since
it has no next segment to join to. Both are deliberate, documented
single-source-of-truth decisions: `TimelineManifest` defines no semantics
for "transitioning in from nothing" or "out to nothing," so those values
are simply inert at the encoder boundary rather than silently
reinterpreted.

**Duration-neutral crossfade math** (requirement #4, the phase's most
important invariant): a naive `xfade` between two `duration_ms`-long
clips shortens the combined output by the fade's own overlap. This
encoder compensates by extending the EARLIER segment's own `-loop`
duration by `crossfade_duration_ms` before the fade runs
(`_effective_segment_duration_ms`) and setting `xfade`'s `offset` to the
sum of the ORIGINAL (never-extended) durations of every prior segment:

```
segment A: duration Da, transition_out = CROSSFADE
segment B: duration Db
crossfade duration X

A's own -loop/-i input held for (Da + X) ms, not Da ms
xfade(A, B, offset = Da/1000 s, duration = X/1000 s)
combined length = Da/1000 + Db/1000 s  -- exactly Da + Db, unchanged
```

This generalizes across any chain mixing `CUT`/`HOLD`/`CROSSFADE` joins
in any order: the offset for the join feeding segment *i* is always the
sum of segments `0..i-1`'s ORIGINAL durations -- exactly where segment
*i* would start in a plain concatenation, regardless of how many earlier
joins were crossfades (`test_three_segments_mixed_cut_then_crossfade`/
`test_three_segments_crossfade_then_cut` prove both orderings directly at
the command-construction level). Only the segment whose OWN
`transition_out` is `CROSSFADE` is ever extended; the LAST segment is
never extended, even if its own `transition_out` happened to be
`CROSSFADE` (unused, per above). `TimelineManifest.total_duration_ms`
therefore always equals the final encoded duration exactly, with no
hidden retiming of the program as a whole.

**Video chain shape**: when no segment's `transition_out` is
`CROSSFADE` anywhere in the request, the video chain is Phase 28's exact
flat `concat=n=N` -- byte-identical filter construction, verified by
regression tests. When at least one `CROSSFADE` exists, the chain becomes
a left-to-right pairwise walk, alternating `concat=n=2` (for `CUT`/`HOLD`
joins) and `xfade=transition=fade:duration=...:offset=...` (for
`CROSSFADE` joins) as appropriate, producing intermediate labels
`[vjoin1]`, `[vjoin2]`, ... and a final `[vout]`.

**Validation** (`InvalidCrossfadeDurationError`,
`VideoEncoder._validate_crossfade_durations`, checked for every adjacent
pair before any other per-segment validation runs): `crossfade_duration_ms`
must be strictly less than BOTH adjacent segments' own `duration_ms` --
exactly equal is also rejected. Never silently shortened to fit; the
caller must lower `VideoEncodingSettings.crossfade_duration_ms` or
lengthen the segment(s) instead.

**Audio is untouched**: `CROSSFADE` is visual only. `_build_audio_filter_parts`
is Phase 28's exact function, extracted unchanged -- every segment's
narration is still silence-padded (`apad=whole_dur=...`) against its own
ORIGINAL `duration_ms`, never the video-side-extended one, and the final
audio `concat` spans every segment exactly as before. Narration is never
crossfaded, time-shifted, or overlapped.

### Motion-lite vocabulary (requirements #5-10)

Exactly five members, closed: `STATIC` (default, no filter applied at
all), `SLOW_ZOOM_IN`, `SLOW_ZOOM_OUT`, `PAN_LEFT`, `PAN_RIGHT`. Always
explicitly authored via `TimelineBuilderInput.visual_motions` -- no
content analysis, no CV/saliency, no "auto zoom toward face," no LLM
inference anywhere in this phase; an unspecified segment is `STATIC`.
Motion spans exactly a segment's own authored `duration_ms` and never
changes segment start/end timing (it is a filter-graph-only concern; the
segment's own `-loop`/`-i` duration is governed solely by the crossfade
extension logic above, completely independently of its motion). Motion
operates on the segment's FINAL resolved raster only -- it never
animates an individual Tí layer, diagram primitive, or reopens a
`LayerCompositionSpec`; two segments referencing the same source raster
with different motions simply get two independent filter-graph branches
(no file duplication, exactly like Phase 28's own reused-visual policy).

**Zoom uses `zoompan`, not `crop`** (a design change from this phase's
first draft, corrected after empirical testing): a `crop` filter's `w`/
`h` expressions are evaluated exactly ONCE, at filter-graph configuration
time -- confirmed empirically (a `t`-based `w`/`h` expression fails
immediately with `"Error when evaluating the expression"`, before any
frame is processed, both via a direct `ffmpeg -f lavfi ... -vf crop=...`
repro and inside this encoder's own smoke test). Only `crop`'s `x`/`y`
are genuinely re-evaluated every frame, which is what `PAN_LEFT`/
`PAN_RIGHT` use instead (below). A time-varying crop SIZE therefore
cannot be built from `crop` alone, so zoom uses `zoompan`. `zoompan`'s
own well-documented `zoom+=increment` self-referencing accumulation
pattern -- the reason this phase's design originally avoided it -- is
sidestepped entirely: the zoom expression here is a pure function of
`on` (`zoompan`'s own monotonic output-frame index), never referencing
its own previous value, so there is nothing to accumulate or drift:

```
zoompan=z='(1.00+(1.06-1.00)*on/(total_frames-1))':
  x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':
  d=1:s=<W>x<H>:fps=<fps>
```

(`1.06`→`1.00` for `SLOW_ZOOM_OUT`.) `total_frames = max(round(duration_ms
/1000 * fps), 2)`. Pairing an explicit `-framerate <fps>` on the
segment's own looped image input (added in this phase for every segment,
not only zoomed ones, for uniformity) with `d=1` (one `zoompan` output
frame per input frame, since the loop already supplies one distinct
frame per timestamp) makes `on` range exactly `0..total_frames-1` across
the segment's own duration, deterministically. `zoompan`'s own `s=`
option performs the zoom crop AND rescales back to the fixed canvas in
one step, so no separate `scale` call is needed for zoom.

**Pan uses `crop`'s `x`/`y`**, confirmed genuinely re-evaluated per
frame: the source is pre-scaled to `round(width * 1.05)` (5% wider than
canvas), then a canvas-sized `crop` window's `x` moves linearly across
the available travel (`max_offset = prescale_width - width`) via a
`t`-based expression, clamped with `min`/`max`:

```
scale=<prescale_w>:<H>,crop=w=<W>:h=<H>:x='min(<max_offset>,max(0,<max_offset>*t/<duration_s>))':y=0   (PAN_RIGHT)
scale=<prescale_w>:<H>,crop=w=<W>:h=<H>:x='min(<max_offset>,max(0,<max_offset>*(1-t/<duration_s>)))':y=0   (PAN_LEFT)
```

No vertical pan in this phase (kept out of MVP scope, per the
requirement's own preference). If `round(width * 1.05) - width <= 0`
(the canvas is small enough that the 5% pre-scale rounds to zero added
pixels), this fails explicitly (`MotionSourceTooSmallError`,
`VideoEncoder._validate_motion`) rather than silently producing a
zero-travel "pan."

### A same-request timebase pitfall (found via manual evaluation)

Running the human-evaluation script (below) with a real 3-segment
timeline mixing a `CUT`, a `CROSSFADE`, and `SLOW_ZOOM_IN`/`PAN_RIGHT`
motions surfaced a real `ffmpeg` failure not caught by any unit or
narrower integration test: `zoompan` produces a stream on a distinct
internal timebase (tied to its own `fps=` option) from a plain `scale`/
`crop` chain's generic timebase. `ffmpeg`'s `concat` filter silently
tolerates two inputs with different timebases, but a downstream `xfade`
fed from that `concat`'s output does not --

```
[Parsed_xfade_N] First input link main timebase (1/1000000) do not match
the corresponding second input link xfade timebase (1/30)
```

Fixed by appending `settb=AVTB` to the end of EVERY segment's own
normalization chain (after `fps=`/`format=`/`setsar=1`, before the label)
-- this puts every stream on the same arbitrary timebase before it ever
reaches a `concat` or `xfade` node, regardless of join/motion order, and
is a no-op for chains that never hit the mismatch. Regression-tested
directly (`test_real_mixed_cut_then_crossfade_with_zoom_motion`, using
real `ffmpeg`) with the exact shape that reproduced it: `[zoom]`--`CUT`
-->`[static]`--`CROSSFADE`-->`[pan]` (a `concat` feeding into an
`xfade`, with `zoompan` upstream of the `concat`).

### Required-filter check (requirement #14)

Before building the real command, `VideoEncoder._check_ffmpeg_filters_available`
queries the resolved `ffmpeg` executable's own `ffmpeg -hide_banner
-filters` output once (via the same injectable `ProcessRunner` used for
the main encode and the `ffprobe` verification call) and confirms every
filter this specific request actually needs is listed: `xfade` if any
`CROSSFADE` join exists, `zoompan` if any `SLOW_ZOOM_IN`/`SLOW_ZOOM_OUT`
segment exists, `crop`/`scale` if any `PAN_LEFT`/`PAN_RIGHT` segment
exists. A request needing none of these (Phase 28's own `CUT`/`HOLD`/
`STATIC`-only shape) makes no filter-check call at all -- confirmed by a
regression test asserting the fake runner sees exactly the same two
calls (`ffmpeg` + `ffprobe`) as Phase 28's own happy path. Missing filter
raises `FFmpegFilterUnavailableError`, listing exactly which filter(s)
are absent -- never a silent fallback, never an automatic download of a
different `ffmpeg` build. The filter-name match (`_filter_listed`) is an
exact per-line match against `ffmpeg -filters`'s own name column, not a
substring search, to avoid a false match like `"scale"` matching the
unrelated `"scale2ref"` row.

### Failure behavior (requirement #15)

| Failure | Error |
|---|---|
| `crossfade_duration_ms` not strictly shorter than both adjacent segments | `InvalidCrossfadeDurationError` |
| a `VisualMotionType` value with no known filter mapping (unreachable; closed enum) | `UnsupportedMotionTypeError` |
| pan pre-scale would add zero whole pixels of travel | `MotionSourceTooSmallError` |
| resolved `ffmpeg` build missing a filter this request needs | `FFmpegFilterUnavailableError` |
| an unrecognized future `TimelineTransitionType` value (unreachable today) | `UnsupportedVideoTransitionError` (retired for `CROSSFADE` specifically; kept as a defensive guard) |

Every Phase 28 failure mode (missing/unsupported/mismatched visual or
narration, missing executables, non-zero exit, missing/zero-byte output)
is unchanged.

### Persistence (requirement #22)

`EncodedVideoAsset` (`app/models/video.py`) and `VideoEncodeResult`
(`app/video_encoder/models.py`) each gain two small optional fields
rather than a second, competing video-output model:

```python
crossfade_count: int = 0
"""How many adjacent-segment joins were executed as a real CROSSFADE."""
motion_profile_used: list[VisualMotionType] = Field(default_factory=list)
"""The distinct non-STATIC VisualMotionType values actually applied,
in first-seen order."""
```

Both propagate unchanged from `VideoEncodeResult` through
`VideoRenderer._encode` into the persisted `EncodedVideoAsset`.
`TimelineManifest` remains the sole source of timing truth; nothing about
this phase changes that.

### Determinism (requirement #23)

Identical `VideoEncodeRequest` inputs (segments, settings, resolved
executable path) still always produce an identical `ffmpeg` argv list,
including the crossfade/motion filter expressions -- confirmed by direct
equality tests for both crossfade chains and motion filters
(`test_crossfade_determinism`, `test_motion_parameter_determinism`).
Visual execution timing (which frame shows which zoom/pan/dissolve state
at which timestamp) is likewise a deterministic function of the request,
never randomized or wall-clock-dependent. Byte-identical MP4 output
across `ffmpeg` versions/environments remains explicitly NOT required or
guaranteed, exactly as Phase 28 already established.

### `scripts/evaluate_motion_lite_video.py`

Extends `scripts/evaluate_timeline_builder.py`'s own three-frame project
and `scripts/evaluate_video_encoder.py`'s own diagram-canvas fix with an
`AssemblyPlan` authoring exactly one `CUT` (S1→S2, `S1.transition_out =
CUT`) and one real `CROSSFADE` (S2→S3, via `S2.transition_out =
DISSOLVE`), plus a `TimelineBuilderInput.visual_motions` override giving
S1 `SLOW_ZOOM_IN` and S3 `PAN_RIGHT` (S2 is left unspecified,
demonstrating the `STATIC` default). Drives the real `VisualRenderer`,
`TimelineBuilder`, and `VideoRenderer` in sequence, producing exactly one
MP4 at `data/motion_lite_evaluation/motily_phase29_preview.mp4`. Prints
path, file size, codecs, fps, canvas, `ffprobe`-measured duration
alongside the asset's and timeline's own authoritative duration
(confirmed identical), the configured crossfade duration, the actual
crossfade count, the motions used, and the local `ffmpeg` version string.

### What Phase 29 deliberately does not do

No independent character animation, no diagram-element animation, no
motion tracking, no semantic camera framing (no "auto zoom toward the
subject"), no AI-chosen motion of any kind, no audio/music crossfades, no
music/SFX mixing, no subtitles, no CapCut/export integration, no
publishing. No rotation, shake, bounce, arbitrary x/y keyframes, path
animation, perspective, or parallax -- the vocabulary stays exactly the
five members above. No change to canonical Tí, `DiagramRenderer`, or
`VisualLayerCompositor` -- motion-lite operates strictly on the already-
composited final raster beneath this execution layer. No broad
`TimelineManifest` redesign -- `visual_motion` is one backward-compatible
field with a safe default, and `crossfade_duration_ms` stays a single
encoder-wide setting rather than a new per-segment numeric field on
`AssemblySegment`/`TimelineSegment`.

## Phase 30 — Deterministic Music & SFX Mix Execution (approved)

Turns `TimelineManifest`'s existing `MUSIC_BED_START`/`MUSIC_DUCK`/
`MUSIC_LIFT`/`MUSIC_BED_END`/`SFX_TRIGGER` cues (Phase 27's own cue
vocabulary, declared but never executed before now) into a real mixed
audio track, layered underneath the narration that remains the exact,
unchanged timing authority throughout. Still pure execution -- no AI
music selection, no beat matching, no creative audio inference, no
search/download of any audio.

```
TimelineManifest cues (MUSIC_*/SFX_TRIGGER)
    |            \
    v             v
AudioMixPlan   AudioAssetBindings (explicit, caller-provided)
    \             /
     v           v
  FFmpegCommandBuilder's audio filter graph
              |
              v
    narration + music + SFX -> AAC
```

### Architecture: one new pure planning module, no responsibility shift

`app/video_encoder/audio_mix.py` is new; `app/video_encoder/encoder.py`
and `app/renderers/video/renderer.py` keep their exact Phase 28/29
dependency direction ("TimelineBuilder describes cues; VideoEncoder
executes them" -- mixing logic never moves upstream into TimelineBuilder,
per this phase's own requirement #1). `build_audio_mix_plan` is a pure
function (no filesystem, no subprocess) turning `VideoEncodeRequest.cues`
+ `.audio_bindings` into a flat `AudioMixPlan` (contiguous
`MusicRegionPlan`s, independent `SfxEventPlan`s); `FFmpegCommandBuilder`
is the only thing that ever turns that plan into a real filter graph.

### Explicit external audio asset input (requirement #2)

```python
class AudioAssetBindings(MotilyModel):   # app/video_encoder/models.py
    music_bed_path: Path | None = None
    sfx_by_id: dict[str, Path] = {}
```

No fuzzy lookup, no filesystem scanning, no default/random SFX, no
network access -- a cue that needs an asset this dict/field doesn't
provide fails explicitly. `TimelineCue.reference` already carries a
stable SFX identifier for every `SFX_TRIGGER` cue (Phase 27's
`TimelineBuilder` populates it from `RenderedVoiceTake.sfx_opportunity`),
so no new `TimelineCue` field was necessary (requirement #8's own
preferred outcome).

### Opt-in wiring: zero regression without new code at every call site

`VideoEncodeRequest.cues: list[TimelineCue] = []` and `.audio_bindings:
AudioAssetBindings = AudioAssetBindings()` both default to "nothing to
mix" -- an empty `cues` list makes every new code path in this phase
inert, and the audio filter graph is byte-for-byte Phase 29's own. Since
`MusicState` (`app/models/common.py`) has no "none" member, EVERY real
`TimelineManifest` built by Phase 27's `TimelineBuilder` carries at least
one music cue (whatever state the first segment declares) plus a closing
`MUSIC_BED_END` -- so unconditionally forwarding `timeline_manifest.cues`
into every `VideoEncodeRequest` would force every existing caller to
either supply a real music binding or hit `MissingMusicAssetError`. This
phase avoids that with one explicit opt-in field:

```python
class VideoRendererInput(MotilyModel):   # app/renderers/video/models.py
    project_id: UUID
    audio_bindings: AudioAssetBindings | None = None
```

`None` (the default, and every pre-Phase-30 call site's own value) makes
`VideoRenderer._encode` build a `VideoEncodeRequest` with `cues=[]` --
the manifest's own MUSIC_*/SFX_TRIGGER cues are never even inspected,
regardless of what they contain. Passing an `AudioAssetBindings` (even an
entirely empty one) forwards `timeline_manifest.cues` for real, engaging
every validation/error path below. This mirrors `TimelineBuilderInput.
visual_motions`/`VisualRendererInput.ti_state_sources`'s own established
shape: an explicit per-request override dict/field that, absent, leaves
prior behavior completely untouched.

### Music state machine (requirement #14)

`app/video_encoder/audio_mix.py`'s `_plan_music_regions` walks only
`MUSIC_BED_START`/`MUSIC_DUCK`/`MUSIC_LIFT`/`MUSIC_BED_END` cues, in the
order given (never re-sorted), maintaining one `active`/`region_state`
pair:

| Cue | While inactive | While active |
|---|---|---|
| `MUSIC_BED_START` | opens a BED region | `InvalidMusicCueSequenceError` |
| `MUSIC_DUCK`/`MUSIC_LIFT` | `InvalidMusicCueSequenceError` | closes the current region, opens a DUCK/LIFT region |
| `MUSIC_BED_END` | `InvalidMusicCueSequenceError` | closes the current region, bed inactive |

Additional rejections (all `InvalidMusicCueSequenceError` except the
timeline-bounds one, which is `AudioMixPlanningError`): cues out of
nondecreasing timestamp order, two music cues sharing the exact same
timestamp ("duplicate/contradictory," requirement #14's own phrase), a
music cue timestamp beyond `total_duration_ms`, and (once more than one
region exists) any region shorter than `music_gain_ramp_ms` -- it could
not be crossfaded into and out of without overlapping ramps. Every
non-music, non-SFX cue type (`CUT`, `STATIC_HOLD`) is inert to this state
machine and to SFX planning -- no visual cue implicitly modifies audio
(requirement #18).

**Unterminated-bed policy (requirement #4, chosen per that requirement's
own preferred default)**: a `MUSIC_BED_START` with no matching
`MUSIC_BED_END` implicitly closes at `total_duration_ms`, never rejected
-- the vast majority of authored timelines simply let the bed run to the
program's own end without an explicit closing cue (indeed, Phase 27's own
`TimelineBuilder` always emits one automatically, so this policy is
mostly a safety net for hand-constructed `VideoEncodeRequest`s in tests).
A `MUSIC_BED_END` with no active bed is rejected explicitly, per that
same requirement's own preference.

### Music gain and gain-ramp policy (requirements #5-6)

Fixed, deterministic gains from `VideoEncodingSettings` -- never
dynamically normalized, never inspecting narration loudness, never
sidechain-compressed:

```python
music_bed_gain_db: float = -24.0
music_duck_gain_db: float = -32.0
music_lift_gain_db: float = -20.0
sfx_gain_db: float = -10.0
music_gain_ramp_ms: int = 80
```

Validated to `[-60.0, 0.0]` dB (a documented floor, never an absurdly
quiet or a boosting positive value) and `music_gain_ramp_ms > 0`.

**Gain ramps were cleanly achievable, so this phase did NOT fall back to
a hard gain switch** (the MVP-acceptable fallback the requirement itself
allows): `ffmpeg`'s `acrossfade` filter -- the audio analogue of
`xfade`, already used for video `CROSSFADE` -- crossfades two
constant-gain region slices over `music_gain_ramp_ms`, using the exact
same duration-neutral extend-then-overlap trick already proven for video:
the earlier region's own trimmed length is extended by
`music_gain_ramp_ms` before the crossfade "spends" that extension, so the
combined bed length still equals the naive sum of every region's own
authored duration. A lone BED region (no `DUCK`/`LIFT` ever happened)
skips `asplit`/`acrossfade` entirely -- nothing to crossfade into, and
therefore exempt from the ramp-length constraint even if very short.

### Music looping/trimming (requirement #7)

`-stream_loop -1` on the music bed's own input loops it indefinitely at
the demuxer level -- this encoder never measures the source file's own
duration, never time-stretches, never changes pitch, never selects a
different file. Each region's own `atrim=start=<X>:end=<Y>` (bed-relative
seconds) then caps the looped stream at exactly the region's own
authored length (extended by the ramp where applicable, per above) --
the SAME mechanism handles a source shorter than needed (there is always
enough looped material to draw from) and a source longer than needed
(`atrim`'s own `end=` is a ceiling) with zero branching logic. Final
music execution never changes `total_duration_ms` -- the outer command's
own final `-t` bound (Phase 28's own) is untouched.

### SFX execution (requirements #8-10)

Each `SFX_TRIGGER` cue gets its OWN `-i` input (never deduplicated by
file, exactly like this module's own reused-visual policy from Phase 28)
so independent events reusing the same source file can each be
positioned independently. Starts exactly at `cue.timestamp_ms` -- never
quantized to a frame boundary:

```
[{sfx_input}:a]atrim=start=0.000:end=<seconds until total_duration_ms>,
  asetpts=PTS-STARTPTS,volume=<sfx_gain_db>dB,
  adelay=<cue.timestamp_ms>:all=1[asfxN]
```

`atrim`'s own `end=` is a ceiling, not a padding instruction -- a
naturally shorter clip is unaffected, and a clip that would otherwise run
past the timeline's own end is cut off there (requirement #9's own
preferred policy, chosen because the authored trigger itself is valid
and the timeline remains authoritative). One global `sfx_gain_db`
default; no per-cue override (`TimelineCue` carries no gain field to read
one from, so requirement #10's "only if already present" condition is
not met).

### FFmpeg audio filter graph (requirements #12-13)

`filter_complex` only, one `ffmpeg` execution, no intermediate WAV files,
no new audio library. Narration's own final concat label is renamed from
`[aout]` to `[anarr]` ONLY when music or SFX mixing is actually engaged
(`mixing_needed = plan.has_music or bool(plan.sfx_events)`) -- when
`cues` is empty, the label stays `[aout]` and the filter string is
byte-for-byte Phase 29's own:

```
[i:a] narration concat/apad chain, per segment           -> [anarr]  (or [aout] if no mixing)
[music:a] asplit -> per-region atrim+volume -> acrossfade chain -> adelay -> [amusicdelayed]
[sfx_i:a] atrim+asetpts+volume+adelay, one chain per event        -> [asfxN]
[anarr][amusicdelayed][asfx0]...amix=inputs=N:duration=longest:normalize=0 -> [aout]
```

`normalize=0` is essential: `amix`'s own default silently scales every
input down by `1/N`, which would defeat every gain level this phase
deliberately chose. `duration=longest` is a safety margin only -- every
constituent stream is already independently bounded, and the outer
command's own final `-t <total_duration_ms>` (unchanged since Phase 28)
remains the authoritative hard cutoff regardless of any internal
filter-graph rounding.

A same-request timebase pitfall (Phase 29's own `zoompan`/`xfade`
mismatch, fixed there with `settb=AVTB`) was checked for here too, but
did not reproduce: `atrim`/`asplit`/`acrossfade`/`adelay`/`amix` all
carried a consistent timebase in every real-`ffmpeg` test run in this
phase, so no analogous workaround was needed on the audio side.

### Narration compatibility (requirement #11)

Narration's own filter construction (`_build_audio_filter_parts`) is
Phase 28's exact, unmodified function -- same per-segment concat/apad
against each segment's own authored `duration_ms`, same silence padding,
no time-stretch, no crossfade, no loudness normalization. Only its FINAL
output label changes (`[aout]` -> `[anarr]`), and only when mixing is
actually engaged. Narration remains perceptually primary through this
phase's own conservative default gains (music at -24/-32/-20dB, SFX at
-10dB, well below narration's own un-attenuated level).

### Validation and errors (requirements #15, #21)

Two passes, mirroring this module's existing visual/narration validation
split: `_validate_audio_assets` (file exists, supported suffix -- `.wav`/
`.mp3`/`.m4a`/`.aac` for both music and SFX, a conservative superset
chosen because duration never needs to be pre-measured for either asset
type, unlike narration) runs during `_validate`, before any executable is
resolved; `_check_audio_assets_readable` (an `ffprobe` stream-presence
check, once per DISTINCT bound file this request actually uses) runs
alongside the existing `_check_ffmpeg_filters_available` call (which
itself now also checks `amix` whenever mixing is engaged, and
`acrossfade` whenever more than one music region exists), after
executables are resolved but before the real command is built.

| Failure | Error |
|---|---|
| music cues exist but no/missing music_bed_path | `MissingMusicAssetError` |
| SFX id resolves in bindings but its file is missing | `MissingSFXAssetError` |
| SFX cue's id is blank or not in `sfx_by_id` | `UnknownSFXReferenceError` |
| music cue state-machine violation | `InvalidMusicCueSequenceError` |
| bound music/SFX file has an unsupported suffix | `UnsupportedAudioAssetFormatError` |
| `ffprobe` finds no audio stream in a bound file | `AudioAssetUnreadableError` |
| a music/SFX cue timestamp is beyond `total_duration_ms` | `AudioMixPlanningError` |

No fallback anywhere in this table -- every failure is raised and
propagated unchanged, exactly like every prior phase's own encoder
errors.

### Silence/no-music/SFX-only compatibility (requirements #16-17)

A request with `cues=[]` encodes exactly as Phase 29 did -- confirmed by
a direct equality test (`_build(request) == _build(request,
AudioMixPlan())`) and by every existing Phase 28/29 test continuing to
pass completely unmodified. Narration-only, narration+music,
narration+SFX, and narration+music+SFX are all supported; narration
itself remains required under Phase 28's own contract (`Field(min_length
=1)` on `narration_clips`) -- this phase does not broaden into
silent-video support.

### Persistence (requirement #19)

`EncodedVideoAsset`/`VideoEncodeResult` each gain three small optional
fields, propagated unchanged, exactly mirroring Phase 29's own
`crossfade_count`/`motion_profile_used` precedent:

```python
has_music: bool = False
sfx_event_count: int = 0
music_cue_count: int = 0
```

No second, competing media-output artifact.

### `scripts/evaluate_audio_mix.py`

Extends `scripts/evaluate_video_encoder.py`'s own three-frame project
with an `AssemblyPlan` authoring music_state `BED`/`DUCK`/`LIFT` on
S1/S2/S3 (`TimelineBuilder`'s own music-state-change cue derivation then
produces `MUSIC_BED_START`/`MUSIC_DUCK`/`MUSIC_LIFT` automatically, plus
an explicit `MUSIC_BED_END` at the timeline's own end) and
`S2.transition_out=DISSOLVE` (a real `CROSSFADE` into S3); a second
`SFX_TRIGGER` opportunity is added to the fixture `VoicePlan` (Phase 27's
own fixture only gives one); `TimelineBuilderInput.visual_motions` gives
S1 a `SLOW_ZOOM_IN`. Three simple, locally-generated sine-tone WAV
fixtures (never real or copyrighted audio, clearly labeled as technical
test audio) are bound via `AudioAssetBindings` and passed into
`VideoRendererInput.audio_bindings`, opting into real mix execution.
Produces exactly one MP4 at
`data/audio_mix_evaluation/motily_phase30_preview.mp4`. Prints path, file
size, codecs, fps, canvas, `ffprobe`-measured duration alongside the
asset's and timeline's own authoritative duration, crossfade count,
motions used, music cue count, SFX event count, the bindings used, and
the local `ffmpeg` version string.

### What Phase 30 deliberately does not do

No AI music generation, no music discovery/download, no beat syncing, no
sidechain compression, no loudness mastering, no EQ, no reverb, no
spatial audio, no subtitle execution, no CapCut integration, no
publishing. No change to canonical Tí, `DiagramRenderer`, or
`VisualLayerCompositor`. No broad `TimelineManifest` redesign -- no new
field was added to `TimelineCue` (its existing `reference` field already
carried a usable SFX id), and mixing logic stays entirely inside
`app/video_encoder/`, never moving upstream into `TimelineBuilder`.

## Phase 31 — Deterministic Subtitle/Caption Execution (approved)

Turns existing authored script/voice/timeline alignment into
deterministic subtitle artifacts, with optional local FFmpeg caption
burn-in where the environment supports it. Captions are never
transcribed, inferred from audio, or generated -- no Whisper, no STT, no
LLM anywhere in this phase.

```
ScriptPlan / VoicePlan
        +
TimelineManifest
        |
        v
CaptionBuilder (app/renderers/caption/)
        |
        v
CaptionManifest (app/models/caption.py)
        |
        +--> SRT exporter (app/captions/srt.py, pure)
        |        |
        |        v
        |    SubtitleFileAsset (always, app/renderers/subtitle/)
        |
        +--> [opt-in] CaptionBurnInRenderer (app/captions/burn_in.py)
                 |
                 v
             CaptionedVideoAsset
```

### Existing-contract inspection (requirement #1)

Before writing any code, the exact identity chain from authored text to
rendered narration was traced through the existing models (no upstream
model was changed):

```
TimelineSegment.narration[i]: TimelineAudioRef
    .chunk_id  ---------------------------> VoicePlan.chunks[chunk_id]: VoiceChunk
                                                 .line_ids -----------> ScriptPlan's own
                                                                        ScriptLine.text
                                                                        (per beat, per line_id)
```

`TimelineAudioRef` deliberately carries only `chunk_id` (Phase 27's own
choice) -- not `line_ids`, and not text -- so a caption layer must
re-join through `VoicePlan.chunks[chunk_id].line_ids` to reach
`ScriptLine.text`; `TimelineSegment.script_line_ids` is only an aggregate
across the WHOLE segment (potentially spanning multiple chunks) and
cannot be used per-`TimelineAudioRef` directly. No upstream model
(`ScriptPlan`, `VoicePlan`, `AssemblyPlan`, `TimelineManifest`) needed a
new field to support this -- the only truly new persisted models are
`CaptionCue`/`CaptionManifest`/`SubtitleFileAsset`/`CaptionedVideoAsset`
themselves.

Unit note carried forward exactly: `TimelineAudioRef.duration_ms` and
`TimelineSegment.start_ms`/`end_ms`/`duration_ms` are already integer
milliseconds (Phase 27's own single float-to-int conversion point,
`TimelineBuilder._resolve_audio_duration_ms`) -- captions consume these
directly, introducing no second unit-conversion boundary.

### Architecture boundary (requirement #2)

Three cleanly separated responsibilities, matching the phase's own
suggested split exactly:

- **`app/captions/builder.py`'s `build_caption_manifest`** (pure,
  dependency-free): determines caption text and timing. No filesystem,
  no subprocess, no DB.
- **`app/captions/srt.py`'s `render_srt`** (pure): serializes a
  `CaptionManifest` into deterministic UTF-8 SRT text. No filesystem.
- **`app/captions/burn_in.py`'s `CaptionBurnInRenderer`**: burns an
  already-built SRT into an already-encoded video stream via local
  `ffmpeg` -- mirrors `app/video_encoder/encoder.py`'s own
  `VideoEncoder` shape (injectable subprocess runner, pure
  `CaptionBurnInCommandBuilder.build()` for testability).

Two artifact-driven renderer-layer packages sit on top, mirroring
`app/renderers/timeline/` and `app/renderers/video/`'s own
DB/freshness/`ModuleRun` responsibilities:

- **`app/renderers/caption/builder.py`'s `CaptionBuilder`**: loads +
  freshness-checks the plan/manifest chain, calls the pure
  `build_caption_manifest`, persists `CaptionManifest`.
- **`app/renderers/subtitle/renderer.py`'s `SubtitleRenderer`**: loads +
  freshness-checks `CaptionManifest` (and, only if burn-in is requested,
  `EncodedVideoAsset`), always exports SRT via the pure `render_srt` +
  `SubtitleFileStore`, optionally invokes `CaptionBurnInRenderer`.

Caption text construction never lives inside `VideoEncoder` -- that
package remains exactly Phase 28/29/30's own, completely untouched by
this phase.

### Caption source-of-truth (requirement #3)

`build_caption_manifest` builds two lookup dicts once per call --
`chunks_by_id` (from `VoicePlan.chunks`) and `line_text_by_id` (from
every `ScriptPlan` beat's own lines) -- then, for each `TimelineAudioRef`
in timeline order, resolves `text = " ".join(line_text_by_id[lid] for lid
in chunk.line_ids)`. This is the ONLY transformation ever applied to
authored text: a single-ASCII-space join across a chunk's own multiple
lines, in their own declared order. For the (common) single-line-per-
chunk case, this join is the identity function -- the cue's `text` is
byte-for-byte that one `ScriptLine.text`, satisfying requirement #28's
own fidelity contract directly. Never: derived from a waveform, run
through STT, auto-corrected for grammar, or altered in punctuation --
confirmed by `test_source_text_fidelity_exact_match`
(tests/test_caption_builder.py), which re-derives the expected joined
text independently and asserts byte-for-byte equality against every
produced cue. Vietnamese diacritics are ordinary Unicode code points to
every layer in this chain (Python strings, pydantic, SQLite JSON storage,
UTF-8 file I/O) -- nothing in this phase performs ASCII transliteration,
case-folding, or any other lossy text transform.

### Timing authority and granularity (requirements #4-6)

Exactly one `CaptionCue` per `TimelineAudioRef` -- never merged across
chunks sharing a visual segment, never split by words/heuristics. Timing
is copied from the SAME cumulative-duration arithmetic
`app/renderers/timeline/builder.py` already used to compute each
segment's own `start_ms`/`end_ms`: the first cue in a segment starts at
`segment.start_ms`; each next cue starts exactly where the previous
narration ref's own `duration_ms` ends. No float timestamps anywhere in
this chain -- `CaptionCue.start_ms`/`end_ms`/`duration_ms` are `int`
milliseconds throughout, and `CaptionCue`'s own validator enforces
`start_ms >= 0`, `end_ms > start_ms`, and `duration_ms == end_ms -
start_ms` exactly (same invariant shape as `TimelineSegment`'s own).

A defensive check (`CaptionTimingOverflowError`) confirms each segment's
own accumulated cue-duration total exactly equals `segment.duration_ms`
-- this can only fail if `TimelineManifest` itself is internally
inconsistent, since this is the identical sum `TimelineBuilder` used to
derive that same `duration_ms` in the first place; kept as a guard
against a caption-layer construction bug, not a real-world reachable
path with a valid `TimelineManifest`.

### Caption manifest contract (requirement #7)

```python
class CaptionCue(MotilyModel):          # app/models/caption.py
    id: UUID
    start_ms: int
    end_ms: int
    duration_ms: int
    text: str
    script_line_ids: list[str]
    voice_chunk_id: str
    render_job_id: str                  # RenderedVoiceTake.render_job_id --
                                         # there is no separate UUID identifier
                                         # for a RenderedVoiceTake to reuse instead


class CaptionManifest(MotilyModel):
    id: UUID
    project_id: UUID
    timeline_manifest_id: UUID
    script_plan_id: UUID
    voice_plan_id: UUID
    voice_render_manifest_id: UUID
    total_duration_ms: int
    cues: list[CaptionCue]
    created_at: datetime
```

No styling/presentation field anywhere on `CaptionCue` or
`CaptionManifest` -- see `CaptionRenderSettings`
(`app/captions/models.py`) below for that entirely separate,
execution-side concern.

### Caption validation (requirement #8)

`CaptionCue`'s own validator: non-blank text, non-negative `start_ms`,
`end_ms > start_ms`, exact `duration_ms`, non-blank `voice_chunk_id`/
`render_job_id`, at least one `script_line_ids` entry.
`CaptionManifest`'s own validator: positive `total_duration_ms`,
timezone-aware `created_at`, no duplicate cue ids, every cue's `end_ms
<= total_duration_ms`, and strictly chronological/non-overlapping cues
(`later.start_ms >= earlier.end_ms` for every adjacent pair -- exact
touching, i.e. zero gap, is valid and expected between two cues from
either the same or adjacent segments; narration itself never overlaps by
`TimelineBuilder`'s own contiguous construction, so any overlap here
indicates a caption-layer bug). `build_caption_manifest` itself raises
`UnknownCaptionChunkReferenceError` (a `TimelineAudioRef.chunk_id` with
no matching `VoiceChunk`), `MissingCaptionSourceTextError` (a
`VoiceChunk.line_ids` entry with no matching `ScriptLine`), and
`CaptionTimingOverflowError` (see above) -- all three are defensive
guards, unreachable with an internally-consistent `TimelineManifest`/
`VoicePlan`/`ScriptPlan` triple, never a fuzzy match or a silent skip.
No grammar/style correctness validation anywhere, per this phase's own
explicit exclusion.

### Freshness (requirement #9)

`CaptionBuilder` re-verifies the EXACT SAME full freshness chain
`app/renderers/video/renderer.py`'s own `VideoRenderer` already checks:
ScriptPlan → VoicePlan → VisualPlan → AssemblyPlan → TimelineManifest,
PLUS TimelineManifest's own cross-check against the CURRENT
VoiceRenderManifest/VisualRenderManifest ids it was built from. Caption
text/timing does not itself depend on VisualPlan/VisualRenderManifest at
all, but this phase deliberately does NOT invent a lighter, caption-
specific freshness exception -- "prefer full timeline freshness" (this
phase's own requirement #9) means CaptionBuilder pays the identical
verification cost VideoRenderer does, for the identical guarantee, so a
project whose visuals were re-rendered without rebuilding the timeline
is caught here exactly as it would be for video encoding, even though
that specific staleness is irrelevant to caption content. No upstream
artifact is ever auto-rebuilt to fix a stale chain -- the caller must
rerun the appropriate builder first, exactly like every prior phase.

`SubtitleRenderer` re-verifies that SAME full chain again (a second,
independent consumer of `TimelineManifest`), plus a direct check that
the current `CaptionManifest.timeline_manifest_id` matches the current
`TimelineManifest.id` (`StaleCaptionManifestError`) -- and, only when
burn-in is requested, that the current `EncodedVideoAsset
.timeline_manifest_id` also matches (`StaleEncodedVideoAssetError`).

### `ModuleRun` lifecycle (requirement #10)

Identical shape to every prior renderer: RUNNING → SUCCESS/FAILED, no
project-state transition, no retry loop, no AI call anywhere.
`CaptionBuilder` uses module name `"caption_builder"`; `SubtitleRenderer`
uses `"subtitle_renderer"` -- one `ModuleRun` per `SubtitleRenderer.run()`
call covers SRT export AND (when requested) burn-in together, since both
are one caller-facing operation with one shared failure mode to record.

### UTF-8 SRT exporter (requirement #11)

`app/captions/srt.py`'s `render_srt(manifest) -> str` is a pure function:
cue blocks (`<number>\n<start> --> <end>\n<text>`) in `manifest.cues`'
own list order (never re-sorted -- `CaptionManifest`'s own validator
already enforces chronological order), joined by exactly one blank line
(`"\n\n"`), with exactly one trailing `"\n"` after the final block's own
text -- i.e. no superfluous trailing blank line after the last cue.
Timestamps are `HH:MM:SS,mmm`, computed via plain integer `divmod`
chains with no capping on the hours component, so timelines beyond 1
hour (confirmed by test up to 10 hours) format correctly with no special
casing. No styling tags, no HTML-escaping (ordinary SRT text needs
neither). Exact-string-compared in
`tests/test_srt_exporter.py` for every one of these behaviors, including
Vietnamese Unicode and multiline authored text.

### SRT artifact/output and paths (requirements #12-13)

```python
class SubtitleFileAsset(MotilyModel):   # app/models/subtitle.py
    id: UUID
    project_id: UUID
    caption_manifest_id: UUID
    format: str = "SRT"                 # the only supported format this phase
    file_path: str                      # relative to SubtitleFileStore.root
    cue_count: int
    total_duration_ms: int
    file_size_bytes: int
    encoding: str = "utf-8"
    created_at: datetime
```

`app/captions/storage.py`'s `SubtitleFileStore` mirrors
`app/audio/storage.py`'s `AudioFileStore` exactly: the same path-
traversal/absolute-path guard (`_validate_relative_path`) and the same
atomic temp-write-then-rename pattern, so a failure never leaves a
partial SRT file at the final path. SRT bytes are never stored in
SQLite -- only the relative path, in `SubtitleFileAsset`.
`SubtitleRenderer` writes to `{project_id}/{caption_manifest_id}/
captions.srt`, mirroring `VideoRenderer`'s own `{project_id}/
{timeline_manifest_id}/video.mp4` convention. Only SRT is supported in
Phase 31 -- ASS is used PURELY as an internal `force_style` override
mechanism for `ffmpeg`'s own `subtitles` filter during burn-in (see
below), never exported or persisted as a public artifact format.

### Caption presentation settings (requirement #14)

```python
class CaptionRenderSettings(MotilyModel):   # app/captions/models.py
    font_family: str | None = None          # None lets fontconfig/libass pick its own default
    font_size: int = 28
    bottom_margin: int = 40
    max_lines: int = 2
    outline_width: int = 2
    alignment: str = "BOTTOM_CENTER"         # or "TOP_CENTER"
```

Deliberately minimal, per this phase's own explicit exclusions: no
per-word color, no karaoke, no arbitrary x/y animation, no bouncing
text, no per-speaker themes, no arbitrary shadow stacks. `max_lines` is
stored as a declared preference but is NOT enforced by an active
line-wrapping engine in this phase (see requirement #19 below) -- no
"arbitrary typography engine" was built to honor it precisely; `libass`'s
own default visual wrapping (based on video width) applies instead.

### Vietnamese/font policy (requirement #15)

Verified empirically on this development environment before writing any
production code: `ffmpeg -filters` reports both `subtitles` and `ass`
(both backed by `libass`), and `ffmpeg -version`'s own configuration
string shows `--enable-libass --enable-fontconfig --enable-libfreetype
--enable-libfribidi --enable-libharfbuzz` -- a full Unicode/complex-
script-capable build. A direct burn-in test with the Vietnamese string
`"Xin chào, đây là phụ đề tiếng Việt."` rendered every diacritic
correctly using the system's own Arial (resolved via Windows
`directwrite`/fontconfig, logged as `fontselect: (Arial, 400, 0) ->
ArialMT`) -- no font was downloaded, packaged, or shipped by this
project; `CaptionRenderSettings.font_family=None` simply lets that same
system resolution happen. If no `subtitles`/`ass` filter capability
exists locally, `CaptionBurnInRenderer` raises
`SubtitleFilterUnavailableError` before building any command -- SRT
generation remains completely valid regardless (it has no ffmpeg
dependency at all), and the burn-in integration test skips cleanly
(`tests/test_caption_burn_in_integration.py`'s own `pytestmark`).

### Burn-in execution boundary and behavior (requirements #16-17)

`app/video_encoder/`'s own audio/video graph (Phase 28/29/30) is
completely untouched -- burn-in is a wholly separate execution wrapper,
`app/captions/burn_in.py`'s `CaptionBurnInRenderer`, consuming an
already-encoded MP4 + an already-exported SRT as pure, already-resolved
inputs (mirroring `VideoEncoder`'s own "every path must already exist"
boundary). Opt-in end to end: `SubtitleRendererInput.burn_in_settings`
defaults to `None`, in which case `SubtitleRenderer` never loads an
`EncodedVideoAsset` and never invokes `ffmpeg` for burn-in at all (SRT
export is unconditional and has zero coupling to it).

Output is a NEW, separate model, `CaptionedVideoAsset` -- never an
in-place rewrite of `EncodedVideoAsset` -- since burn-in produces a
genuinely different pixel stream from a different source (an
already-encoded MP4 + an SRT), not merely added metadata on the same
file:

```python
class CaptionedVideoAsset(MotilyModel):   # app/models/subtitle.py
    id: UUID
    project_id: UUID
    source_encoded_video_asset_id: UUID
    caption_manifest_id: UUID
    subtitle_file_asset_id: UUID
    file_path: str
    container: str = "mp4"
    video_codec: str
    audio_codec: str
    audio_stream_copied: bool
    width: int
    height: int
    fps: int
    duration_ms: int
    file_size_bytes: int
    created_at: datetime
```

Video is always re-encoded (`-c:v libx264 -pix_fmt yuv420p` -- the
`subtitles` filter modifies pixel data, so there is no way to avoid a
re-encode); the FPS flag (`-r`) is deliberately never set explicitly, so
the source's own frame rate passes through unchanged (confirmed by
`test_command_never_sets_fps_explicitly_preserving_source` and the real
integration test's own `fps == 30` assertion against a 30fps source).
Audio is always stream-copied (`-c:a copy`), never re-encoded --
documented here per requirement #17's own ask: this pipeline's own
inputs are always an AAC-in-MP4 file this module itself never chose the
codec for, so a copy is both safe (no unsupported-codec risk) and
strictly more deterministic than a second lossy pass, and it guarantees
narration/music/SFX timing and content are untouched by construction
(there are no audio samples to alter). Canvas and duration are confirmed
approximately/exactly unchanged via a post-encode `ffprobe` call
(`CaptionBurnInRenderer._probe_output`), populating the real measured
width/height/fps/duration/codecs into `CaptionBurnInResult` rather than
trusting the input's own declared values.

### FFmpeg subtitle path escaping (requirement #18)

The `subtitles` filter's own `filename` argument lives inside a `-vf`
filtergraph mini-language whose only special characters are `:`, `'`,
and (as an escape character) `\`. `_escape_subtitle_filter_path`
converts backslashes to forward slashes (Windows paths work fine with
forward slashes in `ffmpeg`), wraps the whole path in single quotes, and
escapes a drive-letter colon as `\:` so it is never misread as the
option-separator colon. Confirmed empirically against real `ffmpeg`
(this exact escaping, no shell involved -- always an argument list,
never `shell=True`) to correctly handle: spaces, parentheses, Windows
drive letters, backslashes, and Vietnamese Unicode directory/file names.

A literal single quote in the path is explicitly UNSUPPORTED: confirmed
empirically (multiple escaping variants attempted, including the
shell-idiom `'\''` sequence) that this local `ffmpeg`/`libass` build
cannot reliably preserve one inside a `subtitles` filter's own filename
argument -- the quote is silently dropped and, depending on position,
the REST of the filter string (including `force_style=...`) gets
absorbed into the corrupted filename argument, breaking the whole
filtergraph silently rather than loudly. Given that risk,
`_escape_subtitle_filter_path` raises `InvalidSubtitlePathError`
explicitly whenever the path contains `'`, rather than emitting a
command that might silently misparse. This is a PATH-only restriction --
the SRT's own text CONTENT may contain apostrophes freely, since that is
parsed by `libass`'s own SRT reader, entirely unrelated to this
filtergraph-argument escaping layer.

### Caption line length (requirement #19)

No aggressive automatic linguistic line-breaking was added. Authored cue
text is kept logically intact in `CaptionManifest`/the exported SRT;
`libass`'s own visual wrapping (based on the video's own width) applies
at render/burn-in time. `CaptionRenderSettings.max_lines` is stored as a
declared preference for a future phase to act on, but no wrapping helper
was implemented in Phase 31 -- the requirement's own bar for adding one
("only if necessary for readability... must break on whitespace, never
alter words, never alter punctuation, never change cue timing") was
judged not yet met by any concrete rendering problem observed in this
phase's own evaluation.

### No speaker labels (requirement #20)

`build_caption_manifest` never prepends a speaker name or label of any
kind -- `CaptionCue.text` is exactly the joined `ScriptLine.text`(s), and
if an authored script line itself already contains something
label-like, that is preserved as ordinary source text, never stripped or
reformatted.

### `scripts/evaluate_captions.py`

Extends `scripts/evaluate_audio_mix.py`'s own project (music `BED`/
`DUCK`/`LIFT`, a real `CROSSFADE` via `S2.transition_out=DISSOLVE`, a
`SLOW_ZOOM_IN` motion, two `SFX_TRIGGER` events, three locally-generated
sine-tone WAV fixtures) with the real `CaptionBuilder` and
`SubtitleRenderer` -- deliberately NOT coupled to Phase 30's own
disposable evaluation database or output path; this script owns its own
fresh, disposable `data/caption_evaluation/eval.db`. Produces
`data/caption_evaluation/motily_phase31_captions.srt` unconditionally and
`data/caption_evaluation/motily_phase31_preview.mp4` when this machine's
`ffmpeg` reports the `subtitles` filter (checked explicitly, never
assumed) -- confirmed on this development environment, producing a real
burned-caption MP4 whose crossfade/motion/music/SFX metadata (read back
from the ORIGINAL `EncodedVideoAsset`) are printed alongside the
captioned output's own `ffprobe`-measured metadata, directly
demonstrating that none of that prior execution was disturbed by adding
captions.

### What Phase 31 deliberately does not do

No STT/Whisper, no translation, no multilingual captions, no word-level
karaoke, no animated captions, no speaker diarization, no automatic
linguistic rewriting, no arbitrary subtitle typography engine, no CapCut
integration, no YouTube upload. No change to `ScriptPlan`/`VoicePlan`'s
own broad structure -- the only new persisted models are additive
(`CaptionManifest`/`CaptionCue`/`SubtitleFileAsset`/
`CaptionedVideoAsset`), and no existing field on any upstream model was
altered. No change to existing audio/video timing -- narration, music,
SFX, `CROSSFADE`, and motion-lite all execute exactly as Phase 28/29/30
already established, confirmed by re-running every one of those phases'
own focused test suites unmodified alongside this phase's own new tests.

## Phase 32 — Automated Media QC / Production Quality Gate (approved)

Adds a deterministic final layer that inspects an already-produced video
(and its optional subtitle sidecar) and decides whether it is
technically safe to hand to a human for approval. This phase does NOT
improve or modify media -- it only detects and reports technical
problems, exactly as its own goal states.

```
EncodedVideoAsset / CaptionedVideoAsset
        +
TimelineManifest
        +
CaptionManifest / SubtitleFileAsset (when present)
        v
MediaQCInspector (app/media_qc/)
        v
MediaQCReport (app/media_qc/models.py)
        v
PASS / WARN / FAIL
        v
human approval gate (ready_for_human_review)
```

### Architecture boundary (requirement #1)

`app/media_qc/` is a standalone, dependency-free package (mirrors
`app/video_encoder/`'s own role): `models.py` (contracts), `errors.py`
(the infra-vs-ordinary-defect split, see below), `probes.py`
(`FFprobeClient`/`FrameSampler`/`AudioAnalyzer`), `rules.py` (one pure
function per check), `inspector.py` (`MediaQCInspector`, orchestrates
`rules.py` into one `MediaQCReport`). `app/renderers/media_qc/` is the
artifact-driven wrapper (mirrors `app/renderers/video/`'s own role):
loads already-produced artifacts, verifies freshness, delegates to the
standalone inspector, persists the result. The inspector itself never
queries a database, a provider, or an LLM -- every path/expected value it
receives (via `MediaQCRequest`) must already be resolved by its caller.

### QC result model and status policy (requirements #2-3)

```python
class QCStatus(str, Enum):      # app/media_qc/models.py
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"

class QCCheckResult(MotilyModel):
    check_id: str
    status: QCStatus
    message: str
    measured_value: str | None = None   # always a formatted string --
    expected_value: str | None = None   # never a numeric union type
    details: str | None = None

class MediaQCReport(MotilyModel):
    id: UUID
    project_id: UUID
    source_video_asset_id: UUID         # EncodedVideoAsset.id or CaptionedVideoAsset.id
    timeline_manifest_id: UUID
    caption_manifest_id: UUID | None = None
    subtitle_file_asset_id: UUID | None = None
    overall_status: QCStatus
    checks: list[QCCheckResult]
    created_at: datetime
```

`overall_status` is never independently settable in a way that could
disagree with `checks`: its own `model_validator` recomputes
`_derive_overall_status(checks)` (FAIL dominates WARN dominates PASS)
and rejects any mismatch outright -- confirmed by
`test_caller_cannot_set_inconsistent_overall_status`. `MediaQCReport.
build(...)` is the preferred construction path, deriving this
automatically so callers never need to compute the dominance rule by
hand. There is no numeric "quality score" anywhere in this model --
every check is a binary-plus-severity technical measurement: FAIL means
a technical defect likely makes the deliverable invalid/unusable, WARN
means the media remains playable but deserves human review, PASS means
the check was satisfied.

### Probe architecture (requirement #19)

`app/media_qc/probes.py` centralizes every subprocess call this phase
makes, rather than scattering them through `rules.py`:

- `FFprobeClient.probe(path) -> MediaProbeResult` (`duration_ms`,
  `width`, `height`, `fps`, `video_codec`, `audio_codec`,
  `pixel_format`, `video_stream_count`, `audio_stream_count`, plus the
  raw parsed JSON kept available for diagnostics, never persisted).
- `FrameSampler.sample_luminances_and_differences(path, duration_ms,
  count) -> (luminances, differences)`.
- `AudioAnalyzer.analyze(path) -> (mean_volume_db, max_volume_db)`.

All three use `subprocess`-argument-list calls only, never `shell=True`,
and share the exact `FFmpegNotFoundError`/`FFprobeNotFoundError`
executable-resolution pattern already established in
`app/video_encoder/encoder.py`/`app/captions/burn_in.py`. `MediaProbeResult`
is a plain `@dataclass`, not a `MotilyModel` -- it is an internal data-
transfer object, never persisted as a domain artifact.

**FPS is converted exactly, never string-compared** (requirement #6):
ffprobe's own rational `r_frame_rate` (e.g. `"30000/1001"` or `"30/1"`)
is split on `/` and converted via true integer division
(`_parse_frame_rate`), confirmed exact against `30000/1001 ≈
29.97002997` in `test_parse_rational_ntsc_fps`. `check_fps` then compares
within a small absolute tolerance (`fps_tolerance`, default 0.01).

### Video checks (requirements #4, #7-9, #18)

Every check derives its expected value from already-persisted metadata
-- `EncodedVideoAsset`/`CaptionedVideoAsset` for codecs/canvas/fps/
duration, `TimelineManifest.total_duration_ms` for the authoritative
timing comparison -- never a hardcoded fixture resolution:

| Check | Source of "expected" | FAIL condition |
|---|---|---|
| `QC_VIDEO_EXISTS` | -- | file missing |
| `QC_VIDEO_NONZERO` | -- | file is 0 bytes |
| `QC_VIDEO_DECODE` | -- | ffprobe cannot read the container |
| `QC_VIDEO_STREAM_PRESENT` | -- | no video stream |
| `QC_AUDIO_PRESENT` | -- | no audio stream |
| `QC_VIDEO_CODEC` | asset's own `video_codec` | mismatch |
| `QC_AUDIO_CODEC` | asset's own `audio_codec` | mismatch |
| `QC_VIDEO_PIXFMT` | `"yuv420p"` (encoder default; `EncodedVideoAsset` does not itself record pixel format, so the encoder's own known default is used per this requirement's own fallback) | mismatch -- FAIL, not WARN (device compatibility is part of the production contract) |
| `QC_VIDEO_FPS` | asset's own `fps` | outside `fps_tolerance` |
| `QC_VIDEO_CANVAS` | asset's own `width`/`height` | mismatch, non-positive, or odd (yuv420p/libx264 needs even chroma-plane dimensions) |
| `QC_VIDEO_DURATION` | `TimelineManifest.total_duration_ms` | outside `duration_tolerance_ms` (100ms default) -- outright FAIL, no WARN band, per this requirement's own simpler preferred policy |
| `QC_VIDEO_STREAM_DURATION_SANITY` | expected duration | reported duration is `<= 0` or implausibly (>10x) larger than expected -- a coarser safety net distinct from the precise duration check, catching e.g. a stray unrelated file |

File integrity (requirement #18) is exactly this same file-existence/
decode/stream-presence sequence -- a file that exists but cannot be
decoded is `QC_VIDEO_DECODE` FAIL, and every downstream check that would
need a successful probe is simply never attempted (not re-reported as a
second, redundant failure).

### Visual sampling: black/frozen-frame detection (requirements #10-11)

`FrameSampler` extracts exactly `sample_frame_count` (default 5) frames
at deterministic, evenly-spaced positions between 10% and 90% of the
video's own duration -- `_sample_positions_ms` generalizes to any sample
count but produces exactly `[10%, 30%, 50%, 70%, 90%]` for the default
of 5, matching this requirement's own preferred example precisely. Never
a full per-frame scan. Extraction uses the same `ffmpeg -ss <t> -i
<path> -frames:v 1 <out>.png` idiom already used by every prior phase's
own evaluation scripts, into a `tempfile.TemporaryDirectory` that is
always cleaned up via its own context-manager guarantee (success or
failure) -- no extracted frame is ever persisted as a production
artifact or leaked on disk (requirement #20).

Per-frame mean luminance (`Pillow`'s own `.convert("L")` + `ImageStat.
Stat(...).mean[0]`, a 0-255 grayscale average) and consecutive-frame
mean absolute difference (`ImageChops.difference(...)` + the same
`ImageStat` mean) are the ONLY two measurements computed -- no OCR, no
CV models, no aesthetic judgment of any kind:

- **`QC_VIDEO_BLACK`**: FAIL only when the FRACTION of sampled frames at
  or below `black_luminance_threshold` (default 16.0) meets or exceeds
  `black_frame_fraction_threshold` (default 1.0, i.e. ALL sampled
  frames) -- a single intentionally-dark frame never fails the whole
  video, confirmed by `test_single_dark_frame_does_not_fail_whole_video`.
- **`QC_VIDEO_FROZEN`**: WARN (never FAIL) when every consecutive
  sampled-frame pair's difference is at or below
  `frozen_frame_difference_threshold` (default 2.0) -- this channel's
  own intentional static/limited-motion style means whole-program
  sameness deserves a human look, not automatic rejection. Confirmed by
  a real integration test (`test_static_single_color_video_warns_frozen_not_fail`)
  encoding one solid-color 6-second video through real `ffmpeg` and
  asserting `WARN`, never `FAIL`, with `overall_status` still
  `ready_for_human_review=True`.

Both thresholds were chosen and validated empirically during this
phase's own development: a genuinely black `Image.new("RGB", ..., (0,0,0))`
measures exactly `0.0`; this project's own real still-frame content
(diagram overlays, composed backgrounds) measures well into the 70s-80s
range even for fairly dark scenes; two byte-identical frames measure a
difference of exactly `0.0`, while any real color/content change
measures far above the threshold.

### Audio checks (requirements #12-13)

`AudioAnalyzer` runs `ffmpeg -i <path> -af volumedetect -f null -` once
and parses `mean_volume`/`max_volume` (in dBFS) from its own stderr
output via a small regex -- no custom DSP, no third-party audio library.
Empirically verified: a true silent PCM stream (`anullsrc`) reports
approximately `-91.0 dB` for both values (not literally `-inf`, though
`AudioAnalyzer` handles that string too defensively), comfortably below
the default `-60.0 dB` silence threshold; an audible 440Hz tone reports
around `-21 dB`.

- **`QC_AUDIO_SILENCE`**: FAIL when `mean_volume_db <= silence_threshold_db`
  (default -60.0 dB) -- the entire program is effectively silent.
- **`QC_AUDIO_CLIPPING`**: FAIL when `max_volume_db >= clipping_threshold_db`
  (default 0.0 dBFS) -- a technically impossible/clipped peak. This
  clean, deterministic `ffmpeg`-native measurement made clipping
  detection straightforward to include (the requirement's own "skip if
  not reliably measurable" escape hatch was not needed).

Neither check performs loudness mastering, LUFS targeting, mix-quality
judgment, or narration-intelligibility inspection via STT -- exactly per
this phase's own exclusions.

### Subtitle checks (requirements #14-15)

Only run when a project has a `SubtitleFileAsset`/`CaptionManifest` pair
at all -- captions are entirely optional per project, and their absence
produces no subtitle checks (never a failure). `CaptionManifest` is the
source of truth throughout; the SRT is re-parsed via a NEW function,
`app/captions/srt.py`'s `parse_srt()` -- the exact inverse of Phase 31's
own `render_srt()` for this project's own deterministic output shape
(never a general-purpose SRT parser tolerant of third-party quirks).
This was the one small, natural extension to an existing package this
phase needed; no new SRT-handling module was created.

| Check | Verifies |
|---|---|
| `QC_SUBTITLE_EXISTS` | SRT file exists |
| `QC_SUBTITLE_UTF8` | SRT decodes as valid UTF-8 |
| `QC_SUBTITLE_CUE_COUNT` | parsed cue count == `len(caption_manifest.cues)` |
| `QC_SUBTITLE_TIMESTAMPS_VALID` | every cue: non-negative start, end > start, chronological/non-overlapping across cues |
| `QC_SUBTITLE_TIMELINE_BOUNDS` | every cue's own `end_ms <= caption_manifest.total_duration_ms` |
| `QC_SUBTITLE_TEXT_NONEMPTY` | no cue has blank/whitespace-only text |
| `QC_SUBTITLE_MATCH` | the on-disk SRT text is byte-for-byte `render_srt(caption_manifest)` -- the strictest check, catching ANY stale/wrong subtitle artifact directly, independent of the more granular structural checks above |

`test_stale_subtitle_produces_match_failure` (a stale SRT regenerated
from an OLD `CaptionManifest`, then compared against the CURRENT one)
confirms `QC_SUBTITLE_MATCH` fails even when every individual cue still
parses validly -- this is precisely the scenario requirement #15 asks
this check to catch. No OCR is used to verify visible caption text
anywhere (requirement #16) -- burned-in caption QC is limited to
container/stream/duration/audio checks on the resulting video plus this
same text-identity comparison against the SRT sidecar, never pixel-level
subtitle reading.

### Artifact freshness (requirement #17)

`MediaQCRenderer` re-verifies the EXACT SAME full freshness chain every
prior renderer in this project already checks (ScriptPlan → VoicePlan →
VisualPlan → AssemblyPlan → TimelineManifest, cross-checked against the
current VoiceRenderManifest/VisualRenderManifest ids), PLUS:

- the `EncodedVideoAsset` being inspected must reference the CURRENT
  `TimelineManifest.id` (`StaleEncodedVideoAssetError` otherwise);
- IF a `CaptionManifest` exists, it must reference the current
  `TimelineManifest.id` (`StaleCaptionManifestError`) -- but a MISSING
  one is not an error;
- IF a `SubtitleFileAsset` exists, it must reference the current
  `CaptionManifest.id` (`StaleSubtitleFileAssetError`);
- IF a `CaptionedVideoAsset` exists, it must reference the current
  `EncodedVideoAsset`/`CaptionManifest`/`SubtitleFileAsset` ids all
  together (`StaleCaptionedVideoAssetError`).

No upstream artifact is ever auto-rebuilt to fix a stale chain -- the
caller must rerun the appropriate builder/renderer first, exactly like
every prior phase.

### Infrastructure vs. ordinary-defect errors (requirements #23-24)

`app/media_qc/errors.py` draws this boundary explicitly:
`FFmpegNotFoundError`/`FFprobeNotFoundError`/`QCTempDirError` are
infrastructure errors that MAY propagate/crash the caller -- no check of
any kind can run without a working `ffmpeg`/`ffprobe` or a writable temp
directory. `MediaProbeError`, by contrast, means one SPECIFIC file could
not be read/decoded (missing, corrupt, zero streams) -- an ordinary,
expected outcome for a broken deliverable, ALWAYS caught by
`MediaQCInspector.inspect()` and converted into a FAIL `QCCheckResult`,
never propagated. `inspect()` therefore never raises for a missing
video, a corrupt MP4, a codec/duration/canvas mismatch, silence, or a
stale-looking subtitle -- every one of `tests/test_media_qc_inspector.py`'s
own "diagnostic, never fail-fast" tests confirms this directly (a missing
file, a probe failure, a frame-sampling failure, and an audio-analysis
failure each produce a complete FAIL report, never an exception).

### `ModuleRun` lifecycle (requirement #25)

`MediaQCRenderer` uses RUNNING → SUCCESS whenever the inspection ITSELF
completed -- REGARDLESS of whether the resulting `MediaQCReport` says
PASS/WARN/FAIL, confirmed directly by
`test_module_run_success_regardless_of_report_status`. `FAILED` is
reserved for the inspector genuinely being unable to run at all (an
infrastructure error, or `save_artifact` itself failing), confirmed by
`test_module_run_failed_on_infrastructure_crash` (a fake inspector that
raises `RuntimeError`). No project-state transition, exactly like every
prior renderer.

### Human approval gate (requirement #26)

`MediaQCReport.ready_for_human_review` (a plain Python `@property`, NOT
a pydantic `computed_field`) implements the exact policy: PASS → `True`,
WARN → `True`, FAIL → `False`. Deliberately NOT a `computed_field`: doing
so would serialize into `model_dump_json()`'s own output, which
`get_artifact`'s `model_validate_json()` re-hydration would then reject
outright under this model's own `extra="forbid"` policy -- confirmed
empirically before this design was finalized. This is a gate for human
review only; QC never auto-publishes even on a clean PASS.

### `scripts/evaluate_media_qc.py`

Extends `scripts/evaluate_captions.py`'s own finished production chain
(music `BED`/`DUCK`/`LIFT`, a real `CROSSFADE`, a `SLOW_ZOOM_IN`, two
`SFX_TRIGGER` events, burned-in captions) -- deliberately NOT coupled to
Phase 31's own disposable evaluation database or output path; this
script owns its own fresh `data/media_qc_evaluation/eval.db` -- with the
real `MediaQCRenderer`, producing exactly one JSON report:
`data/media_qc_evaluation/motily_phase32_qc_report.json` (stable-key-
ordered, human-readable, no raw frame/audio data persisted). On this
development environment's own real fixture, all 23 checks PASS
(`overall_status=PASS`, `ready_for_human_review=True`) -- the fixture's
own visual content changes across its three segments (color background,
diagram overlay, composition), so `QC_VIDEO_FROZEN` legitimately PASSES
rather than WARNs.

### What Phase 32 deliberately does not do

No scientific/narrative correctness checking, no aesthetics/thumbnail
scoring, no OCR, no STT, no automatic remediation of any defect found,
no publishing, no YouTube API, no analytics. No media file is ever
regenerated, re-encoded for correction, or otherwise altered by this
phase -- QC only reads already-produced artifacts and writes a report.
No arbitrary "quality score" -- every check is a narrow, explicit
PASS/WARN/FAIL technical measurement with a stable, documented
`check_id`.

## Phase 33 — Deterministic Production Orchestrator & Approval Gates (approved)

Adds the first real production orchestrator: a coordinator that executes
the existing pipeline modules through an explicit, deterministic
dependency graph, respecting human approval gates and never skipping a
required upstream artifact. This phase does NOT redesign any existing
engine, does not merge modules into one service, adds no agent/LLM
decision-making of its own, and introduces no background task queue --
it is one synchronous, blocking call per `run_until()`.

```
ProductionRunRequest (project_id, target_node)
        v
ProductionGraph.ancestors_closure(target_node)   -- static, code-defined DAG
        v
for each node, in stable topological order:
    dependencies SUCCEEDED this run?  -- else BLOCKED (BLOCKED_DEPENDENCY)
        v
    load_current() + is_fresh()?  -- reuse, no re-execution
        | (else)
    execute()  -- the real existing renderer/builder, via one adapter
        v
    gate_after declared?  -- gate_ok() precondition, then gate_decision_for()
        v
    SUCCEEDED / WAITING_APPROVAL / BLOCKED(GATE_REJECTED)
        v
ProductionRun (persisted trace) -- next run_until()/resume() continues from here
```

### Architecture boundary (requirements #1-2)

`app/orchestration/{errors,models,graph,registry,adapters,gates,runner}.py`
is a standalone package that knows only dependency/freshness/gate/
executability *facts* -- never how an individual engine reasons, draws,
or builds a filter graph. It coordinates `app/renderers/*`/`app/engines/*`
modules already built in Phases 1-32; it does not replace, wrap into one
service, or change the public behavior of any of them. `app/services/
production.py` was considered but not added -- `ProductionRunner` itself
is already a thin enough orchestration surface that a further service
wrapper would add indirection without a corresponding need in this
phase.

### Production node contract (requirement #3)

19 stable node ids, matching the project's own real pipeline stages
(inspected before coding -- no duplicate concept invented):
`IDEA, RESEARCH_R0, FEASIBILITY, RESEARCH_R1, NARRATIVE, PACKAGING_P0,
SCRIPT, SCRIPT_VERIFY, VOICE_PLAN, VISUAL_PLAN, ASSEMBLY_PLAN,
PACKAGING_P1, VOICE_RENDER, VISUAL_RENDER, TIMELINE, VIDEO_RENDER,
CAPTION_BUILD, SUBTITLE_EXPORT, MEDIA_QC`. `SUBTITLE_EXPORT` deliberately
covers both SRT export and optional caption burn-in as one node -- the
real `SubtitleRenderer.run()` always performs SRT export in one call and
optionally burns in captions in the *same* call, so a separate
`SUBTITLE_BURN_IN` node would either duplicate the export or require
awkward cross-node state sharing for no benefit; `MediaQCAdapter`
independently discovers whether a `CaptionedVideoAsset` exists, exactly
as the real `MediaQCRenderer` already does, so nothing downstream is
lost by this collapse.

### Dependency graph and topological order (requirements #4-5)

```python
# app/orchestration/graph.py
class ProductionNodeDefinition:   # frozen dataclass
    node_id: str
    dependencies: tuple[str, ...] = ()
    gate_after: ApprovalGateType | None = None

class ProductionGraph:
    def __init__(self, definitions: list[ProductionNodeDefinition]): ...
    def get(self, node_id) -> ProductionNodeDefinition: ...
    def topological_order(self) -> list[str]: ...
    def ancestors_closure(self, target_node: str) -> list[str]: ...
```

Dependencies are a static, code-defined table (`app/orchestration/
adapters.py`'s own `_NODE_DEPENDENCIES`) -- never inferred from an
artifact's own foreign-key fields at runtime (those fields are used only
to check *freshness*, never to decide *order*). Validated once at
construction: duplicate node id (`DuplicateNodeError`), unknown
dependency (`UnknownDependencyError`), self-dependency
(`SelfDependencyError`), and cycle (`ProductionGraphCycleError`, naming
every unordered remaining node) -- all infrastructure/configuration
errors, never a per-project outcome. Topological order uses Kahn's
algorithm with a `sorted()` tie-break at every step (both the initial
zero-in-degree set and every newly-ready set discovered while
processing), so two independent nodes with no dependency relationship
always appear in the same relative order across repeated calls, process
restarts, and Python versions -- confirmed by
`test_topological_order_is_stable_tie_break_across_independent_nodes`.
`ancestors_closure(target)` returns the target plus every transitive
dependency, filtered through that same stable order, and never a
descendant -- `run_until(target)` can therefore never execute beyond
what was actually requested (requirement #8).

### Node status model (requirements #6-7)

```python
# app/orchestration/models.py
class ProductionNodeStatus(str, Enum):
    PENDING, BLOCKED, READY, RUNNING, SUCCEEDED, FAILED, WAITING_APPROVAL, SKIPPED

class BlockedReason(str, Enum):
    BLOCKED_DEPENDENCY, WAITING_GATE, GATE_REJECTED, QC_NOT_READY,
    STALE_UPSTREAM, NODE_FAILED, NODE_NOT_WIRED

class NodeRunRecord(MotilyModel):
    node_id: str
    status: ProductionNodeStatus
    artifact_id: str | None = None
    module_run_id: UUID | None = None
    executed_this_run: bool = False
    reused_existing_artifact: bool = False
    reason: BlockedReason | None = None
    message: str | None = None

class ProductionRunStatus(str, Enum):
    RUNNING, WAITING_APPROVAL, SUCCEEDED, FAILED, BLOCKED

class ProductionRun(MotilyModel):
    id: UUID
    project_id: UUID
    target_node: str
    status: ProductionRunStatus
    node_states: dict[str, NodeRunRecord]
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    stop_reason: str | None = None
    waiting_gate: str | None = None
```

`SUCCEEDED` is deliberately not derived from `ModuleRunStatus` alone: a
node is `SUCCEEDED` whether its adapter executed this run
(`executed_this_run=True`) or its artifact was simply reused
(`reused_existing_artifact=True`, no `ModuleRun` produced at all this
run) -- `NodeRunRecord`'s own validator rejects a record claiming both
simultaneously. `WAITING_APPROVAL` can follow a fully successful module
execution (the module itself did not fail; a human decision is simply
still pending). `BLOCKED` covers every "cannot proceed" case that is not
a module failure: an unsatisfied dependency, a rejected gate, or an
unmet QC precondition -- distinguished via `reason`, never only a
free-text `message`. `ProductionRun` holds only orchestration metadata
and references -- no artifact payload is ever duplicated into it.

### Approval gates (requirements #12-18)

```python
# app/orchestration/models.py
class ApprovalGateType(str, Enum):
    IDEA_APPROVAL, RESEARCH_APPROVAL, SCRIPT_APPROVAL, FINAL_MEDIA_APPROVAL

class ApprovalDecisionType(str, Enum):
    APPROVED, REJECTED

class ApprovalDecision(MotilyModel):
    id: UUID
    project_id: UUID
    gate_type: ApprovalGateType
    subject_artifact_id: UUID
    decision: ApprovalDecisionType
    decided_at: datetime
    note: str | None = None

# app/orchestration/gates.py
def approve_gate(engine, graph, project_id, gate_type, subject_artifact_id, *, note=None) -> ApprovalDecision: ...
def reject_gate(engine, graph, project_id, gate_type, subject_artifact_id, *, note=None) -> ApprovalDecision: ...
def gate_decision_for(engine, project_id, gate_type, subject_artifact_id) -> ApprovalDecision | None: ...
```

This is a **new**, parallel approval mechanism -- it does not replace or
change `app/review/service.py`'s own existing `HumanApproval`/
`ApprovalRow` gates (`IDEA_REVIEW`, `NARRATIVE_REVIEW`,
`SCRIPT_REVIEW`, etc.), which remain exactly as they were and continue
to drive `ProjectState` transitions with no artifact-id binding. The new
mechanism exists because those existing gates have no concept of
binding a decision to one *exact* artifact id, which `FINAL_MEDIA_APPROVAL`
(a genuinely new gate -- there is no prior `ProjectState`/review-service
concept for "approve the finished, QC'd video") needs. `ApprovalGateType`
documents `IDEA_APPROVAL`/`RESEARCH_APPROVAL`/`SCRIPT_APPROVAL` for a
complete vocabulary, but no node in the registered graph declares them
via `gate_after`, and Phase 33 does not enforce them through
`ApprovalDecision`: `project.state == ProjectState.MVP_COMPLETE`
(already required by every real adapter as its first check, since every
wired renderer's own `RendererStateError` enforces it) is already
conclusive proof the entire upstream review-service gate chain leading
to it was satisfied -- adding a second, competing mechanism for gates
the codebase already enforces correctly was judged unnecessary
duplication, not a gap.

`approve_gate`/`reject_gate` validate only that `gate_type` is declared
by some node in the graph (`UnknownApprovalGateError` otherwise); they
do NOT check that `subject_artifact_id` is the artifact currently at
that node -- that comparison happens where it actually matters, inside
`ProductionRunner`'s own gate-satisfaction check, so a decision recorded
against a since-superseded artifact id simply never matches the current
one and has no effect (requirement #14, "stale approval invalidation").
Duplicate-decision policy (requirement #33): append-only, most-recent-
by-insertion-order wins -- `get_latest_approval_decision` always queries
`ORDER BY id DESC LIMIT 1`, so a human who reconsiders records a new row
rather than mutating history.

### QC gate semantics and final completion (requirements #16-17)

`MediaQCAdapter.gate_ok(report)` returns `report.ready_for_human_review`
(PASS/WARN → `True`, FAIL → `False`). A `FAIL` blocks the run at
`BLOCKED`/`QC_NOT_READY` *before* `FINAL_MEDIA_APPROVAL` is ever even
requested -- the gate is never offered, not merely left pending. A
project reaches `ProductionRunStatus.SUCCEEDED` only when: the final
media artifact exists, `gate_ok()` permits review, AND the latest
`ApprovalDecision` for that *exact* artifact id is `APPROVED`. Until
then the run is `WAITING_APPROVAL` (gate open, decision pending) or
`BLOCKED` (`QC_NOT_READY`/`GATE_REJECTED`) -- a successful video encode
alone is never interpreted as full production success.

### Adapter boundary and freshness (requirements #10, #19-22)

```python
# app/orchestration/registry.py
class NodeAdapter(Protocol):
    node_id: str
    def load_current(self, ctx: ExecutionContext) -> object | None: ...
    def is_fresh(self, artifact, ctx: ExecutionContext) -> bool: ...
    def execute(self, ctx: ExecutionContext) -> NodeExecutionResult: ...
    def gate_ok(self, artifact) -> bool: ...
```

Seven real adapters (`app/orchestration/adapters.py`) wrap the existing
Phase 24-32 renderers/builders unchanged: `VoiceRenderAdapter`,
`VisualRenderAdapter`, `TimelineAdapter`, `VideoRenderAdapter`,
`CaptionBuildAdapter`, `SubtitleExportAdapter`, `MediaQCAdapter`.
Freshness is deliberately NOT centralized into one rule engine
(requirement #10): each adapter's own `is_fresh` re-reads its artifact's
own already-declared foreign-key id fields (e.g.
`TimelineManifest.assembly_plan_id`, `.voice_render_manifest_id`,
`.visual_render_manifest_id`) and compares them against the CURRENT
upstream artifacts, re-fetched via the same `get_artifact` calls each
renderer's own private `_verify_*_is_fresh` method already uses
internally -- a thin, honest reuse of information the artifact already
carries. Because the runner processes nodes in strict topological order,
by the time a downstream adapter's `is_fresh` runs, every ancestor has
already been confirmed current *this same run* -- so a single-level
"does my own recorded id match the current upstream artifact" check is
correct and sufficient; no adapter needs to walk the full upstream chain
itself.

The twelve LLM-based creative-planning nodes (`IDEA` through
`PACKAGING_P1` -- every one of their own `engine.py` modules imports
`app.llm` directly, confirmed by inspection before writing this phase)
are NOT given real adapters. Each instead gets a shared
`UnwiredNodeAdapter(node_id, artifact_type, model_class)`
(`app/orchestration/registry.py`): `load_current`/`gate_ok` work
normally, `is_fresh` is deliberately shallow (existence-only, always
`True` once found -- a real wired adapter downstream independently
re-verifies the same upstream chain and catches true staleness there,
just one node later), and `execute()` always raises `NodeNotWiredError`
naming the node and pointing at the existing engine + review-service
flow. This is the explicit, documented `EXTERNAL/UNWIRED` boundary
requirement #22 asked for, rather than a brittle adapter written merely
to claim false completeness. `app/orchestration/adapters.py`
(`build_default_graph()`/`build_default_adapters()`) registers all 19
nodes in the graph (for an accurate, complete dependency picture) but
only 7 are actually executable this phase -- confirmed by
`WIRED_NODE_IDS`/`UNWIRED_NODE_IDS`.

### Runner algorithm (requirements #23-27)

```python
# app/orchestration/runner.py
class ProductionRunner:
    def run_until(self, project_id, target_node, *, run_id=None, ctx=None) -> ProductionRun: ...
    def resume(self, run_id, *, ctx=None) -> ProductionRun: ...
```

For each node in `graph.ancestors_closure(target_node)`, in stable
topological order: (1) every dependency must already be `SUCCEEDED`
*this run* -- otherwise `BLOCKED`/`BLOCKED_DEPENDENCY`, and the node is
never even given to its adapter; (2) `load_current()` + `is_fresh()` --
if both hold, reuse with no execution; otherwise (3) `adapter.execute()`
-- `NodeNotWiredError` becomes `BLOCKED`/`NODE_NOT_WIRED`, any other
exception becomes `FAILED`/`NODE_FAILED` (the module's own failure,
never swallowed, never re-raised past `run_until()`); (4) if the node
declares `gate_after`, `gate_ok()` then `gate_decision_for()` decide
`SUCCEEDED`/`WAITING_APPROVAL`/`BLOCKED`(`GATE_REJECTED`) as described
above. Every node in the closure is always visited -- an unmet
dependency downstream of an earlier problem still gets its own
`BLOCKED`/`BLOCKED_DEPENDENCY` record rather than being silently
skipped, so the persisted trace always shows the complete picture
(requirement #28). The run's own terminal `ProductionRunStatus` and
`stop_reason` are always taken from the *first* non-`SUCCEEDED` node in
topological order -- a `BLOCKED_DEPENDENCY` cascade several nodes
downstream of a real `WAITING_APPROVAL`/`FAILED`/`GATE_REJECTED` node
must never override that earlier, actual cause; confirmed by
`test_run_stops_at_waiting_approval_and_resume_continues` (a rejected/
waiting node upstream, cascading `BLOCKED_DEPENDENCY` nodes downstream,
correct `WAITING_APPROVAL` run status throughout).

No background execution (requirement #24): `run_until()` is one plain,
blocking Python call -- no async queue, scheduler, daemon, or worker
pool. No hidden retry (requirement #27): a `FAILED` node is recorded
once per call and not retried within that same call; a later, separate
`run_until()`/`resume()` call independently re-evaluates every node from
scratch (via the same `load_current`/`is_fresh`/`execute` path), so a
fixed bug or a newly recorded approval naturally lets execution proceed
further next time with no explicit retry bookkeeping anywhere in the
runner.

### Idempotency and resume (requirements #25-26)

`run_until(target)` called twice with nothing changed: the first call
executes every missing node; the second finds every artifact still
`is_fresh` and reuses all of them, calling no adapter's `execute()` at
all -- confirmed by `test_second_run_reuses_all_fresh_outputs`. Passing
`run_id=<a prior run's id>` (or calling `resume(run_id)`, a thin wrapper
that looks up that run's own `target_node`) continues the *same*
`ProductionRun` row (same `id`, `created_at` preserved) rather than
allocating a new one; each call still fully recomputes every node's
status from scratch, so approving a gate and resuming naturally lets
previously-blocked downstream nodes execute for the first time without
any special "continue from where you left off" bookkeeping -- the
recomputation itself already produces that result.

### Production trace and persistence (requirements #28-29, #42-44)

`app/storage/orm.py` adds two new tables in the *same* database file
(`Base.metadata.create_all` picks them up automatically, confirmed via
the existing `init_database`/`tests/conftest.py` `engine` fixture --
no second database was created): `production_runs` (one row per
`ProductionRun`, `node_states_json` holding the full serialized
`dict[str, NodeRunRecord]`) and `approval_decisions` (one row per
`ApprovalDecision`, append-only). Both are keyed by their own `id`
rather than `(project_id, artifact_type)`, since a project accumulates
many runs/decisions over time, unlike `ArtifactRow`'s one-row-per-type
convention. New repositories `app/storage/production_runs.py`/
`app/storage/approval_decisions.py` mirror `app/storage/module_runs.py`'s
own upsert-by-id/query shape exactly; new `ProductionRunNotFoundError`
in `app/storage/errors.py`. `ProductionRun` never replaces `ModuleRun`
-- a `NodeRunRecord` references a `module_run_id` where the underlying
adapter's own renderer produced one (a reused node has none, since no
`ModuleRun` executes for it). Diagnostics use stable `BlockedReason`
codes throughout (requirement #29), never only a free-text message, so
a caller can branch on `reason` without string-matching `message`.
`app/orchestration/` adds no `ProjectState` transitions of its own --
`ProductionRun.status` is an independent execution concept, and every
existing state-machine gate (Phases 1-21's own `app/review/service.py`)
is left completely untouched.

### Source purity (requirement #30)

`tests/test_orchestration_purity.py` asserts, via a fresh subprocess
(mirroring `tests/test_timeline_builder.py::
test_no_llm_dependency_in_fresh_subprocess`) plus a static per-file
source scan, that no file directly under `app/orchestration/` -- adapters
included -- ever imports `app.llm` or a provider package. This holds for
`app/orchestration/adapters.py` too with no carve-out needed: every one
of the seven real adapters delegates to a renderer already confirmed
LLM-free (that confirmation is exactly why those seven, and not others,
were chosen for wiring in the first place).

### Real integration slice and human evaluation (requirements #40-41)

`tests/test_orchestration_integration.py` builds a real MVP_COMPLETE
project (real `ScriptPlan`/`VoicePlan`/`VisualPlan`/`AssemblyPlan`, two
`GENERATED_STILL` visual beats needing no `ti_compositor`/
`diagram_renderer`) and drives `ProductionRunner` through all seven
wired nodes for real: `FakeTTSProvider`/`FakeVisualProvider` stand in
for network TTS/image providers (never a live API call), while every
other step -- `ffmpeg` encode, `ffprobe`-based QC inspection, SRT
export, caption burn-in -- is the real local implementation. The run
reaches `WAITING_APPROVAL` at `FINAL_MEDIA_APPROVAL` with QC `PASS`/
`WARN`; approving the exact final-media artifact and calling `resume()`
reaches `SUCCEEDED`, reusing every upstream artifact with zero
re-execution. `scripts/evaluate_production_orchestrator.py` runs the
same shape as a standalone, human-readable script against its own
disposable `data/production_orchestrator_evaluation/eval.db`, printing
a full per-node trace for both passes (node/status/executed-or-reused/
artifact id) plus a summary line for each of: production run id,
first-run status, final status, executed node count (run 1), reused
node count (resume), QC status, final media artifact id, and approval
gate subject id.

### What Phase 33 deliberately does not do

No distributed workers, no background scheduling, no retry queues, no
publishing, no YouTube API, no analytics, no automatic QC remediation,
no autonomous agent/LLM planning or decision-making anywhere in
`app/orchestration/`, no automatic human approval on any gate (QC `PASS`
never auto-approves `FINAL_MEDIA_APPROVAL`), and no UI/dashboard --
`app/orchestration/gates.py`'s `approve_gate`/`reject_gate` and
`scripts/evaluate_production_orchestrator.py` are the only surfaces this
phase adds, both explicit, code-level calls. `ProjectState`'s own
existing state machine is untouched. Twelve of the nineteen registered
nodes (every LLM-based creative-planning stage) remain deliberately
unwired -- their artifacts must still be produced via the existing
engine + `app/review/service.py` flow directly, exactly as before this
phase existed.

## Phase 34 — Full Upstream Pipeline Wiring & Approval Gate Unification (approved)

Wires the twelve upstream creative/planning nodes Phase 33 left
unwired into `ProductionRunner`, so a single run now drives the entire
implemented chain end to end: IDEA through `FINAL_MEDIA_APPROVAL`. This
phase coordinates existing engines -- it does not redesign any of them,
adds no agent/LLM decision-making of its own, and `app/orchestration/`
remains exactly as provider-unaware as Phase 33 left it.

### Architecture: the execution adapter bridge (requirement #2)

```
app/orchestration/            <- still provider/LLM-unaware (unchanged)
    graph.py, registry.py (protocol only), runner.py, gates.py, models.py, adapters.py (7 downstream)

app/production_adapters/      <- NEW, outside the core, MAY import app.llm
    upstream.py                  12 real adapters (IDEA..PACKAGING_P1)
    registry.py                  combines 12 + 7 into one 19-node graph
```

Every one of the twelve upstream engines imports `app.llm`
(`LLMProvider`/`LLMSettings`/`generate_structured`) -- confirmed by
reading each `engine.py` file directly, never assumed from a phase
name. `app/production_adapters/upstream.py` is where that dependency is
allowed to live; `app/orchestration/` itself never imports it, verified
by `tests/test_orchestration_purity.py`'s existing fresh-subprocess
check (unchanged) plus a new positive check confirming
`app.production_adapters.upstream` genuinely imports `app.llm.provider`
in its own fresh subprocess -- proving the isolation is a real boundary,
not an accidental absence. `app/orchestration/adapters.py`'s own
`build_default_graph()`/`build_default_adapters()` (Phase 33) are left
completely untouched; `app/production_adapters/registry.py`'s
`build_full_graph()`/`build_full_adapters()` are the new, additional
entry point, reusing the SAME `_NODE_DEPENDENCIES` topology table by
direct import so the two graphs can never silently drift apart.

### Exact node -> engine mapping (requirement #4)

| ProductionNodeId | Engine class | Required inputs | Output artifact type | ModuleRun module | Freshness identity | Gate |
|---|---|---|---|---|---|---|
| `IDEA` | `IdeaEngine` | `ExecutionContext.initial_idea_input` (an `IdeaEngineInput`) | `idea_candidate` (`IdeaCandidate`) | `idea_engine` | Root -- always fresh once it exists | `IDEA_APPROVAL` (after) |
| `RESEARCH_R0` | `R0ResearchEngine` | current `IdeaCandidate` | `research_r0` (`ResearchR0`) | `research_r0_engine` | `ResearchR0.idea_id == IdeaCandidate.id` | none |
| `FEASIBILITY` | `FeasibilityEngine` | current `IdeaCandidate` + `ResearchR0` | `feasibility_report` (`FeasibilityReport`) | `feasibility_engine` | Latest SUCCESS ModuleRun `input_ids == [idea.id, research_r0.id]` (no FK field exists) | `RESEARCH_APPROVAL` (after) |
| `RESEARCH_R1` | `R1ResearchEngine` | current `IdeaCandidate` + `ResearchR0` + `FeasibilityReport` (must be PASS) | `research_r1` (`ResearchPackage`) | `research_r1_engine` | Latest SUCCESS ModuleRun `input_ids == [idea.id, research_r0.id, feasibility.id]` (no FK field) | none |
| `NARRATIVE` | `NarrativeEngine` | current `IdeaCandidate` + `ResearchPackage` | `narrative_plan` (`NarrativePlan`) | `narrative_engine` | Latest SUCCESS ModuleRun `input_ids == [idea.id, research_package.id]` (no FK field) | `NARRATIVE_APPROVAL` (after) |
| `PACKAGING_P0` | `PackagingP0Engine` | current `IdeaCandidate` + `ResearchPackage` + `NarrativePlan` | `packaging_prototype` (`PackagingPrototype`) | `packaging_p0_engine` | Latest SUCCESS ModuleRun `input_ids == [idea.id, research_package.id, narrative.id]` (no FK field; mirrors `app/review/service.py::_verify_packaging_is_fresh` exactly) | `PACKAGING_P0_APPROVAL` (after) |
| `SCRIPT` | `ScriptEngine` | current `IdeaCandidate` + `ResearchPackage` + `NarrativePlan` + `PackagingPrototype` | `script_plan` (`ScriptPlan`) | `script_engine` | Latest SUCCESS ModuleRun `input_ids == [idea.id, research_package.id, narrative.id, packaging.id]` (no FK field) | none |
| `SCRIPT_VERIFY` | `ScriptVerificationEngine` | current `ResearchPackage` + `NarrativePlan` + `PackagingPrototype` + `ScriptPlan` | `script_verification_report` (`ScriptVerificationReport` -- **no `id` field**) | `script_verification_engine` | Latest SUCCESS ModuleRun `input_ids == [research_package.id, narrative.id, packaging.id, script_plan.id]` (mirrors `_verify_script_verification_is_fresh` exactly) | `SCRIPT_APPROVAL` (after; spans `SCRIPT_VERIFICATION` then `SCRIPT_REVIEW`) |
| `VOICE_PLAN` | `VoicePlanningEngine` | current `ScriptPlan` (project must be `MVP_COMPLETE`) | `voice_plan` (`VoicePlan`) | `voice_plan_engine` | `VoicePlan.script_plan_id == ScriptPlan.id` | none |
| `VISUAL_PLAN` | `VisualPlanningEngine` | current `ScriptPlan` + `VoicePlan` | `visual_plan` (`VisualPlan`) | `visual_plan_engine` | `VisualPlan.script_plan_id`/`.voice_plan_id` match current | none |
| `ASSEMBLY_PLAN` | `AssemblyPlanningEngine` | current `ScriptPlan` + `VoicePlan` + `VisualPlan` | `assembly_plan` (`AssemblyPlan`) | `assembly_plan_engine` | `AssemblyPlan.script_plan_id`/`.voice_plan_id`/`.visual_plan_id` match current | none |
| `PACKAGING_P1` | `PackagingP1Engine` | current `PackagingPrototype` + `ScriptPlan` + `VisualPlan` + `AssemblyPlan` | `packaging_p1` (`FinalPackagingPlan`) | `packaging_p1_engine` | `FinalPackagingPlan.packaging_prototype_id`/`.script_plan_id`/`.visual_plan_id`/`.assembly_plan_id` match current | none |

`Project` (`app/models/project.py`) carries only seven "current pointer"
fields (`idea_candidate_id`, `research_r0_id`, `feasibility_id`,
`research_r1_id`, `narrative_plan_id`, `packaging_prototype_id`,
`script_plan_id`) -- confirmed by reading the model directly.
`app/storage/projects.py::update_artifact_reference` hard-validates
against exactly these seven; there is no `voice_plan_id`/
`visual_plan_id`/`assembly_plan_id`/`packaging_p1_id`/
`script_verification_id` field on `Project` at all, which is precisely
why five of the twelve adapters above use ModuleRun-`input_ids`
freshness instead of a Project-reference/FK comparison.

### Approval gate unification (requirements #11-16)

Six real human decision points exist in `app/review/service.py` (a
sixteen-function file, read in full before writing this phase, not
guessed from names):

| ApprovalGateType | Real `app/review/service.py` gate | Approve function | The only reject path (if any) |
|---|---|---|---|
| `IDEA_APPROVAL` | `ProjectState.IDEA_REVIEW` | `approve_idea` | none -- `revise_idea` only sends back to `IDEA_DISCOVERY` |
| `RESEARCH_APPROVAL` | `ProjectState.FEASIBILITY` | `decide_feasibility(PASS)` | `decide_feasibility(REJECT)` -> `ARCHIVED` |
| `NARRATIVE_APPROVAL` | `ProjectState.NARRATIVE_REVIEW` | `approve_narrative` | none -- `revise_narrative`/`send_narrative_back_to_research` only send back |
| `PACKAGING_P0_APPROVAL` | `ProjectState.PACKAGING_P0` | `approve_packaging_p0` | none -- no `ARCHIVED` edge exists from this state at all |
| `SCRIPT_APPROVAL` | `ProjectState.SCRIPT_VERIFICATION` then `SCRIPT_REVIEW` | `accept_script_verification` then `approve_final_script` | `reject_final_script` -> `ARCHIVED` (the only optional-feedback, no-freshness-recheck reject function in the file) |
| `FINAL_MEDIA_APPROVAL` | *(new this project -- no prior concept)* | `app/orchestration/gates.py::approve_gate` | `reject_gate` |

Only two of these sixteen functions ever record a true
`ApprovalStatus.REJECTED`: `decide_feasibility(..., GateStatus.REJECT)`
and `reject_final_script`. Every other "not approved" action records
`ApprovalStatus.REVISE` and routes the project BACKWARD to an earlier
state -- "redo this stage," never a terminal rejection. `RESEARCH_APPROVAL`
was deliberately named for `decide_feasibility`'s own gate rather than
inventing a separate "approve R0/R1 research" concept: `R1ResearchEngine`
itself transitions `R1_RESEARCH -> NARRATIVE` automatically once
feasibility has passed, with no intervening human step.

`app/orchestration/gates.py::gate_decision_for` dispatches on
`gate_type`: for these five legacy types it calls
`_read_legacy_gate_decision`, which reconstructs an `ApprovalDecision`-
shaped result purely from `ProjectState` (compared against a fixed
mainline rank table) plus the corresponding `HumanApproval` row
(`app/storage/approvals.py::get_latest_approval_for_stage`) -- no new
decision table, no duplicated review logic. This is safe for source
purity: neither `app/review/service.py` nor `app/storage/approvals.py`
imports `app.llm` or a provider package. `approve_gate`/`reject_gate`
raise the new `LegacyGateNotWritableError` if called with any of these
five -- the real decision must be made through
`app/review/service.py`'s own functions directly, never a second,
competing write path. `SCRIPT_APPROVAL`'s own subject id is resolved
specially: since `ScriptVerificationReport` has no `id` field at all,
the runner always passes `subject_artifact_id=None` for this one gate,
and `gate_decision_for` substitutes `project.script_plan_id` instead.

### Business-outcome blocking, not silent success (requirements #19, #24, #37)

Three new `BlockedReason` codes mirror `MediaQCAdapter`'s own
`QC_NOT_READY` pattern exactly -- a business-outcome failure blocks the
node BEFORE its gate is ever offered, rather than dangling in
`WAITING_APPROVAL` for a decision that would just fail anyway:

- `FeasibilityAdapter.gate_ok` = `report.status is GateStatus.PASS` ->
  `FEASIBILITY_NOT_PASSED` otherwise (mirrors `decide_feasibility`'s own
  precondition -- REFRAME/REJECT sends the project backward, it does not
  wait for an approval that could never be granted at RESEARCH_APPROVAL).
- `ScriptVerifyAdapter.gate_ok` = `report.status is GateStatus.PASS` ->
  `SCRIPT_VERIFICATION_NOT_PASSED` otherwise -- VOICE_PLAN/VISUAL_PLAN
  and everything downstream never proceeds on an unverified script.
- `PackagingP0Adapter.gate_ok` = `risk_of_misleading is not
  RiskLevel.HIGH` -> `PACKAGING_RISK_TOO_HIGH` otherwise (mirrors
  `approve_packaging_p0`'s own `PackagingRiskTooHighError`).

`NodeAdapter.gate_not_ready_reason` is a new OPTIONAL class attribute
(`app/orchestration/registry.py`'s own protocol docstring documents it);
`ProductionRunner._evaluate_node` reads it via
`getattr(adapter, "gate_not_ready_reason", BlockedReason.QC_NOT_READY)`,
so every Phase 33 adapter that doesn't declare it keeps the exact
original `QC_NOT_READY` behavior -- a minimal, backward-compatible
protocol extension, not a breaking change to `gate_ok`'s own signature.

### Registered/wired node coverage (requirement #30)

`tests/test_production_adapters_registry.py` asserts the full 19-node
graph and the full adapter set are identical sets, and that zero
`UnwiredNodeAdapter` instances remain -- every realistically executable
node this project's own engines support now has a real adapter. No node
was left unwired due to a genuine technical blocker in this phase.

### Full no-network integration proof (requirements #44-46)

`tests/test_full_production_integration.py` builds a bare project (no
artifact pre-seeded except the initial `IdeaEngineInput`) and drives it
through `ProductionRunner` only, using real `app/review/service.py`
calls for the five legacy gates and `approve_gate` for
`FINAL_MEDIA_APPROVAL`. `VOICE_PLAN`/`VISUAL_PLAN`/`ASSEMBLY_PLAN`/
`PACKAGING_P1`'s own structured-output schemas carry REAL foreign-key
fields their engines never override (unlike `idea_id`/`central_question`
elsewhere in the chain, which every earlier engine deterministically
overrides post-parse regardless of what the LLM said) -- confirmed by
reading each `engine.py` directly (`voice_plan = generation.value`, no
`model_copy` override). This means those four fixtures can only be
built AFTER the real upstream id they reference has actually been
generated, so the trace is twelve `run_until()` calls rather than a
flat five: each stage whose own JSON must reference a not-yet-known
real id gets its own call, injecting a freshly-built fixture in
between -- exactly what a real caller preparing real LLM prompts would
have to do. The full trace: IDEA -> `WAITING_APPROVAL`(IDEA_APPROVAL) ->
approve -> RESEARCH_R0+FEASIBILITY -> `WAITING_APPROVAL`
(RESEARCH_APPROVAL) -> approve -> RESEARCH_R1+NARRATIVE ->
`WAITING_APPROVAL`(NARRATIVE_APPROVAL) -> approve -> PACKAGING_P0 ->
`WAITING_APPROVAL`(PACKAGING_P0_APPROVAL) -> approve ->
SCRIPT+SCRIPT_VERIFY -> `WAITING_APPROVAL`(SCRIPT_APPROVAL) -> approve
(both `accept_script_verification` and `approve_final_script`) ->
upstream chain `SUCCEEDED` -> VOICE_PLAN -> VISUAL_PLAN -> ASSEMBLY_PLAN
-> PACKAGING_P1 -> downstream renderer slice ->
`WAITING_APPROVAL`(FINAL_MEDIA_APPROVAL) -> `approve_gate` -> `resume` ->
`SUCCEEDED`, with every one of the 18 nodes MEDIA_QC's own closure
includes (`PACKAGING_P1` is a dead-end branch nothing depends on, so it
is correctly excluded) showing `reused_existing_artifact=True` on that
final resume. Artifact-id linkage is checked directly, not merely file
existence: `VoicePlan.script_plan_id == ScriptPlan.id`,
`VisualPlan.voice_plan_id == VoicePlan.id`,
`TimelineManifest.assembly_plan_id == AssemblyPlan.id`,
`MediaQCReport.timeline_manifest_id == TimelineManifest.id`.

### Backward compatibility (requirements #49-50)

No destructive migration: no column was removed or retyped on
`ProductionRunRow`/`ApprovalDecisionRow`. `tests/test_phase33_compatibility.py`
builds a run with Phase 33's own `build_default_graph()`/
`build_default_adapters()` (every upstream node `BLOCKED`/
`NODE_NOT_WIRED`), confirms it persists and reloads correctly, then
resumes the SAME `run_id` through the NEW `build_full_graph()`/
`build_full_adapters()` -- the boundary moves from "no adapter exists"
to "adapter exists, needs real input" (a `FAILED`/`ProductionInputMissingError`,
not a silent permanent block), proving the upgrade path needs no
migration at all.

### What Phase 34 deliberately does not do

No autonomous prompt/model selection anywhere in `app/orchestration/`
or `app/production_adapters/` -- adapters resolve inputs and call
existing engines, they never choose a provider, model, temperature, or
prompt. No live network calls in any test or the evaluation script
(`FakeLLMProvider`/`FakeResearchRetriever`/`FakeTTSProvider`/
`FakeVisualProvider` throughout). No background workers, no queue
infrastructure, no publishing, no YouTube API, no analytics, no
automatic QC/business-outcome remediation, no automatic human approval
on any gate, and no UI/dashboard -- `app/review/service.py`'s own
functions, `app/orchestration/gates.py::approve_gate`/`reject_gate`, and
`scripts/evaluate_full_production_orchestrator.py` are the only
surfaces this phase adds or touches, all explicit, code-level calls.
`ProjectState`'s own existing state machine and `app/workflow/` are
completely untouched.

## Phase 35 — Production CLI / Single-Command Workflow (approved)

Exposes the complete Phase 34 orchestrator through one installable
`motily` command-line tool. Purely an execution surface (requirement
#26): every command handler parses inputs, calls exactly one
`ProductionService` method, formats the result, and maps it to an exit
code -- no dependency ordering, freshness, or approval logic is
duplicated here.

```
motily produce/resume  ->  ProductionService  ->  ProductionRunner (Phase 33/34, unchanged)
motily approve/reject   ->  ProductionService  ->  app/services/production_review.py (5 legacy gates)
                                                 -> app/orchestration/gates.py (FINAL_MEDIA_APPROVAL)
motily status/runs      ->  ProductionService  ->  app/storage/production_runs.py (read-only)
```

### Composition root (requirement #8)

`app/bootstrap.py::build_composition(db_path)` is the only place that
opens the database, builds the full 19-node graph/adapter registry
(`app.production_adapters.registry`, Phase 34, unchanged), and
constructs the one `ProductionService`. It never constructs a provider:
this codebase has never implemented a concrete, network-calling
`LLMProvider` (`app/llm/provider.py` is a Protocol; `app/llm/fake.py` is
the only implementation) -- a real `motily produce` invocation correctly
reaches `FAILED`/`AdapterConfigurationError` at IDEA, a deliberate,
documented limitation rather than a silent no-op or confusing crash
(see README's Phase 35 section and this document's own "unresolved
limitations" note).

### Test seams (requirement #10)

`app/cli/main.py` exposes exactly two module-level overrides -- never
"dozens of monkeypatched globals":

```python
_composition_override: AppComposition | None = None
_context_factory_override: Callable[[UUID, Engine], ExecutionContext] | None = None
```

Tests build one `AppComposition` (one shared SQLite engine) for a whole
CLI scenario and set `_composition_override` to it; `_context_factory_override`
is called fresh before every `produce`/`resume` invocation so a test can
swap in a freshly-queued `FakeLLMProvider` between pipeline stages,
exactly mirroring how `tests/test_full_production_integration.py`
(Phase 34) reconfigures `ExecutionContext.llm_provider` between
`run_until()` calls one level down.

### Target aliases (requirement #5)

```python
TARGET_ALIASES = {
    "idea": "IDEA", "research": "FEASIBILITY", "script": "SCRIPT_VERIFY",
    "plans": "ASSEMBLY_PLAN", "video": "VIDEO_RENDER", "qc": "MEDIA_QC", "final": "MEDIA_QC",
}
```

`"qc"` and `"final"` resolve to the SAME node (`MEDIA_QC`) because
`FINAL_MEDIA_APPROVAL` is declared as that node's own `gate_after` --
there is no way to reach "past QC but before the gate" in this graph,
so the two aliases are honestly identical rather than faked apart.
`"research"` targets `FEASIBILITY` (not `RESEARCH_R1`) because
`decide_feasibility` is the actual human research-continuation decision
in this system -- confirmed against `app/review/service.py` in Phase 34,
never assumed from the word "research". No alias exists for `VOICE_PLAN`/
`VISUAL_PLAN`/`ASSEMBLY_PLAN` individually (only their shared endpoint
via `"plans"`) -- `ProductionService.start_at_node(project_id, raw_node_id, ctx)`
is the internal (non-CLI-exposed) seam that advances through them one at
a time, needed because each one's own structured-output schema
references a real upstream id that only becomes known once the previous
step has actually executed (VOICE_PLAN/VISUAL_PLAN/ASSEMBLY_PLAN/
PACKAGING_P1 never override their own foreign-key fields post-parse,
unlike every earlier engine in the chain -- confirmed in Phase 34).

### Gate aliases and legacy dispatch (requirements #17, #20-21)

```python
GATE_ALIASES = {
    "idea": IDEA_APPROVAL, "research": RESEARCH_APPROVAL, "narrative": NARRATIVE_APPROVAL,
    "packaging": PACKAGING_P0_APPROVAL, "script": SCRIPT_APPROVAL, "final": FINAL_MEDIA_APPROVAL,
}
```

`app/services/production_review.py::approve_legacy_gate`/
`reject_legacy_gate` route each of the five legacy gates to its exact
canonical `app/review/service.py` function, verifying the project is
actually at the `ProjectState` that gate expects first
(`GateNotPendingError` otherwise). Two behaviors worth calling out
precisely, both verified against the real review-service contracts
rather than assumed:

- **`SCRIPT_APPROVAL` needs two `approve` calls.** It spans
  `SCRIPT_VERIFICATION` then `SCRIPT_REVIEW` with no engine work between
  them. Each CLI `approve` performs exactly ONE real mutation
  (`accept_script_verification` OR `approve_final_script`, whichever the
  current `ProjectState` calls for) -- never silently chaining both from
  one command, per requirement #29's "keep mutations explicit and
  auditable". The gate still reads `WAITING_APPROVAL` after the first
  call; a second `approve`+`resume` cycle is required to fully clear it.
- **Four of the five legacy gates have no reject path.** Only
  `RESEARCH_APPROVAL` and `SCRIPT_APPROVAL` can ever be rejected in the
  real system (`decide_feasibility(REJECT)`/`reject_final_script`) --
  and `RESEARCH_APPROVAL` specifically can NEVER be rejected once it
  reaches `WAITING_APPROVAL`, because `FeasibilityAdapter.gate_ok`
  already guarantees `FeasibilityReport.status == PASS` before the gate
  is ever offered (a REFRAME/REJECT report blocks earlier, as
  `FEASIBILITY_NOT_PASSED`) -- calling `decide_feasibility(REJECT)`
  against a PASSing report would itself raise
  `FeasibilityDecisionMismatchError`. `IDEA_APPROVAL`/
  `NARRATIVE_APPROVAL`/`PACKAGING_P0_APPROVAL` have no reject function
  in `app/review/service.py` at all. `motily reject` raises a clear
  `GateRejectionNotSupportedError` for all of these, naming the real
  reason, rather than attempting a call that would fail confusingly.

### Exit-code contract (requirement #13)

`app/cli/exit_codes.py`: `0` success (including every read-only command
that completes normally), `2` `WAITING_APPROVAL`, `3` `BLOCKED`, `4`
`FAILED`, `5` invalid CLI/input, `6` infrastructure unavailable
(`motily doctor` only -- a node's own technical failure is always exit
`4`, even when its root cause happens to be an infrastructure problem
inside that one engine, since the run's own status is what the exit
code reflects, not a guess about causation).

### Approval/resume policy (requirements #28-29)

`produce` always creates a new `ProductionRun` -- calling it twice
against the same project makes two runs, never deduplicated.
`approve`/`reject` write a decision and return immediately; they never
call `resume` themselves. This keeps every mutation a single, explicit,
auditable CLI invocation, matching Phase 33/34's own "no automatic
approval" principle at the command layer too.

### JSON DTO (requirement #33)

`app/cli/dto.py::ProductionRunDTO` is a plain dataclass, not a dump of
`ProductionRun` itself -- a future change to the persisted orchestration
model cannot silently change what `--json` consumers see. Fields:
`production_run_id, project_id, target, status, stop_reason,
waiting_gate, subject_artifact_id, executed_nodes, reused_nodes,
blocked_nodes, failed_nodes`. Never includes provider configuration or
credentials.

### Full no-network CLI integration (requirements #45-46)

`tests/test_full_production_integration.py`'s own fixture reasoning
(Phase 34) applies again here: `tests/test_full_cli_integration.py`
drives `motily produce`/`approve`/`resume` (via Typer's `CliRunner`,
the exact command dispatch a real invocation goes through) from a bare
project through all six real gates to `SUCCEEDED`, with zero live
network calls. The ungated `VOICE_PLAN -> VISUAL_PLAN -> ASSEMBLY_PLAN`
stretch uses `ProductionService.start_at_node` directly (documented as
an "internal service implementation" call, not a bypass to
`ProductionRunner`) since no published `--target` alias addresses them
individually. A final `resume` after `SUCCEEDED` is asserted to execute
zero new `ModuleRun` rows and reuse every node.

### What Phase 35 deliberately does not do

No web/desktop GUI, no background daemon, no worker queue, no
publishing/YouTube API, no analytics, no automatic approvals, no
auto-retry, no new creative engines, no interactive TUI framework.
`motily doctor` never calls a provider or verifies a credential over
the network. No secret/API key value is ever printed, logged, or
persisted into a `ProductionRun` row.

### Unresolved limitation

No concrete, network-calling `LLMProvider` implementation exists
anywhere in this codebase (only `app/llm/fake.py`) -- Phase 1 through 35
never added one, since every phase's own tests use `FakeLLMProvider`
exclusively. This means `motily produce`/`resume` cannot drive the
twelve upstream creative nodes against a real API today; they correctly
and immediately report `FAILED`/`AdapterConfigurationError` at IDEA in
that mode. `app/audio/providers/gemini.py`/`app/visual/providers/
gemini.py`/`app/visual/providers/cloudflare.py` DO have real backends
already (reading `GEMINI_API_KEY`/`CLOUDFLARE_*` from the environment,
per the existing convention `motily doctor` checks for) -- wiring those
into the composition root for `VOICE_RENDER`/`VISUAL_RENDER` would be a
natural, low-risk follow-up, but implementing a real LLM text-generation
provider is a materially larger undertaking outside this phase's "CLI
execution surface only" scope.
