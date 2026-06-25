"""Beta Test Mode: broker-gated outbound message approval queue.

When Beta Test Mode is on (BETA_TEST_MODE), any message the bot autonomously
generates for another user — deal/match notifications, the vet-to-vet cascade,
freight/driver pickup & offload texts, backend-relayed workflow messages — is
NOT sent straight away. Instead it is held here and the broker is asked to
confirm it over SMS first. The broker can approve it (send as drafted), skip it
(drop it), or replace it with their own wording.

Sends the broker explicitly dictated/confirmed (blast, direct message, price
offers) and same-caller texts bypass this gate via notifications.send_sms_raw.

State is persisted to a JSON file using the same durable pattern as the session,
reminder, and negotiation stores.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from swinedesk.notifications import send_sms_raw
from swinedesk.settings import settings

logger = logging.getLogger(__name__)

# In-memory mirror of the durable store: {"seq": int, "items": {id: record}}.
_seq = 0
_items: dict[str, dict[str, Any]] = {}
_loaded = False
_lock = asyncio.Lock()
_warned_no_channel = False

# Decision words the broker may use when resolving a draft.
_SKIP_WORDS = {"skip", "reject", "no", "cancel", "drop", "discard", "kill", "don't send"}
_APPROVE_WORDS = {"approve", "send", "yes", "confirm", "ok", "okay", "go", "go ahead"}


def _has_broker_channel() -> bool:
    """Is there any broker phone we can route approvals to / accept reviews from?"""
    return bool(settings.broker_sms_phone_set) or bool(settings.effective_broker_alert_phone)


def should_intercept(to_phone: str | None) -> bool:
    """True if a message to this recipient should be held for broker approval."""
    global _warned_no_channel
    if not settings.beta_test_mode:
        return False
    if not (to_phone and str(to_phone).strip()):
        return False
    if settings.is_broker_phone(to_phone):
        # Broker-facing messages (alerts, summaries) never need broker approval.
        return False
    if not _has_broker_channel():
        # Fail open: with no broker to review, gating would silently swallow every
        # outbound message. Send normally and warn once.
        if not _warned_no_channel:
            logger.warning(
                "BETA_TEST_MODE is on but no broker phone is configured "
                "(BROKER_SMS_PHONES / BROKER_ALERT_PHONE) — outbound gating is disabled."
            )
            _warned_no_channel = True
        return False
    return True


def _store_path() -> Path:
    path = settings.beta_approval_store_path
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def _save() -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps({"seq": _seq, "items": _items}, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _load() -> None:
    global _loaded, _seq
    if _loaded:
        return
    path = _store_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _seq = int(data.get("seq", 0))
                items = data.get("items", {})
                if isinstance(items, dict):
                    _items.update(items)
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    _loaded = True


async def _recipient_label(to_phone: str) -> str:
    """Best-effort friendly label for the recipient, e.g. 'Buyer Jane Doe (+52...)'.

    Falls back to the bare phone when no local session/profile is on file.
    """
    try:
        from swinedesk.session import get_session

        session = await get_session(to_phone)
        if session is not None:
            profile = session.actor_profile or {}
            name = (
                profile.get("first_name")
                or profile.get("name")
                or profile.get("contact_name")
                or ""
            )
            role = (session.role or "").replace("_", " ").strip()
            role_label = role.title() if role and role != "unknown" else ""
            label = " ".join(p for p in [role_label, str(name).strip()] if p).strip()
            if label:
                return f"{label} ({to_phone})"
    except Exception:  # noqa: BLE001 - labeling is best-effort, never fatal
        logger.debug("Could not resolve recipient label for %s", to_phone, exc_info=True)
    return to_phone


def _broker_review_phone() -> str:
    """Where to text the draft for review."""
    return settings.effective_broker_alert_phone


def _format_broker_prompt(record: dict[str, Any]) -> str:
    rid = record["id"]
    return (
        f"[BETA] Draft #{rid} to {record.get('to_label') or record['to_phone']}:\n"
        f"\"{record['message']}\"\n"
        f"Reply: \"approve {rid}\" to send, \"skip {rid}\" to drop, "
        f"or \"send {rid} <your text>\" to replace."
    )


async def queue_outbound(
    to_phone: str, message: str, *, context: str = ""
) -> dict[str, Any]:
    """Hold an outbound message for broker approval and notify the broker."""
    global _seq
    label = await _recipient_label(to_phone)
    async with _lock:
        _load()
        _seq += 1
        rid = str(_seq)
        record = {
            "id": rid,
            "to_phone": to_phone,
            "to_label": label,
            "message": message,
            "context": context,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _items[rid] = record
        _save()

    # Notify the broker (best effort, ungated). On stage without live Twilio this
    # no-ops; the broker can still pull the queue with review_pending_messages.
    review_phone = _broker_review_phone()
    if review_phone:
        await send_sms_raw(review_phone, _format_broker_prompt(record))

    logger.info(
        "Beta Test Mode held outbound #%s to %s for broker approval", rid, to_phone
    )
    return {
        "success": True,
        "queued_for_approval": True,
        "approval_id": rid,
        "to_phone": to_phone,
    }


async def maybe_queue_outbound(
    to_phone: str, message: str, *, context: str = ""
) -> dict[str, Any] | None:
    """Queue for approval when interception applies; otherwise return None."""
    if not should_intercept(to_phone):
        return None
    return await queue_outbound(to_phone, message, context=context)


async def list_pending() -> list[dict[str, Any]]:
    """Return pending drafts, oldest first."""
    async with _lock:
        _load()
        pending = [dict(r) for r in _items.values() if r.get("status") == "pending"]
    return sorted(pending, key=lambda r: r.get("created_at", ""))


async def pending_count() -> int:
    pending = await list_pending()
    return len(pending)


def _normalize_id(approval_id: str | int | None) -> str:
    return str(approval_id or "").strip().lstrip("#").strip()


async def resolve_approval(
    approval_id: str | int | None,
    *,
    decision: str = "",
    message: str = "",
) -> dict[str, Any]:
    """Approve (send), skip (drop), or revise (send replacement) a pending draft.

    Providing replacement ``message`` text always means "send this instead",
    regardless of the decision wording.
    """
    rid = _normalize_id(approval_id)
    replacement = (message or "").strip()
    norm_decision = (decision or "").strip().lower()

    async with _lock:
        _load()
        record = _items.get(rid)
        if record is None or record.get("status") != "pending":
            pending_ids = sorted(
                (r["id"] for r in _items.values() if r.get("status") == "pending"),
                key=lambda x: int(x) if x.isdigit() else 0,
            )
            hint = f" Pending: {', '.join('#' + i for i in pending_ids)}." if pending_ids else ""
            return {"error": f"No pending message #{rid}.{hint}"}
        to_phone = record["to_phone"]
        original = record["message"]

    # Decide the action. Replacement text wins; then explicit skip; then approve.
    if not replacement and norm_decision in _SKIP_WORDS:
        async with _lock:
            stored = _items.get(rid)
            if stored is not None:
                stored["status"] = "skipped"
                stored["resolved_at"] = datetime.now(timezone.utc).isoformat()
                _save()
        logger.info("Beta Test Mode draft #%s skipped by broker", rid)
        return {"result": f"Skipped draft #{rid}. Nothing was sent to {to_phone}.", "id": rid}

    if not replacement and norm_decision and norm_decision not in _APPROVE_WORDS:
        return {
            "error": (
                f"Not sure what to do with draft #{rid}. Reply 'approve {rid}', "
                f"'skip {rid}', or 'send {rid} <new text>'."
            )
        }

    final_message = replacement or original
    revised = bool(replacement) and replacement != original

    send_result = await send_sms_raw(to_phone, final_message)
    delivered = bool(send_result.get("success"))

    async with _lock:
        stored = _items.get(rid)
        if stored is not None:
            stored["status"] = "sent" if delivered else "send_failed"
            stored["resolved_at"] = datetime.now(timezone.utc).isoformat()
            stored["final_message"] = final_message
            stored["revised"] = revised
            stored["delivered"] = delivered
            if not delivered:
                stored["last_error"] = str(send_result.get("error", "send failed"))
            _save()

    verb = "Sent your revised message" if revised else "Sent draft"
    if delivered:
        result_text = f"{verb} #{rid} to {to_phone}."
    else:
        # Outbound only delivers when Twilio is live; be honest about non-delivery.
        result_text = (
            f"Approved draft #{rid} for {to_phone}, but delivery failed "
            f"({send_result.get('error', 'Twilio not configured')})."
        )
    logger.info("Beta Test Mode draft #%s resolved (delivered=%s)", rid, delivered)
    return {
        "result": result_text,
        "id": rid,
        "delivered": delivered,
        "revised": revised,
        **send_result,
    }


async def purge_resolved(retention_days: int | None = None) -> int:
    """Drop resolved drafts older than the retention window. Returns count removed."""
    days = retention_days if retention_days is not None else settings.reminder_retention_days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with _lock:
        _load()
        stale = [
            rid
            for rid, r in _items.items()
            if r.get("status") in ("sent", "skipped", "send_failed")
            and r.get("resolved_at", "") < cutoff
        ]
        for rid in stale:
            _items.pop(rid, None)
        if stale:
            _save()
    if stale:
        logger.info("Purged %d resolved beta-approval drafts older than %d days", len(stale), days)
    return len(stale)
