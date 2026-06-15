"""Durable store for broker-initiated price offers (price negotiations).

When the broker asks a seller/buyer "would you take $85?", the question is sent
over SMS and the answer arrives later in a *different* conversation (the seller's).
This module holds the pending-offer state that bridges the two, persisted to a JSON
file the same way reminders and sessions are. The price is only updated when the
recipient cleanly accepts; counters and declines are relayed to the broker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from swinedesk.settings import settings

logger = logging.getLogger(__name__)

_offers: dict[str, dict[str, Any]] = {}
_loaded = False
_lock = asyncio.Lock()


def _store_path() -> Path:
    path = settings.negotiation_store_path
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def _save() -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(_offers, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _load() -> None:
    global _loaded
    if _loaded:
        return
    path = _store_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _offers.update(data)
        except (OSError, json.JSONDecodeError):
            pass
    _loaded = True


async def create_offer(
    *,
    to_phone: str,
    order_id: str,
    proposed_price: float,
    broker_phone: str,
    side: str = "",
    current_price: float | None = None,
    label: str = "",
) -> dict[str, Any]:
    """Record a pending price offer, superseding any prior pending offer for the
    same recipient + order so a reply is never ambiguous."""
    offer_id = uuid.uuid4().hex[:12]
    record = {
        "id": offer_id,
        "to_phone": to_phone,
        "order_id": order_id,
        "side": (side or "").upper(),
        "proposed_price": proposed_price,
        "current_price": current_price,
        "label": label,
        "broker_phone": broker_phone,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    async with _lock:
        _load()
        for other in _offers.values():
            if (
                other.get("status") == "pending"
                and other.get("to_phone") == to_phone
                and other.get("order_id") == order_id
            ):
                other["status"] = "superseded"
                other["resolved_at"] = record["created_at"]
        _offers[offer_id] = record
        _save()
    return record


async def get_pending_offer_for_phone(phone: str) -> dict[str, Any] | None:
    """Return the most recent pending offer addressed to this phone, if any."""
    if not phone:
        return None
    async with _lock:
        _load()
        pending = [
            r for r in _offers.values()
            if r.get("to_phone") == phone and r.get("status") == "pending"
        ]
    if not pending:
        return None
    return max(pending, key=lambda r: r.get("created_at", ""))


async def resolve_offer(
    offer_id: str, status: str, counter_price: float | None = None
) -> dict[str, Any] | None:
    """Mark an offer accepted / declined / countered."""
    async with _lock:
        _load()
        record = _offers.get(offer_id)
        if record is None:
            return None
        record["status"] = status
        if counter_price is not None:
            record["counter_price"] = counter_price
        record["resolved_at"] = datetime.now(timezone.utc).isoformat()
        _save()
    return record


async def purge_resolved_offers(retention_days: int | None = None) -> int:
    """Drop resolved offers older than the retention window. Returns count removed."""
    days = retention_days if retention_days is not None else settings.reminder_retention_days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with _lock:
        _load()
        stale = [
            oid for oid, r in _offers.items()
            if r.get("status") != "pending" and r.get("resolved_at", "") < cutoff
        ]
        for oid in stale:
            _offers.pop(oid, None)
        if stale:
            _save()
    if stale:
        logger.info("Purged %d resolved price offers older than %d days", len(stale), days)
    return len(stale)
