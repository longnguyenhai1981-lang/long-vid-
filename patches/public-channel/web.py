from __future__ import annotations

import os
from pathlib import Path
from tempfile import mkdtemp
import shutil
from uuid import UUID

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.bootstrap import AppComposition, build_composition
from app.orchestration.models import ProductionRunStatus
from app.orchestration.registry import ExecutionContext
from app.services.production_review import GateRejectionNotSupportedError
from app.storage.database import DEFAULT_DB_PATH
from app.studio.models import GateDecisionRequest, StartProductionRequest, StudioActionResult, UpdateChannelConfigRequest
from app.studio.service import StudioService, run_summary
from app.studio.update import DevUpdateManager, default_update_root, restart_command, source_project_root, spawn_restart_helper
from app.studio.update_channel import GitHubDevUpdateChannel, PublicManifestUpdateChannel, RemoteUpdateError

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def _uuid(value: str, label: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(400, f"Invalid {label}: {value!r}") from None


def _safe_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}")


def _context(composition: AppComposition, project_id: UUID) -> ExecutionContext:
    if composition.context_factory is not None:
        return composition.context_factory(project_id, composition.db_engine)
    return ExecutionContext(db_engine=composition.db_engine, project_id=project_id)


def create_studio_app(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    composition: AppComposition | None = None,
    update_manager: DevUpdateManager | None = None,
    update_channel: GitHubDevUpdateChannel | PublicManifestUpdateChannel | None = None,
) -> FastAPI:
    composition = composition or build_composition(Path(db_path))
    studio = StudioService(composition, workspace_root=os.environ.get("MOTILY_WORKSPACE_ROOT", "data/workspace"))
    app = FastAPI(title="Motily Studio", version="0.1.0", docs_url="/api/docs", redoc_url=None)
    app.state.composition = composition
    app.state.studio = studio
    updater = update_manager or DevUpdateManager(source_project_root(), default_update_root())
    channel = update_channel or PublicManifestUpdateChannel(config_path=updater.update_root / "channel_config.json")
    app.state.updater = updater
    app.state.update_channel = channel

    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/api/health")
    def health():
        return {"ok": True, "app": "motily-studio"}

    def _require_local_update(request: Request) -> None:
        if os.environ.get("MOTILY_STUDIO_ALLOW_REMOTE_UPDATES") == "1":
            return
        host = request.client.host if request.client else ""
        if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
            raise HTTPException(403, "Development updates are allowed from localhost only")

    @app.get("/api/dev-update/status")
    def dev_update_status(request: Request):
        _require_local_update(request)
        return {**updater.status(), "remote_channel": channel.status()}

    @app.post("/api/dev-update/configure")
    def dev_update_configure(payload: UpdateChannelConfigRequest, request: Request):
        _require_local_update(request)
        try:
            return {"ok": True, "remote_channel": channel.configure(payload.repository, payload.branch)}
        except RemoteUpdateError as exc:
            raise _safe_error(exc)

    @app.post("/api/dev-update/check")
    def dev_update_check(request: Request):
        _require_local_update(request)
        try:
            info = channel.check(current_revision=updater.installed_remote_revision)
            return {"ok": True, "remote": info.as_dict()}
        except RemoteUpdateError as exc:
            raise _safe_error(exc)

    @app.post("/api/dev-update/download")
    def dev_update_download(request: Request):
        _require_local_update(request)
        temp_path: Path | None = None
        try:
            info = channel.check(current_revision=updater.installed_remote_revision)
            staged = updater.latest_staged()
            if staged is not None and staged.remote_revision == info.revision:
                return {"ok": True, "remote": info.as_dict(), "staged": staged.as_dict(), "reused_staged": True}
            temp_path = channel.download(info.revision)
            staged = updater.stage(
                temp_path,
                original_filename=f"motily-{info.short_revision}.zip",
                remote_revision=info.revision,
                remote_source=f"github:{info.repository}@{info.branch}",
            )
            return {"ok": True, "remote": info.as_dict(), "staged": staged.as_dict(), "reused_staged": False}
        except RemoteUpdateError as exc:
            raise _safe_error(exc)
        except Exception as exc:
            raise _safe_error(exc)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    @app.post("/api/dev-update/quick")
    def dev_update_quick(request: Request):
        _require_local_update(request)
        temp_path: Path | None = None
        try:
            info = channel.check(current_revision=updater.installed_remote_revision)
            if not info.available:
                return {"ok": True, "updated": False, "remote": info.as_dict(), "message": "Already up to date"}
            staged = updater.latest_staged()
            if staged is None or staged.remote_revision != info.revision:
                temp_path = channel.download(info.revision)
                staged = updater.stage(
                    temp_path,
                    original_filename=f"motily-{info.short_revision}.zip",
                    remote_revision=info.revision,
                    remote_source=f"github:{info.repository}@{info.branch}",
                )
            receipt = updater.apply(staged.update_id)
            return {
                "ok": True,
                "updated": True,
                "remote": info.as_dict(),
                "staged": staged.as_dict(),
                "receipt": receipt,
            }
        except RemoteUpdateError as exc:
            raise _safe_error(exc)
        except Exception as exc:
            raise _safe_error(exc)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    @app.post("/api/dev-update/stage")
    def dev_update_stage(request: Request, file: UploadFile = File(...)):
        _require_local_update(request)
        raw_name = (file.filename or "update.zip").replace("\\", "/")
        if not raw_name.lower().endswith(".zip"):
            raise HTTPException(400, "Development update must be a .zip file")
        temp_path = _save_upload(file)
        try:
            staged = updater.stage(temp_path, original_filename=raw_name.rsplit("/", 1)[-1])
            return {"ok": True, "staged": staged.as_dict(), "current_version": updater.current_version}
        except HTTPException:
            raise
        except Exception as exc:
            raise _safe_error(exc)
        finally:
            _cleanup_upload(temp_path)

    @app.post("/api/dev-update/apply/{update_id}")
    def dev_update_apply(update_id: str, request: Request):
        _require_local_update(request)
        try:
            return {"ok": True, **updater.apply(update_id)}
        except Exception as exc:
            raise _safe_error(exc)

    @app.post("/api/dev-update/restart")
    def dev_update_restart(request: Request):
        _require_local_update(request)
        cmd = restart_command()
        if not cmd:
            raise HTTPException(409, "Studio restart is unavailable for this launch mode; restart Motily Studio manually")
        if os.environ.get("MOTILY_STUDIO_DISABLE_SELF_RESTART") == "1":
            raise HTTPException(409, "Studio self-restart is disabled by MOTILY_STUDIO_DISABLE_SELF_RESTART")
        import threading
        def _restart() -> None:
            spawn_restart_helper(cmd, cwd=Path.cwd())
            os._exit(0)
        threading.Timer(0.6, _restart).start()
        return {"ok": True, "restarting": True}

    @app.get("/api/projects")
    def projects():
        return [p.model_dump(mode="json") for p in studio.projects()]

    @app.get("/api/projects/{project_id}")
    def project(project_id: str):
        try:
            return studio.project(_uuid(project_id, "project id"))
        except HTTPException:
            raise
        except Exception as exc:
            raise _safe_error(exc)

    @app.get("/api/projects/{project_id}/readiness")
    def readiness(project_id: str):
        try:
            return studio.readiness(_uuid(project_id, "project id"))
        except HTTPException:
            raise
        except Exception as exc:
            raise _safe_error(exc)

    @app.get("/api/projects/{project_id}/evidence")
    def evidence(project_id: str):
        try:
            return studio.evidence_status(_uuid(project_id, "project id"))
        except HTTPException:
            raise
        except Exception as exc:
            raise _safe_error(exc)

    @app.get("/api/projects/{project_id}/audio")
    def audio(project_id: str):
        try:
            return studio.audio_status(_uuid(project_id, "project id"))
        except HTTPException:
            raise
        except Exception as exc:
            raise _safe_error(exc)

    @app.post("/api/productions", response_model=StudioActionResult)
    def start_production(request: StartProductionRequest):
        service = composition.service
        if request.target not in {"idea", "research", "script", "plans", "video", "qc", "final"}:
            raise HTTPException(400, f"Unknown target: {request.target}")
        try:
            if request.project_id:
                project_id = _uuid(request.project_id, "project id")
            else:
                if not request.brief or not request.brief.strip():
                    raise HTTPException(400, "A new production requires a non-empty brief")
                project_id = service.create_project(title=(request.title or request.brief[:80]))

            idea_input = None
            if request.brief is not None:
                idea_input = service.build_initial_idea_input(
                    project_id,
                    brief=request.brief,
                    domain=request.domain,
                    discovery_mode=request.discovery_mode,
                    additional_context=request.additional_context,
                )
            ctx = _context(composition, project_id)
            if idea_input is not None and ctx.initial_idea_input is None:
                ctx.initial_idea_input = idea_input
            run = service.start(project_id, request.target, ctx)
            return StudioActionResult(message="Production run started", run=run_summary(run))
        except HTTPException:
            raise
        except Exception as exc:
            raise _safe_error(exc)

    @app.post("/api/runs/{run_id}/resume", response_model=StudioActionResult)
    def resume(run_id: str):
        try:
            rid = _uuid(run_id, "run id")
            existing = composition.service.status(rid)
            ctx = _context(composition, existing.project_id)
            run = composition.service.resume(rid, ctx)
            return StudioActionResult(message="Production resumed", run=run_summary(run))
        except HTTPException:
            raise
        except Exception as exc:
            raise _safe_error(exc)

    @app.get("/api/runs/{run_id}")
    def run_status(run_id: str):
        try:
            run = composition.service.status(_uuid(run_id, "run id"))
            return run_summary(run).model_dump(mode="json")
        except HTTPException:
            raise
        except Exception as exc:
            raise _safe_error(exc)

    @app.post("/api/runs/{run_id}/approve", response_model=StudioActionResult)
    def approve(run_id: str, request: GateDecisionRequest):
        try:
            rid = _uuid(run_id, "run id")
            outcome = composition.service.approve_pending(rid, request.gate, note=request.note)
            run = composition.service.status(rid)
            return StudioActionResult(
                message=f"Approved {outcome.gate_type.value}",
                run=run_summary(run),
                data={"gate": outcome.gate_type.value, "subject_artifact_id": outcome.subject_artifact_id},
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _safe_error(exc)

    @app.post("/api/runs/{run_id}/reject", response_model=StudioActionResult)
    def reject(run_id: str, request: GateDecisionRequest):
        try:
            rid = _uuid(run_id, "run id")
            outcome = composition.service.reject_pending(rid, request.gate, note=request.note)
            run = composition.service.status(rid)
            return StudioActionResult(
                message=f"Rejected {outcome.gate_type.value}",
                run=run_summary(run),
                data={"gate": outcome.gate_type.value, "subject_artifact_id": outcome.subject_artifact_id},
            )
        except GateRejectionNotSupportedError as exc:
            raise HTTPException(409, str(exc)) from None
        except HTTPException:
            raise
        except Exception as exc:
            raise _safe_error(exc)

    def _save_upload(upload: UploadFile) -> Path:
        raw_name = (upload.filename or "upload.bin").replace("\\", "/")
        basename = raw_name.rsplit("/", 1)[-1] or "upload.bin"
        temp_dir = Path(mkdtemp(prefix="motily_studio_"))
        temp_path = temp_dir / basename
        total = 0
        max_bytes = 2 * 1024 * 1024 * 1024
        with temp_path.open("wb") as tmp:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(413, "Upload exceeds the 2 GiB local Studio limit")
                tmp.write(chunk)
        return temp_path

    def _cleanup_upload(path: Path) -> None:
        shutil.rmtree(path.parent, ignore_errors=True)

    @app.post("/api/projects/{project_id}/evidence/bind")
    def bind_evidence(project_id: str, source_id: str = Form(...), file: UploadFile = File(...)):
        temp_path = _save_upload(file)
        try:
            pid = _uuid(project_id, "project id")
            asset = studio.evidence.bind(pid, source_id, temp_path)
            return {"ok": True, "source_id": source_id, "asset_id": str(asset.id), "filename": file.filename}
        except HTTPException:
            raise
        except Exception as exc:
            raise _safe_error(exc)
        finally:
            _cleanup_upload(temp_path)

    @app.post("/api/projects/{project_id}/audio/music")
    def bind_music(project_id: str, file: UploadFile = File(...)):
        temp_path = _save_upload(file)
        try:
            asset = studio.audio.bind_music(_uuid(project_id, "project id"), temp_path)
            return {"ok": True, "asset_id": str(asset.id), "filename": file.filename}
        except HTTPException:
            raise
        except Exception as exc:
            raise _safe_error(exc)
        finally:
            _cleanup_upload(temp_path)

    @app.post("/api/projects/{project_id}/audio/sfx")
    def bind_sfx(project_id: str, reference: str = Form(...), file: UploadFile = File(...)):
        temp_path = _save_upload(file)
        try:
            asset = studio.audio.bind_sfx(_uuid(project_id, "project id"), reference, temp_path)
            return {"ok": True, "asset_id": str(asset.id), "reference": reference, "filename": file.filename}
        except HTTPException:
            raise
        except Exception as exc:
            raise _safe_error(exc)
        finally:
            _cleanup_upload(temp_path)

    return app
