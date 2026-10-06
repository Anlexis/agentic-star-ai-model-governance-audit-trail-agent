# CMN-C1-068 — Regression test: outer graph get_output() surfaces the domain result
#
# SR #12 re-review (a peer template lesson). AgentBaseGraph.get_output() returns only
# the minimal {output, status, trace_id, correlation_id, node_history} envelope.
# GovernanceDocGraphNode.merge_output() adds domain keys to the OUTER state
# (governance_document, governance_sections, compliance_flags,
# human_review_required, audit_entry), but WITHOUT an outer get_output()
# override those keys are dropped (None) from the dict a compiled-graph
# .invoke() returns to the caller.
#
# AIModelGovernanceAuditTrailAgent.get_output() extends the base envelope with
# the POST-gate domain result, surfaced only when status == SUCCESS (S-3
# fail-closed — never the pre-gate raw governance_document). This test compiles
# the REAL outer graph and asserts a successful invoke actually returns the
# structured domain result; it fails if the override is removed.
#
# S-4 audit events are patched at the node MODULE level (never a sys.modules
# stub, which would break the real `shared` package the framework loads at
# import time). Uses VERIFIED_EXTERNAL caller trust — the real external path;
# NEVER for_internal().

import json

import pytest

from framework.schemas.agent_status import AgentStatus

# A SUCCESS-yielding, high-risk-tier governance payload (high tier -> S-5
# human_review_required True). All PreProcessNode required fields present
# (model_name, intended_use, risk_tier); intended_use is a lawful, non-Article-5
# use so the S-1 gate admits it.
_VALID_PAYLOAD = json.dumps(
    {
        "model_name": "AcmeCreditRiskScorer",
        "model_version": "2.1.0",
        "model_type": "gradient-boosted decision tree",
        "intended_use": (
            "Assist loan officers in assessing consumer credit-risk applications as "
            "decision support; a human makes the final lending decision."
        ),
        "deployment_scope": "internal decision support for retail lending",
        "risk_tier": "high",
        "target_frameworks": ["eu_ai_act", "iso_42001"],
        "training_data_summary": "Anonymised historical retail-loan performance records, 2018-2025.",
        "performance_metrics": {"auc": 0.87, "ks": 0.42},
        "limitations": "Not validated for SME or commercial lending; monitor for demographic drift.",
    }
)

_NODE_MODULES = (
    "pre_process_node",
    "input_validate_node",
    "parse_model_metadata_node",
    "generate_governance_sections_node",
    "compliance_check_node",
    "output_format_node",
    "post_process_node",
)


@pytest.fixture(autouse=True)
def _patch_emit(monkeypatch):
    """Silence S-4 audit-trace emission in every node module (no audit backend in tests)."""
    for mod in _NODE_MODULES:
        try:
            monkeypatch.setattr(f"src.nodes.{mod}.emit_trace_event", lambda *a, **k: None)
        except AttributeError:
            pass  # module not importable here / no emit symbol; fine


def _invoke():
    from framework.schemas.invocation_context import InvocationContext, TrustLevel
    from src.graph.graph import Graph

    agent = Graph()
    agent.compile()
    ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
    return agent.invoke(_VALID_PAYLOAD, ctx=ctx)


class TestOuterGraphGetOutputSurfacesDomainResult:
    """The outer get_output() override must surface the domain result on a
    successful compiled-graph invoke (regression guard for the peer template lesson)."""

    def test_invoke_succeeds(self):
        result = _invoke()
        assert (
            result.get("status") == AgentStatus.SUCCESS.value
        ), f"expected SUCCESS, got {result.get('status')!r}; error_log={result.get('error_log')}"

    def test_base_envelope_output_still_present(self):
        """The base {output, status, ...} envelope must be preserved by the override."""
        result = _invoke()
        assert result.get("output") is not None
        assert "AI MODEL GOVERNANCE DOCUMENT" in result["output"]

    def test_gated_governance_document_is_surfaced(self):
        """The POST-gate, caller-facing document is surfaced (never the pre-gate raw)."""
        result = _invoke()
        doc = result.get("governance_document")
        assert doc, "gated governance_document must be surfaced on SUCCESS"
        assert "AI MODEL GOVERNANCE DOCUMENT" in doc
        # It equals the S-3-gated PostProcessNode outputs.
        assert result.get("formatted_output") == doc
        assert result.get("result") == doc

    def test_structured_domain_fields_are_surfaced_on_success(self):
        """governance_sections / compliance_flags / audit_entry / human_review_required
        are dropped to None by the base get_output(); the override must surface them."""
        result = _invoke()

        assert result.get("governance_sections") is not None, "governance_sections was dropped"
        assert result.get("compliance_flags") is not None, "compliance_flags was dropped"
        assert result.get("audit_entry") is not None, "audit_entry (S-4 artifact) was dropped"
        # High risk tier -> S-5 human-review flag must be True.
        assert result.get("human_review_required") is True

        # And they deserialize to the expected shapes.
        sections = json.loads(result["governance_sections"])
        assert "risk_classification" in sections
        flags = json.loads(result["compliance_flags"])
        assert "frameworks" in flags
        audit = json.loads(result["audit_entry"])
        assert "document_sha256" in audit
