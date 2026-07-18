"""Chat with the platform agent's model-serving endpoint.

The agent (agents/platform_agent) is read-only by construction; when asked to
change something it emits proposal markers that this router parses into
structured proposals. The UI renders them as cards whose Apply path is the
console's own guarded /api/actions flow — the agent's output is never trusted
to mutate anything directly. The backend is stateless: the conversation lives
in the browser.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from backend import deps
from backend.errors import payload
from backend.models import ChatRequest
from backend.proposals import parse_proposals

router = APIRouter(prefix="/api/chat")

_DEPLOY_HINT = (
    "Deploy the agent with `python agents/platform_agent/deploy_agent.py`, grant this "
    "app's service principal CAN_QUERY on the endpoint, and set "
    "DBX_PLATFORM_AGENT_ENDPOINT in app.yaml if the name differs. See docs/runbook.md."
)


def _extract_text(response: dict) -> str:
    """Pull assistant text out of a ResponsesAgent invocation response,
    tolerating shape drift across mlflow versions."""
    chunks: list[str] = []
    for item in response.get("output") or []:
        content = item.get("content")
        if isinstance(content, str):
            chunks.append(content)
            continue
        for part in content or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "\n".join(c for c in chunks if c).strip()


@router.post("")
def chat(body: ChatRequest):
    endpoint = deps.agent_endpoint()
    try:
        response = deps.get_ws().api_client.do(
            "POST",
            f"/serving-endpoints/{endpoint}/invocations",
            body={"input": [{"role": m.role, "content": m.content} for m in body.messages]},
        )
    except Exception as e:  # noqa: BLE001 — the agent is optional; degrade with guidance
        return JSONResponse(status_code=503, content=payload(
            "agent_unavailable", f"Could not reach agent endpoint '{endpoint}': {e}",
            _DEPLOY_HINT))
    text = _extract_text(response if isinstance(response, dict) else {})
    if not text:
        return JSONResponse(status_code=502, content=payload(
            "agent_bad_response", f"Agent endpoint '{endpoint}' returned no text."))
    message, proposals = parse_proposals(text)
    return {"message": message, "proposals": proposals, "endpoint": endpoint}
