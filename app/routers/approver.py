from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.database import get_db
from app.core.auth import AuthUtils, get_current_approver, get_current_superuser
from app.models.approver import Approver
from app.models.admin import Admin
from app.schemas.approver import (
    ApproverCreate,
    ApproverUpdate,
    ApproverResponse,
    ApproverLogin,
    ApproverLoginResponse,
    ApproverSimple,
    Token,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
)


router = APIRouter(prefix="/api/approvers", tags=["Approvers"])


@router.post("/login", response_model=ApproverLoginResponse, status_code=status.HTTP_200_OK)
def login(
    login_data: ApproverLogin,
    db: Session = Depends(get_db)
):
    """
    Authenticate an approver with username and plain text password.

    The password is received in plain text (via JSON) and verified against the
    bcrypt-encrypted password stored in the database.

    This endpoint accepts application/json data.

    Args:
        login_data: Login credentials (username and password)
        db: Database session

    Returns:
        JWT access token and approver information

    Raises:
        HTTPException: If credentials are invalid or account is inactive
    """
    username = login_data.username
    password = login_data.password

    # Try to find approver by username first
    approver = db.query(Approver).filter(Approver.username == username).first()
    
    # If not found by username, try to find by email
    if not approver:
        approver = db.query(Approver).filter(Approver.email == username).first()
    
    # If not found in approvers table, check vis_admin table
    admin = None
    if not approver:
        admin = db.query(Admin).filter(Admin.username == username).first()
        
        # If not found by username, try to find by email
        if not admin:
            admin = db.query(Admin).filter(Admin.email == username).first()
    
    # Check if user exists in either table
    if not approver and not admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password"
        )
    
    # Determine which user we're authenticating
    user = approver if approver else admin
    is_admin = admin is not None
    
    # Verify password
    if not AuthUtils.verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password"
        )

    # Check if account is active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive. Please contact administrator."
        )

    # Create access token
    access_token = AuthUtils.create_access_token(
        data={"sub": user.username, "approver_id": user.id}
    )
    
    # Convert admin to approver-like response format
    if is_admin:
        # Create a dict with approver-compatible fields for admin (exclude hashed_password)
        approver_dict = {
            "id": admin.id,
            "username": admin.username,
            "email": admin.email,
            "name": admin.name,
            "ph_no": None,  # Admin users don't have phone numbers
            "warehouse": admin.warehouse,
            "superuser": False,
            "admin": True,  # Mark as admin
            "is_active": admin.is_active,
            "created_at": admin.created_at,
            "updated_at": admin.updated_at
        }
        approver_response = ApproverResponse.model_validate(approver_dict)
    else:
        approver_response = ApproverResponse.model_validate(approver)

    return ApproverLoginResponse(
        access_token=access_token,
        token_type="bearer",
        approver=approver_response
    )


@router.post("/", response_model=ApproverResponse, status_code=status.HTTP_201_CREATED)
def create_approver(
    approver_data: ApproverCreate,
    db: Session = Depends(get_db),
    current_user: Approver = Depends(get_current_superuser)
):
    """
    Create a new approver. Only superusers can create new approvers.

    Args:
        approver_data: Approver creation data
        db: Database session
        current_user: Current authenticated superuser

    Returns:
        Created approver information

    Raises:
        HTTPException: If username or email already exists
    """
    # Check if username already exists
    existing_approver = db.query(Approver).filter(
        (Approver.username == approver_data.username) | (Approver.email == approver_data.email)
    ).first()

    if existing_approver:
        if existing_approver.username == approver_data.username:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already exists"
            )

    # Hash the password
    hashed_password = AuthUtils.hash_password(approver_data.password)

    # Create new approver
    new_approver = Approver(
        username=approver_data.username,
        email=approver_data.email,
        name=approver_data.name,
        ph_no=approver_data.ph_no,
        warehouse=approver_data.warehouse,
        hashed_password=hashed_password,
        superuser=approver_data.superuser,
        admin=approver_data.admin
    )

    try:
        db.add(new_approver)
        db.commit()
        db.refresh(new_approver)
        return ApproverResponse.model_validate(new_approver)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username or email already exists"
        )


@router.get("/", response_model=List[ApproverResponse], status_code=status.HTTP_200_OK)
def get_all_approvers(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: Approver = Depends(get_current_approver)
):
    """
    Get all approvers with pagination.

    Args:
        skip: Number of records to skip
        limit: Maximum number of records to return
        db: Database session
        current_user: Current authenticated approver

    Returns:
        List of approvers
    """
    approvers = db.query(Approver).offset(skip).limit(limit).all()
    return [ApproverResponse.model_validate(approver) for approver in approvers]


@router.get("/list", response_model=List[ApproverSimple], status_code=status.HTTP_200_OK)
def get_approvers_list(
    active_only: bool = True,
    db: Session = Depends(get_db)
):
    """
    Get a simplified list of approvers (username, name, email, phone).
    This is a public endpoint useful for dropdowns and selection lists.
    Returns approvers ordered by username.

    Args:
        active_only: If True, return only active approvers (default: True)
        db: Database session

    Returns:
        List of approvers with username, name, email, and phone number
    """
    query = db.query(Approver)

    if active_only:
        query = query.filter(Approver.is_active == True)

    approvers = query.order_by(Approver.username).all()
    return [ApproverSimple.model_validate(approver) for approver in approvers]


@router.get("/me", response_model=ApproverResponse, status_code=status.HTTP_200_OK)
def get_current_approver_info(
    current_user: Approver = Depends(get_current_approver)
):
    """
    Get current authenticated approver information.

    Args:
        current_user: Current authenticated approver

    Returns:
        Current approver information
    """
    return ApproverResponse.model_validate(current_user)


@router.post("/forgot-password", response_model=ForgotPasswordResponse, status_code=status.HTTP_200_OK)
def forgot_password(
    request: ForgotPasswordRequest,
    db: Session = Depends(get_db)
):
    """
    Reset password for a user without requiring old password or verification.
    Public endpoint - no authentication required.
    No email/SMS verification required.
    
    Accepts either username or email address to identify the user.

    Args:
        request: Forgot password request containing username (or email) and new password
        db: Database session

    Returns:
        Success message with username

    Raises:
        HTTPException: If approver not found or account is inactive
    """
    # Try to find approver by username first
    approver = db.query(Approver).filter(Approver.username == request.username).first()
    
    # If not found by username, try to find by email
    if not approver:
        approver = db.query(Approver).filter(Approver.email == request.username).first()

    if not approver:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with username or email '{request.username}' not found. Please check your username or email address."
        )

    # Check if account is active
    if not approver.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive. Please contact administrator."
        )

    # Hash the new password
    hashed_password = AuthUtils.hash_password(request.new_password)

    # Update the password
    approver.hashed_password = hashed_password

    try:
        db.commit()
        db.refresh(approver)
        return ForgotPasswordResponse(
            message="Password has been reset successfully",
            username=approver.username
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset password: {str(e)}"
        )


@router.get("/{username}", response_model=ApproverResponse, status_code=status.HTTP_200_OK)
def get_approver_by_username(
    username: str,
    db: Session = Depends(get_db)
):
    """
    Get a specific approver by username. Public endpoint - no authentication required.

    Args:
        username: Username of the approver to retrieve (e.g., CF0001)
        db: Database session

    Returns:
        Approver information

    Raises:
        HTTPException: If approver not found
    """
    # Try to find by username first, then by name for backward compatibility
    approver = db.query(Approver).filter(
        (Approver.username == username) | (Approver.name == username)
    ).first()

    if not approver:
        # Log available approvers for debugging
        import logging
        logger = logging.getLogger(__name__)
        all_approvers = db.query(Approver).filter(Approver.is_active == True).all()
        logger.info(f"[Approver API] Available approvers: {[(a.username, a.name) for a in all_approvers[:10]]}")
        
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approver '{username}' not found"
        )

    return ApproverResponse.model_validate(approver)


@router.put("/{username}", response_model=ApproverResponse, status_code=status.HTTP_200_OK)
def update_approver(
    username: str,
    approver_data: ApproverUpdate,
    db: Session = Depends(get_db)
):
    """
    Update an approver. Public endpoint - no authentication required.

    If password is provided, it should be in plain text. It will be
    automatically encrypted using bcrypt before storing in the database.

    Args:
        username: Username of the approver to update (e.g., CF0001)
        approver_data: Updated approver data containing:
            - password (optional): Plain text password (will be encrypted)
            - name, email, ph_no, etc. (all optional)
        db: Database session

    Returns:
        Updated approver information

    Raises:
        HTTPException: If approver not found or username/email already exists
    """
    approver = db.query(Approver).filter(Approver.username == username).first()

    if not approver:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approver with username '{username}' not found"
        )

    # Check for duplicate username or email if being updated
    if approver_data.username and approver_data.username != approver.username:
        existing = db.query(Approver).filter(Approver.username == approver_data.username).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists"
            )

    if approver_data.email and approver_data.email != approver.email:
        existing = db.query(Approver).filter(Approver.email == approver_data.email).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already exists"
            )

    # Update fields
    update_data = approver_data.model_dump(exclude_unset=True)

    # Hash password if provided
    if "password" in update_data:
        update_data["hashed_password"] = AuthUtils.hash_password(update_data.pop("password"))

    for field, value in update_data.items():
        setattr(approver, field, value)

    try:
        db.commit()
        db.refresh(approver)
        return ApproverResponse.model_validate(approver)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username or email already exists"
        )


@router.delete("/{username}", status_code=status.HTTP_204_NO_CONTENT)
def delete_approver(
    username: str,
    db: Session = Depends(get_db)
):
    """
    Delete an approver. Public endpoint - no authentication required.

    Args:
        username: Username of the approver to delete (e.g., CF0001)
        db: Database session

    Raises:
        HTTPException: If approver not found
    """
    approver = db.query(Approver).filter(Approver.username == username).first()

    if not approver:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approver with username '{username}' not found"
        )

    db.delete(approver)
    db.commit()

    return None
