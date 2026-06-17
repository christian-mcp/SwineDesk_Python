"""Tool: submit delivery grading."""

from __future__ import annotations

import logging
from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.hellosign import send_grade_sheet
from swinedesk.tool_helpers import (
    clear_workflow_draft,
    ensure_role,
    merge_workflow_draft,
    remember_load,
    require_actor_id,
)
from swinedesk.tooling import Arg, Tool

logger = logging.getLogger(__name__)

GRADING_DRAFT_KEYS = {
    "load_id",
    "grading_stage",
    "grading_date",
    "grader_name",
    "head_count_received",
    "underweight",
    "unthrifty",
    "ruptures",
    "scrotal_ruptures",
    "navel_infections",
    "greasy_pigs",
    "humpback",
    "swollen_joints",
    "abscesses",
    "severely_crippled",
    "swollen_ears",
    "bad_feet",
    "rail_backs",
    "doa",
    "dead_within_12hrs",
    "boars",
    "comments",
}


class SubmitGrading(Tool, name="submit_grading"):
    TOOL_PATH = "/tools/grading/submit_grading"
    DESCRIPTION = "Submit grading results after a buyer receives a load."
    ARGUMENTS = {
        "load_id": Arg("Load identifier"),
        "grading_date": Arg("Date grading was completed", optional=True),
        "grader_name": Arg("Person who completed grading", optional=True),
        "head_count_received": Arg("Head count received"),
        "underweight": Arg("Underweight write-offs", optional=True),
        "unthrifty": Arg("Unthrifty write-offs", optional=True),
        "ruptures": Arg("Ruptures write-offs", optional=True),
        "scrotal_ruptures": Arg("Scrotal ruptures write-offs", optional=True),
        "navel_infections": Arg("Navel infection write-offs", optional=True),
        "greasy_pigs": Arg("Greasy pigs write-offs", optional=True),
        "humpback": Arg("Humpback write-offs", optional=True),
        "swollen_joints": Arg("Swollen joints write-offs", optional=True),
        "abscesses": Arg("Abscesses write-offs", optional=True),
        "severely_crippled": Arg("Severely crippled write-offs", optional=True),
        "swollen_ears": Arg("Swollen ears write-offs", optional=True),
        "bad_feet": Arg("Bad feet write-offs", optional=True),
        "rail_backs": Arg("Rail backs write-offs", optional=True),
        "doa": Arg("Dead on arrival write-offs", optional=True),
        "dead_within_12hrs": Arg("Dead within 12 hours write-offs", optional=True),
        "boars": Arg("Boars write-offs", optional=True),
        "comments": Arg("Additional comments", optional=True),
        "photo_urls": Arg("List of photo URLs", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"buyer"})
        if role_error:
            return role_error

        actor_id, error = require_actor_id(state)
        if error:
            return error

        load_id = str(arguments.get("load_id", "")).strip()
        if not load_id:
            return {"error": "load_id is required."}

        merge_workflow_draft(state, "submit_grading", arguments)
        backend = get_backend_client()
        response = await backend.submit_grading(actor_id, arguments)
        remember_load(state, load_id)

        writeoff_fields = (
            "underweight",
            "unthrifty",
            "ruptures",
            "scrotal_ruptures",
            "navel_infections",
            "greasy_pigs",
            "humpback",
            "swollen_joints",
            "abscesses",
            "severely_crippled",
            "swollen_ears",
            "bad_feet",
            "rail_backs",
            "doa",
            "dead_within_12hrs",
            "boars",
        )
        for field in writeoff_fields:
            value = arguments.get(field, 0)
            if str(value).strip() not in {"", "0", "0.0", "None"}:
                if state is not None:
                    state.add_escalation_flag(f"grading-review:{load_id}")
                break

        try:
            profile = await backend.get_actor_profile(actor_id, "buyer")
            buyer_email = str(profile.get("email") or "").strip()
            if buyer_email:
                logger.info("Building grade sheet PDF for load %s to %s", load_id, buyer_email)
                gs = await send_grade_sheet(
                    load_id=load_id,
                    head_count_received=arguments.get("head_count_received", "?"),
                    grader_name=str(arguments.get("grader_name") or ""),
                    grading_date=str(arguments.get("grading_date") or ""),
                    market=str(response.get("market") or profile.get("market") or ""),
                    buyer_name=str(profile.get("first_name") or profile.get("name") or ""),
                    buyer_email=buyer_email,
                    buyer_company=str((profile.get("company") or {}).get("name") or ""),
                    buyer_phone=str(profile.get("phone") or ""),
                    site=str(response.get("site_name") or response.get("destination") or ""),
                    head_shipped=response.get("load_quantity") or response.get("quantity") or "",
                    comments=str(arguments.get("comments") or ""),
                    writeoffs=arguments,
                )
                if not gs.get("success"):
                    logger.warning("Grade sheet email failed for load %s: %s", load_id, gs.get("error"))
                else:
                    logger.info("Grade sheet email sent for load %s to %s", load_id, buyer_email)
            else:
                logger.warning("Skipping grade sheet email for load %s: buyer email missing", load_id)
        except Exception:
            logger.exception("Failed to send grade sheet email for load %s", load_id)

        clear_workflow_draft(state, keys=GRADING_DRAFT_KEYS)

        return {
            "result": response.get("msg", f"Submitted grading for {load_id}."),
            **response,
        }
