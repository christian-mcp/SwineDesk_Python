"""HTTP client wrapper for the external backend API."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from swinedesk.notifications import send_sms_notification
from swinedesk.settings import settings

logger = logging.getLogger(__name__)


ROLE_ALIASES = {
    "seller": "seller",
    "buyer": "buyer",
    "freight": "freight_operator",
    "freight_operator": "freight_operator",
    "vet": "vet",
    "broker": "broker",
    "unknown": "unknown",
    "producer": "seller",
}


class BackendClient:
    """Thin async HTTP client with auth and timeout defaults."""

    def __init__(self) -> None:
        base_url = settings.backend_api_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=settings.backend_timeout_seconds,
            headers=self._base_headers(),
        )

    def _base_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if settings.backend_api_token:
            headers["Authorization"] = f"Bearer {settings.backend_api_token}"
        return headers

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    async def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._client.post(path, json=payload or {})
        response.raise_for_status()
        return response.json()

    async def patch(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._client.patch(path, json=payload or {})
        response.raise_for_status()
        return response.json()

    async def put(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._client.put(path, json=payload or {})
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()

    def _normalize_role(self, raw_role: str | None) -> str:
        return ROLE_ALIASES.get((raw_role or "unknown").lower().strip(), "unknown")

    def _to_legacy_sell_order_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Translate SMS listing fields into the legacy /v1/orders/sell contract."""
        legacy: dict[str, Any] = {
            "phone": payload.get("phone", ""),
            "market": payload.get("market", ""),
            "headCount": payload.get("head_count"),
            "health": payload.get("health", ""),
            "pricePerHead": payload.get("price_target"),
            "weightRange": payload.get("weight_range"),
            "shipDate": payload.get("first_ship_date"),
        }
        return {key: value for key, value in legacy.items() if value not in (None, "")}

    def _to_legacy_buy_order_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Translate SMS buy-request fields into the /v1/orders/buy contract."""
        legacy: dict[str, Any] = {
            "phone": payload.get("phone", ""),
            "market": payload.get("market", ""),
            "headCount": payload.get("head_count_needed") or payload.get("head_count"),
            "health": payload.get("health_requirement") or payload.get("health", ""),
            "pricePerHead": payload.get("budget_target") or payload.get("price_per_head") or payload.get("pricePerHead"),
            "weightRange": payload.get("weight_range"),
            "deliveryDate": payload.get("delivery_start_date") or payload.get("delivery_date") or payload.get("deliveryDate"),
        }
        return {key: value for key, value in legacy.items() if value not in (None, "")}

    async def resolve_actor_by_phone(self, phone: str) -> dict[str, Any]:
        """Resolve actor role and profile context for an inbound phone number."""
        logger.info("Resolving actor by phone via primary SMS endpoint: phone=%s", phone)
        try:
            data = await self.get("/v1/sms/actors/resolve", params={"phone": phone})
        except httpx.HTTPError as exc:
            logger.warning(
                "Primary SMS actor lookup failed for phone=%s path=%s error=%s",
                phone,
                "/v1/sms/actors/resolve",
                repr(exc),
            )
            try:
                logger.info("Falling back to legacy role lookup endpoint: phone=%s", phone)
                legacy = await self.get("/v1/roles/resolve", params={"phone": phone})
            except httpx.HTTPError as legacy_exc:
                logger.error(
                    "Legacy role lookup failed for phone=%s path=%s error=%s",
                    phone,
                    "/v1/roles/resolve",
                    repr(legacy_exc),
                )
                return {"role": "unknown", "phone": phone}
            logger.info("Legacy role lookup succeeded for phone=%s payload=%s", phone, legacy)
            return {
                "role": self._normalize_role(str(legacy.get("role", "unknown"))),
                "phone": phone,
            }

        logger.info("Primary SMS actor lookup succeeded for phone=%s payload=%s", phone, data)
        data["role"] = self._normalize_role(str(data.get("role", "unknown")))
        data.setdefault("phone", phone)
        logger.info("Normalized actor lookup result for phone=%s role=%s", phone, data.get("role"))
        return data

    async def resolve_phone_role(self, phone: str) -> str:
        """Backward-compatible role lookup wrapper."""
        data = await self.resolve_actor_by_phone(phone)
        return str(data.get("role", "unknown"))

    async def get_actor_profile(self, actor_id: str, role: str) -> dict[str, Any]:
        return await self.get(f"/v1/sms/actors/{actor_id}", params={"role": role})

    async def create_sell_listing(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self.post("/v1/sms/market/sell-listings", payload)
        except httpx.HTTPError:
            # Backward-compatible fallback to the existing SwineDesk route.
            legacy_payload = self._to_legacy_sell_order_payload(payload)
            logger.info("Falling back to legacy sell order payload=%s", legacy_payload)
            return await self.post("/v1/orders/sell", legacy_payload)

    async def create_buy_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self.post("/v1/sms/market/buy-requests", payload)
        except httpx.HTTPError:
            legacy_payload = self._to_legacy_buy_order_payload(payload)
            logger.info("Falling back to legacy buy order payload=%s", legacy_payload)
            return await self.post("/v1/orders/buy", legacy_payload)

    async def create_sell_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Backward-compatible wrapper for the older tool name."""
        return await self.create_sell_listing(payload)

    async def create_buy_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Backward-compatible wrapper for the older tool name."""
        return await self.create_buy_request(payload)

    async def get_my_open_requests(
        self, actor_id: str, role: str, filters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        params = {"actorId": actor_id, "role": role, **(filters or {})}
        try:
            return await self.get("/v1/sms/market/open-requests", params=params)
        except httpx.HTTPError:
            return {"requests": [], "loads": []}

    async def get_my_request_detail(self, actor_id: str, request_id: str) -> dict[str, Any]:
        try:
            return await self.get(f"/v1/sms/market/requests/{request_id}", params={"actorId": actor_id})
        except httpx.HTTPError:
            return {"msg": "Request detail unavailable.", "request_id": request_id}

    async def get_order_status(self, order_id: str) -> dict[str, Any]:
        """Backward-compatible direct order lookup for legacy tools."""
        return await self.get(f"/v1/orders/{order_id}")

    async def resolve_site(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post("/v1/sms/sites/resolve", payload)

    async def create_site_for_known_actor(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post("/v1/sms/sites", payload)

    async def list_my_loads(self, actor_id: str, role: str, payload: dict[str, Any]) -> dict[str, Any]:
        params = {"actorId": actor_id, "role": role, **payload}
        try:
            return await self.get("/v1/sms/loads", params=params)
        except httpx.HTTPError:
            return {"loads": []}

    async def get_my_load_detail(self, actor_id: str, role: str, load_id: str) -> dict[str, Any]:
        try:
            return await self.get(f"/v1/sms/loads/{load_id}", params={"actorId": actor_id, "role": role})
        except httpx.HTTPError:
            return {"msg": "Load detail unavailable.", "load_id": load_id}

    async def get_driver_details(self, actor_id: str, role: str, load_id: str) -> dict[str, Any]:
        try:
            return await self.get(
                f"/v1/sms/loads/{load_id}/driver",
                params={"actorId": actor_id, "role": role},
            )
        except httpx.HTTPError:
            return {"msg": "Driver details unavailable.", "load_id": load_id}

    async def get_freight_loads(self, actor_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self.get("/v1/sms/freight/loads", params={"actorId": actor_id, **payload})
        except httpx.HTTPError:
            return {"loads": []}

    async def get_freight_load_detail(self, actor_id: str, load_id: str) -> dict[str, Any]:
        try:
            return await self.get(f"/v1/sms/freight/loads/{load_id}", params={"actorId": actor_id})
        except httpx.HTTPError:
            return {"msg": "Freight load detail unavailable.", "load_id": load_id}

    async def confirm_freight_assignment(self, actor_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self.post("/v1/sms/freight/confirm", {"actorId": actor_id, **payload})
        except httpx.HTTPError:
            return {"success": False, "msg": "Freight confirmation unavailable."}

    async def submit_freight_details(self, actor_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self.post("/v1/sms/freight/details", {"actorId": actor_id, **payload})
        except httpx.HTTPError:
            return {"success": False, "msg": "Freight details submission unavailable."}

    async def get_health_cert_status(self, actor_id: str, role: str, load_id: str) -> dict[str, Any]:
        try:
            return await self.get(
                f"/v1/sms/health/loads/{load_id}",
                params={"actorId": actor_id, "role": role},
            )
        except httpx.HTTPError:
            return {"msg": "Health cert status unavailable.", "load_id": load_id}

    async def mark_health_cert_received(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self.post("/v1/sms/health/received", payload)
        except httpx.HTTPError:
            load_id = str(payload.get("load_id", ""))
            fallback = {
                "from_email": payload.get("from_email"),
                "attachment_url": payload.get("attachment_url"),
            }
            return await self.patch(f"/v1/loads/{load_id}/health-cert", fallback)

    async def get_vet_pending_loads(self, actor_id: str) -> dict[str, Any]:
        try:
            return await self.get(f"/v1/sms/vets/{actor_id}/pending-health-certs")
        except httpx.HTTPError:
            return {"load_ids": [], "loads": []}

    async def submit_grading(self, actor_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self.post("/v1/sms/grading", {"actorId": actor_id, **payload})
        except httpx.HTTPError:
            return {"success": False, "msg": "Grading submission unavailable."}

    async def get_grading_submission_status(self, actor_id: str, load_id: str) -> dict[str, Any]:
        try:
            return await self.get(f"/v1/sms/grading/{load_id}", params={"actorId": actor_id})
        except httpx.HTTPError:
            return {"msg": "Grading status unavailable.", "load_id": load_id}

    async def report_delivery_issue(self, actor_id: str, role: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self.post("/v1/sms/issues", {"actorId": actor_id, "role": role, **payload})
        except httpx.HTTPError:
            return {"success": False, "msg": "Issue reporting unavailable."}

    async def create_unknown_contact_attempt(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self.post("/v1/sms/unknown-contacts", payload)
        except httpx.HTTPError:
            return {"success": False, "queued_locally": True, **payload}

    async def notify_assigned_broker(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self.post("/v1/sms/ops/notify-broker", payload)
        except httpx.HTTPError:
            broker_phone = str(payload.get("broker_phone") or settings.effective_broker_alert_phone)
            message = str(payload.get("message", "")).strip()
            if not broker_phone or not message:
                return {"success": False, "error": "Missing broker phone or message."}
            return await send_sms_notification(broker_phone, message)

    async def send_role_notification(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self.post("/v1/sms/ops/notify-role", payload)
        except httpx.HTTPError:
            to_phone = str(payload.get("to_phone", ""))
            message = str(payload.get("message", ""))
            return await send_sms_notification(to_phone, message)

    async def create_reminder(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self.post("/v1/sms/reminders", payload)
        except httpx.HTTPError:
            return {"success": False, "msg": "Reminder creation unavailable."}

    async def list_reminders(self, phone: str) -> dict[str, Any]:
        try:
            return await self.get("/v1/sms/reminders", params={"phone": phone})
        except httpx.HTTPError:
            return {"reminders": []}

    async def create_note(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self.post("/v1/query/notes", payload)
        except httpx.HTTPError:
            return {"success": False, "msg": "Note creation unavailable."}

    async def get_user_history(self, phone: str) -> dict[str, Any]:
        try:
            return await self.get("/v1/query/user-history", params={"phone": phone})
        except httpx.HTTPError:
            return {"notes": [], "orders": [], "message_count": 0}

    async def get_pending_tasks(self, phone: str) -> dict[str, Any]:
        try:
            data = await self.get("/v1/query/pending-tasks", params={"phone": phone})
        except httpx.HTTPError:
            return {"tasks": []}
        # Backend returns a bare JSON array of tasks; normalize to a dict.
        if isinstance(data, list):
            return {"tasks": data}
        return data

    async def get_open_market(self) -> dict[str, Any]:
        try:
            return await self.get("/v1/query/open-market")
        except httpx.HTTPError:
            return {"supply": [], "demand": [], "supply_count": 0, "demand_count": 0}

    async def get_daily_recap(self) -> dict[str, Any]:
        try:
            return await self.get("/v1/query/recap")
        except httpx.HTTPError:
            return {"new_listings": 0, "new_requests": 0, "items": []}

    async def complete_load(self, load_id: str) -> dict[str, Any]:
        return await self.post(f"/v1/query/loads/{load_id}/complete")

    async def record_purchase_order(self, load_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post(f"/v1/query/loads/{load_id}/purchase-order", payload)

    async def list_contacts(self, role: str | None = None, state: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if role:
            params["role"] = role
        if state:
            params["state"] = state
        try:
            return await self.get("/v1/query/contacts", params=params)
        except httpx.HTTPError:
            return {"count": 0, "contacts": []}

    async def start_auction(self, short_id: str, duration_hours: int) -> dict[str, Any]:
        return await self.post(
            f"/v1/query/orders/{short_id}/start-auction",
            {"duration_hours": duration_hours},
        )

    async def submit_bid(
        self, short_id: str, bid_price: float, buyer_phone: str
    ) -> dict[str, Any]:
        return await self.post(
            f"/v1/query/orders/{short_id}/bid",
            {"bid_price": bid_price, "buyer_phone": buyer_phone},
        )

    async def close_auction(self, short_id: str) -> dict[str, Any]:
        return await self.post(f"/v1/query/orders/{short_id}/close-auction")

    async def match_orders(
        self, buy_order_id: str, sell_order_id: str, regrade: str = ""
    ) -> dict[str, Any]:
        payload = {"buy_order_id": buy_order_id, "sell_order_id": sell_order_id}
        if regrade:
            payload["regrade"] = regrade
        return await self.post("/v1/query/match-orders", payload)

    async def reject_order(self, order_id: str, reason: str = "") -> dict[str, Any]:
        payload = {"order_id": order_id, "reason": reason}
        return await self.post("/v1/query/reject-order", payload)

    async def update_order_price(
        self, order_id: str, price: float, side: str = ""
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"price": price}
        if side:
            payload["side"] = side
        try:
            return await self.post(f"/v1/query/orders/{order_id}/price", payload)
        except httpx.HTTPError as exc:
            return {"success": False, "error": f"Price update unavailable: {exc}"}

    async def send_message_to_user(self, to_phone: str, message: str) -> dict[str, Any]:
        payload = {"to_phone": to_phone, "message": message}
        try:
            return await self.post("/v1/sms/ops/send-direct", payload)
        except httpx.HTTPError:
            return await send_sms_notification(to_phone, message)


_backend_client: BackendClient | None = None


def get_backend_client() -> BackendClient:
    global _backend_client
    if _backend_client is None:
        _backend_client = BackendClient()
    return _backend_client
