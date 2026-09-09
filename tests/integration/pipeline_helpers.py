"""Phase 12: shared coherent fixture DATA for the core-MVP integration suite.

This module holds only static builder functions (canned LLM-response JSON,
canned retrieval responses, tiny settings constructors) -- deliberately no
orchestration logic. Every actual engine.run()/review-service call sequence
lives in the test files and in tests/integration/conftest.py's fixtures, so
the integration suite proves the real public operations compose, rather than
hiding that behind a new pipeline/orchestrator abstraction (Phase 12, section
30: no monolithic orchestrator).

All values describe one internally coherent project -- the Tacoma Narrows
Bridge flutter story -- so claim ids, narrative-node ids, and script
claim/node references all line up the way a real project's would, per Phase
12 section 29.
"""

from __future__ import annotations

import json

from app.llm.config import LLMSettings
from app.llm.models import LLMResponse
from app.research.models import RetrievedSource, ResearchQuery, ResearchSearchResponse

IDEA_TOPIC = "Tacoma Narrows Bridge"
IDEA_CENTRAL_QUESTION = "Why did a sturdy bridge collapse in mild wind?"
IDEA_PHYSICS_CORE = "Self-excited aeroelastic flutter"

R0_URLS = [
    "https://r0.example/collapse-report",
    "https://r0.example/flutter-explainer",
    "https://r0.example/aeroelastic-overview",
]

R1_URLS = [
    "https://r1.example/investigation-report",
    "https://r1.example/flutter-mechanism",
    "https://r1.example/original-technical-report",
    "https://r1.example/resonance-misconception",
    "https://r1.example/timeline",
]


def response(text: str) -> LLMResponse:
    return LLMResponse(text=text, provider="fake", model="fake-model")


def llm_settings(max_structured_retries: int = 2) -> LLMSettings:
    return LLMSettings(
        provider="fake", default_model="fake-model", max_structured_retries=max_structured_retries
    )


# ---------------------------------------------------------------------------
# Idea (IdeaEngine)
# ---------------------------------------------------------------------------


def idea_candidate_json() -> str:
    return json.dumps(
        {
            "topic": IDEA_TOPIC,
            "central_question": IDEA_CENTRAL_QUESTION,
            "abt": {
                "and_context": "Engineers believed the new suspension bridge was perfectly safe",
                "but_complication": "It twisted itself apart in wind far below its design limit",
                "therefore_investigation": "Investigate the hidden aerodynamic mechanism",
            },
            "primary_payoff": "REVERSAL",
            "physics_core": IDEA_PHYSICS_CORE,
            "audience_prerequisite": "none",
            "brand_fit": {"status": "PASS", "reason": "Fits the science-mystery format"},
            "general_audience_gate": {"status": "PASS", "reason": "No prerequisite knowledge needed"},
            "longform_potential": {"status": "PASS", "reason": "Rich investigative arc"},
        }
    )


def idea_candidate_json_variant() -> str:
    """A second, distinct IdeaCandidate -- used by the revise/rerun recovery test
    to prove the *new* artifact becomes current, not a copy of the first."""
    return json.dumps(
        {
            "topic": IDEA_TOPIC,
            "central_question": "How did engineers finally explain the Tacoma Narrows collapse?",
            "abt": {
                "and_context": "The bridge's collapse baffled engineers for years",
                "but_complication": "The accepted resonance explanation turned out to be wrong",
                "therefore_investigation": "Trace how flutter theory replaced the resonance myth",
            },
            "primary_payoff": "DISCOVERY",
            "physics_core": IDEA_PHYSICS_CORE,
            "audience_prerequisite": "none",
            "brand_fit": {"status": "PASS", "reason": "Fits the science-mystery format"},
            "general_audience_gate": {"status": "PASS", "reason": "No prerequisite knowledge needed"},
            "longform_potential": {"status": "PASS", "reason": "Strong historical arc"},
        }
    )


# ---------------------------------------------------------------------------
# R0 Research (R0ResearchEngine) -- 3 queries: topic, central_question, physics_core
# ---------------------------------------------------------------------------


def r0_search_responses() -> list[ResearchSearchResponse]:
    titles = ["Tacoma Narrows collapse report", "Why bridges flutter", "Aeroelastic flutter overview"]
    return [
        ResearchSearchResponse(
            query=ResearchQuery(query=f"r0-query-{i}"),
            results=[RetrievedSource(title=title, url=url, snippet="Snippet.")],
        )
        for i, (title, url) in enumerate(zip(titles, R0_URLS), start=1)
    ]


def research_r0_json() -> str:
    return json.dumps(
        {
            # idea_id is required for the JSON to parse against ResearchR0, but
            # R0ResearchEngine overrides it deterministically from the real
            # idea.id after parsing -- any syntactically valid UUID works here.
            "idea_id": "00000000-0000-0000-0000-000000000000",
            "topic_valid": True,
            "credible_sources_available": True,
            "story_material_available": True,
            "physics_material_available": True,
            "initial_findings": ["Flutter is a documented, well-studied bridge failure mode"],
            "candidate_sources": [R0_URLS[0], R0_URLS[1]],
            "major_risks": [],
            "recommendation": "CONTINUE",
        }
    )


# ---------------------------------------------------------------------------
# Feasibility (FeasibilityEngine) -- all five axes PASS so the deterministic
# derive_overall_status() lands on PASS.
# ---------------------------------------------------------------------------


def feasibility_report_json(overall: str = "PASS") -> str:
    axis = {"status": overall, "reason": "Strong across the board"}
    return json.dumps(
        {
            "status": overall,
            "audience": axis,
            "science": axis,
            "narrative": axis,
            "visual": axis,
            "production": {"status": overall, "estimated_complexity": "LOW", "reason": "Standard explainer production"},
        }
    )


# ---------------------------------------------------------------------------
# R1 Research (R1ResearchEngine) -- 5 queries: central_question, physics_core,
# "<topic> original paper official technical report", "<topic> common
# misconception" (REVERSAL payoff), "<topic> history timeline".
# ---------------------------------------------------------------------------


def r1_search_responses() -> list[ResearchSearchResponse]:
    titles = [
        "Investigation report",
        "Flutter mechanism paper",
        "Original technical report",
        "Resonance misconception debunked",
        "Timeline of the collapse",
    ]
    return [
        ResearchSearchResponse(
            query=ResearchQuery(query=f"r1-query-{i}"),
            results=[RetrievedSource(title=title, url=url, snippet="Snippet.")],
        )
        for i, (title, url) in enumerate(zip(titles, R1_URLS), start=1)
    ]


def research_package_json() -> str:
    return json.dumps(
        {
            "central_question": IDEA_CENTRAL_QUESTION,
            "executive_summary": "Self-excited aeroelastic flutter caused the deck to twist itself apart.",
            "timeline": ["1940-07: Bridge opens", "1940-11: Bridge collapses in ~64 km/h wind"],
            "physics_core": IDEA_PHYSICS_CORE,
            "claims": [
                {
                    "claim_id": "C001",
                    "claim": "The collapse was caused by self-excited aeroelastic flutter, not simple resonance.",
                    "status": "SAFE",
                    "confidence": "HIGH",
                    "source_ids": ["S001"],
                },
                {
                    "claim_id": "C002",
                    "claim": "Torsional flutter developed once the deck's twisting motion coupled with the wind.",
                    "status": "SAFE",
                    "confidence": "HIGH",
                    "source_ids": ["S001"],
                },
            ],
            "disputed_points": [],
            "misconceptions": ["It is commonly misattributed to simple mechanical resonance rather than flutter."],
            "simplification_boundary": (
                "1. safe_model: torsional flutter as a self-reinforcing twist-wind coupling. "
                "2. allowed_simplifications: omit detailed modal analysis. "
                "3. omitted_complexity: full aeroelastic equations. "
                "4. dangerous_oversimplifications: none."
            ),
            "prohibited_claims": [],
            "sources": [
                {
                    "source_id": "S001",
                    "title": "Aeroelastic flutter mechanism report",
                    "url": R1_URLS[1],
                    "type": "technical_report",
                    "quality_tier": 1,
                    "authoritative": True,
                    "supports_claims": ["C001", "C002"],
                }
            ],
        }
    )


# ---------------------------------------------------------------------------
# Narrative (NarrativeEngine) -- Q0 -> Q1 -> Q2 (Q2 terminal), claim_ids only
# ever reference C001/C002.
# ---------------------------------------------------------------------------


def narrative_plan_json() -> str:
    return json.dumps(
        {
            "central_question": IDEA_CENTRAL_QUESTION,
            "scqa": {
                "situation": "A brand-new suspension bridge opens to fanfare in 1940.",
                "complication": "Months later it twists itself apart in ordinary wind.",
                "question": IDEA_CENTRAL_QUESTION,
                "answer": "Self-excited aeroelastic flutter, not resonance.",
            },
            "opening": "MYSTERY_FIRST",
            "question_ladder": [
                {
                    "id": "Q0",
                    "question": "Why did the deck start twisting?",
                    "why_viewer_cares": "It looks impossible for solid steel to do this.",
                    "partial_answer": "The deck's shape let wind forces couple with its motion.",
                    "claim_ids": ["C001"],
                    "creates_next_question": "Q1",
                    "information_gap": "What made that coupling self-reinforcing?",
                },
                {
                    "id": "Q1",
                    "question": "Why didn't the twisting damp itself out?",
                    "why_viewer_cares": "Real bridges flex in wind all the time without collapsing.",
                    "partial_answer": "The motion and the wind forces fed each other, growing instead of shrinking.",
                    "claim_ids": ["C002"],
                    "creates_next_question": "Q2",
                    "information_gap": "What finally broke the structure?",
                },
                {
                    "id": "Q2",
                    "question": "What finally tore the bridge apart?",
                    "why_viewer_cares": "It's the payoff moment viewers are waiting for.",
                    "partial_answer": "The growing torsional flutter exceeded the deck's structural limit.",
                    "claim_ids": [],
                    "creates_next_question": None,
                    "information_gap": "none left",
                },
            ],
            "ti_role": "Investigator",
            "physics_entry_points": ["torsional flutter", "self-excited oscillation"],
            "ending": "Callback to the opening image of the bridge looking perfectly ordinary.",
            "claim_ids_used": ["C001", "C002"],
        }
    )


# ---------------------------------------------------------------------------
# Packaging P0 (PackagingP0Engine)
# ---------------------------------------------------------------------------


def packaging_prototype_json(risk: str = "LOW") -> str:
    return json.dumps(
        {
            "promise": "A sturdy bridge tore itself apart in wind too mild to explain it.",
            "title_direction": "The bridge that shook itself to pieces",
            "thumbnail_conflict": "CAN WIND REALLY DO THIS?",
            "viewer_expectation": "An investigation into a real, still-relevant aerodynamic mechanism.",
            "risk_of_misleading": risk,
        }
    )


# ---------------------------------------------------------------------------
# Script (ScriptEngine) -- beats walk Q0 -> Q1 -> Q2 in order; every claim_id
# used is C001 or C002.
# ---------------------------------------------------------------------------


def script_plan_json(duration: int = 520) -> str:
    return json.dumps(
        {
            "estimated_duration_seconds": duration,
            "beats": [
                {
                    "beat_id": "B001",
                    "narrative_node": "Q0",
                    "narrative_function": "QUESTION",
                    "lines": [
                        {
                            "line_id": "L001",
                            "text": "Cây cầu này từng được coi là một kỳ quan kỹ thuật.",
                            "function": "INFORM",
                            "claim_ids": [],
                        },
                        {
                            "line_id": "L002",
                            "text": "Vậy tại sao nó lại tự xoắn mình đến vỡ vụn?",
                            "function": "QUESTION",
                            "claim_ids": ["C001"],
                        },
                    ],
                },
                {
                    "beat_id": "B002",
                    "narrative_node": "Q1",
                    "narrative_function": "ESCALATE",
                    "lines": [
                        {
                            "line_id": "L003",
                            "text": "Cầu nào cũng rung nhẹ trong gió, chuyện đó bình thường.",
                            "function": "INFORM",
                            "claim_ids": [],
                        },
                        {
                            "line_id": "L004",
                            "text": "Nhưng lần này, chuyển động không hề tắt dần.",
                            "function": "ESCALATE",
                            "claim_ids": ["C002"],
                        },
                    ],
                },
                {
                    "beat_id": "B003",
                    "narrative_node": "Q2",
                    "narrative_function": "REVEAL",
                    "lines": [
                        {
                            "line_id": "L005",
                            "text": "Đó chính là hiện tượng rung động khí động học tự kích thích.",
                            "function": "REVEAL",
                            "claim_ids": ["C001", "C002"],
                        },
                        {
                            "line_id": "L006",
                            "text": "Không phải cộng hưởng như nhiều người vẫn tưởng.",
                            "function": "CLARIFY",
                            "claim_ids": ["C001"],
                        },
                    ],
                },
            ],
            "claim_coverage": ["C001", "C002"],
            "unmapped_claims": [],
            "qa_status": "PASS",
        }
    )


# ---------------------------------------------------------------------------
# Script Verification (ScriptVerificationEngine)
# ---------------------------------------------------------------------------


def verification_report_json(status: str = "PASS") -> str:
    if status == "PASS":
        return json.dumps(
            {
                "status": "PASS",
                "unsupported_lines": [],
                "overstated_lines": [],
                "dangerous_simplifications": [],
                "recommended_rewrites": [],
            }
        )
    return json.dumps(
        {
            "status": status,
            "unsupported_lines": ["L005: needs stronger sourcing before this can pass"],
            "overstated_lines": [],
            "dangerous_simplifications": [],
            "recommended_rewrites": ["L005: soften the claim until better sourced"],
        }
    )
