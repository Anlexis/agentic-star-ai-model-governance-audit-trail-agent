"""AgentCore Platform v1.0"""

# CMN-C1-068 — DomainWorkflowGraph (inner BaseGraph)
#
# This is the INNER graph for the two-layer nested architecture.
# It encapsulates the AI-model-governance document generation pipeline:
#
#   START
#     → input_validate               (InputValidateNode)
#     → parse_model_metadata         (ParseModelMetadataNode)
#     → generate_governance_sections (GenerateGovernanceSectionsNode)
#     → compliance_check             (ComplianceCheckNode)
#     → output_format                (OutputFormatNode)
#     → END
#
# Called by GovernanceDocGraphNode.get_subgraph() (graph.py).
# get_output() shapes the sub_result dict consumed by merge_output() there.
#
# Rules enforced:
#   ✅ Inherits BaseGraph (fully custom topology — no forced backbone)
#   ✅ Implements all 7 BaseGraph ABC methods
#   ✅ register_nodes() does NOT call super() (abstract in BaseGraph)
#   ✅ Does NOT register initialize / finalize (outer backbone concerns)
#   ✅ All inner nodes declare required_trust_level = TrustLevel.ANONYMOUS
#   ✅ get_output() designed together with GovernanceDocGraphNode.merge_output()
#   ✅ All inner node ctors are empty-parens (no constructor args — CoE no-arg rule)
#   ❌ No Level-0 platform SDK imports
#   ❌ Not placed under src/subagents/

from typing import Any, Dict

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.graph.context_bridge import take_governance_settings
from src.nodes.compliance_check_node import ComplianceCheckNode
from src.nodes.generate_governance_sections_node import GenerateGovernanceSectionsNode
from src.nodes.input_validate_node import InputValidateNode
from src.nodes.output_format_node import OutputFormatNode
from src.nodes.parse_model_metadata_node import ParseModelMetadataNode
from src.schemas.state import State


class DomainWorkflowGraph(BaseGraph):
    """Inner domain workflow graph for CMN-C1-068.

    Inherits BaseGraph directly for a fully custom node topology.
    Called by GovernanceDocGraphNode.get_subgraph() in graph.py.

    Pipeline (linear):
        START
          → input_validate               (InputValidateNode)
          → parse_model_metadata         (ParseModelMetadataNode)
          → generate_governance_sections (GenerateGovernanceSectionsNode)
          → compliance_check             (ComplianceCheckNode)
          → output_format                (OutputFormatNode)
          → END

    All nodes are FunctionNode subclasses with ANONYMOUS trust_level.
    initialize / finalize are outer backbone concerns — not registered here.
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "cmn_c1_068_ai_governance_doc_workflow"

    @property
    def state_schema(self) -> type:
        return State

    # ── Config validation ─────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """No constructor configuration: settings arrive as seeded state.

        The inner graph takes no constructor arguments. Its runtime settings are
        validated once by the outer graph and reach the nodes through
        _extra_initial_state() below — see src/graph/context_bridge.py for why that
        is the only ordering the framework supports.
        """
        pass

    # ── Runtime settings ──────────────────────────────────────────────────────

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the outer graph's validated runtime settings into inner state.

        Called by BaseGraph.invoke() after GovernanceDocGraphNode.extract_input()
        has stashed them. Returns an empty mapping when this graph is invoked
        directly (no outer graph in the call stack); each node then falls back to
        its own module defaults rather than to a half-populated mapping.
        """
        settings = take_governance_settings()
        return {"governance_settings": settings} if settings else {}

    # ── Node registration ─────────────────────────────────────────────────────

    def register_nodes(self) -> None:
        """Register all 5 domain nodes.

        No super() call — BaseGraph.register_nodes() is abstract.
        Do NOT register initialize or finalize; those are outer backbone
        concerns handled by AgentBaseGraph in graph.py.
        Every key registered here is referenced in add_edges().
        All nodes are instantiated with empty-parens (no ctor args) —
        CoE no-arg ctor rule: SDK v1 FunctionNode subclasses take no arguments.
        """
        self._nodes["input_validate"] = InputValidateNode()
        self._nodes["parse_model_metadata"] = ParseModelMetadataNode()
        self._nodes["generate_governance_sections"] = GenerateGovernanceSectionsNode()
        self._nodes["compliance_check"] = ComplianceCheckNode()
        self._nodes["output_format"] = OutputFormatNode()

    # ── Edge wiring ───────────────────────────────────────────────────────────

    def add_edges(self) -> None:
        """Wire the linear governance-document generation topology.

        Linear flow:
            input_validate → parse_model_metadata → generate_governance_sections
            → compliance_check → output_format → END.

        No conditional branching — all paths through the document pipeline are
        linear in v1.  route() satisfies the ABC but is not used at runtime.
        """
        self._sg.add_edge(START, "input_validate")
        self._sg.add_edge("input_validate", "parse_model_metadata")
        self._sg.add_edge("parse_model_metadata", "generate_governance_sections")
        self._sg.add_edge("generate_governance_sections", "compliance_check")
        self._sg.add_edge("compliance_check", "output_format")
        self._sg.add_edge("output_format", END)

    # ── Routing ───────────────────────────────────────────────────────────────

    def route(self, state: AgentState) -> str:
        """Conditional routing — required by BaseGraph ABC.

        Linear topology; add_conditional_edges() is not used, so this method
        is never called at runtime.  Returns END on error so an unexpected
        invocation does not re-enter a processing node.
        """
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        return "output_format"

    # ── Output shape ──────────────────────────────────────────────────────────

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Shape the output dict returned to the outer graph as sub_result.

        This dict is received by GovernanceDocGraphNode.merge_output()
        in graph.py as the `sub_result` argument.  Both methods are designed
        together to guarantee field-name consistency:

            Inner get_output() emits:   "governance_document", "governance_sections",
                                        "compliance_flags", "human_review_required",
                                        "audit_entry", "redacted_fields", "status"
            Outer merge_output() reads: sub_result.get(...) for each key above.

        No ``or`` fallback is used anywhere in this mapping. A template subgraph
        that resolves one key as ``a or b`` recreates the framework's own
        ``formatted_output or result`` hazard one level down: when the gated value
        is falsy the alternative re-opens, and the caller receives the pre-gate
        assembly. Every key here reads exactly one state field.
        """
        return {
            "governance_document": state.get("governance_document"),
            "governance_sections": state.get("governance_sections"),
            "compliance_flags": state.get("compliance_flags"),
            "human_review_required": state.get("human_review_required", False),
            "audit_entry": state.get("audit_entry"),
            "redacted_fields": state.get("redacted_fields"),
            "status": state.get("status"),
            "node_history": state.get("node_history", []),
            "correlation_id": state.get("correlation_id"),
        }
