"""R0ResearchEngine: cheap discovery research -- the second vertical slice.

R0_RESEARCH -> deterministic search queries -> ResearchRetriever -> dedupe
evidence -> generate_structured(ResearchR0) -> business URL cross-check
(one bounded correction attempt) -> persist artifact -> update
Project.research_r0_id -> R0_RESEARCH -> FEASIBILITY -> persist ModuleRun.

No Feasibility Engine, no orchestrator, no BaseEngine framework.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine

from app.engines.errors import EngineStateError
from app.engines.idea.models import IDEA_CANDIDATE_ARTIFACT_TYPE
from app.engines.research_r0.errors import MissingIdeaArtifactError, ResearchSourceHallucinationError
from app.engines.research_r0.models import (
    RESEARCH_R0_ARTIFACT_TYPE,
    R0ResearchInput,
    R0ResearchResult,
)
from app.engines.research_r0.prompt import (
    build_source_correction_request,
    build_system_prompt,
    build_user_prompt,
)
from app.engines.research_r0.queries import build_search_queries
from app.llm.config import LLMSettings
from app.llm.models import LLMRequest
from app.llm.provider import LLMProvider
from app.llm.structured import generate_structured
from app.models.common import ModuleRunStatus, ProjectState
from app.models.idea import IdeaCandidate
from app.models.module_run import ModuleRun
from app.models.project import Project
from app.models.research import ResearchR0
from app.research.models import RetrievedSource
from app.research.retriever import ResearchRetriever
from app.storage import artifacts as artifact_storage
from app.storage import module_runs as module_run_storage
from app.storage import projects as project_storage
from app.storage.errors import ArtifactNotFoundError, ArtifactValidationError

MODULE_NAME = "research_r0_engine"
MODULE_VERSION = "0.1"

# "Maximum business-correction attempts: 1" -- exactly one extra provider call,
# with no further JSON/schema retries of its own, if the business URL check fails.
MAX_BUSINESS_CORRECTION_ATTEMPTS = 1


class R0ResearchEngine:
    def __init__(
        self,
        db_engine: Engine,
        provider: LLMProvider,
        retriever: ResearchRetriever,
        llm_settings: LLMSettings,
        *,
        project_repo=project_storage,
        artifact_repo=artifact_storage,
        module_run_repo=module_run_storage,
    ):
        self._db_engine = db_engine
        self._provider = provider
        self._retriever = retriever
        self._llm_settings = llm_settings
        self._project_repo = project_repo
        self._artifact_repo = artifact_repo
        self._module_run_repo = module_run_repo

    def run(self, engine_input: R0ResearchInput) -> R0ResearchResult:
        project = self._project_repo.get_project(self._db_engine, engine_input.project_id)
        if project.state is not ProjectState.R0_RESEARCH:
            raise EngineStateError(
                f"R0ResearchEngine requires project state R0_RESEARCH, got {project.state.value}"
            )
        idea = self._load_and_verify_idea(project)

        run_record = ModuleRun(
            project_id=engine_input.project_id,
            module=MODULE_NAME,
            module_version=MODULE_VERSION,
            started_at=datetime.now(timezone.utc),
            input_ids=[str(idea.id)],
            status=ModuleRunStatus.RUNNING,
        )
        self._module_run_repo.save_module_run(self._db_engine, run_record)

        try:
            queries = build_search_queries(idea)
            evidence = self._retrieve_evidence(queries)

            research, generation_attempts, response = self._generate_research(
                idea, evidence, engine_input.additional_context
            )

            self._artifact_repo.save_artifact(
                self._db_engine, engine_input.project_id, RESEARCH_R0_ARTIFACT_TYPE, research
            )
            self._project_repo.update_artifact_reference(
                self._db_engine, engine_input.project_id, "research_r0_id", research.id
            )
            self._project_repo.update_project_state(
                self._db_engine, engine_input.project_id, ProjectState.FEASIBILITY
            )
        except Exception as exc:
            # Broad on purpose: any failure from retrieval through the state
            # transition must be recorded as a FAILED audit record and re-raised
            # unchanged -- mirrors IdeaEngine (Phase 4).
            self._module_run_repo.save_module_run(
                self._db_engine, _with_failure(run_record, exc)
            )
            raise

        self._module_run_repo.save_module_run(
            self._db_engine, _with_success(run_record, research.id)
        )

        return R0ResearchResult(
            research=research,
            module_run_id=run_record.run_id,
            generation_attempts=generation_attempts,
            provider=response.provider,
            model=response.model,
            token_usage=response.usage,
            retrieval_query_count=len(queries),
            retrieved_source_count=len(evidence),
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

    def _retrieve_evidence(self, queries) -> list[RetrievedSource]:
        all_results: list[RetrievedSource] = []
        for query in queries:
            response = self._retriever.search(query)
            all_results.extend(response.results)
        return _deduplicate_by_url(all_results)

    def _generate_research(self, idea: IdeaCandidate, evidence, additional_context):
        request = LLMRequest(
            system_prompt=build_system_prompt(idea),
            user_prompt=build_user_prompt(evidence, additional_context),
            model=self._llm_settings.default_model,
            temperature=self._llm_settings.default_temperature,
            max_output_tokens=self._llm_settings.default_max_output_tokens,
        )
        generation = generate_structured(
            self._provider, request, ResearchR0, max_retries=self._llm_settings.max_structured_retries
        )
        research = generation.value.model_copy(update={"idea_id": idea.id})
        attempts = generation.attempts
        response = generation.response

        unknown_urls = _find_unknown_urls(research, evidence)
        if unknown_urls:
            correction_request = build_source_correction_request(request, unknown_urls, evidence)
            correction_generation = generate_structured(
                self._provider,
                correction_request,
                ResearchR0,
                max_retries=MAX_BUSINESS_CORRECTION_ATTEMPTS - 1,
            )
            research = correction_generation.value.model_copy(update={"idea_id": idea.id})
            attempts += correction_generation.attempts
            response = correction_generation.response

            unknown_urls = _find_unknown_urls(research, evidence)
            if unknown_urls:
                raise ResearchSourceHallucinationError(unknown_urls)

        return research, attempts, response


def _deduplicate_by_url(sources: list[RetrievedSource]) -> list[RetrievedSource]:
    seen_urls: set[str] = set()
    deduped: list[RetrievedSource] = []
    for source in sources:
        if source.url not in seen_urls:
            seen_urls.add(source.url)
            deduped.append(source)
    return deduped


def _find_unknown_urls(research: ResearchR0, evidence: list[RetrievedSource]) -> list[str]:
    retrieved_urls = {source.url for source in evidence}
    return [url for url in research.candidate_sources if url not in retrieved_urls]


def _with_success(run_record: ModuleRun, research_id) -> ModuleRun:
    return ModuleRun(
        run_id=run_record.run_id,
        project_id=run_record.project_id,
        module=run_record.module,
        module_version=run_record.module_version,
        started_at=run_record.started_at,
        completed_at=datetime.now(timezone.utc),
        input_ids=run_record.input_ids,
        output_id=str(research_id),
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
