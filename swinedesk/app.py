"""FastAPI app for SwineDesk SMS webhooks."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import FastAPI, Form, Header, HTTPException
from fastapi.responses import JSONResponse, Response
from twilio.twiml.messaging_response import MessagingResponse

from swinedesk.agent import run_swinedesk_agent
from swinedesk.backend_client import get_backend_client
from swinedesk.daily_summary import start_daily_summary_task
from swinedesk.negotiations import get_pending_offer_for_phone
from swinedesk.notifications import send_sms_notification
from swinedesk.reminders import start_reminder_scheduler
from swinedesk.phone_region import infer_region_from_phone
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


def _strip_formatting(text: str) -> str:
    """Remove markdown that renders as literal characters on SMS."""
    # Bold/italic asterisks and underscores
    text = re.sub(r"\*{1,3}(.*?)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,2}(.*?)_{1,2}", r"\1", text)
    # Inline code backticks
    text = re.sub(r"`{1,3}(.*?)`{1,3}", r"\1", text)
    # Markdown headers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Em/en dashes render badly on SMS and read awkwardly. Replace separator
    # usages (with surrounding spaces) with a comma. Bare em/en dashes also drop
    # to comma — we never want them in outbound SMS. Compound hyphens like
    # "12-14 lbs" stay intact since they use the regular "-" character.
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    # Curly quotes to straight quotes
    text = (text.replace("‘", "'").replace("’", "'")
                .replace("“", '"').replace("”", '"'))
    # Trailing spaces left by removals
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


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
    region = infer_region_from_phone(phone) or "unknown"
    return (
        "New SMS lead\n"
        f"Phone: {phone}\n"
        f"Region: {region}\n"
        f"Intent: {intent}\n"
        f"At: {timestamp}\n"
        f"Msg: {inbound}"
    )


def _is_broker_phone(phone: str) -> bool:
    """True if this phone is on the broker SMS allowlist (BROKER_SMS_PHONES)."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    return bool(digits) and digits in settings.broker_sms_phone_set


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


def _validate_backend_notification_token(authorization_header: str | None) -> None:
    """Require the shared backend token when one is configured."""
    if not settings.backend_api_token:
        return
    expected_header = f"Bearer {settings.backend_api_token}"
    if authorization_header != expected_header:
        raise HTTPException(status_code=401, detail="Invalid notification token")


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
    start_daily_summary_task()
    start_reminder_scheduler()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    backend = get_backend_client()
    await backend.close()


@app.get("/")
async def health() -> JSONResponse:
    return JSONResponse(
        {"status": "SwineDesk external SMS runtime", "time": datetime.now(timezone.utc).isoformat()}
    )


@app.post("/notifications/sms")
async def backend_sms_notification(
    payload: dict,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> JSONResponse:
    _validate_backend_notification_token(authorization)

    to_phone = str(payload.get("to_phone", "") or "").strip()
    message = str(payload.get("message", "") or "").strip()
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    logger.info(
        "Outbound backend notification requested: to=%s action=%s method=%s",
        to_phone,
        event.get("notification_action_type"),
        event.get("notification_method"),
    )

    if not to_phone or not message:
        return JSONResponse(
            {"success": False, "error": "Missing to_phone or message."},
            status_code=400,
        )

    result = await send_sms_notification(to_phone, message)
    status_code = 200 if result.get("success") else 502
    return JSONResponse({"event": event, **result}, status_code=status_code)


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
        # Internal broker over SMS: a phone on the allowlist always resolves to broker,
        # regardless of what the backend phonebook says.
        if _is_broker_phone(phone):
            resolved_role = "broker"
            actor = {**actor, "role": "broker"}
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
        pending_offer = await get_pending_offer_for_phone(phone)
        if pending_offer:
            state.pending_offer = pending_offer
            logger.info("Inbound phone=%s has pending price offer %s", phone, pending_offer.get("id"))
        logger.info(
            "Running agent: role=%s tier=%s actor=%s workflow=%s",
            resolved_role,
            state.user_tier,
            state.actor_id or "none",
            state.active_workflow or "none",
        )
        result = await run_swinedesk_agent(
            user_prompt=inbound,
            state=state,
            message_history=prior_messages,
        )
        raw_reply = str(result.output).strip() or "Got your message. Please retry in a minute."
        reply = _strip_formatting(raw_reply)
        logger.info(
            "Agent reply: role=%s chars=%d tools_used=%s",
            resolved_role,
            len(reply),
            getattr(result, "all_messages_json", None) and "yes" or "unknown",
        )

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
