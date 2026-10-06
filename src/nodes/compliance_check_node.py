"""AgentCore Platform v1.0"""

# CMN-C1-068 — ComplianceCheckNode
# Inner domain node 4: map the governed model to the applicable framework
# requirements and emit a citation list (the value-add of this template).
#
# Industry-agnostic: the frameworks below are cross-industry AI-governance
# standards (EU AI Act, ISO/IEC 42001, FSA AI Risk, METI DGC, Cabinet Office AI
# Guidelines). No industry-specific KB is assumed (Cat 1).
#
# S-5: when the assessed risk tier is high / unacceptable, human_review_required
# is set True with an advisory recommendation note.
#
# Inner node — ANONYMOUS trust (review finding 5, corrected 2026-07-02).
# Returns ONLY the state keys this node writes (partial-dict contract).

import logging
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)

# Risk tiers that require a documented human governance review (S-5).
_HUMAN_REVIEW_TIERS = {"high", "unacceptable"}

# Framework → representative clause citations for the generated model card.
# These are advisory pointers, not legal advice; they anchor the document to
# specific, checkable requirements per framework.
_FRAMEWORK_CITATIONS: Dict[str, List[str]] = {
    "eu_ai_act": [
        "EU AI Act Art. 11 & Annex IV — technical documentation",
        "EU AI Act Art. 9 — risk management system",
        "EU AI Act Art. 14 — human oversight",
    ],
    "iso_42001": [
        "ISO/IEC 42001:2023 Clause 6.1 — actions to address risks and opportunities",
        "ISO/IEC 42001:2023 Clause 8 — operation (AI system lifecycle)",
        "ISO/IEC 42001:2023 Annex A — AI management controls",
    ],
    "fsa_ai_risk": [
        "FSA AI Model Risk Management — model documentation and validation",
        "FSA AI Model Risk Management — ongoing monitoring",
    ],
    "meti_dgc": [
        "METI Digital Governance Code — AI system accountability and audit trail",
    ],
    "cao_ai_strategy": [
        "Cabinet Office AI Guidelines for Business Operators — transparency & accountability",
    ],
}


def _build_citations(applicable_frameworks: List[str]) -> Dict[str, List[str]]:
    """Assemble the citation list for the applicable frameworks."""
    citations: Dict[str, List[str]] = {}
    for fw in applicable_frameworks:
        clauses = _FRAMEWORK_CITATIONS.get(fw)
        if clauses:
            citations[fw] = list(clauses)
    return citations


class ComplianceCheckNode(FunctionNode):
    """Map the governed model to framework requirements + emit citations.

    Produces a compliance_flags dict with the applicable frameworks, a per-
    framework citation list, and the human-review recommendation (S-5) for
    inclusion in the governance document's compliance section.

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        model_metadata: str  — JSON-serialised enriched governance payload (ADR-005)

    Output state keys (partial dict):
        compliance_flags:      str   — JSON-serialised framework-mapping dict (ADR-005)
        human_review_required: bool  — True if a human governance review is recommended
        status:                str
        error_log:             list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        model_metadata: Dict[str, Any] = from_json(state.get("model_metadata"), {})

        if not model_metadata:
            logger.error("ComplianceCheckNode: model_metadata missing in state")
            emit_trace_event(
                "compliance_check_failed",
                {"reason": "missing_model_metadata"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ComplianceCheckNode: model_metadata missing in state"],
            }

        model_name = model_metadata.get("model_name", "unknown")
        risk_tier = str(model_metadata.get("risk_tier", "limited"))
        applicable_frameworks = list(
            model_metadata.get("applicable_frameworks") or model_metadata.get("target_frameworks", [])
        )

        citations = _build_citations(applicable_frameworks)
        human_review_required = risk_tier in _HUMAN_REVIEW_TIERS

        advisory_note = (
            "This mapping supports a compliance assessment against the applicable "
            "frameworks and is advisory only; it does not constitute a certification "
            "or legal determination."
        )
        if human_review_required:
            advisory_note += (
                " A documented human governance review is recommended before deployment "
                f"(assessed risk tier: {risk_tier})."
            )

        compliance_result: Dict[str, Any] = {
            "frameworks": applicable_frameworks,
            "citations": citations,
            "human_review_required": human_review_required,
            "risk_tier": risk_tier,
            "advisory_note": advisory_note,
        }

        logger.info(
            "ComplianceCheckNode: model_name=%s frameworks=%d human_review=%s",
            model_name,
            len(applicable_frameworks),
            human_review_required,
        )
        emit_trace_event(
            "compliance_check_complete",
            {
                "model_name": model_name,
                "framework_count": len(applicable_frameworks),
                "human_review_required": human_review_required,
                "risk_tier": risk_tier,
            },
            state,
        )

        return {
            "compliance_flags": to_json(compliance_result),
            "human_review_required": human_review_required,
            "status": AgentStatus.SUCCESS.value,
        }
