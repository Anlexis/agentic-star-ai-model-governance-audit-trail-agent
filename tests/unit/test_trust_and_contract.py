# Trust-gate coverage and the node-implementation contract.
#
# Both were previously asserted against a deprecated MainNode stub that no graph
# referenced — a module whose docstring said "DO NOT USE" and whose tests were the
# only thing importing it. The stub is gone; these assertions now run against the
# nodes that are actually on the runtime path, and the contract check runs against
# EVERY node rather than one.

import inspect
import json

import pytest
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel

from src.nodes.pre_process_node import PreProcessNode


def _node_classes():
    """Every concrete node class the graphs register."""
    from src.graph.graph import GovernanceDocGraphNode
    from src.nodes.compliance_check_node import ComplianceCheckNode
    from src.nodes.generate_governance_sections_node import GenerateGovernanceSectionsNode
    from src.nodes.input_validate_node import InputValidateNode
    from src.nodes.output_format_node import OutputFormatNode
    from src.nodes.parse_model_metadata_node import ParseModelMetadataNode
    from src.nodes.post_process_node import PostProcessNode

    return [
        PreProcessNode,
        GovernanceDocGraphNode,
        PostProcessNode,
        InputValidateNode,
        ParseModelMetadataNode,
        GenerateGovernanceSectionsNode,
        ComplianceCheckNode,
        OutputFormatNode,
    ]


class TestNodeImplementationContract:
    """Nodes override execute(self, state) — never _invoke_impl or process."""

    @pytest.mark.parametrize("node_cls", _node_classes(), ids=lambda c: c.__name__)
    def test_execute_signature(self, node_cls):
        assert hasattr(node_cls, "execute"), f"{node_cls.__name__} must implement execute()"
        params = list(inspect.signature(node_cls.execute).parameters)
        assert params[:2] == [
            "self",
            "state",
        ], f"{node_cls.__name__}.execute must be execute(self, state), got {params}"

    @pytest.mark.parametrize("node_cls", _node_classes(), ids=lambda c: c.__name__)
    def test_no_config_parameter(self, node_cls):
        """BaseNode.__call__ calls execute(state) with one argument, so a `config`
        parameter can never receive a value. Declaring one advertises a runtime
        configuration path that does not exist — settings reach nodes as seeded
        state instead (see src/graph/context_bridge.py)."""
        params = list(inspect.signature(node_cls.execute).parameters)
        assert "config" not in params, (
            f"{node_cls.__name__}.execute declares a config parameter that the " "framework never supplies"
        )

    @pytest.mark.parametrize("node_cls", _node_classes(), ids=lambda c: c.__name__)
    def test_no_prohibited_overrides(self, node_cls):
        for prohibited in ("_invoke_impl", "process"):
            assert (
                prohibited not in node_cls.__dict__
            ), f"{node_cls.__name__} must not define {prohibited}() — use execute()"


class TestS1TrustGate:
    """Trust-gate coverage.

    Node invocations must go through ``BaseNode.__call__``, which runs the trust
    gate BEFORE ``execute()`` — calling ``execute()`` directly bypasses the gate
    and leaves the negative-authorization path untested.

    PreProcessNode is the only node whose ``required_trust_level`` is above
    ANONYMOUS (VERIFIED_EXTERNAL), so it is the gate's negative-control target;
    every other node in this template is ANONYMOUS.
    """

    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.pre_process_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        self.node = PreProcessNode()

    # Personal-data-free governance payload: all-lowercase, no '@', no digit
    # groups, and no consecutive title-case words — so the platform input filter
    # leaves user_input untouched and the JSON still parses inside execute().
    _PAYLOAD = json.dumps(
        {
            "model_name": "retail credit risk scoring model",
            "intended_use": "assist loan officers with consumer credit risk decision support",
            "risk_tier": "high",
        }
    )

    def test_gate_rejects_untrusted_caller_before_execute(self):
        """Negative control: an ANONYMOUS caller is denied by __call__ before
        execute() runs, so execute()'s output key is absent from the delta."""
        state = {
            "user_input": self._PAYLOAD,
            "input_context": {},
            "caller_trust_level": TrustLevel.ANONYMOUS.value,
        }
        result = self.node(state)
        assert result["status"] == AgentStatus.ERROR.value
        assert any("trust gate denied" in e for e in result["error_log"])
        assert "validated_input" not in result

    def test_gate_admits_trusted_caller(self):
        """Positive control: a VERIFIED_EXTERNAL caller passes the gate and
        execute() runs to SUCCESS, producing validated_input."""
        state = {
            "user_input": self._PAYLOAD,
            "input_context": {},
            "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
        }
        result = self.node(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "validated_input" in result

    def test_status_is_the_serialized_string_not_an_enum_member(self):
        """The checkpointed status must be the serialized string, never a bare
        enum member — msgpack checkpointing cannot round-trip the enum. Exact-type
        check so a str-Enum member still fails here."""
        state = {
            "user_input": self._PAYLOAD,
            "input_context": {},
            "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
        }
        result = self.node(state)
        assert result["status"].__class__ is str
