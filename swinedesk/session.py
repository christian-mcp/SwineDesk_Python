"""Durable per-phone session store for SwineDesk SMS conversations."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from swinedesk.settings import settings
from swinedesk.state import SwineDeskState


@dataclass(slots=True)
class Session:
    """Per-phone conversation state."""

    phone: str
    role: str = "unknown"
    user_tier: str = "known"
    session_count: int = 0
    actor_id: str = ""
    contact_id: str = ""
    company_id: str = ""
    active_workflow: str | None = None
    complete: bool = False
    actor_profile: dict[str, Any] = field(default_factory=dict)
    draft_data: dict[str, Any] = field(default_factory=dict)
    referenced_order_ids: list[str] = field(default_factory=list)
    referenced_load_ids: list[str] = field(default_factory=list)
    known_site_ids: list[str] = field(default_factory=list)
    escalation_flags: list[str] = field(default_factory=list)
    last_broker_alert_at: str | None = None
    messages: list[dict[str, str]] = field(default_factory=list)
    last_activity: float = field(default_factory=time.time)

    def to_state(self) -> SwineDeskState:
        """Convert session storage shape into agent state."""
        return SwineDeskState(
            phone=self.phone,
            role=self.role,  # type: ignore[arg-type]
            user_tier=self.user_tier,  # type: ignore[arg-type]
            actor_id=self.actor_id,
            contact_id=self.contact_id,
            company_id=self.company_id,
            active_workflow=self.active_workflow,
            complete=self.complete,
            actor_profile=self.actor_profile,
            draft_data=self.draft_data,
            referenced_order_ids=list(self.referenced_order_ids),
            referenced_load_ids=list(self.referenced_load_ids),
            known_site_ids=list(self.known_site_ids),
            last_broker_alert_at=self.last_broker_alert_at,
            escalation_flags=list(self.escalation_flags),
        )

    def apply_state(self, state: SwineDeskState) -> None:
        """Persist state changes back onto the session."""
        self.role = state.role
        self.user_tier = state.user_tier
        self.actor_id = state.actor_id
        self.contact_id = state.contact_id
        self.company_id = state.company_id
        self.active_workflow = state.active_workflow
        self.complete = state.complete
        self.actor_profile = dict(state.actor_profile)
        self.draft_data = dict(state.draft_data)
        self.referenced_order_ids = list(state.referenced_order_ids)
        self.referenced_load_ids = list(state.referenced_load_ids)
        self.known_site_ids = list(state.known_site_ids)
        self.last_broker_alert_at = state.last_broker_alert_at
        self.escalation_flags = list(state.escalation_flags)
        self.last_activity = time.time()


_sessions: dict[str, Session] = {}
_loaded = False
_lock = asyncio.Lock()
_cleanup_task: asyncio.Task[None] | None = None


def _session_timeout_seconds() -> int:
    return settings.session_timeout_minutes * 60


def _cleanup_interval_seconds() -> int:
    return settings.session_cleanup_interval_minutes * 60


def _store_path() -> Path:
    path = settings.session_store_path
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def _is_expired(session: Session, now: float | None = None) -> bool:
    current = now if now is not None else time.time()
    return (current - session.last_activity) > _session_timeout_seconds()


def _ensure_store_parent() -> None:
    _store_path().parent.mkdir(parents=True, exist_ok=True)


def _serialize_sessions() -> dict[str, Any]:
    return {phone: asdict(session) for phone, session in _sessions.items()}


def _save_sessions_to_disk() -> None:
    _ensure_store_parent()
    path = _store_path()
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(_serialize_sessions(), indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _load_sessions_from_disk() -> None:
    global _loaded
    if _loaded:
        return

    path = _store_path()
    if not path.exists():
        _loaded = True
        return

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _loaded = True
        return

    for phone, payload in data.items():
        if not isinstance(payload, dict):
            continue
        _sessions[phone] = Session(
            phone=phone,
            role=str(payload.get("role", "unknown")),
            user_tier=str(payload.get("user_tier", "known")),
            session_count=int(payload.get("session_count", 0)),
            actor_id=str(payload.get("actor_id", "")),
            contact_id=str(payload.get("contact_id", "")),
            company_id=str(payload.get("company_id", "")),
            active_workflow=payload.get("active_workflow"),
            complete=bool(payload.get("complete", False)),
            actor_profile=dict(payload.get("actor_profile", {})),
            draft_data=dict(payload.get("draft_data", {})),
            referenced_order_ids=list(payload.get("referenced_order_ids", [])),
            referenced_load_ids=list(payload.get("referenced_load_ids", [])),
            known_site_ids=list(payload.get("known_site_ids", [])),
            escalation_flags=list(payload.get("escalation_flags", [])),
            last_broker_alert_at=payload.get("last_broker_alert_at"),
            messages=list(payload.get("messages", [])),
            last_activity=float(payload.get("last_activity", time.time())),
        )
    _loaded = True


async def get_session(phone: str) -> Session | None:
    """Get active session for a phone, dropping it if expired."""
    async with _lock:
        _load_sessions_from_disk()
        session = _sessions.get(phone)
        if session is None:
            return None
        if _is_expired(session):
            _sessions.pop(phone, None)
            _save_sessions_to_disk()
            return None
        return session


def _compute_user_tier(session_count: int) -> str:
    """Derive warm/cold/known tier from lifetime session count."""
    if session_count == 0:
        return "cold"
    if session_count < 3:
        return "warm"
    return "known"


async def create_session(phone: str, role: str = "unknown") -> Session:
    """Create a new session, incrementing session_count from any prior expired session."""
    async with _lock:
        _load_sessions_from_disk()
        # Carry over session_count from a previously expired session if stored
        prior_count = 0
        prior = _sessions.get(phone)
        if prior is not None:
            prior_count = prior.session_count
        new_count = prior_count + 1
        session = Session(
            phone=phone,
            role=role,
            session_count=new_count,
            user_tier=_compute_user_tier(new_count),
        )
        _sessions[phone] = session
        _save_sessions_to_disk()
    return session


async def get_or_create(phone: str, role: str = "unknown") -> Session:
    """Return current active session or create a new one."""
    existing = await get_session(phone)
    if existing is not None:
        if existing.role == "unknown" and role != "unknown":
            existing.role = role
            async with _lock:
                _save_sessions_to_disk()
        return existing
    return await create_session(phone, role=role)


async def add_message(phone: str, role: str, content: str) -> Session:
    """Append message and keep only the latest N."""
    session = await get_or_create(phone)
    async with _lock:
        session.messages.append({"role": role, "content": content})
        session.last_activity = time.time()
        max_messages = max(1, settings.session_max_messages)
        if len(session.messages) > max_messages:
            session.messages = session.messages[-max_messages:]
        _save_sessions_to_disk()
    return session


async def update_session(phone: str, updates: dict[str, Any]) -> Session:
    """Merge updates into an existing session."""
    session = await get_or_create(phone)
    async with _lock:
        for key, value in updates.items():
            if hasattr(session, key):
                setattr(session, key, value)
        session.last_activity = time.time()
        _save_sessions_to_disk()
    return session


async def update_session_from_state(phone: str, state: SwineDeskState) -> Session:
    """Persist a mutated agent state back into session storage."""
    session = await get_or_create(phone, role=state.role)
    async with _lock:
        session.apply_state(state)
        _save_sessions_to_disk()
    return session


async def mark_broker_alert_sent(phone: str, timestamp_iso: str) -> Session:
    """Persist the time when a broker alert was last sent."""
    return await update_session(phone, {"last_broker_alert_at": timestamp_iso})


async def reset_session(phone: str) -> None:
    """Delete a session explicitly."""
    async with _lock:
        _load_sessions_from_disk()
        _sessions.pop(phone, None)
        _save_sessions_to_disk()


async def cleanup_expired_sessions_once() -> int:
    """Delete expired sessions and return deleted count."""
    now = time.time()
    async with _lock:
        _load_sessions_from_disk()
        expired = [phone for phone, session in _sessions.items() if _is_expired(session, now)]
        for phone in expired:
            _sessions.pop(phone, None)
        if expired:
            _save_sessions_to_disk()
        return len(expired)


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(_cleanup_interval_seconds())
        await cleanup_expired_sessions_once()


def start_cleanup_task() -> None:
    """Start background cleanup task once."""
    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.create_task(_cleanup_loop())
