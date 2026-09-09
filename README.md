# Motily — Một Tí Lý AI Content Pipeline

Một Tí Lý is a planned AI-assisted content pipeline for producing short-form
science explainer videos hosted by "Tí", a lanh chảnh science-detective
character. The full pipeline (idea discovery, research, narrative design,
scripting, verification, and production) is described in the project's
Technical Spec.

## Current MVP Scope

**CORE MVP: COMPLETE AND END-TO-END TESTED.**
**Production layer: Voice + Visual + Timing/Assembly + final Packaging
Planning, plus two execution/rendering components (Voice Renderer, Visual
Renderer) — a real Voice provider (Gemini TTS, canonical voice `Puck`)
and two real still-image Visual providers, `GENERATED_STILL` only
(Gemini Image — architecturally correct but currently blocked by this
account's zero image-generation quota; Cloudflare Workers AI / FLUX.1
Schnell — added as a free-tier-friendly alternative). `DIAGRAM`
intentionally has no real production provider yet. No real video
provider, no CapCut automation, or publishing implemented.**

This repository currently implements **Phase 1 (typed contracts) through
Phase 21.1 (Tí State Mapping + Canonical Asset Ingestion)**. Phases 1-12 form the
complete, originally scoped core MVP pipeline — idea through a verified,
human-approved script — proven to compose end to end using only the
implemented public engine/review operations (`tests/integration/`). See
[docs/CORE_MVP_AUDIT_v0.1.md](docs/CORE_MVP_AUDIT_v0.1.md) for the full
audit, including two real human-gate-bypass defects the audit found and
fixed (a stale script-verification report and a stale packaging prototype
could each be used to skip a gate that should have required a fresh
artifact — both closed in `app/review/service.py`, no locked contract
touched).

The production layer plans four things so far, none of which generates
any media: **Voice Planning** turns the finished `ScriptPlan` into
delivery-performance metadata (`VoicePlan`) — vocal state, pace, energy,
take count, background-music intent, and optional SFX opportunities per
chunk of adjacent lines. **Visual Planning** turns `NarrativePlan` +
`ScriptPlan` + `VoicePlan` into a visual representation strategy
(`VisualPlan`) — what the viewer should see for each beat, expressed as a
visual level (L1 establish / L2 watch-action / L3 understand-relationship),
a visual function, a media-router choice among 7 fixed types, a C0-C3
complexity class, and a primary/secondary/context focus hierarchy.
**Timing / Assembly Planning** turns `ScriptPlan` + `VoicePlan` +
`VisualPlan` into an estimated temporal assembly (`AssemblyPlan`) — one
segment per visual beat, with estimated start/end timing, a resolved
music state, transition intent (one of 5 fixed values), and CapCut-oriented
metadata, all built to a continuous, gap-free timeline whose total stays
within tolerance of `ScriptPlan`'s own duration estimate. **Packaging P1**
is the final packaging reasoning pass: now that the video is fully
planned, it turns `PackagingPrototype` (P0) + `ResearchPackage` +
`NarrativePlan` + `ScriptPlan` + `VisualPlan` + `AssemblyPlan` into a
`FinalPackagingPlan` — one final title, one final thumbnail creative
direction, and one final promise statement, refining P0's promise rather
than replacing it. All four engines run directly against a project at
`MVP_COMPLETE`, never mutate their upstream artifacts, and never
transition project state. Visual Planning, Timing/Assembly Planning, and
Packaging P1 each enforce real production-integrity gates: they refuse to
run at all (no LLM call, no audit record, no write) if any upstream plan
they depend on was built against an artifact other than the one the
project now references -- Packaging P1's own gate chains transitively
across Voice, Visual, and Assembly planning, plus a check that
`PackagingPrototype` is still the project's actual, currently-approved P0.

Phase 17 adds the pipeline's **first execution/rendering component**: the
**Voice Renderer** (`app/renderers/voice/`) turns the locked `VoicePlan` +
`ScriptPlan` into real rendered audio files plus a `VoiceRenderManifest`
— one take per `(chunk, take_number)`, synthesized through a new
provider-independent TTS abstraction (`app/audio/`, mirroring `app/llm/`)
and written to disk through `AudioFileStore`. **This is deliberately not
a thirteenth engine**: it lives outside `app/engines/` in a new
`app/renderers/` package, has no prompt and no LLM call anywhere in it
(zero `app.llm` dependency, direct or transitive — enforced by a
dedicated test), and reasons about nothing — it only executes a plan an
engine already produced. Audio bytes are never stored in SQLite, only a
relative file path per take, in the manifest. Phase 18 adds
**`GeminiTTSProvider`** (`app/audio/providers/gemini.py`) — the first
*real* `TTSProvider`, alongside the still-unchanged `FakeTTSProvider` —
proving Phase 17's boundary was drawn correctly: `TTSProvider`,
`TTSRequest`, `TTSResponse`, `TTSSettings`, and `VoiceRenderer` itself
needed zero changes to accept it. It wraps Gemini's raw PCM output into a
real WAV container (stdlib `wave` only), maps `VoiceState`/`Pace`/`Energy`
to deterministic natural-language direction, and reads the API key only
from an environment variable. The canonical Tí voice is `Puck`, selected
by a human after listening to the Phase 18 Gemini Vietnamese voice
shortlist (Puck, Achird, Zubenelgenubi, Iapetus) — recorded as
`CANONICAL_TI_VOICE_ID` in `app/audio/config.py`, an application-level
preference that changes no provider contract.

Phase 19 adds the pipeline's **second execution/rendering component**:
the **Visual Renderer** (`app/renderers/visual/`) turns a locked
`VisualPlan` into rendered still-image asset files plus a
`VisualRenderManifest`, through a new provider-independent visual
abstraction (`app/visual/`, mirroring `app/audio/`). Still-asset
rendering only: `GENERATED_STILL`/`DIAGRAM` beats call a `VisualProvider`
(only `FakeVisualProvider` exists so far — no real image/video vendor
integration yet); `ASSET_REUSE`/`TI_STATE` beats resolve to a reuse-only
reference with no provider call (preferring `reuse_key`, falling back to
a deterministic `ti_state:<value>` identifier); `LIMITED_MOTION`/
`EVIDENCE_MEDIA`/`AI_HERO_VIDEO` beats are recorded as external
requirements for a future execution path, also with no provider call.
Like the Voice Renderer, it lives outside `app/engines/`, has zero
`app.llm` dependency direct or transitive, runs only at
`project.state == MVP_COMPLETE`, never transitions state, and never
re-saves `ScriptPlan`/`VoicePlan`/`VisualPlan`. Visual asset bytes are
never stored in SQLite — only a relative file path per rendered beat, in
the manifest.

Phase 20 adds **`GeminiImageProvider`**
(`app/visual/providers/gemini.py`) — the first *real* `VisualProvider`,
alongside the still-unchanged `FakeVisualProvider` — proving Phase 19's
boundary was drawn correctly: `VisualProvider`, `VisualRenderRequest`,
`VisualRenderResponse`, `VisualSettings`, and `VisualRenderer` itself
needed zero changes to accept it. It supports **`GENERATED_STILL`
only** — `DIAGRAM` (and every other media type) is rejected before any
network call — and **PNG only** (validated on the response, never
forced by request config). `GeminiImageProvider` owns the **only**
vendor-specific image prompt in the repository, wrapping
`concept`/`primary_focus`/`secondary_elements`/`context_elements` —
copied verbatim, never rewritten — in a small fixed style-guidance block
(painterly editorial 2D, semi-real simplified anatomy, flat 2-3 tone
shading, warm-light/cool-shadow, background-only haze, no embedded
text). A production-routing mechanism,
**`MediaTypeVisualProvider`** (`app/visual/router.py`), lets real wiring
send `GENERATED_STILL` to Gemini while `DIAGRAM` fails non-retryably as
unavailable (`VisualProviderUnavailableError`) — **without a single
change to `VisualRenderer`**, since the router is just another object
satisfying `VisualProvider` structurally. Gemini's image response
exposes no width/height metadata, so those stay honestly `None` — no
Pillow was added to fake them. **Canonical Tí visual consistency across
generated stills is explicitly not solved** — a per-state
mood/expression note is included in the prompt when `ti_state` is
present, but there is no reference-image conditioning or character
sheet yet.

**Phase 20.1 correction (immediately after Phase 20):** a real live
call against this project's Gemini Developer API / AI Studio key proved
Phase 20's original choice — `client.models.generate_images(...)`,
model `imagen-3.0-generate-002` — genuinely does not work with that key
at all (it requires Vertex AI / Gemini Enterprise Agent Platform
credentials; the SDK also flags `generate_images` as deprecated). This
was an implementation mistake, not a later deprecation, and is not
glossed over. `GeminiImageProvider` was corrected in place to call
`client.models.generate_content(...)` with
`response_modalities=["IMAGE"]` and a `types.ImageConfig` — the same
`generate_content` entrypoint `GeminiTTSProvider` already uses — with
default model **`gemini-2.5-flash-image`**, still fully configurable
and never silently substituted. `VisualProvider`/`VisualRenderRequest`/
`VisualRenderResponse`/`VisualSettings`/`VisualRenderer`/
`MediaTypeVisualProvider` all remained unchanged; the fix stayed inside
`app/visual/providers/gemini.py` and its own tests.

**Eight content engines exist: Idea, R0 Research, Feasibility, R1
Research, Narrative, Packaging P0, Script, and Script Verification.** No
autonomous orchestrator exists yet — every engine and every human-review
action is invoked directly by a caller (a test today). No remote LLM
provider and no real search provider are implemented or required — every
test drives deterministic in-memory fakes (`FakeLLMProvider`,
`FakeResearchRetriever`). Nothing in this repository calls a real LLM, a
real search API, or fetches a real webpage/PDF. R0 is discovery research
only; R1 produces `ResearchPackage`, the factual source of truth for
everything downstream. The Narrative Engine may never invent a factual
claim. Packaging P0 tests whether the narrative can be expressed as a
strong, honest viewer promise and never approves its own output. The
Script Engine converts the approved `NarrativePlan` into Tí's final
spoken wording, staying grounded in `ResearchPackage` and aligned with the
approved `PackagingPrototype` promise — it owns wording only, not facts,
story architecture, or the viewer promise. The Script Verification Engine
is an independent auditor of that exact script: it never rewrites it, only
judges whether it is scientifically and narratively safe to publish, and
never transitions project state on its own — every route forward
(including the final `MVP_COMPLETE` sign-off) requires an explicit human
decision.

Phase 9 was an explicit, authorized contract migration (`PACKAGING_P0`
state, `Project.packaging_prototype_id`, `PackagingPrototype.id`). Phases
10 and 11 required no contract changes at all — every pre-flight
compatibility check in both phases came back clean. Phase 12 was an
audit/integration phase, not a feature phase — no locked contract, engine,
or domain model changed; the only production-code change was the two
defect fixes above, confined to `app/review/service.py`. Phases 13-16 each
added one brand-new domain model (`VoicePlan`/`VisualPlan`/`AssemblyPlan`/
`FinalPackagingPlan`) — Phase 16 reused the existing `RiskLevel` enum
rather than adding a new one — but changed no existing locked contract,
`ProjectState`, or state-graph edge. Phase 17 added one new enum
(`AudioFormat`) and, for the first time, a new top-level package outside
both `app/engines/` and `app/models/` (`app/audio/`, plus the new
`app/renderers/` category) — still with no existing locked contract,
`ProjectState`, or state-graph edge changed. Phase 18 added a new
subpackage (`app/audio/providers/`) and one new dependency
(`google-genai`), while proving `TTSProvider`/`TTSRequest`/`TTSResponse`/
`TTSSettings`/`VoiceRenderer` themselves needed **zero** changes to
accept a second, real provider. Phase 19 added two new enums
(`VisualOutputFormat`, `VisualRequirementStatus`) and two new top-level
packages (`app/visual/`, `app/renderers/visual/`) — still with no
existing locked contract, `ProjectState`, `VisualPlan`, `VisualBeat`, or
state-graph edge changed. Phase 20 added a new subpackage
(`app/visual/providers/`) and one new small composite module
(`app/visual/router.py`), reusing the already-present `google-genai`
dependency (no new dependency), while proving `VisualProvider`/
`VisualRenderRequest`/`VisualRenderResponse`/`VisualSettings`/
`VisualRenderer` themselves needed **zero** changes to accept a second,
real provider — `app/renderers/visual/renderer.py` was not modified at
all this phase. Phase 20.1 corrected a genuine implementation mistake
inside `app/visual/providers/gemini.py` (wrong SDK method/model for this
project's Gemini Developer API key) with, again, **zero** contract
changes. Phase 20.2 added a second real `VisualProvider`
(`app/visual/providers/cloudflare.py`, Cloudflare Workers AI) purely
because Gemini's account currently has zero image-generation quota —
additive only, `GeminiImageProvider` untouched, `MediaTypeVisualProvider`
unchanged, `httpx` promoted from a transitive to a direct dependency
(it now makes real HTTP calls, not just `isinstance` checks). See
`docs/TECHNICAL_SPEC_v0.1.md` for the full history.

Phase 1 covers:

- Repository/project scaffold (`app/`, `tests/`)
- `GlobalConfig` loading and validation (`app/config/`)
- Core typed domain models and enums (`app/models/`)
- JSON serialization/deserialization for every model
- Model-level validation
- Unit tests for all of the above

Phase 2 adds:

- Deterministic `ProjectState` transition rules, validating topology only
  (`app/workflow/`)
- SQLite persistence via SQLAlchemy 2.x (`app/storage/`): first-class
  `projects`, `approvals`, and `module_runs` tables, plus a shared
  `artifacts` table storing validated JSON payloads for the eight
  module-output contract types
- `ModuleRun` audit-log domain model (`app/models/module_run.py`)
- Unit tests for the state machine and every repository

Phase 3 adds:

- A provider-independent LLM interface: `Engine -> StructuredGenerator ->
  LLMProvider -> concrete provider` (`app/llm/`)
- `LLMRequest`/`LLMResponse` contracts, generic `StructuredGenerationResult[T]`
- `generate_structured()`: bounded structured-output retries (validation
  failures only — provider failures are never retried here), correction
  requests built from the target schema, no markdown-fence or prose
  scraping tricks
- `FakeLLMProvider`: a deterministic in-memory provider for tests — no
  network calls, no API keys, ever
- Unit tests for requests/responses, the provider interface, structured
  generation retry semantics, and configuration

Phase 4 adds:

- The **Idea Engine** (`app/engines/idea/`) — the first real content
  engine: `Project (IDEA_DISCOVERY) -> IdeaEngine.run() ->
  generate_structured(..., IdeaCandidate) -> persist artifact -> update
  Project.idea_candidate_id -> transition to IDEA_REVIEW -> persist
  ModuleRun`
- `EngineStateError` (`app/engines/errors.py`) — an engine refuses to run
  outside its required project state; no `BaseEngine` framework
- Any failure between generation and the state transition is recorded as
  a `FAILED` `ModuleRun` and re-raised; the project is never left pointing
  at a non-existent artifact or advanced without one
- 31 tests covering the input/output contracts, the business prompt's
  content, and the full vertical slice (success, retry, provider failure,
  retry exhaustion, storage failure, EXPAND/OPEN modes) via
  `FakeLLMProvider`

Phase 5 adds:

- **Human approval bridge** (`app/review/`) — `approve_idea`/`revise_idea`:
  deterministic functions, no LLM call, transitioning `IDEA_REVIEW ->
  R0_RESEARCH` or back to `IDEA_DISCOVERY`. Rejecting an idea
  (`IDEA_REVIEW -> ARCHIVED`) is intentionally not implemented — the
  locked state graph has no such edge.
- **Research retrieval abstraction** (`app/research/`) — a
  `ResearchRetriever` protocol no engine may bypass, plus
  `FakeResearchRetriever` for tests. No real search provider exists.
- **R0 Research Engine** (`app/engines/research_r0/`) — the second
  content engine: `R0_RESEARCH -> deterministic search queries (capped at
  4, deduplicated) -> ResearchRetriever -> generate_structured(...,
  ResearchR0) -> business URL-hallucination guard (one bounded correction
  attempt, never silently repaired) -> persist artifact -> update
  Project.research_r0_id -> transition to FEASIBILITY -> persist
  ModuleRun`. R0 is cheap discovery research, never final verification,
  and its recommendation never blocks the transition — that judgment
  belongs to a future Feasibility Engine.
- 48 tests covering review actions, retrieval contracts, query
  construction, prompt content, and the full R0 vertical slice (success,
  query cap, deduplication, hallucination guard, LLM/retriever failure,
  retry exhaustion, invalid start state, missing idea).

Phase 6 adds:

- **Feasibility Engine** (`app/engines/feasibility/`) — the third content
  engine: `FEASIBILITY -> generate_structured(..., FeasibilityReport)`
  across exactly five axes (audience, science, narrative, visual,
  production) -> deterministic overall-status derivation (any `REJECT`
  axis wins, else any `REFRAME` axis, else `PASS` — the LLM's own overall
  value is never trusted) -> persist artifact -> update
  `Project.feasibility_id`. **The engine never transitions project
  state** — it stays in `FEASIBILITY` awaiting a human decision.
- **Human Feasibility decision** (`app/review/service.py`) —
  `decide_feasibility(project_id, decision, feedback=None)`: the decision
  must equal the stored report's status exactly
  (`FeasibilityDecisionMismatchError` otherwise — no override feature
  yet). `PASS -> R1_RESEARCH`, `REFRAME -> IDEA_DISCOVERY` (feedback
  required, prior artifact references retained), `REJECT -> ARCHIVED`
  (terminal).
- 37 tests covering status derivation, prompt content, the full engine
  vertical slice, and every human-decision path and guard.

Phase 7 adds:

- **R1 Research Engine** (`app/engines/research_r1/`) — the fourth
  content engine, producing `ResearchPackage`: deterministic, bounded
  query planning (`<=10` queries) -> `ResearchRetriever` -> deduplicated
  evidence -> `generate_structured(..., ResearchPackage)` -> a
  deterministic `central_question` override -> business validation
  (source-URL provenance, claim/source referential integrity, id
  uniqueness, evidence-by-claim-status) with one bounded correction
  attempt -> persist artifact -> update `Project.research_r1_id` ->
  transition `R1_RESEARCH -> NARRATIVE`.
- Preconditions uniquely require the stored `FeasibilityReport.status ==
  PASS`, on top of the usual valid-matching-artifacts checks.
- 48 tests covering query planning, business validation (unit and
  end-to-end), prompt content, and the full engine vertical slice.

Phase 8 adds:

- **Narrative Engine** (`app/engines/narrative/`) — the fifth content
  engine, producing `NarrativePlan`: `generate_structured(...,
  NarrativePlan)` -> a deterministic `central_question` override ->
  business validation (question-ladder structural integrity: unique ids,
  a single linear chain, no cycles, no orphans; claim referential
  integrity: every referenced claim must exist in `ResearchPackage` and
  must never be `PROHIBITED`) with one bounded correction attempt ->
  persist artifact -> update `Project.narrative_plan_id` -> transition
  `NARRATIVE -> NARRATIVE_REVIEW`.
- **Human narrative review** (`app/review/service.py`) —
  `revise_narrative` (`NARRATIVE_REVIEW -> NARRATIVE`) and
  `send_narrative_back_to_research` (`NARRATIVE_REVIEW -> R1_RESEARCH`,
  feedback required) are implemented. **`approve_narrative` is not** — the
  locked state graph's only forward edge from `NARRATIVE_REVIEW` goes
  directly to `SCRIPT`, bypassing the not-yet-built Packaging P0 stage, so
  it unconditionally raises `NarrativeApprovalNotAvailableError` rather
  than silently skip that gap.
- 42 tests covering ladder/claim validation (unit and end-to-end), prompt
  content, the full engine vertical slice, and every review action
  including the documented approval gap.

Phase 9 adds (an explicit, authorized contract migration):

- **State graph migration**: `NARRATIVE_REVIEW`'s edge to `SCRIPT` is
  replaced by an edge to the new `PACKAGING_P0` state; `PACKAGING_P0`
  itself routes forward to `SCRIPT` or back to `NARRATIVE`/`R1_RESEARCH`
  only (recovery stays local, by design).
- **`approve_narrative()` activated** — the Phase 8 stub that always
  raised is gone; it now really transitions `NARRATIVE_REVIEW ->
  PACKAGING_P0`.
- **Packaging P0 Engine** (`app/engines/packaging_p0/`) — the sixth
  content engine, producing one recommended `PackagingPrototype` (never a
  title/thumbnail menu): `generate_structured(..., PackagingPrototype)`
  -> business validation (non-blank fields only —
  `risk_of_misleading == HIGH` is never a validation failure) -> persist
  artifact -> update `Project.packaging_prototype_id`. **Never
  transitions state itself**, even for a HIGH-risk result.
- **Human packaging review** — `approve_packaging_p0` (`PACKAGING_P0 ->
  SCRIPT`, blocked by `PackagingRiskTooHighError` when
  `risk_of_misleading == HIGH`), `revise_packaging_p0` (`-> NARRATIVE`),
  `send_packaging_back_to_research` (`-> R1_RESEARCH`).
- 50 new/updated tests, including dedicated coverage proving the old
  `NARRATIVE_REVIEW -> SCRIPT` edge is now invalid and every other prior
  graph edge is unchanged.

Phase 10 adds:

- **Script Engine** (`app/engines/script/`) — the seventh content engine,
  producing `ScriptPlan`: `generate_structured(..., ScriptPlan)` ->
  business validation (beat/line structure, narrative-node validity and
  non-regressing order against `NarrativePlan.question_ladder`, claim
  referential integrity, `[420, 660]`-second duration band) -> persist
  artifact -> update `Project.script_plan_id` -> transition `SCRIPT ->
  SCRIPT_VERIFICATION`.
- Tí's conversational voice, "bạn" address, short TTS-readable sentences,
  the three upstream-ownership boundaries (research/narrative/packaging),
  and light-censored-profanity-only are all prompt-enforced.
- **No locked-contract changes this phase** — all six required
  compatibility checks (on `ScriptPlan.id`, `ScriptBeat.narrative_node`,
  `ScriptBeat.micro_hook`, `ScriptLine.emotion`, `Project.script_plan_id`,
  and the `SCRIPT -> SCRIPT_VERIFICATION` edge) came back clean.
- 43 tests covering business validation (unit and end-to-end), prompt
  content, and the full engine vertical slice.

Phase 11 adds (completing the original core MVP):

- **Script Verification Engine** (`app/engines/script_verification/`) —
  the eighth content engine, producing `ScriptVerificationReport`:
  `generate_structured(..., ScriptVerificationReport)` -> deterministic
  status normalization (always reconciles `status` with whether any issue
  list is non-empty) -> a claim-reference defense-in-depth check
  (independent of `ScriptEngine`'s own validation) with one bounded
  correction attempt -> persist artifact. **Never modifies the reviewed
  `ScriptPlan` and never transitions project state itself** — the project
  stays in `SCRIPT_VERIFICATION` awaiting a human decision.
- **Human resolution + Final Script Review** (`app/review/service.py`) —
  from `SCRIPT_VERIFICATION`: `accept_script_verification` (`-> SCRIPT_
  REVIEW`, requires the report's `status == PASS`), `send_script_for_
  rewrite` (`-> SCRIPT`), `send_script_back_to_narrative` (`->
  NARRATIVE`), `send_script_back_to_research` (`-> R1_RESEARCH`). From
  `SCRIPT_REVIEW`, the final human gate: `approve_final_script` (`->
  MVP_COMPLETE`, same `PASS` guard), `revise_final_script` (`-> SCRIPT`),
  `send_final_script_back_to_narrative` (`-> NARRATIVE`),
  `reject_final_script` (`-> ARCHIVED`, feedback optional).
- **No locked-contract changes this phase** — all seven required
  compatibility checks (on `ScriptVerificationReport.id`'s absence,
  `Project`'s lack of a verification-report reference field, the report's
  plain-`list[str]` issue fields, the `SCRIPT_VERIFICATION`/`SCRIPT_
  REVIEW` graph edges, generic artifact storage, the pre-existing
  `SCRIPT_REVIEW`/`MVP_COMPLETE` states, and `ScriptPlan` immutability)
  came back clean.
- 93 tests covering status normalization and the defense-in-depth check
  (unit), prompt content, the full engine vertical slice, and every human
  resolution/final-review action including both `PASS`-guard blocks.

With Phase 11 complete, every state in the locked `ProjectState` graph is
reachable via a real engine or a real human-review action, from
`NEW_PROJECT` through `MVP_COMPLETE`.

Phase 12 adds (audit/integration only — not a feature phase):

- **`tests/integration/`** (19 new tests): a full, literal 16-step
  end-to-end happy-path test driving the real public engine/review
  operations from `NEW_PROJECT` to `MVP_COMPLETE` (no orchestrator);
  artifact/human-gate/`ModuleRun` integrity checks at `MVP_COMPLETE`; six
  human-gate-bypass tests; six recovery-path tests (idea revise,
  feasibility reframe, narrative revise, two back-to-`R1_RESEARCH`
  routes, script rewrite, final-script revise); and the two adversarial
  stale-artifact-safety tests that found the defects below.
- **Two real human-gate-bypass defects found and fixed**, both in
  `app/review/service.py`: a stale `ScriptVerificationReport` could
  accept a rewritten, never-verified script; a stale `PackagingPrototype`
  could bypass a fresh Packaging P0 gate after a narrative revision. Both
  fixed by cross-checking the generating engine's most recent successful
  `ModuleRun.input_ids` against the project's current upstream reference
  (new `StaleScriptVerificationError`, `StalePackagingPrototypeError`) —
  no locked contract, `ProjectState`, state graph, or repository schema
  changed. Full writeup: [docs/CORE_MVP_AUDIT_v0.1.md](docs/CORE_MVP_AUDIT_v0.1.md).
- **`docs/CORE_MVP_AUDIT_v0.1.md`**: the full architecture audit —
  state-graph, source-of-truth, engine-boundary, provider/agent-framework,
  human-gate, persistence, `ModuleRun`, and stale-artifact audits; the
  transaction partial-progress matrix (documented, deliberately not
  fixed); known technical debt; and a `READY_FOR_PRODUCTION_LAYER`
  verdict.
- 19 new tests; all 540 prior tests continue to pass. Fixing the two
  defects required correcting (not weakening) two pre-existing
  `test_review_service.py` fixture helpers to also persist a plausible
  `ModuleRun` (559 total).

Phase 13 adds (begins the production layer):

- **`VoicePlan` / `VoiceChunk`** (`app/models/voice.py`) — a brand-new
  domain contract: delivery-performance metadata only (vocal state, pace,
  energy, take count, music state, optional SFX opportunity) grouped into
  chunks of adjacent script lines. Four new enums in
  `app/models/common.py`: `VoiceState` (8 values), `Pace`, `Energy`,
  `MusicState` (3 each).
- **Voice Planning Engine** (`app/engines/voice_plan/`) — the ninth
  engine, and the first outside the core MVP: `generate_structured(...,
  VoicePlan)` -> business validation (a single unified check: the
  flattened chunk line-id sequence must exactly equal the ScriptPlan's own
  line order — this simultaneously proves coverage, no duplicates, global
  order, and chunk adjacency) -> persist artifact. Runs only at
  `project.state == MVP_COMPLETE`; **never transitions state, never
  updates a `Project` reference field (none exists), and never re-saves
  `ScriptPlan`.**
- **No audio is generated.** No TTS provider, no voice cloning, no
  music/SFX rendering — `music_state`/`sfx_opportunity` are plans for a
  future rendering pass only.
- **Freshness**: `VoicePlan.script_plan_id` and `ModuleRun.input_ids[0]`
  both record the exact `ScriptPlan.id` a plan was built from, so a stale
  `VoicePlan` is detectable after a script rewrite — detection only, no
  enforcement built this phase.
- **No locked-contract changes** — all 8 required compatibility checks
  (VoicePlan's absence, `Project.voice_plan_id`'s absence, `ScriptLine`'s
  `pause_after`/`emotion`/`function` types, the absence of any
  production-layer `ProjectState`, generic artifact storage, and
  `ScriptLine`'s lack of an `intent` field) came back clean.
- 60 new tests covering the domain model, business validation (missing
  line, extra line, duplicate line, order violation, non-contiguous
  chunk), prompt content, and the full engine vertical slice (success,
  structured retry, business correction, business correction exhaustion,
  script immutability, stale-input detectability, LLM/structured
  failures).

Phase 14 adds (continues the production layer):

- **`VisualPlan` / `VisualBeat`** (`app/models/visual.py`) — a brand-new
  domain contract: visual representation strategy only (visual level,
  visual function, media type, complexity class, concept, a primary/
  secondary/context focus hierarchy, optional Tí state, evidence source
  ids, motion intent, reuse key). Five new enums in `app/models/common.py`:
  `VisualLevel` (L1/L2/L3), `VisualFunction` (5), `VisualMediaType` (7),
  `ComplexityClass` (C0-C3), `VisualTiState` (9 — deliberately separate
  from `VoiceState`).
- **Visual Planning Engine** (`app/engines/visual_plan/`) — the tenth
  engine: `generate_structured(..., VisualPlan)` -> business validation
  (the same coverage/order/adjacency check as Voice Planning, plus
  narrative-node alignment, evidence-source integrity, and a C3-complexity
  budget capped at 20% for plans of 5+ beats) -> persist artifact. Runs
  only at `project.state == MVP_COMPLETE`; **never transitions state,
  never updates a `Project` reference field (none exists), and never
  re-saves `ScriptPlan` or `VoicePlan`.**
- **A real production-integrity gate**: before any LLM call, `ModuleRun`,
  or write, the engine verifies the current `VoicePlan.script_plan_id`
  matches the project's current `script_plan_id` — raising
  `StaleVoicePlanError` otherwise. This is the only precondition in the
  codebase that blocks purely on a cross-artifact mismatch rather than a
  missing/invalid artifact.
- **No image or video is generated.** No image-prompt generator, no
  animation renderer, no footage downloading, no web image search, no
  CapCut automation — `VisualPlan` is planning metadata for a future
  rendering pass only.
- **Design decision**: `ResearchPackage` is always loaded (not skipped as
  merely optional), because evidence-source-id validation is an
  unconditional required check that only makes sense with real sources to
  validate against.
- **Freshness**: `VisualPlan.script_plan_id`/`voice_plan_id` and
  `ModuleRun.input_ids` record both ids used, so a stale `VisualPlan` is
  detectable after either `ScriptPlan` or `VoicePlan` changes — detection
  only, no enforcement built this phase.
- **No locked-contract changes** — all 9 required compatibility checks
  came back clean.
- 74 new tests covering the domain model, business validation (missing/
  extra/duplicate/reordered lines, cross-narrative-node beats, unknown
  narrative nodes, evidence-source integrity, the C3 budget), prompt
  content, and the full engine vertical slice (success, the stale-VoicePlan
  gate, freshness detectability, structured retry, business correction and
  exhaustion, immutability of both `ScriptPlan` and `VoicePlan`,
  LLM/structured failures).

Phase 15 adds (continues the production layer):

- **`AssemblyPlan` / `AssemblySegment`** (`app/models/assembly.py`) — a
  brand-new domain contract: temporal/assembly strategy only (one segment
  per visual beat, estimated start/end, resolved music state, transition
  intent in/out, optional emphasis/assembly notes). One new enum in
  `app/models/common.py`: `TransitionIntent` (`CUT`/`DISSOLVE`/`MATCH`/
  `PUSH`/`NONE`).
- **Timing / Assembly Planning Engine** (`app/engines/assembly_plan/`) —
  the eleventh engine: `generate_structured(..., AssemblyPlan)` ->
  deterministic normalization (the engine, not the LLM, derives every
  segment's `voice_chunk_ids` from real `VoicePlan` overlap, plus all
  three plan-level ids) -> business validation (one VisualBeat = one
  AssemblySegment in `VisualPlan`'s own order; independent script-line
  coverage; a contiguous, gap-free timeline with `1e-3`s float tolerance;
  a total duration within `max(30s, 10%)` of `ScriptPlan`'s own estimate)
  -> persist artifact. Runs only at `project.state == MVP_COMPLETE`;
  **never transitions state, never updates a `Project` reference field
  (none exists), and never re-saves `ScriptPlan`, `VoicePlan`, or
  `VisualPlan`.**
- **Three real production-integrity gates**: before any LLM call,
  `ModuleRun`, or write, the engine verifies `VoicePlan.script_plan_id`
  and `VisualPlan.script_plan_id` both match the project's current
  `script_plan_id`, and that `VisualPlan.voice_plan_id` matches the
  *current* `VoicePlan.id` — raising `StaleVoicePlanError` or
  `StaleVisualPlanError` otherwise.
- **No media generation, no CapCut automation.** No XML/EDL export, no
  actual timeline file, no image/video/audio rendering — `AssemblyPlan`
  is planning metadata for a future rendering pass only.
- **Freshness**: `AssemblyPlan.script_plan_id`/`voice_plan_id`/
  `visual_plan_id` and `ModuleRun.input_ids` record all three ids used, so
  a stale `AssemblyPlan` is detectable after any upstream plan changes —
  detection only, no enforcement built this phase.
- **No locked-contract changes** — all 10 required compatibility checks
  came back clean.
- 75 new tests covering the domain model, normalization and business
  validation (missing/duplicate visual beats, segment/beat line
  mismatches, reordering, unknown beats, voice-chunk-overlap
  normalization, timeline gaps/overlaps, duration-tolerance boundaries,
  float tolerance), prompt content, and the full engine vertical slice
  (success, all three staleness gates, freshness detectability, structured
  retry, business correction and exhaustion, immutability of `ScriptPlan`/
  `VoicePlan`/`VisualPlan`, LLM/structured failures).

Phase 16 adds (completes the current production-planning stack):

- **`FinalPackagingPlan`** (`app/models/packaging_p1.py`) — a brand-new
  domain contract: final title, thumbnail creative direction (never an
  image-generation prompt), and final promise statement. Reuses the
  existing `RiskLevel` enum from Packaging P0 -- no new enum needed.
- **Packaging P1 Engine** (`app/engines/packaging_p1/`) — the twelfth
  engine, and the final packaging reasoning pass, run only after script,
  voice, visual, and assembly planning are all done:
  `generate_structured(..., FinalPackagingPlan)` -> deterministic
  normalization of all four upstream-reference ids -> business validation
  (every required field non-blank) -> persist artifact. Runs only at
  `project.state == MVP_COMPLETE`; **never transitions state, never
  updates a `Project` reference field (none exists), and never re-saves
  any of the six upstream artifacts it reads** (`PackagingPrototype`,
  `ResearchPackage`, `NarrativePlan`, `ScriptPlan`, `VisualPlan`,
  `AssemblyPlan`).
- **A transitively-chained freshness gate**: before any LLM call,
  `ModuleRun`, or write, the engine verifies `VoicePlan` is fresh against
  `ScriptPlan`, then `VisualPlan` against both, then `AssemblyPlan`
  against all three -- plus a standard check that `PackagingPrototype` is
  still the project's actual, currently-approved P0. Any mismatch raises
  `StaleVoicePlanError`/`StaleVisualPlanError`/`StaleAssemblyPlanError`/
  `MissingPackagingPrototypeArtifactError` with zero side effects.
- **Deliberate deviation**: non-blank checks for `title`/
  `thumbnail_concept`/`final_promise`/`expected_payoff`/
  `viewer_expectation`/`rationale`/`thumbnail_text` live at the business
  layer, not as Pydantic constraints (unlike Phase 13-15) -- so a schema-
  valid-but-blank response is a genuine business-correction case, per the
  patch's own explicit test requirement.
- **HIGH-risk output is persisted, not rejected** — mirrors Packaging
  P0 exactly; no publish gate exists yet to stop a HIGH-risk plan from
  being used later.
- **No thumbnail rendering, no image-generation prompt, no publishing.**
  `thumbnail_concept` is creative direction only.
- **Freshness**: `FinalPackagingPlan`'s four reference ids plus
  `ModuleRun.input_ids` record every id used (plus `research_package_id`
  for provenance), so a stale `FinalPackagingPlan` is detectable after any
  upstream plan or P0 itself changes — detection only, no enforcement
  built this phase.
- **No locked-contract changes** — all 10 required compatibility checks
  came back clean.
- 70 new tests covering the domain model, normalization and business
  validation (blank required fields, optional `thumbnail_text`), prompt
  content, and the full engine vertical slice (success, id normalization,
  all three staleness gates plus P0-currentness, HIGH-risk persistence,
  freshness detectability, structured retry, business correction and
  exhaustion, immutability of all six upstream artifacts, LLM/structured
  failures).

Phase 17 adds the first execution/rendering component:

- **`app/audio/`** — a provider-independent TTS abstraction mirroring
  `app/llm/`: `TTSRequest`/`TTSResponse`, a `TTSProvider` Protocol,
  `FakeTTSProvider`, `TTSSettings` (no API key field), and
  `AudioFileStore` (atomic writes, path-safety validation). `AudioFormat`
  (`WAV`/`MP3`) added to `app/models/common.py`.
- **`app/models/audio.py`** — `RenderedVoiceTake`/`VoiceRenderManifest`,
  metadata-only contracts. Audio bytes are never persisted; only a
  relative file path per take is recorded.
- **Voice Renderer** (`app/renderers/voice/`) — **not a thirteenth
  engine**: no prompt, zero `app.llm` dependency direct or transitive
  (enforced by a source-scan test and a fresh-subprocess `sys.modules`
  test). For every `VoiceChunk` and every take, synthesizes verbatim
  script text (the newline-joined `ScriptLine.text` for that chunk) with
  the chunk's `voice_state`/`pace`/`energy` passed through unchanged (no
  vendor mapping), retries only a provider-side failure (bounded), writes
  the result to disk exactly once with no retry on a write failure, then
  builds and persists a `VoiceRenderManifest`. Runs only at
  `project.state == MVP_COMPLETE`; **never transitions state, never
  updates a `Project` reference field (none exists), and never re-saves
  `ScriptPlan`/`VoicePlan`.**
- **One direct freshness gate** (not a transitive chain — Voice Rendering
  has exactly one upstream planning artifact): `VoicePlan.script_plan_id
  == Project.script_plan_id`, checked before any provider call,
  `ModuleRun`, or file write — else `StaleVoicePlanError`, zero side
  effects.
- **Documented, not a defect**: a mid-run failure leaves already-written
  takes on disk as orphans and withholds only the manifest artifact — no
  automatic cleanup exists yet.
- **Upsert semantics**: rerunning the renderer overwrites the manifest
  artifact and every audio file at the same deterministic path, matching
  `save_artifact`'s existing upsert-only convention.
- **No locked-contract changes** — all 10 required compatibility checks
  came back clean.
- 93 new tests covering the TTS request/response/settings/provider layer,
  `AudioFileStore` (including atomic-write and path-safety rejection),
  the render-manifest domain models, pure manifest-integrity validation,
  and the full renderer vertical slice (happy path, text ownership,
  delivery-metadata passthrough, call-order correctness, the stale-
  VoicePlan gate, provider retry success/exhaustion, no-retry-on-write-
  failure, partial-write orphan behavior, persistence/rerender-upsert,
  upstream-artifact immutability, duration aggregation, and the
  zero-LLM-dependency audit).

Phase 18 adds the first real TTS provider:

- **`GeminiTTSProvider`** (`app/audio/providers/gemini.py`) — a
  provider-adapter phase only: `TTSProvider`/`TTSRequest`/`TTSResponse`/
  `TTSSettings`/`VoiceRenderer` are all **unchanged**. Constructs
  `genai.Client(api_key=...)` from an environment variable (default
  `GEMINI_API_KEY`) only when no client is injected, eagerly at
  construction time — a missing key fails immediately
  (`GeminiTTSConfigurationError`) with zero network calls. Tests inject a
  fake client and never touch the environment or the network.
- **Deterministic style mapping**: `VoiceState`/`Pace`/`Energy` map to
  fixed natural-language director's notes placed before the transcript —
  no numeric vendor speed/pitch values. **Text ownership**: the
  transcript is inserted byte-for-byte, never rewritten, paraphrased, or
  translated; one `generate_content` call per `synthesize()` call, no LLM
  rewriting pass. **Voice**: `TTSRequest.voice_id` maps directly, exactly,
  to Gemini's prebuilt voice name — single-speaker only.
- **PCM -> WAV**: raw PCM is wrapped into a real WAV container using
  stdlib `wave`/`io` only (no `ffmpeg`, no `pydub`). Sample rate is read
  from the SDK's own response metadata when present, falling back to
  config otherwise; duration is computed from actual PCM frame count,
  never a file-size heuristic. MP3 requests are rejected explicitly
  (`GeminiUnsupportedFormatError`) before any network call — never
  silently mislabeled.
- **Narrow, documented error boundary**: only `google.genai.errors.APIError`
  and `httpx.HTTPError` translate to `TTSProviderError`; everything else
  (a genuine bug) stays visible. A malformed/missing response shape
  translates to `TTSOutputError` explicitly — no raw
  `IndexError`/`AttributeError` leaks through.
- **No nested retry** — the adapter retries nothing itself;
  `VoiceRenderer` already owns that policy. A missing-API-key failure
  (`GeminiTTSConfigurationError`) deliberately does **not** subclass
  `TTSProviderError`, so it's never pointlessly retried either.
- **Opt-in only, everywhere**: the default test suite makes zero live
  Gemini calls and needs no secret. A live smoke test
  (`tests/test_gemini_tts_live_smoke.py`) exists but is gated by both a
  registered `live_tts` marker and `RUN_LIVE_GEMINI_TTS=1`, and skips
  cleanly otherwise.
- **`scripts/evaluate_gemini_voices.py`** — a manual Vietnamese
  voice-listening utility (default shortlist: Puck, Achird,
  Zubenelgenubi, Iapetus). No automated scoring or LLM-based ranking; a
  human decides the canonical Tí voice later.
- **No locked-contract changes** — all 10 required compatibility checks
  came back clean.
- 53 new tests (48 adapter-level, 4 full-renderer integration, 1
  live-smoke skip check); all 931 prior tests continue to pass unmodified
  (984 total).
- Addendum: a human listened to the Phase 18 shortlist and selected
  **`Puck`** as the canonical Tí voice (Achird, Zubenelgenubi, and Iapetus
  were rejected for sounding more foreign/Western-accented). Recorded as
  `CANONICAL_TI_VOICE_ID` in `app/audio/config.py` — an application-level
  preference only; no `TTSProvider`/`TTSRequest`/`TTSResponse`/
  `TTSSettings` contract changed.

Phase 19 proves the visual runtime boundary with a deterministic fake
provider — still-asset rendering only:

- **`app/visual/`** — a provider-independent visual-render abstraction
  mirroring `app/audio/`: `VisualRenderRequest`/`VisualRenderResponse`, a
  `VisualProvider` Protocol, `FakeVisualProvider`, `VisualSettings` (no API
  key field), and `VisualFileStore` (atomic writes, path-safety
  validation, mirroring `AudioFileStore` exactly). `VisualOutputFormat`
  (`PNG`/`JPG`) and `VisualRequirementStatus` (`RENDERED`/
  `EXTERNAL_REQUIRED`/`REUSE_ONLY`) added to `app/models/common.py`.
  `VisualRenderRequest` is deliberately **not** an image-generation prompt
  — it is a provider-neutral render brief copied verbatim from one
  `VisualBeat`; a future concrete provider adapter owns translating it
  into vendor-specific parameters.
- **`app/models/visual_render.py`** — `RenderedVisualAsset`/
  `VisualRenderRequirement`/`VisualRenderManifest`, metadata-only
  contracts. Asset bytes are never persisted; only a relative file path
  per rendered beat is recorded.
- **Visual Renderer** (`app/renderers/visual/`) — **not a fourteenth
  engine**: no prompt, zero `app.llm` dependency direct or transitive
  (enforced by a source-scan test and a fresh-subprocess `sys.modules`
  test). Routes every `VisualBeat`, in plan order, by a fixed,
  non-LLM media router: `GENERATED_STILL`/`DIAGRAM` call
  `VisualProvider.render()` (retrying only a provider-side failure,
  bounded, then writing the result to disk exactly once with no retry on
  a write failure); `ASSET_REUSE`/`TI_STATE` become a `REUSE_ONLY`
  requirement with no provider call (`reuse_key` preferred, `TI_STATE`
  falling back to a deterministic `ti_state:<value>` identifier;
  `ASSET_REUSE` with no `reuse_key` is rejected as an invalid beat, not
  silently skipped); `LIMITED_MOTION`/`EVIDENCE_MEDIA`/`AI_HERO_VIDEO`
  become an `EXTERNAL_REQUIRED` requirement with no provider call
  (evidence source ids and motion intent preserved for a future execution
  path). Builds and persists a `VisualRenderManifest` only once every
  beat has been accounted for exactly once, across assets and
  requirements combined. Runs only at `project.state == MVP_COMPLETE`;
  **never transitions state, never updates a `Project` reference field
  (none exists), and never re-saves `ScriptPlan`/`VoicePlan`/`VisualPlan`.**
- **Two direct freshness gates** (mirroring Voice Rendering's one):
  `VisualPlan.script_plan_id == Project.script_plan_id` and
  `VisualPlan.voice_plan_id == (current VoicePlan).id`, both checked
  before any provider call, `ModuleRun`, or file write — else
  `StaleVoicePlanError`/`StaleVisualPlanError`, zero side effects.
- **Documented, not a defect**: a mid-run failure leaves already-written
  assets on disk as orphans and withholds only the manifest artifact — no
  automatic cleanup exists yet, exactly mirroring Phase 17's Voice
  Renderer behavior.
- **Upsert semantics**: rerunning the renderer overwrites the manifest
  artifact and every asset file at the same deterministic path
  (`{project_id}/{visual_plan_id}/{render_job_id}.{ext}`), matching
  `save_artifact`'s existing upsert-only convention.
- **No Pillow dependency added**: `FakeVisualProvider` returns tiny,
  arbitrary placeholder bytes (not a real decodable PNG/JPG) — exactly
  like `FakeTTSProvider`'s fixture audio isn't a real decodable WAV. Codec
  validity is a concrete provider's future concern, not this contract's.
- **No locked-contract changes** — all 12 required compatibility checks
  came back clean; `VisualPlan`/`VisualBeat` (Phase 14) needed zero
  changes.
- 103 new tests covering the visual request/response/settings/provider
  layer, `VisualFileStore` (including atomic-write and path-safety
  rejection), the render-manifest domain models, pure manifest-integrity
  validation, and the full renderer vertical slice (seven-media-type
  happy-path routing, request/beat field ownership, provider call-order
  and call-count correctness per media type, both stale-plan gates,
  provider retry success/exhaustion, no-retry-on-write-failure,
  partial-write orphan behavior, persistence/rerender-upsert, upstream
  three-plan immutability, no-bytes-in-DB, and the zero-LLM-dependency
  audit); all 984 prior tests continue to pass unmodified (1087 total,
  1086 passed + 1 opt-in skip).

Phase 20 adds the first real visual provider, `GENERATED_STILL` only
(**method/model corrected immediately after by Phase 20.1, below — this
block describes Phase 20's original, as-shipped implementation**):

- **`GeminiImageProvider`** (`app/visual/providers/gemini.py`) — a
  provider-adapter phase only: `VisualProvider`/`VisualRenderRequest`/
  `VisualRenderResponse`/`VisualSettings`/`VisualRenderer` are all
  **unchanged**. Called Gemini's dedicated `generate_images` endpoint
  (not `generate_content`) with default model
  `imagen-3.0-generate-002`, read directly from the installed
  `google-genai==2.22.0` SDK's own docstring example, never guessed
  from memory. Constructs `genai.Client(api_key=...)` from an
  environment variable (default `GEMINI_API_KEY`) only when no client
  is injected, eagerly at construction time — a missing key fails
  immediately (`GeminiImageConfigurationError`) with zero network
  calls.
- **GENERATED_STILL + PNG only**: any other `VisualMediaType` (`DIAGRAM`
  included) or `VisualOutputFormat` (`JPG`) is rejected before any
  network call. PNG was requested explicitly from Gemini
  (`output_mime_type="image/png"`) rather than assumed.
- **Prompt translation**: `GeminiImageProvider` owns the only
  vendor-specific image prompt in the repository, wrapping
  `concept`/`primary_focus`/`secondary_elements`/`context_elements` —
  copied verbatim, never rewritten — in a fixed style-guidance block
  (painterly editorial 2D, semi-real simplified anatomy, flat 2-3 tone
  shading, warm-light/cool-shadow, background-only haze, no embedded
  text). A per-`VisualTiState` mood/expression note represents Tí when
  present, paired with an unconditional "Tí may only observe or react,
  never cause or alter an event" line preserving the Phase 14 causality
  rule — **not** a character-sheet ontology.
- **Response handling**: a Responsible-AI-filtered or empty result
  raises `VisualOutputError` with the filter reason when available; an
  explicit, unexpected MIME type is rejected, but a MIME type the SDK
  simply didn't populate is not treated as an error. Imagen exposed no
  width/height metadata at all — `width`/`height` stay honestly `None`;
  no Pillow was added to fake them. No official per-image request id
  was exposed by this endpoint either — `provider_request_id` stayed
  `None`.
- **Narrow, documented error boundary**: only
  `google.genai.errors.APIError` and `httpx.HTTPError` translate to
  `VisualProviderError`; everything else (a genuine bug) stays visible.
- **No nested retry** — the adapter retries nothing itself;
  `VisualRenderer` already owns that policy.
- **Production routing without touching `VisualRenderer`**:
  **`MediaTypeVisualProvider`** (`app/visual/router.py`) is a tiny
  composite satisfying `VisualProvider` structurally, built from a
  `{VisualMediaType: VisualProvider}` mapping. Handing
  `MediaTypeVisualProvider({VisualMediaType.GENERATED_STILL:
  GeminiImageProvider(...)})` to `VisualRenderer` routes
  `GENERATED_STILL` to Gemini while an unmapped `DIAGRAM` fails
  non-retryably with the new `VisualProviderUnavailableError` — no
  fallback guessing, no LLM, no plugin-registry framework, and **zero
  modification to `app/renderers/visual/renderer.py`** (proven by a
  dedicated source-scan test).
- **Opt-in only, everywhere**: the default test suite makes zero live
  Gemini Image calls and needs no secret. A live smoke test
  (`tests/test_gemini_image_live_smoke.py`) exists but is gated by both
  a registered `live_image` marker and `RUN_LIVE_GEMINI_IMAGE=1`, and
  skips cleanly otherwise.
- **`scripts/evaluate_gemini_image.py`** — a manual visual-evaluation
  utility (three fixed briefs: L1 establishing, L2 mechanism/action, a
  Tí-present scene). No automated scoring or LLM-based ranking; a human
  decides the visual style later. **Canonical Tí visual consistency
  across generated stills is not solved** — no reference-image
  conditioning or character sheet exists yet.
- **No locked-contract changes** — all 12 required compatibility checks
  came back clean. No Pillow (or OpenCV/ffmpeg/torch) dependency added.
- 55 new tests (45 adapter-level, 4 router, 5 full-renderer
  integration incl. a vendor-independence source-scan, 1 live-smoke
  skip check); all 1087 prior tests continue to pass unmodified (1142
  total, 1140 passed + 2 opt-in skips).

Phase 20.1 fixes a genuine implementation mistake exposed by a real live
call, immediately after Phase 20:

- **Root cause**: `client.models.generate_images(...)` (Phase 20's
  choice) requires Vertex AI / Gemini Enterprise Agent Platform
  credentials and simply does not work with this project's Gemini
  Developer API / AI Studio key — confirmed by an actual live
  `ValueError` from the installed SDK, not a hypothetical. Not glossed
  over: this was wrong, not merely superseded.
- **Fix**: `GeminiImageProvider` now calls
  `client.models.generate_content(...)` with
  `response_modalities=["IMAGE"]` and a `types.ImageConfig(aspect_ratio=
  ...)` — the same `generate_content` entrypoint `GeminiTTSProvider`
  already uses, verified locally against the installed SDK (not
  memory): `types.Modality.IMAGE` and `types.ImageConfig` both exist and
  are documented for exactly this purpose. Default model changed to
  **`gemini-2.5-flash-image`**, still fully configurable, never silently
  substituted. Response extraction was rewritten for
  `GenerateContentResponse`'s shape (first image part used, text parts
  ignored, blocked-prompt reason surfaced); `provider_request_id` can
  now be populated from `response.response_id` when the SDK sets it.
- **API key precedence re-verified, no change needed**: the confusing
  "Using GOOGLE_API_KEY" log line from the live run was confirmed (by
  reading SDK source and by an empirical check) to be a false alarm —
  `GeminiImageProvider` already passed the configured env var's value
  explicitly to `genai.Client(api_key=...)`, which always wins over
  ambient environment resolution. A dedicated test now proves this
  directly.
- **Contracts unchanged**: `VisualProvider`/`VisualRenderRequest`/
  `VisualRenderResponse`/`VisualSettings`/`VisualRenderer`/`VisualPlan`/
  `VisualBeat`/`MediaTypeVisualProvider` — all untouched. The fix stayed
  entirely inside `app/visual/providers/gemini.py` and its tests;
  `tests/test_gemini_image_live_smoke.py` and
  `scripts/evaluate_gemini_image.py` needed no edits.
- 9 net new/changed tests (54 adapter-level total); all 1142 prior tests
  continue to pass unmodified (1151 total, 1149 passed + 2 opt-in
  skips).

Phase 20.2 adds a second real visual provider, purely to unblock
evaluation while Gemini's account quota is resolved:

- **`CloudflareImageProvider`** (`app/visual/providers/cloudflare.py`)
  — additive only: `GeminiImageProvider` is untouched, still correct,
  still implemented. `VisualProvider`/`VisualRenderRequest`/
  `VisualRenderResponse`/`VisualSettings`/`VisualRenderer`/
  `MediaTypeVisualProvider` all needed **zero** changes. Backed by
  Cloudflare Workers AI's default model
  `@cf/black-forest-labs/flux-1-schnell` via a plain REST call
  (`POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}`,
  `Authorization: Bearer {token}`) — no SDK added; reuses the `httpx`
  dependency already present transitively via `google-genai` (now also
  declared directly in `pyproject.toml`, since this module makes real
  HTTP calls with it).
- **GENERATED_STILL + JPG only** — DIAGRAM and every other media type,
  and PNG output, are rejected before any HTTP call. JPG because
  FLUX.1 Schnell's official examples treat its base64 `result.image`
  output as JPEG. `VisualOutputFormat`/`VisualRenderer` weren't
  changed: wiring this provider just means configuring
  `VisualSettings(output_format=VisualOutputFormat.JPG)`.
- **Authentication**: `CLOUDFLARE_ACCOUNT_ID`/`CLOUDFLARE_API_TOKEN`
  read from the environment eagerly at construction (missing either
  raises `CloudflareImageConfigurationError` before any HTTP call); an
  injected HTTP client may be paired with explicit `account_id=`/
  `api_token=` constructor arguments so tests need zero real
  credentials and zero network. The token is used only to build the
  `Authorization` header — never logged, never in an error message,
  never stored on the (secret-free) `CloudflareImageConfig`.
- **Prompt translation**: its own independently-defined
  `_build_prompt`/style-guidance block — not shared with `gemini.py`'s
  (proven by a dedicated test) — encoding the same locked visual
  language, wrapping `concept`/`primary_focus`/`secondary_elements`/
  `context_elements` verbatim. **FLUX prompt-only generation does not
  solve canonical Tí character consistency any more than Gemini's
  does** — restated explicitly, not glossed over.
- **Response handling**: validates JSON shape, `success: true`, a
  non-blank `result.image`, and successful non-empty base64 decoding —
  each failure raises `VisualOutputError` explicitly (never a raw
  `KeyError`/`TypeError`/`binascii.Error`). A `success: false` envelope
  or a non-2xx HTTP status both translate to `VisualProviderError`
  (retryable); `httpx.HTTPError` network failures translate the same
  way. `width`/`height` stay `None` (no dimension data in the envelope,
  no Pillow added); `provider_request_id` reads the `cf-ray` header
  when present, `None` otherwise.
- **No nested retry** — the adapter retries nothing itself;
  `VisualRenderer` already owns that policy. No automatic quota
  handling or provider/model fallback of any kind.
- **`tests/test_cloudflare_image_live_smoke.py`** — opt-in, gated by a
  registered `live_cloudflare_image` marker and
  `RUN_LIVE_CLOUDFLARE_IMAGE=1`; skips cleanly otherwise.
  **`scripts/evaluate_cloudflare_image.py`** — reuses the exact same
  three conceptual briefs as `scripts/evaluate_gemini_image.py`
  (unmodified, to keep the comparison fair), writing one JPG per brief
  to `data/cloudflare_image_evaluation/`. No automated scoring or
  LLM-based ranking.
- **No locked-contract changes** — all 12 required compatibility
  checks came back clean; `app/renderers/visual/renderer.py` was not
  modified at all (proven by a dedicated source-scan test).
- 57 new tests (52 adapter-level, 4 full-renderer integration incl. a
  vendor-independence source-scan and production routing to Cloudflare
  with DIAGRAM unavailable, 1 live-smoke skip check); all 1151 prior
  tests continue to pass unmodified (1208 total, 1205 passed + 3
  opt-in skips).

**Phase 20.2 human evaluation and role decision (2026-09-06,
COMPLETE):** `scripts/evaluate_cloudflare_image.py` was run once
against real Cloudflare credentials (3 pre-existing briefs, no prompt
tuning, no model change). Human review: `L1_ESTABLISH` acceptable for
production background use; `L2_MECHANISM` usable but too
cinematic/random for deterministic mechanism explanation;
`TI_PRESENT` rejected for production mascot use (identity/style
drift). **Decision: `CloudflareImageProvider` is kept but is *not* the
universal visual provider — its approved production role is limited
to background generation, establishing scenes, environmental
illustration, and generic scene/object stills where exact character
identity is not required.** Not approved for canonical Tí mascot
generation, character consistency, brand-defining character art,
diagrams, or exact historical/mechanical relationships requiring
deterministic precision. No code changed. Future intended visual
strategy (not yet implemented): AI provider → backgrounds/environments/
generic scenes; Tí → a separate canonical reusable deterministic asset
mechanism; diagrams → a separate deterministic/semi-deterministic
system; text/labels → an editor/compositor layer. Full record in
`docs/TECHNICAL_SPEC_v0.1.md` and `docs/CHANGELOG.md`.

**Phase 21 — Canonical Tí Asset System** implements the "Tí → a separate
canonical reusable deterministic asset mechanism" line from Phase 20.2's
decision above, as **contracts, storage, validation, and retrieval
only** — no scene compositing, no image generation for Tí, and no
`VisualRenderer`/`Cloudflare`/`Gemini` changes.

- **`app/models/ti_assets.py`** — `TiAssetType` (`STILL` only, MVP),
  `TiState` (`NEUTRAL`/`CURIOUS`/`SKEPTICAL`/`EXCITED`/`SERIOUS`/
  `DEADPAN`/`PANIC`/`LOW_ENERGY` — deliberately matching `VoiceState`'s
  vocabulary exactly, **not** `VisualTiState`'s, per this phase's
  instruction to align with the voice-state system; the two visual-side
  enums are now intentionally different, an open decision, not an
  oversight — see the Technical Spec), `TiAsset` (one canonical file per
  state: `relative_path`, `output_format`, `mime_type`, `width`/`height`,
  `transparent_background`, `notes` — never image bytes), `TiAssetSet` (a
  versioned, `is_active`-flagged collection of `TiAsset`s that **enforces
  no-duplicate-state and full-8-state completeness at construction time**
  — an incomplete or conflicting set cannot be built in memory, let alone
  stored).
- **`app/ti_assets/`** (new top-level package, sibling to `app/visual/`/
  `app/audio/`) — `storage.py` (`TiAssetFileStore`: atomic `write`, plus
  `exists`/`resolve` for retrieval-time checks; `build_relative_path()`
  implements the `{asset_set_id}/{version}/{state}.{ext}` convention),
  `lookup.py` (exact-match `TiState -> TiAsset`, no fuzzy/LLM selection),
  `validation.py` (`validate_files_exist` — the one rule needing disk
  access), `errors.py`, and `retriever.py` — the **future integration
  boundary**: a `TiAssetRetriever` `Protocol` (`get_active_asset_set`/
  `get_asset`/`list_available_states`/`resolve_path`) plus its one
  concrete implementation, `SqliteTiAssetRetriever`. Mirrors
  `VisualProvider`'s role exactly, so a future compositor depends on the
  Protocol, not on SQLite/filesystem details.
- **Storage**: a new `TiAssetSetRow` (`app/storage/orm.py`, composite
  primary key `(asset_set_id, version)`) plus `app/storage/ti_assets.py`
  (`save_ti_asset_set`/`get_ti_asset_set`/`get_active_ti_asset_set`/
  `list_ti_asset_sets`/`activate_ti_asset_set`) — the full `TiAssetSet` is
  stored as one validated JSON payload per version (never image bytes,
  same pattern as `ArtifactRow`); activating a version atomically
  deactivates every other row in the same transaction, so **at most one
  row system-wide can ever be active**. `is_active` is also a dedicated
  column (not read from the JSON blob) specifically so this atomic
  bulk-deactivate never has to rewrite every other row's payload.
- **Not wired anywhere**: `VisualRenderer`, `app/visual/router.py`, and
  both real `VisualProvider`s are completely untouched.
  `VisualMediaType.TI_STATE` beats still resolve to a
  `"ti_state:<VisualTiState value>"` placeholder reference exactly as
  before Phase 21 (see `app/renderers/visual/renderer.py`'s
  `_reuse_only_requirement`) — a future phase would resolve that
  reference through `TiAssetRetriever` instead, but Phase 21 does not do
  so.
- 91 new tests (model validation, exact-match lookup, filesystem
  path/write/exists behavior, repository round-trip/versioning/
  single-active-set invariant/corrupt-payload handling, and
  end-to-end retriever behavior incl. missing-file and no-active-set
  failure); all prior tests continue to pass unmodified.

**Phase 21.1 — Tí State Mapping + Canonical Asset Ingestion (COMPLETE)**
closes the two concrete gaps Phase 21 left open, still contracts/storage/
retrieval only — no compositing, no AI generation, no `CloudflareImageProvider`/
`GeminiImageProvider` change.

- **`app/ti_assets/visual_state_mapping.py`** — one explicit,
  exhaustive `VisualTiState -> TiState` mapping
  (`VISUAL_TI_STATE_TO_TI_STATE`, a read-only `MappingProxyType`) plus
  `to_ti_state()`. `NEUTRAL`/`CURIOUS`/`SKEPTICAL`/`EXCITED`/`DEADPAN`/
  `PANIC` map like-for-like; the three `VisualTiState` values with no
  direct counterpart get an explicit canonical decision:
  `CONFUSED -> CURIOUS`, `SURPRISED -> EXCITED`, `SMUG -> SKEPTICAL`. No
  fuzzy matching, no name-string coercion, and no default to `NEUTRAL` --
  an unmapped value raises `ValueError`, and the module raises
  `RuntimeError` at import time if the table is ever missing a
  `VisualTiState` member. `TiState.SERIOUS`/`TiState.LOW_ENERGY` are
  documented as canonical assets with no `VisualTiState` source --
  selectable only by future compositor/editor logic directly, never
  produced by this mapping. `VisualTiState` and `TiState` remain
  deliberately separate enums (different domains: narrative-planning
  mood vs. brand-asset identity) -- this mapping is the bridge, not a
  merge.
- **`app/ti_assets/png_metadata.py`** — a small, dependency-free PNG
  header reader (`read_png_metadata`: signature/IHDR/tRNS only, never
  decodes pixel data). **Pillow was evaluated and not added**: it isn't
  currently installed or declared, and this phase's validation surface
  (valid-PNG check, width/height, alpha/tRNS presence) needs nothing
  Python's stdlib `struct` module can't already do, so the smaller
  dependency footprint (zero new dependencies) was chosen over adding a
  new one — mirroring `CloudflareImageProvider`'s earlier "reuse `httpx`,
  don't add an SDK" precedent.
- **`app/ti_assets/ingest.py`** — `ingest_ti_asset_set(engine, file_store,
  source_dir, version, notes=None, activate=False)`: validates the
  entire source directory (all 8 exact `STATE.png` filenames present,
  each a valid PNG with alpha/tRNS transparency) and checks the
  requested `version` isn't already stored, **before any database or
  file-store write**; only then copies files through `TiAssetFileStore`
  (existing path convention), builds `TiAsset`/`TiAssetSet`, and
  persists via the existing `save_ti_asset_set` (which already owns the
  atomic single-active-set flip). A validation failure lists every
  problem found, not just the first. A mid-copy filesystem failure
  propagates before any database row is written -- an incomplete set is
  never activated -- but files already copied before that failure are
  **not** cleaned up (documented honestly as an accepted, safe-but-not-
  tidy orphan-file possibility, consistent with "no automatic repair").
- **`scripts/ingest_ti_assets.py`** — the human-facing CLI:
  `python scripts/ingest_ti_assets.py --source <dir> --version v1
  [--notes "..."] [--activate]`. Fails clearly (exit 1, message to
  stderr) until a human supplies all 8 real PNGs -- fabricates nothing.
- 66 new tests (mapping exhaustiveness/determinism/non-mutation/no-
  fallback, PNG header parsing incl. alpha-via-color-type and
  alpha-via-tRNS and malformed/truncated/zero-dimension rejection,
  ingestion success/missing-file/wrong-filename/invalid-PNG/no-alpha/
  version-conflict/activate-flag behavior/no-bytes-in-DB, and CLI
  success/failure); all prior tests continue to pass unmodified.

**Phase 22 — Deterministic Tí Compositor** adds the first thing that
actually combines a `TiAsset` with a background image — still **not**
wired into `VisualRenderer`, `app/visual/router.py`, or either real
`VisualProvider`. Tí comes exclusively from the active `TiAssetSet` via
`TiAssetRetriever`; nothing here generates Tí, calls a `VisualProvider`,
or uses an LLM for placement.

- **`app/ti_compositor/`** (new top-level package, sibling to
  `app/ti_assets/`) — `models.py` (`TiAnchor`: the fixed 6-member MVP
  placement vocabulary — `BOTTOM_LEFT`/`BOTTOM_CENTER`/`BOTTOM_RIGHT`/
  `CENTER_LEFT`/`CENTER`/`CENTER_RIGHT`, no computer-vision or
  saliency-based placement; `TiScalePolicy` — Tí's rendered height as an
  explicit fraction of the background's height, `0 < relative_height <=
  1`, aspect ratio always preserved; `TiPlacement` — anchor + scale +
  `margin`/`offset_x`/`offset_y`; `TiCompositeRequest` — background path,
  output path, placement, and **exactly one** of `visual_ti_state` or
  `ti_state`, never both, never neither; `TiCompositeResult` — resolved
  state plus final pixel geometry and whether clamping occurred),
  `errors.py`, and `compositor.py` (`TiCompositor`, depending only on
  `TiAssetRetriever`).
- **`VisualTiState -> TiState` resolution reuses Phase 21.1's
  `to_ti_state()` unchanged** — when a request supplies
  `visual_ti_state`, `TiCompositor` resolves it through that existing
  deterministic mapping before ever calling
  `TiAssetRetriever.get_asset()`. No fuzzy state inference anywhere in
  this phase.
- **Out-of-bounds policy (documented, not left implicit): clamp first,
  fail only when clamping can't work.** `TiScalePolicy` already
  guarantees Tí's rendered height never exceeds the background's height;
  given that, a requested anchor+margin+offset position that would push
  Tí outside the frame is clamped back in-bounds (`TiCompositeResult.
  clamped` reports whether that happened) rather than the request being
  rejected. The one case this compositor refuses outright, via
  `TiCompositeBoundsError`, is a rendered Tí **width** wider than the
  background (possible when Tí's aspect ratio is wide relative to a
  narrow background) — no position could ever fit it, so it fails loudly
  instead of silently cropping.
- **Alpha compositing**: the canonical Tí PNG's own alpha channel is used
  as the paste mask directly (`Image.paste(ti, (x, y), ti)`) against an
  opaque background canvas — no white/black backing is ever baked in
  around Tí, and the background's own alpha (if a PNG background happens
  to have one) is intentionally flattened away first, since the
  background is always treated as the final opaque canvas, not a further
  composable layer.
- **Pillow added as a real project dependency** (`pyproject.toml`,
  `Pillow>=11.0,<13.0`) — a deliberate reversal of Phase 21.1's "Pillow
  evaluated and not added" call, because the need is genuinely different:
  Phase 21.1 only ever read PNG header bytes (`app/ti_assets/
  png_metadata.py`, untouched by this phase and still Pillow-free);
  Phase 22 actually decodes JPEG/PNG pixel data, resizes, and
  alpha-composites, which the stdlib cannot do without hand-writing a
  codec. Pillow is not imported into, and must never be imported into,
  `app/ti_assets/png_metadata.py`, `app/visual/providers/cloudflare.py`,
  or `app/visual/providers/gemini.py`.
- Supports PNG and JPG/JPEG backgrounds; output is PNG-only (deterministic
  lossless composition — enforced by `TiCompositeRequest`, not left as an
  unenforced preference).
- **Not wired anywhere**: `VisualRenderer`, `app/visual/router.py`, and
  both real `VisualProvider`s are completely untouched and their full
  test suites still pass unmodified. `VisualMediaType.TI_STATE` beats
  still resolve to the same `"ti_state:<VisualTiState value>"`
  placeholder reference as before — resolving that reference through
  `TiCompositor` is left to a future phase.
- **`scripts/evaluate_ti_compositor.py`** — a manual, no-scoring human
  evaluation script (same spirit as `scripts/evaluate_gemini_image.py`/
  `scripts/evaluate_cloudflare_image.py`): composites `NEUTRAL`
  (`BOTTOM_LEFT`), `CURIOUS` (`BOTTOM_RIGHT`), and `PANIC`
  (`CENTER_RIGHT`) from the active `v1` `TiAssetSet` onto the one
  existing background this repository already had that wasn't itself a
  Tí asset (`data/cloudflare_image_evaluation/L1_ESTABLISH.jpg`, from
  Phase 20.2) — no new background image is generated. Outputs land in
  `data/ti_compositor_evaluation/`.
- 39 new tests (pure anchor/scale/clamp math for every anchor, canonical-
  asset retrieval and `VisualTiState -> TiState` resolution through the
  real retriever, alpha-compositing correctness — opaque core fully
  overwrites, transparent border shows the background untouched, PNG and
  JPG backgrounds, output-dimensions-equal-background-dimensions,
  missing-active-set and missing-state-file failures, both out-of-bounds
  behaviors, byte-identical determinism across repeated runs, and no
  provider/LLM import or network-client construction); all prior tests
  continue to pass unmodified.

**Phase 23 — VisualRenderer ↔ Tí Compositor Integration** replaces
`TI_STATE`'s old placeholder-only handling (a `"ti_state:<VisualTiState
value>"` reference string, never resolved to anything real) with the
deterministic canonical resolution/compositing Phase 21-22 built but never
wired in. `CloudflareImageProvider`/`GeminiImageProvider` are completely
untouched, confirmed by their full pre-existing test suites still passing
unmodified; no LLM placement, no diagrams, no video compositing.

- **`VisualRenderer` gains one new optional constructor dependency**,
  `ti_compositor: TiCompositor | None = None` — the only new dependency,
  per this phase's preferred shape. `VisualRenderer` never instantiates
  `SqliteTiAssetRetriever`/`TiAssetFileStore`/a database engine for Tí
  assets itself; it only ever calls through the injected `TiCompositor`
  (including its new read-only `.retriever` property, below) exactly the
  way it already calls through an injected `VisualProvider`.
- **`TiCompositor` gains one new read-only property**, `.retriever` —
  exposes the underlying `TiAssetRetriever` for a caller (this phase's
  standalone `TI_STATE` mode) that needs canonical-asset identity/
  metadata without loading, resizing, or compositing any image bytes.
  `TiCompositor.composite()` itself is completely unchanged.
- **Two explicit, caller-selected `TI_STATE` modes** — never inferred from
  beat content — via a new `app/renderers/visual/models.py` contract,
  `TiStateSource` (`mode: TiStateRenderMode`, `background_path: Path |
  None`, `placement: TiPlacement | None`), supplied per beat through
  `VisualRendererInput.ti_state_sources: dict[str, TiStateSource]`:
  - **`STANDALONE`** (the default — a beat with no entry in that mapping
    gets this) — `beat.ti_state` resolves through Phase 21.1's existing
    `to_ti_state()` mapping, then `TiCompositor.retriever.get_asset()`
    resolves the exact canonical `TiAsset`. No compositing, no file
    written. Recorded as a `VisualRenderRequirement` with a new
    `VisualRequirementStatus.CANONICAL_ASSET_READY` status — `reference`
    points directly at the canonical asset's own `relative_path` (which
    already self-encodes asset-set id/version/state per
    `{asset_set_id}/{version}/{state}.ext`), and the new
    `resolved_ti_state` field records the resolved `TiState` itself.
  - **`COMPOSITE`** (only reachable via an explicit `TiStateSource` naming
    a `background_path` — this renderer never invents one) —
    `TiCompositor.composite()` renders Tí onto that background; the
    result becomes a real `RenderedVisualAsset` (PNG, `resolved_ti_state`
    set), the same shape as a `GENERATED_STILL`/`DIAGRAM` asset. Output is
    always `.png` regardless of `VisualSettings.output_format`, matching
    `TiCompositor`'s own PNG-only policy. A `TiStateSource` requesting
    `COMPOSITE` with no `background_path` raises
    `TiStateMissingBackgroundError` — clearly, before any compositing is
    attempted, never silently downgraded to `STANDALONE`.
- **No AI fallback, ever, for `TI_STATE`**: neither mode calls a
  `VisualProvider` — `TI_STATE` is absent from every provider-routing set
  in `app/renderers/visual/renderer.py`, structurally, not just by
  runtime check. A `TI_STATE` beat encountered with `ti_compositor=None`
  raises `TiCompositorNotConfiguredError` immediately, rather than ever
  reverting to the old placeholder string or calling a provider. Missing
  active canonical set / missing canonical file propagate the existing
  Phase 21 `TiAssetSetNotFoundError`/`TiAssetMissingFileError` unchanged —
  never wrapped, never silently substituted.
- **`ASSET_REUSE` is unaffected** and keeps its own `reuse_key` ->
  `REUSE_ONLY` behavior exactly as before. One removed behavior, called
  out explicitly: `TI_STATE` no longer honors an authored `reuse_key` as
  an override (the pre-Phase-21 "no canonical asset exists yet, so accept
  an arbitrary placeholder key instead" escape hatch) — a real canonical
  asset now always exists for every `TiState`, so that override no longer
  has a purpose, and keeping it would silently bypass canonical
  resolution.
- **`VisualRenderManifest`'s shape is not redesigned** — only two small,
  optional, backward-compatible additions: `RenderedVisualAsset.
  resolved_ti_state`/`.notes` and `VisualRenderRequirement.
  resolved_ti_state` (both `None` for every non-`TI_STATE` case), plus the
  one new `VisualRequirementStatus.CANONICAL_ASSET_READY` member.
  `VisualRendererResult` gains `canonical_asset_ready_count` alongside the
  existing counts.
- **ModuleRun lifecycle, freshness gates, manifest persistence, and
  deterministic asset paths are all unchanged** — `TI_STATE` composite
  output uses the exact same `{project_id}/{visual_plan_id}/
  {render_job_id}.png` convention as a provider-rendered asset. No
  provider retry budget is spent on `TiCompositor` — it is a deterministic
  local operation, never retried.
- 154 new/updated tests across `tests/test_visual_renderer.py`,
  `tests/test_visual_renderer_ti_state_integration.py` (new — the
  dedicated Phase 23 focused suite), `tests/test_visual_render_models.py`,
  and `tests/test_visual_render_validation.py`: standalone canonical
  resolution, `VisualTiState` mapping use, zero provider calls in either
  mode, composite mode invoking `TiCompositor` and registering a real
  asset, missing-background/missing-active-set/missing-canonical-file/
  not-configured failures, no-AI-fallback, unchanged non-`TI_STATE`
  routing, deterministic repeated output, and `ModuleRun`/freshness
  behavior preserved; all prior tests continue to pass (some updated in
  place where they asserted the now-superseded placeholder behavior).
- **`scripts/evaluate_visual_renderer_ti_state_integration.py`** — a
  manual, no-scoring human evaluation script that drives the real
  `VisualRenderer.run()` path (not `TiCompositor` in isolation) twice
  against one project with one `TI_STATE` beat: once `STANDALONE`, once
  `COMPOSITE` onto the existing
  `data/cloudflare_image_evaluation/L1_ESTABLISH.jpg` background (no new
  background generated) — using the real active `v1` `TiAssetSet`. Output
  lands in `data/visual_renderer_ti_state_evaluation/`.

**Phase 24 — Beat-to-Beat Visual Composition** lets a `TI_STATE`
`COMPOSITE` beat use the *rendered output of another `VisualBeat` in the
same `VisualPlan`* as its background, instead of requiring a
caller-supplied `background_path` on disk. This is explicit dependency
resolution the renderer computes from the plan's own beats, never AI
reasoning, never saliency/computer-vision placement, and never inferred
from list position — Cloudflare/Gemini stay generic scene/background
providers only, canonical Tí stays deterministic, and `TiCompositor`
itself is completely unchanged.

- **`TiStateSource` (`app/renderers/visual/models.py`) gains one new
  field**, `background_beat_id: str | None`, mutually exclusive with
  Phase 23's `background_path`. Validation is now explicit and exact,
  never inferred:
  - **`STANDALONE`** — both `background_path` and `background_beat_id`
    must be `None`. Setting either is a validation failure.
  - **`COMPOSITE`** — exactly one of `background_path`/
    `background_beat_id` must be set. Supplying both, or neither, is a
    validation failure raised immediately at `TiStateSource` construction
    (a `pydantic.ValidationError`), before a `VisualRendererInput` or
    `VisualRenderer.run()` is ever reached. This tightens Phase 23's
    contract, where a `COMPOSITE` `TiStateSource` with no
    `background_path` only failed later, at render time
    (`TiStateMissingBackgroundError`, now retired as unreachable and
    removed).
- **A deterministic beat dependency graph, built once per render pass,
  before anything renders** (`app/renderers/visual/renderer.py`):
  `_build_background_dependency_edges` walks every `TI_STATE` beat's
  `TiStateSource` and turns each `background_beat_id` into a dependency
  edge (background beat → dependent beat) — failing immediately, with no
  provider call, no `ModuleRun`, and no file write yet attempted, on an
  unknown beat_id (`UnknownBackgroundBeatError`) or a self-reference
  (`SelfReferentialBackgroundBeatError`). No fuzzy matching, no
  "nearest previous beat", no list-position assumptions — an edge exists
  only for an exact, existing `background_beat_id`.
- **A stable, deterministic topological render order**
  (`_topological_beat_order`, plain Kahn's algorithm with an
  authored-index tiebreak — not a general workflow/orchestrator
  framework): a dependency always renders before its dependent; a cycle
  fails loudly (`BackgroundBeatCycleError`) before any beat in the plan
  renders; and the authored `VisualPlan.beats` order is preserved exactly
  wherever the dependency graph does not force a beat later — a beat
  named as a `background_beat_id` may be authored *after* the beat that
  depends on it and still renders first.
- **Resolution never re-renders, copies, or mutates anything.** Once a
  background beat renders in the same pass (`GENERATED_STILL`/`DIAGRAM`
  via a `VisualProvider`, or `TI_STATE` `COMPOSITE` via `TiCompositor` —
  standalone `TI_STATE`, `ASSET_REUSE`, and every `EXTERNAL_REQUIRED`
  media type never produce a `RenderedVisualAsset` and so can never
  satisfy a `background_beat_id`), its `RenderedVisualAsset` is looked up
  from that same pass's in-memory results and resolved to
  `VisualFileStore.root / file_path`; that existing file is handed to
  `TiCompositor` exactly as an explicit `background_path` always was — no
  temp-file copy, no byte duplication, no mutation of the source asset. A
  `background_beat_id` naming a beat that resolved to a requirement
  instead of an asset raises `BackgroundBeatNotRenderedError`; one whose
  asset file is missing on disk raises `BackgroundBeatAssetMissingError`.
  Neither ever falls back to `STANDALONE`, generates a replacement
  background, or calls a `VisualProvider`.
- **Zero extra provider calls for beat-to-beat composition.** A
  `GENERATED_STILL` beat used as a background still makes exactly one
  provider call, as before; the dependent `TI_STATE` `COMPOSITE` beat
  consuming its output makes zero — `TI_STATE` still has no AI fallback,
  in either background-source mode.
- **`VisualRenderManifest` keeps both assets as first-class entries** —
  the background beat's own `RenderedVisualAsset` (e.g. a
  `GENERATED_STILL` `.jpg`/`.png`) and the dependent `TI_STATE` beat's
  composited `.png` both appear in `manifest.assets`, at their existing
  deterministic `{project_id}/{visual_plan_id}/{render_job_id}.<ext>`
  paths — nothing is replaced or hidden.
- **`ModuleRun` lifecycle, `ScriptPlan`/`VoicePlan`/`VisualPlan` freshness
  gates, manifest persistence, and provider retry ownership are all
  unchanged.** All dependency-graph validation happens before the
  `ModuleRun` is marked `RUNNING`'s work begins, so a graph error (unknown
  beat, self-reference, cycle) still records a `FAILED` `ModuleRun`
  through the exact same broad `except Exception` path every other
  renderer failure already used.
- **28 new focused tests** in
  `tests/test_visual_renderer_beat_composition.py` (new — the dedicated
  Phase 24 suite): `TiStateSource` contract validation (`STANDALONE`
  rejects both fields, `COMPOSITE` requires exactly one), pure unit tests
  over the dependency-graph helpers (dependency ordering, authored-order
  preservation when a background beat is listed later, independent beats
  keep their authored order, a minimal-reorder stability case, self-
  reference/unknown-beat/direct- and multi-beat-cycle failures), full
  `VisualRenderer.run()` resolution tests (a real rendered background
  resolves correctly, a requirement-only beat fails explicitly, a missing
  rendered file fails explicitly, one `TI_STATE` composite consuming
  another acyclically), compatibility checks (`background_path` mode,
  `STANDALONE` mode, `ASSET_REUSE`, non-`TI_STATE` routing all
  unchanged), provider-call-count assertions, manifest-shape assertions,
  and `ModuleRun` success/failure lifecycle preservation. Two Phase 23
  tests that asserted the now-retired
  `TiStateMissingBackgroundError`/no-background runtime path were updated
  in place to assert the new construction-time `ValidationError` instead;
  every other Phase 19/21/22/23 test continues to pass unmodified.
- **`scripts/evaluate_visual_renderer_beat_composition.py`** — a manual,
  no-scoring human evaluation script that drives the real
  `VisualRenderer.run()` path against one disposable project with two
  beats, `V2` (`TI_STATE` `COMPOSITE`, `background_beat_id="V1"`)
  authored *before* `V1` (`GENERATED_STILL`, rendered via
  `FakeVisualProvider` with a real decodable fixture image — never a live
  API) in the `VisualPlan`'s own beat list, specifically to prove
  dependency ordering is real and not an accident of list order. Uses the
  real active `v1` `TiAssetSet`. Output lands in
  `data/visual_renderer_beat_composition_evaluation/`.

**Phase 25 — Deterministic Diagram Renderer** replaces `DIAGRAM`'s
provider-based handling with a new, fully local, deterministic
`DiagramRenderer` (`app/diagram_renderer/`) — `DIAGRAM` beats no longer
depend on AI image generation at all. This is structural rendering, not
AI reasoning: a `DiagramSpec` declares exactly what to draw, in exactly
what position, and `DiagramRenderer` draws exactly that with Pillow —
nothing here infers layout, content, or placement. Canonical Tí
(Phase 21–23) and AI scene/background generation (Cloudflare/Gemini)
remain completely separate and unchanged.

- **A new minimum typed diagram contract**, `app/models/diagram.py` (pure
  pydantic, zero Pillow dependency, so `VisualBeat` — which now carries
  `diagram_spec: DiagramSpec | None` — never needs one either):
  - `DiagramCanvas` — explicit pixel `width`/`height`, plus
    `background_color: str | None` (a hex string paints an opaque
    background of that color; `None` means a fully transparent PNG —
    documented explicitly, not left ambiguous).
  - `DiagramPoint` — a position in **normalized `[0.0, 1.0]` diagram
    space**, resolution-independent; `DiagramRenderer` is the only place
    that ever converts one to a pixel coordinate, via
    `DiagramCanvas.width`/`height`. No hidden auto-layout anywhere.
  - `DiagramElement` — a `type`-discriminated union of exactly 8 element
    models: `DiagramLine`, `DiagramArrow`, `DiagramRectangle`,
    `DiagramEllipse`, `DiagramPolyline`, `DiagramTextLabel`, `DiagramArc`
    (needed for angle/relationship diagrams), and `DiagramDot` (a small
    filled circle — kept because it materially simplifies a point-mass/
    pivot marker). No other element type; pydantic itself rejects an
    unrecognized `type` value.
  - `DiagramStyle` — the minimum explicit styling: `stroke_color`,
    `stroke_width`, `fill_color`, `line_style` (`SOLID`/`DASHED`),
    `font_size`, `text_color`, `arrowhead_size`. No gradients, no
    textures, no external fonts.
  - `DiagramSpec` — one `DiagramCanvas` + a non-empty, ordered element
    list (later elements paint over earlier ones). Validates: canvas
    dimensions `> 0`; every coordinate strictly within `[0.0, 1.0]`;
    non-empty element list; non-blank `TEXT_LABEL.text`; zero-length
    `LINE`/`ARROW` rejected; zero-area `RECTANGLE` rejected; `ELLIPSE`/
    `ARC`/`DOT` radii within `(0.0, 1.0]`; `ARC` sweep angle nonzero and
    `<= 360` degrees; duplicate `element_id` values rejected. All via
    plain pydantic `model_validator`s (mirroring `TiCompositeRequest`/
    `TiPlacement`), not a bespoke validation-error hierarchy.
- **`app/diagram_renderer/`** (new package, mirrors `app/ti_compositor/`'s
  architecture-boundary role exactly): `DiagramRenderer.render(spec,
  output_path) -> DiagramRenderResult` draws every element with Pillow's
  `ImageDraw` and writes a PNG. No injected dependency and no
  configuration at all — unlike `TiCompositor`, there is no external
  state (no database, no asset set) to inject, so `VisualRenderer`
  instantiates a default one itself. Text uses Pillow's own bundled
  bitmap default font (`ImageFont.load_default(size=...)`, Pillow ≥10.1's
  sized variant) — no external font file is ever downloaded or read from
  the host. Output is deterministic: identical `DiagramSpec` in always
  produces byte-identical PNG bytes out.
- **`VisualRenderer` integration**: `DIAGRAM` is removed from the
  provider-routed media-type set entirely — `_render_diagram_beat` calls
  `DiagramRenderer` directly, never `VisualProvider.render()`, never
  retried (a deterministic local operation, exactly like `TiCompositor`).
  A new upfront check, `_validate_diagram_specs` (run before any beat
  renders, mirroring Phase 24's dependency-graph validation timing),
  raises `MissingDiagramSpecError` for a `DIAGRAM` beat with no
  `diagram_spec` and `UnexpectedDiagramSpecError` for a non-`DIAGRAM` beat
  that carries one — both checked where the field is actually consumed,
  the same pattern `TI_STATE`'s own `ti_state` requirement already used,
  not a new `VisualBeat`-level cross-field validator. A `DiagramRenderer`
  failure raises `DiagramRenderError`, propagates unchanged, and is
  recorded as a `FAILED` `ModuleRun` through the existing broad
  `except Exception` path — no AI fallback, no substitute generic still,
  ever.
- **Manifest, `ModuleRun` lifecycle, and freshness gates are all
  unaffected** — a `DIAGRAM` `RenderedVisualAsset` uses the exact same
  `{project_id}/{visual_plan_id}/{render_job_id}.png` path convention as
  every other rendered asset, and (as a direct consequence) a `DIAGRAM`
  beat is now also a valid Phase 24 `background_beat_id` source, since it
  always produces a real `RenderedVisualAsset`.
- **71 new focused tests**: `tests/test_diagram_models.py` (contract
  validation), `tests/test_diagram_renderer.py` (every element type
  renders; PNG output; output dimensions equal canvas dimensions; both
  background policies; deterministic repeated output; render-failure
  handling; structurally no network/provider dependency),
  `tests/test_visual_renderer_diagram_integration.py` (zero provider
  calls, manifest shape, `MissingDiagramSpecError`/
  `UnexpectedDiagramSpecError`, render-failure → `FAILED` `ModuleRun`,
  freshness gates preserved, deterministic reruns, a `DIAGRAM` beat
  usable as a Phase 24 background source, authored-order preservation).
  `tests/test_visual_renderer.py`, `tests/test_visual_renderer_
  cloudflare_integration.py`, and `tests/test_visual_renderer_gemini_
  integration.py` were updated in place wherever they asserted `DIAGRAM`'s
  now-retired provider-routed behavior (a `DIAGRAM` beat used to reach —
  and, in production routing, fail against — a `VisualProvider`/router
  with no `DIAGRAM` entry; it now always succeeds locally instead); every
  other prior test continues to pass unmodified.
- **`scripts/evaluate_diagram_renderer.py`** — a manual, no-scoring human
  evaluation script driving the real `DiagramRenderer` path directly
  (mirroring `scripts/evaluate_ti_compositor.py`'s shape): renders 3
  hand-authored diagrams — `FORCE_BLOCK` (a block with three labeled
  force arrows), `SUN_STICK_SHADOW` (a stick, a sun with rays, and its
  cast shadow), and `CIRCLE_ANGLE_RELATION` (a circle, two radii, and an
  angle arc) — covering every element type between them, and verifies
  determinism inline by rendering each twice and comparing bytes. No live
  image API, no AI generation. Output lands in
  `data/diagram_renderer_evaluation/`.

**Phase 26 — Deterministic Visual Layer Composition** introduces a small
service, `VisualLayerCompositor` (`app/layer_compositor/`), that
combines multiple ALREADY-EXISTING rendered visual assets into one final
static PNG frame. It sits ABOVE `GENERATED_STILL`/`TI_STATE`/`DIAGRAM`
without modifying, replacing, or depending on any of them — it creates
no new visual content, only structural composition: alpha-compositing
raster images at caller-specified positions and scales. AI providers
still create scene/background imagery only; canonical Tí and diagrams
remain exactly as deterministic as Phases 21–25 left them.

- **`app/layer_compositor/`** (new package, mirrors `app/ti_compositor/`'s
  and `app/diagram_renderer/`'s architecture-boundary role): no injected
  dependency, no configuration, no database/manifest/provider access of
  any kind — `VisualLayerCompositor.compose(spec) -> LayerCompositionResult`
  receives only concrete, already-resolved file paths.
  - `LayerSourceType` — exactly two kinds, `BACKGROUND` and `OVERLAY`; no
    semantic "Tí layer"/"diagram layer" concept — every layer is treated
    as a plain raster image regardless of what produced it.
  - `LayerAnchor` — a 9-position placement vocabulary (`TOP_LEFT` through
    `BOTTOM_RIGHT`, all 3×3 combinations), plus explicit `margin`/
    `offset_x`/`offset_y`. No automatic/saliency-based placement.
  - `LayerScale` — `relative_height`/`relative_width` (each in
    `(0.0, 1.0]`), at most one supplied; both `None` means the overlay's
    native pixel size. Aspect ratio is always preserved — never distorted.
  - `BackgroundLayer`/`OverlayLayer` (a `source_type`-discriminated
    union) — `BackgroundLayer` structurally has no anchor/scale/margin/
    offset fields at all (meaningless for the layer that defines the
    canvas). Exactly one `BackgroundLayer` is required per
    `LayerCompositionSpec`; zero or multiple is a validation failure. The
    background's own pixel dimensions become the final canvas size — no
    auto-sizing from overlays.
  - **Z-order**: overlays draw in ascending `z_index` order; ties break by
    authored list order (Python's own stable sort, no duplicate-`z_index`
    rejection) — the background always draws first, beneath every
    overlay, structurally (it has no `z_index` field to even compete on).
  - **Bounds policy**: identical to `TiCompositor`'s Phase 22 policy —
    an in-frame requested position is clamped to stay fully on-canvas
    (`LayerGeometry.clamped` reports whether that happened); a scaled
    overlay whose width OR height exceeds the canvas outright raises
    `LayerCompositionBoundsError` rather than silently cropping.
  - Output is always PNG; alpha compositing only (no blend modes, no
    gradients, no textures) — a transparent PNG overlay reveals the
    background beneath it correctly, and an opaque JPEG overlay covers
    it completely.
- **`app/renderers/visual/models.py`** gains `CompositionSpec`/
  `BackgroundLayerSource`/`OverlayLayerSource` — the renderer-input,
  pre-resolution counterpart to `VisualLayer`: each layer source carries
  exactly one of an explicit `source_path` or a `source_beat_id`
  (mirroring `TiStateSource`'s `background_path`/`background_beat_id`
  duality from Phase 24), resolved to a concrete path by the renderer
  before `VisualLayerCompositor` ever sees it.
  `VisualRendererInput.composition_specs: dict[str, CompositionSpec]`
  supplies these per beat, keyed by `beat_id` — unlike `ti_state_sources`,
  there is no default; a `COMPOSITION` beat with no entry fails explicitly
  (`MissingCompositionSpecError`).
- **A new `VisualMediaType.COMPOSITION` member** — the minimum contract
  change needed to give a beat a routable identity for "combine other
  beats' outputs" (deliberately a rendering-layer-only concept, not
  something Visual Planning's LLM prompt is updated to choose).
  `VisualRenderer` routes it to a new dedicated branch,
  `_render_composition_beat`, exactly parallel to `TI_STATE`'s and
  `DIAGRAM`'s own branches — never through `VisualProvider`, never
  retried.
- **Phase 24's dependency-graph machinery is reused, not duplicated.**
  `_build_background_dependency_edges` is generalized (via a new
  `_iter_beat_to_beat_references` helper) to also turn every
  `CompositionSpec.layers[*].source_beat_id` into a dependency edge,
  alongside `TiStateSource.background_beat_id` — the same topological
  sort, the same `UnknownBackgroundBeatError`/
  `SelfReferentialBackgroundBeatError`/`BackgroundBeatCycleError`, one
  dependency engine for both use cases. A referenced beat may be authored
  anywhere in the `VisualPlan` — before or after the beat that depends on
  it — and still renders first.
- **A composition layer may resolve to a standalone canonical Tí
  requirement — deliberately broader than TI_STATE's own background
  rule.** A `TI_STATE` beat rendered in `STANDALONE` mode never produces
  a `RenderedVisualAsset` (it stays a `CANONICAL_ASSET_READY`
  requirement, Phase 23) and is therefore invalid as another `TI_STATE`
  beat's `background_beat_id` (Phase 24, unchanged). A `COMPOSITION`
  layer's `source_beat_id`, however, MAY reference such a beat: the
  renderer resolves it via the same injected `TiCompositor`'s retriever
  the original beat used (`get_asset()`/`resolve_path()`), never a second
  lookup path. Any other unresolvable reference (`REUSE_ONLY`,
  `EXTERNAL_REQUIRED`, or an unknown/self-referential/cyclic beat_id)
  fails explicitly (`CompositionSourceNotRenderedError`, or the shared
  Phase 24 graph errors) — never a silently dropped layer, never a
  provider fallback.
- **Zero extra provider calls.** `GENERATED_STILL`/`DIAGRAM`/`TI_STATE`
  beats consumed as composition layers call a provider exactly as they
  would on their own (zero, one, or the router's own count); the
  `COMPOSITION` beat itself always contributes zero.
- **Manifest keeps full traceability.** The background/diagram/Tí source
  assets remain first-class `manifest.assets`/`requirements` entries
  alongside the new composed-frame `RenderedVisualAsset` — nothing is
  hidden, replaced, or merged away. The composite's `notes` field records
  which source beat_ids it was built from.
- **78 new focused tests**: `tests/test_layer_compositor_models.py`
  (contract validation: exactly one background, duplicate/blank ids,
  invalid scale, PNG-only output), `tests/test_layer_compositor.py`
  (every anchor, offsets/margins, native/relative-height/relative-width
  scaling with aspect ratio preserved, alpha correctness, PNG/JPG
  backgrounds, transparent PNG and opaque JPEG overlays, z-order and
  stable ties, oversized-overlay/clamp behavior, missing/corrupt sources,
  deterministic repeated output), and
  `tests/test_visual_renderer_composition_integration.py`
  (background+DIAGRAM, background+Tí, background+DIAGRAM+Tí, scrambled
  authored order, unknown/unresolvable/cyclic source failures, provider-
  call-count and manifest assertions, deterministic reruns, `ModuleRun`/
  freshness preservation). Every prior test continues to pass unmodified.
- **`scripts/evaluate_visual_layer_composition.py`** — a manual,
  no-scoring human evaluation script driving the real
  `VisualRenderer.run()` path against a disposable four-beat project
  authored in SCRAMBLED order (`V4` `COMPOSITION` first, `V1`
  `GENERATED_STILL` last), proving dependency ordering is real: `V4`'s
  `CompositionSpec` combines `V1` (background, via `FakeVisualProvider`
  with a real decodable fixture — never a live API), `V2` (a `DIAGRAM`
  overlay, rendered locally), and `V3` (a `TI_STATE` `STANDALONE` overlay,
  the real active `v1` canonical `TiAssetSet`) into one final PNG. Total
  provider calls for the whole run: 1. Output lands in
  `data/visual_layer_composition_evaluation/`.

**Phase 27 — Deterministic Timeline Assembly** introduces `TimelineBuilder`
(`app/renderers/timeline/`), which turns an already-locked ScriptPlan/
VoicePlan/VisualPlan/AssemblyPlan chain plus their already-rendered
VoiceRenderManifest/VisualRenderManifest outputs into one deterministic,
executable `TimelineManifest` (`app/models/timeline.py`) — the final
planning-adjacent artifact before real video encoding. No ffmpeg call, no
CapCut project, no audio mixing, no LLM timing inference: narration audio
duration is the sole timing authority, and every id/reference is resolved
from artifacts that already exist.

- **Timebase**: integer milliseconds, chosen explicitly over
  `AssemblyPlan`'s own float seconds (drift-prone across many segments)
  and over frames-per-second (a final-encoding concern, not a
  planning-adjacent one). Every `TimelineSegment` carries `start_ms`/
  `end_ms`/`duration_ms`, validated for internal consistency
  (`duration_ms == end_ms - start_ms`, `start_ms >= 0`, `end_ms >
  start_ms`).
- **Narration is the primary timing authority.** `_resolve_audio_duration_ms`
  prefers a `RenderedVoiceTake`'s own recorded `duration_seconds` (Phase
  17/18 provider metadata); when absent, it measures the EXACT duration
  from the WAV file's own header (`frames / framerate`) via Python's
  stdlib `wave` module — zero new dependency, since every real TTS
  provider in this project (`GeminiTTSProvider`) only emits WAV. A
  non-WAV file with no recorded duration fails explicitly
  (`UnresolvableAudioDurationError`) — never estimated from text length,
  never guessed.
- **Alignment reuses `AssemblyPlan` (Phase 15) entirely — no new contract
  field was needed.** `AssemblyPlan`'s own normalization already
  guarantees each segment's `voice_chunk_ids` are exactly the overlapping
  `VoiceChunk`s, and its own business validation already guarantees
  exactly one segment per `VisualBeat`, in `VisualPlan.beats`' own order.
  "Multiple narration chunks sharing one visual" (a documented
  requirement) is therefore already structurally represented: one
  segment's `voice_chunk_ids` may list more than one chunk, played
  consecutively under that segment's single visual reference.
- **Visual resolution never triggers rendering.** `TimelineVisualRef`
  mirrors the `RenderedVisualAsset`/`VisualRenderRequirement` duality
  (Phase 19+) directly: a beat that was actually rendered resolves to a
  concrete `file_path`; a beat that only produced a requirement (a
  standalone canonical Tí reference, an `ASSET_REUSE` `reuse_key`, or an
  `EXTERNAL_REQUIRED` placeholder) still resolves, via `reference`, for a
  future editor to place by hand — the timeline layer itself never calls
  `VisualRenderer`, `TiCompositor`, `DiagramRenderer`, or
  `VisualLayerCompositor`.
- **Transition downgrade, documented explicitly.** `AssemblyPlan`'s own
  `TransitionIntent` (5 members: `CUT`/`DISSOLVE`/`MATCH`/`PUSH`/`NONE`)
  is richer than a static timeline can execute. Every `TimelineSegment`
  keeps the original `TransitionIntent` in full
  (`source_transition_in`/`out`, never discarded) alongside a
  deterministically downgraded `TimelineTransitionType` (`CUT`/`HOLD`/
  `CROSSFADE` only): `CUT`→`CUT`, `NONE`→`HOLD`, `DISSOLVE`→`CROSSFADE`,
  and `MATCH`/`PUSH`→`CUT` (a motion-based transition cannot be executed
  by a static timeline yet — downgraded to an instantaneous cut rather
  than silently misrepresented, until a future motion-lite phase).
- **Music/SFX as lightweight cue events, not fake audio files.**
  `MusicState` (Phase 13: `BED`/`DUCK`/`LIFT`, no explicit "off" member)
  drives a fixed, deterministic cue-derivation rule: a cue fires whenever
  a segment's `music_state` differs from the previous one
  (`MUSIC_BED_START`/`MUSIC_DUCK`/`MUSIC_LIFT`), plus one closing
  `MUSIC_BED_END` at the timeline's end if music was ever used. Each
  `RenderedVoiceTake.sfx_opportunity` becomes one `SFX_TRIGGER` cue at its
  segment's start. `CUT`/`STATIC_HOLD` cue types exist in the vocabulary
  for a future flat-event-stream consumer but are never emitted by this
  builder — `TimelineSegment.transition_in`/`out` already carries that
  information per-segment, so duplicating it as cues was avoided.
- **Freshness is a six-artifact chain, all checked before anything is
  built.** ScriptPlan → VoicePlan → VisualPlan → AssemblyPlan →
  VoiceRenderManifest → VisualRenderManifest, each verified against the
  ones before it (mirroring every prior renderer's freshness-gate
  pattern) — a stale link anywhere fails immediately
  (`Stale*Error`/`Missing*ArtifactError`), before any file is read or
  `ModuleRun` is created.
- **`ModuleRun` lifecycle unchanged**: RUNNING → SUCCESS/FAILED, no
  project-state transition, no retry loop (deterministic local logic —
  there is nothing transient to retry, and `TimelineBuilder` calls no
  provider or LLM of any kind, confirmed by a fresh-subprocess import
  check exactly like every prior renderer).
- **69 new focused tests**: `tests/test_timeline_models.py` (contract
  validation), `tests/test_timeline_validation.py` (pure defense-in-depth
  checks: duplicate segment ids, gaps/overlaps, out-of-bounds cue
  timestamps), and `tests/test_timeline_builder.py` (timing from real
  audio metadata, cumulative/total duration, alignment, requirement vs.
  rendered visual resolution, music/SFX cue derivation, the
  `TransitionIntent` downgrade mapping, every freshness gate, `ModuleRun`
  lifecycle, deterministic reruns, no `app.llm` dependency). Every prior
  test continues to pass unmodified.
- **`scripts/evaluate_timeline_builder.py`** — a manual, no-scoring human
  evaluation script driving the real `VisualRenderer.run()` path (a
  `GENERATED_STILL` background via `FakeVisualProvider`, a local
  `DIAGRAM` overlay, and a `COMPOSITION` frame combining both) and then
  the real `TimelineBuilder.run()` path against a hand-authored
  `AssemblyPlan` and a `VoiceRenderManifest` pointing at three real local
  WAV fixtures — no live AI or TTS API of any kind. Prints a readable
  segment table (`segment_id`/`start_ms`/`end_ms`/`duration_ms`/
  `voice_ref`/`visual_ref`), total duration, and cue events, and persists
  the `TimelineManifest`. Output lands in
  `data/timeline_builder_evaluation/`.

**Phase 28 — Deterministic Local Video Encoder** consumes a Phase 27
`TimelineManifest` and executes it into the first real, locally playable
MP4: `VideoEncoder` (`app/video_encoder/`) combines already-rendered
static images and WAV narration via the system `ffmpeg`/`ffprobe`
executables, and `VideoRenderer` (`app/renderers/video/`) is the
artifact-driven, freshness-checked layer that resolves a `TimelineManifest`
into an encode job and persists the result. No AI reasoning occurs here —
this phase is pure execution.

- **FFmpeg dependency strategy**: the system/local `ffmpeg`/`ffprobe`
  executables only, invoked via `subprocess` with explicit argument lists
  (never `shell=True`, never an interpolated command string). No
  `ffmpeg-python`, no `moviepy`, no OpenCV. Resolved from
  `VideoEncodingSettings.ffmpeg_path`/`ffprobe_path` when given, else from
  `PATH` (`shutil.which`) — `FFmpegNotFoundError`/`FFprobeNotFoundError`
  if neither exists; this encoder never downloads or installs a binary.
- **Canvas policy**: the FIRST timeline segment's own resolved image
  establishes the output width/height (read once via Pillow); every
  later segment's image must match exactly, or
  `VideoDimensionMismatchError` fails the whole encode before `ffmpeg`
  ever runs — no silent resize/upscale/downscale. Output is always MP4/
  H.264/AAC/`yuv420p`/constant frame rate (30fps default).
- **Command construction is a pure function** (`FFmpegCommandBuilder.build`,
  `app/video_encoder/encoder.py`) — fully testable without invoking
  `ffmpeg`, and needs **no temporary concat-list files at all**: every
  segment's image is its own `-loop 1 -t <duration> -i <path>` input,
  every narration clip its own `-i <path>` input, and one
  `-filter_complex` graph (using `ffmpeg`'s `concat` FILTER, not the
  concat DEMUXER) does all concatenation in-memory. Reusing the same
  image across segments never duplicates bytes on disk — each segment
  simply gets its own input referencing the same existing file.
- **Narration timing**: each segment's narration clips play back-to-back
  from its start; if their real, re-measured total (via stdlib `wave`,
  the same technique Phase 27's `TimelineBuilder` already uses) is
  shorter than the segment's authored `duration_ms`, the remainder is
  silence-padded (`apad=whole_dur=...`) — never time-stretched, never
  loudness-normalized. If real audio would be LONGER than its authored
  region, encoding fails explicitly (`NarrationDurationOverflowError`)
  rather than truncating.
- **Transition policy**: `CUT` and `HOLD` both execute as an
  instantaneous visual replacement (no MVP fidelity difference between
  them). `CROSSFADE` (reachable via Phase 27's `DISSOLVE` mapping) is
  executed for real as of Phase 29 — see below.
- **Freshness is a deeper chain than Phase 27's own**: beyond the usual
  ScriptPlan→VoicePlan→VisualPlan→AssemblyPlan links, `VideoRenderer`
  also confirms the CURRENTLY stored `VoiceRenderManifest`/
  `VisualRenderManifest` are the EXACT ones (by id) the `TimelineManifest`
  recorded building from — catching the case where voice or visual assets
  were re-rendered after the timeline was built, without the timeline
  itself being rebuilt (`StaleTimelineManifestError`).
- **A segment whose visual was never actually rendered** (a
  `VisualRenderRequirement` rather than a file — e.g. a standalone
  canonical Tí reference) cannot be encoded and fails explicitly
  (`UnrenderedVisualSegmentError`) — Phase 28 never renders one on the
  fly.
- **`ModuleRun` lifecycle unchanged**: RUNNING → SUCCESS/FAILED, no
  project-state transition, no retry loop (an `ffmpeg` process failure is
  captured with its exit code and stderr via `VideoEncodingProcessError`,
  never retried automatically).
- **Determinism, precisely scoped**: identical `TimelineManifest`/input
  files/settings always produce an identical `ffmpeg` argv list and
  identical business timing (`EncodedVideoAsset.duration_ms` is always
  the timeline's own authoritative `total_duration_ms`, never a value
  re-measured from the output container). Byte-identical MP4 output
  across `ffmpeg` versions/environments is explicitly NOT guaranteed or
  required — only the command construction and business timing are.
- **74 new focused tests**: `tests/test_video_encoder_models.py`
  (contract/settings validation), `tests/test_video_encoder_command_builder.py`
  (pure `ffmpeg` argv construction: one/three segments, repeated visuals,
  multiple narration refs, paths with spaces, Windows-style paths, exact
  fps/codec/pixel-format selection, no shell invocation, determinism),
  `tests/test_video_encoder.py` (every validation/error path with an
  injected fake subprocess runner — missing/unsupported/mismatched
  visual or narration, narration overflow, `CROSSFADE` rejection, missing
  executables, non-zero exit, missing/zero-byte output, `ffprobe`
  verification), `tests/test_video_encoder_integration.py` (an opt-in,
  environment-aware test that runs a REAL 3-segment `ffmpeg` encode and
  re-verifies it with a direct `ffprobe` call — skipped cleanly when
  `ffmpeg`/`ffprobe` aren't on `PATH`), and `tests/test_video_renderer.py`
  (the artifact-driven layer: freshness gates including the new
  render-manifest-id check, `UnrenderedVisualSegmentError`, `ModuleRun`
  lifecycle, deterministic reruns). Every prior test continues to pass
  unmodified.
- **`scripts/evaluate_video_encoder.py`** — a manual, no-scoring human
  evaluation script extending `scripts/evaluate_timeline_builder.py`'s
  own three-frame project (with an all-`CUT`/`HOLD` `AssemblyPlan`, since
  that script's own `DISSOLVE` transition is exactly what Phase 28
  rejects) through the real `VideoRenderer.run()` path to produce exactly
  one MP4: `data/video_encoder_evaluation/motily_phase28_preview.mp4`.
  Prints path, file size, codecs, dimensions, fps, `ffprobe`-measured
  duration alongside the asset's and timeline's own authoritative
  duration (confirmed identical), and the local `ffmpeg` version. No live
  AI or TTS API of any kind.

**Phase 29 — Deterministic Motion-Lite Video Execution** closes Phase 28's
one hard blocker (`CROSSFADE` was rejected outright) and adds a
deliberately tiny, deterministic per-segment camera-motion vocabulary —
both still pure local `ffmpeg` execution, never AI reasoning.

- **`CROSSFADE` execution**: a real dissolve via `ffmpeg`'s `xfade`
  filter (`transition=fade`). Only `TimelineSegment.transition_out`
  decides whether segment *i* joins segment *i+1* as a crossfade —
  `transition_in` is never consulted anywhere (neither is the LAST
  segment's own `transition_out`, which has no next segment to blend
  into); this is a deliberate single-source-of-truth choice, not an
  oversight. Duration is one global `VideoEncodingSettings.
  crossfade_duration_ms` (default 300ms, no per-segment override —
  `AssemblySegment`/`TimelineSegment` carry no numeric transition-duration
  field today, and adding one was judged out of scope for this phase).
  Validation (`InvalidCrossfadeDurationError`, raised before `ffmpeg` ever
  runs): the configured duration must be strictly shorter than BOTH
  adjacent segments' own `duration_ms` — never silently shortened to fit.
- **Total duration is duration-neutral**: a naive `xfade` would shorten
  the combined output by the overlap, so the EARLIER segment's own
  `-loop` duration is extended by exactly `crossfade_duration_ms` before
  the fade runs, and `xfade`'s `offset` is always the sum of the prior
  segments' ORIGINAL (never-extended) durations — `TimelineManifest.
  total_duration_ms` still equals the final encoded duration exactly, in
  any chain mixing `CUT`/`HOLD`/`CROSSFADE` joins in any order.
  `CROSSFADE` is VISUAL ONLY — the narration filter graph is completely
  unchanged from Phase 28 (silence-padded against each segment's own
  ORIGINAL `duration_ms`, never the video-side-extended one).
- **Motion-lite vocabulary** (`VisualMotionType`, `app/models/timeline.py`):
  exactly `STATIC` (default), `SLOW_ZOOM_IN`, `SLOW_ZOOM_OUT`, `PAN_LEFT`,
  `PAN_RIGHT` — always explicitly authored
  (`TimelineBuilderInput.visual_motions: dict[segment_id, VisualMotionType]`,
  mirroring `ti_state_sources`/`composition_specs`'s own per-item-override
  shape), never inferred from content, never LLM-chosen, never
  CV/saliency-based. Lives on `TimelineSegment.visual_motion` (execution),
  not `ScriptPlan`/`VisualPlan`/`AssemblyPlan` (content/planning) — motion
  is a presentation decision, not a narrative or scientific one. Spans
  exactly a segment's own authored `duration_ms`; never changes segment
  start/end timing; operates only on the segment's FINAL resolved raster
  (never an individual Tí/diagram layer, never reopens
  `LayerCompositionSpec`).
- **Zoom uses `zoompan`, not `crop`**: a `crop` filter's `w`/`h` are
  evaluated ONCE at filter-graph configuration time (confirmed
  empirically — a `t`-based `w`/`h` expression fails immediately, before
  any frame is processed), so a time-varying crop SIZE cannot be built
  from `crop` alone. `zoompan`'s own well-known `zoom+=increment`
  self-referencing pattern is avoided entirely: the zoom expression is a
  pure function of `on` (zoompan's own monotonic output-frame index),
  never referencing its own previous value, so there is nothing to
  accumulate or drift. Range: `1.00→1.06` (`SLOW_ZOOM_IN`) / `1.06→1.00`
  (`SLOW_ZOOM_OUT`), deliberately small. Every segment's image input also
  gets an explicit `-framerate <fps>` (deterministic input frame count,
  which zoompan's `on`-based math depends on).
- **Pan uses `crop`'s `x`/`y`**, which genuinely ARE re-evaluated every
  frame: the source is pre-scaled 5% wider than the canvas, then a
  canvas-sized window's `x` position moves linearly across the available
  travel via a `t`-based expression. No vertical pan in this phase. If
  the configured canvas width is small enough that the 5% pre-scale
  rounds to zero added pixels, this fails explicitly
  (`MotionSourceTooSmallError`) rather than silently producing a
  zero-travel "pan."
- **A same-request timebase pitfall, found via manual evaluation and now
  regression-tested**: `zoompan`'s internal timebase does not match a
  plain `scale`/`crop` chain's — `ffmpeg`'s `concat` filter silently
  tolerates the mismatch, but a later `xfade` fed from that `concat`
  refuses to join two streams whose timebases differ. Every segment's own
  normalization chain now ends in `settb=AVTB`, putting every stream on
  the same arbitrary timebase before it ever reaches a `concat`/`xfade`
  node, regardless of join/motion order.
- **Required-filter check** (`FFmpegFilterUnavailableError`): before
  building the real command, this encoder queries the resolved `ffmpeg`
  executable's own `-filters` output once and confirms `xfade` (if any
  `CROSSFADE` join exists), `zoompan` (if any zoom segment exists), and
  `crop`/`scale` (if any pan segment exists) are actually present in this
  local build — never a silent fallback, never an automatic download of a
  different build.
- **`EncodedVideoAsset`/`VideoEncodeResult` gain two small optional
  fields**: `crossfade_count` (how many joins were a real `CROSSFADE`)
  and `motion_profile_used` (the distinct non-`STATIC` motions actually
  applied) — no second, competing video-output model.
- **New/updated focused tests** across `tests/test_video_encoder_models.py`
  (crossfade/motion field validation), `tests/test_video_encoder_command_builder.py`
  (pure `ffmpeg` argv construction: two/three-segment crossfade timing and
  `xfade` offsets, mixed `CUT`/`CROSSFADE` chains, default/custom
  crossfade duration, narration unchanged, all four motion filter
  expressions, determinism), `tests/test_video_encoder.py` (`transition_in`
  and last-segment `transition_out` ignored, invalid crossfade duration,
  required-filter check, `MotionSourceTooSmallError`), `tests/
  test_video_encoder_integration.py` (real `ffmpeg`: a real crossfade
  encode, a real one-segment-per-motion encode, and a regression test for
  the timebase pitfall above), `tests/test_timeline_builder.py`
  (`visual_motions` override/default/unknown-reference), and
  `tests/test_video_renderer.py` (a real crossfade encode end to end, and
  an `InvalidCrossfadeDurationError` failure path). Every Phase 28 test
  continues to pass, with the two that asserted blanket `CROSSFADE`
  rejection rewritten for Phase 29's real semantics.
- **`scripts/evaluate_motion_lite_video.py`** — extends `scripts/
  evaluate_video_encoder.py`'s own three-frame project with an
  `AssemblyPlan` authoring one `CUT` (S1→S2) and one real `CROSSFADE`
  (S2→S3, via `DISSOLVE`) plus `TimelineBuilderInput.visual_motions`
  overrides (`SLOW_ZOOM_IN` on S1, `PAN_RIGHT` on S3, S2 left at the
  `STATIC` default) through the real `VideoRenderer.run()` path to
  produce exactly one MP4:
  `data/motion_lite_evaluation/motily_phase29_preview.mp4`. Prints path,
  file size, codecs, fps, canvas, `ffprobe`-measured duration alongside
  the asset's and timeline's own authoritative duration, crossfade
  duration, crossfade count, motions used, and the local `ffmpeg`
  version. No live AI or TTS API of any kind.

**Phase 30 — Deterministic Music & SFX Mix Execution** turns
`TimelineManifest`'s existing `MUSIC_BED_START`/`MUSIC_DUCK`/`MUSIC_LIFT`/
`MUSIC_BED_END`/`SFX_TRIGGER` cues (Phase 27's own cue vocabulary, never
executed before now) into a real mixed audio track, layered underneath
the narration that remains the exact, unchanged timing authority. Still
pure local `ffmpeg` execution — no AI music selection, no beat matching,
no creative audio inference of any kind.

- **Explicit asset bindings, opt-in**: `AudioAssetBindings`
  (`music_bed_path: Path | None`, `sfx_by_id: dict[str, Path]`) is the
  only way a cue ever resolves to a real file — no inference, no
  filesystem scanning, no default/random SFX. `VideoRendererInput.
  audio_bindings` defaults to `None`, in which case `VideoRenderer` never
  even forwards the manifest's own cues to `VideoEncoder` — every
  pre-Phase-30 caller gets exactly Phase 29's audio behavior with zero
  code changes, regardless of the fact that a real manifest always
  carries at least one music cue (`MusicState` has no "none" member).
  Passing an `AudioAssetBindings` (even an empty one) opts into real mix
  execution, where a cue with no bound asset then fails explicitly.
- **Pure planning layer** (`app/video_encoder/audio_mix.py`,
  `build_audio_mix_plan`): no filesystem, no subprocess — turns cues +
  bindings into a flat `AudioMixPlan` (contiguous music gain regions,
  independent SFX events). The music state machine validates
  `MUSIC_BED_START` while already active, `MUSIC_DUCK`/`MUSIC_LIFT`/
  `MUSIC_BED_END` while inactive, out-of-order or duplicate-timestamp
  cues, and a region too short for its own gain ramp — all
  `InvalidMusicCueSequenceError`, never silently reordered or merged. An
  unterminated `MUSIC_BED_START` implicitly closes at the timeline's own
  `total_duration_ms` (this phase's chosen, documented policy).
- **Music**: exactly one background bed, input via `-stream_loop -1`
  (loops indefinitely at the demuxer level — this encoder never measures
  the source file's own duration) and cut to size per gain region via
  `atrim`, which naturally handles both "shorter than needed" (loops) and
  "longer than needed" (trims) with the same mechanism. Fixed gains
  (`VideoEncodingSettings.music_bed_gain_db`=-24dB/`music_duck_gain_db`
  =-32dB/`music_lift_gain_db`=-20dB by default) — never dynamically
  normalized, never inspects narration loudness. Adjacent gain regions
  are joined with `acrossfade` over `music_gain_ramp_ms` (80ms default) —
  the exact same duration-neutral extend-then-overlap trick this phase's
  own `CROSSFADE` support already uses for video, applied to audio, so
  the combined bed length always equals the naive sum of every region's
  own authored duration.
- **SFX**: each `SFX_TRIGGER` cue's own `reference` field is already a
  stable id (Phase 27's `TimelineBuilder` populates it from
  `RenderedVoiceTake.sfx_opportunity` — no new `TimelineCue` field was
  needed), resolved via `AudioAssetBindings.sfx_by_id`. Starts exactly at
  `cue.timestamp_ms` (never quantized to a frame boundary), trimmed at
  the timeline's own end if it would otherwise run past it, at one fixed
  `sfx_gain_db` (-10dB default).
- **Final mix**: narration (Phase 28/29's own exact, unchanged filter
  chain) plus the music bed (if any) plus every SFX event, combined via
  `amix=inputs=N:duration=longest:normalize=0` — `normalize=0` is
  essential, since `amix`'s own default silently scales every input down
  by `1/N`, which would defeat every gain level this phase deliberately
  chose. The outer command's own final `-t <total_duration_ms>` (Phase
  28's own) remains the authoritative hard bound regardless of internal
  filter-graph rounding — music and SFX can never extend the final
  output.
- **A same-request timebase pitfall was NOT an issue here** (unlike
  Phase 29's own `zoompan`/`xfade` mismatch) — the new `atrim`/`asplit`/
  `acrossfade`/`adelay`/`amix` audio filters all shared a consistent
  timebase in testing; no `settb` workaround was needed on the audio
  side.
- **`EncodedVideoAsset`/`VideoEncodeResult` gain three small optional
  fields**: `has_music`, `sfx_event_count`, `music_cue_count` — no
  second, competing media-output artifact.
- **New/updated focused tests**: `tests/test_audio_mix_plan.py` (the pure
  planner: every music state-machine transition and rejection, SFX
  resolution, region timing exact in integer ms), `tests/
  test_video_encoder_command_builder.py` (music/SFX filter construction:
  single- and multi-region gain ramps, loop/trim, `adelay` positioning,
  full-mix input/filter ordering, paths with spaces, determinism),
  `tests/test_video_encoder.py` (every new validation/error path with an
  injected fake runner), `tests/test_video_encoder_integration.py` (real
  `ffmpeg`: narration+music, narration+SFX, and a full mix combining
  `DUCK`/`LIFT`, SFX, `CROSSFADE`, and motion-lite), and `tests/
  test_video_renderer.py` (the `audio_bindings` opt-in: absent ignores
  the manifest's own music cues entirely, present engages real mixing,
  present-but-incomplete fails explicitly). Every Phase 28/29 test
  continues to pass unmodified.
- **`scripts/evaluate_audio_mix.py`** — extends `scripts/
  evaluate_video_encoder.py`'s own three-frame project with a
  music-BED/DUCK/LIFT `AssemblyPlan` (plus a real `CROSSFADE` on S2→S3
  and a `SLOW_ZOOM_IN` on S1), a second `SFX_TRIGGER` opportunity added
  to the fixture `VoicePlan`, and three simple, locally-generated
  sine-tone WAV fixtures (never real or copyrighted audio) bound via
  `AudioAssetBindings`, through the real `VideoRenderer.run()` path to
  produce exactly one MP4:
  `data/audio_mix_evaluation/motily_phase30_preview.mp4`. Prints path,
  file size, codecs, fps, canvas, `ffprobe`-measured duration alongside
  the asset's and timeline's own authoritative duration, music cue count,
  SFX event count, the bindings used, and the local `ffmpeg` version. No
  live AI or TTS API of any kind.

**Phase 31 — Deterministic Subtitle/Caption Execution** turns existing
authored script/voice/timeline alignment into deterministic subtitle
artifacts, with local FFmpeg caption burn-in where the environment
supports it. Captions are never transcribed from audio — no Whisper, no
STT, no LLM anywhere in this phase.

- **Source-text identity chain, fully reused, nothing new upstream**:
  `TimelineSegment.narration[i].chunk_id` → `VoicePlan.chunks[chunk_id]
  .line_ids` → the exact `ScriptLine.text` for each of those ids. A
  chunk spanning multiple lines gets its lines' texts joined with a
  single space, in `line_ids`' own order — never re-punctuated,
  re-capitalized, or normalized. For the common one-line-per-chunk case
  this join is the identity function: the cue's text is byte-for-byte
  that one `ScriptLine.text`.
- **`CaptionCue`/`CaptionManifest`** (`app/models/caption.py`): exactly
  one `CaptionCue` per `TimelineAudioRef` (never merged across chunks,
  never split by words) — timing is copied from the SAME cumulative-
  duration arithmetic `TimelineBuilder` already used to compute each
  segment's own `start_ms`/`end_ms`, so a `CaptionManifest` can never
  disagree with the timeline it was built from. `CaptionManifest`
  validates cue chronology/non-overlap, cue-id uniqueness, and that
  every cue stays inside `total_duration_ms`.
- **`CaptionBuilder`** (`app/renderers/caption/`) reuses the EXACT same
  full freshness chain `VideoRenderer` already checks (script/voice/
  visual/assembly/timeline/render-manifest ids) — deliberately not a
  lighter caption-only exception, per this phase's own "prefer full
  timeline freshness" preference — then persists a `CaptionManifest`
  with the standard `ModuleRun` RUNNING→SUCCESS/FAILED lifecycle.
- **Deterministic UTF-8 SRT export** (`app/captions/srt.py`): a pure
  function of a `CaptionManifest`, exact-string-tested for millisecond
  formatting, timestamps past 1/10 hours, Vietnamese Unicode, punctuation,
  multiline text, and a defined final-newline policy (no superfluous
  trailing blank line). `SubtitleRenderer` (`app/renderers/subtitle/`)
  always exports this SRT and persists a `SubtitleFileAsset` — SRT is
  the only exported format in this phase.
- **Optional local caption burn-in** (`app/captions/burn_in.py`,
  opt-in via `SubtitleRendererInput.burn_in_settings`): `ffmpeg`'s
  `subtitles` filter (libass) renders an already-exported SRT into an
  already-encoded MP4 — video is always re-encoded (the filter modifies
  pixels), audio is always stream-copied (`-c:a copy`), guaranteeing
  narration/music/SFX are byte-for-byte untouched. Produces a new,
  separate `CaptionedVideoAsset` (never an in-place rewrite of
  `EncodedVideoAsset`). Checked once via `ffmpeg -filters` before
  building the real command (`SubtitleFilterUnavailableError` if
  missing) — never a silent fallback, never a downloaded font. Confirmed
  empirically on this development environment: Vietnamese diacritics
  render correctly via the system's own Arial/fontconfig resolution, no
  packaged font needed.
- **FFmpeg filtergraph path escaping**, confirmed empirically against
  real `ffmpeg`: spaces, parentheses, Windows drive letters, backslashes,
  and Vietnamese Unicode directory/file names all work correctly inside
  the `subtitles` filter's own `filename` argument (backslashes → forward
  slashes, drive-letter colon escaped as `\:`, whole path single-quoted).
  A literal single quote in the SUBTITLE PATH is rejected explicitly
  (`InvalidSubtitlePathError`) — confirmed this local ffmpeg/libass build
  cannot reliably preserve one inside that specific filter argument. This
  is a path-only restriction; the SRT's own text content may contain
  apostrophes freely.
- **Burn-in is opt-in end to end**: `VideoRendererInput`'s own
  Phase 30 `audio_bindings` opt-in is unaffected, and a normal
  `SubtitleRendererInput` with `burn_in_settings=None` never loads an
  `EncodedVideoAsset` and never invokes `ffmpeg` at all — SRT-only export
  has zero coupling to video encoding.
- **New focused tests**: `tests/test_caption_models.py`,
  `tests/test_caption_builder.py` (pure text/timing derivation +
  freshness/`ModuleRun`), `tests/test_srt_exporter.py` (exact-string SRT
  output), `tests/test_caption_burn_in.py` (command construction +
  escaping + fake-runner validation), `tests/
  test_caption_burn_in_integration.py` (real `ffmpeg` burn-in, skips
  cleanly without the `subtitles` filter), and `tests/
  test_subtitle_renderer.py` (SRT-always/burn-in-opt-in orchestration).
  Every Phase 27-30 test continues to pass unmodified — captions
  disabled means every existing renderer call behaves exactly as before.
- **`scripts/evaluate_captions.py`** — extends `scripts/
  evaluate_audio_mix.py`'s own project (music `BED`/`DUCK`/`LIFT`, a real
  `CROSSFADE`, a `SLOW_ZOOM_IN`, two `SFX_TRIGGER` events) with the real
  `CaptionBuilder` and `SubtitleRenderer`, producing
  `data/caption_evaluation/motily_phase31_captions.srt` always and
  `data/caption_evaluation/motily_phase31_preview.mp4` when local burn-in
  is available. Prints cue text/timing, `ffprobe`-measured video metadata,
  and confirms the source video's own crossfade/motion/music/SFX
  properties are unchanged.

**Phase 32 — Automated Media QC / Production Quality Gate** adds a
deterministic final layer that inspects an already-produced video (and
its optional subtitle sidecar) and decides whether it is technically
safe to hand to a human for approval. QC never regenerates, fixes,
masters, or otherwise alters media — it only detects and reports
technical problems.

- **`MediaQCReport`/`QCCheckResult`** (`app/media_qc/models.py`):
  `overall_status` (`PASS`/`WARN`/`FAIL`) is never independently
  settable — its own validator recomputes the dominance rule (FAIL
  dominates WARN dominates PASS) from `checks` and rejects any mismatch;
  `MediaQCReport.build()` derives it automatically. `ready_for_human_review`
  is a plain derived property (`True` for PASS/WARN, `False` for FAIL) —
  deliberately not a pydantic `computed_field`, since that would break
  the artifact round-trip under this model's own `extra="forbid"` policy
  (confirmed empirically). This is a gate for human review only, never
  an auto-publish signal.
- **`MediaProbe` abstraction** (`app/media_qc/probes.py`): `FFprobeClient`
  (structured container/stream metadata, including exact rational-FPS
  conversion — `30000/1001` via true division, never a string compare),
  `FrameSampler` (deterministic evenly-spaced-percentage frame extraction
  into a `tempfile.TemporaryDirectory`, always cleaned up), and
  `AudioAnalyzer` (`ffmpeg`'s own `volumedetect` filter, parsed
  deterministically) — all argument-list subprocess calls, never
  `shell=True`. Every one of them raises `MediaProbeError` for a broken
  SPECIFIC file (never a crash); only a missing `ffmpeg`/`ffprobe`
  executable itself is an infrastructure error allowed to propagate.
- **QC is diagnostic, never fail-fast** (`app/media_qc/inspector.py`):
  `MediaQCInspector.inspect()` always returns a complete `MediaQCReport`
  — a missing file, a corrupt container, a codec/duration/canvas
  mismatch, silence, or a stale subtitle are all FAIL/WARN
  `QCCheckResult`s, never exceptions. Checks that depend on a successful
  probe are skipped (not re-reported) once the container itself could
  not be read; visual sampling and audio analysis are each skipped
  independently when their own required stream is absent.
- **Required video checks**: file exists/non-zero/decodable, video/audio
  stream presence, codec/pixel-format/fps/canvas/duration match against
  the persisted `EncodedVideoAsset`/`CaptionedVideoAsset` (never
  hardcoded fixture dimensions), plus a coarse stream-duration sanity
  check. Duration policy: outside a small tolerance (100ms default) is
  FAIL outright, no WARN band. Pixel-format mismatch is FAIL (device
  playback compatibility is part of the production contract, per this
  phase's own preferred policy).
- **Frozen/black-frame detection, explicitly tuned for this channel's
  own static/limited-motion style**: 5 frames sampled at deterministic
  10/30/50/70/90% positions (Pillow mean-luminance and consecutive-frame
  mean-difference — no OCR, no CV models). ALL sampled frames black is
  FAIL; a single intentionally-dark frame never fails the whole video.
  ALL sampled frames effectively identical is WARN, never FAIL — "a
  valid static explainer may intentionally hold one image for a long
  time" is honored exactly, confirmed by a real single-color-video
  integration test.
- **Audio checks**: `ffmpeg volumedetect`'s own `mean_volume` below
  -60dBFS (default) across the whole program is FAIL (effectively
  silent); `max_volume` at/above 0dBFS is FAIL (technically impossible
  clipping) — both are native `ffmpeg` measurements, no custom DSP.
- **Subtitle checks** (only run when a `SubtitleFileAsset`/
  `CaptionManifest` pair exists for the project — captions are entirely
  optional): file exists, valid UTF-8, cue count/timestamps/timeline
  bounds/non-empty text all match the `CaptionManifest`, and — the
  strictest check — the on-disk SRT is byte-for-byte identical to what
  `render_srt(caption_manifest)` would produce right now
  (`QC_SUBTITLE_MATCH`), catching a stale/wrong subtitle artifact
  directly. A new `app/captions/srt.py` `parse_srt()` (the exact inverse
  of Phase 31's own `render_srt`) was added for this — QC's only
  addition to an existing package.
- **`MediaQCRenderer`** (`app/renderers/media_qc/`) prefers a fresh
  `CaptionedVideoAsset` when one exists over the plain
  `EncodedVideoAsset` (the more final deliverable a human would actually
  review), reuses the exact same full script/voice/visual/assembly/
  timeline freshness chain every prior renderer already verifies, and
  additionally rejects a STALE `CaptionManifest`/`SubtitleFileAsset`/
  `CaptionedVideoAsset` — but a MISSING one is not an error, since
  captions are optional per project. `ModuleRun` is `SUCCESS` whenever
  the inspection itself completed, REGARDLESS of whether the resulting
  report says PASS/WARN/FAIL — `FAILED` is reserved for the inspector
  genuinely being unable to run at all (e.g. no `ffprobe` executable).
- **New focused tests**: `tests/test_media_qc_models.py`,
  `tests/test_media_qc_probes.py` (fake-runner ffprobe/volumedetect
  parsing, rational FPS), `tests/test_media_qc_rules.py` (every check as
  a pure function), `tests/test_media_qc_inspector.py` (orchestration
  with fake collaborators), `tests/test_media_qc_integration.py` (real
  `ffmpeg`-built healthy/silent/black/frozen/corrupt fixtures), and
  `tests/test_media_qc_renderer.py` (freshness + `ModuleRun` semantics).
  Every Phase 28-31 test continues to pass unmodified.
- **`scripts/evaluate_media_qc.py`** — extends `scripts/
  evaluate_captions.py`'s own finished production chain (music, real
  `CROSSFADE`, motion, SFX, burned captions) with the real
  `MediaQCRenderer`, producing exactly one JSON report:
  `data/media_qc_evaluation/motily_phase32_qc_report.json`. Prints a
  `check_id`/`status`/`measured`/`expected` table plus `overall_status`,
  `ready_for_human_review`, and summary counts.

**Phase 33 — Deterministic Production Orchestrator & Approval Gates**
adds the first real coordinator that walks the existing renderer/builder
modules through an explicit dependency graph, reusing fresh artifacts
and stopping at human approval gates — without rewriting a single
existing engine, without any LLM/agent decision-making of its own, and
without a background task queue.

- **`app/orchestration/graph.py`** — `ProductionGraph`/
  `ProductionNodeDefinition`: a static, code-defined DAG over 19 node
  ids (never inferred from artifact fields at runtime). Validated once
  at construction (duplicate/unknown/self dependency, cycle) and
  topologically sorted with Kahn's algorithm using a `sorted()` tie-break
  at every step, so two independent nodes always order the same way
  across repeated calls, process restarts, and Python versions.
  `ancestors_closure(target_node)` returns exactly the target plus every
  transitive dependency, in that stable order — never a descendant, so
  `run_until(target)` can never execute past what was asked for.
- **`app/orchestration/models.py`** — `ProductionNodeStatus`
  (`PENDING`/`BLOCKED`/`READY`/`RUNNING`/`SUCCEEDED`/`FAILED`/
  `WAITING_APPROVAL`/`SKIPPED`) is deliberately not derived from
  `ModuleRunStatus` alone: a node is `SUCCEEDED` whether its module ran
  this pass or its artifact was simply reused, and a node whose module
  ran successfully can still leave the run `WAITING_APPROVAL`.
  `NodeRunRecord`/`ProductionRun` are the persisted trace (which nodes
  ran/reused/blocked, which artifact/`ModuleRun` each produced, and a
  stable `BlockedReason` code — `BLOCKED_DEPENDENCY`/`WAITING_GATE`/
  `GATE_REJECTED`/`QC_NOT_READY`/`NODE_FAILED`/`NODE_NOT_WIRED` — never
  only a free-text message). `ApprovalGateType`/`ApprovalDecision` bind
  one human `APPROVED`/`REJECTED` decision to an *exact* subject artifact
  id — a decision for a since-replaced artifact simply never matches the
  new one, so approval never silently carries forward.
- **`app/orchestration/registry.py`** — the `NodeAdapter` protocol
  (`load_current`/`is_fresh`/`execute`/`gate_ok`) is the entire boundary
  between the orchestrator and every existing module: the runner never
  knows how a renderer resolves inputs, builds an FFmpeg filter graph, or
  calls a provider. `UnwiredNodeAdapter` covers the twelve LLM-based
  creative-planning nodes (IDEA through PACKAGING_P1) this phase does
  not execute — its `execute()` always raises `NodeNotWiredError`,
  documented as an explicit boundary rather than a brittle fake adapter.
- **`app/orchestration/adapters.py`** — seven real adapters
  (`VoiceRenderAdapter`, `VisualRenderAdapter`, `TimelineAdapter`,
  `VideoRenderAdapter`, `CaptionBuildAdapter`, `SubtitleExportAdapter`,
  `MediaQCAdapter`) wrap the existing Phase 24-32 renderers/builders
  unchanged. Freshness is never centralized: each adapter's own
  `is_fresh` reads the artifact's own already-declared foreign-key id
  fields (e.g. `TimelineManifest.assembly_plan_id`) and compares them
  against the current upstream artifact — the same information each
  renderer's own private freshness check already uses, never a rebuilt
  rule engine. `build_default_graph()`/`build_default_adapters()`
  assemble the full 19-node graph with `gate_after=FINAL_MEDIA_APPROVAL`
  on `MEDIA_QC` only. `SUBTITLE_EXPORT` deliberately covers both SRT
  export and optional caption burn-in as one node, mirroring
  `SubtitleRenderer.run()`'s own single-call API.
- **`app/orchestration/runner.py`** — `ProductionRunner.run_until(
  project_id, target_node)` walks the target's ancestor closure in
  topological order: an unmet dependency resolves to `BLOCKED`
  (`BLOCKED_DEPENDENCY`) rather than being skipped, so a fully-populated
  trace is always persisted even when the run stops partway. A gated
  node whose own `gate_ok()` fails (e.g. QC `FAIL`) is `BLOCKED`
  (`QC_NOT_READY`) *before* any approval is ever requested; one whose
  gate is open but undecided is `WAITING_APPROVAL`; one whose latest
  decision is `REJECTED` is `BLOCKED` (`GATE_REJECTED`) without ever
  deleting or regenerating the artifact. The run's own terminal status
  and stop reason are always taken from the *first* non-`SUCCEEDED` node
  in topological order, so a cascading `BLOCKED_DEPENDENCY` downstream of
  a real failure or an open gate never masks the actual cause. Calling
  `run_until()` again (or `resume(run_id)`) is fully idempotent: every
  node independently re-derives its own status from scratch, so fresh
  artifacts are reused with zero re-execution and a resolved gate is
  picked up with no special-cased retry path.
- **`app/orchestration/gates.py`** — `approve_gate`/`reject_gate`/
  `gate_decision_for` are the only human-approval write path (no UI
  simulation); a decision against an unregistered gate type raises
  `UnknownApprovalGateError`. This is a *new*, parallel mechanism
  alongside the existing `app/review/service.py` gates — it does not
  replace them. Only `FINAL_MEDIA_APPROVAL` is actually enforced this
  phase: `IDEA_APPROVAL`/`RESEARCH_APPROVAL`/`SCRIPT_APPROVAL` are
  documented in `ApprovalGateType` for a complete vocabulary, but
  `project.state == MVP_COMPLETE` (already required by every real
  adapter) is already conclusive proof the existing review-service chain
  leading to it was satisfied.
- **Storage** (`app/storage/orm.py`) adds two new tables in the *same*
  database — `production_runs` and `approval_decisions` — since a
  project accumulates many `ProductionRun`/`ApprovalDecision` rows over
  time, unlike the one-row-per-artifact-type convention. `ProductionRun`
  is a cross-module workflow record; it never replaces `ModuleRun`
  (one module's own single execution), and a `NodeRunRecord` references
  a `module_run_id` where the underlying adapter produced one.
- **No orchestration-layer AI**: `tests/test_orchestration_purity.py`
  asserts (via a fresh subprocess, and a static per-file source scan)
  that no file under `app/orchestration/` — adapters included, since
  every wired adapter delegates to an already-LLM-free renderer — ever
  imports `app.llm` or a provider package.
- **New focused tests**: `tests/test_orchestration_models.py`,
  `tests/test_orchestration_graph.py` (validation, deterministic
  topological order, ancestor closure), `tests/test_orchestration_gates.py`
  (approval persistence, exact-artifact scoping, duplicate-decision
  policy), `tests/test_orchestration_runner.py` (fake-adapter happy
  path/idempotency/stop-resume/rejection/stale-invalidation/execution-
  failure/QC-gate semantics), `tests/test_orchestration_purity.py`, and
  `tests/test_orchestration_integration.py` (the real wired 7-node slice
  — `FakeTTSProvider`/`FakeVisualProvider` stand in for network
  providers, everything else is the real local `ffmpeg`/`ffprobe`
  pipeline — reaching `WAITING_APPROVAL` at `FINAL_MEDIA_APPROVAL`, then
  `SUCCEEDED` after `approve_gate` + `resume`). Every Phase 24-32
  renderer/builder test continues to pass unmodified.
- **`scripts/evaluate_production_orchestrator.py`** — builds a
  disposable local MVP_COMPLETE project, runs the real wired slice via
  `ProductionRunner` to `MEDIA_QC` (stopping `WAITING_APPROVAL`), then
  approves the exact final-media artifact and resumes to `SUCCEEDED`,
  printing a full per-node trace for both passes plus a summary (run id,
  first/final status, executed vs. reused node counts, QC status, final
  media artifact id, approval subject id).

**Phase 34 — Full Upstream Pipeline Wiring & Approval Gate Unification**
wires the remaining twelve upstream creative/planning nodes (IDEA
through PACKAGING_P1) into `ProductionRunner`, so a single run can now
drive the *entire* implemented chain — IDEA through `FINAL_MEDIA_APPROVAL`
— while keeping `app/orchestration/` exactly as provider-unaware as
Phase 33 left it.

- **`app/production_adapters/`** (new package, deliberately *outside*
  `app/orchestration/`) — `upstream.py` holds the twelve real adapters;
  every one of their underlying engines imports `app.llm` (confirmed by
  reading each `engine.py` directly, never assumed from phase names),
  which is exactly why this bridge exists as a separate package rather
  than living in the core. `registry.py` combines these twelve with the
  seven Phase 33 downstream adapters (re-exported from
  `app/orchestration/adapters.py` unchanged) into one complete 19-node
  graph with the real gate placement. `app/orchestration/adapters.py`'s
  own `build_default_graph()`/`build_default_adapters()` are untouched —
  Phase 33's own evaluation script and tests still work exactly as
  before.
- **Freshness for artifacts with no foreign key at all**:
  `FeasibilityReport`/`ResearchPackage`/`NarrativePlan`/`ScriptPlan`/
  `ScriptVerificationReport` declare no upstream-reference field
  (confirmed by reading each model directly) — freshness for these five
  compares the latest **SUCCESS** `ModuleRun`'s own recorded `input_ids`
  against the current upstream artifact ids, mirroring
  `app/review/service.py`'s own `_verify_packaging_is_fresh`/
  `_verify_script_verification_is_fresh` exactly. The other seven
  (`IdeaCandidate` has no upstream; `ResearchR0.idea_id`;
  `VoicePlan`/`VisualPlan`/`AssemblyPlan`/`FinalPackagingPlan`'s own real
  foreign keys) compare those fields directly, exactly like Phase 33's
  downstream adapters.
- **Business outcomes are BLOCKED, never silently successful**: three
  new `BlockedReason` codes — `FEASIBILITY_NOT_PASSED`,
  `SCRIPT_VERIFICATION_NOT_PASSED`, `PACKAGING_RISK_TOO_HIGH` — mirror
  `MediaQCAdapter`'s own `QC_NOT_READY` pattern: a node whose adapter
  reports `gate_ok()=False` is blocked *before* its gate is ever
  offered, and the specific reason is now adapter-configurable
  (`NodeAdapter.gate_not_ready_reason`, an optional attribute the runner
  reads via `getattr(..., BlockedReason.QC_NOT_READY)` — fully backward
  compatible with every Phase 33 adapter that doesn't declare it).
- **Approval-gate unification** (`app/orchestration/gates.py`): five new
  `ApprovalGateType` members — `IDEA_APPROVAL`, `RESEARCH_APPROVAL`,
  `NARRATIVE_APPROVAL`, `PACKAGING_P0_APPROVAL`, `SCRIPT_APPROVAL` — map
  to five *exact* `app/review/service.py` decision points (verified by
  reading that file's own sixteen functions directly, never assumed from
  names: e.g. `RESEARCH_APPROVAL` is `decide_feasibility`'s own gate, the
  real human research-continuation decision — there is no separate
  "approve R0/R1 research" step in the existing system). These five are
  **read-only** through `gate_decision_for`, reconstructed purely from
  `ProjectState` + `HumanApproval` rows (no new decision table, no
  duplicated review logic) — `approve_gate`/`reject_gate` raise
  `LegacyGateNotWritableError` if called with one of them, since the
  real decision must be made through `app/review/service.py`'s own
  functions directly. Only `FINAL_MEDIA_APPROVAL` remains writable
  through the `ApprovalDecision` table, exactly as Phase 33 left it.
  `SCRIPT_APPROVAL` spans two real sequential states with no engine work
  between them (`SCRIPT_VERIFICATION` then `SCRIPT_REVIEW`) collapsed
  into one gate on the `SCRIPT_VERIFY` node — `ScriptVerificationReport`
  has no `id` field at all (a locked contract), so its subject resolves
  to the project's own current `script_plan_id` instead.
- **All 19 nodes now have real, executable adapters** — zero
  `UnwiredNodeAdapter` remain in the full registry, enforced by a
  dedicated test.
- **New focused tests**: `tests/test_production_adapters_upstream.py`
  (per-adapter artifact resolution/engine invocation/freshness-reuse/
  stale-rerun for all twelve, using each engine's own existing
  fake-provider test fixtures), `tests/test_production_adapters_registry.py`
  (full 19-node coverage, zero `UnwiredNodeAdapter`, exact 6-gate
  placement), `tests/test_orchestration_legacy_gates.py` (real
  `app/review/service.py` calls -> `gate_decision_for` reconstruction:
  approved/rejected/pending/wrong-subject/persisted-reload for all five
  legacy gates), `tests/test_orchestration_purity.py` extended (proves
  `app/production_adapters/upstream.py` genuinely imports `app.llm`,
  confirming the isolation is a real boundary, not an accident),
  `tests/test_full_production_integration.py` (the complete no-network
  pipeline, IDEA through `FINAL_MEDIA_APPROVAL`, stopping at all six real
  gates and resuming past each with a real review-service call, ending
  `SUCCEEDED` with full artifact-id-chain verification), and
  `tests/test_phase33_compatibility.py` (a Phase-33-style persisted
  `ProductionRun` with unwired upstream nodes still deserializes, and the
  new registry can resume it once real inputs are supplied). Every
  Phase 24-33 test continues to pass unmodified.
- **`scripts/evaluate_full_production_orchestrator.py`** (new; Phase 33's
  own `evaluate_production_orchestrator.py` is preserved unchanged as
  the historical downstream-only slice) — drives the complete pipeline
  from a bare project through all six real approval gates to
  `SUCCEEDED`, printing a per-run trace and a final summary (registered/
  wired node counts, the real gate sequence encountered, resume count,
  executed/reused node totals, QC status, final media/subtitle paths,
  final approval subject id, `network_calls=0`), and persisting
  `data/full_orchestrator_evaluation/motily_phase34_report.json`.

**Phase 35 — Production CLI / Single-Command Workflow** exposes the
complete Phase 34 orchestrator through one installable command-line
tool, `motily`. The CLI is an execution surface only — every decision
(dependency ordering, freshness, gate placement, approval semantics)
still lives in `ProductionRunner`/`app/review/service.py`; nothing here
re-implements orchestration logic.

- **Entrypoint**: `motily` (`app/cli/main.py`, a Typer application),
  installed via `[project.scripts]` in `pyproject.toml` — `pip install -e .`
  exposes it directly, no `python scripts/...` required. Commands:
  `produce`, `resume`, `status`, `runs`, `approve`, `reject`, `doctor`.
- **`app/bootstrap.py`** — the one composition root. `build_composition(db_path)`
  opens/creates the SQLite database, builds the full 19-node graph and
  adapter registry (Phase 34, unchanged), and wraps them in one
  `ProductionService`. It never constructs a provider itself: this
  codebase has never implemented a concrete, network-calling
  `LLMProvider` (only `app/llm/fake.py` exists) — a real `motily produce`
  run correctly reaches `FAILED`/`AdapterConfigurationError` at `IDEA`
  rather than silently doing nothing. Tests/the evaluation script inject
  `FakeLLMProvider`/`FakeTTSProvider`/`FakeVisualProvider` explicitly
  through `ExecutionContext`.
- **`app/services/production_service.py`** — the `ProductionService`
  façade every CLI command calls (`start`/`start_at_node`/`resume`/
  `status`/`list_runs`/`approve_pending`/`reject_pending`). Also the home
  of the two explicit alias tables: `--target` (`idea`/`research`/
  `script`/`plans`/`video`/`qc`/`final`, default `final`, mapping
  deterministically to `IDEA`/`FEASIBILITY`/`SCRIPT_VERIFY`/
  `ASSEMBLY_PLAN`/`VIDEO_RENDER`/`MEDIA_QC`/`MEDIA_QC`) and `--gate`
  (`idea`/`research`/`narrative`/`packaging`/`script`/`final`, mapping to
  the six `ApprovalGateType` values). No fuzzy matching — an unrecognized
  alias is always a hard `UnknownTargetAliasError`/`UnknownGateAliasError`.
- **`app/services/production_review.py`** — the narrow dispatcher that
  routes each of the five legacy gates to its *exact* canonical
  `app/review/service.py` mutation (never the generic Phase 33
  `ApprovalDecision` writer, which Phase 34 already made refuse them).
  `SCRIPT_APPROVAL` spans two real states with no engine work between
  them; each `approve`/`reject` call performs exactly ONE real mutation
  (one auditable `HumanApproval` row) — clearing it fully needs two
  approve+resume cycles, never silently chained into one command.
  `RESEARCH_APPROVAL`/`IDEA_APPROVAL`/`NARRATIVE_APPROVAL`/
  `PACKAGING_P0_APPROVAL` have no reject path at all (mirroring
  `app/review/service.py`'s own real capability, confirmed in Phase 34) —
  `reject` raises a clear `GateRejectionNotSupportedError` for these.
- **Exit codes** (a stable scripting contract, `app/cli/exit_codes.py`):
  `0` success/read-only-command-completed, `2` `WAITING_APPROVAL`, `3`
  `BLOCKED`, `4` `FAILED`, `5` invalid CLI input, `6` infrastructure
  unavailable (`doctor` only — a node's own `FAILED` status is always
  exit `4`, even if its root cause happens to be infrastructure).
- **`approve` never auto-resumes** (`resume` is a separate, explicit
  command) — mutations stay auditable one at a time. `produce` always
  creates a new `ProductionRun`; running it twice makes two runs (no
  magic deduplication) — `resume`/`status`/`runs` are the read/continue
  side.
- **JSON output** (`--json` on every command) uses a stable DTO
  (`app/cli/dto.py`), decoupled from `ProductionRun`'s own persisted
  shape, so a future orchestration-model change can't silently break a
  script parsing CLI output. Never includes provider config or secrets.
- **`motily doctor`** — local-only diagnostics (database directory
  writable, `ffmpeg`/`ffprobe` on PATH, `GEMINI_API_KEY` presence) —
  never a network call, never credential verification.
- **New focused tests**: `tests/test_cli_dto_and_aliases.py` (every
  target/gate alias, exit-code mapping, DTO key stability, no secrets in
  JSON), `tests/test_production_review_dispatch.py` (each of the five
  legacy gates dispatches to its exact canonical function; two-call
  `SCRIPT_APPROVAL` semantics; unsupported-reject paths raise clearly),
  `tests/test_cli_commands.py` (Typer `CliRunner`-driven: produce/
  approve-does-not-resume/approve-then-resume/reject/status-is-read-
  only/runs-ordering-and-limit, via two explicit DI seams —
  `app.cli.main._composition_override`/`_context_factory_override` —
  never dozens of monkeypatched globals), `tests/test_bootstrap_composition.py`,
  and `tests/test_full_cli_integration.py` (the complete no-network
  pipeline driven ONLY through `motily produce`/`approve`/`resume`,
  IDEA through `FINAL_MEDIA_APPROVAL`, ending `SUCCEEDED`, then proving
  a further `resume` reuses everything with zero re-execution). Every
  Phase 24-34 test continues to pass unmodified.
- **`scripts/evaluate_production_cli.py`** — drives the real Typer app
  through its own `CliRunner` (the same dispatch a real `motily`
  invocation uses), printing a `$ motily ...` transcript for every
  command and persisting
  `data/production_cli_evaluation/motily_phase35_report.json`.

**Operator quick reference:**

```bash
# Start a new production (creates a brand-new project from --brief, or
# continue an existing one with --project-id):
motily produce --brief "A bridge that tore itself apart in the wind" --target final

# Inspect what happened / what's pending, without executing anything:
motily status <run-id> --verbose

# Approve the gate currently pending on that run (writes only -- does
# not continue the pipeline by itself):
motily approve <run-id> --gate idea

# Continue the run past the gate you just approved:
motily resume <run-id>

# List recent runs for a project:
motily runs --project-id <project-id> --limit 10
```

See [docs/TECHNICAL_SPEC_v0.1.md](docs/TECHNICAL_SPEC_v0.1.md) for the full
architecture boundary and [docs/CHANGELOG.md](docs/CHANGELOG.md) for the
detailed history.

## Requirements

- Python 3.11+

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate   # Windows
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest
```

The default run makes zero live network calls and requires no secret —
`GeminiTTSProvider` and `GeminiImageProvider` are only ever exercised
through an injected fake client in tests.

## Using the real Gemini / Cloudflare providers (optional)

Only needed to actually call a real provider (the live smoke tests, or
the manual evaluation scripts) — never required for `pytest`:

```bash
export GEMINI_API_KEY=...              # bash
set GEMINI_API_KEY=...                 # Windows cmd
$env:GEMINI_API_KEY = "..."            # PowerShell

export CLOUDFLARE_ACCOUNT_ID=...       # bash
export CLOUDFLARE_API_TOKEN=...        # bash
set CLOUDFLARE_ACCOUNT_ID=...          # Windows cmd
set CLOUDFLARE_API_TOKEN=...           # Windows cmd
```

```bash
# Opt-in live smoke tests (each makes one real API call):
RUN_LIVE_GEMINI_TTS=1 pytest tests/test_gemini_tts_live_smoke.py -m live_tts -q
RUN_LIVE_GEMINI_IMAGE=1 pytest tests/test_gemini_image_live_smoke.py -m live_image -q
RUN_LIVE_CLOUDFLARE_IMAGE=1 pytest tests/test_cloudflare_image_live_smoke.py -m live_cloudflare_image -q

# Manual voice listening (writes one WAV per voice, does not rank them):
python scripts/evaluate_gemini_voices.py

# Manual visual evaluation -- Gemini (writes 3 PNGs; currently blocked by
# this account's zero image-generation quota, see docs/CHANGELOG.md Phase 20.1):
python scripts/evaluate_gemini_image.py

# Manual visual evaluation -- Cloudflare Workers AI / FLUX.1 Schnell
# (writes 3 JPGs, same 3 briefs as the Gemini script, does not rank them):
python scripts/evaluate_cloudflare_image.py
```

## Architecture Boundary

`app/config/` loads and validates `GlobalConfig` from
`app/config/global.yaml`. `app/models/` defines the typed domain contracts
(`Project`, `IdeaCandidate`, `ResearchR0`, `ResearchPackage`,
`FeasibilityReport`, `NarrativePlan`, `PackagingPrototype`, `ScriptPlan`,
`ScriptVerificationReport`, `VoicePlan`, `VisualPlan`, `AssemblyPlan`,
`FinalPackagingPlan`, `VoiceRenderManifest`, `VisualRenderManifest`,
`HumanApproval`, `ModuleRun`, and their nested types). `app/workflow/`
validates `ProjectState` transitions (topology only). `app/storage/`
persists all of the above to a local SQLite database (metadata only —
never raw audio bytes). `app/llm/` is the provider-independent LLM
interface and structured-output layer. `app/audio/` is the
provider-independent TTS interface (`TTSProvider`, `FakeTTSProvider`) and
filesystem audio store (`AudioFileStore`) — deliberately zero dependency
on `app/llm/`. `app/audio/providers/` holds concrete TTS provider
adapters — `gemini.py` (`GeminiTTSProvider`), the first real one; SDK
imports (`google.genai`, `httpx`) are confined to this one file.
`app/visual/` is the provider-independent visual-render interface
(`VisualProvider`, `FakeVisualProvider`) and filesystem visual-asset
store (`VisualFileStore`), mirroring `app/audio/` — deliberately zero
dependency on `app/llm/`. `app/visual/providers/` holds concrete visual
provider adapters — `gemini.py` (`GeminiImageProvider`, `GENERATED_STILL`/
PNG only, Gemini's `generate_content` image modality; architecturally
correct, currently blocked by this account's zero image-generation
quota) and `cloudflare.py` (`CloudflareImageProvider`, `GENERATED_STILL`/
JPG only, Cloudflare Workers AI's FLUX.1 Schnell REST API — added as a
free-tier-friendly alternative); SDK/HTTP imports (`google.genai`,
`httpx`) are confined to their respective one file each. `app/visual/router.py`
(`MediaTypeVisualProvider`) is a tiny composite, satisfying
`VisualProvider` itself, that dispatches to a different concrete
provider per `VisualMediaType` — the production-routing mechanism that
lets `GENERATED_STILL` reach whichever concrete provider it's configured
with while `DIAGRAM` stays unmapped (raising
`VisualProviderUnavailableError`), entirely without `VisualRenderer`
knowing which vendor (or router) it was handed.
`app/research/` is the provider-independent research retrieval interface.
`app/review/` is the human-approval application layer, covering idea,
Feasibility, narrative, packaging, script-verification-resolution, and
final Script Review decision points (Voice, Visual, Timing/Assembly, and
Packaging P1 Planning have no review gate — all four are artifact-driven,
not workflow-driven). `app/engines/` holds content engines — `idea/`
(Idea Engine), `research_r0/` (R0 Research Engine), `feasibility/`
(Feasibility Engine), `research_r1/` (R1 Research Engine), `narrative/`
(Narrative Engine), `packaging_p0/` (Packaging P0 Engine), `script/`
(Script Engine), `script_verification/` (Script Verification Engine),
`voice_plan/` (Voice Planning Engine), `visual_plan/` (Visual Planning
Engine), `assembly_plan/` (Timing / Assembly Planning Engine), and
`packaging_p1/` (Packaging P1 / Final Packaging Engine) — the last four
are the production layer, all running only at `MVP_COMPLETE`.
`app/renderers/` is a top-level category, separate from
`app/engines/`, for execution/rendering components that turn an
already-locked plan into real media rather than reasoning about content —
`voice/` (Voice Renderer, Phase 17) and `visual/` (Visual Renderer,
Phase 19) are the only two so far. Neither has a prompt or a dependency
on `app/llm/`, direct or transitive.
`app/ti_assets/` (Phase 21) is a separate top-level package, sibling to
`app/audio/`/`app/visual/`, for the canonical Tí brand-asset system —
deterministic storage (`TiAssetFileStore`), lookup, and retrieval
(`TiAssetRetriever` protocol, `SqliteTiAssetRetriever`) for a small,
human-curated set of Tí image files, one per `TiState`. It has zero
dependency on `app/visual/`, `app/llm/`, or any provider, and is not yet
called from `app/renderers/visual/renderer.py` or anywhere else — Phase 21
defines the contracts, storage, and retrieval boundary only, deliberately
stopping short of wiring it into production rendering.
`tests/integration/` (Phase 12) proves the core MVP pipeline composes end
to end through the real public engine/review operations, with no separate
orchestrator module anywhere in the repository.

`FakeTTSProvider` and `GeminiTTSProvider` are the only two `TTSProvider`
implementations — no second real TTS vendor (ElevenLabs, OpenAI TTS,
Azure TTS, Edge TTS, local neural TTS) or voice cloning is implemented.
`FakeVisualProvider`, `GeminiImageProvider`, and `CloudflareImageProvider`
are the only three `VisualProvider` implementations — `GeminiImageProvider`
covers `GENERATED_STILL`/PNG only (architecturally correct, currently
blocked by this account's zero image-generation quota) and
`CloudflareImageProvider` covers `GENERATED_STILL`/JPG only via FLUX.1
Schnell (added as a free-tier-friendly alternative, not a replacement);
no third real image provider (Imagen as a distinct integration, OpenAI
image, Midjourney, Stable Diffusion, ComfyUI), no real AI video
generation, no real footage download, no web image search, and no stock
media integration is implemented. No dedicated diagram renderer, no
animation rendering, CapCut automation, XML/EDL export, thumbnail
rendering, YouTube publishing, autonomous orchestrator, real LLM
provider, real search provider, or CLI/web behavior are implemented yet.
The canonical Tí voice is `Puck`, a human listening decision recorded as
`CANONICAL_TI_VOICE_ID` in `app/audio/config.py` (see
`scripts/evaluate_gemini_voices.py`); no canonical visual style has been
chosen, and **canonical Tí visual consistency across independently
generated stills is not solved by either image provider** — both
include a per-state mood/expression note in their prompt, but there is
no reference-image conditioning or character sheet yet (see
`scripts/evaluate_gemini_image.py`/`scripts/evaluate_cloudflare_image.py`).
`DIAGRAM` remains architecturally provider-renderable but has no real
production provider configured — routing it through
`MediaTypeVisualProvider` raises `VisualProviderUnavailableError` rather
than silently calling either image provider.
