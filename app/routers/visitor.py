from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone, date
from concurrent.futures import ThreadPoolExecutor
import threading
import re
import logging
import io

from app.core.database import get_db
from app.core.auth import get_current_approver
from app.models.approver import Approver
from app.models.visitor import Visitor, VisitorStatus
from app.models.appointment import Appointment
from app.models.otp import OtpCode
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
        # Always re-sign the URL before sending it out. The DB stores a presigned
        # URL with ExpiresIn=7 days, so anything older has a dead URL and the
        # frontend would show the grey silhouette. refresh_presigned_url uses the
        # stored URL only as a source of the S3 object key, then mints a fresh
        # 7-day URL using our IAM credentials. No-op for None / non-S3 URLs.
        "img_url": s3_service.refresh_presigned_url(visitor.img_url),
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
    # On revisit the visitor doesn't re-enter email / company / selfie — we
    # reuse what was stored on the previous visit. These are therefore optional.
    email_address: Optional[str] = Form(None, description="Email address (required for first-time visitors)"),
    company: Optional[str] = Form(None, description="Company name (required for first-time visitors)"),
    warehouse: Optional[str] = Form(None, max_length=255, description="Warehouse location"),
    health_declaration: Optional[str] = Form(None, description="Health & safety declaration as JSON string"),
    image: Optional[UploadFile] = File(None, description="Visitor image file (required for first-time visitors)"),
    is_revisit: Optional[str] = Form(None, description='"true" if reusing details from a previous visit'),
    db: Session = Depends(get_db),
):
    """
    Check in a visitor — either a brand-new visitor (with a selfie upload) or
    a returning one (identified by phone via the OTP flow), in which case we
    reuse the selfie/email/company from the most recent prior visit.

    Behavior:
      - First-time:  `image` is required; goes through S3 upload + WhatsApp approval as before.
      - Revisit:     `image` may be omitted; we look up the most recent visit by
                     mobile_number and copy across img_url, email, company so the
                     new check-in row has the same supporting context as the old one.

    Args:
        visitor_name, mobile_number, person_to_meet, reason_to_visit: required.
        email_address, company: required for first-time visitors. Optional on
            revisit (looked up from prior visit if omitted).
        warehouse, health_declaration: optional.
        image: required for first-time visitors. Optional on revisit (reuses prior
            visit's S3 URL — no new upload).
        is_revisit: "true" enables the revisit path. Any other value (or omitted)
            falls back to first-time validation.

    Raises:
        HTTPException: invalid image format/size, or missing fields the chosen
        path requires (e.g., image missing on first-time check-in).
    """
    is_revisit_flag = (is_revisit or "").strip().lower() == "true"

    # Look up prior visit on revisit to copy missing context across.
    prior_visit: Optional[Visitor] = None
    if is_revisit_flag:
        last10 = _normalize_phone_for_otp(mobile_number)
        prior_visit = (
            db.query(Visitor)
            .filter(Visitor.mobile_number.like(f"%{last10}") if last10 else False)
            .order_by(Visitor.check_in_time.desc())
            .first()
        )
        if not prior_visit:
            # OTP succeeded but the row is gone — refuse rather than create a
            # half-formed record without a selfie.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No previous visit found for this number. Please register as a first-time visitor.",
            )

    # Image handling depends on path.
    file_content: Optional[bytes] = None
    image_content_type: Optional[str] = None

    if image is not None and image.filename:
        # An image was actually uploaded — validate it the same way regardless of path.
        allowed_content_types = ["image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp"]
        if image.content_type not in allowed_content_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid image format. Allowed formats: {', '.join(allowed_content_types)}",
            )
        max_file_size = 10 * 1024 * 1024  # 10 MB
        file_content = await image.read()
        if len(file_content) > max_file_size:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Image file size exceeds 10MB limit",
            )
        image_content_type = image.content_type
    elif not is_revisit_flag:
        # First-time visitor with no image — that's not allowed.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Selfie image is required for first-time visitors.",
        )

    # Fill in revisit-only defaults from the prior visit.
    if is_revisit_flag and prior_visit is not None:
        if not email_address:
            email_address = prior_visit.email_address
        if not company:
            company = prior_visit.company
        if not warehouse:
            warehouse = prior_visit.warehouse

    if not email_address:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="email_address is required.",
        )
    if not company:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="company is required.",
        )

    # Create new visitor first to get the auto-generated ID. On revisit we set
    # img_url right away from the prior visit so the visitor pass shows the
    # selfie immediately even though the rest is still WAITING.
    new_visitor = Visitor(
        visitor_name=visitor_name,
        mobile_number=mobile_number,
        email_address=email_address,
        company=company,
        person_to_meet=person_to_meet,
        reason_to_visit=reason_to_visit,
        warehouse=warehouse,
        health_declaration=health_declaration,
        img_url=prior_visit.img_url if is_revisit_flag and prior_visit else None,
        status=VisitorStatus.WAITING,
    )

    db.add(new_visitor)
    db.commit()
    db.refresh(new_visitor)

    # Generate visitor number in YYYYMMDDHHMMSS format using the check_in_time
    check_in_time = new_visitor.check_in_time
    visitor_number = check_in_time.strftime("%Y%m%d%H%M%S")

    # Resolve approver + superusers now (while DB session is open)
    approver = _find_approver_for_notification(db, person_to_meet)
    superuser_phones = _get_superuser_phone_numbers(db)
    target_phones: List[str] = []
    if approver and approver.ph_no:
        target_phones.append(approver.ph_no)
    for p in superuser_phones:
        if p not in target_phones:
            target_phones.append(p)
    approver_name = approver.name if approver else person_to_meet
    visitor_id_int = new_visitor.id

    # Enrich for response (img_url will be None initially; updated in background)
    visitor_data = enrich_visitor_with_contact(new_visitor, db)

    # Run S3 upload + WhatsApp send IN PARALLEL but SYNCHRONOUSLY before returning.
    #
    # Previously this used `threading.Thread(..., daemon=True)` which works locally
    # under uvicorn but is unreliable on Lambda: the container freezes once the
    # response is returned, killing the daemon thread mid-send so the host never
    # gets the WhatsApp message. Running them in a blocking ThreadPoolExecutor
    # adds ~3-5s to the response but guarantees both tasks complete before
    # Lambda freezes.
    existing_image_url = (
        prior_visit.img_url if (is_revisit_flag and prior_visit) else None
    )

    # On revisit we don't get a fresh selfie upload, so file_content is None.
    # If we just passed the prior visit's S3 URL through to Meta as a link,
    # Meta would have to fetch it itself — and that URL is a presigned URL
    # that expires after 7 days (and the bucket isn't necessarily public),
    # so anything older than a week silently fails to deliver.
    #
    # Instead, fetch the bytes directly from S3 using our IAM credentials
    # (same access the rest of the app already needs). That always works as
    # long as the object still exists in S3, regardless of URL age. Then we
    # feed the bytes through the same Media API upload path that first-time
    # check-ins use.
    if is_revisit_flag and file_content is None and existing_image_url:
        result = s3_service.download_visitor_image(existing_image_url)
        if result is not None:
            file_content, image_content_type = result
            logger.info(
                f"[CHECK-IN] Loaded prior selfie via S3 for revisit "
                f"({len(file_content)} bytes, {image_content_type})"
            )
        else:
            logger.warning(
                f"[CHECK-IN] Could not load prior selfie via S3 for revisit; "
                f"WhatsApp will fall back to the stored URL or default image."
            )

    def _send_whatsapp():
        if not target_phones:
            logger.warning(f"[CHECK-IN] No target phones for visitor {visitor_id_int}")
            return
        for phone in target_phones:
            try:
                sent = whatsapp_service.send_visitor_approval_request(
                    to_phone=phone,
                    visitor_name=visitor_name,
                    visitor_mobile=mobile_number,
                    visitor_email=email_address,
                    visitor_company=company,
                    reason_for_visit=reason_to_visit,
                    visitor_id=str(visitor_id_int),
                    warehouse=warehouse,
                    person_to_meet_name=approver_name,
                    image_bytes=file_content,
                    image_content_type=image_content_type or "image/jpeg",
                    visitor_image_url=existing_image_url,
                )
                logger.info(f"[CHECK-IN] WhatsApp {'sent' if sent else 'failed'} to {phone}")
            except Exception as e:
                logger.error(f"[CHECK-IN] WhatsApp error to {phone}: {e}", exc_info=True)

    def _upload_s3_and_update_db():
        # On revisit we may have downloaded the prior selfie just to feed it to
        # WhatsApp Media — but the new visitor row already has its img_url set
        # from prior_visit, so don't re-upload to S3.
        if (not file_content) or is_revisit_flag:
            logger.info(
                f"[CHECK-IN] Skipping S3 upload for visitor {visitor_id_int} — "
                f"{'revisit reuses existing image URL' if is_revisit_flag else 'no image to upload'}."
            )
            return
        try:
            logger.info(f"[CHECK-IN] Starting S3 upload for visitor {visitor_number}")
            img_url = s3_service.upload_visitor_image(file_content, visitor_number, image_content_type)
            logger.info(f"[CHECK-IN] S3 upload done: {img_url}")
        except Exception as e:
            logger.error(f"[CHECK-IN] S3 upload failed: {e}", exc_info=True)
            return
        try:
            from app.core.database import SessionLocal
            db_bg = SessionLocal()
            try:
                v = db_bg.query(Visitor).filter(Visitor.id == visitor_id_int).first()
                if v:
                    v.img_url = img_url
                    db_bg.commit()
                    # Reflect the new URL on the response object too.
                    visitor_data["img_url"] = img_url
                    logger.info(f"[CHECK-IN] img_url saved to DB for visitor {visitor_id_int}")
            finally:
                db_bg.close()
        except Exception as e:
            logger.error(f"[CHECK-IN] DB update failed: {e}", exc_info=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_send_whatsapp), pool.submit(_upload_s3_and_update_db)]
        for f in futures:
            try:
                # Hard cap each task at 25s so a hung Meta/S3 call can't push
                # the handler past API Gateway's 29s timeout.
                f.result(timeout=25)
            except Exception as e:
                logger.error(f"[CHECK-IN] Parallel task error: {e}", exc_info=True)

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

    # Enrich with contact information, then validate each row individually so that
    # a single malformed legacy record cannot 500 the whole page.
    response_visitors = []
    skipped = 0
    with_image = 0
    without_image = 0
    for visitor in visitors:
        try:
            visitor_data = enrich_visitor_with_contact(visitor, db)
            response_visitors.append(VisitorResponse.model_validate(visitor_data))
            if visitor_data.get("img_url"):
                with_image += 1
            else:
                without_image += 1
        except Exception as e:
            skipped += 1
            logger.warning(
                f"[visitors/list] Skipping visitor id={getattr(visitor, 'id', '?')} "
                f"due to validation error: {e}"
            )

    if skipped:
        logger.warning(f"[visitors/list] Skipped {skipped} unvalidatable row(s) on page {page}")

    # Aggregate summary so the console isn't drowning in per-row [IMG] lines.
    logger.info(
        f"[visitors/list] page={page} returned={len(response_visitors)} "
        f"with_image={with_image} without_image={without_image}"
    )

    return VisitorListResponse(
        total=total,
        visitors=response_visitors,
        page=page,
        page_size=page_size,
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


# ---------------------------------------------------------------------------
# Revisit OTP (visitor_revisit_otp template)
# ---------------------------------------------------------------------------
# Flow: a returning visitor enters their phone on the register page; we look
# up the most recent visit, generate a 6-digit code, store it in vis_otp_codes
# with a 5-minute TTL, and send it via the visitor_revisit_otp WhatsApp
# template. They then enter the code, we verify, and the frontend routes to
# /revisit?phone=... where prior details are pre-filled.
# ---------------------------------------------------------------------------

OTP_TTL_MINUTES = 5
OTP_MAX_ATTEMPTS = 5


def _normalize_phone_for_otp(phone: str) -> str:
    """Strip non-digits and take the last 10 digits to match how visitors are stored."""
    digits = ''.join(ch for ch in (phone or '') if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


class SendOtpRequest(BaseModel):
    phone: str = Field(..., min_length=10, max_length=20)


class SendOtpResponse(BaseModel):
    sent: bool
    message: str
    expires_in_seconds: int = OTP_TTL_MINUTES * 60


class VerifyOtpRequest(BaseModel):
    phone: str = Field(..., min_length=10, max_length=20)
    otp: str = Field(..., min_length=4, max_length=10)


class VerifyOtpResponse(BaseModel):
    verified: bool
    message: str


@router.post("/send-revisit-otp", response_model=SendOtpResponse, status_code=status.HTTP_200_OK)
def send_revisit_otp(payload: SendOtpRequest, db: Session = Depends(get_db)):
    """
    Generate and send a one-time code for the 'Visited us before?' quick check-in.

    Returns 404 if the phone has no prior visit. Overwrites any existing OTP for
    the same phone so the latest code is always authoritative.
    """
    import secrets
    from datetime import timedelta

    phone = _normalize_phone_for_otp(payload.phone)
    if len(phone) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide a valid 10-digit mobile number.",
        )

    # Visitor must have a prior visit on record; otherwise this isn't a "revisit".
    prior_visit = (
        db.query(Visitor)
        .filter(Visitor.mobile_number.like(f"%{phone}"))
        .order_by(Visitor.check_in_time.desc())
        .first()
    )
    if not prior_visit:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No previous visit found for this number. Please register as a first-time visitor.",
        )

    # Generate 6-digit code (000000 - 999999), zero-padded.
    # Use timezone-aware UTC so comparisons against the DB column
    # (DateTime(timezone=True)) don't raise TypeError later.
    otp_code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_TTL_MINUTES)

    existing = db.query(OtpCode).filter(OtpCode.phone == phone).first()
    if existing:
        existing.otp = otp_code
        existing.expires_at = expires_at
        existing.attempts = 0
    else:
        db.add(OtpCode(phone=phone, otp=otp_code, expires_at=expires_at, attempts=0))

    db.commit()

    # Best-effort cleanup of stale rows so the table doesn't grow forever.
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        db.query(OtpCode).filter(OtpCode.expires_at < cutoff).delete()
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"[OTP] Cleanup of expired codes failed: {e}")

    # Send the WhatsApp message — use the visitor's actual stored number (with
    # country code etc.) rather than the trimmed key, in case the template
    # service formats it differently.
    to_phone = prior_visit.mobile_number or phone
    if not whatsapp_service.enabled:
        # Returning 503 so the frontend's "if (!res.ok)" branch shows the user
        # a clear error instead of silently advancing them to the OTP screen.
        logger.warning(
            f"[OTP] WhatsApp service is disabled — refusing to send OTP to {to_phone}. "
            f"Check WHATSAPP_ENABLED / WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID env vars."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "OTP service is currently unavailable. Please register as a "
                "first-time visitor below, or try again later."
            ),
        )

    try:
        ok = whatsapp_service.send_otp_notification(to_phone=to_phone, otp_code=otp_code)
    except Exception as e:
        logger.error(f"[OTP] send_otp_notification raised: {e}", exc_info=True)
        ok = False

    if not ok:
        # Don't leak the code on failure; the visitor can request another.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not send OTP via WhatsApp. Please try again or register as a first-time visitor.",
        )

    return SendOtpResponse(
        sent=True,
        message=f"OTP sent on WhatsApp to {to_phone[-4:].rjust(len(to_phone), '*')}.",
    )


@router.post("/verify-revisit-otp", response_model=VerifyOtpResponse, status_code=status.HTTP_200_OK)
def verify_revisit_otp(payload: VerifyOtpRequest, db: Session = Depends(get_db)):
    """
    Validate a previously-issued revisit OTP.

    On success the row is deleted so the code can't be reused; on the
    OTP_MAX_ATTEMPTS-th wrong guess the row is also deleted to force a fresh
    send (limits brute force).
    """
    phone = _normalize_phone_for_otp(payload.phone)
    if len(phone) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide a valid 10-digit mobile number.",
        )

    row = db.query(OtpCode).filter(OtpCode.phone == phone).first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No OTP requested for this number. Please request a new code.",
        )

    # Normalise both sides to UTC-aware. Postgres returns timezone-aware datetimes
    # (DateTime(timezone=True) column), so comparing against datetime.utcnow()
    # (naive) raises TypeError and bubbles up as a 500.
    row_expiry = row.expires_at
    if row_expiry.tzinfo is None:
        row_expiry = row_expiry.replace(tzinfo=timezone.utc)
    if row_expiry < datetime.now(timezone.utc):
        db.delete(row)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This OTP has expired. Please request a new code.",
        )

    submitted = (payload.otp or "").strip()
    if submitted != row.otp:
        row.attempts = (row.attempts or 0) + 1
        if row.attempts >= OTP_MAX_ATTEMPTS:
            db.delete(row)
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many incorrect attempts. Please request a new code.",
            )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Incorrect OTP. {OTP_MAX_ATTEMPTS - row.attempts} attempt(s) remaining.",
        )

    # Success — consume the code so it can't be reused.
    db.delete(row)
    db.commit()
    return VerifyOtpResponse(verified=True, message="Verification successful.")


@router.get("/export", status_code=status.HTTP_200_OK)
def export_visitors_excel(
    from_date: date = Query(..., description="Start date (YYYY-MM-DD)"),
    to_date: date = Query(..., description="End date (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
    current_user: Approver = Depends(get_current_approver),
):
    """
    Export visitor data as Excel file for a given date range.
    Requires superuser authentication.
    Excludes img_url, created_at, and updated_at columns.
    """
    if not current_user.superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only superusers can export visitor data"
        )
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    start_dt = datetime.combine(from_date, datetime.min.time())
    end_dt = datetime.combine(to_date, datetime.max.time())

    visitors = (
        db.query(Visitor)
        .filter(Visitor.check_in_time >= start_dt, Visitor.check_in_time <= end_dt)
        .order_by(Visitor.check_in_time.desc())
        .all()
    )

    columns = [
        ("ID", "id"),
        ("Visitor Name", "visitor_name"),
        ("Mobile Number", "mobile_number"),
        ("Email Address", "email_address"),
        ("Company", "company"),
        ("Person To Meet", "person_to_meet"),
        ("Reason To Visit", "reason_to_visit"),
        ("Warehouse", "warehouse"),
        ("Health Declaration", "health_declaration"),
        ("Carrying Electronics", "carrying_electronics"),
        ("Electronics Items", "electronics_items"),
        ("Status", "status"),
        ("Rejection Reason", "rejection_reason"),
        ("Check In Time", "check_in_time"),
        ("Check Out Time", "check_out_time"),
    ]

    wb = Workbook()
    ws = wb.active
    ws.title = "Visitors"

    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center")
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    for col_idx, (header, _) in enumerate(columns, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    for row_idx, visitor in enumerate(visitors, 2):
        for col_idx, (_, attr) in enumerate(columns, 1):
            value = getattr(visitor, attr, None)
            if isinstance(value, datetime):
                value = value.strftime("%Y-%m-%d %H:%M:%S")
            elif hasattr(value, "value"):
                value = value.value
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.border = thin_border

    for col_idx, (header, _) in enumerate(columns, 1):
        max_len = len(header)
        for row in ws.iter_rows(min_row=2, min_col=col_idx, max_col=col_idx):
            for cell in row:
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = min(max_len + 2, 40)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"visitors_{from_date}_{to_date}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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

    # Tolerant per-row validation — skip and log malformed rows.
    response_visitors = []
    for visitor in visitors:
        try:
            response_visitors.append(VisitorResponse.model_validate(enrich_visitor_with_contact(visitor, db)))
        except Exception as e:
            logger.warning(f"[visitors/phone] Skipping visitor id={getattr(visitor, 'id', '?')}: {e}")
    return response_visitors


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

    img = visitor_data.get("img_url")
    logger.info(
        f"[visitors/{{id}}] id={visitor_id} img_url={'set' if img else 'NULL'}"
        + (f" (stored={'yes' if visitor.img_url else 'no'})" if not img else "")
    )

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

    # Notify visitor of approve/reject decision via WhatsApp.
    # Sent synchronously (not via BackgroundTasks): on Lambda, background tasks can be
    # killed when the runtime freezes the container after returning the response, and
    # any silent failure becomes invisible. Synchronous + explicit logging lets us see
    # exactly which step failed.
    if status_data.status in (VisitorStatus.APPROVED, VisitorStatus.REJECTED):
        is_approval = status_data.status == VisitorStatus.APPROVED
        label = "approval" if is_approval else "rejection"
        visitor_number = visitor.check_in_time.strftime("%Y%m%d%H%M%S")

        if not whatsapp_service.enabled:
            logger.warning(
                f"[WA] Service disabled — skipping {label} notification for visitor {visitor_id}. "
                f"Check WHATSAPP_ENABLED / WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID."
            )
        elif not visitor.mobile_number:
            logger.warning(
                f"[WA] Visitor {visitor_id} ({visitor.visitor_name}) has no mobile_number — "
                f"skipping {label} notification."
            )
        else:
            logger.info(
                f"[WA] Sending {label} WhatsApp to {visitor.visitor_name} at "
                f"{visitor.mobile_number} (cn={visitor_number})"
            )
            try:
                if is_appointment:
                    if is_approval:
                        # qr_code is set above when an appointment is approved (CONFIRMED branch)
                        wa_sent = whatsapp_service.send_appointment_approved_notification(
                            to_phone=visitor.mobile_number,
                            visitor_name=visitor.visitor_name,
                            date_of_visit=date_of_visit,
                            time_slot=time_slot,
                            qr_code=qr_code if 'qr_code' in locals() else visitor_number,
                            visitor_id_str=visitor_number,
                        )
                    else:
                        wa_sent = whatsapp_service.send_appointment_rejected_notification(
                            to_phone=visitor.mobile_number,
                            visitor_name=visitor.visitor_name,
                            date_of_visit=date_of_visit,
                            time_slot=time_slot,
                            rejection_reason=(getattr(status_data, 'rejection_reason', None) or visitor.rejection_reason),
                            visitor_id_str=visitor_number,
                        )
                elif is_approval:
                    wa_sent = whatsapp_service.send_approval_notification(
                        to_phone=visitor.mobile_number,
                        visitor_id_str=visitor_number,
                    )
                else:
                    wa_sent = whatsapp_service.send_rejection_notification(
                        to_phone=visitor.mobile_number,
                        visitor_id_str=visitor_number,
                    )
                if wa_sent:
                    logger.info(f"[WA] {label.capitalize()} sent OK to {visitor.mobile_number}")
                else:
                    logger.warning(
                        f"[WA] {label.capitalize()} send returned False for {visitor.mobile_number} — "
                        f"check Meta API error above."
                    )
            except Exception as e:
                logger.error(f"[WA] Error sending {label} WhatsApp: {e}", exc_info=True)

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
                            # Google-form submissions are always appointments -> send the
                            # appointment-specific approval template carrying date/time.
                            wa_sent = whatsapp_service.send_appointment_approval_request(
                                to_phone=to_phone,
                                visitor_name=visitor_name,
                                company=company,
                                purpose=reason.replace("[APPOINTMENT] ", ""),
                                date_of_visit=date_of_visit,
                                time_slot=time_slot,
                                visitor_id=str(visitor_id),
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

    # Tolerant per-row validation — skip and log malformed rows.
    response_visitors = []
    for visitor in visitors:
        try:
            response_visitors.append(VisitorResponse.model_validate(enrich_visitor_with_contact(visitor, db)))
        except Exception as e:
            logger.warning(f"[visitors/today] Skipping visitor id={getattr(visitor, 'id', '?')}: {e}")
    return response_visitors
