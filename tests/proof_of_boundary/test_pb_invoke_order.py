# PB-6: Invoke Execution Order Verification
# Verifies BaseNode.__call__() enforces: S-1 trust gate -> S-4 node_start ->
# S-2 _security_gate_input() -> execute() -> S-3 _security_gate_output() ->
# S-4 node_complete, for every concrete node under src/nodes/.
#
# Also verifies the full backbone invoke order for the outer
# AIModelGovernanceAuditTrailAgent (two-layer nested graph):
#   InitializeNode -> PreProcessNode (pre_process) -> GovernanceDocGraphNode (main)
#   -> PostProcessNode (post_process) -> FinalizeNode
#
# Plus the domain proof-of-boundary invariants for CMN-C1-068:
#   PB-PROHIBITED  — S-1 hard-refuses an EU AI Act Article 5 prohibited practice
#   PB-CERT        — S-3 withholds output that asserts certification / compliance
#   PB-AUDITAPPEND — the audit-trail entry is a content-addressed (SHA-256),
#                    append-only compliance artifact, never a mutation of a prior one
#
# PB-6 invoke uses VERIFIED_EXTERNAL caller trust (the real external path) — NEVER
# for_internal(). A VERIFIED_EXTERNAL InvocationContext exercises the same code path a
# real STG caller uses: it clears the outer PreProcessNode S-1 gate
# (required_trust_level = VERIFIED_EXTERNAL) AND passes through the inner ANONYMOUS
# domain nodes. for_internal() (INTERNAL) would not represent a real external caller,
# so it is deliberately not used.

import hashlib
import importlib
import inspect
import json
import pkgutil
from pathlib import Path

import pytest

# ── Template-specific constants ───────────────────────────────────────────────

# Class name of the node in the `main` backbone slot.
_MAIN_SLOT_NODE = "GovernanceDocGraphNode"

# A SUCCESS-yielding AI-model-governance payload for the backbone invoke test.
# High risk tier -> human_review_required True (S-5). All PreProcessNode required
# fields present (model_name, intended_use, risk_tier); intended_use is a lawful,
# non-Article-5 use so PreProcessNode admits it.
#
# CONTRACT (reference_newgen_stg_deploy): deploy/invoke_payload.json["input"]
# MUST equal this exact string — the Stage-5 deploy-stg evidence invoke and the
# PB-6 test must exercise the identical payload. test_invoke_payload_matches_pb6
# below asserts that equality so the two can never drift.
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

# ─────────────────────────────────────────────────────────────────────────────


def _discover_node_classes() -> list[type]:
    """Import every module under src/nodes/ and collect concrete BaseNode subclasses."""
    from framework.nodes.base_node import BaseNode

    try:
        pkg = importlib.import_module("src.nodes")
    except ImportError:
        return []

    discovered = []
    for _, modname, _ in pkgutil.walk_packages(pkg.__path__, prefix="src.nodes."):
        module = importlib.import_module(modname)
        for attr in vars(module).values():
            if (
                isinstance(attr, type)
                and issubclass(attr, BaseNode)
                and attr is not BaseNode
                and attr.__module__ == modname
                and not inspect.isabstract(attr)
            ):
                discovered.append(attr)
    return discovered


def _patch_domain_emit(monkeypatch):
    """Patch emit_trace_event in every domain node module (avoids audit-backend calls)."""
    for mod_suffix in (
        "pre_process_node",
        "input_validate_node",
        "parse_model_metadata_node",
        "generate_governance_sections_node",
        "compliance_check_node",
        "output_format_node",
        "post_process_node",
    ):
        try:
            monkeypatch.setattr(
                f"src.nodes.{mod_suffix}.emit_trace_event",
                lambda *a, **k: None,
            )
        except AttributeError:
            pass  # module not yet imported / no emit symbol; fine


class TestInvokeOrder:
    """PB-6: __call__ must run S-1 -> node_start -> S-2 -> execute() -> S-3 -> node_complete."""

    def test_call_order_for_every_node(self, monkeypatch):
        node_classes = _discover_node_classes()
        if not node_classes:
            pytest.skip("no concrete BaseNode subclasses found under src/nodes/")

        import framework.nodes.base_node as base_node_module

        failures: list[str] = []
        for node_cls in node_classes:
            order: list[str] = []
            monkeypatch.setattr(
                base_node_module,
                "emit_trace_event",
                lambda event_type, _payload, _state, _o=order: _o.append(f"event:{event_type}"),
            )

            for method_name, label in (
                ("_security_gate_input", "security_gate_input"),
                ("execute", "execute"),
                ("_security_gate_output", "security_gate_output"),
            ):
                original = getattr(node_cls, method_name)

                def spy(self, arg, _o=order, _label=label, _orig=original):
                    _o.append(_label)
                    return _orig(self, arg)

                monkeypatch.setattr(node_cls, method_name, spy)

            instance = node_cls()
            # caller trust == the node's required level so the S-1 gate always passes here;
            # the gate-denial branch is asserted separately in TestS1TrustGate.
            state = {
                "caller_trust_level": node_cls.required_trust_level.value,
                "correlation_id": "pb6-invoke-order-test",
            }
            instance(state)

            expected = [
                "event:node_start",
                "security_gate_input",
                "execute",
                "security_gate_output",
                "event:node_complete",
            ]
            if order != expected:
                failures.append(
                    f"{node_cls.__name__}: invoke order violation.\n" f"expected: {expected}\nactual:   {order}"
                )

        assert not failures, "\n\n".join(failures)


class TestS1TrustGate:
    """PB-6 S-1: the trust gate in BaseNode.__call__ runs BEFORE execute() and denies
    a caller whose trust is below the node's required_trust_level (NODE level)."""

    def test_pre_process_denies_anonymous_caller(self, monkeypatch):
        """PreProcessNode (required VERIFIED_EXTERNAL) must refuse an ANONYMOUS caller."""
        _patch_domain_emit(monkeypatch)
        from framework.schemas.agent_status import AgentStatus
        from framework.schemas.invocation_context import TrustLevel
        from src.nodes.pre_process_node import PreProcessNode

        node = PreProcessNode()
        result = node(
            {
                "caller_trust_level": TrustLevel.ANONYMOUS.value,
                "user_input": _VALID_PAYLOAD,
                "correlation_id": "pb6-s1-denial",
            }
        )
        assert result["status"] == AgentStatus.ERROR.value
        assert any(
            "trust gate" in e.lower() for e in result.get("error_log", [])
        ), f"expected an S-1 trust-gate denial, got error_log={result.get('error_log')}"

    def test_pre_process_admits_verified_external_caller(self, monkeypatch):
        """The same node admits a VERIFIED_EXTERNAL caller and runs execute() to SUCCESS."""
        _patch_domain_emit(monkeypatch)
        from framework.schemas.agent_status import AgentStatus
        from framework.schemas.invocation_context import TrustLevel
        from src.nodes.pre_process_node import PreProcessNode

        node = PreProcessNode()
        result = node(
            {
                "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
                "user_input": _VALID_PAYLOAD,
                "input_context": {},
                "correlation_id": "pb6-s1-admit",
            }
        )
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["validated_input"] is not None


class TestBackboneInvokeOrder:
    """PB-6 backbone: a full Graph().invoke() runs the 5-node backbone in order.

    Backbone order: InitializeNode -> PreProcessNode (pre_process) ->
                    GovernanceDocGraphNode (main) ->
                    PostProcessNode (post_process) -> FinalizeNode

    Uses VERIFIED_EXTERNAL caller trust — the real external path.
    InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL) is mandatory;
    NEVER use for_internal(), which would not represent a real external caller.
    """

    def _invoke(self, monkeypatch):
        _patch_domain_emit(monkeypatch)
        from framework.schemas.invocation_context import InvocationContext, TrustLevel
        from src.graph.graph import Graph

        agent = Graph()
        agent.compile()
        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        return agent.invoke(_VALID_PAYLOAD, ctx=ctx)

    def test_backbone_invoke_succeeds_and_returns_output(self, monkeypatch):
        from framework.schemas.agent_status import AgentStatus

        result = self._invoke(monkeypatch)
        assert result.get("status") == AgentStatus.SUCCESS.value, (
            f"Expected status={AgentStatus.SUCCESS.value!r}, got: {result.get('status')!r}\n"
            f"error_log: {result.get('error_log')}"
        )
        assert result.get("output") is not None, "output must be set after a successful invoke"
        # The assembled governance document must be present in the surfaced output.
        assert "AI MODEL GOVERNANCE DOCUMENT" in result["output"]
        assert "AcmeCreditRiskScorer" in result["output"]

    def test_backbone_node_history_matches_expected_order(self, monkeypatch):
        result = self._invoke(monkeypatch)
        history = result.get("node_history", [])
        assert history == [
            "InitializeNode",
            "PreProcessNode",
            "GovernanceDocGraphNode",
            "PostProcessNode",
            "FinalizeNode",
        ], f"unexpected backbone node_history: {history}"

    def test_main_slot_is_governance_doc_graph_node(self):
        """The `main` backbone slot must be GovernanceDocGraphNode (a GraphNode)."""
        from framework.nodes.graph_node import GraphNode
        from src.graph.graph import AIModelGovernanceAuditTrailAgent, GovernanceDocGraphNode

        agent = AIModelGovernanceAuditTrailAgent()
        agent.compile()
        main_node = agent._nodes.get("main")
        assert main_node is not None, "main slot must be registered"
        assert isinstance(
            main_node, GovernanceDocGraphNode
        ), f"main slot must be GovernanceDocGraphNode, got {type(main_node).__name__}"
        assert isinstance(main_node, GraphNode), "main slot node must subclass GraphNode (nested contract)"
        assert main_node.__class__.__name__ == _MAIN_SLOT_NODE

    def test_invoke_payload_matches_pb6(self):
        """deploy/invoke_payload.json["input"] MUST equal _VALID_PAYLOAD (Stage-5 alignment).

        The deploy-stg evidence invoke (stg_invoke_evidence.py POSTs invoke_payload.json
        as the request body) must exercise the same payload PB-6 asserts yields SUCCESS.
        """
        repo_root = Path(__file__).resolve().parents[2]
        payload_file = repo_root / "deploy" / "invoke_payload.json"
        assert payload_file.exists(), "deploy/invoke_payload.json is required for deploy-stg"
        body = json.loads(payload_file.read_text())
        assert body.get("input") == _VALID_PAYLOAD, (
            "deploy/invoke_payload.json['input'] must equal the PB-6 _VALID_PAYLOAD "
            "(reference_newgen_stg_deploy contract)"
        )
        # And the payload the STG server forwards to agent.invoke() must itself be a
        # valid, PreProcessNode-parseable governance JSON object.
        governance = json.loads(body["input"])
        for required in ("model_name", "intended_use", "risk_tier"):
            assert required in governance, f"invoke_payload input missing required field: {required}"


class TestPBProhibitedPractice:
    """PB-PROHIBITED: the S-1 boundary hard-refuses an EU AI Act Article 5 prohibited
    practice — no governance document is produced for a prohibited AI system."""

    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.pre_process_node.emit_trace_event", lambda *a, **k: None)

    def test_prohibited_intended_use_is_refused(self):
        from framework.schemas.agent_status import AgentStatus
        from src.nodes.pre_process_node import PreProcessNode

        payload = json.dumps(
            {
                "model_name": "CityWatchAI",
                "intended_use": "Real-time remote biometric identification of pedestrians in public spaces.",
                "risk_tier": "high",
            }
        )
        result = PreProcessNode().execute({"user_input": payload, "input_context": {}})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("prohibited practice" in e.lower() for e in result["error_log"])
        # No validated_input is emitted — the request never reaches the doc pipeline.
        assert "validated_input" not in result

    def test_social_scoring_is_refused(self):
        from framework.schemas.agent_status import AgentStatus
        from src.nodes.pre_process_node import PreProcessNode

        payload = json.dumps(
            {
                "model_name": "CitizenScore",
                "intended_use": "General-purpose social scoring of citizens for public benefit eligibility.",
                "risk_tier": "high",
            }
        )
        result = PreProcessNode().execute({"user_input": payload, "input_context": {}})
        assert result["status"] == AgentStatus.ERROR.value

    def test_lawful_use_is_admitted(self):
        from framework.schemas.agent_status import AgentStatus
        from src.nodes.pre_process_node import PreProcessNode

        result = PreProcessNode().execute({"user_input": _VALID_PAYLOAD, "input_context": {}})
        assert result["status"] == AgentStatus.SUCCESS.value


class TestPBCertificationGate:
    """PB-CERT: the S-3 output boundary must withhold any document that asserts a
    system IS compliant / certified — governance docs are advisory, never certifying."""

    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.post_process_node.emit_trace_event", lambda *a, **k: None)

    def test_certification_language_is_withheld(self):
        from framework.schemas.agent_status import AgentStatus
        from src.nodes.post_process_node import PostProcessNode

        doc = "AI MODEL GOVERNANCE DOCUMENT\nThe model meets all regulatory requirements and is fully compliant."
        result = PostProcessNode().execute({"governance_document": doc, "human_review_required": False})
        assert result["status"] == AgentStatus.ERROR.value
        # Truthy notice: the framework resolves the caller's output as
        # `formatted_output or result`, so a falsy one re-opens the fallback.
        assert result["formatted_output"]
        assert "WITHHELD" in result["formatted_output"]
        # `result` is the un-gated channel — present in the delta and cleared.
        assert "result" in result and result["result"] is None
        for field in ("governance_document", "governance_sections", "compliance_flags", "audit_entry"):
            assert field in result, f"{field} must be present in the cleared delta"
            assert result[field] is None, f"{field} must be cleared on a violation"
        assert any("output gate blocked" in e for e in result["error_log"])
        assert not any("fully compliant" in e for e in result["error_log"])

    def test_advisory_language_passes(self):
        from framework.schemas.agent_status import AgentStatus
        from src.nodes.post_process_node import PostProcessNode

        doc = (
            "AI MODEL GOVERNANCE DOCUMENT\nThis document supports a compliance assessment "
            "under the applicable frameworks and is advisory only."
        )
        result = PostProcessNode().execute({"governance_document": doc, "human_review_required": True})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["formatted_output"] == doc

    def test_gate_helper_flags_each_certification_phrase(self):
        from src.nodes.post_process_node import _security_gate_output

        for phrase in (
            "The system is compliant.",
            "It is fully compliant.",
            "This is certified compliant.",
            "The vendor guarantees compliance.",
            "The model meets all requirements.",
        ):
            assert _security_gate_output(phrase) is not None, f"should flag: {phrase!r}"


class TestPBAuditAppendOnly:
    """PB-AUDITAPPEND: the audit-trail entry (S-4) is an append-only compliance
    artifact — content-addressed (SHA-256 of the document), tamper-evident, and
    deterministic for identical content. OutputFormatNode always emits a fresh
    entry for the current document; it never mutates a prior one."""

    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.output_format_node.emit_trace_event", lambda *a, **k: None)

    def _state(self):
        from src.schemas.state import to_json

        sections = {
            "model_overview": "Model Name:    AcmeCreditRiskScorer",
            "intended_use_and_scope": "Intended Use: credit-risk decision support",
            "training_data_summary": "Anonymised loan records.",
            "performance_characteristics": "  - auc: 0.87",
            "limitations_and_risks": "Not validated for SME lending.",
            "risk_classification": "  Assessed Risk Tier: HIGH",
            "human_oversight_measures": "Human review recommended.",
        }
        compliance = {
            "frameworks": ["eu_ai_act", "iso_42001"],
            "citations": {"eu_ai_act": ["EU AI Act Art. 11 & Annex IV"]},
            "human_review_required": True,
            "risk_tier": "high",
            "advisory_note": "Advisory only; does not constitute a certification.",
        }
        metadata = {
            "model_name": "AcmeCreditRiskScorer",
            "model_version": "2.1.0",
            "applicable_frameworks": ["eu_ai_act", "iso_42001"],
        }
        return {
            "governance_sections": to_json(sections),
            "compliance_flags": to_json(compliance),
            "model_metadata": to_json(metadata),
        }

    def test_audit_entry_hash_matches_document(self):
        from src.nodes.output_format_node import OutputFormatNode
        from src.schemas.state import from_json

        result = OutputFormatNode().execute(self._state())
        audit = from_json(result["audit_entry"])
        expected = hashlib.sha256(result["governance_document"].encode("utf-8")).hexdigest()
        assert audit["document_sha256"] == expected
        assert audit["model_name"] == "AcmeCreditRiskScorer"

    def test_hash_is_deterministic_for_identical_document(self):
        from src.nodes.output_format_node import OutputFormatNode
        from src.schemas.state import from_json

        node = OutputFormatNode()
        a = from_json(node.execute(self._state())["audit_entry"])["document_sha256"]
        b = from_json(node.execute(self._state())["audit_entry"])["document_sha256"]
        assert a == b, "identical document content must yield an identical audit hash"

    def test_hash_changes_when_document_changes(self):
        from src.nodes.output_format_node import OutputFormatNode
        from src.schemas.state import from_json, to_json

        node = OutputFormatNode()
        base = from_json(node.execute(self._state())["audit_entry"])["document_sha256"]

        tampered = self._state()
        sections = from_json(tampered["governance_sections"])
        sections["model_overview"] = "Model Name:    DifferentModel"
        tampered["governance_sections"] = to_json(sections)
        changed = from_json(node.execute(tampered)["audit_entry"])["document_sha256"]

        assert base != changed, "a change in document content must change the audit hash"
