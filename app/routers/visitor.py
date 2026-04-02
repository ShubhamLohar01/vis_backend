from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, Form, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
import re
import logging

from app.core.database import get_db
from app.core.auth import get_current_approver
from app.models.approver import Approver
from app.models.visitor import Visitor, VisitorStatus
from app.models.appointment import Appointment
from app.schemas.visitor import (
    VisitorCheckIn,
    VisitorUpdate,
    VisitorStatusUpdate,
    VisitorResponse,
    VisitorCheckInResponse,
    VisitorListResponse,
    VisitorStatsResponse,
    GoogleFormSubmission,
)
from app.services.s3_service import s3_service
from app.services.whatsapp_service import whatsapp_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/visitors", tags=["Visitors"])

def _find_approver_for_notification(db: Session, person_to_meet: str) -> Optional[Approver]:
    """Find approver by username or name (case-insensitive, trimmed)."""
    if not person_to_meet:
        return None
    key = person_to_meet.strip()
    if not key:
        return None
    return db.query(Approver).filter(
        (Approver.username.ilike(key)) |
        (Approver.name.ilike(key))
    ).first()

def _get_superuser_phone_numbers(db: Session) -> List[str]:
    """Return all configured superuser phone numbers (deduped)."""
    superusers = db.query(Approver).filter(
        Approver.superuser == True,  # noqa: E712
        Approver.is_active == True,  # noqa: E712
        Approver.ph_no.isnot(None),
        Approver.ph_no != "",
    ).all()
    phones = []
    for s in superusers:
        if s.ph_no and s.ph_no not in phones:
            phones.append(s.ph_no)
    return phones


def enrich_visitor_with_contact(visitor: Visitor, db: Session) -> dict:
    """
    Enrich visitor data with the person to meet's contact information.

    Args:
        visitor: Visitor object
        db: Database session

    Returns:
        Dictionary with visitor data including person_to_meet_contact
    """
    # Extract date_of_visit and time_slot from health_declaration JSON if present
    date_of_visit = None
    time_slot = None
    if visitor.health_declaration:
        try:
            import json
            health_data = json.loads(visitor.health_declaration)
            date_of_visit = health_data.get('date_of_visit')
            time_slot = health_data.get('time_slot')
        except:
            pass

    visitor_dict = {
        "id": visitor.id,
        "visitor_name": visitor.visitor_name,
        "mobile_number": visitor.mobile_number,
        "email_address": visitor.email_address,
        "company": visitor.company,
        "person_to_meet": visitor.person_to_meet,
        "reason_to_visit": visitor.reason_to_visit,
        "warehouse": visitor.warehouse,
        "health_declaration": visitor.health_declaration,
        "status": visitor.status,
        "check_in_time": visitor.check_in_time,
        "check_out_time": visitor.check_out_time,
        "created_at": visitor.created_at,
        "updated_at": visitor.updated_at,
        "person_to_meet_contact": None,
        "img_url": visitor.img_url,
        "date_of_visit": date_of_visit,
        "time_slot": time_slot
    }

    # Try to find approver by name to get contact information
    approver = db.query(Approver).filter(Approver.name == visitor.person_to_meet).first()
    if approver and approver.ph_no:
        visitor_dict["person_to_meet_contact"] = approver.ph_no

    return visitor_dict


def validate_visitor_id(visitor_id: str) -> int:
    """
    Validate visitor ID format and return as integer.
    Accepts either 14-digit YYYYMMDDHHMMSS format or any numeric ID.

    Args:
        visitor_id: Visitor ID string to validate

    Returns:
        Integer representation of the visitor ID

    Raises:
        HTTPException: If ID format is invalid
    """
    # Validate ID format: Must be all digits
    if not re.match(r'^\d+$', visitor_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid visitor ID format. Expected numeric ID."
        )

    # If it's exactly 14 digits, validate the date/time format
    if len(visitor_id) == 14:
        try:
            year = int(visitor_id[0:4])
            month = int(visitor_id[4:6])
            day = int(visitor_id[6:8])
            hour = int(visitor_id[8:10])
            minute = int(visitor_id[10:12])
            second = int(visitor_id[12:14])

            # Basic validation
            if not (1900 <= year <= 2100):
                raise ValueError("Invalid year")
            if not (1 <= month <= 12):
                raise ValueError("Invalid month")
            if not (1 <= day <= 31):
                raise ValueError("Invalid day")
            if not (0 <= hour <= 23):
                raise ValueError("Invalid hour")
            if not (0 <= minute <= 59):
                raise ValueError("Invalid minute")
            if not (0 <= second <= 59):
                raise ValueError("Invalid second")

            # Try to create a datetime object to validate the complete date
            datetime(year, month, day, hour, minute, second)

        except (ValueError, Exception) as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid date/time in visitor ID: {str(e)}"
            )

    return int(visitor_id)


@router.post("/check-in", response_model=VisitorCheckInResponse, status_code=status.HTTP_201_CREATED)
def check_in_visitor(
    visitor_data: VisitorCheckIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Check in a new visitor. This is a public endpoint (no authentication required).

    Args:
        visitor_data: Visitor check-in information
        db: Database session

    Returns:
        Created visitor information with check-in details
    """
    # Prepare health_declaration JSON to include appointment data if provided
    import json
    appointment_data = {}
    if visitor_data.date_of_visit:
        appointment_data['date_of_visit'] = visitor_data.date_of_visit
    if visitor_data.time_slot:
        appointment_data['time_slot'] = visitor_data.time_slot
    
    # Merge with existing health_declaration if provided
    health_declaration_json = visitor_data.health_declaration
    if appointment_data:
        if health_declaration_json:
            try:
                existing_data = json.loads(health_declaration_json)
                existing_data.update(appointment_data)
                health_declaration_json = json.dumps(existing_data)
            except:
                health_declaration_json = json.dumps(appointment_data)
        else:
            health_declaration_json = json.dumps(appointment_data)

    # Create new visitor
    new_visitor = Visitor(
        visitor_name=visitor_data.visitor_name,
        mobile_number=visitor_data.mobile_number,
        email_address=visitor_data.email_address,
        company=visitor_data.company,
        person_to_meet=visitor_data.person_to_meet,
        reason_to_visit=visitor_data.reason_to_visit,
        warehouse=visitor_data.warehouse,
        health_declaration=health_declaration_json,
        status=VisitorStatus.WAITING
    )

    db.add(new_visitor)
    db.commit()
    db.refresh(new_visitor)

    # Enrich with contact information
    visitor_data = enrich_visitor_with_contact(new_visitor, db)

    # Send SMS notification asynchronously (non-blocking)
    def send_sms_background(visitor_id: int, person_to_meet: str, visitor_name: str, 
                           mobile: str, email: Optional[str], company: Optional[str], 
                           reason: str, warehouse: Optional[str]):
        """Background task to send SMS without blocking the response."""
        try:
            # Get a new database session for background task
            from app.core.database import SessionLocal
            db_session = SessionLocal()
            try:
                approver = db_session.query(Approver).filter(
                    (Approver.username.ilike(person_to_meet.strip())) |
                    (Approver.name.ilike(person_to_meet.strip()))
                ).first()
                
                # Always notify superusers as well (they should see all SMS)
                superuser_phones = _get_superuser_phone_numbers(db_session)
                target_phones: List[str] = []
                if approver and approver.ph_no:
                    target_phones.append(approver.ph_no)
                for p in superuser_phones:
                    if p not in target_phones:
                        target_phones.append(p)

                if target_phones:
                    for to_phone in target_phones:
                        # Send WhatsApp notification with Approve/Reject buttons
                        wa_sent = whatsapp_service.send_visitor_approval_request(
                            to_phone=to_phone,
                            visitor_name=visitor_name,
                            visitor_mobile=mobile,
                            visitor_email=email,
                            visitor_company=company,
                            reason_for_visit=reason,
                            visitor_id=str(visitor_id),
                            warehouse=warehouse,
                            person_to_meet_name=approver.name if approver else person_to_meet,
                        )
                        if wa_sent:
                            logger.info(f"WhatsApp notification sent to {to_phone} for visitor {visitor_id}")
                        else:
                            logger.warning(f"Failed to send WhatsApp notification to {to_phone}")
                else:
                    logger.warning(f"Approver '{person_to_meet}' not found or has no phone number. Notification not sent.")
            finally:
                db_session.close()
        except Exception as e:
            logger.error(f"Error sending SMS notification in background: {e}", exc_info=True)
    
    # Add background task (non-blocking)
    background_tasks.add_task(
        send_sms_background,
        new_visitor.id,
        new_visitor.person_to_meet,
        new_visitor.visitor_name,
        new_visitor.mobile_number,
        new_visitor.email_address,
        new_visitor.company,
        new_visitor.reason_to_visit,
        new_visitor.warehouse
    )

    return VisitorCheckInResponse(
        message="Visitor checked in successfully",
        visitor=VisitorResponse.model_validate(visitor_data)
    )


@router.post("/check-in-with-image", response_model=VisitorCheckInResponse, status_code=status.HTTP_201_CREATED)
async def check_in_visitor_with_image(
    visitor_name: str = Form(..., min_length=1, max_length=255, description="Name of the visitor"),
    mobile_number: str = Form(..., min_length=10, max_length=20, description="Mobile number of the visitor"),
    person_to_meet: str = Form(..., min_length=1, max_length=255, description="Person the visitor wants to meet"),
    reason_to_visit: str = Form(..., min_length=1, max_length=500, description="Reason for the visit"),
    email_address: str = Form(..., description="Email address of the visitor"),
    company: str = Form(..., min_length=1, max_length=255, description="Company name of the visitor"),
    warehouse: Optional[str] = Form(None, max_length=255, description="Warehouse location"),
    health_declaration: Optional[str] = Form(None, description="Health & safety declaration as JSON string"),
    image: UploadFile = File(..., description="Visitor image file"),
    db: Session = Depends(get_db)
):
    """
    Check in a new visitor with an image. This is a public endpoint (no authentication required).

    The endpoint accepts multipart form data with visitor information and an image file.
    The image is uploaded to S3 and the visitor record is created with the image URL.
    The visitor number (ID) will be in YYYYMMDDHHMMSS format and used as the image filename.

    Args:
        visitor_name: Name of the visitor
        mobile_number: Mobile number of the visitor
        person_to_meet: Person the visitor wants to meet
        reason_to_visit: Reason for the visit
        email_address: Email address of the visitor (optional)
        company: Company name of the visitor (optional)
        warehouse: Warehouse location (optional)
        image: Image file (JPEG, PNG, etc.)
        db: Database session

    Returns:
        Created visitor information with check-in details and image URL

    Raises:
        HTTPException: If image upload fails or validation fails
    """
    # Validate image file
    allowed_content_types = ["image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp"]
    if image.content_type not in allowed_content_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid image format. Allowed formats: {', '.join(allowed_content_types)}"
        )

    # Validate file size (max 10MB)
    max_file_size = 10 * 1024 * 1024  # 10MB
    file_content = await image.read()
    if len(file_content) > max_file_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Image file size exceeds 10MB limit"
        )

    # Create new visitor first to get the auto-generated ID
    new_visitor = Visitor(
        visitor_name=visitor_name,
        mobile_number=mobile_number,
        email_address=email_address,
        company=company,
        person_to_meet=person_to_meet,
        reason_to_visit=reason_to_visit,
        warehouse=warehouse,
        health_declaration=health_declaration,
        status=VisitorStatus.WAITING
    )

    db.add(new_visitor)
    db.commit()
    db.refresh(new_visitor)

    # Generate visitor number in YYYYMMDDHHMMSS format using the check_in_time
    check_in_time = new_visitor.check_in_time
    visitor_number = check_in_time.strftime("%Y%m%d%H%M%S")

    # Upload to S3 immediately (synchronous) - with increased timeouts this should complete within API Gateway limit
    try:
        logger.info(f"Starting S3 upload for visitor {visitor_number}")
        img_url = s3_service.upload_visitor_image(
            file_content=file_content,
            visitor_number=visitor_number,
            content_type=image.content_type
        )
        new_visitor.img_url = img_url
        db.commit()
        db.refresh(new_visitor)
        logger.info(f"S3 upload complete for visitor {visitor_number}: {img_url}")
    except Exception as e:
        logger.error(f"S3 upload failed for visitor {visitor_number}: {str(e)}")
        # Continue anyway - image can be uploaded later if needed
        new_visitor.img_url = None
        db.commit()
        db.refresh(new_visitor)

    # Enrich with contact information
    visitor_data = enrich_visitor_with_contact(new_visitor, db)
    
    # Extract date_of_visit and time_slot from health_declaration if present
    date_of_visit = None
    time_slot = None
    if health_declaration:
        try:
            import json
            health_data = json.loads(health_declaration)
            date_of_visit = health_data.get('date_of_visit')
            time_slot = health_data.get('time_slot')
        except:
            pass

    # Send SMS notification - quick lookup and send (with timeout protection)
    # Note: In Lambda, BackgroundTasks don't work as expected. We'll do a quick synchronous send with timeout.
    try:
        logger.info(f"[SMS] Searching for approver: {person_to_meet}")
        approver = _find_approver_for_notification(db, person_to_meet)
        
        # Always notify superusers as well (they should see all SMS)
        superuser_phones = _get_superuser_phone_numbers(db)
        target_phones: List[str] = []
        if approver and approver.ph_no:
            target_phones.append(approver.ph_no)
        for p in superuser_phones:
            if p not in target_phones:
                target_phones.append(p)

        if target_phones:
            for to_phone in target_phones:
                logger.info(f"[WA] Sending WhatsApp to {to_phone}")
                wa_sent = whatsapp_service.send_visitor_approval_request(
                    to_phone=to_phone,
                    visitor_name=visitor_name,
                    visitor_mobile=mobile_number,
                    visitor_email=email_address,
                    visitor_company=company,
                    reason_for_visit=reason_to_visit,
                    visitor_id=str(new_visitor.id),
                    warehouse=warehouse,
                    person_to_meet_name=approver.name if approver else person_to_meet,
                    visitor_image_url=new_visitor.img_url,
                )
                if wa_sent:
                    logger.info(f"[WA] WhatsApp sent to {to_phone}")
                else:
                    logger.warning(f"[WA] WhatsApp failed to {to_phone}")
        else:
            logger.warning(f"[WA] Approver '{person_to_meet}' not found or has no phone number")
    except Exception as e:
        # Don't fail the request if SMS fails
        logger.error(f"[SMS] ✗ SMS error: {e}")
        pass

    return VisitorCheckInResponse(
        message="Visitor checked in successfully with image",
        visitor=VisitorResponse.model_validate(visitor_data)
    )


@router.get("/", response_model=VisitorListResponse, status_code=status.HTTP_200_OK)
def get_all_visitors(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(100, ge=1, le=100, description="Number of items per page"),
    db: Session = Depends(get_db),
    current_user: Approver = Depends(get_current_approver)
):
    """
    Get all visitors with pagination. Requires authentication.

    Args:
        page: Page number (starts from 1)
        page_size: Number of items per page (default: 100)
        db: Database session
        current_user: Current authenticated approver

    Returns:
        Paginated list of visitors
    """
    query = db.query(Visitor)

    # Get total count
    total = query.count()

    # Apply pagination
    offset = (page - 1) * page_size
    visitors = query.order_by(Visitor.check_in_time.desc()).offset(offset).limit(page_size).all()

    # Enrich with contact information
    enriched_visitors = [enrich_visitor_with_contact(visitor, db) for visitor in visitors]

    return VisitorListResponse(
        total=total,
        visitors=[VisitorResponse.model_validate(visitor_data) for visitor_data in enriched_visitors],
        page=page,
        page_size=page_size
    )


@router.get("/stats", response_model=VisitorStatsResponse, status_code=status.HTTP_200_OK)
def get_visitor_stats(
    db: Session = Depends(get_db),
    current_user: Approver = Depends(get_current_approver)
):
    """
    Get visitor statistics. Requires authentication.

    Args:
        db: Database session
        current_user: Current authenticated approver

    Returns:
        Visitor statistics by status
    """
    total_visitors = db.query(Visitor).count()
    waiting = db.query(Visitor).filter(Visitor.status == VisitorStatus.WAITING).count()
    approved = db.query(Visitor).filter(Visitor.status == VisitorStatus.APPROVED).count()
    rejected = db.query(Visitor).filter(Visitor.status == VisitorStatus.REJECTED).count()

    return VisitorStatsResponse(
        total_visitors=total_visitors,
        waiting=waiting,
        approved=approved,
        rejected=rejected
    )


@router.get("/phone/{phone_number}", response_model=List[VisitorResponse], status_code=status.HTTP_200_OK)
def get_visitor_by_phone(
    phone_number: str,
    db: Session = Depends(get_db)
):
    """
    Get all visitors by phone number. This is a public endpoint (no authentication required).
    Returns all visitor records associated with the given phone number.

    Args:
        phone_number: Phone number to search for
        db: Database session

    Returns:
        List of visitors with matching phone number

    Raises:
        HTTPException: If no visitors found with this phone number
    """
    visitors = db.query(Visitor).filter(Visitor.mobile_number == phone_number).order_by(Visitor.check_in_time.desc()).all()

    if not visitors:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No visitors found with phone number {phone_number}"
        )

    # Enrich with contact information
    enriched_visitors = [enrich_visitor_with_contact(visitor, db) for visitor in visitors]

    return [VisitorResponse.model_validate(visitor_data) for visitor_data in enriched_visitors]


@router.get("/{visitor_id}", response_model=VisitorResponse, status_code=status.HTTP_200_OK)
def get_visitor_by_id(
    visitor_id: str,
    db: Session = Depends(get_db)
):
    """
    Get a specific visitor by ID. This is a public endpoint (no authentication required).
    ID must be in YYYYMMDDHHMMSS format (14 digits).

    Args:
        visitor_id: ID of the visitor to retrieve (format: YYYYMMDDHHMMSS, e.g., 20251120170530)
        db: Database session

    Returns:
        Visitor information

    Raises:
        HTTPException: If visitor ID format is invalid or visitor not found
    """
    # Validate and convert visitor ID
    visitor_id_int = validate_visitor_id(visitor_id)

    visitor = db.query(Visitor).filter(Visitor.id == visitor_id_int).first()

    if not visitor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Visitor with ID {visitor_id} not found"
        )

    # Enrich with contact information
    visitor_data = enrich_visitor_with_contact(visitor, db)

    return VisitorResponse.model_validate(visitor_data)


@router.put("/{visitor_id}", response_model=VisitorResponse, status_code=status.HTTP_200_OK)
def update_visitor(
    visitor_id: str,
    visitor_data: VisitorUpdate,
    db: Session = Depends(get_db),
    current_user: Approver = Depends(get_current_approver)
):
    """
    Update visitor information. Requires authentication.

    Args:
        visitor_id: ID of the visitor to update (format: YYYYMMDDHHMMSS, e.g., 20251120170530)
        visitor_data: Updated visitor data
        db: Database session
        current_user: Current authenticated approver

    Returns:
        Updated visitor information

    Raises:
        HTTPException: If visitor ID format is invalid or visitor not found
    """
    # Validate and convert visitor ID
    visitor_id_int = validate_visitor_id(visitor_id)

    visitor = db.query(Visitor).filter(Visitor.id == visitor_id_int).first()

    if not visitor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Visitor with ID {visitor_id} not found"
        )

    # Update fields
    update_data = visitor_data.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(visitor, field, value)

    db.commit()
    db.refresh(visitor)

    # Enrich with contact information
    visitor_dict = enrich_visitor_with_contact(visitor, db)

    return VisitorResponse.model_validate(visitor_dict)


@router.patch("/{visitor_id}/status", response_model=VisitorResponse, status_code=status.HTTP_200_OK)
def update_visitor_status(
    visitor_id: str,
    status_data: VisitorStatusUpdate,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
    current_user: Approver = Depends(get_current_approver)
):
    """
    Update visitor status. Requires authentication.

    Args:
        visitor_id: ID of the visitor (format: YYYYMMDDHHMMSS, e.g., 20251120170530)
        status_data: New status (WAITING, APPROVED, or REJECTED)
        db: Database session
        current_user: Current authenticated approver

    Returns:
        Updated visitor information

    Raises:
        HTTPException: If visitor ID format is invalid or visitor not found
    """
    # Validate and convert visitor ID
    visitor_id_int = validate_visitor_id(visitor_id)

    visitor = db.query(Visitor).filter(Visitor.id == visitor_id_int).first()

    if not visitor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Visitor with ID {visitor_id} not found"
        )

    # Update status
    visitor.status = status_data.status
    
    # Check if this is an appointment
    is_appointment = visitor.reason_to_visit and visitor.reason_to_visit.startswith("[APPOINTMENT]")
    
    # Extract appointment details from health_declaration
    import json
    appointment_data = {}
    date_of_visit = None
    time_slot = None
    
    if visitor.health_declaration:
        try:
            health_data = json.loads(visitor.health_declaration)
            date_of_visit = health_data.get('date_of_visit')
            time_slot = health_data.get('time_slot')
            appointment_data = {
                'carrying_items': health_data.get('carrying_items'),
                'additional_remarks': health_data.get('additional_remarks'),
                'source': health_data.get('source', 'google_form'),
            }
        except:
            pass
    
    # Handle appointment approval
    if is_appointment and status_data.status == VisitorStatus.APPROVED:
        logger.info(f"[Appointment] Visitor {visitor_id} is an approved appointment, saving to vis_appointment table")
        
        # Generate unique QR code
        import uuid
        qr_code = f"APT-{visitor.id}-{uuid.uuid4().hex[:8].upper()}"
        
        # Create or update appointment record
        existing_appointment = db.query(Appointment).filter(
            Appointment.visitor_id == visitor.id
        ).first()
        
        if existing_appointment:
            # Update existing appointment
            existing_appointment.status = 'CONFIRMED'
            existing_appointment.qr_code = qr_code
            existing_appointment.updated_at = datetime.now()
            appointment = existing_appointment
        else:
            # Create new appointment record
            appointment = Appointment(
                visitor_name=visitor.visitor_name,
                mobile_number=visitor.mobile_number,
                email_address=visitor.email_address,
                company=visitor.company,
                person_to_meet=visitor.person_to_meet,
                purpose_of_visit=visitor.reason_to_visit.replace("[APPOINTMENT] ", ""),
                preferred_time_slot=time_slot,
                carrying_items=appointment_data.get('carrying_items'),
                additional_remarks=appointment_data.get('additional_remarks'),
                source=appointment_data.get('source', 'google_form'),
                status='CONFIRMED',
                visitor_id=visitor.id,
                qr_code=qr_code,
                qr_code_sent='NO'
            )
            db.add(appointment)
        
        db.commit()
        db.refresh(appointment)
        
        # Send QR code via email in background
        def send_qr_email_background(appointment_id: int, qr_code: str, visitor_email: str, visitor_name: str, 
                                     visitor_number: str, appointment_date: Optional[str], appointment_time: Optional[str],
                                     approver_name: str):
            """Background task to send QR code via email"""
            try:
                from app.services.email_service import email_service
                logger.info(f"[Appointment] Sending QR code email to {visitor_email} for appointment {appointment_id}")
                
                email_sent = email_service.send_appointment_qr(
                    to_email=visitor_email,
                    visitor_name=visitor_name,
                    qr_code=qr_code,
                    visitor_number=visitor_number,
                    appointment_date=appointment_date,
                    appointment_time=appointment_time,
                    approver_name=approver_name
                )
                
                if email_sent:
                    # Update appointment record
                    from app.core.database import SessionLocal
                    db_session = SessionLocal()
                    try:
                        apt = db_session.query(Appointment).filter(Appointment.id == appointment_id).first()
                        if apt:
                            apt.qr_code_sent = 'YES'
                            db_session.commit()
                        logger.info(f"[Appointment] QR code email sent successfully to {visitor_email}")
                    finally:
                        db_session.close()
                else:
                    logger.warning(f"[Appointment] Failed to send QR code email to {visitor_email}")
            except Exception as e:
                logger.error(f"[Appointment] Error sending QR code email: {e}", exc_info=True)
        
        # Generate visitor number from check_in_time (format: YYYYMMDDHHMMSS)
        visitor_number = visitor.check_in_time.strftime("%Y%m%d%H%M%S")
        
        # Get approver name (the person who approved)
        approver_name = current_user.name if current_user else None
        
        # Add background task to send email
        background_tasks.add_task(
            send_qr_email_background,
            appointment.id,
            qr_code,
            visitor.email_address,
            visitor.visitor_name,
            visitor_number,
            date_of_visit,
            time_slot,
            approver_name
        )
        
        logger.info(f"[Appointment] Appointment {appointment.id} created/updated with QR code: {qr_code}")
    
    # Handle appointment rejection
    elif is_appointment and status_data.status == VisitorStatus.REJECTED:
        logger.info(f"[Appointment] Visitor {visitor_id} appointment rejected, updating appointment status and sending polite rejection email")
        
        # Update or create appointment record with CANCELLED status
        existing_appointment = db.query(Appointment).filter(
            Appointment.visitor_id == visitor.id
        ).first()
        
        if existing_appointment:
            existing_appointment.status = 'CANCELLED'
            existing_appointment.updated_at = datetime.now()
        else:
            # Create appointment record for rejected appointment
            appointment = Appointment(
                visitor_name=visitor.visitor_name,
                mobile_number=visitor.mobile_number,
                email_address=visitor.email_address,
                company=visitor.company,
                person_to_meet=visitor.person_to_meet,
                purpose_of_visit=visitor.reason_to_visit.replace("[APPOINTMENT] ", ""),
                preferred_time_slot=time_slot,
                carrying_items=appointment_data.get('carrying_items'),
                additional_remarks=appointment_data.get('additional_remarks'),
                source=appointment_data.get('source', 'google_form'),
                status='CANCELLED',
                visitor_id=visitor.id,
                qr_code=None,
                qr_code_sent='NO'
            )
            db.add(appointment)
        
        db.commit()
        
        # Send rejection email in background
        def send_rejection_email_background(visitor_email: str, visitor_name: str, 
                                            appointment_date: Optional[str], 
                                            appointment_time: Optional[str],
                                            rejection_reason: Optional[str]):
            """Background task to send rejection email"""
            try:
                from app.services.email_service import email_service
                logger.info(f"[Appointment] Sending rejection email to {visitor_email}")
                
                email_sent = email_service.send_appointment_rejection(
                    to_email=visitor_email,
                    visitor_name=visitor_name,
                    appointment_date=appointment_date,
                    appointment_time=appointment_time,
                    rejection_reason=rejection_reason
                )
                
                if email_sent:
                    logger.info(f"[Appointment] Rejection email sent successfully to {visitor_email}")
                else:
                    logger.warning(f"[Appointment] Failed to send rejection email to {visitor_email}")
            except Exception as e:
                logger.error(f"[Appointment] Error sending rejection email: {e}", exc_info=True)
        
        # Get rejection reason if available
        rejection_reason = getattr(status_data, 'rejection_reason', None) or visitor.rejection_reason
        
        # Add background task to send rejection email
        background_tasks.add_task(
            send_rejection_email_background,
            visitor.email_address,
            visitor.visitor_name,
            date_of_visit,
            time_slot,
            rejection_reason
        )
        
        logger.info(f"[Appointment] Appointment rejection processed for visitor {visitor_id}")

    # Send WhatsApp to visitor when approved (for both regular visitors and appointments)
    if status_data.status == VisitorStatus.APPROVED:
        logger.info(f"[WA] Visitor {visitor_id} approved, sending WhatsApp notification to visitor")

        def send_approval_wa_background(visitor_mobile: str, visitor_name: str,
                                        person_to_meet_name: Optional[str],
                                        visitor_id_str: str, is_appt: bool,
                                        visit_date: Optional[str], visit_time: Optional[str]):
            """Background task to send approval WhatsApp to visitor"""
            try:
                logger.info(f"[WA] Sending approval WhatsApp to visitor {visitor_name} at {visitor_mobile}")

                message_parts = [
                    f"Your visit request has been approved!",
                    f"",
                    f"Dear {visitor_name},",
                ]
                if is_appt:
                    message_parts.append("Your appointment has been approved.")
                    if visit_date:
                        message_parts.append(f"Date: {visit_date}")
                    if visit_time:
                        message_parts.append(f"Time: {visit_time}")
                else:
                    message_parts.append("Your visit request has been approved. Please come and visit us.")
                if person_to_meet_name:
                    message_parts.append(f"Meeting with: {person_to_meet_name}")
                message_parts.append(f"Visitor ID: {visitor_id_str}")
                message_parts.extend(["", "We look forward to seeing you!", "Thank you, Candor Foods"])

                wa_sent = whatsapp_service.send_text_message(
                    to_phone=visitor_mobile,
                    text="\n".join(message_parts),
                )

                if wa_sent:
                    logger.info(f"[WA] Approval WhatsApp sent to visitor {visitor_name} at {visitor_mobile}")
                else:
                    logger.warning(f"[WA] Failed to send approval WhatsApp to visitor {visitor_name} at {visitor_mobile}")
            except Exception as e:
                logger.error(f"[WA] Error sending approval WhatsApp to visitor: {e}", exc_info=True)
        
        # Get person to meet name
        person_to_meet_name = None
        try:
            approver = db.query(Approver).filter(
                (Approver.username == visitor.person_to_meet) | 
                (Approver.name == visitor.person_to_meet)
            ).first()
            if approver:
                person_to_meet_name = approver.name
        except:
            person_to_meet_name = visitor.person_to_meet
        
        # Generate visitor number from check_in_time (format: YYYYMMDDHHMMSS)
        visitor_number = visitor.check_in_time.strftime("%Y%m%d%H%M%S")
        
        # Add background task to send approval WhatsApp
        background_tasks.add_task(
            send_approval_wa_background,
            visitor.mobile_number,
            visitor.visitor_name,
            person_to_meet_name,
            visitor_number,
            is_appointment,
            date_of_visit,
            time_slot
        )

    db.commit()
    db.refresh(visitor)

    # Enrich with contact information
    visitor_dict = enrich_visitor_with_contact(visitor, db)

    return VisitorResponse.model_validate(visitor_dict)


@router.delete("/{visitor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_visitor(
    visitor_id: str,
    db: Session = Depends(get_db),
    current_user: Approver = Depends(get_current_approver)
):
    """
    Delete a visitor record. Requires authentication.

    Args:
        visitor_id: ID of the visitor to delete (format: YYYYMMDDHHMMSS, e.g., 20251120170530)
        db: Database session
        current_user: Current authenticated approver

    Raises:
        HTTPException: If visitor ID format is invalid or visitor not found
    """
    # Validate and convert visitor ID
    visitor_id_int = validate_visitor_id(visitor_id)

    visitor = db.query(Visitor).filter(Visitor.id == visitor_id_int).first()

    if not visitor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Visitor with ID {visitor_id} not found"
        )

    db.delete(visitor)
    db.commit()

    return None


@router.post("/google-form", response_model=VisitorCheckInResponse, status_code=status.HTTP_201_CREATED)
def google_form_submission(
    form_data: GoogleFormSubmission,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Handle Google Form submission and create visitor record.
    This endpoint maps Google Form fields to our visitor schema.

    Args:
        form_data: Google Form submission data
        db: Database session

    Returns:
        Created visitor information with check-in details
    """
    # Explicit terminal prints (helpful on some hosts where logging is buffered/filtered)
    print("=" * 80, flush=True)
    print("[Google Form] NEW APPOINTMENT REQUEST RECEIVED", flush=True)
    print(f"[Google Form] Visitor Name: {form_data.visitor_name}", flush=True)
    print(f"[Google Form] Mobile Number: {form_data.mobile}", flush=True)
    print(f"[Google Form] Email Address: {form_data.email}", flush=True)
    print(f"[Google Form] Company: {form_data.company}", flush=True)
    print(f"[Google Form] Person to Meet: {form_data.host_name}", flush=True)
    print(f"[Google Form] Purpose of Visit: {form_data.purpose}", flush=True)
    print(f"[Google Form] Preferred Time Slot: {form_data.preferred_time_slot}", flush=True)
    print("=" * 80, flush=True)

    logger.info("=" * 80)
    logger.info("[Google Form] ========== NEW APPOINTMENT REQUEST RECEIVED ==========")
    logger.info("=" * 80)
    logger.info(f"[Google Form] Visitor Name: {form_data.visitor_name}")
    logger.info(f"[Google Form] Mobile Number: {form_data.mobile}")
    logger.info(f"[Google Form] Email Address: {form_data.email}")
    logger.info(f"[Google Form] Company: {form_data.company}")
    logger.info(f"[Google Form] Person to Meet: {form_data.host_name}")
    logger.info(f"[Google Form] Purpose of Visit: {form_data.purpose}")
    logger.info(f"[Google Form] Preferred Time Slot: {form_data.preferred_time_slot}")
    logger.info(f"[Google Form] Carrying Items: {form_data.carrying_items}")
    logger.info(f"[Google Form] Additional Remarks: {form_data.additional_remarks}")
    logger.info(f"[Google Form] Source: {form_data.source or 'google_form'}")
    logger.info(f"[Google Form] Submitted At: {form_data.submitted_at}")
    if form_data.sheet_name:
        logger.info(f"[Google Form] Sheet Name: {form_data.sheet_name}")
    if form_data.row_number:
        logger.info(f"[Google Form] Row Number: {form_data.row_number}")
    logger.info("=" * 80)
    import json
    
    # Map Google Form fields to our schema
    # Prepare additional data for health_declaration JSON
    additional_data = {
        "source": form_data.source or "google_form",
        "submitted_at": form_data.submitted_at,
        "carrying_items": form_data.carrying_items,
        "additional_remarks": form_data.additional_remarks,
    }
    if form_data.sheet_name:
        additional_data["sheet_name"] = form_data.sheet_name
    if form_data.row_number:
        additional_data["row_number"] = form_data.row_number
    if form_data.preferred_time_slot:
        additional_data["time_slot"] = form_data.preferred_time_slot

    health_declaration_json = json.dumps(additional_data)

    # Find approver by name with partial/fuzzy matching
    logger.info(f"[Google Form] Looking for approver with name: '{form_data.host_name}'")
    
    # First try exact match
    approver = db.query(Approver).filter(
        (Approver.name.ilike(form_data.host_name)) | 
        (Approver.username.ilike(form_data.host_name))
    ).first()
    
    # If no exact match, try partial matching
    if not approver:
        logger.info(f"[Google Form] No exact match found, trying partial matching...")
        
        # Get all active approvers for partial matching
        all_approvers = db.query(Approver).filter(Approver.is_active == True).all()
        
        def levenshtein_distance(s1: str, s2: str) -> int:
            """Calculate Levenshtein distance between two strings"""
            if len(s1) < len(s2):
                return levenshtein_distance(s2, s1)
            
            if len(s2) == 0:
                return len(s1)
            
            previous_row = range(len(s2) + 1)
            for i, c1 in enumerate(s1):
                current_row = [i + 1]
                for j, c2 in enumerate(s2):
                    insertions = previous_row[j + 1] + 1
                    deletions = current_row[j] + 1
                    substitutions = previous_row[j] + (c1 != c2)
                    current_row.append(min(insertions, deletions, substitutions))
                previous_row = current_row
            
            return previous_row[-1]
        
        def word_similarity(word1: str, word2: str) -> float:
            """Calculate similarity between two words (0.0 to 1.0) - strict matching"""
            if not word1 or not word2:
                return 0.0
            
            word1 = word1.lower().strip()
            word2 = word2.lower().strip()
            
            # Exact match
            if word1 == word2:
                return 1.0
            
            # Calculate Levenshtein distance for character-level differences only
            max_len = max(len(word1), len(word2))
            min_len = min(len(word1), len(word2))
            
            if max_len == 0:
                return 1.0
            
            # Words must be similar length (not too different)
            if min_len < max_len * 0.6:  # If one word is less than 60% of the other, reject
                return 0.0
            
            distance = levenshtein_distance(word1, word2)
            
            # Allow only 1-2 character differences (strict)
            max_allowed_diff = 1 if max_len <= 4 else 2
            
            if distance <= max_allowed_diff:
                # Similarity based on distance
                similarity = 1.0 - (distance / max_len)
                # For 1 character difference, give good score
                if distance == 1:
                    similarity = max(similarity, 0.75)
                elif distance == 2:
                    similarity = max(similarity, 0.6)
                return similarity
            
            return 0.0
        
        def similarity_score(name1: str, name2: str) -> float:
            """
            Calculate similarity between two names (0.0 to 1.0) - strict matching with character tolerance.
            
            Rules:
            1. ALL words from INPUT must be matched in database name
            2. Database can have extra words (like middle names) - that's okay
            3. Character-level differences allowed (1-2 chars)
            
            Examples:
            - "pooja" vs "Pooja Suresh" → REJECT (input has 1 word, but might match multiple people)
            - "pooja malim" vs "Pooja Suresh Mhalim" → MATCH (both input words matched: pooja→Pooja, malim→Mhalim)
            - "pooja malim" vs "Pooja Mhalim" → MATCH (both 2 words, all match)
            - "yash gawdi" vs "Yash Gawadi" → MATCH (both 2 words, 1 char diff)
            """
            if not name1 or not name2:
                return 0.0
            
            name1 = name1.lower().strip()
            name2 = name2.lower().strip()
            
            # Exact match
            if name1 == name2:
                return 1.0
            
            # Split names into words
            words_input = name1.split()  # Input from Google Form
            words_db = name2.split()     # Database name
            
            if not words_input or not words_db:
                return 0.0
            
            # IMPORTANT: If input has only 1 word, reject to avoid false matches
            # Example: "pooja" should NOT match "Pooja Suresh" (there might be multiple Poojas)
            if len(words_input) == 1:
                # Only allow if database also has 1 word
                if len(words_db) == 1:
                    score = word_similarity(words_input[0], words_db[0])
                    return score
                else:
                    logger.debug(f"[Fuzzy Match] Rejected: Input has 1 word, DB has {len(words_db)} words (avoid false matches)")
                    return 0.0
            
            # Match each INPUT word to database words
            # This ensures ALL input words are matched (database can have extra words like middle names)
            total_similarity = 0.0
            matched_db_indices = set()
            
            for input_word in words_input:
                best_match_score = 0.0
                best_db_idx = -1
                
                # Find best matching word from database
                for idx, db_word in enumerate(words_db):
                    if idx in matched_db_indices:
                        continue
                    
                    score = word_similarity(input_word, db_word)
                    if score > best_match_score:
                        best_match_score = score
                        best_db_idx = idx
                
                # If we found a match for this input word
                if best_db_idx >= 0 and best_match_score > 0.0:
                    matched_db_indices.add(best_db_idx)
                    total_similarity += best_match_score
                else:
                    # Input word couldn't be matched - reject entire match
                    logger.debug(f"[Fuzzy Match] Rejected: Input word '{input_word}' not matched in database name")
                    return 0.0
            
            # All input words were matched successfully
            # Calculate average similarity
            avg_similarity = total_similarity / len(words_input)
            
            return avg_similarity
        
        # Find best matching approver (minimum 50% similarity)
        best_match = None
        best_score = 0.0
        
        for appr in all_approvers:
            # Check similarity with name
            name_score = similarity_score(form_data.host_name, appr.name)
            # Check similarity with username
            username_score = similarity_score(form_data.host_name, appr.username)
            
            score = max(name_score, username_score)
            
            logger.info(f"[Google Form] Similarity check: '{form_data.host_name}' vs '{appr.name}' ({appr.username}) = {score:.2f}")
            
            if score > best_score and score >= 0.5:  # Minimum 50% similarity (strict matching)
                best_match = appr
                best_score = score
        
        if best_match:
            approver = best_match
            logger.info(f"[Google Form] Found partial match: '{form_data.host_name}' matched to '{approver.name}' ({approver.username}) with {best_score:.2f} similarity")
        else:
            logger.warning(f"[Google Form] No suitable match found for '{form_data.host_name}'")
            # List available approvers for debugging
            available_names = [f"{a.name} ({a.username})" for a in all_approvers[:10]]
            logger.info(f"[Google Form] Available approvers: {available_names}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No matching approver found for '{form_data.host_name}'. Please check the person's name."
            )
    
    logger.info(f"[Google Form] Final match: {approver.username} (name: {approver.name})")
    print(f"[Google Form] Matched Approver: {approver.name} ({approver.username})", flush=True)
    
    # Create VisitorCheckIn object from Google Form data
    visitor_data = VisitorCheckIn(
        visitor_name=form_data.visitor_name,
        mobile_number=form_data.mobile,
        email_address=form_data.email,
        company=form_data.company,
        person_to_meet=approver.username,  # Use approver username for consistency
        reason_to_visit=f"[APPOINTMENT] {form_data.purpose}",  # Mark as appointment
        warehouse=None,
        health_declaration=health_declaration_json,
        date_of_visit=None,  # Google Form doesn't have date field
        time_slot=form_data.preferred_time_slot
    )
    
    logger.info(f"[Google Form] Creating visitor record - person_to_meet: {approver.username}, visitor: {form_data.visitor_name}")

    # Create new visitor using the same logic as check_in_visitor
    appointment_data = {}
    if visitor_data.date_of_visit:
        appointment_data['date_of_visit'] = visitor_data.date_of_visit
    if visitor_data.time_slot:
        appointment_data['time_slot'] = visitor_data.time_slot
    
    # Merge with existing health_declaration if provided
    if appointment_data:
        try:
            existing_data = json.loads(health_declaration_json)
            existing_data.update(appointment_data)
            health_declaration_json = json.dumps(existing_data)
        except:
            health_declaration_json = json.dumps({**additional_data, **appointment_data})

    new_visitor = Visitor(
        visitor_name=visitor_data.visitor_name,
        mobile_number=visitor_data.mobile_number,
        email_address=visitor_data.email_address,
        company=visitor_data.company,
        person_to_meet=visitor_data.person_to_meet,
        reason_to_visit=visitor_data.reason_to_visit,
        warehouse=visitor_data.warehouse,
        health_declaration=health_declaration_json,
        status=VisitorStatus.WAITING
    )

    db.add(new_visitor)
    db.commit()
    db.refresh(new_visitor)

    logger.info(f"[Google Form] Visitor record created successfully!")
    print(f"[Google Form] Visitor record created successfully! id={new_visitor.id}", flush=True)
    logger.info(f"[Google Form] Visitor ID: {new_visitor.id}")
    logger.info(f"[Google Form] Visitor Number: {new_visitor.check_in_time.strftime('%Y%m%d%H%M%S')}")
    logger.info(f"[Google Form] Status: {new_visitor.status}")
    logger.info(f"[Google Form] Matched Approver: {approver.name} (Username: {approver.username})")
    logger.info(f"[Google Form] Approver Phone: {approver.ph_no if approver.ph_no else 'Not available'}")
    logger.info("=" * 80)
    print(f"[Google Form] Approver Phone: {approver.ph_no if approver.ph_no else 'Not available'}", flush=True)

    # Enrich with contact information
    visitor_response = enrich_visitor_with_contact(new_visitor, db)

    # Send SMS notification asynchronously (non-blocking)
    def send_sms_background(visitor_id: int, person_to_meet: str, visitor_name: str, 
                           mobile: str, email: Optional[str], company: Optional[str], 
                           reason: str, warehouse: Optional[str], date_of_visit: Optional[str] = None,
                           time_slot: Optional[str] = None):
        """Background task to send SMS without blocking the response."""
        logger.info(f"[SMS] Starting SMS background task for visitor {visitor_id}, person_to_meet: {person_to_meet}")
        try:
            from app.core.database import SessionLocal
            db_session = SessionLocal()
            try:
                logger.info(f"[SMS] Searching for approver with username or name: {person_to_meet}")
                approver = _find_approver_for_notification(db_session, person_to_meet)
                
                if approver:
                    logger.info(f"[SMS] Found approver: {approver.username} (name: {approver.name}), phone: {approver.ph_no}")
                    superuser_phones = _get_superuser_phone_numbers(db_session)
                    target_phones: List[str] = []
                    if approver.ph_no:
                        target_phones.append(approver.ph_no)
                    for p in superuser_phones:
                        if p not in target_phones:
                            target_phones.append(p)

                    if target_phones:
                        for to_phone in target_phones:
                            logger.info(f"[WA] Attempting to send WhatsApp to {to_phone}")
                            wa_sent = whatsapp_service.send_visitor_approval_request(
                                to_phone=to_phone,
                                visitor_name=visitor_name,
                                visitor_mobile=mobile,
                                visitor_email=email,
                                visitor_company=company,
                                reason_for_visit=reason,
                                visitor_id=str(visitor_id),
                                warehouse=warehouse,
                                person_to_meet_name=approver.name,
                            )
                            if wa_sent:
                                logger.info(f"[WA] WhatsApp notification sent successfully to {to_phone} for visitor {visitor_id}")
                            else:
                                logger.warning(f"[WA] Failed to send WhatsApp notification to {to_phone} for visitor {visitor_id}")
                    else:
                        logger.warning(f"[WA] Approver '{person_to_meet}' found but has no phone number, and no superuser phones configured")
                else:
                    logger.warning(f"[SMS] Approver '{person_to_meet}' not found in database. SMS not sent.")
                    all_approvers = db_session.query(Approver).all()
                    logger.info(f"[SMS] Available approvers in DB: {[(a.username, a.name) for a in all_approvers[:10]]}")
            finally:
                db_session.close()
        except Exception as e:
            logger.error(f"[SMS] ✗ Error sending SMS notification in background: {e}", exc_info=True)
    
    # Extract date and time slot from health_declaration for SMS
    appointment_date = None
    appointment_time = None
    try:
        health_data = json.loads(health_declaration_json)
        appointment_date = health_data.get('date_of_visit')
        appointment_time = health_data.get('time_slot') or form_data.preferred_time_slot
    except:
        appointment_time = form_data.preferred_time_slot
    
    logger.info(f"[SMS] Adding background task to send SMS for visitor {new_visitor.id}, person_to_meet: {new_visitor.person_to_meet}, date: {appointment_date}, time: {appointment_time}")
    background_tasks.add_task(
        send_sms_background,
        new_visitor.id,
        new_visitor.person_to_meet,
        new_visitor.visitor_name,
        new_visitor.mobile_number,
        new_visitor.email_address,
        new_visitor.company,
        new_visitor.reason_to_visit,
        new_visitor.warehouse,
        appointment_date,
        appointment_time
    )

    return VisitorCheckInResponse(
        message="Visitor check-in created successfully from Google Form",
        visitor=VisitorResponse(**visitor_response)
    )


@router.get("/today/active", response_model=List[VisitorResponse], status_code=status.HTTP_200_OK)
def get_today_active_visitors(
    db: Session = Depends(get_db),
    current_user: Approver = Depends(get_current_approver)
):
    """
    Get all active visitors for today (waiting or approved). Requires authentication.

    Args:
        db: Database session
        current_user: Current authenticated approver

    Returns:
        List of active visitors checked in today
    """
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    visitors = db.query(Visitor).filter(
        Visitor.check_in_time >= today_start,
        Visitor.status.in_([VisitorStatus.WAITING, VisitorStatus.APPROVED])
    ).order_by(Visitor.check_in_time.desc()).all()

    # Enrich with contact information
    enriched_visitors = [enrich_visitor_with_contact(visitor, db) for visitor in visitors]

    return [VisitorResponse.model_validate(visitor_data) for visitor_data in enriched_visitors]
