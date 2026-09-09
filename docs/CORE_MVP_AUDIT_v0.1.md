# Core MVP Architecture Audit v0.1

Phase 12. This document is the read-only architecture audit plus the record
of what the Phase 12 integration suite proved, found, and fixed. It is not a
redesign proposal — per the Phase 12 patch, code changes this phase are
limited to integration tests, fixtures, audit documentation, and the two
tiny defect fixes documented in section 9, each following the
write-failing-test-first protocol.

## 1. Executive Result

**PASS, with two real defects found and fixed.**

The full core MVP pipeline — `NEW_PROJECT` through `MVP_COMPLETE` — was
proven to compose correctly using only the already-implemented public
engine and review operations, called directly and sequentially (no new
orchestrator). Every human-approval gate is preserved by the normal public
flow; every terminal state is genuinely terminal; every source-of-truth
ownership rule holds; no engine invokes another engine; no vendor SDK or
agent framework exists anywhere in `app/`.

The audit's adversarial stale-artifact tests (section 9 below) found two
real gate-bypass defects — a rewritten script could be waved through final
approval on a stale PASS verification report from the *previous* script
version, and a revised narrative's packaging gate could be bypassed with a
stale `PackagingPrototype` generated for the *previous* narrative. Both are
now fixed, with regression tests, and documented in full below. The known,
pre-existing per-repository-call transaction gap (section 10) was
deliberately **not** touched, per the patch's explicit instruction.

## 2. Implemented Pipeline

```
NEW_PROJECT → IDEA_DISCOVERY → IDEA_REVIEW → R0_RESEARCH → FEASIBILITY →
R1_RESEARCH → NARRATIVE → NARRATIVE_REVIEW → PACKAGING_P0 → SCRIPT →
SCRIPT_VERIFICATION → SCRIPT_REVIEW → MVP_COMPLETE
```

Eight content engines (`app/engines/{idea,research_r0,feasibility,
research_r1,narrative,packaging_p0,script,script_verification}/`), each a
plain constructor-injected class with one `run()` method; no `BaseEngine`
framework. Seventeen human-review operations in `app/review/service.py`.
`tests/integration/test_full_happy_path_reaches_mvp_complete` drives all 16
steps of the patch's section 3 literally and explicitly — every call is a
real public engine/review operation, in the order a human would actually
issue them, with every intermediate state asserted.

## 3. State Graph Audit

Re-read directly from `app/workflow/states.py` (not from memory) before any
code was written this phase:

| State | Outgoing edges |
|---|---|
| `NEW_PROJECT` | `IDEA_DISCOVERY` |
| `IDEA_DISCOVERY` | `IDEA_REVIEW` |
| `IDEA_REVIEW` | `R0_RESEARCH`, `IDEA_DISCOVERY` |
| `R0_RESEARCH` | `FEASIBILITY` |
| `FEASIBILITY` | `R1_RESEARCH`, `IDEA_DISCOVERY`, `ARCHIVED` |
| `R1_RESEARCH` | `NARRATIVE` |
| `NARRATIVE` | `NARRATIVE_REVIEW`, `R1_RESEARCH` |
| `NARRATIVE_REVIEW` | `PACKAGING_P0`, `NARRATIVE`, `R1_RESEARCH`, `ARCHIVED` |
| `PACKAGING_P0` | `SCRIPT`, `NARRATIVE`, `R1_RESEARCH` |
| `SCRIPT` | `SCRIPT_VERIFICATION`, `NARRATIVE`, `R1_RESEARCH` |
| `SCRIPT_VERIFICATION` | `SCRIPT_REVIEW`, `SCRIPT`, `NARRATIVE`, `R1_RESEARCH` |
| `SCRIPT_REVIEW` | `MVP_COMPLETE`, `SCRIPT`, `NARRATIVE`, `ARCHIVED` |
| `MVP_COMPLETE` | *(none — terminal)* |
| `ARCHIVED` | *(none — terminal)* |

`update_project_state` (`app/storage/projects.py`) always routes through
`validate_transition`, so this graph is enforced for real, not just
documented — confirmed both by re-reading the code and empirically, since
every one of the ~40 state-transition assertions across the Phase 12
integration suite would have raised `InvalidStateTransitionError` had any
edge been wrong.

`test_narrative_review_cannot_jump_directly_to_script` confirms the
Phase 9 migration's removed edge stays removed, as an integration-level
cross-check (the topology validator itself was already unit-tested in
`tests/test_state_machine.py` and was **not** touched this phase, per
section 8's explicit instruction not to redesign it into a business-policy
engine).

## 4. Source-of-Truth Ownership

| Contract | Owns | Confirmed by |
|---|---|---|
| `ResearchPackage` | Factual truth | `docs/TECHNICAL_SPEC_v0.1.md` lines 61-62; every downstream engine (Narrative, Packaging P0, Script, Script Verification) loads it read-only and never writes it |
| `NarrativePlan` | Story architecture | Spec lines 63-64; only `NarrativeEngine` ever calls `save_artifact` for it |
| `PackagingPrototype` | Viewer-promise contract | Established in the Phase 9 spec section's prose, not the top-level list (see Known Technical Debt) |
| `ScriptPlan` | Spoken text | Spec lines 65-66; only `ScriptEngine` ever calls `save_artifact` for it — confirmed `ScriptVerificationEngine` contains exactly one `save_artifact` call, targeting the report only, never the plan |
| `ScriptVerificationReport` | Independent QA assessment | Established in the Phase 11 spec section's prose, not the top-level list (see Known Technical Debt) |
| `GlobalConfig` | Immutable brand constraints | Spec lines 67-69; loaded once via `load_global_config()`, never mutated at runtime anywhere in `app/` |

No violating code path found. **Finding, not a defect:** the top-level
"Source-of-Truth Ownership" section of `docs/TECHNICAL_SPEC_v0.1.md` (written
at Phase 1.1) was never extended with explicit bullets for
`PackagingPrototype` or `ScriptVerificationReport`, even though their
ownership scope is clearly established in their own phase sections' prose.
Documentation completeness gap only — recorded in section 11.

## 5. Engine Boundaries

Every `app/engines/*/engine.py`'s imports were read directly (not assumed).
Cross-package imports are exclusively `from app.engines.<other>.models
import <ARTIFACT_TYPE constant>` — never `from app.engines.<other>.engine
import <EngineClass>`. No engine imports or instantiates another engine.
Per-engine boundary claims, all confirmed by direct source inspection:

- **IdeaEngine**: no dependency on any later-stage engine.
- **R0ResearchEngine**: depends only on `idea.models`; no Narrative/Script coupling.
- **FeasibilityEngine**: writes only `FeasibilityReport`; never touches narrative or script state.
- **R1ResearchEngine**: writes only `ResearchPackage`; never touches narrative or script state.
- **NarrativeEngine**: loads `ResearchPackage` read-only as factual truth; never mutates it.
- **PackagingP0Engine**: produces a *prototype* only — its own module docstring states "not final title/thumbnail production"; no asset-generation code exists anywhere in the repository.
- **ScriptEngine**: its Phase 10 prompt explicitly forbids storyboards and voice/delivery timing plans (`OUTPUT DISCIPLINE` section); no such output type exists in `ScriptPlan`.
- **ScriptVerificationEngine**: exactly one `save_artifact` call in the whole file (confirmed by direct grep), targeting `script_verification_report` only — it never calls `save_artifact` for `ScriptPlan`.

No hidden autonomous chain exists: `app/engines/__init__.py` re-exports only
`EngineStateError`, nothing resembling an orchestrator or pipeline runner.

## 6. Human Approval Gates

`tests/integration/test_mvp_complete_human_approval_gates_are_recorded`
proves the happy path leaves exactly six `HumanApproval` records — one per
actual human decision point (`IDEA_REVIEW`, `FEASIBILITY`,
`NARRATIVE_REVIEW`, `PACKAGING_P0`, `SCRIPT_VERIFICATION`, `SCRIPT_REVIEW`)
— each `APPROVED`, correctly scoped to the project, and correctly resolved
by `get_latest_approval_for_stage`. It also proves no approval is ever
invented for an engine-only stage (`R0_RESEARCH`, `R1_RESEARCH`,
`NARRATIVE`, `SCRIPT` all return `None`).

Bypass audit (`test_full_mvp_flow.py`, section 7 of the patch), using only
the public engine/review surface, never the topology validator directly:

| Bypass attempt | Result |
|---|---|
| Run `R0ResearchEngine` from `IDEA_REVIEW` (skip `approve_idea`) | `EngineStateError` |
| Run `PackagingP0Engine` alone, expect it to reach `SCRIPT` | Stays `PACKAGING_P0` (engine never transitions state — by design, Phase 9) |
| Run `FeasibilityEngine` alone, expect it to reach `R1_RESEARCH` | Stays `FEASIBILITY` (engine never transitions state — by design, Phase 6) |
| Run `ScriptVerificationEngine` alone, expect it to reach `SCRIPT_REVIEW` | Stays `SCRIPT_VERIFICATION` (engine never transitions state — by design, Phase 11) |
| Re-run `ScriptVerificationEngine` from `SCRIPT_REVIEW` | `EngineStateError` |
| Re-run `accept_script_verification` from `SCRIPT_REVIEW` | `ReviewStateError` |

**PASS.**

## 7. Persistence / Artifact Integrity

`tests/integration/test_mvp_complete_artifacts_are_all_present_and_referenced`
confirms, at `MVP_COMPLETE`: all eight canonical artifact types are
retrievable via `get_artifact`, and for the seven that have a `Project`
reference field, `project.<x>_id == artifact.id` exactly.
`ScriptVerificationReport` has no `id` and no reference field by design
(Phase 11); retrieval by `(project_id, artifact_type)` alone is the only,
and sufficient, lookup path.

Canonical artifact-type strings (`app/engines/*/models.py`, grepped
directly): `idea_candidate`, `research_r0`, `feasibility_report`,
`research_r1`, `narrative_plan`, `packaging_prototype`, `script_plan`,
`script_verification_report`. No spelling variants found; every consumer
(engines and `app/review/service.py`) imports the same constant, never a
re-typed string literal.

Project artifact-reference fields (`app/models/project.py`, re-read
directly): `idea_candidate_id, research_r0_id, feasibility_id,
research_r1_id, narrative_plan_id, packaging_prototype_id, script_plan_id`
— exactly seven, matching `app/storage/projects.py`'s
`_ARTIFACT_REFERENCE_FIELDS` set exactly. `ScriptVerificationReport`'s
absence from this list is intentional (documented in Phase 11, re-confirmed
this phase) — not a gap.

## 8. ModuleRun Audit

`tests/integration/test_mvp_complete_module_runs_are_recorded` confirms
exactly eight `SUCCESS` `ModuleRun` records at `MVP_COMPLETE` — one per
engine (`idea_engine, research_r0_engine, feasibility_engine,
research_r1_engine, narrative_engine, packaging_p0_engine, script_engine,
script_verification_engine`) — each with the correct `project_id`,
`completed_at` set, and `module_version == "0.1"`. Every engine except
`script_verification_engine` has a non-`None` `output_id`;
`script_verification_engine`'s is `None`, which is correct and unchanged
from Phase 11 (`ScriptVerificationReport` has no `id` to record) — not
"fixed" here, per the patch's explicit instruction.

Failure-path audit: all eight engines' source was read directly. Every one
follows the identical shape — precondition failure raises before any
`ModuleRun` is created; execution begins with `ModuleRun(RUNNING)` saved
immediately; the entire generate→persist→transition sequence is wrapped in
one `try/except Exception`; any exception is recorded as
`ModuleRun(FAILED, completed_at=..., error_message=...)` and re-raised
unchanged. No engine leaves a `RUNNING` row un-updated after any exception
its own code can catch. (The one exception to this — an uncaught process
kill mid-transaction — is a transaction/partial-progress concern, covered
in section 10, not a code defect.)

## 9. Stale Artifact Safety — two real defects found and fixed

Sections 25-27 of the patch ask whether a stale artifact — one generated
for a *previous* version of something it depends on — can be used through
the public review surface to bypass a gate that should require a fresh
one. It can, in two places. Both are now fixed.

### Defect 1: stale `ScriptVerificationReport` could accept an unverified rewritten script

**Reproduction**
(`tests/integration/test_stale_artifact_safety.py::test_stale_pass_verification_cannot_wave_through_a_rewritten_script`,
written first and confirmed failing against the pre-fix code):

1. Script A is verified `PASS` → report A is stored.
2. `send_script_for_rewrite` → state returns to `SCRIPT`. Report A is
   untouched (no code path clears or marks it).
3. `ScriptEngine` runs again → Script B is generated and stored; state
   advances to `SCRIPT_VERIFICATION`. The *stored* verification report is
   still report A — verification has not run against Script B.
4. Before verifying Script B, `accept_script_verification` was called.

**Before the fix:** it succeeded. `accept_script_verification` and
`approve_final_script` only checked "does *a* `ScriptVerificationReport`
exist, and is its `status == PASS`" — never whether that report was
generated for the `ScriptPlan` currently referenced by the project. Since
`ScriptVerificationReport` has no `id` and no field referencing the script
it audited (locked Phase 1 contract — confirmed, not touched), nothing
prevented Script B from riding through on Script A's stale approval, all
the way to a false `MVP_COMPLETE`.

**Fix** (`app/review/service.py`): a new `_verify_script_verification_is_fresh`
check, added to both `accept_script_verification` and `approve_final_script`
after the existing artifact/report lookups. It finds the most recent
`SUCCESS` `script_verification_engine` `ModuleRun` for the project (via the
already-existing `list_module_runs_for_project` — no repository API
change) and confirms its recorded `input_ids[3]` (the `ScriptPlan.id` that
run actually verified, per `ScriptVerificationEngine.run()`) equals the
project's *current* `script_plan_id`. Mismatch (or no such run at all)
raises the new `StaleScriptVerificationError`. No locked contract was
touched; the fix reads data the system already persists.

### Defect 2: stale `PackagingPrototype` could bypass a fresh Packaging P0 gate after a narrative revision

**Reproduction**
(`tests/integration/test_stale_artifact_safety.py::test_stale_packaging_reference_survives_a_narrative_revision_round_trip`,
same protocol):

1. `PackagingPrototype` is generated for narrative A.
2. `revise_packaging_p0` → `NARRATIVE`; `NarrativeEngine` runs again,
   producing narrative B; `approve_narrative` → `PACKAGING_P0`. The
   `packaging_prototype_id` reference is never cleared by this round trip
   (by design — the patch explicitly says not to require clearing it).
   The project is now back in `PACKAGING_P0` with the *old* prototype
   (built for narrative A) still referenced, and narrative B never
   evaluated by Packaging P0 at all.
3. `approve_packaging_p0` was called without regenerating packaging.

**Before the fix:** it succeeded, advancing straight to `SCRIPT` on a
promise that was never actually checked against the current narrative.

**Fix**: the identical pattern — `_verify_packaging_is_fresh` checks the
most recent `SUCCESS` `packaging_p0_engine` `ModuleRun`'s `input_ids[2]`
(the `NarrativePlan.id` it ran against) against the project's current
`narrative_plan_id`, raising the new `StalePackagingPrototypeError` on
mismatch. Wired into `approve_packaging_p0`, before the existing
HIGH-risk check.

### Regression protocol followed (patch section 38)

1. Failing tests written first, confirmed red against the pre-fix code
   (both defects reproduced above).
2. Smallest fix: two new private helpers plus two new error types,
   entirely within `app/review/`; zero changes to any engine, any Pydantic
   model, `ProjectState`, the state graph, or any repository schema.
3. Fixing surfaced an ordering bug (the freshness check was initially
   placed *before* the existing "does a report exist at all" check,
   which would have misreported `StaleScriptVerificationError` instead of
   `MissingReviewArtifactError` for a project with no report yet) — caught
   by the pre-existing `test_review_service.py` suite and corrected by
   re-ordering: existence is always checked before freshness.
4. Fixing also broke ten pre-existing `test_review_service.py` unit tests,
   because their fixtures build `PackagingPrototype`/
   `ScriptVerificationReport` artifacts directly (bypassing the real
   engine, to keep review-layer unit tests independent of engine
   behavior) and so never created a matching `ModuleRun`. Per the "do not
   weaken existing tests" instruction, these were **not** loosened — their
   fixtures were corrected to also persist a plausible `ModuleRun`
   (matching what a real engine run would leave behind), which is a
   fixture-accuracy improvement, not a weakened assertion. Every original
   assertion in those ten tests is byte-for-byte unchanged.
5. Full suite re-run after the fix: **559 passed, 0 failed, 0 skipped**
   (540 prior + 19 new integration tests; the ten `test_review_service.py`
   tests pass with their original assertions intact).

## 10. Transaction Partial-Progress Matrix

Every multi-step engine/review flow persists through independent
`Session(...).begin()` blocks per repository call (`app/storage/*.py`) —
confirmed by direct re-reading of `artifacts.py`, `module_runs.py`,
`approvals.py`, and `projects.py`. This is a known, accepted characteristic
across every phase since Phase 4 and is **not** touched this phase, per
the patch's explicit instruction (section 24).

| Operation | Potential partial-progress point | Invariant broken? | Recoverable? | Future fix needed? |
|---|---|---|---|---|
| Any content engine | `save_artifact` succeeds, `update_artifact_reference` never runs | "Project reference always points at the last-saved artifact of its type" — transiently false | Yes — rerunning the same engine (state unchanged, precondition still holds) upserts a fresh artifact and reference together | Low priority; a single-transaction wrapper around save+reference would close the window |
| Any content engine | `update_artifact_reference` succeeds, `update_project_state` never runs | "State reflects whether this stage's artifact step is done" — transiently false | Yes — rerunning the engine succeeds (state unchanged) and produces a coherent artifact+reference+transition together | Same as above |
| Any content engine | `update_project_state` succeeds, `ModuleRun(SUCCESS)` never persists | "`ModuleRun.status` accurately reflects completion" — a `RUNNING` row survives indefinitely even though the project functionally succeeded and moved on | Project itself: yes, fully usable. Audit-log row: no automatic reconciliation exists today | Medium priority — a startup/periodic job to flag `RUNNING` runs whose project has since moved past that stage, or a single transaction spanning the whole sequence |
| Any review action | `save_approval` succeeds, `update_project_state` never runs | "An `APPROVED` `HumanApproval` implies the state advanced" — transiently false | Yes — retrying the same review action succeeds (state unchanged) and creates a second, harmless `APPROVED` row for the same stage (no uniqueness constraint blocks duplicates; `get_latest_approval_for_stage` always resolves to the most recent) | Low priority; same class of fix |
| Phase 12 freshness checks (`_verify_script_verification_is_fresh`, `_verify_packaging_is_fresh`) | N/A — pure reads | None | N/A | None; these introduce no new write, hence no new partial-progress risk |

No partial-progress scenario audited leaves the system **unrecoverable**
or **silently unsafe** — every one heals via a straightforward retry of
the same public operation, because every precondition re-reads live
database state rather than trusting an in-memory value. This is a
materially different category from the two stale-artifact defects in
section 9, which were **not** transaction-timing issues at all — they
existed even under perfect atomicity, because the check being performed
was simply the wrong one (freshness was never being checked).

## 11. Known Technical Debt

1. **Transaction partial-progress** (section 10) — accepted, documented,
   not fixed this phase per explicit instruction. The audit-log
   (`ModuleRun`) staleness case is the one worth prioritizing first if this
   is ever revisited, since it degrades observability rather than
   correctness.
2. **Top-level Source-of-Truth Ownership section incomplete**
   (`docs/TECHNICAL_SPEC_v0.1.md`, section 4 above) — `PackagingPrototype`
   and `ScriptVerificationReport` are never added as explicit bullets,
   even though their ownership is established in their own phase
   sections. Cosmetic; a future phase should append two bullets.
3. **No artifact version history** — `save_artifact` upserts by
   `(project_id, artifact_type)`, so a rewritten `ScriptPlan` or a
   redone `PackagingPrototype` overwrites the previous one; the only
   surviving trail of "what used to be current" is the `ModuleRun` log
   (which the section 9 fix now actually depends on for correctness, not
   just observability). If deep history/audit becomes a product
   requirement, this is the point a real migration would need to address.
4. **Duplicate `HumanApproval` rows are tolerated, not prevented** (see
   section 10's matrix) — pre-existing since Phase 5, orthogonal to
   Phase 12's own findings.

## 12. Recommendation Before Production Layer

**READY_FOR_PRODUCTION_LAYER** (Voice/Visual/Timing/Packaging P1/publishing),
conditioned on the fixes in section 9 already being in place (they are, as
of this phase). The transaction partial-progress gap (section 10 / debt
item 1) is worth a deliberate design decision before a real (non-Fake)
provider and concurrent/multi-user usage are introduced, since retry-based
self-healing becomes less reliable once a human isn't the one driving every
step one at a time — but it is not a blocker for the next phase's likely
scope, and the patch explicitly reserves that decision for later.
