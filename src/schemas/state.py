"""AgentCore Platform v1.0"""

# ADR-005: State must be a flat TypedDict — never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption.  Extend AgentState with agent-specific
# fields only.  Do NOT add credentials, secrets, or Pydantic models.
#
# CMN-C1-068 — AI Model Governance Documentation & Audit Trail Generation Agent
# Cat 1 (industry-agnostic) DocGeneration template built on the two-layer
# nested structural pattern: outer backbone (AgentBaseGraph) + inner domain
# workflow (BaseGraph).  Fields below cover both layers.
#
# ADR-005 compliance: all dict/list-valued fields are stored as JSON-
# serialized Optional[str].  Use to_json() / from_json() helpers below
# at every producer and consumer node — one contract end-to-end.
# Never type a dict/list field as a bare dict/list; that causes msgpack
# serialization failures and a CoE Stage-6 state-contract finding.

import json
from typing import Any, NotRequired, Optional

from framework.schemas.agent_state import AgentState


def to_json(value: Any) -> Optional[str]:
    """Serialize a value to a JSON string for State storage (ADR-005)."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def from_json(value: Optional[str], default: Any = None) -> Any:
    """Deserialize a JSON string from State storage (ADR-005)."""
    if value is None:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


class State(AgentState):
    """Flat TypedDict for CMN-C1-068.

    All shared fields (user_input, status, session_id, node_history,
    error_log, hitl_*, etc.) are inherited from AgentState.

    ADR-005: dict/list fields use JSON-serialized Optional[str].
    formatted_output is NOT re-declared here — it is inherited from AgentState.
    """

    # ------------------------------------------------------------------
    # Outer layer — set by PreProcessNode (pre_process backbone, S-1)
    # ------------------------------------------------------------------

    # Validated and normalised JSON string of the model-governance payload.
    # Produced by PreProcessNode; consumed by inner InputValidateNode.
    validated_input: NotRequired[Optional[str]]

    # JSON-serialised channel/request metadata dict (ADR-005: stored as str).
    # Shape: {"source": str, "channel": str, "model_name": str}
    enriched_context: NotRequired[Optional[str]]

    # JSON-serialised validated runtime settings, seeded from the graph config by
    # AIModelGovernanceAuditTrailAgent._extra_initial_state() and carried into the
    # inner graph by src/graph/context_bridge.py.  This is how a declared value in
    # config/config.yaml reaches the nodes that consume it: nodes are called as
    # execute(state) and never receive a config argument.
    # Shape: {"max_input_bytes": int, "max_field_chars": int,
    #   "max_performance_metrics": int, "max_target_frameworks": int}
    governance_settings: NotRequired[Optional[str]]

    # ------------------------------------------------------------------
    # Inner layer — domain nodes (DomainWorkflowGraph)
    # ------------------------------------------------------------------

    # JSON-serialised parsed + enriched model-governance payload (ADR-005: str).
    # Shape: {model_name, model_type, intended_use, deployment_scope,
    #   risk_tier, target_frameworks (list), applicable_frameworks (list),
    #   training_data_summary, performance_metrics (dict), limitations,
    #   requires_human_review (bool), model_version}
    model_metadata: NotRequired[Optional[str]]

    # JSON-serialised governance document section dict (ADR-005: stored as str).
    # Keys match the 7 model-card / governance sections:
    #   model_overview, intended_use_and_scope, training_data_summary,
    #   performance_characteristics, limitations_and_risks,
    #   risk_classification, human_oversight_measures
    # Each value is the rendered text for that section.
    governance_sections: NotRequired[Optional[str]]

    # JSON-serialised framework-mapping / citation result (ADR-005: stored as str).
    # Shape: {frameworks: list, citations: {framework: [clauses]},
    #   human_review_required: bool, risk_tier: str, advisory_note: str}
    compliance_flags: NotRequired[Optional[str]]

    # True when a human governance review is recommended (risk tier high /
    # unacceptable).  S-5: high-risk classifications must carry this flag.
    human_review_required: NotRequired[Optional[bool]]

    # JSON-serialised immutable audit-trail entry (ADR-005: stored as str).
    # Shape: {generated_at, model_name, model_version, generator_id,
    #   document_sha256, frameworks (list)}.  Append-only compliance artifact (S-4).
    audit_entry: NotRequired[Optional[str]]

    # JSON list of declared field names whose content the platform input filter
    # had already redacted when it reached this template.  Rendered as a
    # provenance line in the document and recorded in the audit entry, so a
    # reader can tell that text was removed rather than never supplied.
    redacted_fields: NotRequired[Optional[str]]

    # Final formatted AI-governance document (plain text, model-card ready).
    # Assembled by inner OutputFormatNode from governance_sections + compliance_flags.
    # This is the PRE-gate document: it is written in the inner graph and carried
    # to outer state, and it is not caller-facing until PostProcessNode has gated it.
    governance_document: NotRequired[Optional[str]]

    # ------------------------------------------------------------------
    # Outer layer — set by PostProcessNode (post_process backbone, S-3)
    # ------------------------------------------------------------------

    # Primary result surfaced to the caller.  Written ONLY by PostProcessNode, and
    # only on the gated success path — it is the channel the framework falls back
    # to when formatted_output is falsy, so no other node may write it.
    # formatted_output (from AgentState) is also set by PostProcessNode.
    result: NotRequired[Optional[str]]

    # ------------------------------------------------------------------
    # Tracing / audit — framework-managed; do NOT write from node code
    # ------------------------------------------------------------------

    trace_id: NotRequired[Optional[str]]
    correlation_id: NotRequired[Optional[str]]
    # node_history inherited from AgentState
