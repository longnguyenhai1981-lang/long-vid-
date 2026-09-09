"""Phase 35: the `motily` command-line surface over ProductionService.

This module is intentionally thin (requirement #26): every command
handler parses its own arguments, calls exactly one ProductionService
method, formats the result, and maps it to an exit code. No orchestration
logic, no graph traversal, no approval decision logic lives here -- see
app/services/production_service.py and app/services/production_review.py
for the actual behavior.

Two explicit, minimal test seams (requirement #10) -- never dozens of
monkeypatched globals:

- `_composition_override`: when set, every command uses this
  AppComposition instead of building a fresh one from `--db`. Tests
  build one composition (one shared SQLite engine) for a whole scenario.
- `_context_factory_override`: when set, called as
  `factory(project_id, db_engine) -> ExecutionContext` instead of the
  real-mode default (an empty context with no providers). Tests use this
  to inject a freshly-queued FakeLLMProvider/FakeTTSProvider/etc. before
  each `produce`/`resume` invocation, exactly like a real caller
  preparing real provider calls would have to reconfigure between steps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from uuid import UUID

import typer

from app.bootstrap import AppComposition, build_composition
from app.cli.dto import build_run_dto
from app.cli.exit_codes import (
    EXIT_FAILED,
    EXIT_INVALID_INPUT,
    EXIT_SUCCESS,
    exit_code_for_status,
)
from app.orchestration.models import ApprovalGateType, ProductionRunStatus
from app.orchestration.registry import ExecutionContext
from app.services.production_review import GateNotPendingError, GateRejectionNotSupportedError
from app.services.production_service import (
    DEFAULT_TARGET_ALIAS,
    GATE_ALIASES,
    GateMismatchError,
    NoGateCurrentlyPendingError,
    TARGET_ALIASES,
    UnknownGateAliasError,
    gate_alias_for,
)
from app.storage.database import DEFAULT_DB_PATH
from app.storage.errors import ProductionRunNotFoundError, ProjectNotFoundError

app = typer.Typer(name="motily", help="Motily production orchestrator CLI.", no_args_is_help=True)

_composition_override: AppComposition | None = None
_context_factory_override = None  # Callable[[UUID, Engine], ExecutionContext] | None


def _get_composition(db: Path) -> AppComposition:
    if _composition_override is not None:
        return _composition_override
    return build_composition(db)


def _build_context(composition: AppComposition, project_id: UUID) -> ExecutionContext:
    if _context_factory_override is not None:
        return _context_factory_override(project_id, composition.db_engine)
    return ExecutionContext(db_engine=composition.db_engine, project_id=project_id)


_READABLE_REASONS = {
    "FEASIBILITY_NOT_PASSED": "The feasibility report did not pass -- rework the idea/research before continuing.",
    "SCRIPT_VERIFICATION_NOT_PASSED": "Script verification did not pass -- the script needs rework before continuing.",
    "PACKAGING_RISK_TOO_HIGH": "Packaging risk-of-misleading is HIGH -- rework packaging before continuing.",
    "QC_NOT_READY": "Media QC did not pass -- the video needs rework before final approval.",
    "GATE_REJECTED": "A human explicitly rejected this gate.",
    "BLOCKED_DEPENDENCY": "An upstream dependency did not succeed.",
    "NODE_NOT_WIRED": "This node has no adapter implementation.",
}


def _parse_stop_reason(run) -> tuple[str | None, str | None]:
    if not run.stop_reason:
        return None, None
    node_id, _, reason_code = run.stop_reason.partition(":")
    return (node_id or None), (reason_code or None)


def _format_human(run) -> str:
    dto = build_run_dto(run)
    lines = [f"Production run: {dto.production_run_id}", f"Status: {dto.status}"]

    if run.status is ProductionRunStatus.SUCCEEDED:
        lines.append("Production complete.")
    elif run.status is ProductionRunStatus.WAITING_APPROVAL:
        lines.append(f"Stopped at: {dto.waiting_gate}")
        lines.append(f"Subject: {dto.subject_artifact_id}")
    elif run.status is ProductionRunStatus.BLOCKED:
        node_id, reason_code = _parse_stop_reason(run)
        lines.append(f"Reason: {reason_code}")
        if reason_code in _READABLE_REASONS:
            lines.append(_READABLE_REASONS[reason_code])
    elif run.status is ProductionRunStatus.FAILED:
        node_id, _reason_code = _parse_stop_reason(run)
        record = run.node_states.get(node_id) if node_id else None
        lines.append(f"Node: {node_id}")
        if record is not None:
            lines.append(f"ModuleRun: {record.module_run_id}")
            lines.append(f"Error: {record.message}")

    lines.append("")
    lines.append(f"Executed: {len(dto.executed_nodes)}")
    lines.append(f"Reused: {len(dto.reused_nodes)}")
    lines.append(f"Blocked: {len(dto.blocked_nodes)}")
    lines.append(f"Failed: {len(dto.failed_nodes)}")

    if run.status is ProductionRunStatus.WAITING_APPROVAL and dto.waiting_gate is not None:
        alias = gate_alias_for(ApprovalGateType(dto.waiting_gate))
        lines.append("")
        lines.append("Next:")
        lines.append(f"  motily approve {dto.production_run_id} --gate {alias}")
        lines.append(f"  motily resume {dto.production_run_id}")

    return "\n".join(lines)


def _format_verbose_trace(run) -> str:
    header = f"{'NODE':<16} {'STATUS':<18} {'EXECUTED/REUSED':<18} REASON"
    rows = [header]
    for node_id, record in run.node_states.items():
        mode = "executed" if record.executed_this_run else ("reused" if record.reused_existing_artifact else "-")
        reason = record.reason.value if record.reason else "-"
        rows.append(f"{node_id:<16} {record.status.value:<18} {mode:<18} {reason}")
    return "\n".join(rows)


def _emit_run(run, *, json_output: bool, verbose: bool = False) -> None:
    if json_output:
        typer.echo(_json_dumps(build_run_dto(run).to_json_dict()))
    else:
        typer.echo(_format_human(run))
        if verbose:
            typer.echo("")
            typer.echo(_format_verbose_trace(run))


def _json_dumps(data: dict) -> str:
    import json

    return json.dumps(data, indent=2, sort_keys=False)


def _exit_for_run(run) -> None:
    raise typer.Exit(code=exit_code_for_status(run.status))


def _fail(message: str, *, code: int = EXIT_INVALID_INPUT) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=code)


@app.command()
def produce(
    project_id: Optional[str] = typer.Option(None, "--project-id", help="Existing project UUID; creates a new project if omitted."),
    brief: Optional[str] = typer.Option(None, "--brief", help="Initial idea seed/brief text."),
    target: str = typer.Option(DEFAULT_TARGET_ALIAS, "--target", help=f"One of: {', '.join(sorted(TARGET_ALIASES))}"),
    domain: str = typer.Option("physics", "--domain"),
    discovery_mode: str = typer.Option("open", "--discovery-mode", help="open or expand"),
    additional_context: Optional[str] = typer.Option(None, "--additional-context"),
    db: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path."),
    json_output: bool = typer.Option(False, "--json"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """Start exactly one new ProductionRun (requirement #28: running
    `produce` twice creates two separate runs -- use `resume` to
    continue an existing one)."""
    if target not in TARGET_ALIASES:
        _fail(f"Unknown --target {target!r}. Valid targets: {', '.join(sorted(TARGET_ALIASES))}")
    if project_id is None and brief is None:
        _fail("A new production needs either --project-id (existing project) or --brief (new project).")

    composition = _get_composition(db)
    service = composition.service

    if project_id is not None:
        try:
            pid = UUID(project_id)
        except ValueError:
            _fail(f"Invalid project id: {project_id!r}")
    else:
        pid = service.create_project(title=brief[:80])

    idea_input = None
    if brief is not None or project_id is None:
        idea_input = service.build_initial_idea_input(
            pid, brief=brief, domain=domain, discovery_mode=discovery_mode,
            additional_context=additional_context,
        )

    ctx = _build_context(composition, pid)
    if idea_input is not None and ctx.initial_idea_input is None:
        ctx.initial_idea_input = idea_input

    try:
        run = service.start(pid, target, ctx)
    except ProjectNotFoundError:
        _fail(f"Project not found: {project_id}")
    except Exception as exc:  # noqa: BLE001 -- surfaced as a clear CLI error, optionally with traceback
        if debug:
            raise
        _fail(f"{type(exc).__name__}: {exc}", code=EXIT_FAILED)

    _emit_run(run, json_output=json_output)
    _exit_for_run(run)


@app.command()
def resume(
    run_id: str = typer.Argument(..., help="ProductionRun id to resume."),
    db: Path = typer.Option(DEFAULT_DB_PATH, "--db"),
    json_output: bool = typer.Option(False, "--json"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """Continue an existing ProductionRun: reuses every fresh upstream
    artifact and stops at the next gate/block/failure. Never
    auto-approves a waiting gate."""
    try:
        rid = UUID(run_id)
    except ValueError:
        _fail(f"Invalid production run id: {run_id!r}")

    composition = _get_composition(db)
    try:
        existing = composition.service.status(rid)
    except ProductionRunNotFoundError:
        _fail(f"Production run not found: {run_id}")

    ctx = _build_context(composition, existing.project_id)
    try:
        run = composition.service.resume(rid, ctx)
    except Exception as exc:  # noqa: BLE001
        if debug:
            raise
        _fail(f"{type(exc).__name__}: {exc}", code=EXIT_FAILED)

    _emit_run(run, json_output=json_output)
    _exit_for_run(run)


@app.command()
def status(
    run_id: str = typer.Argument(...),
    db: Path = typer.Option(DEFAULT_DB_PATH, "--db"),
    json_output: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Read-only: prints the current state of a ProductionRun. Never
    executes any node."""
    try:
        rid = UUID(run_id)
    except ValueError:
        _fail(f"Invalid production run id: {run_id!r}")

    composition = _get_composition(db)
    try:
        run = composition.service.status(rid)
    except ProductionRunNotFoundError:
        _fail(f"Production run not found: {run_id}")

    _emit_run(run, json_output=json_output, verbose=verbose)
    raise typer.Exit(code=EXIT_SUCCESS)


@app.command()
def runs(
    project_id: str = typer.Option(..., "--project-id"),
    limit: int = typer.Option(20, "--limit"),
    db: Path = typer.Option(DEFAULT_DB_PATH, "--db"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Read-only: lists recent ProductionRuns for a project, newest
    first."""
    try:
        pid = UUID(project_id)
    except ValueError:
        _fail(f"Invalid project id: {project_id!r}")

    composition = _get_composition(db)
    run_list = composition.service.list_runs(pid, limit=limit)

    if json_output:
        typer.echo(_json_dumps({"runs": [build_run_dto(r).to_json_dict() for r in run_list]}))
    else:
        if not run_list:
            typer.echo("No production runs found for this project.")
        for r in run_list:
            typer.echo(f"{r.id}  {r.created_at.isoformat()}  target={r.target_node}  status={r.status.value}  stop_reason={r.stop_reason}")

    raise typer.Exit(code=EXIT_SUCCESS)


@app.command()
def approve(
    run_id: str = typer.Argument(...),
    gate: Optional[str] = typer.Option(None, "--gate", help=f"One of: {', '.join(sorted(GATE_ALIASES))} (optional if only one gate is pending)"),
    note: Optional[str] = typer.Option(None, "--note"),
    db: Path = typer.Option(DEFAULT_DB_PATH, "--db"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Writes an approval only -- does NOT automatically resume the
    pipeline (requirement #29: mutations stay explicit and auditable).
    Run `motily resume <run-id>` afterward to continue."""
    try:
        rid = UUID(run_id)
    except ValueError:
        _fail(f"Invalid production run id: {run_id!r}")

    composition = _get_composition(db)
    try:
        outcome = composition.service.approve_pending(rid, gate_alias=gate, note=note)
    except (UnknownGateAliasError, GateMismatchError, NoGateCurrentlyPendingError, GateNotPendingError) as exc:
        _fail(str(exc))
    except ProductionRunNotFoundError:
        _fail(f"Production run not found: {run_id}")

    if json_output:
        typer.echo(_json_dumps({"gate": outcome.gate_type.value, "decision": outcome.decision.value, "subject_artifact_id": outcome.subject_artifact_id}))
    else:
        typer.echo(f"APPROVED {outcome.gate_type.value} (subject={outcome.subject_artifact_id})")
        typer.echo(f"Not resumed yet -- run: motily resume {run_id}")

    raise typer.Exit(code=EXIT_SUCCESS)


@app.command()
def reject(
    run_id: str = typer.Argument(...),
    gate: Optional[str] = typer.Option(None, "--gate"),
    note: Optional[str] = typer.Option(None, "--note"),
    db: Path = typer.Option(DEFAULT_DB_PATH, "--db"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Writes a rejection. Production stays blocked; no artifact is
    deleted or regenerated automatically."""
    try:
        rid = UUID(run_id)
    except ValueError:
        _fail(f"Invalid production run id: {run_id!r}")

    composition = _get_composition(db)
    try:
        outcome = composition.service.reject_pending(rid, gate_alias=gate, note=note)
    except (UnknownGateAliasError, GateMismatchError, NoGateCurrentlyPendingError, GateNotPendingError, GateRejectionNotSupportedError) as exc:
        _fail(str(exc))
    except ProductionRunNotFoundError:
        _fail(f"Production run not found: {run_id}")

    if json_output:
        typer.echo(_json_dumps({"gate": outcome.gate_type.value, "decision": outcome.decision.value, "subject_artifact_id": outcome.subject_artifact_id}))
    else:
        typer.echo(f"REJECTED {outcome.gate_type.value} (subject={outcome.subject_artifact_id})")
        typer.echo("Production is blocked and requires explicit user action (rework upstream, then produce/resume again).")

    raise typer.Exit(code=EXIT_SUCCESS)


@app.command()
def doctor(
    db: Path = typer.Option(DEFAULT_DB_PATH, "--db"),
) -> None:
    """Local environment diagnostics only -- never calls a provider or
    verifies credentials over the network."""
    import os
    import shutil

    problems: list[str] = []

    try:
        db.parent.mkdir(parents=True, exist_ok=True)
        probe = db.parent / ".motily_doctor_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        typer.echo(f"[ok] database directory writable: {db.parent}")
    except OSError as exc:
        problems.append(f"database directory not writable: {db.parent} ({exc})")

    for exe in ("ffmpeg", "ffprobe"):
        if shutil.which(exe) is None:
            problems.append(f"{exe} not found on PATH")
        else:
            typer.echo(f"[ok] {exe} found on PATH")

    if os.environ.get("GEMINI_API_KEY"):
        typer.echo("[ok] GEMINI_API_KEY is set (presence only -- not verified)")
    else:
        typer.echo("[info] GEMINI_API_KEY is not set -- real TTS/visual providers are unavailable")

    for problem in problems:
        typer.echo(f"[missing] {problem}", err=True)

    if problems:
        raise typer.Exit(code=6)
    raise typer.Exit(code=EXIT_SUCCESS)


if __name__ == "__main__":
    app()
