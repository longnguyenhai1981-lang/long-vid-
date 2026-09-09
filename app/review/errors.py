"""Errors for the human-review application layer. Distinct from EngineStateError:
these guard human review actions, not engine execution."""

from __future__ import annotations


class ReviewStateError(Exception):
    """Raised when a review action is attempted while the project is in the wrong state."""


class MissingReviewArtifactError(Exception):
    """Raised when a review action's stage has no valid reference/artifact to act on
    (e.g. IDEA_REVIEW with no IdeaCandidate, or FEASIBILITY with no FeasibilityReport)."""


class FeasibilityDecisionMismatchError(Exception):
    """Raised when a human Feasibility decision does not match the stored
    FeasibilityReport's status. Phase 6 does not support overriding the engine's
    recommendation -- that would be a future, explicit override feature."""


class PackagingRiskTooHighError(Exception):
    """Raised by approve_packaging_p0 when the stored PackagingPrototype has
    risk_of_misleading == HIGH. A HIGH-risk prototype is a legitimate,
    honestly-reported engine outcome (see PackagingP0Engine, Phase 9) -- it is
    persisted and visible, but a human cannot approve it forward to SCRIPT in
    that form. The human must revise or send it back to research first."""


class ScriptVerificationNotPassedError(Exception):
    """Raised by accept_script_verification and approve_final_script when the
    current ScriptVerificationReport.status is not PASS. A non-PASS report is
    a legitimate, honestly-reported engine outcome (see
    ScriptVerificationEngine, Phase 11) -- it is persisted and visible, but a
    human cannot accept or give final approval to a script in that state. The
    human must send it for rewrite, or back to narrative/research, first."""


class StaleScriptVerificationError(Exception):
    """Phase 12 defect fix. Raised by accept_script_verification and
    approve_final_script when the current ScriptVerificationReport was
    generated for a ScriptPlan other than the one project.script_plan_id
    currently references (e.g. the script was rewritten after verification
    but not re-verified). ScriptVerificationReport carries no id and no
    reference back to the ScriptPlan it audited (locked Phase 1 contract),
    so freshness is established indirectly, from the most recent successful
    script_verification_engine ModuleRun's recorded input_ids. See
    docs/CORE_MVP_AUDIT_v0.1.md for the full defect writeup."""


class StalePackagingPrototypeError(Exception):
    """Phase 12 defect fix. Raised by approve_packaging_p0 when the current
    PackagingPrototype was generated for a NarrativePlan other than the one
    project.narrative_plan_id currently references (e.g. the narrative was
    revised after packaging but packaging was never regenerated).
    PackagingPrototype carries no reference back to the NarrativePlan it was
    built from (locked contract), so freshness is established indirectly,
    from the most recent successful packaging_p0_engine ModuleRun's recorded
    input_ids. See docs/CORE_MVP_AUDIT_v0.1.md for the full defect writeup."""
