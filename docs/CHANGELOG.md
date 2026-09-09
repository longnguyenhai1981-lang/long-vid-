# Changelog

## Phase 35 — Production CLI / Single-Command Workflow (2026-09-09)

Exposes the complete Phase 34 orchestrator through one installable
command-line tool, `motily`, built on Typer (newly added dependency --
no CLI framework existed before this phase). Purely an execution
surface: no orchestration/approval logic was duplicated into the CLI.

- New `motily` console-script entrypoint (`app.cli.main:app`, configured
  via `[project.scripts]` in `pyproject.toml`) with commands `produce`,
  `resume`, `status`, `runs`, `approve`, `reject`, `doctor`.
- New `app/bootstrap.py`: the one composition root building the
  database engine, the full 19-node graph/adapter registry, and a
  `ProductionService`. Never constructs a provider itself -- this
  codebase has no real (network-calling) `LLMProvider` implementation,
  so `motily produce` in real mode correctly reaches `FAILED`/
  `AdapterConfigurationError` at IDEA rather than doing nothing silently
  or crashing confusingly. Documented as a known limitation, not papered
  over.
- New `app/services/production_service.py`: the `ProductionService`
  façade (`start`/`start_at_node`/`resume`/`status`/`list_runs`/
  `approve_pending`/`reject_pending`) plus the two explicit alias
  tables -- `--target` (idea/research/script/plans/video/qc/final,
  default final) and `--gate` (idea/research/narrative/packaging/
  script/final) -- mapping deterministically to real ProductionNodeIds/
  ApprovalGateTypes, no fuzzy matching.
- New `app/services/production_review.py`: dispatches each of the five
  legacy gates to its exact canonical `app/review/service.py` mutation,
  never the generic Phase 33 ApprovalDecision writer (which already
  refuses them). `SCRIPT_APPROVAL` requires two approve+resume cycles
  (one real HumanApproval row per CLI call, never silently chained).
  Four of the five legacy gates have no reject path at all -- `reject`
  raises a clear `GateRejectionNotSupportedError` for those, mirroring
  `app/review/service.py`'s own real capability exactly.
- New `app/cli/exit_codes.py` (stable scripting contract: 0 success/
  read-only, 2 WAITING_APPROVAL, 3 BLOCKED, 4 FAILED, 5 invalid input, 6
  infrastructure unavailable) and `app/cli/dto.py` (a stable JSON DTO
  decoupled from ProductionRun's own persisted shape).
- `approve` never auto-resumes (separate explicit `resume` command);
  `produce` always creates a new ProductionRun (no deduplication).
  `motily doctor` performs local-only diagnostics (DB directory
  writable, ffmpeg/ffprobe on PATH, GEMINI_API_KEY presence) -- never a
  network call.
- New focused tests: `tests/test_cli_dto_and_aliases.py`,
  `tests/test_production_review_dispatch.py`,
  `tests/test_cli_commands.py` (Typer CliRunner-driven, using two
  explicit DI seams -- `app.cli.main._composition_override`/
  `_context_factory_override` -- never monkeypatching internals),
  `tests/test_bootstrap_composition.py`, and
  `tests/test_full_cli_integration.py` (the complete no-network
  pipeline driven only through `motily produce`/`approve`/`resume`,
  IDEA through FINAL_MEDIA_APPROVAL, ending SUCCEEDED, then proving a
  further resume reuses everything with zero re-execution). Every
  Phase 24-34 test continues to pass unmodified.
- New `scripts/evaluate_production_cli.py`: drives the real Typer app
  through its own CliRunner, printing a `$ motily ...` transcript and
  persisting `data/production_cli_evaluation/motily_phase35_report.json`.
- Verified on Windows: PowerShell and Git Bash invocation, Vietnamese/
  Unicode CLI arguments and terminal output, and a database path
  containing spaces.

## Phase 34 — Full Upstream Pipeline Wiring & Approval Gate Unification (2026-09-09)

Wires the remaining twelve upstream creative/planning nodes (IDEA
through PACKAGING_P1) into `ProductionRunner`, so a single run now
drives the entire implemented chain -- IDEA through FINAL_MEDIA_APPROVAL
-- while `app/orchestration/` stays exactly as provider-unaware as
Phase 33 left it.

- New `app/production_adapters/` package (deliberately outside
  `app/orchestration/`): `upstream.py` holds the twelve real adapters --
  every underlying engine imports `app.llm` (confirmed by reading each
  `engine.py` directly), which is why this bridge exists as a separate
  package. `registry.py` combines these with the seven Phase 33
  downstream adapters (re-exported unchanged from
  `app/orchestration/adapters.py`) into one complete 19-node graph with
  the real gate placement. Phase 33's own `build_default_graph()`/
  `build_default_adapters()`/evaluation script/tests are untouched.
- Freshness for the five artifact models with no upstream foreign key at
  all (`FeasibilityReport`, `ResearchPackage`, `NarrativePlan`,
  `ScriptPlan`, `ScriptVerificationReport` -- confirmed by reading each
  model) compares the latest SUCCESS `ModuleRun`'s own recorded
  `input_ids` against current upstream artifact ids, mirroring
  `app/review/service.py`'s own `_verify_packaging_is_fresh`/
  `_verify_script_verification_is_fresh` exactly -- never "latest
  artifact exists". The other seven compare real foreign-key fields
  directly, exactly like Phase 33's downstream adapters.
- Three new `BlockedReason` codes -- `FEASIBILITY_NOT_PASSED`,
  `SCRIPT_VERIFICATION_NOT_PASSED`, `PACKAGING_RISK_TOO_HIGH` -- mirror
  `MediaQCAdapter`'s own `QC_NOT_READY` pattern: a node whose adapter
  reports `gate_ok()=False` is blocked before its gate is ever offered.
  The reason is now adapter-configurable via an optional
  `NodeAdapter.gate_not_ready_reason` attribute, read via
  `getattr(..., BlockedReason.QC_NOT_READY)` -- fully backward
  compatible with every adapter that doesn't declare it.
- Approval-gate unification (`app/orchestration/gates.py`): five new
  `ApprovalGateType` members (`IDEA_APPROVAL`, `RESEARCH_APPROVAL`,
  `NARRATIVE_APPROVAL`, `PACKAGING_P0_APPROVAL`, `SCRIPT_APPROVAL`) map
  to five exact `app/review/service.py` decision points (verified
  against all sixteen of that file's functions directly, never assumed
  from names). These five are read-only through `gate_decision_for`,
  reconstructed purely from `ProjectState` + `HumanApproval` -- no new
  decision table, no duplicated review logic.
  `approve_gate`/`reject_gate` raise a new `LegacyGateNotWritableError`
  for any of them; only `FINAL_MEDIA_APPROVAL` remains writable through
  the `ApprovalDecision` table. `SCRIPT_APPROVAL` spans
  `SCRIPT_VERIFICATION` then `SCRIPT_REVIEW` (no engine work between
  them) collapsed into one gate on `SCRIPT_VERIFY` -- its subject
  resolves to `project.script_plan_id` since `ScriptVerificationReport`
  has no `id` field at all.
- All 19 graph nodes now have real, executable adapters -- zero
  `UnwiredNodeAdapter` remain, enforced by a dedicated test.
- New focused tests: `tests/test_production_adapters_upstream.py` (per-
  adapter artifact resolution/engine invocation/freshness-reuse/stale-
  rerun for all twelve), `tests/test_production_adapters_registry.py`
  (full 19-node coverage, zero unwired adapters, exact 6-gate
  placement), `tests/test_orchestration_legacy_gates.py` (real
  review-service calls -> gate reconstruction for all five legacy
  gates), `tests/test_orchestration_purity.py` extended (proves
  `app/production_adapters/upstream.py` genuinely imports `app.llm`),
  `tests/test_full_production_integration.py` (the complete no-network
  pipeline stopping at all six real gates, resuming past each, ending
  SUCCEEDED with full artifact-id-chain verification), and
  `tests/test_phase33_compatibility.py` (a Phase-33-style persisted
  ProductionRun with unwired upstream nodes still deserializes and can
  be resumed through the new registry). Every Phase 24-33 test continues
  to pass unmodified.
- New `scripts/evaluate_full_production_orchestrator.py` (Phase 33's own
  `evaluate_production_orchestrator.py` preserved unchanged as the
  historical downstream-only slice): drives the complete pipeline from a
  bare project through all six real approval gates to SUCCEEDED,
  printing a per-run trace and summary, and persisting
  `data/full_orchestrator_evaluation/motily_phase34_report.json`.

## Phase 33 — Deterministic Production Orchestrator & Approval Gates (2026-09-08)

Adds the first real coordinator that executes the existing pipeline
modules through an explicit, code-defined dependency graph, reusing
fresh artifacts instead of recomputing them, and stopping at human
approval gates. This phase coordinates existing engines -- it does not
redesign any of them, does not merge modules into one service, adds no
agent/LLM decision-making, and runs synchronously with no background
task queue.

- New `app/orchestration/graph.py`: `ProductionGraph`/
  `ProductionNodeDefinition` -- a static DAG over 19 node ids, never
  inferred from artifact fields. Validated once at construction
  (duplicate/unknown/self dependency, cycle); topologically sorted via
  Kahn's algorithm with a `sorted()` tie-break at every step for
  deterministic, reproducible ordering. `ancestors_closure(target)`
  returns the target plus every transitive dependency, never a
  descendant.
- New `app/orchestration/models.py`: `ProductionNodeStatus`
  (`PENDING`/`BLOCKED`/`READY`/`RUNNING`/`SUCCEEDED`/`FAILED`/
  `WAITING_APPROVAL`/`SKIPPED`) is not derived from `ModuleRunStatus`
  alone -- `SUCCEEDED` covers both fresh execution and reuse, and a
  fully successful module can still leave the run `WAITING_APPROVAL`.
  `NodeRunRecord`/`ProductionRun` persist the full trace with stable
  `BlockedReason` codes (`BLOCKED_DEPENDENCY`/`WAITING_GATE`/
  `GATE_REJECTED`/`QC_NOT_READY`/`NODE_FAILED`/`NODE_NOT_WIRED`).
  `ApprovalGateType`/`ApprovalDecision` bind one `APPROVED`/`REJECTED`
  decision to an exact subject artifact id -- a decision against a
  since-replaced artifact never matches the new one.
- New `app/orchestration/registry.py`: the `NodeAdapter` protocol
  (`load_current`/`is_fresh`/`execute`/`gate_ok`) is the only boundary
  between the orchestrator and every existing module. `UnwiredNodeAdapter`
  covers the twelve LLM-based creative-planning nodes (IDEA through
  PACKAGING_P1) this phase does not execute -- `execute()` always raises
  `NodeNotWiredError`, an explicit documented boundary rather than a
  brittle fake adapter.
- New `app/orchestration/adapters.py`: seven real adapters
  (`VoiceRenderAdapter`, `VisualRenderAdapter`, `TimelineAdapter`,
  `VideoRenderAdapter`, `CaptionBuildAdapter`, `SubtitleExportAdapter`,
  `MediaQCAdapter`) wrap the existing Phase 24-32 renderers/builders
  completely unchanged. Freshness is never centralized -- each adapter's
  `is_fresh` reads the artifact's own already-declared foreign-key id
  fields and compares them against the current upstream artifact, the
  same information each renderer's own private freshness check already
  uses. `build_default_graph()`/`build_default_adapters()` assemble the
  full 19-node graph with `gate_after=FINAL_MEDIA_APPROVAL` on
  `MEDIA_QC` only. `SUBTITLE_EXPORT` deliberately covers both SRT export
  and optional caption burn-in as one node, mirroring
  `SubtitleRenderer.run()`'s own single-call API.
- New `app/orchestration/runner.py`: `ProductionRunner.run_until(
  project_id, target_node)` walks the target's ancestor closure in
  topological order. An unmet dependency resolves to `BLOCKED`
  (`BLOCKED_DEPENDENCY`) rather than being skipped, so the full trace is
  always persisted. A gated node whose `gate_ok()` fails (QC `FAIL`) is
  `BLOCKED` (`QC_NOT_READY`) before any approval is ever requested; an
  open gate with no decision yet is `WAITING_APPROVAL`; a `REJECTED`
  decision is `BLOCKED` (`GATE_REJECTED`) without ever deleting or
  regenerating the artifact. The run's terminal status and stop reason
  are always taken from the first non-`SUCCEEDED` node in topological
  order, so a `BLOCKED_DEPENDENCY` cascade never masks the real upstream
  cause. `run_until()`/`resume(run_id)` are fully idempotent: every node
  re-derives its own status from scratch each call, so a repeat call
  reuses fresh outputs with zero re-execution and a resolved gate is
  picked up with no special-cased retry logic.
- New `app/orchestration/gates.py`: `approve_gate`/`reject_gate`/
  `gate_decision_for` are the only human-approval write path (no UI
  simulation) -- an unregistered gate type raises
  `UnknownApprovalGateError`. This is a new, parallel mechanism
  alongside the existing `app/review/service.py` gates, not a
  replacement. Only `FINAL_MEDIA_APPROVAL` is actually enforced this
  phase -- `IDEA_APPROVAL`/`RESEARCH_APPROVAL`/`SCRIPT_APPROVAL` are
  documented for a complete vocabulary, since `project.state ==
  MVP_COMPLETE` already proves the existing review-service chain
  leading to it was satisfied.
- Storage (`app/storage/orm.py`): two new tables in the same database --
  `production_runs` and `approval_decisions` -- since a project
  accumulates many `ProductionRun`/`ApprovalDecision` rows over time,
  unlike the one-row-per-artifact-type convention. `ProductionRun` never
  replaces `ModuleRun`; a `NodeRunRecord` references a `module_run_id`
  where the underlying adapter produced one. New
  `app/storage/production_runs.py`/`app/storage/approval_decisions.py`
  repositories mirror `app/storage/module_runs.py`'s own upsert/query
  shape; new `ProductionRunNotFoundError` in `app/storage/errors.py`.
- No orchestration-layer AI: `tests/test_orchestration_purity.py`
  asserts (fresh-subprocess `sys.modules` check plus a static per-file
  source scan) that no file under `app/orchestration/` -- adapters
  included, since every wired adapter delegates to an already-LLM-free
  renderer -- ever imports `app.llm` or a provider package.
- New focused tests: `tests/test_orchestration_models.py`, `tests/
  test_orchestration_graph.py`, `tests/test_orchestration_gates.py`,
  `tests/test_orchestration_runner.py` (fake-adapter happy path/
  idempotency/stop-resume/rejection/stale-invalidation/execution-
  failure/QC-gate semantics), `tests/test_orchestration_purity.py`, and
  `tests/test_orchestration_integration.py` (the real wired 7-node
  slice -- `FakeTTSProvider`/`FakeVisualProvider` stand in for network
  providers, everything else real local `ffmpeg`/`ffprobe` -- reaching
  `WAITING_APPROVAL` at `FINAL_MEDIA_APPROVAL`, then `SUCCEEDED` after
  `approve_gate` + `resume`). Every Phase 24-32 renderer/builder test
  continues to pass unmodified (301 passed).
- `scripts/evaluate_production_orchestrator.py` (new): builds a
  disposable local MVP_COMPLETE project, runs the real wired slice via
  `ProductionRunner` to `MEDIA_QC` (stops `WAITING_APPROVAL`), approves
  the exact final-media artifact, and resumes to `SUCCEEDED` -- printing
  a full per-node trace for both passes plus a summary (run id, first/
  final status, executed vs. reused node counts, QC status, final media
  artifact id, approval subject id).

## Phase 32 — Automated Media QC / Production Quality Gate (2026-09-08)

Adds a deterministic final layer that inspects an already-produced video
(and its optional subtitle sidecar) and decides whether it is
technically safe to hand to a human for approval. QC never regenerates,
fixes, masters, or otherwise alters media -- it only detects and reports
technical problems.

- New `MediaQCReport`/`QCCheckResult` (`app/media_qc/models.py`):
  `overall_status` (`PASS`/`WARN`/`FAIL`) can never disagree with
  `checks` -- its own validator recomputes the FAIL-dominates-WARN-
  dominates-PASS rule and rejects any mismatch; `MediaQCReport.build()`
  derives it automatically. `ready_for_human_review` (PASS/WARN -> true,
  FAIL -> false) is a plain property, deliberately not a pydantic
  `computed_field` -- that would break the artifact round-trip under
  this model's own `extra="forbid"` policy (confirmed empirically).
- New `MediaProbe` abstraction (`app/media_qc/probes.py`): `FFprobeClient`
  (structured metadata incl. exact rational-FPS conversion, e.g.
  `30000/1001`, never a string compare), `FrameSampler` (deterministic
  evenly-spaced-percentage frame extraction into an always-cleaned-up
  temp dir), `AudioAnalyzer` (`ffmpeg volumedetect`, parsed
  deterministically). Argument-list subprocess calls only, never
  `shell=True`. A broken specific file raises `MediaProbeError` (always
  caught, never a crash); only a missing `ffmpeg`/`ffprobe` executable
  is an infrastructure error allowed to propagate.
- QC is diagnostic, never fail-fast: `MediaQCInspector.inspect()` always
  returns a complete report -- a missing file, a corrupt container, a
  codec/duration/canvas mismatch, silence, or a stale subtitle are all
  FAIL/WARN results, never exceptions.
- Required video checks: file exists/non-zero/decodable, stream
  presence, codec/pixel-format/fps/canvas/duration match against the
  persisted `EncodedVideoAsset`/`CaptionedVideoAsset` (never hardcoded
  fixture dimensions), plus a coarse duration-sanity check. Duration
  policy: outside a 100ms default tolerance is FAIL outright, no WARN
  band. Pixel-format mismatch is FAIL (device compatibility is part of
  the production contract).
- Frozen/black-frame detection tuned for this channel's own static/
  limited-motion style: 5 frames at deterministic 10/30/50/70/90%
  positions (Pillow mean-luminance and consecutive-frame difference --
  no OCR, no CV models). ALL sampled frames black is FAIL (a single
  dark frame never fails the whole video); ALL sampled frames
  identical is WARN, never FAIL -- a legitimate static explainer must
  not be auto-rejected.
- Audio checks via `ffmpeg volumedetect`: `mean_volume` below -60dBFS
  across the whole program is FAIL (effectively silent); `max_volume`
  at/above 0dBFS is FAIL (clipping) -- both native `ffmpeg`
  measurements, no custom DSP.
- Subtitle checks (only when a `SubtitleFileAsset`/`CaptionManifest`
  pair exists -- captions are optional): file exists, valid UTF-8, cue
  count/timestamps/timeline-bounds/non-empty-text match the
  `CaptionManifest`, and the strictest check, `QC_SUBTITLE_MATCH` -- the
  on-disk SRT is byte-for-byte what `render_srt(caption_manifest)` would
  produce right now, catching a stale/wrong subtitle artifact directly.
  Added a new `parse_srt()` to `app/captions/srt.py` (the exact inverse
  of Phase 31's own `render_srt`) for this.
- New `MediaQCRenderer` (`app/renderers/media_qc/`) prefers a fresh
  `CaptionedVideoAsset` over the plain `EncodedVideoAsset` when one
  exists, reuses the full freshness chain every prior renderer already
  verifies, and rejects a STALE caption/subtitle chain (but not a
  missing one). `ModuleRun` is `SUCCESS` whenever inspection completed
  regardless of PASS/WARN/FAIL -- `FAILED` is reserved for the inspector
  genuinely being unable to run at all.
- New focused tests: `tests/test_media_qc_models.py`, `tests/
  test_media_qc_probes.py`, `tests/test_media_qc_rules.py`, `tests/
  test_media_qc_inspector.py`, `tests/test_media_qc_integration.py`
  (real `ffmpeg`-built healthy/silent/black/frozen/corrupt fixtures),
  and `tests/test_media_qc_renderer.py`. Every Phase 28-31 test
  continues to pass unmodified.
- `scripts/evaluate_media_qc.py` (new): extends `scripts/
  evaluate_captions.py`'s own finished production chain with the real
  `MediaQCRenderer`, producing exactly one JSON report:
  `data/media_qc_evaluation/motily_phase32_qc_report.json`. All 23
  checks PASS on the real fixture (`overall_status=PASS`,
  `ready_for_human_review=True`).

## Phase 31 — Deterministic Subtitle/Caption Execution (2026-09-08)

Turns existing authored script/voice/timeline alignment into
deterministic subtitle artifacts and, where the local environment
supports it, executes burned-in captions locally. Captions are built
from authored narration, never from speech recognition -- no Whisper, no
STT, no LLM anywhere in this phase.

- Source-text identity chain, fully reused, nothing new upstream:
  `TimelineSegment.narration[i].chunk_id` → `VoicePlan.chunks[chunk_id]
  .line_ids` → the exact `ScriptLine.text`. A chunk spanning multiple
  lines joins those lines' texts with a single space, in `line_ids`' own
  order -- never re-punctuated, re-capitalized, or normalized. For the
  common one-line-per-chunk case, the cue's text is byte-for-byte that
  one `ScriptLine.text`.
- New `CaptionCue`/`CaptionManifest` (`app/models/caption.py`): exactly
  one cue per `TimelineAudioRef`, timing copied from the same
  cumulative-duration arithmetic `TimelineBuilder` already used, so a
  `CaptionManifest` can never disagree with its own `TimelineManifest`.
  Validates cue chronology/non-overlap, id uniqueness, and manifest-
  duration bounds.
- New `CaptionBuilder` (`app/renderers/caption/`) reuses the exact same
  full freshness chain `VideoRenderer` already checks -- deliberately not
  a lighter caption-only exception -- with the standard `ModuleRun`
  lifecycle.
- New deterministic UTF-8 SRT exporter (`app/captions/srt.py`, pure,
  exact-string-tested): millisecond formatting, timestamps past 1/10
  hours, Vietnamese Unicode, punctuation, multiline text, a defined
  final-newline policy. `SubtitleRenderer` (`app/renderers/subtitle/`)
  always exports SRT and persists a `SubtitleFileAsset` -- SRT is the
  only exported format.
- New optional local caption burn-in (`app/captions/burn_in.py`, opt-in
  via `SubtitleRendererInput.burn_in_settings`): `ffmpeg`'s `subtitles`
  filter (libass) renders SRT into an already-encoded MP4 -- video always
  re-encoded, audio always stream-copied (`-c:a copy`), guaranteeing
  narration/music/SFX are untouched. Produces a new, separate
  `CaptionedVideoAsset`, never an in-place rewrite of `EncodedVideoAsset`.
  Checked once via `ffmpeg -filters` before building the real command;
  never a silent fallback, never a downloaded font. Confirmed empirically
  on this development environment: Vietnamese diacritics render correctly
  via the system's own Arial/fontconfig resolution.
- FFmpeg filtergraph path escaping confirmed empirically: spaces,
  parentheses, Windows drive letters, backslashes, and Vietnamese
  Unicode names all work inside the `subtitles` filter's own filename
  argument. A literal single quote in the SUBTITLE PATH is rejected
  explicitly (`InvalidSubtitlePathError`) -- confirmed this local
  ffmpeg/libass build cannot reliably preserve one there; the SRT's own
  text content may still contain apostrophes freely.
- Burn-in is opt-in end to end: a normal SRT-only `SubtitleRendererInput`
  never loads an `EncodedVideoAsset` and never invokes `ffmpeg` at all.
- New focused tests: `tests/test_caption_models.py`, `tests/
  test_caption_builder.py`, `tests/test_srt_exporter.py`, `tests/
  test_caption_burn_in.py`, `tests/test_caption_burn_in_integration.py`
  (real `ffmpeg`, skips cleanly without the `subtitles` filter), and
  `tests/test_subtitle_renderer.py`. Every Phase 27-30 test continues to
  pass unmodified -- captions disabled means every existing renderer call
  behaves exactly as before.
- `scripts/evaluate_captions.py` (new): extends `scripts/
  evaluate_audio_mix.py`'s own project (music `BED`/`DUCK`/`LIFT`, a real
  `CROSSFADE`, a `SLOW_ZOOM_IN`, two `SFX_TRIGGER` events) with the real
  `CaptionBuilder`/`SubtitleRenderer`, producing
  `data/caption_evaluation/motily_phase31_captions.srt` always and
  `data/caption_evaluation/motily_phase31_preview.mp4` when local burn-in
  is available. Prints cue text/timing, `ffprobe`-measured metadata, and
  confirms the source video's crossfade/motion/music/SFX are unchanged.

## Phase 30 — Deterministic Music & SFX Mix Execution (2026-09-08)

Turns `TimelineManifest`'s existing `MUSIC_BED_START`/`MUSIC_DUCK`/
`MUSIC_LIFT`/`MUSIC_BED_END`/`SFX_TRIGGER` cues (declared since Phase 27,
never executed before now) into a real mixed audio track underneath
narration, which remains the exact, unchanged timing authority. No AI
music selection, no beat matching, no creative audio inference, no
search/download of any audio.

- `AudioAssetBindings` (`music_bed_path: Path | None`, `sfx_by_id:
  dict[str, Path]`) is the only way a cue resolves to a real file -- no
  inference, no filesystem scanning, no default/random SFX.
  `VideoRendererInput.audio_bindings` defaults to `None`: every
  pre-Phase-30 caller gets exactly Phase 29's audio behavior with zero
  code changes, regardless of the manifest's own always-present music
  cues (`MusicState` has no "none" member). Passing an `AudioAssetBindings`
  (even empty) opts into real mix execution.
- New pure planning layer, `app/video_encoder/audio_mix.py`
  (`build_audio_mix_plan`): no filesystem, no subprocess. The music state
  machine rejects `MUSIC_BED_START` while active, `MUSIC_DUCK`/
  `MUSIC_LIFT`/`MUSIC_BED_END` while inactive, out-of-order or
  duplicate-timestamp cues, and a gain region too short for its own
  crossfade ramp (`InvalidMusicCueSequenceError`). An unterminated
  `MUSIC_BED_START` implicitly closes at `total_duration_ms` (chosen,
  documented policy).
- Music: one background bed, `-stream_loop -1` (loops indefinitely at the
  demuxer level -- never measures the source's own duration) trimmed per
  gain region via `atrim`, handling both "shorter than needed" and
  "longer than needed" with the same mechanism. Fixed gains
  (`music_bed_gain_db`=-24dB/`music_duck_gain_db`=-32dB/
  `music_lift_gain_db`=-20dB by default) -- never dynamically normalized,
  never inspects narration loudness. Adjacent regions crossfade via
  `acrossfade` over `music_gain_ramp_ms` (80ms default), using the same
  duration-neutral extend-then-overlap trick Phase 29's video `CROSSFADE`
  already established -- gain ramps were cleanly achievable, so this
  phase did not need the MVP hard-switch fallback.
- SFX: each `SFX_TRIGGER` cue's own `reference` (already populated by
  Phase 27's `TimelineBuilder` from `RenderedVoiceTake.sfx_opportunity`)
  resolves via `AudioAssetBindings.sfx_by_id` -- no new `TimelineCue`
  field needed. Starts exactly at `cue.timestamp_ms`, trimmed at the
  timeline's own end if it would otherwise run past it, at one fixed
  `sfx_gain_db` (-10dB default).
- Final mix: narration (Phase 28/29's exact, unchanged chain) + music (if
  any) + every SFX, combined via `amix=inputs=N:duration=longest:
  normalize=0` -- `normalize=0` is essential, since `amix`'s own default
  would otherwise silently scale down every carefully-chosen gain level.
  The outer command's own final `-t` bound remains authoritative;
  music/SFX can never extend the final output.
- `EncodedVideoAsset`/`VideoEncodeResult` gain `has_music`,
  `sfx_event_count`, `music_cue_count` (all optional, default "none
  used") -- no second, competing media-output artifact.
- New focused tests: `tests/test_audio_mix_plan.py` (the pure planner,
  every state-machine transition/rejection, SFX resolution), `tests/
  test_video_encoder_command_builder.py` (music/SFX filter construction:
  gain ramps, loop/trim, `adelay` positioning, full-mix ordering,
  determinism), `tests/test_video_encoder.py` (every new validation/error
  path), `tests/test_video_encoder_integration.py` (real `ffmpeg`:
  narration+music, narration+SFX, full mix with `DUCK`/`LIFT`+SFX+
  `CROSSFADE`+motion-lite), and `tests/test_video_renderer.py` (the
  `audio_bindings` opt-in and its explicit failure path). Every Phase
  28/29 test continues to pass unmodified.
- `scripts/evaluate_audio_mix.py` (new): a music-`BED`/`DUCK`/`LIFT` +
  real `CROSSFADE` + `SLOW_ZOOM_IN` + 2 `SFX_TRIGGER` timeline, mixed
  with three locally-generated sine-tone WAV fixtures (never real or
  copyrighted audio) via `AudioAssetBindings`, producing exactly one MP4:
  `data/audio_mix_evaluation/motily_phase30_preview.mp4`. Prints path,
  size, codecs, fps, canvas, `ffprobe`-cross-checked duration, crossfade
  count, motions used, music cue count, SFX event count, bindings used,
  and `ffmpeg` version; no live AI/TTS API.

## Phase 29 — Deterministic Motion-Lite Video Execution (2026-09-08)

Closes Phase 28's one hard blocker -- `CROSSFADE` now executes for real
-- and adds a deliberately tiny, deterministic per-segment camera-motion
vocabulary. Both remain pure local `ffmpeg` execution; no AI reasoning,
no content analysis, no LLM-chosen motion anywhere in this phase.

- `CROSSFADE` is executed via `ffmpeg`'s `xfade` filter
  (`transition=fade`), no longer rejected. Only `TimelineSegment.
  transition_out` decides a join -- `transition_in` (on any segment,
  including the first) and the LAST segment's own `transition_out` are
  never consulted, since neither has an adjacent partner to blend with;
  this is a deliberate single-source-of-truth choice.
- Duration-neutral by construction: the earlier segment's own `-loop`
  duration is extended by `crossfade_duration_ms` before the fade runs,
  and `xfade`'s `offset` is always the sum of the prior segments'
  ORIGINAL (never-extended) durations -- `TimelineManifest.
  total_duration_ms` still equals the final encoded duration exactly, in
  any chain mixing `CUT`/`HOLD`/`CROSSFADE` joins in any order. Video
  chain falls back to Phase 28's exact flat `concat=n=N` when no
  crossfade exists anywhere in the request.
- `VideoEncodingSettings.crossfade_duration_ms` (default 300ms, one
  global setting, no per-segment override -- `AssemblySegment`/
  `TimelineSegment` carry no numeric transition-duration field today).
  `InvalidCrossfadeDurationError` if not strictly shorter than BOTH
  adjacent segments' own `duration_ms` -- never silently shortened.
  `CROSSFADE` is visual only: the narration filter graph
  (`_build_audio_filter_parts`) is Phase 28's exact function, unchanged.
- Motion-lite vocabulary (`VisualMotionType`, `app/models/timeline.py`):
  `STATIC` (default), `SLOW_ZOOM_IN`, `SLOW_ZOOM_OUT`, `PAN_LEFT`,
  `PAN_RIGHT` -- always explicitly authored via
  `TimelineBuilderInput.visual_motions: dict[segment_id, VisualMotionType]`
  (mirroring `ti_state_sources`/`composition_specs`'s own per-item-
  override shape), never inferred. Lives on `TimelineSegment.visual_motion`
  (a backward-compatible field defaulting to `STATIC`), not on
  `ScriptPlan`/`VisualPlan`/`AssemblyPlan` -- motion is presentation
  execution, not narrative/scientific content. Spans exactly a segment's
  own authored duration; never changes segment timing; operates only on
  the segment's final resolved raster.
- Zoom uses `zoompan` (not `crop`, corrected after empirical testing
  showed `crop`'s `w`/`h` are evaluated once at filter-graph
  configuration time, not per frame): the zoom expression is a pure
  function of `on` (zoompan's own monotonic output-frame index), never
  self-referencing, avoiding `zoompan`'s well-known accumulation quirk.
  Range `1.00→1.06`/`1.06→1.00`, deliberately small. Pan uses `crop`'s
  `x`/`y` (genuinely re-evaluated per frame): a 5%-wider pre-scale, then
  a linear `t`-based crop-window `x` position; `MotionSourceTooSmallError`
  if the canvas is too small for any whole-pixel travel. No vertical pan.
- Found and fixed via manual evaluation: `zoompan`'s internal timebase
  doesn't match a plain `scale`/`crop` chain's; `concat` tolerates the
  mismatch but a downstream `xfade` does not. Every segment's
  normalization chain now ends in `settb=AVTB`, regression-tested with
  the exact repro shape (`[zoom]`--CUT-->`[static]`--CROSSFADE-->`[pan]`).
- `FFmpegFilterUnavailableError` if the resolved `ffmpeg` build is
  missing a filter the request actually needs (`xfade`/`zoompan`/
  `crop`+`scale`, checked only when relevant) -- verified once via
  `ffmpeg -filters` before the real command is built.
- `EncodedVideoAsset`/`VideoEncodeResult` gain `crossfade_count` and
  `motion_profile_used` (both optional, default empty) -- no second,
  competing video-output model.
- New/updated focused tests across `tests/test_video_encoder_models.py`,
  `tests/test_video_encoder_command_builder.py` (crossfade timing/offsets,
  all four motion filters, determinism), `tests/test_video_encoder.py`
  (`transition_in`/last-segment-`transition_out` ignored, invalid
  crossfade duration, required-filter check, `MotionSourceTooSmallError`),
  `tests/test_video_encoder_integration.py` (real `ffmpeg`: crossfade
  encode, motion encode, and the timebase-pitfall regression),
  `tests/test_timeline_builder.py` (`visual_motions` override/default/
  unknown-reference), and `tests/test_video_renderer.py` (real crossfade
  encode end to end, `InvalidCrossfadeDurationError` failure path). The
  two Phase 28 tests that asserted blanket `CROSSFADE` rejection were
  rewritten for Phase 29's real semantics; every other Phase 28 test
  continues to pass unmodified.
- `scripts/evaluate_motion_lite_video.py` (new): extends
  `scripts/evaluate_video_encoder.py`'s project with an `AssemblyPlan`
  authoring one `CUT` and one real `CROSSFADE`, plus `visual_motions`
  overrides (`SLOW_ZOOM_IN`/`PAN_RIGHT`), through the real
  `VideoRenderer.run()` path to produce exactly one MP4:
  `data/motion_lite_evaluation/motily_phase29_preview.mp4`. Prints path,
  size, codecs, fps, canvas, `ffprobe`-cross-checked duration, crossfade
  duration/count, motions used, and `ffmpeg` version; no live AI/TTS API.

## Phase 28 — Deterministic Local Video Encoder (2026-09-08)

Consumes a Phase 27 `TimelineManifest` and executes it into the first
real, locally playable MP4. `app/video_encoder/` (standalone, no
database/artifact access) combines already-rendered static images and
WAV narration via the system `ffmpeg`/`ffprobe` executables;
`app/renderers/video/` (`VideoRenderer`) is the artifact-driven,
freshness-checked layer that resolves a `TimelineManifest` into an encode
job and persists the result. Pure execution -- no AI reasoning.

- FFmpeg dependency strategy: system `ffmpeg`/`ffprobe` only, via
  `subprocess` with argument lists (never `shell=True`). Resolved from
  `VideoEncodingSettings.ffmpeg_path`/`ffprobe_path` or `PATH`;
  `FFmpegNotFoundError`/`FFprobeNotFoundError` if neither exists -- never
  downloads or installs a binary.
- New contracts (`app/video_encoder/models.py`): `VideoEncodingSettings`
  (fps=30 default, `libx264`/`aac`/`yuv420p`), `VideoNarrationClip`,
  `VideoSegmentInput`, `VideoEncodeRequest` (`.mp4`-only output enforced),
  `VideoEncodeResult`.
- Canvas policy: the first segment's own image establishes output width/
  height (via Pillow); every later segment must match exactly or
  `VideoDimensionMismatchError` fails before `ffmpeg` runs -- no silent
  resize.
- `FFmpegCommandBuilder.build()` (`app/video_encoder/encoder.py`) is a
  pure function -- fully unit-testable without invoking `ffmpeg`. No
  temporary concat-list files: every image/narration clip is its own
  `-i` input, concatenated via one `-filter_complex` graph (`concat`
  filter, not the concat demuxer). Reusing the same image across
  segments never duplicates bytes on disk.
- Narration: back-to-back clips per segment, silence-padded
  (`apad=whole_dur=...`) if shorter than the authored `duration_ms`
  (measured via stdlib `wave`, matching Phase 27's own technique);
  `NarrationDurationOverflowError` if real audio would be longer -- never
  truncated or time-stretched.
- Transitions: `CUT`/`HOLD` both execute as an instantaneous cut;
  `CROSSFADE` (reachable via Phase 27's `DISSOLVE` mapping) is explicitly
  rejected (`UnsupportedVideoTransitionError`) -- deferred to a future
  motion-lite phase, never silently downgraded.
- `EncodedVideoAsset` (`app/models/video.py`): metadata-only persisted
  artifact (`ENCODED_VIDEO_ASSET_ARTIFACT_TYPE`), MP4 bytes never stored
  in SQLite.
- `VideoRenderer` freshness chain goes one link deeper than Phase 27's
  own: beyond ScriptPlan→VoicePlan→VisualPlan→AssemblyPlan, it confirms
  the CURRENT VoiceRenderManifest/VisualRenderManifest are the exact ones
  (by id) the TimelineManifest recorded building from
  (`StaleTimelineManifestError`) -- catching a re-render that happened
  without rebuilding the timeline. A segment whose visual was never
  actually rendered (a requirement, not a file) fails explicitly
  (`UnrenderedVisualSegmentError`).
- `ModuleRun` unchanged: RUNNING → SUCCESS/FAILED, no project-state
  transition, no retry loop; an `ffmpeg` failure is captured with exit
  code + stderr (`VideoEncodingProcessError`), never retried.
- Determinism: identical inputs produce an identical `ffmpeg` argv and
  identical business timing (`duration_ms` is always the timeline's own
  authoritative total, never re-measured from the output container).
  Byte-identical MP4 across `ffmpeg` versions/environments is explicitly
  NOT required or guaranteed.
- 74 new focused tests across `tests/test_video_encoder_models.py`,
  `tests/test_video_encoder_command_builder.py`, `tests/test_video_encoder.py`
  (fake-subprocess-runner validation/process-behavior coverage),
  `tests/test_video_encoder_integration.py` (opt-in real-`ffmpeg` 3-segment
  encode + `ffprobe` re-verification, skips cleanly without `ffmpeg`), and
  `tests/test_video_renderer.py` (all new). Every prior test continues to
  pass unmodified.
- `scripts/evaluate_video_encoder.py` (new): extends
  `scripts/evaluate_timeline_builder.py`'s three-frame project (with an
  all-CUT/HOLD `AssemblyPlan`) through the real `VideoRenderer.run()` path
  to produce exactly one MP4:
  `data/video_encoder_evaluation/motily_phase28_preview.mp4`. Prints
  path, size, codecs, dimensions, fps, and duration cross-checked via
  `ffprobe` against the asset's and timeline's own authoritative
  duration; no live AI/TTS API.

## Phase 27 — Deterministic Timeline Assembly (2026-09-08)

Introduces `TimelineBuilder` (`app/renderers/timeline/`), which turns an
already-locked ScriptPlan/VoicePlan/VisualPlan/AssemblyPlan chain plus
their already-rendered VoiceRenderManifest/VisualRenderManifest outputs
into one deterministic, executable `TimelineManifest`
(`app/models/timeline.py`) -- the final planning-adjacent artifact before
real video encoding. No ffmpeg, no CapCut, no audio mixing, no LLM timing
inference.

- Timebase: integer milliseconds. `TimelineSegment` carries `start_ms`/
  `end_ms`/`duration_ms`, internally consistency-checked.
- Narration duration is the sole timing authority:
  `_resolve_audio_duration_ms` prefers a `RenderedVoiceTake`'s recorded
  `duration_seconds`; falls back to reading a WAV file's own header
  (`frames / framerate`) via stdlib `wave` -- no new dependency, never
  estimated from text length. A non-WAV file with no recorded duration
  raises `UnresolvableAudioDurationError`.
- Alignment reuses `AssemblyPlan` (Phase 15) as-is -- no new contract
  field needed. Its normalization already guarantees each segment's
  `voice_chunk_ids`/one-segment-per-`VisualBeat` relationship; multiple
  narration chunks sharing one visual is already representable via a
  segment's own multi-chunk `voice_chunk_ids`.
- `TimelineVisualRef` mirrors `RenderedVisualAsset`/
  `VisualRenderRequirement`'s duality (RENDERED vs. REQUIREMENT status) --
  the timeline layer never triggers rendering; an unrendered requirement
  (standalone Tí, `ASSET_REUSE` `reuse_key`, `EXTERNAL_REQUIRED`) is still
  representable for future editor placement.
- `TimelineTransitionType` (`CUT`/`HOLD`/`CROSSFADE`) is a deterministic,
  documented downgrade of `AssemblyPlan`'s 5-member `TransitionIntent`
  (`CUT`→`CUT`, `NONE`→`HOLD`, `DISSOLVE`→`CROSSFADE`, `MATCH`/`PUSH`→
  `CUT`); the original `TransitionIntent` is preserved in full via
  `source_transition_in`/`out`.
- `TimelineCue`/`TimelineCueType`: music cues (`MUSIC_BED_START`/
  `MUSIC_DUCK`/`MUSIC_LIFT`/`MUSIC_BED_END`) derived from consecutive
  `music_state` changes plus a closing cue; `SFX_TRIGGER` cues from
  `RenderedVoiceTake.sfx_opportunity`. `CUT`/`STATIC_HOLD` cue types exist
  for a future flat-event consumer but are not emitted (already captured
  per-segment).
- Freshness: a six-artifact chain (ScriptPlan → VoicePlan → VisualPlan →
  AssemblyPlan → VoiceRenderManifest → VisualRenderManifest), each
  verified before anything is built. `ModuleRun` RUNNING → SUCCESS/FAILED,
  no project-state transition, no retry loop, no provider/LLM dependency
  (confirmed by a fresh-subprocess import check).
- 69 new focused tests: `tests/test_timeline_models.py`,
  `tests/test_timeline_validation.py`, `tests/test_timeline_builder.py`
  (all new). Every prior test continues to pass unmodified.
- `scripts/evaluate_timeline_builder.py` (new): drives the real
  `VisualRenderer.run()` path (`GENERATED_STILL` + `DIAGRAM` +
  `COMPOSITION`) and the real `TimelineBuilder.run()` path against a
  hand-authored `AssemblyPlan`/`VoiceRenderManifest` with three local WAV
  fixtures -- no live AI/TTS API. Prints a segment table, total duration,
  and cue events; persists the `TimelineManifest`.

## Phase 26 — Deterministic Visual Layer Composition (2026-09-07)

Introduces `VisualLayerCompositor` (`app/layer_compositor/`), a small
deterministic service that combines multiple already-existing rendered
visual assets into one final static PNG frame. Sits ABOVE `GENERATED_
STILL`/`TI_STATE`/`DIAGRAM` without modifying or depending on any of
them -- no new visual content, only structural alpha-composition. AI
providers still create scene/background imagery only; diagrams and
canonical Tí remain exactly as deterministic as before.

- New `app/layer_compositor/`: `LayerSourceType` (`BACKGROUND`/
  `OVERLAY`), `LayerAnchor` (9-position vocabulary), `LayerScale`
  (`relative_height`/`relative_width`, at most one, aspect ratio
  preserved), `BackgroundLayer`/`OverlayLayer` (a `source_type`-
  discriminated union -- `BackgroundLayer` has no anchor/scale/margin/
  offset fields at all), `LayerCompositionSpec` (exactly one background
  required; duplicate/blank layer ids and non-`.png` output rejected),
  `LayerCompositionResult`/`LayerGeometry`. `VisualLayerCompositor.
  compose()` takes no injected dependency -- every layer's `source_path`
  must already be a concrete, existing file; the package never queries a
  database, manifest, or provider.
- Z-order: overlays draw ascending by `z_index`; ties break by authored
  list order (stable sort). Background always draws first (structurally,
  not by convention). Bounds: clamp an in-frame overlay position;
  `LayerCompositionBoundsError` if a scaled overlay cannot fit the canvas
  at all. Canvas dimensions come from the background image; no
  auto-sizing from overlays.
- `app/models/common.py`: `VisualMediaType` gains `COMPOSITION` -- the
  minimum new contract needed to route a beat through this new service
  (a rendering-layer-only concept, not exposed to Visual Planning's LLM
  prompt).
- `app/renderers/visual/models.py`: `CompositionSpec`/
  `BackgroundLayerSource`/`OverlayLayerSource` (renderer-input,
  pre-resolution layer sources -- each carries exactly one of
  `source_path` or `source_beat_id`, mirroring `TiStateSource`'s
  Phase 24 duality). `VisualRendererInput.composition_specs: dict[str,
  CompositionSpec]`, keyed by beat_id, with no default (a `COMPOSITION`
  beat with no entry raises `MissingCompositionSpecError`).
- `app/renderers/visual/renderer.py`: new `_render_composition_beat`
  branch, never calling a provider, never retried. Phase 24's dependency-
  graph machinery (`_build_background_dependency_edges`/
  `_topological_beat_order`) is generalized via a new
  `_iter_beat_to_beat_references` helper to also cover
  `CompositionSpec.layers[*].source_beat_id` -- one dependency engine,
  reused, not duplicated. A composition layer may resolve to a standalone
  `TI_STATE` `CANONICAL_ASSET_READY` requirement (via the injected
  `TiCompositor`'s retriever) -- deliberately broader than `TI_STATE`'s
  own `background_beat_id` rule, which still forbids that. An
  unresolvable/unknown/cyclic reference fails explicitly
  (`CompositionSourceNotRenderedError`, or the shared Phase 24 graph
  errors) -- never a silently dropped layer, never a provider fallback.
- Manifest keeps full traceability: background/diagram/Tí source assets
  remain first-class entries alongside the new composed-frame asset;
  nothing hidden or replaced. `ModuleRun` lifecycle and freshness gates
  unchanged.
- 78 new focused tests: `tests/test_layer_compositor_models.py`,
  `tests/test_layer_compositor.py`, `tests/test_visual_renderer_
  composition_integration.py` (all new). Every prior test continues to
  pass unmodified (`tests/test_visual_models.py`'s exact-`VisualMediaType`
  -set assertion updated for the new `COMPOSITION` member).
- `scripts/evaluate_visual_layer_composition.py` (new): drives the real
  `VisualRenderer.run()` path against a disposable four-beat project
  authored in scrambled order (`V4` `COMPOSITION` first, `V1`
  `GENERATED_STILL` last) to prove dependency ordering is real; combines
  a `GENERATED_STILL` background, a `DIAGRAM` overlay, and a `TI_STATE`
  `STANDALONE` Tí overlay into one final frame with 1 total provider
  call.

## Phase 25 — Deterministic Diagram Renderer (2026-09-07)

Replaces `DIAGRAM`'s provider-based handling with a new, fully local,
deterministic `DiagramRenderer` -- `DIAGRAM` beats no longer depend on AI
image generation at all. Structural rendering, not AI reasoning: a
`DiagramSpec` declares exactly what to draw and where, and
`DiagramRenderer` draws exactly that. Canonical Tí and AI scene/background
generation (Cloudflare/Gemini) remain unchanged and separate.

- New `app/models/diagram.py`: `DiagramCanvas` (explicit pixel
  width/height + `background_color: str | None` -- a hex string means an
  opaque background, `None` means a fully transparent PNG),
  `DiagramPoint` (normalized `[0.0, 1.0]` coordinates, resolution
  independent), a `type`-discriminated `DiagramElement` union over
  exactly 8 element models (`DiagramLine`, `DiagramArrow`,
  `DiagramRectangle`, `DiagramEllipse`, `DiagramPolyline`,
  `DiagramTextLabel`, `DiagramArc`, `DiagramDot`), `DiagramStyle` (stroke
  color/width, fill color, line style, font size, text color, arrowhead
  size), and `DiagramSpec` (one canvas + a non-empty ordered element
  list). `VisualBeat` gains `diagram_spec: DiagramSpec | None = None`.
- New `app/diagram_renderer/` package (mirrors `app/ti_compositor/`'s
  role): `DiagramRenderer.render(spec, output_path) -> DiagramRenderResult`
  draws every element via Pillow's `ImageDraw` and writes a PNG. No
  injected dependency, no configuration -- unlike `TiCompositor` there is
  no external state to inject. Text uses Pillow's own bundled bitmap
  default font (`ImageFont.load_default(size=...)`); no external font
  file is ever downloaded. Deterministic: identical `DiagramSpec` always
  produces byte-identical output.
- `app/renderers/visual/renderer.py`: `DIAGRAM` removed from the
  provider-routed media-type set; `_render_diagram_beat` calls
  `DiagramRenderer` directly, never `VisualProvider.render()`, never
  retried. New upfront `_validate_diagram_specs` (run before any beat
  renders) raises `MissingDiagramSpecError` (`DIAGRAM` beat, no
  `diagram_spec`) or `UnexpectedDiagramSpecError` (non-`DIAGRAM` beat with
  one) -- checked where the field is consumed, not as a new
  `VisualBeat`-level cross-field validator. A `DiagramRenderError`
  propagates unchanged and is recorded as a `FAILED` `ModuleRun` -- no AI
  fallback, no substitute generic still.
- Manifest/`ModuleRun`/freshness behavior unaffected: a `DIAGRAM` asset
  uses the same deterministic path convention as every other rendered
  asset, and is now also a valid Phase 24 `background_beat_id` source
  (it always produces a real `RenderedVisualAsset`).
- 71 new focused tests: `tests/test_diagram_models.py`,
  `tests/test_diagram_renderer.py` (new), `tests/test_visual_renderer_
  diagram_integration.py` (new). `tests/test_visual_renderer.py`,
  `tests/test_visual_renderer_cloudflare_integration.py`, and
  `tests/test_visual_renderer_gemini_integration.py` updated in place
  wherever they asserted `DIAGRAM`'s now-retired provider-routed
  behavior; every other prior test continues to pass unmodified.
- `scripts/evaluate_diagram_renderer.py` (new): renders 3 hand-authored
  diagrams (`FORCE_BLOCK`, `SUN_STICK_SHADOW`, `CIRCLE_ANGLE_RELATION`)
  directly through `DiagramRenderer`, covering every element type; no
  live image API, no AI generation.

## Phase 24 — Beat-to-Beat Visual Composition (2026-09-07)

Lets a `TI_STATE` `COMPOSITE` beat use the rendered output of another
`VisualBeat` in the same `VisualPlan` as its background, removing the
requirement that a composite background already exist as a file the
caller manually supplies. Explicit dependency resolution computed from
the plan's own beats, not AI reasoning: `VisualPlan` beat outputs may now
form deterministic visual dependencies, and authored list order is never
relied upon when a dependency requires reordering. AI providers remain
scene/background providers only; canonical Tí remains deterministic;
`TiCompositor` is unchanged.

- `app/renderers/visual/models.py`: `TiStateSource` gains
  `background_beat_id: str | None = None`, mutually exclusive with
  `background_path`. Tightened validation: `STANDALONE` requires both
  fields `None`; `COMPOSITE` requires exactly one of the two -- both set,
  or neither set, is now a `pydantic.ValidationError` at construction
  time (previously, `COMPOSITE` with no `background_path` only failed
  later, at render time, via `TiStateMissingBackgroundError`, now
  removed as unreachable).
- `app/renderers/visual/renderer.py` adds two pure module-level helpers,
  called once per render pass before any beat renders:
  `_build_background_dependency_edges` turns every `background_beat_id`
  into a dependency edge (background beat -> dependent beat), failing on
  an unknown beat_id or self-reference; `_topological_beat_order` (Kahn's
  algorithm, tiebreaking toward the smallest authored index) produces the
  actual rendering order -- a dependency always precedes its dependent, a
  cycle fails before any beat renders, and the authored `VisualPlan.beats`
  order is preserved exactly wherever no dependency forces a beat later.
- `_render_ti_state_beat`/new `_resolve_composite_background_path`: when
  `background_beat_id` is set, the background path is looked up from that
  same render pass's own `rendered_assets_by_beat_id` results (never
  re-rendered, never regenerated, never copied) and resolved to
  `VisualFileStore.root / file_path`, then handed to `TiCompositor`
  exactly as an explicit `background_path` always was. A referenced beat
  that resolved to a requirement instead of an asset raises
  `BackgroundBeatNotRenderedError`; a resolved asset whose file is missing
  on disk raises `BackgroundBeatAssetMissingError`. Neither ever falls
  back to `STANDALONE` or calls a `VisualProvider`.
- New errors (`app/renderers/visual/errors.py`):
  `UnknownBackgroundBeatError`, `SelfReferentialBackgroundBeatError`,
  `BackgroundBeatCycleError`, `BackgroundBeatNotRenderedError`,
  `BackgroundBeatAssetMissingError`. Removed: `TiStateMissingBackgroundError`
  (superseded by `TiStateSource`'s own construction-time validation).
- Zero extra provider calls: a `GENERATED_STILL`/`DIAGRAM` background beat
  still makes exactly one `VisualProvider` call; the dependent `TI_STATE`
  `COMPOSITE` beat consuming its output makes zero. `TI_STATE` still has
  no AI fallback in either background-source mode.
- Manifest unchanged in shape: both the background beat's own
  `RenderedVisualAsset` and the dependent `TI_STATE` beat's composited
  `RenderedVisualAsset` remain first-class `manifest.assets` entries, at
  their existing deterministic paths -- nothing replaced or hidden.
- `ModuleRun` RUNNING -> SUCCESS/FAILED lifecycle, `ScriptPlan`/
  `VoicePlan`/`VisualPlan` freshness gates, manifest persistence, and
  provider retry ownership are all unchanged; a dependency-graph error is
  recorded as a `FAILED` `ModuleRun` through the same broad
  `except Exception` path every other renderer failure already uses.
- 28 new focused tests in `tests/test_visual_renderer_beat_composition.py`
  (new): contract validation, pure dependency-graph/topological-order unit
  tests (including authored-order preservation, a minimal-reorder
  stability case, self-reference/unknown-beat/cycle failures), full
  `VisualRenderer.run()` resolution tests, compatibility checks
  (`background_path` mode/`STANDALONE`/`ASSET_REUSE`/non-`TI_STATE`
  routing all unchanged), provider-call-count and manifest assertions,
  and `ModuleRun` lifecycle preservation. Two Phase 23 tests were updated
  in place to assert the new construction-time `ValidationError` instead
  of the retired `TiStateMissingBackgroundError`; every other prior test
  continues to pass unmodified.
- `scripts/evaluate_visual_renderer_beat_composition.py` (new): drives
  the real `VisualRenderer.run()` path against a disposable project with
  `V2` (`TI_STATE` `COMPOSITE`, `background_beat_id="V1"`) authored
  *before* `V1` (`GENERATED_STILL`) in the `VisualPlan`, to prove
  dependency ordering is real and not an accident of list order. Uses the
  real active `v1` `TiAssetSet`; no live provider call.

## Phase 23 — VisualRenderer ↔ Tí Compositor Integration (2026-09-06)

Replaces `TI_STATE`'s placeholder-only handling (a
`"ti_state:<VisualTiState value>"` reference string that resolved to
nothing real) with Phase 21/22's deterministic canonical resolution and
compositing, finally wired into `VisualRenderer`. No LLM placement, no
diagrams, no video compositing, no change to `CloudflareImageProvider`/
`GeminiImageProvider` (both files' full pre-existing test suites still
pass unmodified).

- `VisualRenderer.__init__` gains one new optional dependency,
  `ti_compositor: TiCompositor | None = None` -- the only new dependency,
  and `VisualRenderer` never instantiates its own
  `SqliteTiAssetRetriever`/`TiAssetFileStore`/database engine for Tí
  assets; every canonical-asset operation goes through the injected
  `TiCompositor`.
- Added `TiCompositor.retriever` (`app/ti_compositor/compositor.py`): a
  new read-only property exposing the underlying `TiAssetRetriever`, so
  standalone-mode metadata lookups don't need a second injected
  dependency. `TiCompositor.composite()` itself is unchanged.
- Added `app/renderers/visual/models.py`: `TiStateRenderMode`
  (`STANDALONE`/`COMPOSITE`), `TiStateSource` (`mode`, `background_path`,
  `placement`), and `VisualRendererInput.ti_state_sources: dict[str,
  TiStateSource]` -- the minimum typed contract for a caller to make a
  `TI_STATE` beat's mode explicit per beat_id. No entry defaults to
  `STANDALONE` (never invents a background).
- `VisualRenderer._render_ti_state_beat` is `TI_STATE`'s new dedicated
  routing branch (previously routed through the same
  `_reuse_only_requirement` helper as `ASSET_REUSE`):
  - **STANDALONE**: `to_ti_state()` (Phase 21.1, unchanged) resolves
    `beat.ti_state`, then `TiCompositor.retriever.get_asset()` resolves
    the canonical `TiAsset`. Recorded as a `VisualRenderRequirement` with
    a new status, `VisualRequirementStatus.CANONICAL_ASSET_READY`;
    `reference` is the asset's own `relative_path` (self-encoding
    asset-set id/version/state); no compositing, no file written.
  - **COMPOSITE** (only reachable with an explicit `background_path`):
    `TiCompositor.composite()` renders Tí onto that background; the
    result becomes a real `RenderedVisualAsset` (always `.png`,
    regardless of `VisualSettings.output_format`). Missing
    `background_path` in this mode raises `TiStateMissingBackgroundError`
    before any compositing is attempted.
  - A `TI_STATE` beat with `ti_compositor=None` raises
    `TiCompositorNotConfiguredError` immediately -- no placeholder
    fallback, no provider fallback.
- **Removed**: `TI_STATE` no longer honors an authored `reuse_key` as an
  override (the pre-canonical-asset "no real asset exists yet" escape
  hatch) -- a real canonical asset now always exists for every `TiState`.
  `ASSET_REUSE` is completely unaffected and keeps its own `reuse_key ->
  REUSE_ONLY` behavior.
- `app/models/visual_render.py`: two small, optional, backward-compatible
  additions -- `RenderedVisualAsset.resolved_ti_state`/`.notes` and
  `VisualRenderRequirement.resolved_ti_state` (all `None` outside
  `TI_STATE`, validated as such). `app/models/common.py`:
  `VisualRequirementStatus` gains `CANONICAL_ASSET_READY`.
  `VisualRenderManifest`'s own shape is untouched.
  `app/renderers/visual/validation.py` updated in lockstep:
  `_REUSE_ONLY_MEDIA_TYPES` now only `ASSET_REUSE`;
  `_CANONICAL_ASSET_READY_MEDIA_TYPES` gates the new status;
  `_ASSET_ALLOWED_MEDIA_TYPES` allows a `TI_STATE` asset (COMPOSITE mode)
  without requiring it be provider-renderable. `VisualRendererResult`
  gains `canonical_asset_ready_count`.
- `ModuleRun` lifecycle, `StaleVoicePlanError`/`StaleVisualPlanError`
  freshness gates, manifest persistence/rerender-upsert semantics, and the
  `{project_id}/{visual_plan_id}/{render_job_id}` deterministic path
  convention are all unchanged. `TiCompositor` is never retried (it is a
  deterministic local operation, not a provider call) -- a `TI_STATE`
  beat, in either mode, always contributes `0` to `provider_call_count`.
- Added `scripts/evaluate_visual_renderer_ti_state_integration.py`: drives
  the real `VisualRenderer.run()` path (not `TiCompositor` in isolation)
  twice against one disposable evaluation project with one `TI_STATE`
  beat -- once `STANDALONE`, once `COMPOSITE` onto the existing
  `data/cloudflare_image_evaluation/L1_ESTABLISH.jpg` (no new background
  generated) -- using the real active `v1` `TiAssetSet`. `FakeVisualProvider([])`
  proves neither mode ever calls a provider. Output lands in
  `data/visual_renderer_ti_state_evaluation/`.
- 154 new/updated tests: `tests/test_visual_renderer_ti_state_integration.py`
  (new, the dedicated Phase 23 focused suite -- standalone resolution,
  `VisualTiState` mapping use, zero provider calls, composite invoking
  `TiCompositor` and registering a real asset, missing-background/
  missing-active-set/missing-canonical-file/not-configured failures,
  no-AI-fallback, deterministic repeated output, `ModuleRun`/freshness
  preserved) plus updates to `tests/test_visual_renderer.py` (TI_STATE
  beats now need a configured `TiCompositor`; the old placeholder-
  reference and reuse_key-override tests replaced with their Phase 23
  equivalents; all non-`TI_STATE` routing tests otherwise unmodified),
  `tests/test_visual_render_models.py`, and
  `tests/test_visual_render_validation.py`.

## Phase 22 — Deterministic Tí Compositor (2026-09-06)

Adds the first thing that actually combines a canonical `TiAsset` with an
existing background image, as a new, separate `app/ti_compositor/`
package -- still not wired into `VisualRenderer`, `app/visual/router.py`,
or either real `VisualProvider`. No AI generation of Tí, no diagrams, no
LLM-based or computer-vision/saliency-based placement.

- Added `app/ti_compositor/models.py`: `TiAnchor` (6-member fixed
  placement vocabulary: `BOTTOM_LEFT`/`BOTTOM_CENTER`/`BOTTOM_RIGHT`/
  `CENTER_LEFT`/`CENTER`/`CENTER_RIGHT`), `TiScalePolicy`
  (`relative_height`, validated `0 < value <= 1`, aspect ratio always
  preserved), `TiPlacement` (anchor + scale + `margin`/`offset_x`/
  `offset_y`), `TiCompositeRequest` (background/output paths + placement +
  exactly one of `visual_ti_state`/`ti_state`, enforced at construction
  time), `TiCompositeResult` (resolved state, final pixel geometry,
  `clamped` flag).
- Added `app/ti_compositor/errors.py`: `TiCompositorError`,
  `TiCompositeBackgroundError`, `TiCompositeAssetError`,
  `TiCompositeBoundsError`, `TiCompositeWriteError` -- a separate
  hierarchy from `TiAssetError`/`VisualError`; `TiAssetRetriever`'s own
  errors (missing active set, missing state asset) propagate unchanged.
- Added `app/ti_compositor/compositor.py`: `TiCompositor(retriever:
  TiAssetRetriever)`, depending only on `TiAssetRetriever` -- never on a
  concrete `VisualProvider`. `visual_ti_state` resolves through Phase
  21.1's existing `to_ti_state()` unchanged before
  `TiAssetRetriever.get_asset()` is ever called.
- **Out-of-bounds policy, chosen and documented (clamp-first)**: a
  rendered Tí width wider than the background raises
  `TiCompositeBoundsError` (no position could ever fit it); otherwise the
  requested position is clamped into `[0, bg_width - ti_width] x [0,
  bg_height - ti_height]` so Tí always lands fully in-frame --
  `TiCompositeResult.clamped` reports whether clamping actually moved it.
  Never silently crops.
- **Alpha compositing**: `Image.paste(ti, (x, y), ti)` uses the canonical
  PNG's own alpha channel as the paste mask against an always-opaque
  background canvas (any background-PNG alpha is intentionally flattened
  first) -- no white/black backing is ever baked in around Tí.
- Output is PNG-only, enforced by `TiCompositeRequest` (not merely a
  default) for byte-identical determinism across repeated runs; PNG and
  JPG/JPEG backgrounds are both supported as input.
- **Pillow added as a real dependency** (`pyproject.toml`,
  `Pillow>=11.0,<13.0`) -- a deliberate reversal of Phase 21.1's "Pillow
  evaluated and not added" call, justified because this phase actually
  decodes/resizes/alpha-composites pixel data, which Phase 21.1's
  header-only PNG parsing never needed. `app/ti_assets/png_metadata.py`,
  `app/visual/providers/cloudflare.py`, and
  `app/visual/providers/gemini.py` remain untouched and Pillow-free.
- Added `scripts/evaluate_ti_compositor.py`: composites `NEUTRAL`
  (`BOTTOM_LEFT`), `CURIOUS` (`BOTTOM_RIGHT`), `PANIC` (`CENTER_RIGHT`)
  from the real active `v1` `TiAssetSet` onto the existing
  `data/cloudflare_image_evaluation/L1_ESTABLISH.jpg` background (no new
  background generated, per this phase's instruction) into
  `data/ti_compositor_evaluation/`. Manual viewing aid only -- no
  automated scoring.
- 39 new tests (pure anchor/scale/clamp-position math for every anchor,
  canonical-asset retrieval and `VisualTiState -> TiState` resolution
  through a real `SqliteTiAssetRetriever`, alpha-compositing correctness
  against synthetic transparent-bordered fixtures, PNG and JPG
  backgrounds, output-dimensions-equal-background-dimensions, missing-
  active-set and missing-state-file failures, both out-of-bounds
  behaviors, byte-identical determinism across repeated runs, and no
  provider/LLM import or network-client construction); all prior tests
  continue to pass unmodified.
- Not wired into `VisualRenderer`, `app/visual/router.py`, or either real
  `VisualProvider` -- `VisualMediaType.TI_STATE` beats still resolve to
  the same `"ti_state:<VisualTiState value>"` placeholder reference as
  before this phase. That integration is left to a future phase.

## Phase 21.1 — Tí State Mapping + Canonical Asset Ingestion (2026-09-06)

Closes the two concrete blockers Phase 21 left open before scene
compositing. Still contracts/storage/retrieval only: no compositing, no
image generation for Tí, no change to `CloudflareImageProvider`/
`GeminiImageProvider`, no diagrams, no LLM calls.

### Part A — `VisualTiState -> TiState` mapping

- Added `app/ti_assets/visual_state_mapping.py`:
  `VISUAL_TI_STATE_TO_TI_STATE` (a `MappingProxyType`, read-only) and
  `to_ti_state(visual_state)`. `VisualTiState` and `TiState` stay two
  separate enums, per this phase's explicit instruction -- they are
  related but not the same domain concept (narrative/visual-planning
  mood vs. canonical brand-asset identity). The mapping is the one
  explicit bridge between them:
  - Like-for-like: `NEUTRAL`, `CURIOUS`, `SKEPTICAL`, `EXCITED`,
    `DEADPAN`, `PANIC`.
  - Canonical decisions for the three `VisualTiState` values with no
    direct `TiState` counterpart: `CONFUSED -> CURIOUS`,
    `SURPRISED -> EXCITED`, `SMUG -> SKEPTICAL`.
  - No fuzzy matching, no string-name coercion, and no silent default to
    `NEUTRAL` -- `to_ti_state` raises `ValueError` for anything not in
    the table. A module-level completeness check raises `RuntimeError`
    at import time if any `VisualTiState` member is ever left unmapped
    -- the same "structural guarantee, not a runtime hope" pattern
    `TiAssetSet`'s own completeness validator uses.
  - `TiState.SERIOUS`/`TiState.LOW_ENERGY` are documented as having no
    `VisualTiState` source -- canonical assets a future compositor/editor
    may select directly, never produced by this mapping.
- 24 new tests in `tests/test_ti_asset_visual_state_mapping.py`: every
  `VisualTiState` member maps exactly once, the required table matches
  exactly, repeated calls are deterministic, neither enum's member list
  is mutated by any call, the mapping object itself is read-only
  (`TypeError` on item assignment), `SERIOUS`/`LOW_ENERGY` never appear
  as a mapping target, and an unmapped input raises rather than
  defaulting.

### Part B — canonical asset ingestion

- **Dependency decision, reported before use**: Pillow is not currently
  installed in this environment or declared in `pyproject.toml`. It was
  evaluated and **not added** -- this phase's validation surface (valid
  PNG, width/height, alpha/tRNS-transparency presence) lives entirely in
  a PNG file's signature/IHDR/tRNS chunks, none of which require pixel
  decoding, so Python's stdlib `struct` module is sufficient. The
  smallest-dependency option (zero new dependencies) was chosen,
  consistent with `CloudflareImageProvider`'s earlier "reuse `httpx`,
  don't add a vendor SDK" precedent.
- Added `app/ti_assets/png_metadata.py`: `read_png_metadata(data) ->
  PngMetadata` (`width`, `height`, `color_type`, `bit_depth`,
  `has_alpha`). Parses only chunk headers, `IHDR`, and the presence of a
  `tRNS` chunk -- never decodes `IDAT` pixel data. `has_alpha` is `True`
  for color types 4/6 (grayscale+alpha, truecolor+alpha) unconditionally,
  or for color types 0/2/3 (grayscale, truecolor, palette) only if a
  `tRNS` chunk is present. Raises `TiAssetInvalidImageError` (new in
  `app/ti_assets/errors.py`) for a bad signature, a truncated/malformed
  chunk, a missing `IHDR`, or non-positive dimensions.
- Added `app/ti_assets/ingest.py`: `ingest_ti_asset_set(engine,
  file_store, source_dir, version, notes=None, activate=False)`.
  Enforces MVP policy exactly: PNG only, transparent background
  required, all 8 exact `STATE.png` filenames required (`NEUTRAL.png`
  through `LOW_ENERGY.png`), no state inference from any other filename,
  no AI generation, no automatic repair, no silent missing-file
  fallback.
  - Order of operations: (1) reject a `version` already present in
    storage as a conflict (checked via `list_ti_asset_sets`, before
    touching the source directory); (2) validate every one of the 8
    required files -- existence, valid PNG, alpha/tRNS transparency --
    collecting and reporting *every* problem found, not just the first,
    via `TiAssetIngestionError` (new in `app/ti_assets/errors.py`); (3)
    only once validation fully passes, copy each file through
    `TiAssetFileStore.write` under a freshly-minted `asset_set_id`, using
    the existing `build_relative_path` convention; (4) construct and
    validate `TiAsset`/`TiAssetSet` (re-checks completeness/no-
    duplicates structurally, cheap insurance); (5) persist via the
    existing `save_ti_asset_set`, which already atomically flips
    `is_active` when `activate=True`.
  - **Atomicity**: no database row is ever written for a failed
    ingestion -- a validation failure never reaches the filesystem or
    database at all, and a mid-copy filesystem failure (step 3) means
    `save_ti_asset_set` (step 5) is never called, so an incomplete set
    can never be activated or even stored as inactive.
  - **Orphan files, documented honestly**: if a copy fails partway
    through step 3, files already copied before the failure are **not**
    cleaned up -- Phase 21.1's own "no automatic repair" policy applies
    to its own failure path too. This is safe (a fresh `uuid4()` per
    call means a retry's paths can never collide with the orphans) but
    not tidy; a human must remove them manually if disk space matters.
- Added `scripts/ingest_ti_assets.py` -- the human-facing CLI:
  `python scripts/ingest_ti_assets.py --source <dir> --version v1
  [--notes "..."] [--activate] [--db <path>] [--asset-root <path>]`.
  Prints a clear one-line failure to stderr and exits 1 on any
  `TiAssetError`; never fabricates a placeholder Tí file. Until a human
  supplies all 8 real PNGs, every invocation fails by design.
- 42 new tests: `tests/test_ti_asset_png_metadata.py` (alpha-via-color-
  type, alpha-via-tRNS, no-alpha, garbage/truncated/missing-IHDR/zero-
  dimension rejection), `tests/test_ti_asset_ingest.py` (successful 8-
  file ingestion, missing file, wrong filename treated as missing,
  invalid PNG, PNG without alpha rejected, PNG with tRNS accepted, all
  problems reported together, missing source directory, duplicate-
  version conflict, `activate=False` leaves the current active set
  unchanged, `activate=True` makes the new set the sole active set, DB
  contains metadata/path only and payload stays far smaller than real
  image bytes would be, no provider/LLM import in the ingestion module),
  and `tests/test_ingest_ti_assets_cli.py` (success with `--activate`,
  clean failure with a missing-files source directory).

No fabricated Tí artwork was created or committed by this phase -- the
CLI and its tests use synthetic, structurally-valid-but-visually-
meaningless PNGs built by a stdlib-only test helper
(`tests/_png_helpers.py`), never real character art.

**Phase 21.1 is now COMPLETE**: focused tests (46) and the full suite
both pass with zero regressions (see test-run records for exact counts).

### Remaining before Phase 22

A human must still supply the first real, artist-produced set of 8 Tí
PNGs and run `scripts/ingest_ti_assets.py --activate` against them --
nothing in this repository can do that step. Wiring `TiAssetRetriever`
into `VisualRenderer`/a future compositor (resolving a `TI_STATE` beat's
`"ti_state:<VisualTiState value>"` reference through `to_ti_state()` and
`SqliteTiAssetRetriever.get_asset()`) remains explicitly out of scope,
per this phase's own instructions, and is left to a future phase.

## Phase 21 — Canonical Tí Asset System (2026-09-06)

Implements the "Tí → a separate canonical reusable deterministic asset
mechanism" line from Phase 20.2's decision: **contracts, storage,
validation, and retrieval only**. No image generation for Tí, no scene
compositing, and no change to `VisualRenderer`, `app/visual/router.py`,
`CloudflareImageProvider`, or `GeminiImageProvider`.

- Added `app/models/ti_assets.py`:
  - `TiAssetType(str, Enum)` — `STILL` only. A pose/presentation-variant
    type was deliberately not added: nothing in the existing visual
    grammar (`VisualBeat`/`VisualTiState`/`VisualMediaType.TI_STATE`)
    distinguishes a pose from a state today.
  - `TiState(str, Enum)` — `NEUTRAL`/`CURIOUS`/`SKEPTICAL`/`EXCITED`/
    `SERIOUS`/`DEADPAN`/`PANIC`/`LOW_ENERGY`, deliberately identical to
    `VoiceState`'s 8-member vocabulary (per this phase's instruction to
    align with the voice-state system), **not** to `VisualTiState`'s
    9-member vocabulary (`CONFUSED`/`SURPRISED`/`SMUG` instead of
    `SERIOUS`/`LOW_ENERGY`). This mismatch is recorded as an open
    decision, not resolved here -- see "Open decisions" below.
  - `TiAsset` — one canonical file per state: `asset_set_id`, `state`,
    `asset_type`, `relative_path`, `output_format`
    (reuses `VisualOutputFormat`, not a new enum), `mime_type`,
    `width`/`height`, `transparent_background`, `notes`. Never an image-
    bytes field. Its own `@model_validator` enforces: positive
    dimensions, `mime_type` matching `output_format`
    (`image/png`/`image/jpeg`), `relative_path`'s extension matching
    `output_format`, and `transparent_background` only ever paired with
    `PNG` (JPG has no alpha channel).
  - `TiAssetSet` — a versioned, `is_active`-flagged collection of
    `TiAsset`s. Its `@model_validator` enforces, at construction time
    (no I/O, no separate call needed): every asset's `asset_set_id`
    matches the set's own id, no two assets share a `TiState`, and all 8
    `TiState` members are covered (`REQUIRED_TI_STATES`) -- an incomplete
    or conflicting set cannot be built in memory, let alone stored or
    activated.
- Added `app/ti_assets/` (new top-level package, sibling to
  `app/audio/`/`app/visual/`):
  - `storage.py` — `TiAssetFileStore` (atomic `write`, plus `exists`/
    `resolve` for retrieval-time checks -- pure render-output stores like
    `VisualFileStore` don't need a read side, this one does) and
    `build_relative_path(asset_set_id, version, state, output_format)`,
    the one place the `{asset_set_id}/{version}/{state}.{ext}` path
    convention is implemented. Path-safety validation (reject blank/
    absolute/`..`-containing paths) copied from `VisualFileStore`'s
    proven logic.
  - `lookup.py` — `index_assets_by_state`/`get_asset_by_state`: exact
    `TiState -> TiAsset` matching only. No fuzzy matching, no LLM-based
    selection, never returns `None` -- a missing state raises
    `TiAssetStateNotFoundError`. Kept independent of `TiAssetSet`'s own
    completeness guarantee so "missing state" stays a directly testable
    code path rather than a provably-unreachable one.
  - `validation.py` — `validate_files_exist(asset_set, file_store)`, the
    one validation rule that genuinely needs disk access (duplicate-state
    and completeness are enforced inside the Pydantic model itself, with
    no I/O required).
  - `errors.py` — `TiAssetError` hierarchy (`TiAssetStateNotFoundError`,
    `TiAssetMissingFileError`) and a separate `TiAssetStorageError`
    hierarchy (`TiAssetPathError`, `TiAssetWriteError`), mirroring
    `app/visual/errors.py`'s two-hierarchy split.
  - `retriever.py` — **the future integration boundary**: a
    `TiAssetRetriever` `Protocol` (`get_active_asset_set`/`get_asset`/
    `list_available_states`/`resolve_path`), playing the same role for a
    future compositor that `VisualProvider` plays for `VisualRenderer`.
    `SqliteTiAssetRetriever` is its one concrete implementation this
    phase ships, wrapping a SQLAlchemy `Engine` and a `TiAssetFileStore`.
    `get_asset(state)` re-checks the file exists on disk before
    returning (catches metadata/filesystem drift, never hands back a
    reference to nothing).
- Added `TiAssetSetRow` to `app/storage/orm.py` — composite primary key
  `(asset_set_id, version)`, storing a full validated `TiAssetSet` as one
  JSON payload (never image bytes) plus a dedicated `is_active` column.
- Added `app/storage/ti_assets.py` — `save_ti_asset_set`/
  `get_ti_asset_set`/`get_active_ti_asset_set`/`list_ti_asset_sets`/
  `activate_ti_asset_set`. Activating one `(asset_set_id, version)`
  atomically clears `is_active` on every other row first, in the same
  transaction -- the one place the "at most one active canonical set,
  system-wide" invariant is enforced. `is_active` is read from the
  dedicated column, not from the embedded JSON, specifically so that
  atomic bulk-deactivate never needs to rewrite every other row's
  payload (a real bug caught and fixed during this phase's own testing --
  see "Bug found and fixed" below).
- Added `TiAssetSetNotFoundError`/`TiAssetSetValidationError`/
  `MultipleActiveTiAssetSetsError` to `app/storage/errors.py`, alongside
  the existing `ArtifactNotFoundError`/`ArtifactValidationError`.
- **Bug found and fixed during this phase's own test-writing**: the
  first version of `_to_domain` in `app/storage/ti_assets.py`
  reconstructed a `TiAssetSet` purely from `row.payload_json`, so
  `activate_ti_asset_set`'s bulk `UPDATE ... SET is_active = False`
  (which only touches the column, not the JSON blob written earlier)
  left every deactivated row's *returned* `is_active` still `True`. Fixed
  by making `row.is_active` authoritative: `_to_domain` now applies
  `asset_set.model_copy(update={"is_active": row.is_active})` after
  parsing the JSON. Caught by
  `test_activating_one_version_deactivates_all_others` before this ever
  reached documentation as "done."
- **Not wired anywhere**: `VisualRenderer`, `app/visual/router.py`, and
  both real `VisualProvider`s are completely untouched -- confirmed by
  running their full existing test suites unmodified.
  `VisualMediaType.TI_STATE` beats still resolve to a
  `"ti_state:<VisualTiState value>"` placeholder reference exactly as
  before this phase (`app/renderers/visual/renderer.py`'s
  `_reuse_only_requirement`); a future phase would resolve that
  reference through `TiAssetRetriever`, not this one.
- 91 new tests across `tests/test_ti_asset_models.py`,
  `tests/test_ti_asset_lookup.py`, `tests/test_ti_asset_storage.py`,
  `tests/test_ti_asset_repository.py`, `tests/test_ti_asset_retriever.py`,
  and `tests/test_ti_asset_validation.py` -- model validation (dimensions,
  mime/format/extension consistency, transparent-background+JPG
  rejection, duplicate-state and incompleteness rejection, no-bytes-field
  structural check), deterministic lookup and missing-state failure,
  filesystem path safety/atomic-write/exists/resolve, repository round-
  trip/versioning/single-active-set invariant/corrupt-payload handling,
  and end-to-end retriever behavior including missing-file and
  no-active-set failure; all prior tests continue to pass unmodified.

### Open decisions (recorded, not resolved, this phase)

- **`TiState` vs. `VisualTiState`**: two different 8-/9-member visual
  expression vocabularies now exist side by side. Reconciling them (or
  deciding they should permanently stay separate -- "canonical asset
  identity" vs. "prompt-generation mood") is a human decision, not made
  in Phase 21.
- **Multiple lineages**: the storage schema technically supports more
  than one `asset_set_id` lineage, but Phase 21 only ever expects one (Tí
  is a single character). Whether a second lineage will ever be needed
  (e.g. a second recurring character) is unresolved and untested.
- **Populating the first real canonical asset set** (the actual Tí image
  files, produced by a human artist/designer) is not part of this phase
  and has no owner or timeline yet.

## Phase 20.2 human evaluation and role decision (2026-09-06)

`scripts/evaluate_cloudflare_image.py` was run once (real Cloudflare
credentials, exactly the 3 pre-existing briefs — `L1_ESTABLISH`,
`L2_MECHANISM`, `TI_PRESENT` — to `data/cloudflare_image_evaluation/`;
no prompt tuning, no model change, no extra variants). Human review
outcome per brief:

- `L1_ESTABLISH`: acceptable for production background use.
- `L2_MECHANISM`: visually usable but too cinematic/random for
  deterministic mechanism explanation.
- `TI_PRESENT`: rejected for production mascot use — identity/style
  drift.

**Decision: `CloudflareImageProvider` (FLUX.1 Schnell) is kept, but is
not the universal visual provider — its production role is limited to
background generation, establishing scenes, environmental
illustration, and generic scene/object stills where exact character
identity is not required.** It is explicitly not approved for
canonical Tí mascot generation, character consistency, brand-defining
character art, diagrams, or exact historical/mechanical relationships
requiring deterministic precision.

Future intended visual strategy (direction only, nothing implemented
this entry): AI provider → backgrounds/environments/generic scene
imagery; Tí → a separate canonical reusable deterministic asset
mechanism; diagrams → a separate deterministic/semi-deterministic
system; text/labels → an editor/compositor layer.

No code was changed — `CloudflareImageProvider`, its prompts, and its
model are exactly as Phase 20.2 shipped them. See
`docs/TECHNICAL_SPEC_v0.1.md`'s "Phase 20.2 human evaluation and role
decision" section for the full record. **Phase 20.2 is now COMPLETE.**

## Phase 20.2 — Cloudflare Workers AI Image Provider (2026-09-06)

Gemini Image (Phase 20/20.1) is architecturally correct -- the live
evaluation run in Phase 20.1 proved the corrected `generate_content`
path is accepted by the Gemini Developer API -- but this project's
Google account currently has zero free-tier quota for
`gemini-2.5-flash-image` (a `429 RESOURCE_EXHAUSTED` billing/quota
error, not a code defect; see Phase 20.1's live-evaluation record).
Phase 20.2 adds a **second** real `VisualProvider`, Cloudflare Workers
AI, purely so real visual evaluation can proceed on a free-tier-friendly
path. This is additive only: `GeminiImageProvider` is untouched, still
implemented, still correct, just currently billing-blocked for this
account.

- Added `app/visual/providers/cloudflare.py` (new file alongside
  `gemini.py` in the same subpackage) -- `CloudflareImageProvider`, the
  second real `VisualProvider`:
  - `CloudflareImageConfig` (`model` default
    `"@cf/black-forest-labs/flux-1-schnell"`, `account_id_env` default
    `"CLOUDFLARE_ACCOUNT_ID"`, `api_token_env` default
    `"CLOUDFLARE_API_TOKEN"`, `timeout_seconds` default `60.0`, `steps`
    default `4`, validated `1-8` per this model's documented range) --
    no secret value fields.
  - Uses the existing `httpx` dependency directly (already present
    transitively via `google-genai`, already imported for its error
    types by both Gemini adapters) -- no new SDK dependency; Cloudflare
    Workers AI is a plain REST API. `httpx` is now also declared as a
    direct `pyproject.toml` dependency, since this module performs real
    HTTP calls with it (not just `isinstance` checks on its error
    types).
  - **Endpoint**: constructed exactly as
    `https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}`
    -- the model string's `@`/`/` characters are embedded literally, per
    Cloudflare's documented URL form (no percent-encoding).
  - **Authentication**: reads `CLOUDFLARE_ACCOUNT_ID`/
    `CLOUDFLARE_API_TOKEN` from the environment eagerly at construction
    time (missing either raises `CloudflareImageConfigurationError`
    before any HTTP call); an injected `client` may be paired with
    explicit `account_id`/`api_token` constructor arguments so tests
    need zero real environment credentials and zero network, mirroring
    Gemini's injected-client precedent. The token is used only to build
    the `Authorization: Bearer` header -- never logged, never included
    in any error message.
  - **GENERATED_STILL + JPG only**: any other `VisualMediaType`
    (`DIAGRAM` included) or `VisualOutputFormat` (`PNG`) is rejected
    before any HTTP call, mirroring Gemini's policy exactly. JPG (not
    PNG) because FLUX.1 Schnell's official examples treat its base64
    output as JPEG -- no format was invented or assumed beyond that.
    `VisualOutputFormat`/`VisualRenderer` were **not** changed to
    accommodate this: `VisualSettings(output_format=VisualOutputFormat.JPG)`
    is simply the settings a caller configures when wiring this
    provider, per the existing, unchanged contract.
  - **Prompt translation**: an independently-defined
    `_build_prompt`/`_STYLE_GUIDANCE`/`_TI_STATE_DIRECTIONS` in this
    module -- deliberately **not** imported or shared with
    `gemini.py`'s equivalents, even though both encode the same locked
    visual language (painterly editorial 2D, semi-real simplified
    anatomy, flat 2-3 tone rendering, warm-light/cool-shadow,
    background-only haze, no embedded text). `concept`/`primary_focus`/
    `secondary_elements`/`context_elements` copied verbatim; a
    per-`VisualTiState` mood/expression note represents Tí when present,
    paired with the same "Tí may only observe or react, never cause or
    alter an event" causality line. **FLUX prompt-only generation does
    not solve canonical Tí consistency any more than Gemini's does** --
    explicitly documented, not glossed over.
  - **Response envelope handling** (`_extract_image_bytes`): validates
    the response is JSON, `success` is `true`, `result` exists,
    `result.image` is non-blank, and the base64 decodes to non-empty
    bytes -- each failure raises `VisualOutputError` explicitly, never a
    raw `KeyError`/`TypeError`/`binascii.Error`. An explicit
    `success: false` envelope (a request-level Cloudflare API failure,
    reported inside a 200 OK body rather than an HTTP status) is treated
    as `VisualProviderError` (retryable) -- the same category as an
    HTTP 4xx/5xx from the API, distinct from a malformed/empty output
    shape.
  - **HTTP/network error translation**: `httpx.HTTPError` (covering both
    transport failures like `ConnectError`/`TimeoutException`) and an
    explicit non-2xx status code both translate to `VisualProviderError`;
    any other exception (a genuine bug) stays visible -- narrow,
    documented boundary, mirroring the Gemini adapters exactly.
  - `width`/`height` always `None` (Cloudflare's envelope carries no
    dimension metadata) -- no Pillow added. `provider_request_id` reads
    the `cf-ray` response header when present (Cloudflare's own standard
    per-request ray id), `None` otherwise, never invented.
  - **No nested retry**: the adapter retries nothing itself --
    `VisualRenderer` already owns the `VisualProviderError` retry
    policy.
  - Canonical provider name: exactly `"cloudflare-workers-ai"`.
- Added `tests/test_cloudflare_image_live_smoke.py` -- one opt-in live
  test, gated by both a registered `live_cloudflare_image` pytest marker
  and `RUN_LIVE_CLOUDFLARE_IMAGE=1`; skips cleanly with zero network/
  credential requirement otherwise. Registered the
  `live_cloudflare_image` marker in `pyproject.toml`.
- Added `scripts/evaluate_cloudflare_image.py` -- reuses the exact same
  three conceptual briefs as `scripts/evaluate_gemini_image.py` (L1
  establishing, L2 mechanism/action, Tí-present), deliberately
  unmodified to favor either provider; writes one JPG per brief to
  `data/cloudflare_image_evaluation/`. No automated scoring or LLM-based
  ranking.
- `MediaTypeVisualProvider` (`app/visual/router.py`) needed **zero**
  changes -- `CloudflareImageProvider` slots into its
  `{VisualMediaType: VisualProvider}` mapping exactly like
  `GeminiImageProvider` does. Architecture is now:
  `VisualProvider` -> `FakeVisualProvider` / `GeminiImageProvider` /
  `CloudflareImageProvider`.
- **No locked-contract changes** -- `VisualProvider`,
  `VisualRenderRequest`, `VisualRenderResponse`, `VisualSettings`,
  `VisualRenderer`, `VisualPlan`, `VisualBeat`, `MediaTypeVisualProvider`
  all unchanged; `app/renderers/visual/renderer.py` was not modified at
  all (confirmed by a dedicated source-scan test, exactly like Phase
  20.1's).
- 57 new tests (52 adapter-level -- protocol conformance, config
  validation incl. the 1-8 steps range, missing-credential/injected-
  client behavior, GENERATED_STILL-accepted/DIAGRAM-and-every-other-
  media-type-rejected/PNG-rejected before any HTTP call, request-shape
  assertions (endpoint/auth header/prompt/steps), prompt ownership incl.
  verbatim content preservation and independence from Gemini's prompt
  builder, valid-response/dimensions-None/cf-ray/mime-type fields,
  invalid-base64/empty-image/missing-result/non-JSON rejection,
  `success:false` envelope and HTTP-status-error translation, the
  narrow-catch programming-error policy, single-call no-nested-retry; 4
  full-renderer integration -- vendor-agnostic construction,
  retry-through-Cloudflare-error, production routing to Cloudflare with
  `DIAGRAM` unavailable, and a source-scan proving
  `app/renderers/visual/renderer.py` contains no Cloudflare-specific
  reference; 1 live-smoke skip-by-default check); all 1151 prior tests
  continue to pass unmodified (1208 total: 1205 passed, 3 opt-in skips).

## Phase 20.1 — Fix Gemini Image Developer API Path (2026-09-06)

A real live evaluation run against this project's Gemini Developer API /
AI Studio key exposed a genuine implementation mistake in Phase 20:
`GeminiImageProvider` called `client.models.generate_images(...)`, which
the installed SDK rejected outright:

```
ValueError: This method is only supported in Gemini Enterprise Agent
Platform mode, not in Gemini Developer API mode.
```

(the SDK also warns that `generate_images` is deprecated). `generate_images`
is Google's dedicated Imagen API, which requires Vertex AI / Gemini
Enterprise Agent Platform credentials -- it was never actually
reachable with the plain Developer API key this project uses, and
**Phase 20's original implementation was wrong**, not merely
suboptimal. This corrective phase does not pretend otherwise.

- **API migration**: `GeminiImageProvider.render()` now calls
  `client.models.generate_content(...)` with
  `GenerateContentConfig(response_modalities=["IMAGE"],
  image_config=types.ImageConfig(aspect_ratio=...))` -- the same
  `generate_content` entrypoint `GeminiTTSProvider` already uses, just
  requesting the `IMAGE` modality instead of `AUDIO`. Verified locally in
  the installed SDK (`google-genai==2.22.0`, not from memory):
  `types.Modality.IMAGE` exists, `GenerateContentConfig
  .response_modalities` accepts it, and `types.ImageConfig` ("The image
  generation configuration to be used in GenerateContentConfig")
  documents `output_mime_type`/`output_compression_quality`/
  `image_output_options` as **not supported in Gemini API** (Developer
  API mode) -- so this adapter deliberately never sets them, and only
  sets `aspect_ratio`.
- **Model migration**: default model changed from
  `imagen-3.0-generate-002` to **`gemini-2.5-flash-image`**, a Gemini-
  native image model supported by the Gemini Developer API. Unlike the
  old default (verifiable directly from the Imagen docstring example),
  this model string does not appear anywhere in the installed SDK's
  source -- model names are never enumerated by the SDK's type system,
  so their validity can only be confirmed by an actual live call. Model
  stays fully configurable via `GeminiImageConfig.model`; no fallback
  between models was added.
- **Response extraction rewritten** for `GenerateContentResponse`
  (`candidates[0].content.parts[*].inline_data`), mirroring
  `GeminiTTSProvider._extract_pcm_bytes` exactly: the first part
  carrying `inline_data` is used, any preceding text part is ignored; no
  candidates, no parts, or every part being text-only all raise
  `VisualOutputError` explicitly (never a raw `IndexError`/
  `AttributeError`); a Responsible-AI-blocked prompt surfaces
  `response.prompt_feedback.block_reason` in the error message when
  present. `response.response_id` (the same field `GeminiTTSProvider`
  already reads) now populates `provider_request_id` when the SDK sets
  it -- Phase 20 had no such field available on the old
  `GenerateImagesResponse` and used `None` unconditionally; that
  `None`-when-absent behavior is preserved, but a real id is no longer
  discarded when the SDK does provide one.
- **PNG validated, not forced**: `ImageConfig.output_mime_type` is
  documented "not supported in Gemini API," so this adapter can no
  longer request PNG explicitly the way the old `GenerateImagesConfig
  .output_mime_type` did -- the returned `inline_data.mime_type` is
  validated against `{"image/png"}` **only when exposed** (unchanged
  policy from Phase 20), never assumed.
- **Aspect ratio set corrected**: `_SUPPORTED_ASPECT_RATIOS` widened from
  the deprecated `GenerateImagesConfig`'s 5 documented values to
  `types.ImageConfig.aspect_ratio`'s own 8 documented values (`"1:1"`,
  `"2:3"`, `"3:2"`, `"3:4"`, `"4:3"`, `"9:16"`, `"16:9"`, `"21:9"`) --
  the correct authoritative set for the API path actually used now.
  Default stays `"16:9"` (valid in both sets).
- **API key precedence confirmed correct, unchanged**: the live run's
  console output ("Both GOOGLE_API_KEY and GEMINI_API_KEY are set. Using
  GOOGLE_API_KEY.") looked alarming but was verified to be a false
  alarm. `GeminiImageProvider._build_default_client` already read
  `os.environ[config.api_key_env]` (default `"GEMINI_API_KEY"`)
  explicitly and passed it as `genai.Client(api_key=...)` -- confirmed
  correct both by reading the SDK's own source
  (`_api_client.py`'s `self.api_key = api_key or env_api_key`, where the
  explicit argument always wins) and by an empirical check
  (constructing a client with a distinct explicit key while both env
  vars were set to different values, then reading back
  `client._api_client.api_key`). The confusing log line comes from an
  SDK-internal helper (`get_env_api_key()`) that runs unconditionally
  for its own logging purposes, regardless of whether its result is
  actually used -- it fires even when an explicit `api_key` is passed
  and completely ignored. No code change was needed for this; a
  dedicated test (mocking `genai.Client` and asserting the captured
  `api_key` kwarg) now proves it directly rather than relying on SDK
  source-reading alone.
- **No contract changes**: `VisualProvider`, `VisualRenderRequest`,
  `VisualRenderResponse`, `VisualSettings`, `VisualRenderer`,
  `VisualPlan`, `VisualBeat`, and `MediaTypeVisualProvider` are all
  unchanged -- this correction stayed entirely inside
  `app/visual/providers/gemini.py` plus its own tests.
- Migrated `tests/test_gemini_image_provider.py`'s fake Gemini client
  from mocking `generate_images` to mocking `generate_content`, and
  added coverage for: a preceding text part being ignored in favor of
  the first image part, a text-only response being rejected, an empty
  parts list being rejected, a blocked-prompt-feedback reason being
  surfaced, `response_id` being used as `provider_request_id` when
  present, a client-side `ValueError` (the exact real failure this
  phase fixes) never being silently mistranslated into a retryable
  `VisualProviderError`, and the explicit-API-key-precedence test
  described above. `tests/test_visual_renderer_gemini_integration.py`'s
  fake client was migrated the same way.
  `tests/test_gemini_image_live_smoke.py` and
  `scripts/evaluate_gemini_image.py` needed **no changes** -- both
  already used `GeminiImageProvider`/`GeminiImageConfig` generically and
  automatically pick up the corrected model/method.
- 9 net new tests (54 adapter-level vs. Phase 20's 45; all Phase 20
  router/renderer-integration/live-smoke tests continue to pass
  unmodified after their fake-client bodies were migrated to
  `generate_content`); all 1142 prior tests continue to pass unmodified
  (1151 total: 1149 passed, 2 opt-in skips).

## Phase 20 — Gemini Image Provider Integration (2026-09-06)

Twelve required read-only compatibility checks ran directly against the
locked files and the installed SDK before any code was written; all
twelve came back clean -- `VisualProvider`/`VisualRenderRequest`/
`VisualRenderResponse`/`VisualSettings`/`VisualRenderer` needed **zero**
changes; Pillow remained not installed and was not added. This phase
adds the first real `VisualProvider`, supporting `GENERATED_STILL` only.

- Added `app/visual/providers/` (new subpackage, mirroring
  `app/audio/providers/`) -- `gemini.py`:
  - `GeminiImageConfig` (`model` default `"imagen-3.0-generate-002"` --
    read directly from the installed `google-genai==2.22.0` SDK's own
    `Models.generate_images` docstring example, not from memory;
    `api_key_env` default `"GEMINI_API_KEY"`; `default_aspect_ratio`
    default `"16:9"`, validated against the SDK's own five documented
    supported values) -- no API key field.
  - `GeminiImageProvider(config, client=None)` -- the first real
    `VisualProvider`. Constructs `genai.Client(api_key=...)` from
    `os.environ[config.api_key_env]` only when no `client` is injected,
    eagerly at `__init__` time (a missing key fails immediately, zero
    network calls). Uses `client.models.generate_images(...)` (Gemini's
    dedicated Imagen text-to-image endpoint), not `generate_content` --
    unlike Gemini TTS, image generation in this installed SDK is a
    separate API surface, not a chat-model modality.
  - **GENERATED_STILL only**: any other `VisualMediaType` (`DIAGRAM`
    included) is rejected with `GeminiImageUnsupportedMediaTypeError`
    before any network call -- DIAGRAM must never silently reach
    Gemini. **PNG only**: any other `VisualOutputFormat` is rejected
    with `GeminiImageUnsupportedOutputFormatError`, also before any
    network call; PNG is requested explicitly
    (`output_mime_type="image/png"`) rather than relying on an unstated
    SDK default.
  - **Prompt translation** (`_build_prompt`, the only place in the
    repository allowed to author a vendor-specific image prompt):
    encodes the approved visual language (painterly editorial 2D,
    semi-real simplified anatomy, flat 2-3 tone rendering, warm-light/
    cool-shadow, background-only haze, restrained detail, one clear
    primary focus, no embedded text unless required) as a small fixed
    style-guidance block, then appends `concept`/`primary_focus`/
    `secondary_elements`/`context_elements` **verbatim** -- content is
    never invented or rewritten, only wrapped. A small fixed
    `VisualTiState -> mood/expression note` lookup (mirroring
    `voice_state_direction`'s pattern) represents Tí when `ti_state` is
    present, paired with an explicit "Tí may only observe or react,
    never cause or alter an event" line preserving the Phase 14
    causality rule -- **not** a character-sheet ontology; canonical Tí
    consistency across independently generated stills is explicitly
    NOT solved this phase. `motion_intent` (when present) is used only
    as still-composition/framing context, never as a request to
    animate. `L1`/`L2`/`L3` visual-level tailoring was **not**
    attempted: `VisualRenderRequest` carries no `visual_level` field,
    and Phase 20 does not migrate one in to add it.
  - **Response extraction** (`_extract_image_bytes`): a Responsible-AI-
    filtered result (`generated.image is None`) raises
    `VisualOutputError` with the filter reason when available; an empty
    `generated_images` list or empty `image_bytes` likewise raises
    `VisualOutputError` -- never a raw `IndexError`/`AttributeError`.
    MIME type is validated **only when exposed**: an explicit,
    unexpected MIME (e.g. `image/jpeg` when PNG was requested) raises
    `VisualOutputError`; a MIME the SDK simply didn't populate is not
    treated as an error. Imagen exposes no width/height metadata at all
    (confirmed by inspecting `google.genai.types.Image`'s own fields) --
    `width`/`height` are `None`, never faked, and Pillow was **not**
    added solely to decode them. No official per-image request id is
    exposed either -- `provider_request_id` is `None`, never invented.
  - **Error translation — a narrow, documented boundary**: only
    `google.genai.errors.APIError` and `httpx.HTTPError` are translated
    to `VisualProviderError`; every other exception propagates
    unchanged, identical policy to `GeminiTTSProvider`.
  - **No nested retry**: the adapter retries nothing itself --
    `VisualRenderer` already owns the `VisualProviderError` retry
    policy.
  - Canonical provider name: exactly `"gemini-image"` (never `"gemini"`,
    `"google"`, or `"gemini_image"`).
- Added `app/visual/router.py` -- `MediaTypeVisualProvider`, a tiny
  composite satisfying `VisualProvider` structurally: constructed with a
  `{VisualMediaType: VisualProvider}` mapping, it dispatches
  `render(request)` to the exact provider configured for
  `request.media_type`, raising the new `VisualProviderUnavailableError`
  (added to `app/visual/errors.py`, deliberately **not** a
  `VisualProviderError` subclass so it is never retried) when no
  provider is configured for that media_type. This is Phase 20's entire
  production-routing mechanism -- **`app/renderers/visual/renderer.py`
  was not modified at all**: `VisualRenderer` simply receives
  `MediaTypeVisualProvider({VisualMediaType.GENERATED_STILL:
  GeminiImageProvider(...)})` as its ordinary `visual_provider`
  argument, exactly as it would receive any single concrete provider.
  `DIAGRAM` is deliberately left unmapped in production wiring, so a
  `DIAGRAM` beat fails non-retryably as unavailable rather than silently
  reaching Gemini.
- Added `tests/test_gemini_image_live_smoke.py` -- one opt-in live test,
  gated by both a registered `live_image` pytest marker and
  `RUN_LIVE_GEMINI_IMAGE=1`; skips cleanly with zero network/secret
  requirement otherwise. Registered the `live_image` marker in
  `pyproject.toml`.
- Added `scripts/evaluate_gemini_image.py` -- a manual visual-evaluation
  utility rendering three representative `GENERATED_STILL` briefs (L1
  establishing scene, L2 mechanism/action scene, a Tí-present scene) to
  one PNG file each; **no automated aesthetic scoring, no LLM-based
  ranking, no canonical visual style chosen** -- a human decides that
  later, mirroring `scripts/evaluate_gemini_voices.py`.
- Added `docs/TECHNICAL_SPEC_v0.1.md` Phase 20 section.
- 55 new tests (45 adapter-level -- protocol conformance, config
  validation incl. every supported aspect ratio, missing/injected API
  key, GENERATED_STILL-accepted/DIAGRAM-and-every-other-media-type-
  rejected/JPG-rejected before any SDK call, prompt ownership incl.
  verbatim concept/primary_focus/secondary/context preservation and
  style-guidance presence, Tí-state wording incl. the causality-rule
  line and its absence when `ti_state` is unset, response bytes/model/
  provider fields, MIME accepted/rejected/absent-is-not-an-error,
  None dimensions, no invented request id, empty-response and
  RAI-filtered handling, SDK/network error translation, the narrow-
  catch programming-error policy, single-call no-nested-retry; 4
  `MediaTypeVisualProvider` router tests -- protocol conformance,
  correct dispatch, unavailable-when-unmapped, no fallback guessing; 5
  full-renderer integration -- vendor-agnostic construction,
  retry-through-Gemini-error, production-routing (`GENERATED_STILL` to
  Gemini, `DIAGRAM` fails non-retryably as unavailable),
  `FakeVisualProvider` renderer behavior unaffected, and a source-scan
  proving `app/renderers/visual/renderer.py` contains no Gemini-specific
  reference; 1 live-smoke skip-by-default check); all 1087 prior tests
  continue to pass unmodified (1142 total: 1140 passed, 2 opt-in skips).

## Phase 19 — Visual Provider Abstraction + Visual Renderer (2026-09-06)

Twelve required read-only compatibility checks ran directly against the
locked files before any code was written; all twelve came back clean —
no visual provider abstraction, `VisualRenderManifest`, or Project
visual-render reference existed yet; `VisualPlan`/`VisualBeat` (Phase 14)
needed **zero** changes; Pillow was not installed and was not added. This
phase proves the visual runtime boundary using a deterministic
`FakeVisualProvider` only -- no real image/video provider is integrated.

- Added `app/visual/` (new top-level package, mirroring `app/audio/`):
  - `models.py` -- `VisualRenderRequest`/`VisualRenderResponse`, a
    provider-neutral request/response pair. `VisualRenderRequest` is
    explicitly **not** an image-generation prompt; it is a render brief
    copied verbatim from one `VisualBeat` (`render_job_id`, `beat_id`,
    `media_type`, `concept`, `primary_focus`, `secondary_elements`,
    `context_elements`, `ti_state`, `motion_intent`, `reuse_key`,
    `output_format`, `metadata`). A future concrete provider adapter
    owns translating this brief into vendor-specific parameters.
  - `provider.py` -- `VisualProvider`, a `typing.Protocol` with one
    method, `render(request) -> response`.
  - `errors.py` -- two deliberately separate hierarchies mirroring
    `app/audio/errors.py`: `VisualError`/`VisualProviderError`
    (retryable)/`VisualOutputError`, and
    `VisualStorageError`/`VisualPathError`/`VisualWriteError`.
  - `fake.py` -- `FakeVisualProvider`: deterministic, in-memory, records
    every request received, returns/raises a fixed sequence in order; no
    network calls, no API keys. Returns tiny, arbitrary placeholder bytes
    (not a real decodable PNG/JPG) -- exactly like `FakeTTSProvider`'s
    fixture audio isn't a real decodable WAV. Pillow was not added
    solely to make fake bytes "real"; codec validity stays a concrete
    provider's future concern, never this contract's.
  - `storage.py` -- `VisualFileStore`: atomic writes (temp file +
    `os.replace`), rejects blank/absolute/traversing relative paths,
    identical design to `AudioFileStore`.
  - `config.py` -- `VisualSettings` (`provider`, `output_format` default
    `PNG`, `max_provider_retries` default 1). No API key field.
- Added `VisualOutputFormat` (`PNG`/`JPG`) and `VisualRequirementStatus`
  (`RENDERED`/`EXTERNAL_REQUIRED`/`REUSE_ONLY`) to
  `app/models/common.py` -- purely additive, no existing enum changed.
- Added `app/models/visual_render.py` -- `RenderedVisualAsset`
  (`render_job_id`, `beat_id`, `media_type`, `file_path`, `width`,
  `height`, `provider_request_id`) and `VisualRenderRequirement`
  (`beat_id`, `media_type`, `status`, `reference`, `notes`; rejects
  `status=RENDERED` by construction -- rendered beats belong in `assets`
  instead), plus `VisualRenderManifest` (`script_plan_id`,
  `voice_plan_id`, `visual_plan_id`, `provider`, `output_format`,
  `assets`, `requirements`, `created_at`). No raw asset bytes anywhere in
  the manifest; a manifest must cover at least one beat.
- Added `app/renderers/visual/` (new package, outside `app/engines/`,
  mirroring `app/renderers/voice/`):
  - `renderer.py` -- `VisualRenderer`. Loads and verifies `ScriptPlan`,
    `VoicePlan`, and `VisualPlan` in that order; checks
    `VoicePlan.script_plan_id == Project.script_plan_id` (else
    `StaleVoicePlanError`) and `VisualPlan.script_plan_id ==
    Project.script_plan_id` **and** `VisualPlan.voice_plan_id ==
    (current VoicePlan).id` (else `StaleVisualPlanError`) -- both gates
    checked before any provider call, `ModuleRun`, or file write.
    Processes `VisualPlan.beats` in exact order (no parallelism) through
    a fixed, non-LLM media router:
    - `GENERATED_STILL`/`DIAGRAM` -> exactly one render job per beat
      (`render_job_id = "{beat_id}_R1"`), calls `VisualProvider.render()`
      (retrying only `VisualProviderError`, bounded by
      `1 + max_provider_retries` total attempts, no nested retry), then
      writes the returned bytes to disk via `VisualFileStore` at
      `{project_id}/{visual_plan_id}/{render_job_id}.{ext}` -- no retry
      on a write failure -- and becomes a `RenderedVisualAsset`.
    - `ASSET_REUSE` -> **never** calls the provider; becomes a
      `REUSE_ONLY` requirement referencing `reuse_key` (a blank
      `reuse_key` raises `InvalidVisualBeatError` -- a business-invalid
      beat, not a provider or storage failure, not retried).
    - `TI_STATE` -> **never** calls the provider; becomes a `REUSE_ONLY`
      requirement, preferring `reuse_key` when present and otherwise
      falling back to a deterministic `"ti_state:<enum-value>"`
      identifier (a `TI_STATE` beat with neither also raises
      `InvalidVisualBeatError`).
    - `LIMITED_MOTION`/`EVIDENCE_MEDIA`/`AI_HERO_VIDEO` -> **never**
      call the provider; each becomes an `EXTERNAL_REQUIRED`
      requirement for a future execution path.
      `EVIDENCE_MEDIA`'s `evidence_source_ids` are preserved as the
      requirement's `reference`; `LIMITED_MOTION`'s `motion_intent` is
      preserved in `notes`. No web access, no download, no animation
      rendering anywhere in this renderer.
    Builds a `VisualRenderManifest`, validates its own integrity as
    defense in depth, persists the manifest artifact, and persists
    `ModuleRun` (`module="visual_renderer"`, `version="0.1"`,
    `input_ids=[script_plan_id, voice_plan_id, visual_plan_id]`). Runs
    only at `project.state == MVP_COMPLETE`; **never transitions state,
    never updates a `Project` reference field (none exists -- confirmed
    absent, this phase's compat finding), and never re-saves
    `ScriptPlan`/`VoicePlan`/`VisualPlan`.**
  - `validation.py` -- `validate_visual_render_manifest`: every
    `VisualBeat` accounted for exactly once across `assets` +
    `requirements` combined (no missing beat, no duplicate beat_id
    anywhere in the manifest), rendered assets restricted to
    `GENERATED_STILL`/`DIAGRAM`, `REUSE_ONLY` restricted to
    `ASSET_REUSE`/`TI_STATE`, `EXTERNAL_REQUIRED` restricted to
    `LIMITED_MOTION`/`EVIDENCE_MEDIA`/`AI_HERO_VIDEO`, unique
    `render_job_id`, non-blank/relative/non-traversing `file_path`, and
    upstream id agreement (`script_plan_id`/`voice_plan_id`/
    `visual_plan_id`).
  - `errors.py` -- `RendererStateError`, `MissingScriptPlanArtifactError`,
    `MissingVoicePlanArtifactError`, `MissingVisualPlanArtifactError`,
    `StaleVoicePlanError`, `StaleVisualPlanError`,
    `InvalidVisualBeatError`, `VisualRenderManifestIntegrityError`.
- **Documented, not a defect**: a mid-run failure (provider exhaustion,
  or a file-write failure after a successful render) leaves
  already-written asset files on disk as orphans and withholds only the
  manifest artifact -- no automatic cleanup exists yet, exactly
  mirroring Phase 17's Voice Renderer.
- **Upsert semantics**: rerunning the renderer overwrites the manifest
  artifact and every asset file at the same deterministic path, matching
  `save_artifact`'s existing upsert-only convention.
- 103 new tests: visual request/response/settings validation (incl. the
  fixed two-member `VisualOutputFormat` and three-member
  `VisualRequirementStatus` enums), `FakeVisualProvider` protocol
  conformance, `VisualFileStore` (atomic write, path-safety rejection
  incl. Windows drive-letter and UNC forms), the render-manifest domain
  models (incl. rejecting `status=RENDERED` on a requirement and
  rejecting an empty manifest), pure manifest-integrity validation (id
  mismatches, missing/unknown/duplicate beat coverage, wrong
  status/media-type pairing, duplicate render_job_id/file_path, absolute/
  traversing paths), and the full renderer vertical slice (the exact
  seven-media-type happy path with call-count/routing assertions,
  request-field-equals-beat-field ownership, provider call order among
  renderable beats, `ASSET_REUSE`/`TI_STATE` no-provider-call behavior
  incl. the missing-reuse-reference business-invalid case,
  `LIMITED_MOTION`/`EVIDENCE_MEDIA`/`AI_HERO_VIDEO` no-provider-call
  behavior with preserved metadata, both stale-plan gates, provider
  retry success/exhaustion, no-retry-on-write-failure, partial-write
  orphan behavior, persistence/rerender-upsert, three-plan upstream
  immutability, no-bytes-in-SQLite, and the zero-LLM-dependency audit
  for both `app/visual/` and `app/renderers/visual/`); all 984 prior
  tests continue to pass unmodified (1087 total: 1086 passed, 1 opt-in
  live-smoke skip).

## Phase 18 — Gemini TTS Provider Integration (2026-09-05)

Ten required compatibility checks read directly from the locked files
(and, for the `google-genai` SDK itself, from live introspection of a
freshly-installed package, not from memory) before any code was written;
all ten came back clean — **`TTSProvider`, `TTSRequest`, `TTSResponse`,
`TTSSettings`, and `VoiceRenderer` are all unchanged**. This is a
provider-adapter phase only.

- Added `google-genai>=2.0,<3.0` to `pyproject.toml` (was absent —
  confirmed via `pip list` before installing).
- Added `app/audio/providers/` (new subpackage) —
  `app/audio/providers/gemini.py`:
  - `GeminiTTSConfig` (`model` default `"gemini-2.5-flash-preview-tts"`,
    `api_key_env` default `"GEMINI_API_KEY"`, `sample_rate_hz` default
    24000, `channels` default 1, `sample_width_bytes` default 2) — no API
    key field; validated bounds on all three numeric fields.
  - `GeminiTTSProvider(config, client=None)` — the first real
    `TTSProvider`. Constructs `genai.Client(api_key=...)` from
    `os.environ[config.api_key_env]` only when no `client` is injected,
    eagerly at `__init__` time (so a missing key fails immediately, with
    zero network calls). An injected client needs no environment key at
    all, keeping every default test fully offline and secret-free.
  - **Style mapping**: three small, fixed lookup dictionaries
    (`voice_state_direction`/`pace_direction`/`energy_direction`) turn
    `VoiceState`/`Pace`/`Energy` into deterministic natural-language
    director's notes (e.g. HIGH energy -> "high energy without shouting
    or exaggeration") — no numeric vendor speed/pitch values anywhere.
  - **Text ownership**: `request.text` is inserted byte-for-byte after a
    `"TRANSCRIPT:\n"` marker, preceded by an explicit "do not add,
    remove, paraphrase, translate, or comment on it" instruction. One
    `generate_content` call per `synthesize()` call — no LLM rewriting
    pass of any kind.
  - **Voice selection**: `TTSRequest.voice_id` maps directly, unchanged
    (no lowercasing, no remapping), to Gemini's
    `PrebuiltVoiceConfig.voice_name`. Single-speaker only.
  - **PCM -> WAV**: raw PCM from `inline_data.data` is wrapped into a
    real, complete in-memory WAV container using stdlib `wave`/`io`
    only (no `ffmpeg`, no `pydub`). The sample rate is parsed from
    `inline_data.mime_type` when present (authoritative), falling back to
    `GeminiTTSConfig.sample_rate_hz` otherwise; channels/sample-width
    always come from config (no per-response SDK signal for them).
    `duration_seconds = frame_count / sample_rate`, computed from the
    actual PCM byte length — never a file-size heuristic. A byte length
    that doesn't divide evenly into whole frames raises `TTSOutputError`
    rather than writing a corrupt WAV.
  - **MP3 explicitly rejected**: `request.output_format == MP3` raises
    `GeminiUnsupportedFormatError` (a `TTSOutputError` subclass) before
    any network call — Gemini output is never silently mislabeled as MP3.
  - **Error translation — a narrow, documented boundary**: only
    `google.genai.errors.APIError` (covers `ClientError`/`ServerError`)
    and `httpx.HTTPError` (transport-level failures) are translated to
    `TTSProviderError`; every other exception (e.g. a genuine `TypeError`
    bug) propagates unchanged. A malformed/missing response shape (no
    candidates, no content parts, no inline audio, empty audio,
    non-frame-aligned PCM) is handled separately and explicitly as
    `TTSOutputError` — no raw `IndexError`/`AttributeError` ever leaks out
    as if it were normal behavior.
  - **No nested retry**: the adapter retries nothing itself —
    `VoiceRenderer` already owns the `TTSProviderError` retry policy.
  - `GeminiTTSConfigurationError` (missing API key) deliberately
    subclasses the generic `TTSError` base, **not** `TTSProviderError` —
    so `VoiceRenderer`'s retry loop never wastes attempts on a
    configuration failure that can never succeed no matter how many times
    it's repeated.
- Added `tests/test_gemini_tts_live_smoke.py` — one opt-in live test,
  gated by both a registered `live_tts` pytest marker and
  `RUN_LIVE_GEMINI_TTS=1`; skips cleanly with zero network/secret
  requirement otherwise. Registered the `live_tts` marker in
  `pyproject.toml`.
- Added `scripts/evaluate_gemini_voices.py` — a manual Vietnamese
  voice-listening utility (default shortlist: Puck, Achird,
  Zubenelgenubi, Iapetus). Renders the same short sample per voice to a
  WAV file each; **no automated naturalness scoring, no LLM-based
  ranking, no canonical Tí voice chosen** — a human decides that later.
  Fixed a real Windows-console encoding bug found while testing it
  (`UnicodeEncodeError` printing Vietnamese text under a non-UTF-8
  console codepage) by forcing UTF-8 on `stdout`/`stderr`.
- Added `docs/TECHNICAL_SPEC_v0.1.md` Phase 18 section.
- 53 new tests (48 adapter-level — protocol conformance, config
  validation, missing/injected API key, Vietnamese text preservation,
  style/pace/energy mapping incl. a corrected "does not instruct
  shouting" check that distinguishes negation from instruction, voice-id
  passthrough, WAV validity incl. authoritative-mime-rate vs. config
  fallback, duration, MP3 rejection, empty/missing/malformed audio,
  SDK/network error translation, the narrow-catch programming-error
  policy; 4 full-renderer integration — vendor-agnostic construction,
  retry-through-Gemini-error, no-nested-retry-multiplication, manifest
  duration persistence; 1 live-smoke skip-by-default check); all 931
  prior tests continue to pass unmodified (984 total).

### Phase 18 addendum — canonical Tí voice selected by human listening (2026-09-06)

Ran `scripts/evaluate_gemini_voices.py` against the shortlist (Puck,
Achird, Zubenelgenubi, Iapetus). A human listened to all four renders and
selected **Puck** as the canonical Tí voice — it sounded the most natural
in Vietnamese. Achird, Zubenelgenubi, and Iapetus were rejected for
sounding more foreign/Western-accented in Vietnamese. This was a
subjective human perceptual evaluation only — no automated naturalness
scoring or LLM-based ranking was used anywhere in this decision.

Recorded as `CANONICAL_TI_VOICE_ID = "Puck"` in `app/audio/config.py` — an
application-level production preference, not a `TTSProvider`/Gemini
provider contract requirement. `TTSSettings.voice_id` remains a plain
overridable field; no contract changed.

## Phase 17 — TTS Provider Abstraction + Voice Renderer (2026-09-05)

Ten required compatibility checks read directly from the locked files
before any code was written; all ten came back "confirmed absent, safe to
add fresh" or "already exists as expected" — **no locked-contract
migration needed or performed this phase**. One new enum (`AudioFormat`:
`WAV`/`MP3`, default `WAV`).

- Added `app/audio/` — a provider-independent TTS abstraction mirroring
  `app/llm/` field-for-field:
  - `models.py` — `TTSRequest` (`text`, `voice_id`, `voice_state`, `pace`,
    `energy`, `output_format`, `metadata`, `provider_options`) /
    `TTSResponse` (`audio_bytes`, `provider`, `model`, `audio_format`,
    `duration_seconds`, `provider_request_id`, `metadata`). `audio_bytes`
    is never persisted or JSON-serialized — read once by the renderer,
    written to a file, then discarded.
  - `provider.py` — `TTSProvider` Protocol (`synthesize()`), matching
    `LLMProvider`'s shape.
  - `errors.py` — two deliberately separate hierarchies: `TTSError` ->
    `TTSProviderError` (retryable) / `TTSOutputError`; and, **not** a
    `TTSError` subclass, `AudioStorageError` -> `AudioPathError` /
    `AudioWriteError`.
  - `fake.py` — `FakeTTSProvider`: fixed response/exception sequence,
    records every request, exposes `call_count` — exact mirror of
    `FakeLLMProvider`.
  - `config.py` — `TTSSettings` (`provider`, `voice_id`, `output_format`
    default `WAV`, `max_provider_retries` default 1, `default_model`) —
    no API key field, same rationale as `LLMSettings`.
  - `storage.py` — `AudioFileStore(root).write(relative_path, data) ->
    Path`: atomic (temp file + `Path.replace`), with `relative_path`
    validated (blank / absolute POSIX / absolute Windows drive-letter or
    UNC / `..`-traversal all raise `AudioPathError`) before any
    filesystem access.
- Added `app/models/audio.py`: `RenderedVoiceTake` (`render_job_id`,
  `chunk_id`, `take_number` 1-3, `line_ids`, `file_path`,
  `duration_seconds`, `provider_request_id`, `music_state`,
  `sfx_opportunity`) and `VoiceRenderManifest` (`id`, `script_plan_id`,
  `voice_plan_id`, `provider`, `voice_id`, `output_format`, `renders`,
  `created_at`) — metadata only; `file_path` is always the relative path
  string the renderer constructed, never an absolute path.
- Added `app/renderers/` (a new top-level package) and
  `app/renderers/voice/` — **the first execution/rendering component**,
  deliberately not a "thirteenth engine": it has no `prompt.py` and zero
  `app.llm` dependency, direct or transitive, proven by both a
  source-level import scan and a fresh-subprocess `sys.modules` check.
  - `renderer.py` — `VoiceRenderer.run()`: precondition
    `project.state == MVP_COMPLETE` exactly, load+verify `ScriptPlan`,
    load+verify `VoicePlan`, verify `VoicePlan` fresh against the current
    `ScriptPlan` (else `StaleVoicePlanError`, zero side effects) -> for
    every `VoiceChunk` in order, for every take 1..`take_count` in order:
    synthesize via `TTSProvider` (retrying only `TTSProviderError`, up to
    `1 + max_provider_retries` attempts) -> write the returned bytes via
    `AudioFileStore` exactly once, **outside** the retry loop (a write
    failure is never a reason to re-invoke the provider) -> build
    `VoiceRenderManifest` -> validate its own integrity as defense in
    depth -> persist `"voice_render_manifest"`. **Never transitions
    project state, never updates any `Project` reference field (none
    exists), and never re-saves `ScriptPlan`/`VoicePlan`.**
  - `validation.py` — `validate_voice_render_manifest`: chunk coverage,
    exact take coverage per chunk (set-equality **plus** a separate
    duplicate-take-number check, so `[1, 1]` against expected `{1, 2}`
    can't slip past a naive set comparison), `line_ids` exactness,
    `render_job_id`/`file_path` uniqueness, file-path safety, and the
    manifest's own `script_plan_id`/`voice_plan_id` freshness against the
    current inputs. A non-empty result means an internal construction
    bug, not an LLM-correctable issue — there is no correction-retry step.
  - `errors.py` — all package-local (not imported from
    `app.engines.errors`): `RendererStateError`,
    `MissingScriptPlanArtifactError`, `MissingVoicePlanArtifactError`,
    `StaleVoicePlanError`, `VoiceRenderManifestIntegrityError`.
  - `models.py` — `VOICE_RENDER_MANIFEST_ARTIFACT_TYPE`,
    `VoiceRendererInput` (`project_id` only — no `additional_context`,
    since there is no LLM to steer), `VoiceRendererResult`.
- **Text ownership**: a chunk's synthesis text is the verbatim
  newline-joined (`"\n"`) concatenation of its lines' `ScriptLine.text`,
  in `chunk.line_ids` order. **Delivery-metadata passthrough**:
  `voice_state`/`pace`/`energy` copy from `VoiceChunk` onto `TTSRequest`
  completely unchanged — no vendor-specific mapping table exists yet.
- **One direct freshness gate** (not a transitive chain — the Voice
  Renderer has exactly one upstream planning artifact):
  `VoicePlan.script_plan_id == Project.script_plan_id`, checked before
  any provider call, `ModuleRun`, or file write.
- **Documented behavior, not a defect**: a mid-run failure does not roll
  back files already written by earlier takes in the same run — they are
  left on disk as harmless orphans, and only the manifest artifact is
  withheld. Orphan cleanup is out of scope for Phase 17.
- **Upsert semantics**: re-running the renderer for the same project
  overwrites the manifest artifact and every audio file at its same
  deterministic `{render_job_id}.{ext}` relative path, matching
  `save_artifact`'s existing upsert-only convention.
- Added `AudioFormat` (`WAV`/`MP3`) to `app/models/common.py`.
- Added `docs/TECHNICAL_SPEC_v0.1.md` Phase 17 section.
- 93 new tests (11 TTS request/response models, 5 provider/fake protocol
  conformance, 6 TTS settings, 17 audio file store including atomic-write
  and path-safety rejection, 21 render-manifest domain models, 14 pure
  manifest-integrity validation, 19 full renderer vertical slice —
  happy path, exact text ownership, delivery-metadata passthrough,
  take-count/job-id/call-order correctness, the stale-VoicePlan gate,
  provider retry success/exhaustion, no-retry-on-write-failure,
  partial-write orphan-file behavior, manifest-integrity wiring,
  persistence/rerender-upsert, upstream-artifact immutability, duration
  aggregation (sum vs. unknown), invalid-state/missing-artifact guards,
  and the zero-LLM-dependency audit); all 838 prior tests continue to
  pass unmodified (931 total).

## Phase 16 — Packaging P1 / Final Packaging Engine (2026-09-05)

Ten required compatibility checks read directly from the locked files
before any code was written; all ten came back "confirmed absent, safe to
add fresh" or "already exists as expected" — **no locked-contract
migration needed or performed this phase**. No new enum was needed --
`RiskLevel` is reused directly from Packaging P0.

- Added `app/models/packaging_p1.py`: `FinalPackagingPlan` (`id`,
  `packaging_prototype_id`, `script_plan_id`, `visual_plan_id`,
  `assembly_plan_id`, `title`, `thumbnail_text`, `thumbnail_concept`,
  `final_promise`, `expected_payoff`, `viewer_expectation`, `rationale`,
  `risk_of_misleading`, `notes`).
- Added `app/engines/packaging_p1/`: the twelfth engine, and the fourth
  in the production layer -- the final packaging reasoning pass, run only
  after script, voice, visual, and assembly planning are all done.
  - `validation.py` — `normalize_final_packaging_plan`: deterministically
    overwrites the four upstream-reference ids on every generation
    attempt, before validation ever runs. `validate_final_packaging_plan`:
    every business-required field (`title`, `thumbnail_concept`,
    `final_promise`, `expected_payoff`, `viewer_expectation`, `rationale`,
    and `thumbnail_text` when present) must be non-blank. **Deliberate
    deviation from Phase 13-15's convention**: these checks live at the
    business layer, not as Pydantic `model_validator`s, so a schema-valid-
    but-blank LLM response is a genuine business-correction case (per the
    patch's own explicit test scenario), not a structured-retry case.
  - `prompt.py` — the final-packaging business prompt: the "package THIS
    video, don't invent a better one" framing, P0->P1 continuity guidance,
    "one final recommendation only" (no A/B menu), final-title guidance
    (drama-first, no forced question framing, no false-title principle),
    thumbnail role/text/complement guidance (0-4 word preference, must
    complement not repeat the title), Tí-in-thumbnail guidance (usually
    present, no quota), visual-feasibility and opening-hook-consistency
    guidance, payoff-ownership and research-safety guidance, HIGH-risk
    semantics (persisted, not rejected), and explicit thumbnail-concept-
    is-creative-direction-not-an-image-prompt discipline (no Midjourney/
    Stable Diffusion syntax, no aspect ratios, no lens/rendering terms).
  - `engine.py` — `PackagingP1Engine.run()`: precondition
    `project.state == MVP_COMPLETE` exactly, plus a transitively-chained
    freshness verification across `VoicePlan` -> `VisualPlan` ->
    `AssemblyPlan`, plus standard reference checks for the currently-
    approved `PackagingPrototype`, `NarrativePlan`, and `ResearchPackage`
    -> `generate_structured(..., FinalPackagingPlan)` -> normalize ->
    business validation with one bounded correction attempt (re-normalized
    after correction too) -> persist artifact. **Never transitions
    project state, never updates any `Project` reference field (none
    exists), and never re-saves any of the six upstream artifacts it
    reads.**
  - `errors.py` — `MissingScriptPlanArtifactError`,
    `MissingVoicePlanArtifactError`, `MissingVisualPlanArtifactError`,
    `MissingAssemblyPlanArtifactError`,
    `MissingPackagingPrototypeArtifactError`,
    `MissingNarrativePlanArtifactError`,
    `MissingResearchPackageArtifactError`, `StaleVoicePlanError`,
    `StaleVisualPlanError`, `StaleAssemblyPlanError`,
    `PackagingP1BusinessValidationError`.
- **Three real production-integrity gates**, chained transitively
  (`VoicePlan` fresh vs. `ScriptPlan`; `VisualPlan` fresh vs. `ScriptPlan`
  and `VoicePlan`; `AssemblyPlan` fresh vs. all three), all checked before
  any LLM call, `ModuleRun`, or write. `VoicePlan` is loaded solely to
  support this chain -- it is never itself a `FinalPackagingPlan`
  reference.
- **P0 currentness**: `PackagingPrototype` is verified as the project's
  actual, currently-referenced artifact via the standard id-match pattern
  every engine uses, guarding against ever building final packaging on a
  stale or accidentally-supplied P0.
- **HIGH-risk output is persisted, not rejected** — mirrors Packaging
  P0's own HIGH-risk handling exactly; no publish gate exists yet to stop
  a HIGH-risk plan from being used later.
- **Freshness**: `FinalPackagingPlan`'s four reference ids plus
  `ModuleRun.input_ids` (`packaging_prototype_id`, `script_plan_id`,
  `visual_plan_id`, `assembly_plan_id`, plus `research_package_id` for
  provenance) record every id used, proving a stale `FinalPackagingPlan`
  is detectable after any upstream plan or P0 itself changes. Only
  detectability is proven this phase; no downstream enforcement was
  built, per the patch's explicit scope.
- Added `docs/TECHNICAL_SPEC_v0.1.md` Phase 16 section.
- 70 new tests (7 model, 14 validation, 27 prompt, 22 engine); all 768
  prior tests continue to pass unmodified (838 total).

## Phase 15 — Timing / Assembly Planning Engine (2026-09-05)

Ten required compatibility checks read directly from the locked files
before any code was written; all ten came back "confirmed absent, safe to
add fresh" or "already exists as expected" — **no locked-contract
migration needed or performed this phase**.

- Added `app/models/assembly.py`: `AssemblySegment` (`segment_id`,
  `script_line_ids` non-empty, `voice_chunk_ids`, `visual_beat_id`,
  `start_seconds`/`end_seconds` non-negative with `end > start` enforced
  by Pydantic, `music_state`, `transition_in`/`transition_out`,
  `emphasis_note`, `assembly_note`) and `AssemblyPlan` (`id`,
  `script_plan_id`, `voice_plan_id`, `visual_plan_id`,
  `estimated_total_duration_seconds`, `segments` non-empty,
  `required_assets` with each entry non-blank, `production_notes`).
  Added one new enum to `app/models/common.py`: `TransitionIntent` (5:
  `CUT`/`DISSOLVE`/`MATCH`/`PUSH`/`NONE`).
- Added `app/engines/assembly_plan/`: the eleventh engine, and the third
  in the production layer.
  - `validation.py` — `normalize_assembly_plan`: deterministically
    overwrites `script_plan_id`/`voice_plan_id`/`visual_plan_id` and every
    segment's `voice_chunk_ids` (recomputed from real `VoicePlan`
    overlap) on every generation attempt, before validation ever runs --
    so a wrong id or overlap guess never itself costs a correction call.
    `validate_assembly_plan`: one VisualBeat = one AssemblySegment,
    enforced as a single list-equality check against `VisualPlan.beats`'
    own order (proving valid ids, exactly-once coverage, and order
    together); independent script-line coverage re-verification; timeline
    contiguity and total-duration checks with a `1e-3`s float tolerance;
    and the `max(30, 10%)`-of-`ScriptPlan.estimated_duration_seconds`
    tolerance band, verified exactly at both boundaries.
  - `prompt.py` — the Timing/Assembly business prompt: the temporal-not-
    editorial framing, the hard one-beat-per-segment structural rule
    (told to the LLM directly, so it never needs a correction cycle to
    learn it), explicit "don't compute voice_chunk_ids yourself" guidance,
    music-state resolution from overlapping `VoiceChunk`s, the 5-value
    transition vocabulary, holistic (non-formulaic) duration estimation
    guidance, visual-hold and pacing-rhythm guidance, CapCut-oriented
    metadata framing, required-asset identifier guidance, and explicit
    output-discipline bans (no media generation, no TTS, no exact edit
    commands/keyframes/frame numbers).
  - `engine.py` — `AssemblyPlanningEngine.run()`: precondition
    `project.state == MVP_COMPLETE` exactly, plus valid, matching
    `ScriptPlan`/`VoicePlan`/`VisualPlan`, gated by two hard freshness
    checks -> `generate_structured(..., AssemblyPlan)` -> normalize ->
    business validation with one bounded correction attempt (re-normalized
    after correction too) -> persist artifact. **Never transitions
    project state, never updates any `Project` reference field (none
    exists), and never re-saves `ScriptPlan`, `VoicePlan`, or
    `VisualPlan`.**
  - `errors.py` — `MissingScriptPlanArtifactError`,
    `MissingVoicePlanArtifactError`, `MissingVisualPlanArtifactError`,
    `StaleVoicePlanError`, `StaleVisualPlanError`,
    `AssemblyPlanBusinessValidationError`.
- **Three real production-integrity gates**, all checked before any LLM
  call, `ModuleRun`, or write: `VoicePlan.script_plan_id` must match the
  project's current `script_plan_id`; `VisualPlan.script_plan_id` must
  match it too; and `VisualPlan.voice_plan_id` must match the *current*
  `VoicePlan.id`. Any mismatch raises `StaleVoicePlanError` or
  `StaleVisualPlanError` with zero side effects.
- **Freshness**: `AssemblyPlan.script_plan_id`/`voice_plan_id`/
  `visual_plan_id` and `ModuleRun.input_ids` all record the exact ids
  used, proving a stale `AssemblyPlan` is detectable after any of the
  three upstream artifacts changes. Only detectability is proven this
  phase; no downstream enforcement was built, per the patch's explicit
  scope.
- Added `docs/TECHNICAL_SPEC_v0.1.md` Phase 15 section.
- 75 new tests (17 model, 19 validation, 24 prompt, 15 engine); all 693
  prior tests continue to pass unmodified (768 total).

## Phase 14 — Visual Planning Engine (2026-09-05)

Nine required compatibility checks read directly from the locked files
before any code was written; all nine came back "confirmed absent, safe
to add fresh" or "already exists as expected" — **no locked-contract
migration needed or performed this phase**.

- Added `app/models/visual.py`: `VisualBeat` (`beat_id`, `script_line_ids`
  non-empty, `narrative_node`, `visual_level`, `visual_function`,
  `media_type`, `complexity`, `concept`, `primary_focus` non-blank,
  `secondary_elements`, `context_elements`, `ti_state`,
  `evidence_source_ids`, `motion_intent`, `reuse_key`, `notes`) and
  `VisualPlan` (`id`, `script_plan_id`, `voice_plan_id`, `beats`
  non-empty, `reusable_assets`, `notes`). Added five new enums to
  `app/models/common.py`: `VisualLevel` (3: `L1_ESTABLISH`/`L2_ACTION`/
  `L3_RELATIONSHIP`), `VisualFunction` (5), `VisualMediaType` (7),
  `ComplexityClass` (4: C0-C3), `VisualTiState` (9 -- a deliberately
  separate enum from Phase 13's `VoiceState`).
- Added `app/engines/visual_plan/`: the tenth engine, and the second in
  the production layer.
  - `validation.py` — `validate_visual_plan`: reuses Phase 13's
    coverage/order/adjacency philosophy exactly (flattened beat
    `script_line_ids` must equal the ScriptPlan's own line-id sequence),
    plus three checks new to this phase — narrative-node alignment (a
    beat may only cover lines resolving to one, matching narrative node),
    evidence-source integrity (`EVIDENCE_MEDIA` requires >=1 real
    `ResearchPackage` source id; no other media type may cite one), and a
    C3-complexity budget (<=20% of beats, for plans of 5+ beats, checked
    via integer cross-multiplication to avoid floating-point ambiguity at
    the exact boundary).
  - `prompt.py` — the Visual Planning business prompt: the L1/L2/L3
    visual grammar with L2's attention-path principle and L3's PRIMARY/
    SECONDARY/CONTEXT hierarchy, the 5 visual functions, the 7-type media
    router, the Evidence Rule ("real footage is evidence, not
    wallpaper"), the C0-C3 complexity guideline with a hard C3 cap, the
    movement principle ("do not animate everything"), Tí's 9 visual
    states with no quota, the historical-causality guard
    (`REPRESENTATIONAL_GAG`), visual-beat granularity and event-density
    guidance, the painterly/editorial-2D design-style guideline (used only
    to guide media/complexity choices, explicitly not an image prompt),
    and explicit output-discipline bans (no image/video generation, no
    image prompts, no frame-by-frame storyboarding, no exact timing).
  - `engine.py` — `VisualPlanningEngine.run()`: precondition
    `project.state == MVP_COMPLETE` exactly, plus valid, matching
    `NarrativePlan`/`ScriptPlan`/`VoicePlan`/`ResearchPackage` ->
    `generate_structured(..., VisualPlan)` -> business validation with one
    bounded correction attempt -> persist artifact. **Never transitions
    project state, never updates any `Project` reference field (none
    exists), and never re-saves `ScriptPlan` or `VoicePlan`.**
  - `errors.py` — `MissingNarrativePlanArtifactError`,
    `MissingScriptPlanArtifactError`, `MissingVoicePlanArtifactError`,
    `MissingResearchPackageArtifactError`, `StaleVoicePlanError`,
    `VisualPlanBusinessValidationError`.
- **Real production-integrity gate**: `StaleVoicePlanError` is raised
  before any LLM call, `ModuleRun`, or write whenever the current
  `VoicePlan.script_plan_id` does not match the project's current
  `script_plan_id` — the one precondition in the codebase that blocks on
  a cross-artifact mismatch rather than a missing/invalid artifact.
- **Design decision**: `ResearchPackage` is always loaded and verified,
  despite the patch framing it as optional/"if useful" for input-loading
  purposes — because the patch's own business-validation list makes
  evidence-source-id validation an unconditional required check, which is
  only meaningful with `ResearchPackage.sources` available. Documented in
  `docs/TECHNICAL_SPEC_v0.1.md`, Phase 14.
- **Freshness**: `VisualPlan.script_plan_id`/`voice_plan_id` and
  `ModuleRun.input_ids` all record the exact ids used, proving a stale
  `VisualPlan` is detectable after either `ScriptPlan` or `VoicePlan`
  changes. Only detectability is proven this phase; no downstream
  enforcement was built, per the patch's explicit scope.
- Added `docs/TECHNICAL_SPEC_v0.1.md` Phase 14 section.
- 74 new tests (16 model, 16 validation, 28 prompt, 14 engine); all 619
  prior tests continue to pass unmodified (693 total).

## Phase 13 — Voice Planning Engine (2026-09-05)

**Begins the production layer.** Eight required compatibility checks read
directly from the locked files before any code was written; all eight came
back "confirmed absent, safe to add fresh" or "already exists as
expected" — **no locked-contract migration needed or performed this
phase**.

- Added `app/models/voice.py`: `VoiceChunk` (one coherent performance unit
  — `chunk_id`, `line_ids` non-empty, `voice_state`, `pace`, `energy`,
  `take_count` bounded `[1, 3]` by Pydantic, `music_state`,
  `sfx_opportunity`, `notes`) and `VoicePlan` (`id`, `script_plan_id`,
  `chunks` non-empty). Added four new enums to `app/models/common.py`,
  matching the established "every enum lives in common.py" convention:
  `VoiceState` (8 values), `Pace` (3), `Energy` (3), `MusicState` (3).
- **Representation decision: chunks only, no separate `VoiceLinePlan`** —
  a chunk's `line_ids` already gives full per-line traceability, so a
  second line-level model would be redundant.
- Added `app/engines/voice_plan/`: the ninth engine, and the first outside
  the core MVP.
  - `validation.py` — `validate_voice_plan`: one unified deterministic
    check (flattened chunk line-id sequence must exactly equal the
    ScriptPlan's own line-id sequence) simultaneously proves full
    coverage, no duplicates, global order preservation, and chunk
    adjacency; unknown/missing lines are still reported as their own,
    more specific issues first.
  - `prompt.py` — the Voice Planning business prompt: young-male/
    early-20s natural delivery target with naturalness prioritized over
    extreme expressiveness, an explicit "not rewriting the script"
    framing, the 8 voice states / 3 pace levels / 3 energy levels / 3
    music states, selective 1-3 take-count guidance (never mechanical),
    sparing SFX-opportunity guidance, TTS performance chunking, and
    explicit output-discipline bans (no audio, no voice cloning, no exact
    timestamps).
  - `engine.py` — `VoicePlanningEngine.run()`: precondition
    `project.state == MVP_COMPLETE` exactly (no production-layer
    `ProjectState` exists, and none was added) plus a valid, matching
    `ScriptPlan` -> `generate_structured(..., VoicePlan)` -> business
    validation with one bounded correction attempt -> persist artifact.
    **Never transitions project state, never updates any `Project`
    reference field (none exists), and never re-saves `ScriptPlan`.**
  - `errors.py` — `MissingScriptPlanArtifactError`,
    `VoicePlanBusinessValidationError`.
- **Freshness**: `VoicePlan.script_plan_id` and `ModuleRun.input_ids[0]`
  both record the exact `ScriptPlan.id` used — checkable directly since
  `VoicePlan` is a brand-new model (unlike Phase 11's
  `ScriptVerificationReport`, which needed the `ModuleRun`-indirection
  approach). Only detectability is proven this phase; no downstream
  enforcement was built, per the patch's explicit scope.
- Added `docs/TECHNICAL_SPEC_v0.1.md` Phase 13 section.
- 60 new tests (18 model, 9 validation, 23 prompt, 10 engine); all 559
  prior tests continue to pass unmodified (619 total).

## Phase 12 — Core MVP Architecture Audit + End-to-End Integration (2026-09-04)

**Not a feature phase.** Read-only architecture inventory (all
`ProjectState` values, the transition graph, all 8 engine packages, all 17
review operations, all 8 artifact types, all 7 `Project` reference fields,
all 8 `ModuleRun` module names, source-of-truth ownership) performed and
reported before any code was written, per this phase's explicit
"read-only first" instruction.

- Added `tests/integration/` (19 new tests): `pipeline_helpers.py` (shared
  coherent Tacoma-Narrows-Bridge fixture data — no orchestration logic),
  `conftest.py` (a `project_at_*` fixture chain advancing one project
  through the real public engine/review operations, one stage per
  fixture — test setup, not a new orchestrator), `test_full_mvp_flow.py`
  (the literal 16-step happy path proof, artifact/human-gate/ModuleRun
  integrity checks, and 6 human-gate-bypass tests using only the public
  engine/review surface), `test_recovery_flows.py` (6 backward-recovery
  paths: idea revise, feasibility reframe, narrative revise, back-to-R1
  ×2, script rewrite, final-script revise), `test_stale_artifact_safety.py`
  (the two adversarial tests that found the defects below).
- **Two real human-gate-bypass defects found and fixed** in
  `app/review/service.py`, both via the write-failing-test-first protocol:
  a stale `ScriptVerificationReport` could accept a rewritten,
  never-verified script (`accept_script_verification`/
  `approve_final_script`); a stale `PackagingPrototype` could bypass a
  fresh Packaging P0 gate after a narrative revision
  (`approve_packaging_p0`). Both fixed by cross-checking the generating
  engine's most recent successful `ModuleRun.input_ids` against the
  project's current upstream reference — new `StaleScriptVerificationError`
  and `StalePackagingPrototypeError`. No locked contract, `ProjectState`,
  state graph, or repository schema changed. Full writeup:
  `docs/CORE_MVP_AUDIT_v0.1.md`, section 9.
- Fixing the defects required updating two pre-existing
  `test_review_service.py` fixture helpers to also persist a plausible
  `ModuleRun` (matching what a real engine run leaves behind) — every
  original assertion in those ten affected tests is unchanged.
- Added `docs/CORE_MVP_AUDIT_v0.1.md`: executive result, state graph
  audit, source-of-truth ownership audit, engine-boundary audit,
  human-gate audit, persistence/artifact-integrity audit, ModuleRun
  audit, the stale-artifact defect writeups, the transaction
  partial-progress matrix (documented, deliberately not fixed per this
  phase's explicit instruction), known technical debt, and the
  `READY_FOR_PRODUCTION_LAYER` recommendation.
- Added `docs/TECHNICAL_SPEC_v0.1.md` Phase 12 section (summary; full
  detail lives in the audit document, not duplicated).
- 19 new tests; all 540 prior tests continue to pass, with two
  pre-existing test fixtures corrected (not weakened) as part of the
  defect fix (559 total).

## Phase 11 — Script Verification + Final Script Review (2026-09-04)

**Completes the original core MVP pipeline.** Seven compatibility checks
performed and reported before any code was written; all seven came back
"already exists as expected, no migration needed" — **no locked-contract
migration performed this phase** (Phase 11 was not authorized to change one).

- Added `app/engines/script_verification/`: the eighth content engine, an
  independent scientific/narrative/promise audit of the already-locked
  `ScriptPlan` — it never rewrites the script itself.
  - `validation.py` — `normalize_status`: always deterministically
    reconciles `ScriptVerificationReport.status` with whether
    `unsupported_lines`/`overstated_lines`/`dangerous_simplifications` are
    empty (PASS iff all empty; an inconsistent PASS-with-issues downgrades
    to REFRAME; an LLM-reported REFRAME/REJECT severity choice is kept
    as-is, never second-guessed). `find_deterministic_claim_issues`/
    `find_unacknowledged_claim_issues`: the one genuine "business
    correction" trigger for this engine — since the locked
    `ScriptVerificationReport` holds findings as plain `list[str]`, not
    structured references, referential-integrity validation has no
    structural target; instead a defense-in-depth pre-check (independent
    of `ScriptEngine`'s own, already-locked validation) names `line_id`s
    whose `claim_ids` reference an unknown or `PROHIBITED` claim, and
    checks by plain substring containment whether the LLM's own
    `unsupported_lines` already acknowledges each one — never a regex
    extraction of new structure from prose.
  - `prompt.py` — the Verification business prompt: verifier-not-writer
    framing, `ResearchPackage` as sole factual source of truth, line-by-line
    factual coverage without a mechanical "every INFORM line needs a
    claim_id" rule, all five `ClaimStatus` framing rules (with a
    DISPUTED-framing example), the simplification boundary and "strategic
    incompleteness is allowed" guidance, narrative-alignment checking for
    MAJOR divergence only, a packaging-promise-delivery checklist, the
    `"L014: ..."` line-id-reference convention, and explicit
    status-setting instructions.
  - `engine.py` — `ScriptVerificationEngine.run()`: preconditions (state +
    four matching upstream artifacts: `ResearchPackage`, `NarrativePlan`,
    `PackagingPrototype`, `ScriptPlan`) -> `generate_structured(...,
    ScriptVerificationReport)` -> deterministic status normalization
    (always applied) -> claim-reference defense-in-depth check with one
    bounded correction attempt if unacknowledged -> persist the report
    artifact. **Never re-saves or modifies the reviewed `ScriptPlan`, never
    updates any `Project` reference field (none exists for this report),
    and never transitions project state** — the project stays in
    `SCRIPT_VERIFICATION` awaiting an explicit human resolution decision,
    exactly like `PackagingP0Engine` (Phase 9).
  - `errors.py` — `MissingResearchPackageArtifactError`,
    `MissingNarrativePlanArtifactError`,
    `MissingPackagingPrototypeArtifactError`, `MissingScriptPlanArtifactError`,
    `ScriptVerificationBusinessError`.
- Extended `app/review/service.py` with the human resolution/final-review
  layer:
  - From `SCRIPT_VERIFICATION`: `accept_script_verification` (->
    `SCRIPT_REVIEW`; blocked by the new `ScriptVerificationNotPassedError`
    unless the current report's status is `PASS`), `send_script_for_rewrite`
    (-> `SCRIPT`), `send_script_back_to_narrative` (-> `NARRATIVE`),
    `send_script_back_to_research` (-> `R1_RESEARCH`).
  - From `SCRIPT_REVIEW` (the final human Script Review gate): 
    `approve_final_script` (-> `MVP_COMPLETE`; same `PASS`-status guard),
    `revise_final_script` (-> `SCRIPT`),
    `send_final_script_back_to_narrative` (-> `NARRATIVE`),
    `reject_final_script` (-> `ARCHIVED`; feedback optional, unlike every
    other action in this phase — a rejection needs no further routing
    information).
  - The current `ScriptVerificationReport` is looked up by artifact type
    alone (no `id`-cross-check against a `Project` field, since neither
    exists on the locked contracts) — an intentional, non-migrating
    implementation detail, not a gap.
- Added `docs/TECHNICAL_SPEC_v0.1.md` Phase 11 section.
- 93 new tests (21 validation, 26 prompt, 17 engine, 29 review-service);
  all 447 prior tests continue to pass unmodified (540 total).

## Phase 10 — Script Engine (2026-09-04)

- **Six compatibility checks performed and reported before any code was
  written** (per this phase's explicit "begin by inspecting" instruction);
  all six came back either "already exists as expected" or "field is
  intentionally loose, enforce via prompt" — **no locked-contract
  migration needed or performed this phase**.
- Added `app/engines/script/`: the seventh content engine, producing
  `ScriptPlan` -- Tí's final spoken wording.
  - `validation.py` — `validate_script_plan`: beat/line non-emptiness,
    `beat_id`/`line_id` global uniqueness, narrative-node validity
    against `NarrativePlan.question_ladder`, non-regressing narrative
    order (computed by walking `creates_next_question`, not trusting list
    order), claim referential integrity + `PROHIBITED` guard, and a
    `[420, 660]`-second duration sanity band (target 480–600).
  - `prompt.py` — the Script business prompt: Tí's conversational voice
    ("bạn" address, strategic "Tí" self-reference, short TTS-readable
    sentences), the three upstream ownership boundaries (research =
    facts, narrative = architecture, packaging = promise), Just-in-Time
    Explanation, MEDIUM science depth, the simplification boundary, all
    five claim-status rules, micro-hook types (prompt-only, since the
    locked field is a plain string), light censored profanity only, and
    explicit no-storyboard/no-voice-plan/no-new-packaging output
    discipline.
  - `engine.py` — `ScriptEngine.run()`: preconditions (state + four
    matching upstream artifacts) -> `generate_structured(...,
    ScriptPlan)` -> business validation with one bounded correction
    attempt -> persist artifact -> update `Project.script_plan_id` ->
    transition `SCRIPT -> SCRIPT_VERIFICATION`.
  - `errors.py` — `MissingIdeaArtifactError`,
    `MissingResearchPackageArtifactError`, `MissingNarrativePlanArtifactError`,
    `MissingPackagingPrototypeArtifactError`, `ScriptBusinessValidationError`.
- **`ScriptBeat.narrative_node` opening/ending convention decided as an
  implementation detail, not a stop**: since the field is required and
  non-nullable with no sentinel in the locked schema, and the Question
  Ladder already spans the full hook-to-resolution arc, every beat
  (including opening/closing ones) must reference a real
  `QuestionLadderNode.id` — no invented `"OPENING"`-style id.
- Added `docs/TECHNICAL_SPEC_v0.1.md` Phase 10 section.
- 43 new tests; all 404 prior tests continue to pass unmodified
  (447 total).

## Phase 9 — Packaging P0 + State Contract Migration (2026-09-04)

**Explicit, authorized contract migration** — the first phase permitted to
change locked contracts, in exactly three ways:

- `ProjectState` (`app/models/common.py`) — added `PACKAGING_P0`. No other
  new state was added.
- `Project` (`app/models/project.py`) — added
  `packaging_prototype_id: UUID | None = None` (confirmed absent
  beforehand; Phase 2's artifact-reference whitelist and the SQLite
  `ProjectRow` schema were extended to match).
- `PackagingPrototype` (`app/models/packaging.py`) — added
  `id: UUID = Field(default_factory=uuid4)` (confirmed absent beforehand;
  Case B per the phase's compatibility check; matches every other
  artifact model's identical pattern). No other field on it changed.

State graph migration (`app/workflow/states.py`): `NARRATIVE_REVIEW`'s
direct edge to `SCRIPT` was removed and replaced with an edge to the new
`PACKAGING_P0`; `PACKAGING_P0` itself got exactly `{SCRIPT, NARRATIVE,
R1_RESEARCH}` (deliberately no edge to `IDEA_DISCOVERY` or `ARCHIVED` --
recovery stays local). Every other edge in the graph is unchanged,
verified by dedicated tests.

- Added `app/engines/packaging_p0/`: the sixth content engine, producing
  `PackagingPrototype`. Tests whether the narrative can be expressed as a
  strong, honest viewer promise before script writing -- not final
  title/thumbnail production. Business validation covers only non-blank
  required fields; `risk_of_misleading == HIGH` is deliberately never a
  validation failure (it's an honest, persisted outcome) -- the engine
  never transitions state itself either way.
- **Activated `approve_narrative()`** (Phase 8's stub is gone): now a real
  action, `NARRATIVE_REVIEW -> PACKAGING_P0`.
  `NarrativeApprovalNotAvailableError` was removed (no remaining
  legitimate use).
- Added `approve_packaging_p0` (`PACKAGING_P0 -> SCRIPT`, blocked by the
  new `PackagingRiskTooHighError` when `risk_of_misleading == HIGH`),
  `revise_packaging_p0` (`PACKAGING_P0 -> NARRATIVE`), and
  `send_packaging_back_to_research` (`PACKAGING_P0 -> R1_RESEARCH`) to
  `app/review/service.py`. All three require a valid, matching
  `PackagingPrototype` artifact.
- Updated `tests/test_state_machine.py` and `tests/test_review_service.py`
  intentionally for the new contract (the old direct-to-`SCRIPT` edge and
  the Phase 8 approval-stub test were replaced, not weakened).
- **Database compatibility**: `ProjectState` is stored as text, so
  existing rows with any pre-Phase-9 state remain readable without a
  migration. A local `data/motily.db` created before Phase 9 will lack
  the new `packaging_prototype_id` column and must be recreated -- there
  is still no migration framework, deliberately.
- Added `docs/TECHNICAL_SPEC_v0.1.md` Phase 9 section.
- 50 new/updated tests; all pre-existing behavior not touched by the
  authorized migration continues to pass (404 total).

## Phase 8 — Narrative Engine (2026-09-04)

- Added `app/engines/narrative/`: the fifth content engine, producing
  `NarrativePlan` -- investigative story architecture built strictly from
  `ResearchPackage` claims.
  - `validation.py` — `validate_narrative_plan`: question-ladder
    structural integrity (unique ids, valid/no-self references, exactly
    one final node, one connected linear chain, no cycles) and claim
    referential integrity (`claim_ids`/`claim_ids_used` must exist and
    must never be `PROHIBITED`).
  - `prompt.py` — the Narrative business prompt: the investigative arc
    (anomaly → mystery → investigation → ... → callback), SCQA, Question
    Ladder, Information Gap, Just-in-Time Explanation, Tí as narrator +
    investigator + character, the simplification-boundary constraint, and
    the one-shot business-correction prompt.
  - `engine.py` — `NarrativeEngine.run()`: preconditions (state + matching
    idea/research-package artifacts) -> `generate_structured(...,
    NarrativePlan)` -> deterministic `central_question` override ->
    business validation with one bounded correction attempt -> persist
    artifact -> update `Project.narrative_plan_id` -> transition
    `NARRATIVE -> NARRATIVE_REVIEW` -> persist `ModuleRun`.
  - `errors.py` — `MissingIdeaArtifactError`,
    `MissingResearchPackageArtifactError`, `NarrativeBusinessValidationError`.
- **Packaging P0 state-graph gap identified and deliberately left
  unresolved, per this phase's explicit instructions**: the locked
  `ProjectState` graph routes `NARRATIVE_REVIEW`'s only forward edge
  directly to `SCRIPT`, with no `PACKAGING_P0` state to represent the
  approved architecture's intermediate stage. `app/review/service.py`
  gained `revise_narrative` (`NARRATIVE_REVIEW -> NARRATIVE`) and
  `send_narrative_back_to_research` (`NARRATIVE_REVIEW -> R1_RESEARCH`,
  both valid locked edges, fully implemented) plus a documented stub,
  `approve_narrative`, that unconditionally raises
  `NarrativeApprovalNotAvailableError` without touching the database. See
  `docs/TECHNICAL_SPEC_v0.1.md`, Phase 8, for the full explanation.
- Added `docs/TECHNICAL_SPEC_v0.1.md` Phase 8 section.
- 42 new tests; all 312 prior tests continue to pass unmodified
  (354 total).

## Phase 7 — R1 Deep Research Engine (2026-09-04)

- Added `app/engines/research_r1/`: the fourth content engine, producing
  `ResearchPackage` -- the factual source of truth for everything
  downstream.
  - `queries.py` — `build_search_queries`: deterministic, bounded at
    `MAX_R1_SEARCH_QUERIES = 10`, deduplicated. Core-mechanism/
    authoritative-source queries always fire; dispute queries follow R0
    `major_risks`; a misconception query fires only for `REVERSAL`
    payoffs; historical/timeline queries are lowest-priority filler.
  - `validation.py` — `validate_research_package`: one deterministic pass
    checking source-URL provenance, `Claim.source_ids`/
    `Source.supports_claims` referential integrity, `claim_id`/
    `source_id` uniqueness, and evidence-by-status (`SAFE`/`QUALIFIED`/
    `DISPUTED` require at least one source; `UNCERTAIN`/`PROHIBITED`
    don't).
  - `prompt.py` — the R1 business prompt: factual-source-of-truth
    framing, the tiered evidence hierarchy, the snippet-only limitation,
    all five claim statuses, the four-part simplification-boundary
    structure, and the false-balance warning for `DISPUTED`.
  - `engine.py` — `R1ResearchEngine.run()`: preconditions (state +
    matching idea/R0/feasibility artifacts + `FeasibilityReport.status ==
    PASS`) -> queries -> retrieval -> `generate_structured(...,
    ResearchPackage)` -> deterministic `central_question` override ->
    business validation with one bounded correction attempt -> persist
    artifact -> update `Project.research_r1_id` -> transition
    `R1_RESEARCH -> NARRATIVE` -> persist `ModuleRun`.
  - `errors.py` — `MissingIdeaArtifactError`,
    `MissingResearchR0ArtifactError`, `MissingFeasibilityArtifactError`,
    `FeasibilityNotPassedError`, `R1BusinessValidationError`.
- Confirmed `Project.research_r1_id` already existed on the locked
  Phase 1 model and was already wired through Phase 2's artifact-reference
  whitelist — no changes needed to either.
- **Spec accommodation, not a contract change**: the approved research
  process calls for `simplification_boundary` to carry four distinct
  components (`safe_model`, `allowed_simplifications`,
  `omitted_complexity`, `dangerous_oversimplifications`), but the locked
  Phase 1 `ResearchPackage.simplification_boundary` is a plain `str`.
  Rather than adding fields to a locked contract, the R1 prompt requires
  the model to write all four as labeled parts within that one string.
  `ResearchPackage` itself was not modified.
- Added `docs/TECHNICAL_SPEC_v0.1.md` Phase 7 section.
- 48 new tests; all 264 prior tests continue to pass unmodified
  (312 total).

## Phase 6 — Feasibility Engine + Human Decision (2026-09-04)

- Added `app/engines/feasibility/`: the third content engine.
  - `status.py` — `derive_overall_status`: deterministically re-derives
    `FeasibilityReport.status` from the five axis statuses (any `REJECT`
    axis wins, else any `REFRAME` axis, else `PASS`), overriding whatever
    the LLM produced.
  - `prompt.py` — the Feasibility business prompt: brand/audience/
    production context, R0 evidence, exactly five axes (audience,
    science, narrative, visual, production), explicit "R0 is provisional,
    not final truth" and no-script/no-narrative-outline/no-visual-
    generation rules.
  - `engine.py` — `FeasibilityEngine.run()`: `FEASIBILITY` -> load +
    verify `IdeaCandidate` and `ResearchR0` -> `generate_structured(...,
    FeasibilityReport)` -> derive overall status -> persist artifact ->
    update `Project.feasibility_id` -> persist `ModuleRun`. **Never
    transitions project state** — the project stays in `FEASIBILITY`.
  - `errors.py` — `MissingIdeaArtifactError`, `MissingResearchArtifactError`.
- Extended `app/review/`: `decide_feasibility(db_engine, project_id,
  decision: GateStatus, feedback=None)` — requires `decision` to equal
  the stored `FeasibilityReport.status` exactly
  (`FeasibilityDecisionMismatchError` otherwise; no override feature in
  Phase 6). Routes `PASS -> R1_RESEARCH`, `REFRAME -> IDEA_DISCOVERY`
  (feedback required), `REJECT -> ARCHIVED`. Prior artifact references
  are never cleared on `REFRAME`.
- Added `docs/TECHNICAL_SPEC_v0.1.md` Phase 6 section.
- 37 new tests; all 227 prior tests continue to pass unmodified
  (264 total).

## Phase 5 — Human Approval Bridge + R0 Research Vertical Slice (2026-09-04)

- Added `app/review/`: `approve_idea`/`revise_idea` (`service.py`),
  `ReviewStateError`/`MissingReviewArtifactError` (`errors.py`). Plain
  deterministic functions, no LLM call, no `BaseReview`/agent framework.
  `IDEA_REVIEW -> ARCHIVED` (reject) is intentionally not implemented —
  the locked state graph has no such edge.
- Added `app/research/`: provider-independent retrieval.
  `ResearchRetriever` (`Protocol`), `ResearchQuery`/`RetrievedSource`/
  `ResearchSearchResponse` (`models.py`), `ResearchRetrieverError`
  (`errors.py`), `FakeResearchRetriever` (`fake.py`) — deterministic,
  no network calls, mirrors `FakeLLMProvider`.
- Added `app/engines/research_r0/`: the second content engine.
  - `queries.py` — deterministic search-query construction from
    `IdeaCandidate` (topic, central question, physics core, up to one
    research question), capped at `MAX_R0_SEARCH_QUERIES = 4`.
  - `prompt.py` — the R0 business prompt (discovery-only, no final-truth
    claims) plus the one-shot business-correction prompt for hallucinated
    source URLs.
  - `engine.py` — `R0ResearchEngine.run()`: `R0_RESEARCH` ->
    deterministic queries -> `ResearchRetriever` -> dedupe by URL ->
    `generate_structured(..., ResearchR0)` -> business URL cross-check
    (one bounded correction attempt, never silently repaired) -> persist
    artifact -> update `Project.research_r0_id` -> transition to
    `FEASIBILITY` -> persist `ModuleRun`.
  - `errors.py` — `MissingIdeaArtifactError`,
    `ResearchSourceHallucinationError`.
- R0's `recommendation` (`CONTINUE`/`REFRAME`/`REJECT`) never blocks the
  transition to `FEASIBILITY` — that judgment belongs to a future
  Feasibility Engine, not R0.
- `ResearchR0.idea_id` is set deterministically by the engine from the
  already-known `idea.id` after generation, rather than trusted from the
  model's own output — a structural field, not model-owned content.
- Added `docs/TECHNICAL_SPEC_v0.1.md` Phase 5 section.
- 48 new tests; all 179 prior tests continue to pass unmodified
  (227 total).

## Phase 4 — Idea Engine Vertical Slice (2026-09-04)

- Added `app/engines/`: the first real content engine.
  - `errors.py` — `EngineStateError` (shared across future engines; not a
    `BaseEngine` framework).
  - `idea/models.py` — `DiscoveryMode` (`OPEN`/`EXPAND`), `IdeaEngineInput`,
    `IdeaEngineResult`, and the centralized `IDEA_CANDIDATE_ARTIFACT_TYPE`
    constant (`"idea_candidate"`).
  - `idea/prompt.py` — `build_system_prompt`/`build_user_prompt`: the
    business prompt owned by the Idea Engine (brand/audience/production
    context, Double Diamond + ABT framework, physics-gravity rule, payoff
    archetypes, output-discipline rules, the `IdeaCandidate` JSON schema).
  - `idea/engine.py` — `IdeaEngine.run()`: the full vertical slice —
    `IDEA_DISCOVERY` → `generate_structured` → persist `IdeaCandidate` →
    update `Project.idea_candidate_id` → transition to `IDEA_REVIEW` →
    persist `ModuleRun`.
- `IdeaEngine.run()` requires `project.state == IDEA_DISCOVERY`
  (`EngineStateError` otherwise, before any `ModuleRun`/provider call/write
  happens) and reaches `IDEA_REVIEW` only — no automatic `HumanApproval`,
  no automatic advance to `R0_RESEARCH`.
- Any failure from generation through the state transition is recorded as
  `ModuleRun(status=FAILED, error_message=...)` and re-raised; the project
  never reaches `IDEA_REVIEW` and `idea_candidate_id` never points at a
  non-existent artifact.
- Repository dependencies (`project_repo`, `artifact_repo`,
  `module_run_repo`) are constructor-injected on `IdeaEngine`, defaulting
  to the real Phase 2 modules — lets tests simulate a storage failure with
  a stub instead of corrupting SQLite.
- Added `docs/TECHNICAL_SPEC_v0.1.md` Phase 4 section.
- 31 new tests (models, prompt content, and the full vertical slice via
  `FakeLLMProvider`); all 148 prior tests continue to pass unmodified
  (179 total).

## Phase 3 — LLM Provider Abstraction + Structured Output (2026-09-04)

- Added `app/llm/`: provider-independent LLM infrastructure.
  - `models.py` — `LLMRequest`, `LLMResponse`, `TokenUsage`,
    `ValidationFailure`, generic `StructuredGenerationResult[T]`.
  - `provider.py` — `LLMProvider`, a `typing.Protocol` with one method,
    `generate(request) -> response`.
  - `structured.py` — `generate_structured(provider, request, output_model,
    max_retries=2)`: parses/validates JSON through the target Pydantic
    model, retries only validation failures (never provider failures) up
    to `1 + max_retries` total calls, and builds a correction request
    (original task + schema + concise error) rather than repeating the
    same prompt or leaking a stack trace.
  - `errors.py` — `LLMError`, `LLMProviderError`, `StructuredOutputError`,
    `StructuredOutputExhaustedError` (carries `attempts` and
    `validation_failures`).
  - `fake.py` — `FakeLLMProvider`: deterministic, in-memory, records every
    request received; no network calls, no API keys.
  - `config.py` — `LLMSettings` (provider, default model, retry bound,
    optional defaults). No API key field — Phase 3 has no concrete
    provider to consume one.
- `generate_structured()` returns metadata only; it never writes to
  SQLite and never creates a `ModuleRun` record — that stays a future
  orchestrator's responsibility.
- No content engines, no business prompts, no concrete remote provider
  (Anthropic/OpenAI/Gemini SDKs), no agent/multi-agent abstraction added.
- Added `docs/TECHNICAL_SPEC_v0.1.md` Phase 3 section.
- 36 new tests; all 112 prior Phase 1/1.1/2 tests continue to pass
  unmodified (148 total).

## Phase 2 — State Machine + SQLite Persistence (2026-09-04)

- Added `app/workflow/`: the approved `ProjectState` transition graph
  (`states.py`), `can_transition`/`validate_transition`
  (`transitions.py`), and `InvalidStateTransitionError` (`errors.py`).
  Validates topology only — no business evaluation.
- Added `app/storage/`: SQLite persistence via SQLAlchemy 2.x.
  - `projects.py` — `create_project`, `get_project`, `list_projects`,
    `update_project_state` (always validated through the transition graph),
    `update_artifact_reference`.
  - `artifacts.py` — `save_artifact`/`get_artifact`, storing the eight
    module-output contract types as validated JSON payloads in one shared
    `artifacts` table keyed by `(project_id, artifact_type)`.
  - `approvals.py` — `save_approval`, `list_approvals_for_project`,
    `get_latest_approval_for_stage`. Approvals never mutate project state.
  - `module_runs.py` — `save_module_run` (upsert by `run_id`),
    `get_module_run`, `list_module_runs_for_project`.
  - `database.py` — `init_database` (idempotent, default
    `data/motily.db`), `orm.py` — row definitions kept separate from the
    Pydantic domain contracts.
- Added `ModuleRun` domain model and `ModuleRunStatus` enum
  (`app/models/module_run.py`, `app/models/common.py`) as audit-log
  metadata only.
- Added `docs/TECHNICAL_SPEC_v0.1.md` Phase 2 section.
- 71 new tests across state machine and all four repositories; all 41
  prior Phase 1/1.1 tests continue to pass unmodified (112 total).

## Phase 1.1 — Contract corrections (2026-09-04)

- `TemplateVersions` defaults changed from `"v1"` to `"0.1"` for all five
  fields (idea, research, feasibility, narrative, script).
- Added `GateEvaluation` (`status: GateStatus`, non-blank `reason: str`) to
  `app/models/common.py`.
- `IdeaCandidate.brand_fit`, `IdeaCandidate.general_audience_gate`, and
  `IdeaCandidate.longform_potential` changed from free-form `str` /
  `GateStatus` to structured `GateEvaluation`.
- Added `PauseIntent` enum (`NONE`, `SHORT`, `MEDIUM`, `LONG`) to
  `app/models/common.py`.
- `ScriptLine.pause_after` changed from `float | None` to `PauseIntent`
  (default `NONE`). Exact pause timing is explicitly out of scope for
  Phase 1 and deferred to a future Voice/Timing module.
- Added `docs/TECHNICAL_SPEC_v0.1.md` as the repository-local source of
  truth (no prior spec file existed in this repository).
- Updated/added tests covering all of the above.

## Phase 1 — Initial contracts

- Repository scaffold, `GlobalConfig` loading/validation, core typed domain
  models and enums, JSON serialization, and unit tests.
