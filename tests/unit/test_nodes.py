# CMN-C1-068 — Unit Tests: domain nodes + graph wiring
#
# Real, non-stub unit tests. They import the REAL modules merged to develop in
# Wave-1 and assert real behaviour (governance-document content, EU-AI-Act risk
# tiering, framework mapping/citations, the S-1 prohibited-practice refusal, the
# S-3 certification/credential output gate, the append-only audit entry, and the
# two-layer nested graph composition).
#
# S-4 audit events are patched at the node MODULE level (not via a sys.modules
# stub, which would break the real `shared` package the framework loads at import
# time). Patch pattern per node:
#     monkeypatch.setattr("src.nodes.<mod>.emit_trace_event", lambda *a, **k: None)

import json

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from src.schemas.state import from_json, to_json


# ── Shared fixtures / helpers ─────────────────────────────────────────────────


def _governance_payload(**overrides) -> dict:
    """A complete, valid raw model-governance payload (as a caller would POST)."""
    payload = {
        "model_name": "AcmeCreditRiskScorer",
        "model_version": "2.1.0",
        "model_type": "gradient-boosted decision tree",
        "intended_use": "Assist loan officers in assessing consumer credit-risk applications as decision support.",
        "deployment_scope": "internal decision support for retail lending",
        "risk_tier": "high",
        "target_frameworks": ["eu_ai_act", "iso_42001"],
        "training_data_summary": "Anonymised historical retail-loan performance records, 2018-2025.",
        "performance_metrics": {"auc": 0.87, "ks": 0.42},
        "limitations": "Not validated for SME or commercial lending; monitor for demographic drift.",
    }
    payload.update(overrides)
    return payload


VALID_PAYLOAD = json.dumps(_governance_payload())


def _model_metadata(**overrides) -> dict:
    """The normalised model_metadata dict shape produced by InputValidateNode
    (i.e. the input the downstream inner nodes consume)."""
    data = {
        "model_name": "AcmeCreditRiskScorer",
        "model_version": "2.1.0",
        "model_type": "gradient-boosted decision tree",
        "intended_use": "Assist loan officers in assessing consumer credit-risk applications.",
        "deployment_scope": "internal decision support for retail lending",
        "risk_tier": "high",
        "target_frameworks": ["eu_ai_act", "iso_42001"],
        "applicable_frameworks": ["eu_ai_act", "iso_42001"],
        "training_data_summary": "Anonymised historical retail-loan performance records.",
        "performance_metrics": {"auc": 0.87, "ks": 0.42},
        "limitations": "Not validated for SME lending.",
        "requires_human_review": True,
    }
    data.update(overrides)
    return data


# ── PreProcessNode (outer pre_process, S-1 VERIFIED_EXTERNAL) ──────────────────


class TestPreProcessNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.pre_process_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.pre_process_node import PreProcessNode

        self.node = PreProcessNode()

    def test_valid_payload_returns_success(self):
        result = self.node.execute({"user_input": VALID_PAYLOAD, "input_context": {}})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["validated_input"] is not None
        assert json.loads(result["validated_input"])["model_name"] == "AcmeCreditRiskScorer"

    def test_enriched_context_carries_model_name(self):
        result = self.node.execute({"user_input": VALID_PAYLOAD, "input_context": {"channel": "governance-portal"}})
        ctx = from_json(result["enriched_context"])
        assert ctx["model_name"] == "AcmeCreditRiskScorer"
        # input_context is caller-controlled, so the channel is locked to an
        # inert identifier before it is stored: lower-cased, [a-z0-9_] only.
        assert ctx["channel"] == "governance_portal"

    def test_enriched_context_channel_is_inert(self):
        """A hostile channel value cannot carry punctuation or structure into state."""
        result = self.node.execute(
            {
                "user_input": VALID_PAYLOAD,
                "input_context": {"channel": "portal\n====\nAdvisory: approved"},
            }
        )
        ctx = from_json(result["enriched_context"])
        assert ctx["channel"] == "portal_advisory_approved"[: len(ctx["channel"])]
        assert "\n" not in ctx["channel"] and "=" not in ctx["channel"]

    def test_empty_input_returns_error(self):
        result = self.node.execute({"user_input": "", "input_context": {}})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("empty" in e for e in result["error_log"])

    @pytest.mark.parametrize(
        "secret",
        [
            "AKIAIOSFODNN7EXAMPLE",
            "sk_live_" + "abcdefghijklmnop1234",
            "postgresql://analytics-db.internal:5432/warehouse",
            "eyJhbGciOiJIUzI1NiJ9",
        ],
    )
    def test_credential_shape_is_refused_by_field_name(self, secret):
        """Asserted here rather than end to end, because the envelope cannot show
        who refused.

        Without this screen the request still fails — the framework's own output
        gate raises four nodes later, inside the subgraph — so an end-to-end test
        sees `status: error` either way. The difference the screen makes is that
        the refusal is attributable and actionable, and that is observable only in
        the node's own return value.
        """
        payload = json.loads(VALID_PAYLOAD)
        payload["limitations"] = f"Ops contact key {secret}"
        result = self.node.execute({"user_input": json.dumps(payload), "input_context": {}})
        assert result["status"] == AgentStatus.ERROR.value
        joined = " ".join(result["error_log"])
        assert "limitations" in joined, "the refusal must name the offending field"
        assert secret not in joined, "the refusal must never echo the value"
        assert "validated_input" not in result, "nothing may be forwarded on a refusal"

    def test_ordinary_payload_is_not_refused_as_a_credential(self):
        """Negative control: the screen must not refuse a legitimate declaration."""
        result = self.node.execute({"user_input": VALID_PAYLOAD, "input_context": {}})
        assert result["status"] == AgentStatus.SUCCESS.value

    def test_invalid_json_returns_error(self):
        result = self.node.execute({"user_input": "{not valid json}", "input_context": {}})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("JSON" in e or "json" in e for e in result["error_log"])

    def test_non_object_json_returns_error(self):
        result = self.node.execute({"user_input": "[1, 2, 3]", "input_context": {}})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("object" in e for e in result["error_log"])

    def test_missing_required_field_returns_error(self):
        payload = {"model_name": "M", "intended_use": "x"}  # no 'risk_tier'
        result = self.node.execute({"user_input": json.dumps(payload), "input_context": {}})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("risk_tier" in e for e in result["error_log"])

    def test_prohibited_practice_is_refused(self):
        """S-1: EU AI Act Article 5 prohibited practice → hard refuse (no doc generated)."""
        payload = _governance_payload(
            intended_use="Real-time remote biometric identification of pedestrians in public spaces.",
        )
        result = self.node.execute({"user_input": json.dumps(payload), "input_context": {}})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("prohibited practice" in e.lower() for e in result["error_log"])
        assert "validated_input" not in result

    def test_trust_level_is_verified_external(self):
        assert self.node.required_trust_level == TrustLevel.VERIFIED_EXTERNAL

    def test_execute_signature_is_state_first(self):
        import inspect
        from src.nodes.pre_process_node import PreProcessNode

        params = list(inspect.signature(PreProcessNode.execute).parameters.keys())
        assert params[0] == "self" and params[1] == "state"
        assert "_invoke_impl" not in PreProcessNode.__dict__


# ── InputValidateNode (inner domain node 1, ANONYMOUS) ─────────────────────────


class TestInputValidateNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.input_validate_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.input_validate_node import InputValidateNode

        self.node = InputValidateNode()

    def test_valid_input_builds_model_metadata(self):
        result = self.node.execute({"validated_input": VALID_PAYLOAD})
        assert result["status"] == AgentStatus.SUCCESS.value
        data = from_json(result["model_metadata"])
        assert data["model_name"] == "AcmeCreditRiskScorer"
        assert data["risk_tier"] == "high"
        assert data["target_frameworks"] == ["eu_ai_act", "iso_42001"]

    def test_risk_tier_aliases_are_normalised(self):
        payload = _governance_payload(risk_tier="critical")
        data = from_json(self.node.execute({"validated_input": json.dumps(payload)})["model_metadata"])
        assert data["risk_tier"] == "high"  # 'critical' → canonical 'high'

    def test_framework_aliases_are_normalised_and_deduped(self):
        payload = _governance_payload(target_frameworks=["eu", "EU-AI-Act", "iso"])
        data = from_json(self.node.execute({"validated_input": json.dumps(payload)})["model_metadata"])
        # eu / EU-AI-Act → eu_ai_act (deduped); iso → iso_42001
        assert data["target_frameworks"] == ["eu_ai_act", "iso_42001"]

    def test_default_framework_when_none_supplied(self):
        payload = _governance_payload(target_frameworks=[])
        data = from_json(self.node.execute({"validated_input": json.dumps(payload)})["model_metadata"])
        assert data["target_frameworks"] == ["iso_42001"]  # zero-config baseline

    def test_falls_back_to_user_input(self):
        result = self.node.execute({"user_input": VALID_PAYLOAD})
        assert result["status"] == AgentStatus.SUCCESS.value

    def test_empty_model_name_returns_error(self):
        payload = _governance_payload(model_name="")
        result = self.node.execute({"validated_input": json.dumps(payload)})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("model_name" in e for e in result["error_log"])

    def test_empty_intended_use_returns_error(self):
        payload = _governance_payload(intended_use="")
        result = self.node.execute({"validated_input": json.dumps(payload)})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("intended_use" in e for e in result["error_log"])

    def test_invalid_json_returns_error(self):
        result = self.node.execute({"validated_input": "{bad json"})
        assert result["status"] == AgentStatus.ERROR.value

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── ParseModelMetadataNode (inner domain node 2, ANONYMOUS) ────────────────────


class TestParseModelMetadataNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.parse_model_metadata_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.parse_model_metadata_node import ParseModelMetadataNode

        self.node = ParseModelMetadataNode()

    def test_derives_applicable_frameworks_with_baseline(self):
        md = _model_metadata(target_frameworks=["eu_ai_act"], applicable_frameworks=[])
        data = from_json(self.node.execute({"model_metadata": to_json(md)})["model_metadata"])
        # ISO 42001 baseline is always unioned in, order-stable.
        assert data["applicable_frameworks"] == ["eu_ai_act", "iso_42001"]

    def test_high_tier_requires_human_review(self):
        md = _model_metadata(risk_tier="high")
        data = from_json(self.node.execute({"model_metadata": to_json(md)})["model_metadata"])
        assert data["requires_human_review"] is True

    def test_unacceptable_tier_requires_human_review(self):
        md = _model_metadata(risk_tier="unacceptable")
        data = from_json(self.node.execute({"model_metadata": to_json(md)})["model_metadata"])
        assert data["requires_human_review"] is True

    def test_minimal_tier_does_not_require_review(self):
        md = _model_metadata(risk_tier="minimal")
        data = from_json(self.node.execute({"model_metadata": to_json(md)})["model_metadata"])
        assert data["requires_human_review"] is False

    def test_missing_model_metadata_returns_error(self):
        result = self.node.execute({})
        assert result["status"] == AgentStatus.ERROR.value

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── GenerateGovernanceSectionsNode (inner domain node 3, ANONYMOUS) ────────────


class TestGenerateGovernanceSectionsNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.generate_governance_sections_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.generate_governance_sections_node import GenerateGovernanceSectionsNode

        self.node = GenerateGovernanceSectionsNode()

    def test_generates_all_seven_sections(self):
        result = self.node.execute({"model_metadata": to_json(_model_metadata())})
        assert result["status"] == AgentStatus.SUCCESS.value
        sections = from_json(result["governance_sections"])
        assert set(sections.keys()) == {
            "model_overview",
            "intended_use_and_scope",
            "training_data_summary",
            "performance_characteristics",
            "limitations_and_risks",
            "risk_classification",
            "human_oversight_measures",
        }

    def test_sections_use_advisory_not_certification_framing(self):
        """S-3 relevance: generated risk_classification must NOT assert compliance."""
        sections = from_json(self.node.execute({"model_metadata": to_json(_model_metadata())})["governance_sections"])
        risk = sections["risk_classification"].lower()
        assert "advisory" in risk
        assert "is compliant" not in risk

    def test_overview_reflects_model_fields(self):
        sections = from_json(self.node.execute({"model_metadata": to_json(_model_metadata())})["governance_sections"])
        assert "AcmeCreditRiskScorer" in sections["model_overview"]

    def test_high_tier_recommends_human_review_text(self):
        md = _model_metadata(risk_tier="high", requires_human_review=True)
        sections = from_json(self.node.execute({"model_metadata": to_json(md)})["governance_sections"])
        assert "human governance review is recommended" in sections["human_oversight_measures"].lower()

    def test_missing_model_metadata_returns_error(self):
        result = self.node.execute({})
        assert result["status"] == AgentStatus.ERROR.value

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── ComplianceCheckNode (inner domain node 4, ANONYMOUS) ───────────────────────


class TestComplianceCheckNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.compliance_check_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.compliance_check_node import ComplianceCheckNode

        self.node = ComplianceCheckNode()

    def test_maps_frameworks_and_emits_citations(self):
        result = self.node.execute({"model_metadata": to_json(_model_metadata())})
        assert result["status"] == AgentStatus.SUCCESS.value
        flags = from_json(result["compliance_flags"])
        assert flags["frameworks"] == ["eu_ai_act", "iso_42001"]
        assert set(flags["citations"].keys()) == {"eu_ai_act", "iso_42001"}
        assert any("Art. 11" in c or "Annex IV" in c for c in flags["citations"]["eu_ai_act"])

    def test_high_tier_sets_human_review_required(self):
        result = self.node.execute({"model_metadata": to_json(_model_metadata(risk_tier="high"))})
        assert result["human_review_required"] is True
        flags = from_json(result["compliance_flags"])
        assert flags["human_review_required"] is True

    def test_minimal_tier_does_not_set_human_review(self):
        result = self.node.execute({"model_metadata": to_json(_model_metadata(risk_tier="minimal"))})
        assert result["human_review_required"] is False

    def test_advisory_note_never_certifies(self):
        flags = from_json(self.node.execute({"model_metadata": to_json(_model_metadata())})["compliance_flags"])
        assert "advisory only" in flags["advisory_note"]
        assert "does not constitute a certification" in flags["advisory_note"]

    def test_falls_back_to_target_frameworks(self):
        md = _model_metadata(applicable_frameworks=[], target_frameworks=["iso_42001"])
        flags = from_json(self.node.execute({"model_metadata": to_json(md)})["compliance_flags"])
        assert flags["frameworks"] == ["iso_42001"]

    def test_missing_model_metadata_returns_error(self):
        result = self.node.execute({})
        assert result["status"] == AgentStatus.ERROR.value

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── OutputFormatNode (inner domain node 5, ANONYMOUS) ──────────────────────────


class TestOutputFormatNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.output_format_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.output_format_node import OutputFormatNode

        self.node = OutputFormatNode()

    def _sections(self) -> dict:
        return {
            "model_overview": "Model Name:    AcmeCreditRiskScorer",
            "intended_use_and_scope": "Intended Use: credit-risk decision support",
            "training_data_summary": "Anonymised loan records.",
            "performance_characteristics": "  - auc: 0.87",
            "limitations_and_risks": "Not validated for SME lending.",
            "risk_classification": "  Assessed Risk Tier: HIGH",
            "human_oversight_measures": "Human review recommended.",
        }

    def _compliance(self) -> dict:
        return {
            "frameworks": ["eu_ai_act", "iso_42001"],
            "citations": {"eu_ai_act": ["EU AI Act Art. 11 & Annex IV — technical documentation"]},
            "human_review_required": True,
            "risk_tier": "high",
            "advisory_note": "This mapping is advisory only; it does not constitute a certification.",
        }

    def _state(self):
        return {
            "governance_sections": to_json(self._sections()),
            "compliance_flags": to_json(self._compliance()),
            "model_metadata": to_json(_model_metadata()),
        }

    def test_assembles_full_document(self):
        result = self.node.execute(self._state())
        assert result["status"] == AgentStatus.SUCCESS.value
        doc = result["governance_document"]
        # `result` is the caller-facing channel the framework falls back to when
        # formatted_output is falsy. Only PostProcessNode writes it, and only
        # after the output gate has run — an inner node writing the PRE-gate
        # document there would put un-gated text one merge away from the caller.
        assert "result" not in result
        assert "AI MODEL GOVERNANCE DOCUMENT" in doc
        for header in (
            "1. Model Overview",
            "2. Intended Use and Scope",
            "3. Training Data Summary",
            "4. Performance Characteristics",
            "5. Limitations and Risks",
            "6. Risk Classification",
            "7. Human Oversight Measures",
            "REGULATORY MAPPING NOTE",
        ):
            assert header in doc, f"missing section header: {header}"

    def test_human_review_recommended_note_present(self):
        doc = self.node.execute(self._state())["governance_document"]
        assert "Human Governance Review:  RECOMMENDED" in doc

    def test_audit_entry_is_content_addressed(self):
        import hashlib

        result = self.node.execute(self._state())
        audit = from_json(result["audit_entry"])
        assert set(audit.keys()) == {
            "generated_at",
            "model_name",
            "model_version",
            "generator_id",
            "document_sha256",
            "frameworks",
            "redacted_fields",
        }
        assert audit["redacted_fields"] == []
        expected_sha = hashlib.sha256(result["governance_document"].encode("utf-8")).hexdigest()
        assert audit["document_sha256"] == expected_sha

    def test_missing_sections_returns_error(self):
        result = self.node.execute({"model_metadata": to_json(_model_metadata())})
        assert result["status"] == AgentStatus.ERROR.value

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── PostProcessNode (outer post_process, output gate, ANONYMOUS) ──────────────

# Every state field the output gate must clear on a violation. Kept as one
# inventory so a new output-bearing field cannot quietly join the state without
# joining the cleared set — test_cleared_set_covers_every_output_field pins it.
_CLEARED_FIELDS = (
    "governance_document",
    "governance_sections",
    "compliance_flags",
    "audit_entry",
)


class TestPostProcessNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.post_process_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.post_process_node import PostProcessNode

        self.node = PostProcessNode()

    def test_clean_document_passes_gate(self):
        doc = "AI MODEL GOVERNANCE DOCUMENT\nModel: X\nThis document is advisory."
        result = self.node.execute({"governance_document": doc, "human_review_required": True})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["formatted_output"] == doc
        assert result["result"] == doc

    def test_empty_document_is_refused_not_reported_as_success(self):
        """Reaching post_process means the workflow reported success, so an empty
        document is a contradiction. Releasing a placeholder under SUCCESS would
        hand the caller a governance artifact that documents nothing while telling
        them everything worked."""
        result = self.node.execute({"governance_document": "", "human_review_required": False})
        assert result["status"] == AgentStatus.ERROR.value
        assert result["formatted_output"], "the notice must be truthy — a falsy one re-opens the fallback"
        assert "WITHHELD" in result["formatted_output"]
        assert "result" in result and result["result"] is None
        for field in _CLEARED_FIELDS:
            assert field in result and result[field] is None

    def test_output_gate_withholds_credential_leak(self):
        leaky = "AI MODEL GOVERNANCE DOCUMENT\ntoken=sk-abcdefghij0123456789ABCDEF"
        result = self.node.execute({"governance_document": leaky, "human_review_required": True})
        self._assert_contained(result, leaky)

    def test_output_gate_withholds_certification_language(self):
        cert = "AI MODEL GOVERNANCE DOCUMENT\nThis system is fully compliant with the EU AI Act."
        result = self.node.execute({"governance_document": cert, "human_review_required": False})
        self._assert_contained(result, cert)

    @pytest.mark.parametrize(
        "secret",
        [
            "AKIAIOSFODNN7EXAMPLE",  # AWS access key id
            "sk_live_" + "abcdefghijklmnop1234",  # Stripe secret key
            "postgresql://analytics-db.internal:5432/warehouse",  # connection string
            "eyJhbGciOiJIUzI1NiJ9",  # two-segment JWT
        ],
    )
    def test_output_gate_matches_the_framework_block_set(self, secret):
        """Detector parity: a value the framework catches must not pass this gate.

        A narrower local set is a bypass, not a smaller gate — the framework
        raises on the way out and the node wrapper then discards this node's whole
        delta, clearing included. Each shape here passed the previous local list.
        """
        from framework.security.credential_detector import detect_credentials

        doc = f"AI MODEL GOVERNANCE DOCUMENT\nLimitations: {secret}"
        assert detect_credentials(doc), "probe is wrong: the framework must catch this shape"
        result = self.node.execute({"governance_document": doc, "human_review_required": False})
        self._assert_contained(result, doc)

    def _assert_contained(self, result, released_text):
        """The error envelope carries a truthy notice and nothing of the document."""
        assert result["status"] == AgentStatus.ERROR.value
        # Truthy: AgentBaseGraph.get_output() resolves the caller's output as
        # `formatted_output or result`, so a falsy notice re-opens the fallback.
        assert result["formatted_output"]
        assert "WITHHELD" in result["formatted_output"]
        # Presence AND emptiness. LangGraph merges partial deltas, so a key
        # omitted from the mapping keeps its previous value in state — asserting
        # only `not result.get(field)` passes on a gate that cleared nothing.
        assert "result" in result and result["result"] is None
        for field in _CLEARED_FIELDS:
            assert field in result, f"{field} must be present in the cleared delta"
            assert result[field] is None, f"{field} must be cleared on a violation"
        # The reason names the violation class, never the text that caused it.
        joined = " ".join(result["error_log"]) + result["formatted_output"]
        for line in released_text.splitlines()[1:]:
            assert line.strip() not in joined

    def test_security_gate_output_helper_detects_and_clears(self):
        from src.nodes.post_process_node import _security_gate_output

        assert _security_gate_output("sk-abcdefghij0123456789ABCDEF") is not None
        assert _security_gate_output("Bearer abcdefgh12345678") is not None
        assert _security_gate_output("This system is compliant.") is not None
        assert _security_gate_output("This guarantees compliance.") is not None
        assert _security_gate_output("A clean, advisory governance document.") is None

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── Graph wiring: outer AgentBaseGraph + inner BaseGraph (two-layer nested) ─────


class TestOuterGraphComposition:
    def test_registers_five_backbone_slots(self):
        from src.graph.graph import (
            AIModelGovernanceAuditTrailAgent,
            GovernanceDocGraphNode,
        )
        from src.nodes.post_process_node import PostProcessNode
        from src.nodes.pre_process_node import PreProcessNode

        agent = AIModelGovernanceAuditTrailAgent()
        agent.compile()
        assert set(agent._nodes.keys()) == {
            "initialize",
            "pre_process",
            "main",
            "post_process",
            "finalize",
        }
        assert isinstance(agent._nodes["pre_process"], PreProcessNode)
        assert isinstance(agent._nodes["main"], GovernanceDocGraphNode)
        assert isinstance(agent._nodes["post_process"], PostProcessNode)

    def test_name_and_state_schema(self):
        from src.schemas.state import State
        from src.graph.graph import AIModelGovernanceAuditTrailAgent

        agent = AIModelGovernanceAuditTrailAgent()
        assert agent.name == "AIModelGovernanceAuditTrailAgent"
        assert agent.state_schema is State

    def test_graph_alias_matches_real_class(self):
        from src.graph.graph import Graph, AIModelGovernanceAuditTrailAgent

        assert Graph is AIModelGovernanceAuditTrailAgent

    def test_main_slot_graphnode_contracts(self):
        from src.graph.graph import GovernanceDocGraphNode

        node = GovernanceDocGraphNode()
        assert node.error_strategy == "propagate"
        assert node.propagate_hitl is False
        # extract_input prefers validated_input, falls back to user_input
        assert node.extract_input({"validated_input": "V", "user_input": "U"}) == "V"
        assert node.extract_input({"user_input": "U"}) == "U"

    def test_merge_output_maps_subresult_keys(self):
        from src.graph.graph import GovernanceDocGraphNode

        node = GovernanceDocGraphNode()
        sub_result = {
            "governance_document": "DOC",
            "governance_sections": "{}",
            "compliance_flags": "{}",
            "human_review_required": True,
            "audit_entry": "{}",
            "status": AgentStatus.SUCCESS.value,
            "node_history": ["x"],  # not forwarded by merge_output
            "correlation_id": "c",  # not forwarded by merge_output
        }
        delta = node.merge_output({}, sub_result)
        assert delta["governance_document"] == "DOC"
        assert delta["human_review_required"] is True
        assert delta["status"] == AgentStatus.SUCCESS.value
        assert set(delta.keys()) == {
            "governance_document",
            "governance_sections",
            "compliance_flags",
            "human_review_required",
            "audit_entry",
            "redacted_fields",
            "status",
        }
        # `result` is deliberately NOT carried: the inner graph's result would be
        # the pre-gate document, and only PostProcessNode may write the outer one.
        assert "result" not in delta


class TestInnerDomainGraph:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        for mod in (
            "input_validate_node",
            "parse_model_metadata_node",
            "generate_governance_sections_node",
            "compliance_check_node",
            "output_format_node",
        ):
            monkeypatch.setattr(f"src.nodes.{mod}.emit_trace_event", lambda *a, **k: None)

    def test_registers_five_domain_nodes(self):
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        g = DomainWorkflowGraph()
        g.register_nodes()
        assert set(g._nodes.keys()) == {
            "input_validate",
            "parse_model_metadata",
            "generate_governance_sections",
            "compliance_check",
            "output_format",
        }

    def test_name_and_state_schema(self):
        from src.schemas.state import State
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        g = DomainWorkflowGraph()
        assert g.name == "cmn_c1_068_ai_governance_doc_workflow"
        assert g.state_schema is State

    def test_inner_graph_invoke_produces_document(self):
        """Standalone inner-graph invoke (ANONYMOUS caller) runs the linear pipeline
        and shapes the get_output() dict consumed by the outer merge_output()."""
        from framework.schemas.invocation_context import InvocationContext, TrustLevel
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        g = DomainWorkflowGraph()
        g.compile()
        ctx = InvocationContext(caller_trust_level=TrustLevel.ANONYMOUS)
        result = g.invoke(VALID_PAYLOAD, ctx=ctx)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["governance_document"] is not None
        assert result["human_review_required"] is True  # high risk tier
        assert "AI MODEL GOVERNANCE DOCUMENT" in result["governance_document"]
