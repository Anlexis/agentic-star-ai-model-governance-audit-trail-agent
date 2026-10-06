# AI Model Governance Audit Trail Agent

AI agent for generating AI model governance documentation and audit trails, built with Agentic Star.

> **Category**: Cat 1 (industry-agnostic single technical capability)
> **Industry**: Common
> **Template ID**: CMN-C1-068

## Overview

Turns a declaration about a machine-learning model into a governance document and a
tamper-evident audit-trail entry. You hand the agent a model's metadata — name, version,
intended use, deployment scope, risk tier, reported metrics, declared limitations — and it
produces a seven-section model card, maps the model to the governance frameworks you named,
cites the specific clauses those frameworks require documentation against, and records a
content-addressed audit entry for the document it produced.

The document is **advisory by construction**. It supports a compliance assessment; it never
states that a system *is* compliant, *is* certified, or *meets all requirements*. That is not
a style guideline — it is enforced at the output boundary: a generated document matching
certification language is withheld in full rather than published with the phrase removed.

The agent documents what you declare. It does not inspect your model, read your training
data, or verify any claim you make about either; assessing whether a declaration is true is
somebody else's job. That boundary is deliberate, and it is what makes the template usable
against any model in any industry.

Three further properties are enforced rather than left to convention:

- **Caller text cannot impersonate document structure.** The document's meaning is carried by
  line structure — numbered headings, rules, and label lines such as
  `Human Governance Review:  RECOMMENDED`. Free-text fields are rendered as a single line with
  rule-character runs collapsed, so a newline in a declared limitation cannot manufacture an
  extra section or a governance verdict the agent never assessed.
- **Every rendered number is finite and every rendered identifier is inert.** A reported metric
  that is not a finite number fails the request rather than reaching the page as `nan`.
- **A refusal releases nothing.** When the output boundary blocks a document, the response
  carries a withholding notice and every document-bearing field is explicitly cleared — not
  merely omitted.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own frameworks and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from the package registry as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent fails at
graph compile / start-up preflight rather than starting in a partially working state. This is
intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Calling it

`POST /invoke` takes the model declaration as a JSON string in `input`:

```json
{
  "input": "{\"model_name\": \"AcmeCreditRiskScorer\", \"model_version\": \"2.1.0\", \"model_type\": \"gradient-boosted decision tree\", \"intended_use\": \"Assist loan officers in assessing consumer credit-risk applications as decision support; a human makes the final lending decision.\", \"deployment_scope\": \"internal decision support for retail lending\", \"risk_tier\": \"high\", \"target_frameworks\": [\"eu_ai_act\", \"iso_42001\"], \"training_data_summary\": \"Anonymised historical retail-loan performance records, 2018-2025.\", \"performance_metrics\": {\"auc\": 0.87, \"ks\": 0.42}, \"limitations\": \"Not validated for SME or commercial lending; monitor for demographic drift.\"}"
}
```

`model_name`, `intended_use` and `risk_tier` are required; everything else is optional and
degrades to a documented placeholder. `risk_tier` accepts the four canonical tiers
(`unacceptable` / `high` / `limited` / `minimal`) plus common aliases; an unrecognised value
falls back to `limited` rather than failing. `target_frameworks` entries are locked to
`[a-z0-9_]{1,32}` because they are rendered into a label line; an id this template has no
citation table for is still accepted and still named in the document. `performance_metrics`
values must be finite numbers.

A request is refused — with the offending field named, never its value — when it exceeds the
size cap, carries a credential-shaped value, carries a chat-template control marker, describes
a practice prohibited under EU AI Act Article 5, or names a model whose identity the platform
input filter has already redacted (see below).

When `INVOKE_AUTH_TOKEN` is set on the server environment, callers must present it as a Bearer
token.

### Known interaction: the platform input filter and title-case names

The platform masks personal-data-shaped spans in caller input **before this template runs**,
and its name heuristic matches any run of two or more title-case words. An ordinary model name
such as `Credit Risk Scorer` therefore arrives already replaced by a redaction sentinel. Two
routes are taken around it, deliberately different:

- **Identity fields** (`model_name`, `model_version`, `model_type`) — the request is **refused**
  and the field is named. An audit trail whose subject is a redaction sentinel is worse than no
  answer, because its reader gets no signal that the identity was erased. Supply an identifier
  that is not shaped like a personal name: `AcmeCreditRiskScorer` and `credit-risk-scorer` both
  pass, `Credit Risk Scorer` does not.
- **Prose fields** (intended use, limitations, training-data summary) — the request **succeeds**,
  and the document plus the audit entry both record which fields arrived redacted. Silent
  removal is the failure mode; a declared one is not.

## Project Structure

```
src/          agent implementation (nodes, graphs, schemas, guards)
tests/        unit, integration and boundary tests
config/       agent manifest and runtime parameters
docs/         design and operational documentation
prompts/      prompt template, for a model-backed generator (see Customising)
```

See `docs/` for the design and the test specification.

## Customising

1. `config/config.yaml` holds every runtime parameter: the payload size cap, the per-field
   text cap, and the caps on rendered metrics and frameworks. Values are validated on load;
   an out-of-range or non-integer setting is ignored in favour of the built-in default rather
   than silently removing a bound.
2. Extend the framework citation tables in `src/nodes/compliance_check_node.py` with the
   frameworks you govern against, and their human-readable names in
   `src/nodes/generate_governance_sections_node.py`.
3. Section generation is deterministic — the manifest declares
   `generation_mode: "deterministic"` and no language model is called. To generate the
   sections with a model instead, replace `GenerateGovernanceSectionsNode`, render
   `prompts/ai_governance.j2`, and change `generation_mode` to `llm` so the manifest keeps
   describing what the agent actually does.
4. Extend the certification-language patterns in `src/nodes/post_process_node.py` if your
   policy prohibits more phrasings. The credential half of that gate delegates to the
   framework's own detector and should stay that way — a narrower local set is a bypass, not
   a smaller gate.
5. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It
carries **no warranty and no support commitment**, and no organisation stands behind its
behaviour or fitness for any purpose. Issues and pull requests may or may not receive a
response; that is at the sole discretion of the repository owner.
