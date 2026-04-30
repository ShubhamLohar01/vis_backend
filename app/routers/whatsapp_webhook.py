"""
WhatsApp Webhook Router for handling Meta WhatsApp Business API callbacks.
Handles interactive button replies (Approve/Reject) from approvers.
"""
from fastapi import APIRouter, Request, HTTPException, Query, status, Depends
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy.orm import Session
from typing import Optional
import logging

from app.core.config import settings
from app.core.database import get_db
from app.models.visitor import Visitor, VisitorStatus
from app.models.approver import Approver
from app.services.whatsapp_service import whatsapp_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/whatsapp", tags=["WhatsApp Webhook"])


def _normalize_phone(phone: str) -> str:
    """Return last 10 digits for approver matching."""
    digits = ''.join(filter(str.isdigit, phone))
    return digits[-10:] if len(digits) >= 10 else digits


def _find_approver(db: Session, phone: str) -> Optional[Approver]:
    """Find approver by phone number using multiple matching strategies."""
    normalized = _normalize_phone(phone)

    # Strategy 1: LIKE match on last 10 digits
    approver = db.query(Approver).filter(
        Approver.ph_no.like(f"%{normalized}%")
    ).first()
    if approver:
        return approver

    # Strategy 2: Exact match
    approver = db.query(Approver).filter(Approver.ph_no == phone).first()
    if approver:
        return approver

    # Strategy 3: Normalized scan
    for a in db.query(Approver).all():
        if a.ph_no and _normalize_phone(a.ph_no) == normalized:
            return a

    return None


@router.get("/status", status_code=status.HTTP_200_OK)
async def whatsapp_status():
    """Return current WhatsApp service configuration status."""
    return {
        "enabled": whatsapp_service.enabled,
        "phone_number_id": settings.whatsapp_phone_number_id,
        "access_token_set": bool(settings.whatsapp_access_token),
        "api_url": settings.whatsapp_api_url,
    }


@router.post("/test/{phone_number}", status_code=status.HTTP_200_OK)
def test_whatsapp_templates(phone_number: str):
    """
    Send all WhatsApp templates to a phone number for testing.
    Returns per-template success/failure results.
    """
    from datetime import datetime

    now = datetime.now()
    visit_time = now.strftime("%I:%M %p")
    reference_no = now.strftime("%Y%m%d%H%M%S")

    results = {}

    # 1. Text message
    results["text_message"] = whatsapp_service.send_text_message(
        phone_number, f"WhatsApp test at {visit_time} - service is working!"
    )

    # 2. visitor_approval_emp template
    results["visitor_approval_emp"] = whatsapp_service.send_visitor_approval_request(
        to_phone=phone_number,
        visitor_name="Test Visitor",
        visitor_mobile="9999999999",
        visitor_email="test@example.com",
        visitor_company="Test Company",
        reason_for_visit="Template Test",
        visitor_id=reference_no,
    )

    # 3. visitor_approved template
    results["visitor_approved"] = whatsapp_service.send_approval_notification(
        to_phone=phone_number,
        visitor_id_str=f"CN-{reference_no}",
    )

    # 4. visitor_rejected template
    results["visitor_rejected"] = whatsapp_service.send_rejection_notification(
        to_phone=phone_number,
        visitor_id_str=f"CN-{reference_no}",
    )

    return {
        "phone": phone_number,
        "whatsapp_enabled": whatsapp_service.enabled,
        "results": results,
    }


@router.get("/webhook", status_code=status.HTTP_200_OK)
async def verify_webhook(
    request: Request,
):
    """
    Webhook verification endpoint for Meta WhatsApp API.
    Meta sends a GET request with hub.mode, hub.verify_token, and hub.challenge
    to verify the webhook URL.
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        logger.info("WhatsApp webhook verified successfully")
        return PlainTextResponse(content=challenge, status_code=200)

    logger.warning(f"WhatsApp webhook verification failed. mode={mode}, token={token}")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def handle_whatsapp_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Handle incoming WhatsApp messages/button replies from Meta API.

    When an approver taps Approve or Reject on the interactive message,
    Meta sends the button_reply payload here.
    """
    try:
        body = await request.json()
        logger.info(f"[WA-WEBHOOK] Received payload: {body}")

        # Meta sends a variety of webhook events; we only care about messages
        entries = body.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])

                for message in messages:
                    await _process_message(db, message)

        # Always return 200 to acknowledge receipt (Meta requirement)
        return JSONResponse(content={"status": "ok"}, status_code=200)

    except Exception as e:
        logger.error(f"[WA-WEBHOOK] Error processing webhook: {e}", exc_info=True)
        # Still return 200 to prevent Meta from retrying
        return JSONResponse(content={"status": "error"}, status_code=200)


async def _process_message(db: Session, message: dict):
    """Process a single incoming WhatsApp message."""
    sender_phone = message.get("from", "")  # e.g., "919876543210"
    msg_type = message.get("type", "")

    logger.info(f"[WA-WEBHOOK] Message from {sender_phone}, type={msg_type}")

    # Handle template quick reply button responses
    if msg_type == "button":
        button_payload = message.get("button", {}).get("payload", "")  # e.g., "approve_20260326101530"
        button_text = message.get("button", {}).get("text", "")

        logger.info(f"[WA-WEBHOOK] Template button reply: payload={button_payload}, text={button_text}")
        await _handle_button_reply(db, sender_phone, button_payload)
        return

    # Handle interactive button replies (non-template)
    if msg_type == "interactive":
        interactive = message.get("interactive", {})
        if interactive.get("type") == "button_reply":
            button_reply = interactive.get("button_reply", {})
            button_id = button_reply.get("id", "")
            logger.info(f"[WA-WEBHOOK] Interactive button reply: id={button_id}")
            await _handle_button_reply(db, sender_phone, button_id)
            return

    # Handle plain text messages (in case approver types instead of tapping button)
    if msg_type == "text":
        text_body = message.get("text", {}).get("body", "").strip().upper()
        if text_body in ("APPROVE", "APPROVED", "YES", "OK", "Y"):
            await _handle_text_approval(db, sender_phone, "approve")
            return
        elif text_body in ("REJECT", "REJECTED", "NO", "DENY", "N"):
            await _handle_text_approval(db, sender_phone, "reject")
            return

    logger.info(f"[WA-WEBHOOK] Ignoring message type: {msg_type}")


async def _handle_button_reply(db: Session, sender_phone: str, button_id: str):
    """Handle an interactive button reply (Approve/Reject)."""
    # Parse button_id: "approve_20260326101530" or "reject_20260326101530"
    parts = button_id.split("_", 1)
    if len(parts) != 2:
        logger.warning(f"[WA-WEBHOOK] Invalid button_id format: {button_id}")
        whatsapp_service.send_text_message(sender_phone, "Invalid response. Please use the dashboard.")
        return

    action, visitor_id_str = parts[0].lower(), parts[1]

    # Find approver
    approver = _find_approver(db, sender_phone)
    if not approver:
        logger.warning(f"[WA-WEBHOOK] No approver found for phone {sender_phone}")
        whatsapp_service.send_text_message(sender_phone, "Your number is not registered as an approver.")
        return

    # Find visitor
    try:
        visitor_id_int = int(visitor_id_str)
    except ValueError:
        logger.error(f"[WA-WEBHOOK] Invalid visitor ID: {visitor_id_str}")
        whatsapp_service.send_text_message(sender_phone, "Invalid visitor ID.")
        return

    visitor = db.query(Visitor).filter(Visitor.id == visitor_id_int).first()

    if not visitor:
        logger.warning(f"[WA-WEBHOOK] Visitor {visitor_id_int} not found")
        whatsapp_service.send_text_message(sender_phone, f"Visitor {visitor_id_str} not found.")
        return

    # Check approver is assigned or is a superuser
    is_assigned = visitor.person_to_meet in (approver.username, approver.name)
    if not is_assigned and not approver.superuser:
        logger.warning(f"[WA-WEBHOOK] Approver {approver.username} not authorized for visitor {visitor_id_int}")
        whatsapp_service.send_text_message(sender_phone, f"Visitor {visitor_id_str} is not assigned to you.")
        return

    if visitor.status != VisitorStatus.WAITING:
        logger.info(f"[WA-WEBHOOK] Visitor {visitor_id_int} already processed (status: {visitor.status.value})")
        whatsapp_service.send_text_message(
            sender_phone,
            f"Visitor {visitor_id_str} ({visitor.visitor_name}) has already been {visitor.status.value.lower()}."
        )
        return

    # Update status
    if action == "approve":
        visitor.status = VisitorStatus.APPROVED
        visitor.rejection_reason = None
        status_text = "approved"
    elif action == "reject":
        visitor.status = VisitorStatus.REJECTED
        status_text = "rejected"
    else:
        logger.warning(f"[WA-WEBHOOK] Unknown action: {action}")
        whatsapp_service.send_text_message(sender_phone, "Unknown action. Please use the dashboard.")
        return

    try:
        db.commit()
        db.refresh(visitor)
        logger.info(f"[WA-WEBHOOK] Visitor {visitor_id_int} {status_text} by {approver.username}")

        # Send confirmation to approver via WhatsApp
        whatsapp_service.send_text_message(
            sender_phone,
            f"Visitor {visitor_id_str} ({visitor.visitor_name}) has been {status_text}."
        )

        # Notify visitor of approval/rejection
        if visitor.mobile_number:
            cn_number = visitor.check_in_time.strftime("%Y%m%d%H%M%S") if visitor.check_in_time else str(visitor.id)
            if action == "approve":
                whatsapp_service.send_approval_notification(
                    to_phone=visitor.mobile_number,
                    visitor_id_str=cn_number,
                )
            else:
                whatsapp_service.send_rejection_notification(
                    to_phone=visitor.mobile_number,
                    visitor_id_str=cn_number,
                )

    except Exception as e:
        db.rollback()
        logger.error(f"[WA-WEBHOOK] Failed to update visitor {visitor_id_int}: {e}", exc_info=True)
        whatsapp_service.send_text_message(sender_phone, "Error updating visitor status. Please use the dashboard.")


async def _handle_text_approval(db: Session, sender_phone: str, action: str):
    """Handle text-based approval/rejection (fallback if approver types instead of tapping button)."""
    approver = _find_approver(db, sender_phone)
    if not approver:
        whatsapp_service.send_text_message(sender_phone, "Your number is not registered as an approver.")
        return

    # Find the most recent WAITING visitor for this approver
    visitor = db.query(Visitor).filter(
        (Visitor.person_to_meet == approver.username) | (Visitor.person_to_meet == approver.name),
        Visitor.status == VisitorStatus.WAITING,
    ).order_by(Visitor.check_in_time.desc()).first()

    if not visitor:
        whatsapp_service.send_text_message(sender_phone, "No pending visitor requests found.")
        return

    if action == "approve":
        visitor.status = VisitorStatus.APPROVED
        visitor.rejection_reason = None
        status_text = "approved"
    else:
        visitor.status = VisitorStatus.REJECTED
        status_text = "rejected"

    try:
        db.commit()
        db.refresh(visitor)
        logger.info(f"[WA-WEBHOOK] Visitor {visitor.id} {status_text} by {approver.username} (text reply)")

        whatsapp_service.send_text_message(
            sender_phone,
            f"Visitor {visitor.id} ({visitor.visitor_name}) has been {status_text}."
        )

        if visitor.mobile_number:
            cn_number = visitor.check_in_time.strftime("%Y%m%d%H%M%S") if visitor.check_in_time else str(visitor.id)
            if action == "approve":
                whatsapp_service.send_approval_notification(
                    to_phone=visitor.mobile_number,
                    visitor_id_str=cn_number,
                )
            else:
                whatsapp_service.send_rejection_notification(
                    to_phone=visitor.mobile_number,
                    visitor_id_str=cn_number,
                )

    except Exception as e:
        db.rollback()
        logger.error(f"[WA-WEBHOOK] Text approval failed: {e}", exc_info=True)
        whatsapp_service.send_text_message(sender_phone, "Error updating visitor. Please use the dashboard.")


