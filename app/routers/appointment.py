"""
Appointment Router
Handles appointment-related endpoints
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional
import logging

from app.core.database import get_db
from app.core.auth import get_current_approver
from app.models.appointment import Appointment
from app.models.visitor import Visitor, VisitorStatus
from app.models.approver import Approver

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/appointments", tags=["Appointments"])


@router.get("/qr/{qr_code}")
def get_appointment_by_qr(
    qr_code: str,
    db: Session = Depends(get_db),
    current_user: Optional[Approver] = Depends(get_current_approver)
):
    """
    Get appointment details by QR code.
    Used when scanning QR code at gate.
    
    Args:
        qr_code: QR code identifier (e.g., APT-12345-ABCD1234)
        db: Database session
        current_user: Current authenticated user (optional for gate scanning)
        
    Returns:
        Appointment details with visitor information
    """
    logger.info(f"[Appointment] Looking up appointment with QR code: {qr_code}")
    
    # Find appointment by QR code
    appointment = db.query(Appointment).filter(
        Appointment.qr_code == qr_code
    ).first()
    
    if not appointment:
        logger.warning(f"[Appointment] QR code not found: {qr_code}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Appointment with QR code '{qr_code}' not found"
        )
    
    # Get visitor details if visitor_id exists
    visitor = None
    if appointment.visitor_id:
        visitor = db.query(Visitor).filter(Visitor.id == appointment.visitor_id).first()
    
    # Get approver details
    approver = None
    if appointment.person_to_meet:
        approver = db.query(Approver).filter(
            (Approver.username == appointment.person_to_meet) |
            (Approver.name == appointment.person_to_meet)
        ).first()
    
    # Build response
    response = {
        "appointment_id": appointment.id,
        "visitor_id": appointment.visitor_id,  # Important: visitor_id for ICard assignment
        "qr_code": appointment.qr_code,
        "status": appointment.status,
        "visitor": {
            "id": visitor.id if visitor else None,
            "name": appointment.visitor_name,
            "mobile": appointment.mobile_number,
            "email": appointment.email_address,
            "company": appointment.company,
        },
        "appointment_details": {
            "person_to_meet": appointment.person_to_meet,
            "person_to_meet_name": approver.name if approver else appointment.person_to_meet,
            "purpose": appointment.purpose_of_visit,
            "date": appointment.preferred_time_slot,  # You may want to add date_of_visit field
            "time": appointment.preferred_time_slot,
        },
        "visitor_status": visitor.status.value if visitor else None,
        "is_approved": appointment.status == "CONFIRMED" and (visitor.status == VisitorStatus.APPROVED if visitor else False),
        "carrying_items": appointment.carrying_items,
        "additional_remarks": appointment.additional_remarks,
    }
    
    logger.info(f"[Appointment] Found appointment {appointment.id} for QR code {qr_code}")
    return response

