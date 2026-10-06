"""AgentCore Platform v1.0"""

# CMN-C1-068 — Outer→inner runtime-settings bridge.
#
# Why this exists
# ---------------
# The runtime parameters this agent honours arrive as ``Graph(config=...)`` on the
# OUTER graph. The nodes that consume them live in the INNER graph, and there is
# no path between the two:
#
#   * ``BaseNode.__call__`` invokes ``execute(state)`` with one argument, so a node
#     signature of ``execute(self, state, config=None)`` can never receive a value —
#     the parameter is decorative.
#   * ``GraphNode.execute()`` calls ``get_subgraph()`` BEFORE ``extract_input(state)``,
#     so the subgraph constructor cannot read outer state either.
#   * A subgraph's ``invoke()`` builds its own initial state from scratch; nothing of
#     the parent's state travels with it.
#
# The one ordering that does work is the one used here: ``extract_input(state)`` runs
# after the subgraph is constructed and before ``subgraph.invoke(...)``, so it can
# stash the settings, and the subgraph's ``_extra_initial_state()`` hook — called
# inside that ``invoke`` — can read them back and seed them into inner state.
#
# The value carried is the already-validated, already-bounded settings mapping, as a
# JSON string, so what crosses the boundary is inert data rather than a live object.

import contextvars
from typing import Optional

# JSON-serialised governance settings for the subgraph invocation in flight.
# A ContextVar (not a module global) so concurrent invocations in the same process
# cannot read each other's settings.
_GOVERNANCE_SETTINGS: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "cmn_c1_068_governance_settings", default=None
)


def stash_governance_settings(settings_json: Optional[str]) -> None:
    """Record the settings for the subgraph invocation about to start."""
    _GOVERNANCE_SETTINGS.set(settings_json)


def take_governance_settings() -> Optional[str]:
    """Return the stashed settings, or None when the subgraph is invoked directly.

    Returning None is a supported outcome, not a failure: a unit test may invoke
    ``DomainWorkflowGraph`` on its own, and each node then falls back to its module
    defaults rather than to a half-populated mapping.
    """
    return _GOVERNANCE_SETTINGS.get()
