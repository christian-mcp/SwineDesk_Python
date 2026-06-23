"""FastAPI app for SwineDesk SMS webhooks."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import Gather, VoiceResponse

from swinedesk import voice
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
from swinedesk.state import Channel, SwineDeskState

app = FastAPI(title="SwineDesk", version="0.2.0")
logger = logging.getLogger(__name__)

UNKNOWN_PHONE_REPLY = (
    "Thanks for reaching out. We don't have your account on file yet. "
    "Someone from ELM Pork will contact you shortly."
)

# State-changing actions worth a written record. When one of these runs during a
# voice call, the spoken reply is also texted to the caller as a confirmation.
IMPORTANT_TOOL_PATHS = {
    "/tools/market/create_sell_listing",
    "/tools/market/create_buy_request",
    "/tools/market/match_orders",
    "/tools/market/reject_order",
    "/tools/market/propose_price",
    "/tools/market/respond_to_price_offer",
    "/tools/orders/submit_purchase_order",
    "/tools/loads/confirm_freight_assignment",
    "/tools/loads/submit_freight_details",
    "/tools/loads/complete_load",
    "/tools/ops/submit_freight_by_text",
    "/tools/grading/submit_grading",
    "/tools/health/mark_cert_received",
    "/tools/issues/report_delivery_issue",
    "/tools/reminders/set_reminder",
}

VOICE_FALLBACK_REPLY = "Sorry, I didn't catch that. Could you say that again?"
VOICE_GOODBYE = "Thanks for calling ELM Pork. Goodbye."
VOICE_TECH_ISSUE = "Sorry, we're having a technical issue. Please try again in a minute."
VOICE_FILLER = "One moment while I pull that up."

# Twilio drops a webhook that doesn't respond within ~15s. An agent turn (backend
# lookups + LLM + tool calls) can easily exceed that, so a turn runs in the
# background and Twilio polls /voice/poll until it's ready. Keyed by Twilio CallSid.
_voice_turns: dict[str, dict] = {}
# How long to wait inline in /voice/gather before handing off to the poll loop.
VOICE_INLINE_WAIT_SECONDS = 8.0
# Silence between poll redirects, and the cap before we give up on a slow turn.
VOICE_POLL_PAUSE_SECONDS = 2
VOICE_MAX_POLLS = 25
# Abandoned turns (caller hung up mid-think) are pruned after this long.
VOICE_TURN_TTL_SECONDS = 180


def _prune_voice_turns(now: float) -> None:
    stale = [
        sid
        for sid, turn in _voice_turns.items()
        if (now - turn.get("created_at", now)) > VOICE_TURN_TTL_SECONDS
    ]
    for sid in stale:
        _voice_turns.pop(sid, None)


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


# Maps a backend notification action to the workflow tag we stamp on the recipient's
# session, so their SMS reply is understood without them quoting an order/load id.
_ACTION_WORKFLOW: dict[str, str] = {
    "BUYER_VET_CONFIRM_MESSAGE": "awaiting_vet_to_vet_confirm",
    "SELLER_VET_TO_REACH_OUT_MESSAGE": "vet_to_vet_seller_heads_up",
    "HEALTH_CERTIFICATE_FORM_ACTION": "awaiting_health_cert",
    "HEALTH_CERT_REMINDER": "awaiting_health_cert",
    "HEALTH_CERT_OVERDUE": "awaiting_health_cert",
    "VET_CHECK_REMINDER": "awaiting_health_cert",
    "FREIGHT_NOMINATION_REMINDER": "awaiting_freight_assignment",
    "FREIGHT_DRIVER_ASSIGNMENT_REMINDER": "awaiting_driver_assignment",
    "DRIVER_MESSAGE_SCALE_TICKET": "awaiting_driver_action",
    "DRIVER_MESSAGE_HEALTH_CERTIFICATE": "awaiting_driver_action",
    "BUYER_NOMINATION_REMINDER": "awaiting_buyer_nomination",
    "BUYER_GRADING_DAY_AFTER_SCHEDULED_DATE": "awaiting_grading",
    "BUYER_GRADING_2_DAYS_AFTER_SCHEDULED_DATE": "awaiting_grading",
    "SITE_MANAGER_GRADING_SCHEDULED_DATE": "awaiting_grading",
    "SITE_MANAGER_GRADING_DAY_AFTER_SCHEDULED_DATE": "awaiting_grading",
    "SITE_MANAGER_GRADING_2_DAYS_AFTER_SCHEDULED_DATE": "awaiting_grading",
}


async def _seed_recipient_session(to_phone: str, event: dict) -> None:
    """Seed the recipient's session with the order/load this notification is about plus a
    workflow tag, so a reply ('all good', 'picked them up', a cert photo) routes to the
    right intent without the recipient quoting an id. Best-effort; never blocks the send."""
    if not to_phone or not isinstance(event, dict):
        return
    updates: dict[str, object] = {}
    order_id = str(event.get("order_short_id") or "").strip()
    load_id = str(event.get("load_short_id") or "").strip()
    if order_id:
        updates["referenced_order_ids"] = [order_id]
    if load_id:
        updates["referenced_load_ids"] = [load_id]
    action = str(event.get("notification_action_type") or "").strip().upper()
    workflow = _ACTION_WORKFLOW.get(action)
    if workflow:
        updates["active_workflow"] = workflow
    if updates:
        try:
            await update_session(to_phone, updates)
        except Exception:
            logger.exception("Failed to seed recipient session for %s", to_phone)


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

    # Seed context BEFORE sending so an immediate reply already has it (and so it works
    # even when Twilio is offline on stage and we simulate the inbound reply by hand).
    await _seed_recipient_session(to_phone, event)

    result = await send_sms_notification(to_phone, message)
    status_code = 200 if result.get("success") else 502
    return JSONResponse({"event": event, **result}, status_code=status_code)


def _extract_executed_tool_paths(result: object) -> set[str]:
    """Return the set of custom tool paths the agent executed this turn.

    All custom tools route through the single ``execute_tool`` bridge, so the real
    tool path lives in that call's ``tool_name`` argument.
    """
    paths: set[str] = set()
    try:
        messages = result.all_messages()  # type: ignore[attr-defined]
    except Exception:
        return paths
    for message in messages:
        for part in getattr(message, "parts", None) or []:
            tool_name = getattr(part, "tool_name", None)
            if tool_name is None:
                continue
            args = getattr(part, "args", None)
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            if tool_name == "execute_tool" and isinstance(args, dict):
                inner = args.get("tool_name")
                if isinstance(inner, str):
                    paths.add(inner)
            else:
                paths.add(str(tool_name))
    return paths


@dataclass
class InboundResult:
    """Outcome of processing one inbound user turn (SMS or voice)."""

    reply: str
    handled_unknown: bool = False
    important: bool = False


async def _process_inbound(phone: str, inbound: str, channel: Channel) -> InboundResult:
    """Resolve the actor, run the role agent, and return the reply.

    Shared by the SMS and voice webhooks so both channels do exactly the same
    operations. ``channel`` only affects phrasing and confirmation-text behavior.
    """
    backend = get_backend_client()
    session = await get_or_create(phone)
    prior_messages = list(session.messages)
    await add_message(phone, "user", inbound)

    actor = await backend.resolve_actor_by_phone(phone)
    resolved_role = str(actor.get("role", "unknown"))
    # Internal broker: a phone on the allowlist always resolves to broker,
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

        await add_message(phone, "assistant", UNKNOWN_PHONE_REPLY)
        return InboundResult(reply=UNKNOWN_PHONE_REPLY, handled_unknown=True)

    state = session.to_state()
    state.channel = channel
    pending_offer = await get_pending_offer_for_phone(phone)
    if pending_offer:
        state.pending_offer = pending_offer
        logger.info("Inbound phone=%s has pending price offer %s", phone, pending_offer.get("id"))
    logger.info(
        "Running agent: channel=%s role=%s tier=%s actor=%s workflow=%s",
        channel,
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

    executed = _extract_executed_tool_paths(result)
    important = bool(executed & IMPORTANT_TOOL_PATHS)
    logger.info(
        "Agent reply: channel=%s role=%s chars=%d tools=%s important=%s",
        channel,
        resolved_role,
        len(reply),
        ",".join(sorted(executed)) or "none",
        important,
    )

    await update_session_from_state(phone, state)
    await add_message(phone, "assistant", reply)

    return InboundResult(reply=reply, important=important)


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
        result = await _process_inbound(phone, inbound, "sms")
        for chunk in _chunk_message(result.reply, 1500):
            twiml.message(chunk)
    except Exception:
        logger.exception("SMS handler failed for phone=%s", phone)
        twiml.message("Having a technical issue. Try again in a minute.")

    return Response(str(twiml), media_type="text/xml")


def _public_base_url(request: Request) -> str:
    """Absolute base URL Twilio uses to fetch generated audio."""
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")
    # Honor the proxy's forwarded scheme (Railway/Heroku terminate TLS upstream).
    forwarded_proto = request.headers.get("x-forwarded-proto")
    base = str(request.base_url).rstrip("/")
    if forwarded_proto:
        base = re.sub(r"^https?", forwarded_proto.split(",")[0].strip(), base)
    return base


async def _speak(target: VoiceResponse | Gather, text: str, request: Request,
                 *, static: bool = False) -> None:
    """Append spoken audio to a TwiML node, via ElevenLabs with a Twilio fallback."""
    audio_id = (
        await voice.synthesize_phrase_and_store(text)
        if static
        else await voice.synthesize_and_store(text)
    )
    if audio_id:
        target.play(f"{_public_base_url(request)}/voice/audio/{audio_id}.mp3")
    else:
        target.say(text)


def _new_gather() -> Gather:
    """A speech-input Gather that posts the transcript back to /voice/gather."""
    return Gather(
        input="speech",
        action="/voice/gather",
        method="POST",
        speechTimeout="auto",
        speechModel="phone_call",
        actionOnEmptyResult=True,
        language="en-US",
    )


@app.post("/voice")
async def voice_webhook(request: Request) -> Response:
    """Inbound call entrypoint: greet the caller and listen for speech."""
    response = VoiceResponse()
    if not voice.voice_available():
        response.say(VOICE_TECH_ISSUE)
        response.hangup()
        return Response(str(response), media_type="text/xml")

    gather = _new_gather()
    await _speak(gather, settings.voice_greeting, request, static=True)
    response.append(gather)
    # Reached only if the caller stays silent past the gather timeout.
    await _speak(response, VOICE_GOODBYE, request, static=True)
    response.hangup()
    return Response(str(response), media_type="text/xml")


async def _finish_voice_turn(call_sid: str, request: Request) -> Response:
    """Build the spoken reply for a completed turn and keep the call going."""
    response = VoiceResponse()
    turn = _voice_turns.pop(call_sid, None)
    task: asyncio.Task | None = turn.get("task") if turn else None
    phone = turn.get("phone", "") if turn else ""

    if task is None:
        await _speak(response, VOICE_TECH_ISSUE, request, static=True)
        response.hangup()
        return Response(str(response), media_type="text/xml")

    try:
        result: InboundResult = task.result()
    except Exception:
        logger.exception("Voice turn failed for call=%s phone=%s", call_sid, phone)
        await _speak(response, VOICE_TECH_ISSUE, request, static=True)
        response.hangup()
        return Response(str(response), media_type="text/xml")

    # Important actions get a written confirmation texted to the caller.
    if result.important and result.reply:
        sms_result = await send_sms_notification(phone, result.reply)
        logger.info("Voice confirmation SMS to %s: %s", phone, sms_result.get("success"))

    if result.handled_unknown:
        await _speak(response, result.reply, request)
        response.hangup()
        return Response(str(response), media_type="text/xml")

    gather = _new_gather()
    await _speak(gather, result.reply, request)
    response.append(gather)
    await _speak(response, VOICE_GOODBYE, request, static=True)
    response.hangup()
    return Response(str(response), media_type="text/xml")


@app.post("/voice/gather")
async def voice_gather(
    request: Request,
    speech_result: Annotated[str | None, Form(alias="SpeechResult")] = None,
    from_phone: Annotated[str | None, Form(alias="From")] = None,
    call_sid: Annotated[str | None, Form(alias="CallSid")] = None,
) -> Response:
    """Kick off one spoken turn in the background; finish inline if it's fast."""
    response = VoiceResponse()
    inbound = (speech_result or "").strip()
    phone = (from_phone or "").strip()
    sid = (call_sid or "").strip() or phone
    logger.info("Inbound voice turn: call=%s from=%s speech=%r", sid, phone, inbound)

    if not phone:
        await _speak(response, VOICE_TECH_ISSUE, request, static=True)
        response.hangup()
        return Response(str(response), media_type="text/xml")

    if not inbound:
        gather = _new_gather()
        await _speak(gather, VOICE_FALLBACK_REPLY, request, static=True)
        response.append(gather)
        await _speak(response, VOICE_GOODBYE, request, static=True)
        response.hangup()
        return Response(str(response), media_type="text/xml")

    now = time.time()
    _prune_voice_turns(now)
    task = asyncio.create_task(_process_inbound(phone, inbound, "voice"))
    _voice_turns[sid] = {"task": task, "phone": phone, "polls": 0, "created_at": now}

    # Give a fast turn a chance to finish inline so the caller hears no filler.
    done, _ = await asyncio.wait({task}, timeout=VOICE_INLINE_WAIT_SECONDS)
    if task in done:
        return await _finish_voice_turn(sid, request)

    # Slow turn: tell the caller to hold, then poll for the result.
    await _speak(response, VOICE_FILLER, request, static=True)
    response.redirect("/voice/poll", method="POST")
    return Response(str(response), media_type="text/xml")


@app.post("/voice/poll")
async def voice_poll(
    request: Request,
    call_sid: Annotated[str | None, Form(alias="CallSid")] = None,
    from_phone: Annotated[str | None, Form(alias="From")] = None,
) -> Response:
    """Wait for a backgrounded turn to finish, holding the call with short pauses."""
    sid = (call_sid or "").strip() or (from_phone or "").strip()
    turn = _voice_turns.get(sid)

    if turn is None or turn.get("task") is None:
        response = VoiceResponse()
        await _speak(response, VOICE_TECH_ISSUE, request, static=True)
        response.hangup()
        return Response(str(response), media_type="text/xml")

    task: asyncio.Task = turn["task"]
    if task.done():
        return await _finish_voice_turn(sid, request)

    turn["polls"] = turn.get("polls", 0) + 1
    if turn["polls"] >= VOICE_MAX_POLLS:
        logger.warning("Voice turn exceeded poll budget for call=%s", sid)
        _voice_turns.pop(sid, None)
        response = VoiceResponse()
        await _speak(response, VOICE_TECH_ISSUE, request, static=True)
        response.hangup()
        return Response(str(response), media_type="text/xml")

    response = VoiceResponse()
    response.pause(length=VOICE_POLL_PAUSE_SECONDS)
    response.redirect("/voice/poll", method="POST")
    return Response(str(response), media_type="text/xml")


@app.get("/voice/audio/{audio_id}.mp3")
async def voice_audio(audio_id: str) -> Response:
    """Serve cached TTS audio for Twilio <Play>."""
    data = voice.get_audio(audio_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Audio not found")
    return Response(content=data, media_type="audio/mpeg")


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
