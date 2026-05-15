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
            return await self.post("/v1/orders/buy", payload)

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
        return await self.get("/v1/sms/market/open-requests", params=params)

    async def get_my_request_detail(self, actor_id: str, request_id: str) -> dict[str, Any]:
        return await self.get(f"/v1/sms/market/requests/{request_id}", params={"actorId": actor_id})

    async def get_order_status(self, order_id: str) -> dict[str, Any]:
        """Backward-compatible direct order lookup for legacy tools."""
        return await self.get(f"/v1/orders/{order_id}")

    async def resolve_site(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post("/v1/sms/sites/resolve", payload)

    async def create_site_for_known_actor(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post("/v1/sms/sites", payload)

    async def list_my_loads(self, actor_id: str, role: str, payload: dict[str, Any]) -> dict[str, Any]:
        params = {"actorId": actor_id, "role": role, **payload}
        return await self.get("/v1/sms/loads", params=params)

    async def get_my_load_detail(self, actor_id: str, role: str, load_id: str) -> dict[str, Any]:
        return await self.get(f"/v1/sms/loads/{load_id}", params={"actorId": actor_id, "role": role})

    async def get_driver_details(self, actor_id: str, role: str, load_id: str) -> dict[str, Any]:
        return await self.get(
            f"/v1/sms/loads/{load_id}/driver",
            params={"actorId": actor_id, "role": role},
        )

    async def get_freight_loads(self, actor_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.get("/v1/sms/freight/loads", params={"actorId": actor_id, **payload})

    async def get_freight_load_detail(self, actor_id: str, load_id: str) -> dict[str, Any]:
        return await self.get(f"/v1/sms/freight/loads/{load_id}", params={"actorId": actor_id})

    async def confirm_freight_assignment(self, actor_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post("/v1/sms/freight/confirm", {"actorId": actor_id, **payload})

    async def submit_freight_details(self, actor_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post("/v1/sms/freight/details", {"actorId": actor_id, **payload})

    async def get_health_cert_status(self, actor_id: str, role: str, load_id: str) -> dict[str, Any]:
        return await self.get(
            f"/v1/sms/health/loads/{load_id}",
            params={"actorId": actor_id, "role": role},
        )

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
        return await self.get(f"/v1/sms/vets/{actor_id}/pending-health-certs")

    async def submit_grading(self, actor_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post("/v1/sms/grading", {"actorId": actor_id, **payload})

    async def get_grading_submission_status(self, actor_id: str, load_id: str) -> dict[str, Any]:
        return await self.get(f"/v1/sms/grading/{load_id}", params={"actorId": actor_id})

    async def report_delivery_issue(self, actor_id: str, role: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post("/v1/sms/issues", {"actorId": actor_id, "role": role, **payload})

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


_backend_client: BackendClient | None = None


def get_backend_client() -> BackendClient:
    global _backend_client
    if _backend_client is None:
        _backend_client = BackendClient()
    return _backend_client
