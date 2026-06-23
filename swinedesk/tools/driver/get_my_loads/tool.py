"""Tool: list the loads assigned to the driver."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role, remember_load
from swinedesk.tooling import Arg, Tool


class GetMyLoads(Tool, name="get_my_loads"):
    TOOL_PATH = "/tools/driver/get_my_loads"
    DESCRIPTION = (
        "Driver-only. List the loads this driver is assigned to, with pickup and drop-off "
        "site, city, state, date, and head count. Use for 'what've I got this week' or "
        "'where am I tomorrow'."
    )
    ARGUMENTS = {
        "window": Arg("today, tomorrow, week, or all. Defaults to all.", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"driver"})
        if role_error:
            return role_error

        phone = str(getattr(state, "phone", "") or "").strip()
        if not phone:
            return {"error": "I can't tell which driver this is from your number."}

        window = str(arguments.get("window", "")).strip().lower()
        backend = get_backend_client()
        response = await backend.get_driver_loads(phone, window)
        for load_id in response.get("load_ids", []):
            remember_load(state, str(load_id))
        return {
            "result": "Loaded driver assignments.",
            **response,
        }
