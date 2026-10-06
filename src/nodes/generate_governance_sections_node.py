"""AgentCore Platform v1.0"""

# CMN-C1-068 — GenerateGovernanceSectionsNode
# Inner domain node 3: generate all 7 AI-governance / model-card sections.
#
# Generation is DETERMINISTIC — the manifest declares generation_mode:
# "deterministic" and this node matches it. Section text is assembled directly
# from the model_metadata fields using fixed templates. No language model is
# called and none is configured; there is no hidden LLM path and no faked call.
# Replacing this node with a model-backed generator is the documented extension
# point (see README "Customising"), and doing so means changing generation_mode
# in the manifest as well.
#
# Every value rendered here has already been bounded by InputValidateNode: prose
# is single-line and length-capped, framework ids and metric names are inert, and
# metric values are finite. Nothing in this node re-checks that, deliberately —
# a second pass would make the first one unfalsifiable.
#
# ADVISORY FRAMING: generated text must SUPPORT a compliance assessment; it must
# never assert that a system IS compliant / certified. The PostProcessNode output
# gate rejects certification language, so the generator uses advisory phrasing
# throughout.
#
# Sections produced:
#   1. model_overview
#   2. intended_use_and_scope
#   3. training_data_summary
#   4. performance_characteristics
#   5. limitations_and_risks
#   6. risk_classification
#   7. human_oversight_measures
#
# Inner node — ANONYMOUS trust (review finding 5, corrected 2026-07-02).
# Returns ONLY the state keys this node writes (partial-dict contract).

import logging
from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.guards import finite_number
from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)

# Human-readable framework names for the generated document.
_FRAMEWORK_NAMES: Dict[str, str] = {
    "eu_ai_act": "EU AI Act",
    "iso_42001": "ISO/IEC 42001:2023",
    "fsa_ai_risk": "FSA AI Model Risk Management Framework",
    "meti_dgc": "METI Digital Governance Code",
    "cao_ai_strategy": "Cabinet Office AI Guidelines for Business Operators",
}


def _framework_label(fw: str) -> str:
    return _FRAMEWORK_NAMES.get(fw, fw)


def _format_metric(value: Any) -> str:
    """Render a reported metric.

    InputValidateNode guarantees finite floats reach this node, so the general
    branch is what normally runs. The fallback exists for direct node invocation
    in a test, and routes anything else through the same finite check rather than
    letting a non-finite value reach the page as "nan".
    """
    number = finite_number(value)
    return f"{number:g}" if number is not None else "not reported"


def _section_model_overview(d: Dict[str, Any]) -> str:
    """Generate the Model Overview section."""
    lines = [
        f"Model Name:    {d.get('model_name', 'N/A')}",
        f"Model Version: {d.get('model_version', 'unversioned')}",
        f"Model Type:    {d.get('model_type', 'unspecified')}",
        f"Deployment Scope: {d.get('deployment_scope', 'unspecified')}",
    ]
    return "\n".join(lines)


def _section_intended_use(d: Dict[str, Any]) -> str:
    """Generate the Intended Use and Scope section."""
    intended = d.get("intended_use", "Not provided.")
    scope = d.get("deployment_scope", "unspecified")
    return (
        f"Intended Use: {intended}\n"
        f"Deployment Scope: {scope}\n"
        "This document supports a governance assessment of the stated intended use; "
        "it does not authorise deployment beyond that scope."
    )


def _section_training_data(d: Dict[str, Any]) -> str:
    """Generate the Training Data Summary section."""
    summary = d.get("training_data_summary", "")
    if not summary:
        return (
            "Training data summary was not provided. Provide a description of the "
            "training data sources, coverage, and known representativeness gaps to "
            "support the governance assessment."
        )
    return str(summary)


def _section_performance(d: Dict[str, Any]) -> str:
    """Generate the Performance Characteristics section."""
    metrics: Dict[str, Any] = d.get("performance_metrics") or {}
    if not metrics:
        return "No performance metrics supplied. Performance characteristics are pending measurement."
    lines = [f"  - {name}: {_format_metric(value)}" for name, value in metrics.items()]
    return "Reported performance metrics (as supplied by the model owner):\n" + "\n".join(lines)


def _section_limitations(d: Dict[str, Any]) -> str:
    """Generate the Limitations and Risks section."""
    limitations = d.get("limitations", "")
    if not limitations:
        return (
            "No limitations were declared. Documenting known limitations, failure "
            "modes, and out-of-scope conditions is recommended before deployment."
        )
    return str(limitations)


def _section_risk_classification(d: Dict[str, Any]) -> str:
    """Generate the Risk Classification section (advisory framing)."""
    risk_tier = str(d.get("risk_tier", "limited")).upper()
    requires_review = d.get("requires_human_review", False)
    frameworks = ", ".join(_framework_label(f) for f in d.get("applicable_frameworks", []))
    lines = [
        f"  Assessed Risk Tier: {risk_tier}",
        f"  Applicable Frameworks: {frameworks or 'N/A'}",
        f"  Human Governance Review Recommended: {'YES' if requires_review else 'NO'}",
        "  This classification supports a compliance assessment under the applicable "
        "frameworks; it is advisory and does not constitute a certification.",
    ]
    return "\n".join(lines)


def _section_human_oversight(d: Dict[str, Any]) -> str:
    """Generate the Human Oversight Measures section."""
    requires_review = d.get("requires_human_review", False)
    if requires_review:
        return (
            "This model is classified at a high / unacceptable risk tier. A documented "
            "human governance review is recommended before and during deployment, with "
            "a named accountable owner, escalation path, and periodic re-assessment."
        )
    return (
        "Human oversight measures should be proportionate to the assessed risk tier: "
        "define a monitoring cadence, an accountable owner, and a documented rollback "
        "procedure."
    )


class GenerateGovernanceSectionsNode(FunctionNode):
    """Generate all 7 AI-governance / model-card sections (advisory framing).

    Deterministic template-based synthesis — no language model is called, which is
    what the manifest's ``generation_mode: "deterministic"`` declares.

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        model_metadata: str  — JSON-serialised enriched governance payload (ADR-005)

    Output state keys (partial dict):
        governance_sections: str  — JSON-serialised section dict (ADR-005)
        status:              str
        error_log:           list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        model_metadata: Dict[str, Any] = from_json(state.get("model_metadata"), {})

        if not model_metadata:
            logger.error("GenerateGovernanceSectionsNode: model_metadata missing in state")
            emit_trace_event(
                "generate_governance_sections_failed",
                {"reason": "missing_model_metadata"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["GenerateGovernanceSectionsNode: model_metadata missing in state"],
            }

        model_name = model_metadata.get("model_name", "unknown")

        # ── Generate all sections ─────────────────────────────────────────────
        sections: Dict[str, str] = {
            "model_overview": _section_model_overview(model_metadata),
            "intended_use_and_scope": _section_intended_use(model_metadata),
            "training_data_summary": _section_training_data(model_metadata),
            "performance_characteristics": _section_performance(model_metadata),
            "limitations_and_risks": _section_limitations(model_metadata),
            "risk_classification": _section_risk_classification(model_metadata),
            "human_oversight_measures": _section_human_oversight(model_metadata),
        }

        logger.info(
            "GenerateGovernanceSectionsNode: model_name=%s sections=%d",
            model_name,
            len(sections),
        )
        emit_trace_event(
            "generate_governance_sections_complete",
            {
                "model_name": model_name,
                "section_count": len(sections),
                "section_keys": sorted(sections.keys()),
            },
            state,
        )

        return {
            "governance_sections": to_json(sections),
            "status": AgentStatus.SUCCESS.value,
        }
