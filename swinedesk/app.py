"""FastAPI app for SwineDesk SMS webhooks."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import FastAPI, Form
from fastapi.responses import JSONResponse, Response
from twilio.twiml.messaging_response import MessagingResponse

from swinedesk.agent import run_swinedesk_agent
from swinedesk.backend_client import get_backend_client
from swinedesk.session import (
    add_message,
    get_or_create,
    mark_broker_alert_sent,
    start_cleanup_task,
    update_session,
    update_session_from_state,
)
from swinedesk.settings import settings
from swinedesk.state import SwineDeskState

app = FastAPI(title="SwineDesk", version="0.2.0")
logger = logging.getLogger(__name__)

UNKNOWN_PHONE_REPLY = (
    "Thanks for reaching out. We don't have your account on file yet. "
    "Someone from ELM Pork will contact you shortly."
)


def _chunk_message(text: str, chunk_size: int = 1500) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


def _parse_load_id_from_subject(subject: str) -> str | None:
    match = subject.upper().strip().replace(":", " ").replace("  ", " ")
    regex = re.compile(r"HEALTH\s+CERT\s+(ELM-[\w-]+)")
    found = regex.search(match)
    return found.group(1) if found else None


def _infer_unknown_intent(message: str) -> str:
    """Infer a coarse intent for unknown inbound numbers."""
    text = message.lower()
    if any(token in text for token in ("health cert", "certificate", "vet")):
        return "vet"
    if any(token in text for token in ("driver", "truck", "freight", "pickup", "delivery")):
        return "freight"
    if any(token in text for token in ("buy", "need pigs", "looking for pigs", "delivery")):
        return "buyer"
    if any(token in text for token in ("sell", "have pigs", "weaned pigs", "feeder pigs")):
        return "seller"
    return "unknown"


def _should_send_broker_alert(last_sent_iso: str | None) -> bool:
    """Throttle repeated broker alerts for the same unknown contact."""
    if not last_sent_iso:
        return True
    try:
        last_sent = datetime.fromisoformat(last_sent_iso)
    except ValueError:
        return True
    window = timedelta(minutes=settings.broker_alert_throttle_minutes)
    return datetime.now(timezone.utc) >= (last_sent + window)


def _build_broker_alert(phone: str, inbound: str, intent: str) -> str:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return (
        "New SMS lead\n"
        f"Phone: {phone}\n"
        f"Intent: {intent}\n"
        f"At: {timestamp}\n"
        f"Msg: {inbound}"
    )


def _normalize_site_ids(raw_value: object) -> list[str]:
    """Normalize site identifiers from backend payloads into a list of strings."""
    if isinstance(raw_value, list):
        return [str(item) for item in raw_value if str(item).strip()]
    if raw_value in ("", None):
        return []
    return [str(raw_value)]


def _configure_app_logging() -> None:
    app_logger = logging.getLogger("swinedesk")
    uvicorn_logger = logging.getLogger("uvicorn.error")
    app_logger.setLevel(logging.INFO)
    if uvicorn_logger.handlers:
        app_logger.handlers = uvicorn_logger.handlers
        app_logger.propagate = False


@app.on_event("startup")
async def on_startup() -> None:
    _configure_app_logging()
    key = settings.anthropic_api_key
    logger.info(
        "ANTHROPIC_API_KEY loaded: len=%d prefix=%s",
        len(key),
        key[:12] if key else "(empty)",
    )
    logger.info("SwineDesk SMS sender configured as %s", settings.twilio_phone_number or "(unset)")
    start_cleanup_task()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    backend = get_backend_client()
    await backend.close()


@app.get("/")
async def health() -> JSONResponse:
    return JSONResponse(
        {"status": "SwineDesk external SMS runtime", "time": datetime.now(timezone.utc).isoformat()}
    )


@app.post("/sms")
async def sms_webhook(
    body: Annotated[str | None, Form(alias="Body")] = None,
    from_phone: Annotated[str | None, Form(alias="From")] = None,
) -> Response:
    twiml = MessagingResponse()
    inbound = (body or "").strip()
    phone = (from_phone or "").strip()
    logger.info("Inbound SMS received: from=%s body=%r", phone, inbound)

    if not inbound or not phone:
        twiml.message("Couldn't read your message. Try again.")
        return Response(str(twiml), media_type="text/xml")

    try:
        backend = get_backend_client()
        session = await get_or_create(phone)
        prior_messages = list(session.messages)
        await add_message(phone, "user", inbound)

        actor = await backend.resolve_actor_by_phone(phone)
        resolved_role = str(actor.get("role", "unknown"))
        logger.info("Resolved inbound phone=%s to role=%s actor=%s", phone, resolved_role, actor)
        actor_updates = {
            "role": resolved_role,
            "actor_id": str(actor.get("actor_id", actor.get("id", ""))),
            "contact_id": str(actor.get("contact_id", actor.get("contactId", ""))),
            "company_id": str(actor.get("company_id", actor.get("companyId", ""))),
            "actor_profile": actor,
            "known_site_ids": _normalize_site_ids(
                actor.get("known_site_ids", actor.get("siteIds", []))
            ),
        }
        session = await update_session(phone, actor_updates)

        if resolved_role == "unknown":
            logger.warning("Phone %s resolved as unknown; sending unknown-contact reply", phone)
            inferred_intent = _infer_unknown_intent(inbound)
            now_iso = datetime.now(timezone.utc).isoformat()
            contact_attempt = {
                "phone": phone,
                "first_message": inbound,
                "timestamp": now_iso,
                "inferred_intent": inferred_intent,
            }
            await backend.create_unknown_contact_attempt(contact_attempt)

            if _should_send_broker_alert(session.last_broker_alert_at):
                await backend.notify_assigned_broker(
                    {
                        "broker_phone": settings.effective_broker_alert_phone,
                        "phone": phone,
                        "timestamp": now_iso,
                        "inferred_intent": inferred_intent,
                        "message": _build_broker_alert(phone, inbound, inferred_intent),
                    }
                )
                await mark_broker_alert_sent(phone, now_iso)

            twiml.message(UNKNOWN_PHONE_REPLY)
            await add_message(phone, "assistant", UNKNOWN_PHONE_REPLY)
            return Response(str(twiml), media_type="text/xml")

        state = session.to_state()
        result = await run_swinedesk_agent(
            user_prompt=inbound,
            state=state,
            message_history=prior_messages,
        )
        reply = str(result.output).strip() or "Got your message. Please retry in a minute."

        await update_session_from_state(phone, state)
        await add_message(phone, "assistant", reply)

        for chunk in _chunk_message(reply, 1500):
            twiml.message(chunk)

    except Exception:
        logger.exception("SMS handler failed for phone=%s", phone)
        twiml.message("Having a technical issue. Try again in a minute.")

    return Response(str(twiml), media_type="text/xml")


@app.post("/docs/health-cert")
async def health_cert_webhook(payload: dict) -> JSONResponse:
    subject = str(payload.get("subject", "") or "")
    from_email = str(payload.get("from", "") or "")
    attachment_url = str(payload.get("attachment_url", "") or "")

    load_id = _parse_load_id_from_subject(subject)
    if not load_id:
        return JSONResponse(
            {"success": False, "error": "Could not parse load ID from subject"},
            status_code=400,
        )

    backend = get_backend_client()
    try:
        await backend.mark_health_cert_received(
            {
                "load_id": load_id,
                "from_email": from_email,
                "attachment_url": attachment_url,
            }
        )
    except Exception:
        return JSONResponse(
            {
                "success": False,
                "error": "Failed to mark health cert received in backend",
                "load_id": load_id,
            },
            status_code=502,
        )

    return JSONResponse({"success": True, "load_id": load_id})
