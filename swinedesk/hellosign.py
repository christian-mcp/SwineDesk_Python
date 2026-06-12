"""Deal confirmation and purchase order PDFs for ELM Pork.

These documents are informational: ELM Pork fills the official ELM Pork PDF templates with
the deal details and emails them to each party as a PDF attachment for their records. No
signature is requested.

Each recipient gets the template that names ELM Pork LLC as their counterparty and shows
only their own details — the real other side of the trade is never revealed:

  * Buyer  -> Deal Confirmation (buyer version): ELM Pork is the seller.
  * Seller -> Deal Confirmation (seller version): ELM Pork is the buyer.
  * Seller -> Purchase Order: ELM Pork is the buyer. (Never sent to the buyer, because it
    carries the seller's details.)

We fill the templates by stamping a text overlay onto the original PDF (so the exact ELM
Pork branding/layout is preserved), then deliver the result as an email attachment.
"""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import date
from pathlib import Path
from typing import Any

from fpdf import FPDF
from pypdf import PdfReader, PdfWriter

from swinedesk.notifications import send_email_with_pdf

logger = logging.getLogger(__name__)

_TEMPLATES = Path(__file__).resolve().parent / "pdf_templates"
_PAGE_W, _PAGE_H = 595.5, 842.25  # template page size in points (A4)

# Baseline offset: mutool reports a label's top edge (y0); a value typeset next to it sits
# on the baseline ~1pt lower than the label's top edge as fpdf2 positions from the top.
_BL = 1.0


def _ascii(text: Any) -> str:
    """The core Helvetica font is Latin-1 only; swap the few unicode chars we emit."""
    return (
        str(text)
        .replace("—", "-").replace("–", "-")
        .replace("’", "'").replace("‘", "'")
        .replace("“", '"').replace("”", '"')
        .replace("•", "-")
    )


class _Overlay:
    """Collects text placements and stamps them onto a template PDF."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def text(self, x: float, y0: float, value: Any, *, size: float = 10,
             style: str = "", white: bool = False) -> None:
        if value in (None, ""):
            return
        self.items.append({"x": x, "y": y0 + _BL, "t": _ascii(value),
                           "size": size, "style": style, "white": white})

    def centered(self, cx: float, y0: float, value: Any, *, size: float = 10,
                 white: bool = False) -> None:
        if value in (None, ""):
            return
        self.items.append({"cx": cx, "y": y0 + _BL, "t": _ascii(value),
                           "size": size, "style": "", "white": white})

    def render_onto(self, template: str) -> bytes:
        pdf = FPDF(unit="pt", format=(_PAGE_W, _PAGE_H))
        pdf.set_auto_page_break(False)
        pdf.add_page()
        for it in self.items:
            pdf.set_font("Helvetica", it["style"], it["size"])
            pdf.set_text_color(255, 255, 255) if it["white"] else pdf.set_text_color(20, 20, 20)
            if "cx" in it:
                w = pdf.get_string_width(it["t"])
                pdf.text(it["cx"] - w / 2, it["y"], it["t"])
            else:
                pdf.text(it["x"], it["y"], it["t"])
        overlay = PdfReader(io.BytesIO(bytes(pdf.output())))

        base = PdfReader(str(_TEMPLATES / template))
        page = base.pages[0]
        page.merge_page(overlay.pages[0])
        writer = PdfWriter()
        writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()


# --- Field coordinates, taken from the templates (label top-edge y, value x) ----------

def _fill_details(ov: _Overlay, x: float, ys: dict[str, float], *,
                  company: str, name: str, address: str, tel: str, email: str) -> None:
    ov.text(x, ys["company"], company)
    ov.text(x, ys["name"], name)
    ov.text(x, ys["address"], address)
    ov.text(x, ys["tel"], tel)
    ov.text(x, ys["email"], email)


def _build_deal_confirmation(
    *, version: str, reference: str, head: Any, market: str,
    company: str, name: str, phone: str, email: str, address: str = "",
) -> bytes:
    ov = _Overlay()
    # Header band (white text)
    ov.text(132, 148.5, reference, size=10, style="B", white=True)
    ov.text(354, 148.5, f"{date.today():%d %b %Y}", size=10, style="B", white=True)

    if version == "buyer":
        template = "deal_confirmation_buyer.pdf"
        box_x, box_ys = 379, {"company": 240.3, "name": 259.0, "address": 276.8,
                              "tel": 295.0, "email": 313.7}
    else:
        template = "deal_confirmation_seller.pdf"
        box_x, box_ys = 91, {"company": 240.2, "name": 259.0, "address": 276.9,
                             "tel": 295.1, "email": 313.8}
    _fill_details(ov, box_x, box_ys, company=company, name=name,
                  address=address, tel=phone, email=email)

    # Head count / product in the (otherwise blank) delivery schedule area, dropped a
    # line below the header for breathing room. Product name kept as supplied (e.g.
    # "weaned pigs", "piglets") rather than title-cased.
    if head or market:
        ov.text(29.2, 486.0, f"{head} head of {market}")
    return ov.render_onto(template)


# Purchase order metric table: row label top-edge y, and column x-centres.
_PO_QTY_CX, _PO_RATE_CX, _PO_AMT_CX = 342.0, 431.5, 523.0
_PO_ROWS = {
    "final_head": 702.0,
    "weight_slide": 723.0,
    "trucking": 745.4,
    "total": 767.5,
}


def _build_purchase_order(
    *, reference: str, head_final: Any, freight_cost: Any,
    weight_slide_count: Any, weight_slide_discount: Any,
    company: str, name: str, phone: str, email: str, address: str = "",
) -> bytes:
    ov = _Overlay()
    ov.text(94, 113.6, reference, size=10, style="B", white=True)  # LOAD NO. (header band)

    # Seller's details box (left); ELM Pork is pre-printed as the buyer.
    _fill_details(ov, 90, {"company": 187.2, "name": 206.0, "address": 224.0,
                           "tel": 242.1, "email": 260.8},
                  company=company, name=name, address=address, tel=phone, email=email)

    # Metric table — fill the cells we have data for; the rest stay blank as on the form.
    ov.centered(_PO_QTY_CX, _PO_ROWS["final_head"], head_final)
    if weight_slide_count:
        ov.centered(_PO_QTY_CX, _PO_ROWS["weight_slide"], weight_slide_count)
    if weight_slide_discount:
        ov.centered(_PO_RATE_CX, _PO_ROWS["weight_slide"], f"${weight_slide_discount}/lb")
    if freight_cost:
        ov.centered(_PO_AMT_CX, _PO_ROWS["trucking"], f"${freight_cost}")
        ov.centered(_PO_AMT_CX, _PO_ROWS["total"], f"${freight_cost}")
    return ov.render_onto("purchase_order.pdf")


# Grade sheet reject rows we can map unambiguously from the grading tool fields, into the
# TOTAL REJECTS # column (centre x). Belly/scrotal split and the SUMMARY totals are left
# for Brian to confirm. Row = label top-edge y per metric.
_GS_TOTAL_REJECTS_CX = 483.0
_GS_ROWS = {
    "underweight": 353.2,       # "Less than 8 #"
    "unthrifty": 373.9,
    "ruptures": 394.1,          # "Belly ruptures" (belly/scrotal split unconfirmed)
    "navel_infections": 434.6,
    "doa": 616.9,
    "dead_within_12hrs": 637.1,
}


def _build_grade_sheet(
    *, load_id: str, grader: str, buyer_company: str, buyer_name: str,
    buyer_phone: str, buyer_email: str, writeoffs: dict[str, Any],
) -> bytes:
    ov = _Overlay()
    ov.text(94, 116.4, load_id, size=10, style="B", white=True)   # LOAD NO. (header band)
    ov.text(372, 116.4, grader, size=10, style="B", white=True)   # GRADER  (header band)

    # Buyer's details box (right); ELM Pork is pre-printed as the seller.
    _fill_details(ov, 383, {"company": 187.5, "name": 206.2, "address": 224.1,
                            "tel": 242.2, "email": 260.7},
                  company=buyer_company, name=buyer_name, address="",
                  tel=buyer_phone, email=buyer_email)

    # Write-offs into the TOTAL REJECTS # column for the rows we can map.
    for key, y in _GS_ROWS.items():
        val = writeoffs.get(key)
        if val and str(val).strip() not in {"0", "0.0", "None"}:
            ov.centered(_GS_TOTAL_REJECTS_CX, y, val)
    return ov.render_onto("grade_sheet.pdf")


# --- Email copy ------------------------------------------------------------------------

def _greeting(name: str) -> str:
    first = (name or "").strip()
    return f"Hi {first}," if first else "Hi,"


# Standard deal terms, shown on every confirmation.
_DEAL_TERMS = (
    "Terms\n\n"
    "    Count, grade, and turn in the load closeout within 48 hours (business) from receiving\n"
    "    Pay invoice within 72 hours (calendar) after receipt of invoice\n"
    "    Please pay the invoice via ACH - We cannot accept a check\n"
    "    In the event the count is off more than 1%, please notify us ASAP, and prepare for recount\n"
    "    In the event the grade is greater than 1%, please notify us ASAP, and do not destroy the NV animals"
)


def _deal_confirmation_subject(side_word: str, head: Any, market: str,
                               price: str, order_id: str) -> str:
    price_part = f" @ {price}/pig" if price else ""
    return (
        f"Deal Confirmation (Vet Check Approved): {head} {market} {side_word}"
        f"{price_part} (ID: {order_id})"
    )


def _deal_confirmation_body(*, name: str, order_id: str, head: Any, market: str,
                            terms: dict[str, Any] | None = None) -> str:
    """Render the deal-confirmation email. Optional deal-term fields are omitted when
    not supplied so the email degrades gracefully until the backend provides them."""
    t = terms or {}
    confirm = "After passing the vet-to-vet check we are writing to confirm our deal"
    if t.get("deal_date"):
        confirm += f" on {t['deal_date']}"
    confirm += ":"

    core = [f"Order ID: {order_id}", f"Headcount: {head}", f"Type: {market}"]
    if t.get("price"):
        core.append(f"Price: {t['price']}")
    if t.get("loads"):
        core.append(f"Loads: {t['loads']}")
        core += [f"- {line}" for line in t.get("load_lines", [])]

    spec = []
    if t.get("source_farms"):
        spec.append(f"Source farm(s): {t['source_farms']}")
    if t.get("health"):
        spec.append(f"Health: {t['health']}")
    if t.get("vaccine"):
        spec.append(f"Vaccine: {t['vaccine']}")
    if t.get("weight_slide"):
        spec.append(f"Weight slide: {t['weight_slide']}")
    if t.get("regrade"):
        spec.append(f"Re-grade: {t['regrade']}")

    parts = [_greeting(name), "", confirm, "", *core]
    if spec:
        parts += ["", *spec]
    parts += [
        "",
        "Additionally, you can find a copy of the deal confirmation attached to this email.",
        "",
        _DEAL_TERMS,
    ]
    return "\n".join(parts)


def _purchase_order_body(name: str, load_id: str) -> str:
    return (
        f"{_greeting(name)}\n\n"
        f"Please find attached your purchase order from ELM Pork LLC for load {load_id}.\n\n"
        "This document is for your records - no signature is required. If anything looks "
        "off, just reply to this email or call Brian McCorkle on +1 (910) 228 6179.\n\n"
        "Thanks,\nELM Pork LLC"
    )


def _collect(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {"success": False, "error": "No recipient emails available."}
    sent = [r for r in results if r.get("success")]
    return {
        "success": bool(sent) and all(r.get("success") for r in results),
        "sent": [r["to"] for r in sent],
        "results": results,
    }


async def send_deal_confirmation(
    order_id: str,
    head: Any,
    market: str,
    seller_name: str,
    seller_email: str,
    buyer_name: str,
    buyer_email: str,
    seller_company: str = "",
    seller_phone: str = "",
    buyer_company: str = "",
    buyer_phone: str = "",
    terms: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Email each party their own deal confirmation PDF (no signature).

    ``terms`` carries optional deal-term fields for the email body (price, loads,
    load_lines, source_farms, health, vaccine, weight_slide, regrade, deal_date); any
    that are missing are simply left off.
    """
    terms = terms or {}
    price = str(terms.get("price") or "")
    filename = f"deal_confirmation_{order_id}.pdf"
    results: list[dict[str, Any]] = []

    if buyer_email:
        # The buyer must not see the seller's source farms.
        buyer_terms = {k: v for k, v in terms.items() if k != "source_farms"}
        pdf = await asyncio.to_thread(
            _build_deal_confirmation, version="buyer", reference=order_id, head=head,
            market=market, company=buyer_company, name=buyer_name, phone=buyer_phone,
            email=buyer_email,
        )
        results.append(await send_email_with_pdf(
            buyer_email,
            _deal_confirmation_subject("Purchased", head, market, price, order_id),
            _deal_confirmation_body(name=buyer_name, order_id=order_id, head=head,
                                    market=market, terms=buyer_terms),
            pdf, filename,
        ))
    if seller_email:
        pdf = await asyncio.to_thread(
            _build_deal_confirmation, version="seller", reference=order_id, head=head,
            market=market, company=seller_company, name=seller_name, phone=seller_phone,
            email=seller_email,
        )
        results.append(await send_email_with_pdf(
            seller_email,
            _deal_confirmation_subject("Sold", head, market, price, order_id),
            _deal_confirmation_body(name=seller_name, order_id=order_id, head=head,
                                    market=market, terms=terms),
            pdf, filename,
        ))
    return _collect(results)


async def send_purchase_order(
    load_id: str,
    head_final: Any,
    market: str,
    freight_cost: Any,
    weight_slide_count: Any,
    weight_slide_discount: Any,
    comments: str,
    buyer_name: str,
    buyer_email: str,
    seller_name: str,
    seller_email: str,
    seller_company: str = "",
    seller_phone: str = "",
    buyer_company: str = "",
    buyer_phone: str = "",
) -> dict[str, Any]:
    """Email the purchase order PDF to the seller only (it carries the seller's details
    and names ELM Pork as the buyer, so the buyer must never receive it)."""
    if not seller_email:
        return {"success": False, "error": "No seller email for purchase order."}

    pdf = await asyncio.to_thread(
        _build_purchase_order, reference=load_id, head_final=head_final,
        freight_cost=freight_cost, weight_slide_count=weight_slide_count,
        weight_slide_discount=weight_slide_discount, company=seller_company,
        name=seller_name, phone=seller_phone, email=seller_email,
    )
    result = await send_email_with_pdf(
        seller_email,
        f"Purchase Order - {load_id} | ELM Pork LLC",
        _purchase_order_body(seller_name, load_id),
        pdf,
        f"purchase_order_{load_id}.pdf",
    )
    return _collect([result])


def _grade_sheet_body(*, name: str, grader: str, head: Any, market: str,
                      site: str, date: str, load_id: str) -> str:
    pig_type = market or "pigs"
    to_site = f" delivered to {site}" if site else " delivered"
    on_date = f" on {date}" if date else ""
    return (
        f"Hi {(name or '').strip() or 'there'},\n\n"
        f"The grade sheet form has just been completed by {grader or 'the buyer'} for the "
        f"{head} {pig_type}{to_site}{on_date} (load ID: {load_id}).\n\n"
        "You can find a copy of this attached for your reference. We will be reviewing and "
        "sending an invoice shortly.\n\n"
        "If you have any issues or questions, please don't hesitate to reach out to:\n\n"
        "Craig: (712) 830-9730 or craig@elmpork.com\n"
        "Logistics: logistics@elmpork.com\n\n"
        "Many thanks,\n\n"
        "Brian,\n(910) 228-6179"
    )


async def send_grade_sheet(
    load_id: str,
    head_count_received: Any,
    grader_name: str,
    grading_date: str,
    market: str,
    buyer_name: str,
    buyer_email: str,
    buyer_company: str = "",
    buyer_phone: str = "",
    site: str = "",
    writeoffs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Email the buyer the grade-sheet confirmation with the filled grade-sheet PDF."""
    if not buyer_email:
        return {"success": False, "error": "No buyer email for grade sheet."}

    pdf = await asyncio.to_thread(
        _build_grade_sheet, load_id=load_id, grader=grader_name,
        buyer_company=buyer_company, buyer_name=buyer_name, buyer_phone=buyer_phone,
        buyer_email=buyer_email, writeoffs=writeoffs or {},
    )
    result = await send_email_with_pdf(
        buyer_email,
        "Grade Sheet Confirmation",
        _grade_sheet_body(name=buyer_name, grader=grader_name, head=head_count_received,
                          market=market, site=site, date=grading_date, load_id=load_id),
        pdf,
        f"grade_sheet_{load_id}.pdf",
    )
    return _collect([result])
