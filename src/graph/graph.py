"""AgentCore Platform v1.0"""

# CMN-C1-068 — Outer graph (AgentBaseGraph; two-layer nested architecture)
#
# CMN-C1-068 is an industry-agnostic document-generation template. It uses a
# two-layer nested STRUCTURAL pattern: the domain workflow is encapsulated in an
# inner BaseGraph so each step is a single-responsibility node. The graph
# structure is orthogonal to the template's category.
#
# Architecture:
#
#   Outer backbone (fixed — do NOT override add_edges()):
#     START → initialize → pre_process → main → {route} → post_process → finalize → END
#                                             ↓ (RETRY, max_retry)
#                                          pre_process
#
#   `main` slot is a GraphNode subclass (GovernanceDocGraphNode) that delegates
#   the full domain workflow to DomainWorkflowGraph (inner BaseGraph).
#
# Directory layout:
#   src/graph/graph.py                 ← outer graph (this file)
#   src/graph/domain_workflow_graph.py ← inner graph (multi-step topology)
#   src/graph/context_bridge.py        ← outer→inner runtime-settings bridge
#
# Rules enforced:
#   ✅ AIModelGovernanceAuditTrailAgent inherits AgentBaseGraph (framework base class)
#   ✅ super().register_nodes() called first (fills initialize + finalize)
#   ✅ GovernanceDocGraphNode assigned to self._nodes["main"]
#   ✅ PreProcessNode (VERIFIED_EXTERNAL) in the pre_process slot (input trust gate)
#   ✅ PostProcessNode (ANONYMOUS) in the post_process slot (output gate)
#   ✅ merge_output() returns only changed keys
#   ✅ get_output() surfaces the gated domain result and withholds it otherwise
#   ✅ class name matches config/agent.yaml class: field exactly
#   ❌ add_edges() NOT overridden on the outer graph
#   ❌ No platform SDK imports

from pathlib import Path
from typing import Any, ClassVar, Dict, Optional, Tuple

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.graph.context_bridge import stash_governance_settings
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State, to_json

# config/agent.yaml is the static registration manifest and holds identity only.
# Every runtime parameter lives in config/config.yaml — the file the platform
# registry loads and passes back as Graph(config=...).
_RUNTIME_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"

# Bounds for each declared governance setting: (minimum, maximum, default).
# A declared value outside its bound is not forwarded — the node then falls back
# to this default — so a malformed configuration file can neither crash graph
# construction nor silently remove a cap.
_GOVERNANCE_BOUNDS: Dict[str, Tuple[int, int, int]] = {
    "max_input_bytes": (1_024, 1_048_576, 32_768),
    "max_field_chars": (80, 20_000, 2_000),
    "max_performance_metrics": (1, 200, 25),
    "max_target_frameworks": (1, 50, 10),
}


def runtime_config() -> Dict[str, Any]:
    """Read the runtime parameters from config/config.yaml.

    The platform registry loads this file and passes it as ``Graph(config=...)``;
    the standalone entry point (src/api/server.py) reads it through this function
    so both paths run on identical configuration. Returns an empty mapping — never
    raises — when the file is absent or unparseable, so a packaging accident
    degrades to the built-in defaults instead of failing at import time.
    """
    try:
        import yaml

        with open(_RUNTIME_CONFIG_PATH, "r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def declared_governance(config: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """Return the validated `governance:` settings from a runtime config mapping.

    Every key is bounds-checked and defaulted individually. Booleans are rejected
    explicitly: ``isinstance(True, int)`` is True in Python, so ``max_field_chars:
    true`` would otherwise pass as the integer 1 and truncate every field to a
    single character.
    """
    raw = (config or {}).get("governance")
    declared: Dict[str, Any] = raw if isinstance(raw, dict) else {}
    settings: Dict[str, int] = {}
    for key, (low, high, default) in _GOVERNANCE_BOUNDS.items():
        value = declared.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not (low <= value <= high):
            value = default
        settings[key] = int(value)
    return settings


class GovernanceDocGraphNode(GraphNode):
    """GraphNode subclass assigned to the `main` slot of AIModelGovernanceAuditTrailAgent.

    Wraps DomainWorkflowGraph (inner BaseGraph).
    Called by the AgentBaseGraph backbone after pre_process and before post_process.

    Contracts:
      get_subgraph()  — instantiate and return DomainWorkflowGraph
      extract_input() — pull validated_input from outer state, and stash the
                        validated runtime settings for the subgraph to seed
      merge_output()  — map sub_result fields into the outer state delta (changed keys only)
      error_strategy  — "propagate": re-raise inner errors as SubgraphError (fail-fast)
    """

    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    def get_subgraph(self) -> Any:
        """Instantiate and return the inner domain workflow graph.

        DomainWorkflowGraph is imported lazily (inside the method) to avoid
        circular-import risk at module load time. It takes no constructor
        arguments: its runtime settings arrive through the context bridge, which
        is the only ordering the framework actually supports (see
        src/graph/context_bridge.py — get_subgraph() runs BEFORE extract_input()).
        """
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph()

    def extract_input(self, state: AgentState) -> str:
        """Return the string input passed into inner_graph.invoke().

        Also stashes the validated runtime settings the outer graph seeded into
        state, so the subgraph can seed them into its own initial state. This runs
        after get_subgraph() and before subgraph.invoke(), which is the only point
        where both the settings and the subgraph exist.

        PreProcessNode validates and normalises the raw user_input and writes the
        result to validated_input. Prefer that; fall back to user_input if
        validated_input is absent (e.g. when a node is exercised directly).
        """
        stash_governance_settings(state.get("governance_settings"))
        return str(state.get("validated_input") or state.get("user_input") or "")

    def merge_output(self, state: AgentState, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        """Map the inner graph sub_result back into the outer state delta.

        sub_result is the dict returned by DomainWorkflowGraph.get_output().
        Returns ONLY changed keys — never the full state.

        Key coupling (designed together with DomainWorkflowGraph.get_output()):
          Inner get_output() emits  → "governance_document", "governance_sections",
                                       "compliance_flags", "human_review_required",
                                       "audit_entry", "redacted_fields", "status"
          This merge_output() reads → sub_result.get(...) for each of these keys.

        Note that `result` is deliberately NOT carried across: the inner graph's
        `result` is the PRE-gate document. The only writer of the outer `result`
        is PostProcessNode, after the output gate has run.

        PostProcessNode (outer post_process) reads governance_document +
        human_review_required from state to apply the output gate and set
        formatted_output.
        """
        return {
            "governance_document": sub_result.get("governance_document"),
            "governance_sections": sub_result.get("governance_sections"),
            "compliance_flags": sub_result.get("compliance_flags"),
            "human_review_required": sub_result.get("human_review_required", False),
            "audit_entry": sub_result.get("audit_entry"),
            "redacted_fields": sub_result.get("redacted_fields"),
            "status": sub_result.get("status"),
        }


class AIModelGovernanceAuditTrailAgent(AgentBaseGraph):
    """Outer graph for CMN-C1-068 (industry-agnostic document generation).

    Inherits AgentBaseGraph directly. Domain logic is fully encapsulated in
    GovernanceDocGraphNode (main slot), which delegates to DomainWorkflowGraph
    (inner BaseGraph).

    Backbone (fixed):
        START → initialize → pre_process → main → post_process → finalize → END

    Overrides:
      - register_nodes():      fills the three domain slots
      - _extra_initial_state(): seeds the validated runtime settings into state
      - get_output():          surfaces the gated domain result, withholds it otherwise

    add_edges() is NOT overridden — backbone wiring belongs to the framework.

    Class name MUST match the config/agent.yaml `class:` field exactly.
    """

    @property
    def name(self) -> str:
        """Agent identifier registered with the platform registry."""
        return "AIModelGovernanceAuditTrailAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        """Fill all 5 backbone slots.

        super().register_nodes() MUST be called first — it injects the framework's
        default InitializeNode (sets schema_version, session_id, trust_level) and
        FinalizeNode (builds response_metadata, total_time_ms).
        """
        super().register_nodes()  # fills: initialize, finalize

        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = GovernanceDocGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the validated runtime settings into initial state.

        This is what makes a declared configuration value reach the nodes that
        consume it. ``Graph(config=...)`` lands on ``self.config``; nodes are
        called as ``execute(state)`` and never receive a config argument, so the
        settings have to travel as state. They are validated once here rather than
        re-read per node, so every node in one invocation sees the same values.
        """
        return {"governance_settings": to_json(declared_governance(self.config))}

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Surface the gated governance result, and withhold it on any other outcome.

        ``AgentBaseGraph.get_output()`` returns the minimal ``{output, status,
        trace_id, correlation_id, node_history}`` envelope, which drops the
        structured domain result that GovernanceDocGraphNode.merge_output() wrote
        into outer state. This override adds those fields back on the success path.

        Two containment properties are enforced here, both on the NON-success path:

          * ``result`` is never surfaced. It is the un-gated channel name; the only
            caller-facing value on a refusal is ``formatted_output``, which the
            output gate itself writes as a truthy withholding notice.
          * ``output`` is re-resolved as ``formatted_output or None``. The framework
            base resolves it as ``formatted_output or result``, and a falsy
            ``formatted_output`` re-opens that fallback — so an error path that left
            an un-gated ``result`` in state would ship it inside the ERROR envelope.
            Nothing writes the outer ``result`` except PostProcessNode today, so the
            base fallback is not reachable in this template as it stands; it is one
            state write away from being reachable, and that is the reason the
            re-resolution is here rather than left to convention.

        The structured fields (governance_sections / compliance_flags /
        human_review_required / audit_entry / redacted_fields) are surfaced ONLY
        when the gate passed.
        """
        output: Dict[str, Any] = dict(
            super().get_output(state)  # {output, status, trace_id, correlation_id, node_history}
        )
        succeeded = state.get("status") == AgentStatus.SUCCESS.value

        formatted = state.get("formatted_output")
        output["formatted_output"] = formatted

        if not succeeded:
            output["output"] = formatted or None
            output["result"] = None
            output["governance_document"] = None
            output["governance_sections"] = None
            output["compliance_flags"] = None
            output["human_review_required"] = None
            output["audit_entry"] = None
            output["redacted_fields"] = None
            return output

        gated_document = formatted or state.get("result")
        output["result"] = state.get("result")
        output["governance_document"] = gated_document
        output["governance_sections"] = state.get("governance_sections")
        output["compliance_flags"] = state.get("compliance_flags")
        output["human_review_required"] = state.get("human_review_required")
        output["audit_entry"] = state.get("audit_entry")
        output["redacted_fields"] = state.get("redacted_fields")
        return output

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.


# Alias for backward compat (server.py imports Graph).
# Class name AIModelGovernanceAuditTrailAgent matches the config/agent.yaml class: field.
Graph = AIModelGovernanceAuditTrailAgent
