"""AgentCore Platform v1.0"""

# Service layer: domain queries, external API wrappers, data aggregation.
# Must NOT contain business logic, routing, or credentials.
# Nodes call this; this calls shared/services/ for external integrations.

from __future__ import annotations

from typing import Any


class Service:
    """Retained scaffold service stub — intentionally unused on the runtime path.

    Source-review note (eng-moderator, SR #12): CMN-C1-068's domain logic lives
    entirely in the inner DomainWorkflowGraph nodes (src/nodes/), so this service
    layer has no runtime caller. The module is deliberately retained (not deleted)
    because the CI ``gate-scaffold-integrity`` job requires a real
    ``src/services/`` module to exist. ``fetch()`` is never invoked on any code
    path; it raises NotImplementedError to fail loudly should a future change wire
    it in without providing an implementation.
    """

    async def fetch(self, query: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Not implemented — retained scaffold stub (see class docstring).

        Never called on the runtime path. Implement only if a service-layer call
        is genuinely added to a future revision of this template.
        """
        raise NotImplementedError(
            "Service.fetch() is an intentionally-unimplemented scaffold stub for "
            "CMN-C1-068 (domain logic lives in src/nodes/)."
        )
