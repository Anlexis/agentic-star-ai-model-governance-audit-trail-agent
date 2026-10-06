"""AgentCore Platform v1.0"""

# CMN-C1-068 — OutputFormatNode (inner domain node 5, last in DomainWorkflowGraph)
# Assembles the final AI-governance document from governance_sections and
# compliance_flags, and writes an append-only audit-trail entry (S-4).
# This is the last inner node — it produces the governance_document string that
# the outer PostProcessNode will S-3-gate.
#
# Audit-trail immutability: the audit entry is a compliance artifact. It is built
# deterministically from the document content (content SHA-256 + generator id) so
# it is idempotent for identical input, and it is only ever added — never used to
# modify a prior entry.
#
# The entry also records which declared fields arrived already redacted by the
# platform input filter. That filter masks personal-data-shaped spans in the
# caller payload before any template code runs, and its name heuristic fires on
# ordinary two-word business English, so text can be removed from a governance
# document with no signal to its reader. Recording the field names converts a
# silent erasure into a visible one.
#
# This node deliberately does NOT write `result`. `result` is the caller-facing
# channel that the framework falls back to when `formatted_output` is falsy, and
# its only legitimate writer is the outer PostProcessNode, after the output gate
# has run. Writing the pre-gate document there — even into inner state that is
# not carried out — puts un-gated text one merge away from the caller.
#
# Inner node — ANONYMOUS trust (review finding 5, corrected 2026-07-02).
# Returns ONLY the state keys this node writes (partial-dict contract).

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)

_GENERATOR_ID = "CMN-C1-068/AIModelGovernanceAuditTrailAgent"

# Section order for the final document (model-card recommended order).
_SECTION_ORDER = [
    "model_overview",
    "intended_use_and_scope",
    "training_data_summary",
    "performance_characteristics",
    "limitations_and_risks",
    "risk_classification",
    "human_oversight_measures",
]

# Human-readable section headers.
_SECTION_HEADERS: Dict[str, str] = {
    "model_overview": "1. Model Overview",
    "intended_use_and_scope": "2. Intended Use and Scope",
    "training_data_summary": "3. Training Data Summary",
    "performance_characteristics": "4. Performance Characteristics",
    "limitations_and_risks": "5. Limitations and Risks",
    "risk_classification": "6. Risk Classification",
    "human_oversight_measures": "7. Human Oversight Measures",
}

_SEPARATOR = "=" * 72
_SUBSEP = "-" * 72


def _assemble_document(
    model_name: str,
    sections: Dict[str, str],
    compliance: Dict[str, Any],
    redacted_fields: List[str],
) -> str:
    """Assemble the full governance document from sections and compliance data."""
    lines = [
        _SEPARATOR,
        "AI MODEL GOVERNANCE DOCUMENT",
        f"Model: {model_name}",
        _SEPARATOR,
        "",
    ]

    for key in _SECTION_ORDER:
        header = _SECTION_HEADERS.get(key, key.replace("_", " ").title())
        content = sections.get(key, "(Section not generated)")
        lines.append(header)
        lines.append(_SUBSEP)
        lines.append(content)
        lines.append("")

    # Append the compliance mapping trailer (advisory framing — never certifies).
    frameworks = ", ".join(compliance.get("frameworks", [])) or "N/A"
    human_review = compliance.get("human_review_required", False)
    advisory = compliance.get(
        "advisory_note",
        "This document is advisory and supports a compliance assessment.",
    )
    lines += [
        _SEPARATOR,
        "REGULATORY MAPPING NOTE",
        _SUBSEP,
        f"  Applicable Frameworks:    {frameworks}",
        f"  Human Governance Review:  {'RECOMMENDED' if human_review else 'not required at this tier'}",
        f"  Advisory:                 {advisory}",
    ]
    citations: Dict[str, List[str]] = compliance.get("citations", {}) or {}
    if citations:
        lines.append("  Citations:")
        for fw, clauses in citations.items():
            for clause in clauses:
                lines.append(f"    - [{fw}] {clause}")
    if redacted_fields:
        lines += [
            "  Redacted:                 the platform input filter removed content from "
            + ", ".join(sorted(redacted_fields))
            + " before this document was generated; the text below is incomplete.",
        ]
    lines.append(_SEPARATOR)

    return "\n".join(lines)


def _build_audit_entry(model_metadata: Dict[str, Any], document: str, redacted_fields: List[str]) -> Dict[str, Any]:
    """Build an immutable, append-only audit-trail entry."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_name": model_metadata.get("model_name", "unknown"),
        "model_version": model_metadata.get("model_version", "unversioned"),
        "generator_id": _GENERATOR_ID,
        "document_sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
        "frameworks": list(model_metadata.get("applicable_frameworks", [])),
        "redacted_fields": sorted(redacted_fields),
    }


class OutputFormatNode(FunctionNode):
    """Assemble the final governance document + append-only audit entry.

    Reads governance_sections and compliance_flags from State, renders the full
    governance document text, writes an immutable audit-trail entry, and exposes
    governance_document (and result) for the outer PostProcessNode.

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        governance_sections: str  — JSON-serialised section dict (ADR-005)
        compliance_flags:    str  — JSON-serialised framework-mapping (ADR-005)
        model_metadata:      str  — JSON-serialised governance payload (ADR-005)
        redacted_fields:     str  — JSON list of fields the input filter had redacted

    Output state keys (partial dict):
        governance_document: str
        audit_entry:         str  (JSON-serialised audit-trail entry, ADR-005)
        status:              str
        error_log:           list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> Dict[str, Any]:
        sections: Dict[str, str] = from_json(state.get("governance_sections"), {})
        compliance: Dict[str, Any] = from_json(state.get("compliance_flags"), {})
        model_metadata: Dict[str, Any] = from_json(state.get("model_metadata"), {})
        redacted_fields: List[str] = from_json(state.get("redacted_fields"), []) or []

        model_name = model_metadata.get("model_name", "unknown")

        if not sections:
            logger.error(
                "OutputFormatNode: governance_sections missing in state for model_name=%s",
                model_name,
            )
            emit_trace_event(
                "output_format_failed",
                {"reason": "missing_governance_sections", "model_name": model_name},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"OutputFormatNode: governance_sections missing for model_name={model_name}"],
            }

        # ── Assemble the document + audit entry ───────────────────────────────
        document = _assemble_document(model_name, sections, compliance, redacted_fields)
        audit_entry = _build_audit_entry(model_metadata, document, redacted_fields)

        logger.info(
            "OutputFormatNode: model_name=%s document_chars=%d human_review=%s",
            model_name,
            len(document),
            compliance.get("human_review_required", False),
        )
        emit_trace_event(
            "output_format_complete",
            {
                "model_name": model_name,
                "document_length": len(document),
                "section_count": len(sections),
                "document_sha256": audit_entry["document_sha256"],
            },
            state,
        )

        return {
            "governance_document": document,
            "audit_entry": to_json(audit_entry),
            "status": AgentStatus.SUCCESS.value,
        }
