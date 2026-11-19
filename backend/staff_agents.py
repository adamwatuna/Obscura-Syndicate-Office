"""Execute an isolated OpenAI Agents session for the assigned staff profile."""
import json
import os
import re

from fastapi import HTTPException

from backend import ai_provider, meita_platform


def response(staff, payload, *, operation, timeout):
    if os.getenv("STAFF_PLATFORM_ENABLED", "false").lower() != "true":
        return ai_provider.response(payload, operation=operation, timeout=timeout)

    key = os.getenv("STAFF_PLATFORM_API_KEY", "").strip()
    if not key:
        raise HTTPException(503, "STAFF_PLATFORM_API_KEY is missing")

    # A profile can use a reusable saved agent, or define its own agent per run.
    try:
        bindings = json.loads(os.getenv("STAFF_PLATFORM_AGENT_IDS", "{}"))
        if not isinstance(bindings, dict) or any(
            not isinstance(value, str) or not re.fullmatch(r"agent_[A-Za-z0-9_-]{1,58}", value)
            for value in bindings.values()
        ) or len(bindings) != len(set(bindings.values())):
            raise ValueError
    except ValueError:
        raise HTTPException(503, "Invalid or duplicate staff Platform agent IDs") from None

    agent_id = bindings.get(staff["id"], "")
    search = bool(payload.get("tools"))
    config = {
        "model": payload["model"],
        "instructions": payload["instructions"],
        "tools": [{"type": "web_search", "mode": "live"}] if search else [],
        "multi_agent": {"enabled": False},
        "text": {"format": {"type": "text"}},
    }
    messages = [{"type": "message", **message} for message in payload["input"]]
    result = meita_platform.run_session(
        key, config, messages, agent_id=agent_id, timeout=timeout,
    )
    output = [message.model_dump(mode="json") for message in result.messages]
    # Final message annotations are provider citation metadata. The workforce
    # rejects research deliverables without verifiable citations.
    return {"output": output, "status": "completed", "agent_session": True}
