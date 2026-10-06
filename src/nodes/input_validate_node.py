"""AgentCore Platform v1.0"""

# CMN-C1-068 — InputValidateNode
# Inner domain node 1: domain-level validation of the model-governance payload,
# and the SINGLE place caller text is bounded before it can reach the document.
#
# Distinct from PreProcessNode (trust + structural JSON + control-token screen +
# prohibited-practice check): this node applies domain-business rules — field
# types, risk-tier normalisation, framework normalisation — and produces the
# `model_metadata` mapping that every downstream node renders from.
#
# Why the bounding lives here and nowhere else
# --------------------------------------------
# The generated document is plain text whose meaning is carried by line structure:
# numbered section headers, "=" rules, and label lines such as
# "Human Governance Review:  RECOMMENDED". Rendering caller prose verbatim lets a
# caller close the current section with a newline and open a forged one — a
# governance verdict the agent never assessed, inside the agent's own document.
# Every downstream node renders from `model_metadata`, so bounding the values once
# here closes that at the source. Adding a second neutralisation pass at the
# render sites would make this one unfalsifiable, so there is deliberately only one.
#
# Inner node — ANONYMOUS trust (the outer PreProcessNode with VERIFIED_EXTERNAL
# already enforced trust; inner nodes must be ANONYMOUS so the outer
# InvocationContext passes through the GraphNode boundary without rejection).
#
# Returns ONLY the state keys this node writes (partial-dict contract).

import json
import logging
from typing import Any, ClassVar, Dict, List, Optional, Set, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.guards import (
    contains_redaction_sentinel,
    finite_number,
    inert_identifier,
    inert_metric_name,
    neutralise_text,
)
from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)

# Fallback bounds used when the graph seeded no settings (direct node invocation).
_DEFAULT_MAX_FIELD_CHARS = 2_000
_DEFAULT_MAX_PERFORMANCE_METRICS = 25
_DEFAULT_MAX_TARGET_FRAMEWORKS = 10

# Free-text fields rendered into the document as prose.
_PROSE_FIELDS = (
    "model_name",
    "model_version",
    "model_type",
    "intended_use",
    "deployment_scope",
    "training_data_summary",
    "limitations",
)

# Canonical governance risk tiers (the EU AI Act tiers as the cross-industry base).
_CANONICAL_RISK_TIERS = {"unacceptable", "high", "limited", "minimal"}

# Risk-tier aliases → canonical tier.  Industry-agnostic; covers common labels.
_RISK_TIER_ALIASES: Dict[str, str] = {
    "critical": "high",
    "severe": "high",
    "high-risk": "high",
    "high_risk": "high",
    "medium": "limited",
    "moderate": "limited",
    "limited-risk": "limited",
    "low": "minimal",
    "minimal-risk": "minimal",
    "prohibited": "unacceptable",
}

# Governance-framework aliases → canonical framework id.
_FRAMEWORK_ALIASES: Dict[str, str] = {
    "eu": "eu_ai_act",
    "euaiact": "eu_ai_act",
    "eu_ai_act": "eu_ai_act",
    "iso": "iso_42001",
    "iso42001": "iso_42001",
    "iso_42001": "iso_42001",
    "fsa": "fsa_ai_risk",
    "fsa_ai_risk": "fsa_ai_risk",
    "meti": "meti_dgc",
    "meti_dgc": "meti_dgc",
    "cao": "cao_ai_strategy",
    "cabinet": "cao_ai_strategy",
    "cao_ai_strategy": "cao_ai_strategy",
}


def _normalise_risk_tier(raw: Any) -> str:
    """Normalise a raw risk-tier label to the canonical set (default 'limited')."""
    key = inert_identifier(raw, 40)
    if key in _CANONICAL_RISK_TIERS:
        return key
    return _RISK_TIER_ALIASES.get(key, "limited")


def _normalise_frameworks(raw_frameworks: Any, limit: int) -> List[str]:
    """Normalise, lock to an inert id, deduplicate and CAP the framework list.

    Framework ids are rendered into a label line of the document, so an unknown id
    is kept (a caller may govern against a framework this template has no citation
    table for) but locked to ``[a-z0-9_]`` and length-capped, and the list itself is
    capped — otherwise a caller controls an unbounded run of rendered text.
    """
    if not isinstance(raw_frameworks, list):
        raw_frameworks = [raw_frameworks] if raw_frameworks else []
    normalised: List[str] = []
    seen: Set[str] = set()
    for fw in raw_frameworks:
        key = inert_identifier(fw, 32)
        canonical = _FRAMEWORK_ALIASES.get(key, key)
        if canonical and canonical not in seen:
            seen.add(canonical)
            normalised.append(canonical)
        if len(normalised) >= limit:
            break
    return normalised


def _normalise_metrics(raw: Any, limit: int) -> Tuple[Dict[str, float], Optional[str]]:
    """Return (metrics, rejected_metric_name).

    Metric names are locked to an inert class and the mapping is capped, because
    both the name and the value are rendered. A value that is not a finite number
    fails the request CLOSED rather than rendering as "nan" or "inf" under a
    success status: Python parses those and every comparison against them is False,
    which is exactly how a non-finite value rides through a check unnoticed.
    """
    if not isinstance(raw, dict):
        return {}, None
    metrics: Dict[str, float] = {}
    for name, value in raw.items():
        key = inert_metric_name(name)
        if not key:
            continue
        number = finite_number(value)
        if number is None:
            return {}, key
        metrics[key] = number
        if len(metrics) >= limit:
            break
    return metrics, None


class InputValidateNode(FunctionNode):
    """Domain validation and bounding of the model-governance payload.

    Applies business-rule checks beyond the structural check in PreProcessNode:
    field types, risk-tier normalisation, framework normalisation, default-framework
    selection when none is supplied — and the caller-text bounds described in the
    module docstring.

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        validated_input:     str  — normalised JSON string from PreProcessNode.
                                    Falls back to user_input for direct invocation.
        governance_settings: str  — JSON-serialised validated runtime settings

    Output state keys (partial dict):
        model_metadata:  str        — JSON-serialised normalised governance payload
        redacted_fields: str        — JSON list of fields the platform input filter
                                      had already redacted when they arrived
        status:          str
        error_log:       list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        raw = state.get("validated_input") or state.get("user_input", "")
        settings: Dict[str, Any] = from_json(state.get("governance_settings"), {}) or {}
        max_field_chars = int(settings.get("max_field_chars", _DEFAULT_MAX_FIELD_CHARS))
        max_metrics = int(settings.get("max_performance_metrics", _DEFAULT_MAX_PERFORMANCE_METRICS))
        max_frameworks = int(settings.get("max_target_frameworks", _DEFAULT_MAX_TARGET_FRAMEWORKS))

        # ── Parse ─────────────────────────────────────────────────────────────
        try:
            payload: Dict[str, Any] = json.loads(raw) if isinstance(raw, str) else {}
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("InputValidateNode: JSON parse error — %s", exc.__class__.__name__)
            emit_trace_event(
                "input_validate_failed",
                {"reason": "json_parse_error"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"InputValidateNode: validated_input is not valid JSON " f"({exc.__class__.__name__})"],
            }

        if not isinstance(payload, dict):
            emit_trace_event(
                "input_validate_failed",
                {"reason": "payload_not_dict"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["InputValidateNode: payload is not a JSON object"],
            }

        # ── Bound every free-text field, and record what arrived redacted ─────
        prose: Dict[str, str] = {}
        redacted_fields: List[str] = []
        for field in _PROSE_FIELDS:
            value = payload.get(field, "")
            if contains_redaction_sentinel(value):
                redacted_fields.append(field)
            prose[field] = neutralise_text(value, max_field_chars)

        model_name = prose["model_name"]
        if not model_name:
            emit_trace_event(
                "input_validate_failed",
                {"reason": "empty_model_name"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["InputValidateNode: model_name is missing or empty"],
            }

        intended_use = prose["intended_use"]
        if not intended_use:
            emit_trace_event(
                "input_validate_failed",
                {"reason": "empty_intended_use"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["InputValidateNode: intended_use is required"],
            }

        # ── Normalise risk tier + frameworks ──────────────────────────────────
        risk_tier = _normalise_risk_tier(payload.get("risk_tier", "limited"))
        target_frameworks = _normalise_frameworks(payload.get("target_frameworks", []), max_frameworks)
        if not target_frameworks:
            # Zero-config default: ISO 42001 is the cross-industry baseline.
            target_frameworks = ["iso_42001"]

        # ── Bound the reported metrics (fail closed on a non-finite value) ────
        performance_metrics, rejected_metric = _normalise_metrics(payload.get("performance_metrics", {}), max_metrics)
        if rejected_metric:
            logger.error("InputValidateNode: non-finite performance metric — %s", rejected_metric)
            emit_trace_event(
                "input_validate_failed",
                {"reason": "non_finite_performance_metric", "metric": rejected_metric},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    f"InputValidateNode: performance_metrics['{rejected_metric}'] is not "
                    "a finite number; supply a finite value or omit the metric"
                ],
            }

        # ── Build normalised model_metadata ───────────────────────────────────
        model_metadata: Dict[str, Any] = {
            "model_name": model_name,
            "model_version": prose["model_version"] or "unversioned",
            "model_type": prose["model_type"] or "unspecified",
            "intended_use": intended_use,
            "deployment_scope": prose["deployment_scope"] or "unspecified",
            "risk_tier": risk_tier,
            "target_frameworks": target_frameworks,
            "training_data_summary": prose["training_data_summary"],
            "performance_metrics": performance_metrics,
            "limitations": prose["limitations"],
        }

        logger.info(
            "InputValidateNode: risk_tier=%s frameworks=%d metrics=%d redacted=%d",
            risk_tier,
            len(target_frameworks),
            len(performance_metrics),
            len(redacted_fields),
        )
        emit_trace_event(
            "input_validate_complete",
            {
                "risk_tier": risk_tier,
                "framework_count": len(target_frameworks),
                "metric_count": len(performance_metrics),
                "redacted_fields": redacted_fields,
            },
            state,
        )

        return {
            "model_metadata": to_json(model_metadata),
            "redacted_fields": to_json(redacted_fields),
            "status": AgentStatus.SUCCESS.value,
        }
