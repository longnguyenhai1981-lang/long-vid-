"""R1ResearchEngine: deep research producing the factual source of truth --
fourth vertical slice.

R1_RESEARCH -> load+verify IdeaCandidate/ResearchR0/FeasibilityReport(PASS)
-> bounded deterministic queries (<=10) -> ResearchRetriever -> dedupe
evidence -> generate_structured(ResearchPackage) -> deterministic
central_question override -> business validation (URL provenance, claim/
source referential integrity, id uniqueness, evidence-by-status) -> one
bounded correction attempt if needed -> persist artifact -> update
Project.research_r1_id -> transition R1_RESEARCH -> NARRATIVE -> persist
ModuleRun.

No Narrative Engine, no orchestrator, no BaseEngine framework.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine

from app.config.loader import GlobalConfig
from app.engines.errors import EngineStateError
from app.engines.feasibility.models import FEASIBILITY_REPORT_ARTIFACT_TYPE
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE
from app.engines.research_r0.models import RESEARCH_R0_ARTIFACT_TYPE
from app.engines.research_r1.errors import (
    FeasibilityNotPassedError,
    MissingFeasibilityArtifactError,
    MissingIdeaArtifactError,
    MissingResearchR0ArtifactError,
    R1BusinessValidationError,
)
from app.engines.research_r1.models import (
    RESEARCH_R1_ARTIFACT_TYPE,
    R1ResearchInput,
    R1ResearchResult,
)
from app.engines.research_r1.prompt import (
    build_correction_request,
    build_system_prompt,
    build_user_prompt,
)
from app.engines.research_r1.queries import build_search_queries
from app.engines.research_r1.validation import validate_research_package
from app.llm.config import LLMSettings
from app.llm.models import LLMRequest
from app.llm.provider import LLMProvider
from app.llm.structured import generate_structured
from app.models.common import GateStatus, ModuleRunStatus, ProjectState
from app.models.feasibility import FeasibilityReport
from app.models.idea import IdeaCandidate
from app.models.module_run import ModuleRun
from app.models.project import Project
from app.models.research import ResearchPackage, ResearchR0
from app.research.models import RetrievedSource
from app.research.retriever import ResearchRetriever
from app.storage import artifacts as artifact_storage
from app.storage import module_runs as module_run_storage
from app.storage import projects as project_storage
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError

MODULE_NAME = "research_r1_engine"
MODULE_VERSION = "0.1"

# "Maximum business correction attempts: 1" -- exactly one extra provider call,
# with no further JSON/schema retries of its own, if business validation fails.
MAX_BUSINESS_CORRECTION_ATTEMPTS = 1


class R1ResearchEngine:
    def __init__(
        self,
        db_engine: Engine,
        provider: LLMProvider,
        retriever: ResearchRetriever,
        global_config: GlobalConfig,
        llm_settings: LLMSettings,
        *,
        project_repo=project_storage,
        artifact_repo=artifact_storage,
        module_run_repo=module_run_storage,
    ):
        self._db_engine = db_engine
        self._provider = provider
        self._retriever = retriever
        self._global_config = global_config
        self._llm_settings = llm_settings
        self._project_repo = project_repo
        self._artifact_repo = artifact_repo
        self._module_run_repo = module_run_repo

    def run(self, engine_input: R1ResearchInput) -> R1ResearchResult:
        project = self._project_repo.get_project(self._db_engine, engine_input.project_id)
        if project.state is not ProjectState.R1_RESEARCH:
            raise EngineStateError(
                f"R1ResearchEngine requires project state R1_RESEARCH, got {project.state.value}"
            )
        idea = self._load_and_verify_idea(project)
        research_r0 = self._load_and_verify_research_r0(project)
        feasibility = self._load_and_verify_feasibility(project)
        if feasibility.status is not GateStatus.PASS:
            raise FeasibilityNotPassedError(
                f"R1ResearchEngine requires FeasibilityReport.status == PASS, "
                f"got {feasibility.status.value} for project {project.project_id}"
            )

        run_record = ModuleRun(
            project_id=engine_input.project_id,
            module=MODULE_NAME,
            module_version=MODULE_VERSION,
            started_at=datetime.now(timezone.utc),
            input_ids=[str(idea.id), str(research_r0.id), str(feasibility.id)],
            status=ModuleRunStatus.RUNNING,
        )
        self._module_run_repo.save_module_run(self._db_engine, run_record)

        try:
            queries = build_search_queries(idea, research_r0, feasibility)
            evidence = self._retrieve_evidence(queries)

            package, generation_attempts, response, business_correction_used = (
                self._generate_research_package(
                    idea, research_r0, feasibility, evidence, engine_input.additional_context
                )
            )

            self._artifact_repo.save_artifact(
                self._db_engine, engine_input.project_id, RESEARCH_R1_ARTIFACT_TYPE, package
            )
            self._project_repo.update_artifact_reference(
                self._db_engine, engine_input.project_id, "research_r1_id", package.id
            )
            self._project_repo.update_project_state(
                self._db_engine, engine_input.project_id, ProjectState.NARRATIVE
            )
        except Exception as exc:
            # Broad on purpose: any failure from retrieval through the state
            # transition must be recorded as a FAILED audit record and re-raised
            # unchanged -- mirrors IdeaEngine/R0ResearchEngine/FeasibilityEngine.
            self._module_run_repo.save_module_run(
                self._db_engine, _with_failure(run_record, exc)
            )
            raise

        self._module_run_repo.save_module_run(
            self._db_engine, _with_success(run_record, package.id)
        )

        return R1ResearchResult(
            research=package,
            module_run_id=run_record.run_id,
            generation_attempts=generation_attempts,
            provider=response.provider,
            model=response.model,
            token_usage=response.usage,
            retrieval_query_count=len(queries),
            retrieved_source_count=len(evidence),
            business_correction_used=business_correction_used,
        )

    def _load_and_verify_idea(self, project: Project) -> IdeaCandidate:
        if project.idea_candidate_id is None:
            raise MissingIdeaArtifactError(
                f"Project {project.project_id} has no idea_candidate_id reference"
            )
        try:
            idea = self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, IDEA_CANDIDATE_ARTIFACT_TYPE, IdeaCandidate
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingIdeaArtifactError(
                f"Project {project.project_id} idea_candidate_id references a "
                f"missing or invalid IdeaCandidate artifact"
            ) from exc
        if idea.id != project.idea_candidate_id:
            raise MissingIdeaArtifactError(
                f"Stored IdeaCandidate id does not match project.idea_candidate_id "
                f"for project {project.project_id}"
            )
        return idea

    def _load_and_verify_research_r0(self, project: Project) -> ResearchR0:
        if project.research_r0_id is None:
            raise MissingResearchR0ArtifactError(
                f"Project {project.project_id} has no research_r0_id reference"
            )
        try:
            research = self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, RESEARCH_R0_ARTIFACT_TYPE, ResearchR0
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingResearchR0ArtifactError(
                f"Project {project.project_id} research_r0_id references a "
                f"missing or invalid ResearchR0 artifact"
            ) from exc
        if research.id != project.research_r0_id:
            raise MissingResearchR0ArtifactError(
                f"Stored ResearchR0 id does not match project.research_r0_id "
                f"for project {project.project_id}"
            )
        return research

    def _load_and_verify_feasibility(self, project: Project) -> FeasibilityReport:
        if project.feasibility_id is None:
            raise MissingFeasibilityArtifactError(
                f"Project {project.project_id} has no feasibility_id reference"
            )
        try:
            report = self._artifact_repo.get_artifact(
                self._db_engine, project.project_id, FEASIBILITY_REPORT_ARTIFACT_TYPE, FeasibilityReport
            )
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise MissingFeasibilityArtifactError(
                f"Project {project.project_id} feasibility_id references a "
                f"missing or invalid FeasibilityReport artifact"
            ) from exc
        if report.id != project.feasibility_id:
            raise MissingFeasibilityArtifactError(
                f"Stored FeasibilityReport id does not match project.feasibility_id "
                f"for project {project.project_id}"
            )
        return report

    def _retrieve_evidence(self, queries) -> list[RetrievedSource]:
        all_results: list[RetrievedSource] = []
        for query in queries:
            response = self._retriever.search(query)
            all_results.extend(response.results)
        return _deduplicate_by_url(all_results)

    def _generate_research_package(
        self,
        idea: IdeaCandidate,
        research_r0: ResearchR0,
        feasibility: FeasibilityReport,
        evidence: list[RetrievedSource],
        additional_context: str | None,
    ):
        request = LLMRequest(
            system_prompt=build_system_prompt(self._global_config, idea, research_r0, feasibility),
            user_prompt=build_user_prompt(evidence, additional_context),
            model=self._llm_settings.default_model,
            temperature=self._llm_settings.default_temperature,
            max_output_tokens=self._llm_settings.default_max_output_tokens,
        )
        generation = generate_structured(
            self._provider,
            request,
            ResearchPackage,
            max_retries=self._llm_settings.max_structured_retries,
        )
        package = generation.value.model_copy(update={"central_question": idea.central_question})
        attempts = generation.attempts
        response = generation.response
        business_correction_used = False

        issues = validate_research_package(package, evidence)
        if issues:
            business_correction_used = True
            correction_request = build_correction_request(request, issues, evidence)
            correction_generation = generate_structured(
                self._provider,
                correction_request,
                ResearchPackage,
                max_retries=MAX_BUSINESS_CORRECTION_ATTEMPTS - 1,
            )
            package = correction_generation.value.model_copy(
                update={"central_question": idea.central_question}
            )
            attempts += correction_generation.attempts
            response = correction_generation.response

            issues = validate_research_package(package, evidence)
            if issues:
                raise R1BusinessValidationError(issues)

        return package, attempts, response, business_correction_used


def _deduplicate_by_url(sources: list[RetrievedSource]) -> list[RetrievedSource]:
    seen_urls: set[str] = set()
    deduped: list[RetrievedSource] = []
    for source in sources:
        if source.url not in seen_urls:
            seen_urls.add(source.url)
            deduped.append(source)
    return deduped


def _with_success(run_record: ModuleRun, package_id) -> ModuleRun:
    return ModuleRun(
        run_id=run_record.run_id,
        project_id=run_record.project_id,
        module=run_record.module,
        module_version=run_record.module_version,
        started_at=run_record.started_at,
        completed_at=datetime.now(timezone.utc),
        input_ids=run_record.input_ids,
        output_id=str(package_id),
        status=ModuleRunStatus.SUCCESS,
    )


def _with_failure(run_record: ModuleRun, exc: Exception) -> ModuleRun:
    return ModuleRun(
        run_id=run_record.run_id,
        project_id=run_record.project_id,
        module=run_record.module,
        module_version=run_record.module_version,
        started_at=run_record.started_at,
        completed_at=datetime.now(timezone.utc),
        input_ids=run_record.input_ids,
        status=ModuleRunStatus.FAILED,
        error_message=_safe_error_message(exc),
    )


def _safe_error_message(exc: Exception) -> str:
    """A concise, external-only description -- no stack trace, no secrets, no
    hidden reasoning."""
    message = str(exc).strip() or type(exc).__name__
    max_len = 500
    if len(message) > max_len:
        message = message[:max_len] + "... (truncated)"
    return message
