# Template Design Specification — CMN-C1-068 AI Model Governance Documentation & Audit Trail Generation Agent

## Position in AgentCore Architecture

- **Agent Class**: AIModelGovernanceAuditTrailAgent
- **L1 Base**: AgentBaseGraph
- **Category**: Cat 1 (industry-agnostic generic capability — governance documentation & audit-trail generation)
- **Pattern**: DocGenerationAgent, built on the two-layer nested structural pattern (outer backbone + inner domain workflow). The graph *structure* is orthogonal to the Cat classification — Cat 1 business classification, nested composition for single-responsibility node design.
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible); ADR-005 JSON-serialised strings for all dict/list fields
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution; nested via `GraphNode`)

## Domain Context

Cross-industry AI governance documentation generator. AI governance officers input model metadata + a risk classification; the agent produces governance documentation (AI Model Card, risk assessment, audit-trail record) that supports a compliance assessment under the EU AI Act, ISO/IEC 42001, the FSA AI Model Risk Management Framework, the METI Digital Governance Code, and the Cabinet Office AI Guidelines for Business Operators.

**Industry-agnostic (Cat 1)**: no domain-specific KB is assumed. The governance frameworks are cross-industry standards; industry specifics (if any) belong to Cat 2 derivatives, not to this template.

## Architecture Overview

### Backbone (outer AgentBaseGraph — fixed 5-node pipeline)

```
START → initialize → pre_process → main(GraphNode) → post_process → finalize → END
                                         ↓ (retry, max 3)
                                       pre_process
```

### Inner Domain Workflow (DomainWorkflowGraph — linear 5-node pipeline)

```
START → input_validate → parse_model_metadata → generate_governance_sections
          → compliance_check → output_format → END
```

### Node Configuration

| Node | Class | File | Trust | Responsibility | Input Keys | Output Keys |
|------|-------|------|-------|---------------|------------|-------------|
| initialize | InitializeNode | framework | — | session init | — | session_id, schema_version |
| pre_process | PreProcessNode | src/nodes/pre_process_node.py | VERIFIED_EXTERNAL | S-1 trust; size bound; strict JSON parse (NaN/Infinity refused); required fields; control-token screen; credential screen (framework detector); redacted-identity refuse; EU Art.5 prohibited-practice refuse | user_input, governance_settings | validated_input, enriched_context |
| main | GovernanceDocGraphNode | src/graph/graph.py | — | delegates to DomainWorkflowGraph; stashes the validated runtime settings for the subgraph | validated_input, governance_settings | governance_document, governance_sections, compliance_flags, human_review_required, audit_entry, redacted_fields |
| post_process | PostProcessNode | src/nodes/post_process_node.py | ANONYMOUS | S-3 output gate (framework credential detector + certification language); on violation clears every output-bearing field explicitly | governance_document | formatted_output, result, plus the cleared set on a violation |
| finalize | FinalizeNode | framework | — | response metadata | — | response_metadata, total_time_ms |
| input_validate (inner) | InputValidateNode | src/nodes/input_validate_node.py | ANONYMOUS | domain field validation; risk-tier & framework normalisation; **the single place caller text is bounded** (structure neutralised, lengths capped, identifiers made inert, non-finite metrics refused) | validated_input, governance_settings | model_metadata, redacted_fields |
| parse_model_metadata (inner) | ParseModelMetadataNode | src/nodes/parse_model_metadata_node.py | ANONYMOUS | ScopeClassify (applicable frameworks) + RiskClassify (S-5 human-review flag) | model_metadata | model_metadata (enriched) |
| generate_governance_sections (inner) | GenerateGovernanceSectionsNode | src/nodes/generate_governance_sections_node.py | ANONYMOUS | generate 7 model-card sections — deterministic synthesis, advisory framing, no model call | model_metadata | governance_sections |
| compliance_check (inner) | ComplianceCheckNode | src/nodes/compliance_check_node.py | ANONYMOUS | map to framework requirements + citations; S-5 human-review recommendation | model_metadata | compliance_flags, human_review_required |
| output_format (inner) | OutputFormatNode | src/nodes/output_format_node.py | ANONYMOUS | assemble final document + append-only audit-trail entry (S-4), including the redaction-provenance line | governance_sections, compliance_flags, redacted_fields | governance_document, audit_entry |

### Data Flow

```
user_input (JSON model-governance payload)
    │
    ▼ PreProcessNode (VERIFIED_EXTERNAL, S-1: required fields + Art.5 refuse)
validated_input (normalised JSON string)
enriched_context (JSON string — ADR-005)
    │
    ▼ GovernanceDocGraphNode → DomainWorkflowGraph
    │   InputValidateNode             → model_metadata (JSON string — ADR-005)
    │   ParseModelMetadataNode        → model_metadata (enriched, ADR-005)
    │   GenerateGovernanceSectionsNode → governance_sections (JSON string — ADR-005)
    │   ComplianceCheckNode            → compliance_flags (JSON string), human_review_required (bool)
    │   OutputFormatNode               → governance_document (str), audit_entry (JSON)
    ▼ merge_output
governance_document, governance_sections, compliance_flags, human_review_required,
audit_entry, redacted_fields → outer state   (`result` is deliberately NOT carried:
the inner `result` would be the pre-gate document, and only PostProcessNode may write
the outer one)
    │
    ▼ PostProcessNode (ANONYMOUS, S-3: credential + certification-language gate)
formatted_output (S-3-gated governance_document), result
```

### State Definition

| Field | Type | Purpose | Producer |
|-------|------|---------|----------|
| validated_input | NotRequired[Optional[str]] | Normalised governance JSON string | PreProcessNode |
| enriched_context | NotRequired[Optional[str]] | JSON: {source, channel, model_name} | PreProcessNode |
| model_metadata | NotRequired[Optional[str]] | JSON: parsed+enriched governance payload | InputValidateNode / ParseModelMetadataNode |
| governance_sections | NotRequired[Optional[str]] | JSON: {section_name: text, ...} × 7 sections | GenerateGovernanceSectionsNode |
| compliance_flags | NotRequired[Optional[str]] | JSON: framework mapping + citations | ComplianceCheckNode |
| human_review_required | NotRequired[Optional[bool]] | S-5: True when risk tier high/unacceptable | ComplianceCheckNode |
| audit_entry | NotRequired[Optional[str]] | JSON: append-only audit-trail record (S-4) | OutputFormatNode |
| governance_document | NotRequired[Optional[str]] | Final formatted governance document text | OutputFormatNode |
| governance_settings | NotRequired[Optional[str]] | JSON: validated runtime settings, seeded from the graph config | outer graph `_extra_initial_state()` |
| redacted_fields | NotRequired[Optional[str]] | JSON list of declared field names the platform input filter had already redacted | InputValidateNode |
| result | NotRequired[Optional[str]] | The caller-facing document, written ONLY by the output gate and only on the gated success path | PostProcessNode |

**ADR-005 constraint**: all dict/list-valued fields use JSON-serialised `Optional[str]`. `to_json()` / `from_json()` helpers are defined in `src/schemas/state.py` and used at every producer/consumer boundary — one contract end-to-end.

**Prohibited**: re-declaring `formatted_output` (inherited from AgentState), credentials in State, Pydantic models.

### Input Payload Schema (user_input JSON)

```json
{
  "model_name": "CreditRiskScorer",
  "model_version": "3.1.0",
  "model_type": "gradient_boosting_classifier",
  "intended_use": "Assist loan officers in assessing credit default risk (decision support)",
  "deployment_scope": "internal_decision_support",
  "risk_tier": "high",
  "target_frameworks": ["eu_ai_act", "iso_42001"],
  "training_data_summary": "Anonymised historical loan performance data, 2018-2025",
  "performance_metrics": {"auc": 0.87, "precision": 0.81},
  "limitations": "Not validated for the SME lending segment"
}
```

### Output Document Sections (model-card format)

1. **Model Overview** — name, version, type, deployment scope
2. **Intended Use and Scope** — authorised purpose and boundary
3. **Training Data Summary** — sources, coverage, representativeness gaps
4. **Performance Characteristics** — reported metrics + measurement context
5. **Limitations and Risks** — known failure modes / out-of-scope conditions
6. **Risk Classification** — assessed risk tier + mapped framework requirements
7. **Human Oversight Measures** — monitoring, accountable owner, escalation, rollback

Plus a **Regulatory Mapping Note** trailer with applicable frameworks + citations, and an append-only **audit-trail entry** (timestamp, model version, generator id, document SHA-256).

## Security Configuration

| Layer | Gate | Implementation |
|-------|------|---------------|
| S-1 | Trust enforcement + prohibited-practice refuse | PreProcessNode `required_trust_level = VERIFIED_EXTERNAL`; EU AI Act Art.5 prohibited-practice hard refuse |
| S-2 | Input validation | PreProcessNode (size bound, strict JSON parse, required fields, control-token screen over keys and values, credential screen delegating to the framework detector, redacted-identity refusal) + InputValidateNode (domain rules, normalisation, caller-text bounding, finite-number enforcement) |
| S-3 | Output gate | PostProcessNode `_security_gate_output()` — module-level function. The credential half **delegates to the framework's own `detect_credentials`** so the gate's block set is identical to the one the framework enforces on the way out; the certification half is domain policy. On a violation the node returns ERROR, a truthy withholding notice, and an explicitly cleared value for every output-bearing field |
| S-4 | Audit logging & trail | `emit_trace_event()` in every node's `execute()`; append-only audit-trail entry written by OutputFormatNode |
| S-5 | Human-review recommendation | ComplianceCheckNode sets `human_review_required` for high/unacceptable risk tiers |

**Manifest / runtime split.** `config/agent.yaml` is the FLAT registration manifest and
carries identity only — there is no `agent:` block and no `config:` block in it. Every
runtime parameter lives in `config/config.yaml`, which the platform registry loads and
passes back as `Graph(config=...)`:

```yaml
max_retry: 3
timeout_s: 30
governance:
  max_input_bytes: 32768
  max_field_chars: 2000
  max_performance_metrics: 25
  max_target_frameworks: 10
security:
  s3_gate_enabled: true   # declared invariant, NOT a runtime switch
```

`security.s3_gate_enabled` is an assertion, not a toggle: the output gate runs
unconditionally and reads nothing from this file. A security control that configuration
can turn off is not a control. `tests/unit/test_runtime_config.py` asserts both halves —
that the declaration stays true, and that no module in `src/` reads the flag.

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (session_id, caller trust level)
- [x] `framework.security.credential_detector.detect_credentials` — the output gate's
      credential half, and `detect_credentials_in_value` for the input screen
- [x] S-3: module-level `_security_gate_output()` in `post_process_node.py` — credential/secret + certification-language scan on output string
- [x] S-4: `emit_trace_event()` — at least one domain-specific event per node `execute()`
- [x] `to_json()` / `from_json()` helpers in `src/schemas/state.py` — ADR-005 serialisation contract

### Composition Pattern

- **Pattern**: two-layer nested — GraphNode wrapping inner BaseGraph
- **Outer graph**: `AIModelGovernanceAuditTrailAgent(AgentBaseGraph)` — fixed 5-node backbone
- **Inner graph**: `DomainWorkflowGraph(BaseGraph)` — 5-node linear domain pipeline
- **Error propagation**: propagate (SubgraphError on inner failure; outer backbone retries pre_process)

## Runtime configuration reaches nodes as SEEDED STATE

`BaseNode.__call__` invokes `execute(state)` with one argument, so a node signature of
`execute(self, state, config=None)` can never receive a value — the parameter is decorative
and no node here declares one (`tests/unit/test_trust_and_contract.py` asserts that for every
node). `GraphNode.execute()` also calls `get_subgraph()` BEFORE `extract_input(state)`, so the
subgraph constructor cannot read outer state either, and a subgraph's `invoke()` builds its own
initial state from scratch.

The one ordering the framework does support is the one used here:

1. `AIModelGovernanceAuditTrailAgent._extra_initial_state()` validates the `governance:` block
   of `Graph(config=...)` once — each key bounds-checked and defaulted individually, booleans
   rejected explicitly — and seeds the result into outer state as `governance_settings`.
2. `GovernanceDocGraphNode.extract_input(state)` runs after the subgraph is constructed and
   before `subgraph.invoke(...)`, and stashes those settings in a `ContextVar`
   (`src/graph/context_bridge.py`).
3. `DomainWorkflowGraph._extra_initial_state()` — called inside that `invoke` — reads them back
   and seeds them into inner state, where the nodes consume them.

`src/api/server.py` constructs the graph as `Graph(config=runtime_config())`, mirroring what the
platform registry does, so a declared value behaves identically on both paths. Constructing with
no config would leave every declared parameter — the framework's own `max_retry` included —
silently on its default.

`tests/integration/test_invoke_contract.py::TestRuntimeConfigIsLoadBearing` drives a declared
value end to end and asserts the rendered document changes. Breaking the bridge fails those
tests while the node-level suite stays green, which is the point of testing it there.

## Output-invariant scope

This template renders **no monetary aggregates** — there is no currency symbol, no ISO currency
code and no thousands-grouped format anywhere in `src/`. The rounding/precision grid that
financial templates enforce is therefore not applicable here, and no such gate is implemented.

The invariant that IS enforced at the output boundary is structural, and it is enforced because
the document's meaning is carried by line structure:

- **No caller string can start a line.** `neutralise_text()` (`src/schemas/guards.py`) flattens
  every whitespace character to a single space in one pass and collapses runs of three or more
  rule characters (`= - _ * #`), so a declared limitation cannot open a forged section, a forged
  rule, or a forged label line such as `Human Governance Review:  not required at this tier`.
  It is deliberately one pass: an earlier version had a line-break substitution AND a separate
  control-character filter, each of which removed newlines on its own, so deleting either left
  every structural test passing and neither guard provable.
- **Values rendered as labels are inert.** Framework ids are locked to `[a-z0-9_]{1,32}`, metric
  names to `[A-Za-z0-9_.-]{1,40}`.
- **Every rendered number is finite.** `NaN` / `Infinity` JSON literals are refused at parse
  (Python's `json` accepts them by default), and a metric whose value is not a finite number —
  including the string `"1e999"`, which `float()` turns into infinity — fails the request.
- **Everything is bounded.** Payload size, per-field length, metric count and framework count
  all come from `config/config.yaml`.

## Retained scaffold stub

`src/services/service.py` (`Service.fetch()`) is retained but never called on the runtime path:
this template's domain logic lives entirely in the inner `DomainWorkflowGraph` nodes, so no
service-layer call exists. The module is kept because the CI scaffold-integrity job requires a
real `src/services/` module; `fetch()` raises `NotImplementedError` to fail loudly if a future
change wires it in without an implementation.

The deprecated `MainNode` compatibility stub that previously sat in `src/nodes/main_node.py` has
been **removed**. No graph referenced it, its own docstring said not to use it, and its tests
were the only thing importing it. The assertions worth keeping — the trust-gate negative control
and the `execute(self, state)` contract — now run against the nodes that are actually on the
runtime path, and the contract check runs against every node rather than one
(`tests/unit/test_trust_and_contract.py`).

## Import Isolation Confirmation
- [x] Template does not import the Level-0 platform SDK
- [x] Import targets: `framework/` and `shared/` only (no Level-0 SDK)

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | AgentBaseGraph | Fixed sequential documentation pipeline; no LLM reasoning loop required |
| Composition pattern | Cat 1 flat (single main) | Nested GraphNode (inner BaseGraph) | Nested GraphNode | 5 sequential domain steps; single-responsibility node design (DocGeneration pattern) |
| Cat classification | Cat 1 (industry-agnostic) | Cat 2 (domain-specific) | Cat 1 | Governance frameworks are cross-industry; no domain KB assumed |
| Certification language | Allow | Advisory framing only (S-3 gate) | Advisory framing only | Governance docs must SUPPORT, never CERTIFY, compliance (architect S-3 rule) |
| State dict fields | bare dict | JSON-serialised str | JSON-serialised str | ADR-005: msgpack serialisation safety |
| Section generation | Model-backed | Deterministic synthesis | Deterministic synthesis | Declared as `generation_mode: "deterministic"` in the manifest; no model is called and none is faked. Swapping in a model-backed generator means changing that declaration too |
| Runtime config delivery | Node constructor args | Seeded state via the context bridge | Seeded state | Nodes take no constructor arguments and `execute(state)` receives no config; seeded state is the only path that reaches them |
| Caller text in the document | Escape on render | Neutralise once at validation | Neutralise once at validation | Every downstream node renders from `model_metadata`; a second pass at the render sites would make the first unfalsifiable |
| Audit trail | Inline in generate | Separate append-only entry in output_format | Separate append-only entry | Compliance artifact — idempotent (content SHA-256), append-only (S-4) |
