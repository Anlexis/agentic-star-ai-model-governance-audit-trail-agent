"""AgentCore Platform v1.0"""

# CMN-C1-068 — ParseModelMetadataNode
# Inner domain node 2: enrich and classify the validated model metadata.
#
# Responsibilities:
#   - ScopeClassify: derive the set of applicable governance frameworks from
#     the requested target_frameworks + a baseline (ISO 42001 always applies)
#   - RiskClassify: confirm the risk tier and derive whether a human governance
#     review is recommended (S-5 — high / unacceptable tiers)
#   - Annotate model_metadata with derived fields for downstream nodes
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

# ISO/IEC 42001 is the cross-industry AI management-system baseline: it applies
# to every governed model regardless of the frameworks the caller requested.
_BASELINE_FRAMEWORK = "iso_42001"

# Risk tiers that require a documented human governance review (S-5).
_HUMAN_REVIEW_TIERS = {"high", "unacceptable"}


def _derive_applicable_frameworks(target_frameworks: List[str]) -> List[str]:
    """Union of requested frameworks + the ISO 42001 baseline, order-stable."""
    applicable: List[str] = []
    for fw in list(target_frameworks) + [_BASELINE_FRAMEWORK]:
        if fw and fw not in applicable:
            applicable.append(fw)
    return applicable


class ParseModelMetadataNode(FunctionNode):
    """Enrich model metadata with framework scope + risk classification.

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        model_metadata: str  — JSON-serialised model-governance payload (ADR-005)

    Output state keys (partial dict):
        model_metadata: str  — enriched JSON string (ADR-005), same key updated
        status:         str
        error_log:      list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        model_metadata: Dict[str, Any] = from_json(state.get("model_metadata"), {})

        if not model_metadata:
            logger.error("ParseModelMetadataNode: model_metadata is empty or missing")
            emit_trace_event(
                "parse_model_metadata_failed",
                {"reason": "missing_model_metadata"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ParseModelMetadataNode: model_metadata missing in state"],
            }

        model_name = model_metadata.get("model_name", "unknown")
        risk_tier = str(model_metadata.get("risk_tier", "limited"))
        target_frameworks = list(model_metadata.get("target_frameworks", []))

        # ── ScopeClassify ─────────────────────────────────────────────────────
        applicable_frameworks = _derive_applicable_frameworks(target_frameworks)

        # ── RiskClassify (S-5) ────────────────────────────────────────────────
        requires_human_review = risk_tier in _HUMAN_REVIEW_TIERS

        # ── Enrich model_metadata (local copy — never mutate a module global) ──
        enriched: Dict[str, Any] = dict(model_metadata)
        enriched["applicable_frameworks"] = applicable_frameworks
        enriched["risk_tier"] = risk_tier
        enriched["requires_human_review"] = requires_human_review

        logger.info(
            "ParseModelMetadataNode: model_name=%s risk_tier=%s applicable=%d review=%s",
            model_name,
            risk_tier,
            len(applicable_frameworks),
            requires_human_review,
        )
        emit_trace_event(
            "parse_model_metadata_complete",
            {
                "model_name": model_name,
                "risk_tier": risk_tier,
                "applicable_frameworks": applicable_frameworks,
                "requires_human_review": requires_human_review,
            },
            state,
        )

        return {
            "model_metadata": to_json(enriched),
            "status": AgentStatus.SUCCESS.value,
        }
