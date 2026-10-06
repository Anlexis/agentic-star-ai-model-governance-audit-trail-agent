"""AgentCore Platform v1.0"""

# AgentRegistry entry-point export (source-review fix — eng-moderator, SR #12).
# config/agent.yaml declares `module: src.graph` + `class:
# AIModelGovernanceAuditTrailAgent`, so `import src.graph` MUST expose that class
# for AgentRegistry to resolve the manifest entry point
# (getattr(import_module("src.graph"), "AIModelGovernanceAuditTrailAgent")).
# The empty scaffold __init__ passed CI (which imports src.graph.graph directly)
# but failed a real registry load. Re-export the class (and the `Graph` alias
# server.py imports) from the package root.
from .graph import AIModelGovernanceAuditTrailAgent, Graph

__all__ = ["AIModelGovernanceAuditTrailAgent", "Graph"]
