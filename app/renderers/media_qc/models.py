"""MediaQCRenderer input/output contracts.

MediaQCReport (app/media_qc/models.py) is the single approved business
output -- this is a thin wrapper around it, not a competing schema.
"""

from __future__ import annotations

from uuid import UUID

from app.media_qc.models import MediaQCReport
from app.models.common import MotilyModel

MEDIA_QC_REPORT_ARTIFACT_TYPE = "media_qc_report"


class MediaQCRendererInput(MotilyModel):
    project_id: UUID


class MediaQCRendererResult(MotilyModel):
    report: MediaQCReport
    module_run_id: UUID
