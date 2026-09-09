"""Phase 33 requirement #30: app/orchestration/ must never import
app.llm/provider packages -- the orchestrator only ever calls a
NodeAdapter's own load_current/is_fresh/execute/gate_ok, never choosing
a prompt or model itself.

Runs in a fresh subprocess -- within this test process, an earlier test
module may have already imported app.llm, which would make an in-process
sys.modules check meaningless (mirrors
tests/test_timeline_builder.py::test_no_llm_dependency_in_fresh_subprocess).

Covers the entire package, including app/orchestration/adapters.py: every
adapter Phase 33 actually wires delegates to a renderer/builder already
confirmed LLM-free, so no carve-out is needed here.

Phase 34 requirement #43 extends this: the twelve new upstream engines
(IDEA through PACKAGING_P1) DO import app.llm -- that dependency is
confined entirely to app/production_adapters/upstream.py, deliberately
OUTSIDE app/orchestration/. The test below proves that boundary is real
(the import genuinely happens there) rather than merely proving an
absence everywhere, which alone wouldn't distinguish "correctly isolated"
from "accidentally never wired at all."
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_no_llm_or_provider_dependency_in_fresh_subprocess():
    project_root = Path(__file__).resolve().parent.parent
    script = (
        "import sys\n"
        "import app.orchestration.adapters\n"
        "import app.orchestration.runner\n"
        "import app.orchestration.gates\n"
        "import app.orchestration.registry\n"
        "loaded = [name for name in sys.modules "
        "if name == 'app.llm' or name.startswith('app.llm.')]\n"
        "print(','.join(loaded))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=str(project_root), capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_orchestration_source_files_never_mention_app_llm():
    """A stricter static check alongside the runtime one above: no source
    file directly under app/orchestration/ may even textually reference
    app.llm, so a future edit can't reintroduce the dependency behind a
    lazy/deferred import that the subprocess check above would miss."""
    orchestration_dir = Path(__file__).resolve().parent.parent / "app" / "orchestration"
    for path in orchestration_dir.glob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            assert not stripped.startswith("import app.llm") and "from app.llm" not in stripped, (
                f"{path.name} imports app.llm: {stripped!r}"
            )


def test_production_adapters_upstream_genuinely_imports_app_llm_in_a_fresh_subprocess():
    """The positive half of requirement #43: app/production_adapters/
    upstream.py is EXPECTED to import app.llm (that is the entire reason
    this package exists outside app/orchestration/) -- proving the import
    actually happens confirms the isolation above is a real architectural
    boundary, not an accident of nothing importing app.llm anywhere."""
    project_root = Path(__file__).resolve().parent.parent
    script = (
        "import sys\n"
        "import app.production_adapters.upstream\n"
        "loaded = [name for name in sys.modules "
        "if name == 'app.llm' or name.startswith('app.llm.')]\n"
        "print(','.join(loaded))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=str(project_root), capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "app.llm.provider" in result.stdout.strip()
