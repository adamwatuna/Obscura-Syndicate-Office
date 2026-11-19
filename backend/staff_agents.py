"""Execute an isolated OpenAI Agents session for the assigned staff profile."""
import json
import logging
import os
import re

from fastapi import HTTPException

from backend import ai_provider, department_playbooks, meita_platform


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

    agent_id = staff.get("platform_agent_id") or bindings.get(staff["id"], "")
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


def bind(db, staff_id, agent_id):
    from psycopg.errors import UniqueViolation

    if agent_id == os.getenv("MEITA_PLATFORM_AGENT_ID", "").strip():
        raise HTTPException(409, "MEITA's agent is reserved for Meita Chat")
    try:
        row = db.execute(
            "UPDATE staff SET platform_agent_id=%s WHERE id=%s RETURNING id",
            (agent_id, staff_id),
        ).fetchone()
    except UniqueViolation:
        raise HTTPException(409, "This agent is already linked to another staff member") from None
    if not row:
        raise HTTPException(404, "Staff not found")


def provision_roster(connect):
    """Create saved agents for unlinked Directors and Specialists on rollout."""
    logger = logging.getLogger("uvicorn.error")
    key = os.getenv("STAFF_PLATFORM_API_KEY", "").strip()
    if os.getenv("STAFF_PLATFORM_ENABLED", "false").lower() != "true" or not key:
        logger.warning("Staff agent provisioning skipped: Agents connection is disabled or missing")
        return
    try:
        from openai import OpenAI

        with connect() as lock_db:
            lock = lock_db.execute(
                "SELECT pg_try_advisory_xact_lock(717071476) AS acquired"
            ).fetchone()
            if not lock["acquired"]:
                return
            staff_ids = [row["id"] for row in lock_db.execute("""
                SELECT id FROM staff WHERE org_role IN ('Director', 'Specialist')
                AND platform_agent_id IS NULL ORDER BY id
            """).fetchall()]
            if not staff_ids:
                linked = lock_db.execute("""
                    SELECT COUNT(*) AS count FROM staff
                    WHERE org_role IN ('Director', 'Specialist')
                    AND platform_agent_id IS NOT NULL
                """).fetchone()["count"]
                logger.info("Staff saved agents verified: %s linked, 0 pending", linked)
                return

            with OpenAI(api_key=key, timeout=15, max_retries=0) as client:
                agents = client.beta.agents
                # Recover agents already created if an earlier rollout stopped
                # between the OpenAI response and the local database commit.
                existing = {}
                for agent in agents.list():
                    staff_id = (agent.metadata or {}).get("obscura_staff_id")
                    if staff_id:
                        existing[staff_id] = agent.id

                for staff_id in staff_ids:
                    with connect() as db:
                        staff = db.execute("""
                            SELECT * FROM staff WHERE id=%s AND platform_agent_id IS NULL
                            AND org_role IN ('Director', 'Specialist') FOR UPDATE
                        """, (staff_id,)).fetchone()
                        if not staff:
                            continue
                        agent_id = existing.get(staff_id)
                        if not agent_id:
                            agent_id = agents.create(
                                name=staff["name"],
                                model=os.getenv("OPENAI_MODEL", "gpt-5.4"),
                                instructions=department_playbooks.agent_scope(staff),
                                metadata={"obscura_staff_id": staff_id},
                                tools=[],
                                multi_agent={"enabled": False},
                            ).id
                        bind(db, staff_id, agent_id)
                counts = lock_db.execute("""
                    SELECT COUNT(platform_agent_id) AS linked,
                           COUNT(*) - COUNT(platform_agent_id) AS pending
                    FROM staff WHERE org_role IN ('Director', 'Specialist')
                """).fetchone()
                logger.info("Staff saved agents verified: %s linked, %s pending",
                            counts["linked"], counts["pending"])
    except Exception as error:
        # Report safe error metadata without leaking a key or a staffing brief.
        logger.warning("Staff saved-agent provisioning stopped: %s (HTTP %s)",
                       type(error).__name__, getattr(error, "status_code", "unavailable"))
