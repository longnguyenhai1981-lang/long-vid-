"""Phase 35 requirement #13: stable process exit codes for `motily`.

These are a scripting contract -- a CI pipeline or shell script branches
on them, so once published they must never be renumbered.

    0  requested operation completed (SUCCEEDED, or a read-only command
       such as `status`/`runs` that completed normally)
    2  the production run is WAITING_APPROVAL -- not a failure, but
       distinguishable from full success for scripting
    3  the production run is BLOCKED (a domain/business outcome, or a
       rejected gate -- never a technical crash)
    4  the production run is FAILED (a technical execution failure in
       some node's own adapter/engine)
    5  invalid CLI input (bad target/gate alias, malformed UUID, missing
       required brief, unknown run/project id)
    6  an infrastructure/runtime dependency is unavailable (e.g. the
       database path is not writable, ffmpeg/ffprobe missing) -- checked
       proactively by `motily doctor`, never inferred from a node FAILED
       status (which is always exit code 4, even if its own root cause
       happens to be an infrastructure problem inside that one engine)
"""

from __future__ import annotations

from app.orchestration.models import ProductionRunStatus

EXIT_SUCCESS = 0
EXIT_WAITING_APPROVAL = 2
EXIT_BLOCKED = 3
EXIT_FAILED = 4
EXIT_INVALID_INPUT = 5
EXIT_INFRASTRUCTURE_UNAVAILABLE = 6

_STATUS_EXIT_CODES = {
    ProductionRunStatus.SUCCEEDED: EXIT_SUCCESS,
    ProductionRunStatus.WAITING_APPROVAL: EXIT_WAITING_APPROVAL,
    ProductionRunStatus.BLOCKED: EXIT_BLOCKED,
    ProductionRunStatus.FAILED: EXIT_FAILED,
}


def exit_code_for_status(status: ProductionRunStatus) -> int:
    """RUNNING is never a terminal status returned by run_until()/resume()
    (Phase 33's own contract) -- if it were ever seen here, that itself
    is an infrastructure-level inconsistency, not a normal outcome."""
    return _STATUS_EXIT_CODES.get(status, EXIT_INFRASTRUCTURE_UNAVAILABLE)
