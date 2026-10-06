# Test Specification — CMN-C1-068 AI Model Governance Documentation & Audit Trail Generator

## 1. Test Strategy

- **Agent:** CMN-C1-068 — AI Model Governance Documentation & Audit Trail
  Generator (Cat 1, industry-agnostic, DocGeneration pattern). Built on the
  two-layer nested graph: outer `AgentBaseGraph` backbone + inner
  `DomainWorkflowGraph` (`BaseGraph`). The business classification is **Cat 1**;
  the nested graph structure is orthogonal to the Cat classification.
- **Coverage target:** ≥ 90% of `src/nodes/` + `src/graph/` branches.
- **Test types:** Unit (per node + graph wiring) · Proof-of-Boundary (framework
  security/serialization contracts + domain boundaries) · Backbone invoke (full
  `Graph().invoke()`).
- **Framework provisioning:** `framework` (agenticstar-agentcore) is installed from
  the package registry by CI. Tests import the REAL modules; there are no stub nodes
  and nothing in the suite substitutes a graph, a node or the framework.
- **S-4 audit:** `emit_trace_event` is patched at the node module level in unit
  tests to avoid audit-backend calls, never via a `sys.modules` stub (which would
  break the real `shared` package the framework loads at import time).

### Test file map

| File | Scope |
|------|-------|
| `tests/unit/test_nodes.py` | All 5 inner domain nodes + PreProcess/PostProcess gates + outer & inner graph wiring |
| `tests/unit/test_guards.py` | `src/schemas/guards.py` — structural neutralisation, inert identifiers, finite numbers, the control-token screen |
| `tests/unit/test_runtime_config.py` | The shipped `config/config.yaml`, the bounds validator, and the output gate's cleared-set inventory |
| `tests/unit/test_trust_and_contract.py` | Trust-gate positive/negative controls + the `execute(self, state)` contract for every node |
| `tests/unit/test_graph_get_output.py` | Outer `get_output()` surfaces the gated domain result on success |
| `tests/unit/test_framework_compliance_tc06_tc07.py` | Framework compliance TC-06 / TC-07 |
| `tests/integration/test_invoke_contract.py` | End-to-end through the real ASGI `/invoke` — clean path, runtime config, structural forgery, input screens, non-finite numbers, structural caps, platform redaction, error-envelope containment |
| `tests/proof_of_boundary/test_pb_invoke_order.py` | PB-6 per-node + backbone invoke order (VERIFIED_EXTERNAL) + S-1 node-level gate + payload alignment + PB-PROHIBITED / PB-CERT / PB-AUDITAPPEND |
| `tests/proof_of_boundary/test_import_isolation.py` | PB-4 Level-0 import isolation (AST scan) |
| `tests/proof_of_boundary/test_state_safety.py` | PB-2/PB-5 State msgpack/credential safety (AST scan) |
| `tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` | PB-7 HITL interrupt-propagation (skip stub — no cross-boundary HITL) |

### Canonical valid payload (PB-6 `_VALID_PAYLOAD`)

The high-risk AI-model-governance request used by the backbone invoke test and by
`deploy/invoke_payload.json` (the two MUST stay identical — asserted by
`test_invoke_payload_matches_pb6`):

```json
{
  "model_name": "AcmeCreditRiskScorer",
  "model_version": "2.1.0",
  "model_type": "gradient-boosted decision tree",
  "intended_use": "Assist loan officers in assessing consumer credit-risk applications as decision support; a human makes the final lending decision.",
  "deployment_scope": "internal decision support for retail lending",
  "risk_tier": "high",
  "target_frameworks": ["eu_ai_act", "iso_42001"],
  "training_data_summary": "Anonymised historical retail-loan performance records, 2018-2025.",
  "performance_metrics": {"auc": 0.87, "ks": 0.42},
  "limitations": "Not validated for SME or commercial lending; monitor for demographic drift."
}
```

Risk tiering: `risk_tier = high` ⇒ `requires_human_review = True` (S-5); ISO/IEC
42001 is unioned in as the cross-industry baseline framework.

## 2. Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Where |
|-------|------|----------------|-------|
| TC-01 | State contract: flat `TypedDict`, domain fields `NotRequired`, ADR-005 JSON-string storage, no Pydantic/dataclass | AST scan: 0 violations | `test_state_safety.py` |
| TC-02 | Invalid/empty/non-JSON/missing-field input rejected at PreProcessNode | `status=error`, `error_log` populated | `TestPreProcessNode` |
| TC-03 | No JWT/credential in State | CI `gate-credential-scan`: 0 violations | CI + `test_state_safety.py` |
| TC-04 | `execute(self, state)` contract — no `_invoke_impl`, no decorative `config` parameter | Signature `(self, state)` for EVERY node | `test_trust_and_contract.py::TestNodeImplementationContract` |
| TC-05 | S-4: `emit_trace_event()` called inside each node `execute()` | ≥1 domain event per node (positional form) | verified by CoE preflight S-4/#3 |
| TC-08 | S-1: `required_trust_level` enforced in `__call__` before `execute()` | ANONYMOUS caller → refused; VERIFIED_EXTERNAL → admitted | `test_trust_and_contract.py::TestS1TrustGate` |
| TC-08a | Outer `PreProcessNode` = VERIFIED_EXTERNAL; inner nodes + post_process = ANONYMOUS | trust levels asserted per node | `test_trust_level_*` |
| TC-11 | S-3 output gate on post_process | credential OR certification pattern → withheld + `status=error`, truthy notice, every output-bearing field present-and-cleared; clean → pass | `TestPostProcessNode` |
| TC-12 | Detector parity: the gate's credential half equals the framework's block set | `AKIA…`, `sk_live_…`, a connection string and a two-segment JWT are all blocked | `test_output_gate_matches_the_framework_block_set` |
| TC-13 | Declared runtime configuration reaches the nodes that consume it | a declared cap changes the rendered document end to end; out-of-range and boolean values fall back to the default | `TestRuntimeConfigIsLoadBearing` |
| TC-14 | Caller text cannot manufacture document structure | a newline-laden field produces no extra section, rule or verdict line | `TestStructuralForgery` |
| TC-15 | Chat-template control markers refused as a class | `<<SYS>>`, `<\|im_start\|>`, `[INST]`, `<system>` — in values AND in keys | `TestInputScreens` |
| TC-16 | Non-finite numbers fail closed | `NaN` / `Infinity` literals and `"1e999"` refused; finite numeric strings accepted | `TestNonFiniteNumbers` |
| TC-17 | Structural caps | 400 metrics → 25 rendered; a 20k-character field cannot dominate the document; 200 frameworks capped | `TestStructuralCaps` |
| TC-18 | Platform redaction is declared, never silent | a redacted identity is refused; redacted prose is named in the document and the audit entry | `TestPlatformRedaction` |
| TC-19 | Error-envelope containment | a refusal returns no released text, no domain field, no traceback and no source path | `TestErrorEnvelopeContainment` |

## 3. Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Where |
|-------|----------|------|----------------|-------|
| PB-2 | State serialization | AST scan of `src/schemas/state.py` | primitives only; no Pydantic/dataclass | `test_state_safety.py` |
| PB-4 | Import isolation | AST scan of `src/` | 0 Level-0 (`agenticstar` / platform) imports | `test_import_isolation.py` |
| PB-5 | Checkpoint safety | no credential-named fields / prohibited types in State | inspection pass | `test_state_safety.py` |
| PB-6 | Invoke execution order (per node) | `__call__`: S-1 gate → S-4 node_start → S-2 input gate → `execute()` → S-3 output gate → S-4 node_complete | order verified for every `src/nodes/` class | `TestInvokeOrder` |
| PB-6b | Backbone invoke order | full `Graph().invoke(_VALID_PAYLOAD, ctx=VERIFIED_EXTERNAL)` | `status=success`; node_history = `[Initialize, PreProcess, GovernanceDocGraphNode, PostProcess, Finalize]`; `output` carries the assembled document | `TestBackboneInvokeOrder` |
| PB-6c | Real external caller | `InvocationContext(caller_trust_level=VERIFIED_EXTERNAL)` — **never** `for_internal()` | inner ANONYMOUS nodes accept the passthrough trust; SUCCESS end-to-end | `TestBackboneInvokeOrder` |
| PB-6d | Payload alignment | `deploy/invoke_payload.json["input"] == _VALID_PAYLOAD` | Stage-5 deploy-stg invoke exercises the PB-6 payload | `test_invoke_payload_matches_pb6` |
| PB-PROHIBITED | S-1 prohibited-practice refusal | EU AI Act Art. 5 intended-use (real-time biometric surveillance / social scoring) | `status=error`; no `validated_input`; lawful use admitted | `TestPBProhibitedPractice` |
| PB-CERT | S-3 certification-language gate | output asserts "is compliant" / "meets all requirements" | withheld + `status=error`; advisory framing passes | `TestPBCertificationGate` |
| PB-AUDITAPPEND | S-4 append-only audit trail | audit entry `document_sha256` == SHA-256 of the document | content-addressed, deterministic for identical content, changes when the document changes | `TestPBAuditAppendOnly` |
| PB-7 | HITL interrupt propagation | skip stub — `propagate_hitl=False`, no cross-boundary interrupt() checkpoint | skipped with reason (real assertion when HITL wired) | `test_pb7_hitl_interrupt_propagation.py` |

## 4. Business Logic Tests

| BL-ID | Test | Input | Expected Result | Where |
|-------|------|-------|----------------|-------|
| BL-01 | Happy-path model-card generation | `_VALID_PAYLOAD` | 7-section governance document + regulatory-mapping note; `AI MODEL GOVERNANCE DOCUMENT` + model_name present | `test_backbone_invoke_succeeds_and_returns_output`, `TestInnerDomainGraph` |
| BL-02 | Risk-tier normalisation | `risk_tier="critical"` | canonicalised to `high` | `test_risk_tier_aliases_are_normalised` |
| BL-03 | Framework alias normalisation + dedup | `["eu","EU-AI-Act","iso"]` | `["eu_ai_act","iso_42001"]` | `test_framework_aliases_are_normalised_and_deduped` |
| BL-04 | Zero-config default framework | `target_frameworks=[]` | `["iso_42001"]` baseline | `test_default_framework_when_none_supplied` |
| BL-05 | Applicable-framework derivation (ISO 42001 baseline union) | `["eu_ai_act"]` | `["eu_ai_act","iso_42001"]` | `test_derives_applicable_frameworks_with_baseline` |
| BL-06 | S-5 human-review classification | risk tiers high / unacceptable / minimal | `requires_human_review` True / True / False | `TestParseModelMetadataNode`, `TestComplianceCheckNode` |
| BL-07 | 7-section generation + advisory framing | normalised `model_metadata` | all 7 section keys; risk_classification advisory, never "is compliant" | `TestGenerateGovernanceSectionsNode` |
| BL-08 | Framework mapping + citations | `["eu_ai_act","iso_42001"]` | per-framework clause citations; advisory note never certifies | `TestComplianceCheckNode` |
| BL-09 | Document assembly + audit entry | 7 rendered sections + compliance flags | all 7 headers + `REGULATORY MAPPING NOTE`; SHA-256 audit entry | `TestOutputFormatNode` |
| BL-10 | Graph key coupling | inner `get_output` ↔ outer `merge_output` | 6 coupled keys mapped; `merge_output` returns changed keys only | `TestOuterGraphComposition`, `TestInnerDomainGraph` |

### Negative / boundary cases

| Case | Node | Expected |
|------|------|----------|
| empty `user_input` | PreProcessNode | `status=error`, "empty" |
| invalid JSON | PreProcessNode | `status=error`, "invalid JSON" |
| JSON root not an object | PreProcessNode | `status=error`, "object" |
| missing `risk_tier` (required) | PreProcessNode | `status=error`, "risk_tier" |
| EU Art. 5 prohibited intended_use | PreProcessNode | `status=error`, "prohibited practice"; no `validated_input` |
| empty `model_name` | InputValidateNode | `status=error`, "model_name" |
| empty `intended_use` | InputValidateNode | `status=error`, "intended_use" |
| missing `model_metadata` | Parse / Generate / Compliance | `status=error` |
| missing `governance_sections` | OutputFormatNode | `status=error` |
| empty `governance_document` | PostProcessNode | withheld, `status=error` — reaching post_process means the workflow reported success, so an empty document is a contradiction, not a degraded result |
| payload over the declared size cap | PreProcessNode | `status=error`, limit named, value never echoed |
| credential-shaped value in any field | PreProcessNode | `status=error`, field named, value never echoed |
| chat-template control marker (value or key) | PreProcessNode | `status=error`, finding class named |
| redacted `model_name` / `model_version` / `model_type` | PreProcessNode | `status=error`, field named |
| non-finite `performance_metrics` value | InputValidateNode | `status=error`, metric named |
| credential leak in output | PostProcessNode | withheld, `status=error` (S-3) |
| certification language in output | PostProcessNode | withheld, `status=error` (S-3) |

## 5. Test Execution Summary

- Execution: `pytest tests/` against the real framework wheel.
- Total: **259 tests — 258 passed, 1 skipped** (the HITL interrupt-propagation stub, by
  design: `propagate_hitl=False`, so there is no cross-boundary checkpoint to assert).
- Coverage: every node and graph module is exercised on both success and error paths, and
  the end-to-end suite drives the real ASGI `/invoke` rather than calling nodes directly.
- **Mutation-verified.** Each guard was reverted individually and the suite re-run; every
  revert fails at least one test, and restoring the original shipped `src/` fails 21 of the
  end-to-end tests. Two reverts initially failed to fail and were treated as findings rather
  than formalities: a redundant second whitespace pass made the structural guard unprovable
  (the passes were merged into one), and the input credential screen was unfalsifiable end to
  end because the framework refuses the same request one layer down — that assertion moved to
  node level, where the difference is observable.
