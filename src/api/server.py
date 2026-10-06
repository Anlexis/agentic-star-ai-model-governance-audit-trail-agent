"""AgentCore Platform v1.0"""

# Standalone HTTP entry point for the agent.
# Entry points are adapters only — no business logic here.
# For platform-level routing, AgentGateway calls agent.invoke() directly.

import os
import secrets
from typing import Any, Dict
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets import SecretProvider
from framework.secrets.context import bound_secrets
from shared.secrets import InMemoryProvider
from shared.secrets import factory as secrets_factory
from src.graph.graph import AIModelGovernanceAuditTrailAgent as Graph
from src.graph.graph import runtime_config

app = FastAPI(title="Agent")

# The platform registry loads config/config.yaml and passes it as
# Graph(config=...); this adapter mirrors that exactly, so a value declared in
# that file behaves identically on both paths. Constructing the graph with no
# config would leave every declared runtime parameter — the framework's own
# max_retry included — silently on its default.
agent = Graph(config=runtime_config())
agent.compile()
# Replace namespace/agent_name to match the agent's manifest values.
agent.provision_secrets(secrets_factory(namespace="cmn-c1-068", agent_name="AIModelGovernanceAuditTrailAgent"))


def _entrypoint_secrets() -> SecretProvider:
    """Boot-time SecretProvider for the ENTRY-POINT / DEPLOYMENT credential.

    S-5 (secret-provider boundary): the caller-auth token INVOKE_AUTH_TOKEN is a
    *deployment-level* credential — it authenticates the /invoke caller at the
    standalone HTTP boundary, BEFORE any InvocationContext (and thus ctx.secrets)
    exists — so it is a different class of secret from the agent's per-invocation
    secrets (resolved node-side through ctx.secrets.require()).

    The framework SecretProvider is still the sanctioned access boundary: this
    loads the deployment credential from its documented source (the process
    environment set by the deploy job — the staging runbook §5 / deploy/local-stg.yml
    / the pipeline definition deploy-stg exports it, and scripts/stg_invoke_evidence.py
    presents the SAME env value as a Bearer token) INTO an InMemoryProvider once
    at boot — exactly as shared.secrets.factory() loads dotenv values into a
    DotenvProvider — so the request handler reads the token via the provider
    accessor (.get()) rather than a raw os.environ read. Boot-safe: an
    absent/empty token yields a provider whose .get() returns None, preserving
    the "no token set → ANONYMOUS callers" contract with no raise at import.

    PATTERN NOTE (for other templates): entry-point/deployment credentials that
    arrive via the process environment are loaded into an InMemoryProvider here;
    agent secrets that arrive via env/*.env files stay on secrets_factory() and
    are read node-side through ctx.secrets.require().
    """
    values: Dict[str, str] = {}
    token = os.environ.get("INVOKE_AUTH_TOKEN")
    if token:
        values["INVOKE_AUTH_TOKEN"] = token
    return InMemoryProvider(values, namespace="cmn-c1-068", agent_name="AIModelGovernanceAuditTrailAgent")


_ENTRYPOINT_SECRETS = _entrypoint_secrets()


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> Dict[str, Any]:
    trust = getattr(request.state, "trust_level", TrustLevel.ANONYMOUS)
    # Standalone caller auth (the staging runbook §5): when
    # INVOKE_AUTH_TOKEN is set on the server environment, callers that no upstream
    # middleware vouched for (still ANONYMOUS) must present it as a Bearer token
    # and run at VERIFIED_EXTERNAL. Middleware-established trust is never demoted.
    # This adapter is the entry-point auth boundary (standalone equivalent of
    # platform AuthMiddleware) — a deployment-level caller credential, not an
    # agent secret, so ctx.secrets does not apply (no InvocationContext exists
    # before auth); see the framework rules §3 "Entry-point exception".
    #
    # S-5: the token is read through the framework SecretProvider accessor
    # (_ENTRYPOINT_SECRETS.get(), built once at boot from the deployment env),
    # never via a raw os.environ read in the handler.
    expected = _ENTRYPOINT_SECRETS.get("INVOKE_AUTH_TOKEN")
    if expected and trust is TrustLevel.ANONYMOUS:
        supplied = request.headers.get("authorization", "")
        # Compare bytes: compare_digest raises TypeError on non-ASCII str input
        # (headers decode as latin-1), which would 500 instead of the generic 401.
        if not secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
            # Generic body on purpose — do not leak whether the token was absent,
            # malformed, or wrong.
            raise HTTPException(status_code=401, detail="Token is invalid or expired.")
        trust = TrustLevel.VERIFIED_EXTERNAL
    with bound_secrets(agent._secrets_provider):
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        result: Dict[str, Any] = agent.invoke(req.input, ctx=ctx)
        return result


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "agent": "AIModelGovernanceAuditTrailAgent"}
