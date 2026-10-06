"""AgentCore Platform v1.0"""

# CMN-C1-068 — PreProcessNode
# Outer backbone pre_process slot (trust gate + governance input validation).
#
# Responsibilities:
#   - Enforce VERIFIED_EXTERNAL trust (required_trust_level)
#   - Bound the payload by size before parsing it
#   - Reject empty / non-JSON input early (fail-fast), and refuse the non-standard
#     NaN / Infinity JSON literals that Python's parser would otherwise accept
#   - Confirm required model-governance fields are present
#   - Screen the parsed payload for chat-template control markers and instruction-
#     override directives, depth-first, over keys as well as values
#   - Refuse a payload carrying a credential-shaped value, using the framework's
#     own detector so the refusal set matches its block set exactly
#   - Refuse a request whose model identity has already been redacted by the
#     platform input filter — an audit trail keyed on the redaction sentinel is
#     worse than a refusal, because the reader gets no signal anything was removed
#   - Governance rule: hard-refuse documentation requests for AI systems that
#     describe a prohibited practice under the EU AI Act Article 5 list
#     (industry-agnostic — real-time biometric surveillance, social scoring,
#     subliminal manipulation, emotion recognition in workplace/education,
#     untargeted face scraping)
#   - Write validated_input (normalised JSON string) + enriched_context to State
#   - Emit an audit event for every validation decision
#
# Every refusal names a LOCATION — a field — and never echoes the value that
# caused it.
#
# Returns ONLY the state keys this node writes (partial-dict contract).

import json
import logging
import re
from typing import Any, ClassVar, Dict, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from framework.security.credential_detector import detect_credentials_in_value
from shared.utils.audit_logger import emit_trace_event

from src.schemas.guards import (
    NonFiniteJSONConstant,
    contains_redaction_sentinel,
    inert_identifier,
    loads_strict,
    safe_field_label,
    screen_control_tokens,
)
from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)

# Fallback bound used when the graph seeded no settings (direct node invocation).
_DEFAULT_MAX_INPUT_BYTES = 32_768

# Required top-level keys for a valid model-governance payload.
# model_name is the mandatory identifier; intended_use + risk_tier are the
# minimum needed to generate a governance document and classify risk.
_REQUIRED_GOVERNANCE_KEYS = frozenset(
    {
        "model_name",
        "intended_use",
        "risk_tier",
    }
)

# Prohibited-practice markers, industry-agnostic (EU AI Act Article 5 list).
# A documentation request whose intended_use / deployment_scope describes any of
# these is hard-refused: this template does not produce governance docs that
# would legitimise a prohibited AI system.  Patterns are deliberately broad and
# domain-independent so the rule applies to any industry.
_PROHIBITED_PRACTICE_PATTERNS = [
    (r"real[\s-]*time\s+(?:remote\s+)?biometric\s+(?:identification|surveillance)", "real_time_biometric_surveillance"),
    (r"social\s+scoring", "social_scoring"),
    (r"subliminal\s+manipulation", "subliminal_manipulation"),
    (
        r"emotion\s+recognition\s+in\s+(?:the\s+)?(?:workplace|school|education)",
        "emotion_recognition_restricted_context",
    ),
    (r"untargeted\s+(?:scraping|harvesting)\s+of\s+(?:facial|face)\s+images", "untargeted_facial_scraping"),
    (r"predictive\s+policing\s+(?:profil|based\s+on\s+profil)", "predictive_policing_profiling"),
]


def _detect_prohibited_practice(text: str) -> Optional[str]:
    """Return the prohibited-practice name if the text matches one, else None."""
    for pattern, name in _PROHIBITED_PRACTICE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    return None


class PreProcessNode(FunctionNode):
    """Input validation for CMN-C1-068.

    Validates the caller-supplied AI-model-governance payload before the domain
    workflow runs.  This is the outer backbone's pre_process slot — the only node
    with VERIFIED_EXTERNAL trust, so unauthenticated or anonymous callers are
    rejected here (fail-fast; inner domain nodes carry ANONYMOUS trust and never
    see untrusted input directly).

    Input state keys:
        user_input:          str  — caller-supplied JSON model-governance payload
        governance_settings: str  — JSON-serialised validated runtime settings

    Output state keys (partial dict):
        validated_input:  str        — normalised JSON string (re-serialised)
        enriched_context: str        — JSON-serialised channel metadata
        status:           str        — AgentStatus.SUCCESS.value or AgentStatus.ERROR.value
        error_log:        list[str]  — set only on ERROR
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        user_input = state.get("user_input", "")
        input_context = state.get("input_context", {})
        settings: Dict[str, Any] = from_json(state.get("governance_settings"), {}) or {}
        max_input_bytes = int(settings.get("max_input_bytes", _DEFAULT_MAX_INPUT_BYTES))

        # ── Emptiness check ───────────────────────────────────────────────────
        if not user_input or not isinstance(user_input, str) or not user_input.strip():
            logger.warning("PreProcessNode: user_input is empty or missing")
            emit_trace_event(
                "pre_process_validation_failed",
                {"reason": "empty_input"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PreProcessNode: user_input is empty or missing"],
            }

        # ── Size bound — applied BEFORE parsing ───────────────────────────────
        payload_bytes = len(user_input.encode("utf-8"))
        if payload_bytes > max_input_bytes:
            logger.warning(
                "PreProcessNode: payload too large — %d bytes (limit %d)",
                payload_bytes,
                max_input_bytes,
            )
            emit_trace_event(
                "pre_process_validation_failed",
                {"reason": "payload_too_large", "limit_bytes": max_input_bytes},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PreProcessNode: payload exceeds the {max_input_bytes}-byte limit"],
            }

        # ── JSON parse (non-finite literals refused) ──────────────────────────
        try:
            payload: Dict[str, Any] = loads_strict(user_input.strip())
        except NonFiniteJSONConstant:
            emit_trace_event(
                "pre_process_validation_failed",
                {"reason": "non_finite_json_constant"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "PreProcessNode: payload uses a non-finite JSON constant "
                    "(NaN / Infinity); send a finite number or omit the field"
                ],
            }
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("PreProcessNode: JSON parse failed — %s", exc)
            emit_trace_event(
                "pre_process_validation_failed",
                {"reason": "json_parse_error"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PreProcessNode: payload is not valid JSON ({exc.__class__.__name__})"],
            }

        if not isinstance(payload, dict):
            emit_trace_event(
                "pre_process_validation_failed",
                {"reason": "payload_not_object"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PreProcessNode: JSON root must be an object"],
            }

        # ── Required field check ──────────────────────────────────────────────
        missing = _REQUIRED_GOVERNANCE_KEYS - payload.keys()
        if missing:
            emit_trace_event(
                "pre_process_validation_failed",
                {"reason": "missing_required_fields", "missing": sorted(missing)},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PreProcessNode: missing required fields: {sorted(missing)}"],
            }

        # ── Control-token / directive screen (fail closed) ────────────────────
        # Runs over the PARSED payload so a \u-escaped marker cannot evade it, and
        # over keys as well as values. The platform screen covers <|im_start|>,
        # [INST] and <system> but scores <<SYS>> as nothing at all, so a directive
        # in that marker reaches the document unless this screen refuses it.
        screened = screen_control_tokens(payload)
        if screened:
            finding, field_label = screened
            logger.error("PreProcessNode: refused — %s in %s", finding, field_label)
            emit_trace_event(
                "pre_process_injection_refused",
                {"finding": finding, "field": field_label},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PreProcessNode: refused — {finding} detected in {field_label}"],
            }

        # ── Credential-shape screen (fail closed, readable refusal) ───────────
        # A credential anywhere in the payload is rendered into the assembled
        # document, where the framework's own output gate RAISES on it — four
        # nodes later, inside the subgraph, producing an opaque failure with no
        # reason the caller can act on. The request cannot succeed either way, so
        # refuse it here and name the field instead.
        #
        # The scan delegates to the same function the framework's gate calls, so
        # this refusal set equals its block set by construction and cannot drift.
        # Fields are iterated one at a time only to name the offending one:
        # detect_credentials_in_value(dict) is defined as the union over .values(),
        # so per-field scanning is exactly equivalent to scanning the whole payload.
        for index, (field_name, field_value) in enumerate(payload.items(), start=1):
            if detect_credentials_in_value(field_value):
                label = safe_field_label(str(field_name), index)
                logger.error("PreProcessNode: refused — credential shape in %s", label)
                emit_trace_event(
                    "pre_process_credential_refused",
                    {"field": label},
                    state,
                )
                return {
                    "status": AgentStatus.ERROR.value,
                    "error_log": [
                        f"PreProcessNode: refused — '{label}' contains a value shaped "
                        "like a credential; remove it and re-submit"
                    ],
                }

        # ── Prohibited-practice hard refuse (EU AI Act Art. 5) ────────────────
        scan_text = " ".join(
            str(payload.get(k, "")) for k in ("intended_use", "deployment_scope", "model_type", "model_name")
        )
        prohibited = _detect_prohibited_practice(scan_text)
        if prohibited:
            logger.error("PreProcessNode: refused prohibited AI practice — %s", prohibited)
            emit_trace_event(
                "pre_process_prohibited_practice_refused",
                {"prohibited_practice": prohibited},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "PreProcessNode: refused — intended use describes an EU AI Act "
                    f"Article 5 prohibited practice ({prohibited}); this template does "
                    "not generate governance documentation for prohibited AI systems"
                ],
            }

        # ── Redacted model identity refuse ────────────────────────────────────
        # The platform input filter masks personal-data-shaped spans in user_input
        # BEFORE this node runs, and its name heuristic fires on any run of two or
        # more title-case words — so an ordinary model name such as "Credit Risk
        # Scorer" arrives as the redaction sentinel. Generating a governance
        # document and an audit entry whose subject is the sentinel would report
        # success while silently erasing the identity the whole artifact is about.
        for identity_field in ("model_name", "model_version", "model_type"):
            if contains_redaction_sentinel(payload.get(identity_field, "")):
                logger.error(
                    "PreProcessNode: refused — %s was redacted by the input filter",
                    identity_field,
                )
                emit_trace_event(
                    "pre_process_redacted_identity_refused",
                    {"field": identity_field},
                    state,
                )
                return {
                    "status": AgentStatus.ERROR.value,
                    "error_log": [
                        f"PreProcessNode: refused — '{identity_field}' was redacted by "
                        "the platform input filter and cannot identify the model; supply "
                        "an identifier that is not shaped like personal data"
                    ],
                }

        # ── Success ───────────────────────────────────────────────────────────
        model_name = str(payload.get("model_name", "unknown"))
        normalised_json = json.dumps(payload, ensure_ascii=False, allow_nan=False)

        logger.info(
            "PreProcessNode: validated payload_keys=%d bytes=%d",
            len(payload),
            payload_bytes,
        )
        emit_trace_event(
            "pre_process_validated",
            {
                "payload_keys": sorted(payload.keys()),
                "payload_bytes": payload_bytes,
            },
            state,
        )

        return {
            "validated_input": normalised_json,
            "enriched_context": to_json(
                {
                    "source": "AIModelGovernanceAuditTrailAgent",
                    "channel": inert_identifier(input_context.get("channel", "unknown")),
                    "model_name": model_name,
                }
            ),
            "status": AgentStatus.SUCCESS.value,
        }
