"""AgentCore Platform v1.0"""

# CMN-C1-068 — PostProcessNode
# Outer backbone post_process slot: the domain output gate. Scans the assembled
# governance document, then exposes it as formatted_output and result.
#
# The gate covers two classes:
#   (a) credentials — delegated to the framework's own detector rather than a
#       local pattern list, and
#   (b) compliance certification language ("this system IS compliant", "fully
#       compliant", "guarantees compliance", "meets all requirements"). A
#       governance document must use ADVISORY framing: it SUPPORTS a compliance
#       assessment, it never certifies one.
#
# Why the credential half delegates
# ---------------------------------
# A local pattern set that is NARROWER than the framework's is not a smaller gate,
# it is a bypass. The framework runs its own credential scan over every value this
# node returns and RAISES when it finds one; the node wrapper then converts that
# raise into a bare error partial and discards this node's delta — including the
# clearing below. So a value the framework catches and a local list misses turns a
# clean, contained refusal into an opaque failure with nothing cleared. The local
# list here missed `AKIA…`, `sk_live_…`, `postgresql://…` and two-segment JWTs,
# all six of which the framework catches. Calling the framework's
# `detect_credentials` makes the two sets identical by construction, so they
# cannot drift apart again.
#
# On a violation the node returns ERROR, a TRUTHY withholding notice as
# formatted_output, and an explicit cleared value for every output-bearing state
# field. Truthiness matters: `AgentBaseGraph.get_output()` resolves the caller's
# output as `formatted_output or result`, so a falsy notice re-opens the fallback
# it was written to close.
#
# The domain gate is a module-level function (_security_gate_output) called from
# inside execute() — NOT an instance method on the node class, which the real SDK
# auto-wraps.
#
# Returns ONLY the state keys this node writes (partial-dict contract).

import logging
import re
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from framework.security.credential_detector import detect_credentials
from shared.utils.audit_logger import emit_trace_event

logger = logging.getLogger(__name__)

# Certification-language patterns — a governance document must NOT assert that a
# system IS compliant / certified. These are advisory documents that SUPPORT a
# compliance assessment; they never certify one.
_CERTIFICATION_PATTERNS: List[Tuple[str, str]] = [
    (r"\bis\s+(?:fully\s+)?compliant\b", "asserts_is_compliant"),
    (r"\bfully\s+compliant\b", "asserts_fully_compliant"),
    (r"\bcertified\s+compliant\b", "asserts_certified_compliant"),
    (r"\bguarantees?\s+compliance\b", "asserts_guarantees_compliance"),
    (r"\bmeets\s+all\s+(?:regulatory\s+)?requirements\b", "asserts_meets_all_requirements"),
]

# Every state field that can carry document text or a structured payload derived
# from it. On a violation each one is written back EMPTY — not omitted. LangGraph
# merges partial deltas, so a key left out of the returned mapping keeps its
# previous value in state, which is indistinguishable from clearing when read back
# with `not state.get(field)`.
_OUTPUT_BEARING_FIELDS = (
    "governance_document",
    "governance_sections",
    "compliance_flags",
    "audit_entry",
)


def _cleared_output_state() -> Dict[str, Any]:
    """Explicitly empty every output-bearing field (presence, not omission)."""
    return {field: None for field in _OUTPUT_BEARING_FIELDS}


def _security_gate_output(content: str) -> Optional[str]:
    """Scan output for credentials and certification language.

    Returns the first violation name, or None if the output is clean. The
    credential half delegates to the framework's detector so this gate's block set
    is identical to the one the framework enforces on the way out — see the module
    docstring for why a narrower local set is a bypass rather than a smaller gate.
    Module-level function (not a node instance method) — the instance form is
    auto-wrapped by the real SDK.
    """
    findings = detect_credentials(content)
    if findings:
        return "credential_" + str(findings[0]["type"])
    for pattern, name in _CERTIFICATION_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return name
    return None


class PostProcessNode(FunctionNode):
    """Apply the domain output gate and expose the final governance document.

    Outer backbone post_process slot. Declared ANONYMOUS — trust was already
    enforced at PreProcessNode (VERIFIED_EXTERNAL).

    Input state keys:
        governance_document:   str   — formatted document from the inner OutputFormatNode
        human_review_required: bool  — human-review recommendation flag

    Output state keys (partial dict):
        formatted_output: str
        result:           str
        status:           str
        error_log:        list[str]  (only on ERROR)
        governance_document / governance_sections / compliance_flags /
        audit_entry:      cleared (None) on any non-success outcome
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        governance_document: str = state.get("governance_document") or ""
        human_review_required: bool = bool(state.get("human_review_required"))

        # ── No document to gate ───────────────────────────────────────────────
        # Reaching this slot means the domain workflow reported success, so an
        # empty document is a contradiction, not a degraded result. Reporting it
        # as SUCCESS with a placeholder would hand the caller a governance
        # artifact that documents nothing while telling them everything worked.
        if not governance_document.strip():
            logger.error("PostProcessNode: no document content to release")
            emit_trace_event(
                "post_process_empty_document",
                {"human_review_required": human_review_required},
                state,
            )
            return {
                **_cleared_output_state(),
                "formatted_output": (
                    "[GOVERNANCE DOCUMENT WITHHELD: the generation pipeline produced no "
                    "document content. Nothing has been released. Re-submit the request; "
                    "if it recurs, the payload does not describe a documentable model.]"
                ),
                "result": None,
                "status": AgentStatus.ERROR.value,
                "error_log": ["PostProcessNode: no document content to release"],
            }

        # ── Domain output gate ────────────────────────────────────────────────
        violation = _security_gate_output(governance_document)
        if violation:
            logger.error("PostProcessNode: output gate violation — %s", violation)
            emit_trace_event(
                "post_process_output_violation",
                {"violation": violation},
                state,
            )
            # Names the violation CLASS only — never the matched text, which is
            # exactly the value the gate exists to withhold.
            sanitised = (
                f"[GOVERNANCE DOCUMENT WITHHELD: the generated document matched a "
                f"disallowed pattern ({violation}) and has not been released. A "
                f"governance document must use advisory framing and must not carry "
                f"credentials. Revise the model declaration and re-submit.]"
            )
            return {
                **_cleared_output_state(),
                "formatted_output": sanitised,
                "result": None,
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PostProcessNode: output gate blocked the document — {violation}"],
            }

        logger.info(
            "PostProcessNode: output gate passed — length=%d human_review_required=%s",
            len(governance_document),
            human_review_required,
        )
        emit_trace_event(
            "post_process_complete",
            {
                "output_length": len(governance_document),
                "human_review_required": human_review_required,
            },
            state,
        )

        return {
            "formatted_output": governance_document,
            "result": governance_document,
            "status": AgentStatus.SUCCESS.value,
        }
