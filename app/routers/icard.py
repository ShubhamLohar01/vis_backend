from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone

from app.core.database import get_db
from app.core.auth import get_current_approver
from app.models.approver import Approver
from app.models.icard import ICard
from app.models.visitor import Visitor
from app.schemas.icard import (
    ICardCreate,
    ICardUpdate,
    ICardAssign,
    ICardResponse,
    ICardListResponse,
    ICardStatsResponse,
    VisitorCardResponse,
)


router = APIRouter(prefix="/api/icards", tags=["ICards"])


@router.post("/", response_model=ICardResponse, status_code=status.HTTP_201_CREATED)
def create_icard(
    icard_data: ICardCreate,
    db: Session = Depends(get_db),
    current_user: Approver = Depends(get_current_approver)
):
    """
    Create a new ICard. Requires authentication.

    Args:
        icard_data: ICard creation data
        db: Database session
        current_user: Current authenticated approver

    Returns:
        Created ICard information

    Raises:
        HTTPException: If card name already exists
    """
    # Check if card name already exists
    existing_card = db.query(ICard).filter(ICard.card_name == icard_data.card_name).first()
    if existing_card:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Card with name '{icard_data.card_name}' already exists"
        )

    # Create new card
    new_card = ICard(
        card_name=icard_data.card_name,
        occ_status=icard_data.occ_status,
        occ_to=icard_data.occ_to
    )

    db.add(new_card)
    db.commit()
    db.refresh(new_card)

    return ICardResponse.model_validate(new_card)


@router.get("/", response_model=ICardListResponse, status_code=status.HTTP_200_OK)
def get_all_icards(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(100, ge=1, le=100, description="Number of items per page"),
    occ_status: Optional[bool] = Query(None, description="Filter by occupation status"),
    search: Optional[str] = Query(None, description="Search by card name"),
    db: Session = Depends(get_db),
    current_user: Approver = Depends(get_current_approver)
):
    """
    Get all ICards with pagination and filters. Requires authentication.

    Args:
        page: Page number (starts from 1)
        page_size: Number of items per page (default: 100)
        occ_status: Optional filter by occupation status
        search: Optional search by card name
        db: Database session
        current_user: Current authenticated approver

    Returns:
        Paginated list of ICards
    """
    query = db.query(ICard)

    # Apply occupation status filter
    if occ_status is not None:
        query = query.filter(ICard.occ_status == occ_status)

    # Apply search filter
    if search:
        search_term = f"%{search}%"
        query = query.filter(ICard.card_name.ilike(search_term))

    # Get total count
    total = query.count()

    # Apply pagination
    offset = (page - 1) * page_size
    cards = query.order_by(ICard.card_name).offset(offset).limit(page_size).all()

    return ICardListResponse(
        total=total,
        cards=[ICardResponse.model_validate(card) for card in cards],
        page=page,
        page_size=page_size
    )


@router.get("/stats", response_model=ICardStatsResponse, status_code=status.HTTP_200_OK)
def get_icard_stats(
    db: Session = Depends(get_db),
    current_user: Approver = Depends(get_current_approver)
):
    """
    Get ICard statistics. Requires authentication.

    Args:
        db: Database session
        current_user: Current authenticated approver

    Returns:
        ICard statistics
    """
    total_cards = db.query(ICard).count()
    available_cards = db.query(ICard).filter(ICard.occ_status == False).count()
    occupied_cards = db.query(ICard).filter(ICard.occ_status == True).count()

    return ICardStatsResponse(
        total_cards=total_cards,
        available_cards=available_cards,
        occupied_cards=occupied_cards
    )


@router.get("/available", response_model=List[ICardResponse], status_code=status.HTTP_200_OK)
def get_available_icards(
    db: Session = Depends(get_db),
    current_user: Approver = Depends(get_current_approver)
):
    """
    Get all available (unoccupied) ICards. Requires authentication.

    Args:
        db: Database session
        current_user: Current authenticated approver

    Returns:
        List of available ICards
    """
    cards = db.query(ICard).filter(ICard.occ_status == False).order_by(ICard.card_name).all()
    return [ICardResponse.model_validate(card) for card in cards]


@router.get("/visitor/{visitor_id}/card", response_model=VisitorCardResponse, status_code=status.HTTP_200_OK)
def get_visitor_card(
    visitor_id: str,
    db: Session = Depends(get_db)
):
    """
    Get the card assigned to a specific visitor by visitor ID. This is a public endpoint.

    Args:
        visitor_id: Visitor ID in YYYYMMDDHHMMSS format (e.g., 20251125143000)
        db: Database session

    Returns:
        Card information assigned to the visitor (card_name and card_id)

    Raises:
        HTTPException: If visitor ID format is invalid
    """
    # Import visitor validation function
    from app.routers.visitor import validate_visitor_id

    # Validate and convert visitor ID
    visitor_id_int = validate_visitor_id(visitor_id)

    # Find the card assigned to this visitor
    card = db.query(ICard).filter(
        ICard.occ_to == visitor_id_int,
        ICard.occ_status == True
    ).first()

    if card:
        return VisitorCardResponse(
            visitor_id=visitor_id_int,
            card_name=card.card_name,
            card_id=card.id
        )
    else:
        # No card assigned to this visitor
        return VisitorCardResponse(
            visitor_id=visitor_id_int,
            card_name=None,
            card_id=None
        )


@router.get("/{card_id}", response_model=ICardResponse, status_code=status.HTTP_200_OK)
def get_icard_by_id(
    card_id: int,
    db: Session = Depends(get_db),
    current_user: Approver = Depends(get_current_approver)
):
    """
    Get a specific ICard by ID. Requires authentication.

    Args:
        card_id: ID of the card to retrieve
        db: Database session
        current_user: Current authenticated approver

    Returns:
        ICard information

    Raises:
        HTTPException: If card not found
    """
    card = db.query(ICard).filter(ICard.id == card_id).first()

    if not card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Card with ID {card_id} not found"
        )

    return ICardResponse.model_validate(card)


@router.put("/{card_id}", response_model=ICardResponse, status_code=status.HTTP_200_OK)
def update_icard(
    card_id: int,
    icard_data: ICardUpdate,
    db: Session = Depends(get_db),
    current_user: Approver = Depends(get_current_approver)
):
    """
    Update ICard information. Requires authentication.

    Args:
        card_id: ID of the card to update
        icard_data: Updated card data
        db: Database session
        current_user: Current authenticated approver

    Returns:
        Updated ICard information

    Raises:
        HTTPException: If card not found or card name already exists
    """
    card = db.query(ICard).filter(ICard.id == card_id).first()

    if not card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Card with ID {card_id} not found"
        )

    # Check if new card name already exists (if card_name is being updated)
    if icard_data.card_name and icard_data.card_name != card.card_name:
        existing_card = db.query(ICard).filter(ICard.card_name == icard_data.card_name).first()
        if existing_card:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Card with name '{icard_data.card_name}' already exists"
            )

    # Update fields
    update_data = icard_data.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(card, field, value)

    db.commit()
    db.refresh(card)

    return ICardResponse.model_validate(card)


@router.post("/{card_id}/assign", response_model=ICardResponse, status_code=status.HTTP_200_OK)
def assign_icard(
    card_id: int,
    assign_data: ICardAssign,
    db: Session = Depends(get_db),
    current_user: Approver = Depends(get_current_approver)
):
    """
    Assign an ICard to a visitor. Requires authentication.

    Args:
        card_id: ID of the card to assign
        assign_data: Assignment data (visitor_id)
        db: Database session
        current_user: Current authenticated approver

    Returns:
        Updated ICard information

    Raises:
        HTTPException: If card not found or already occupied
    """
    card = db.query(ICard).filter(ICard.id == card_id).first()

    if not card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Card with ID {card_id} not found"
        )

    if card.occ_status:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Card '{card.card_name}' is already occupied"
        )

    # Check if visitor already has a card assigned
    existing_assignment = db.query(ICard).filter(
        ICard.occ_to == assign_data.visitor_id,
        ICard.occ_status == True
    ).first()
    
    if existing_assignment:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Visitor already has an ICard assigned: '{existing_assignment.card_name}'. Please release the existing card before assigning a new one."
        )

    # Assign card to visitor
    card.occ_status = True
    card.occ_to = assign_data.visitor_id

    db.commit()
    db.refresh(card)

    return ICardResponse.model_validate(card)


@router.post("/{card_id}/release", response_model=ICardResponse, status_code=status.HTTP_200_OK)
def release_icard(
    card_id: int,
    db: Session = Depends(get_db),
    current_user: Approver = Depends(get_current_approver)
):
    """
    Release an ICard from a visitor. Requires authentication.
    Automatically updates the visitor's check_out_time to current timestamp.

    Args:
        card_id: ID of the card to release
        db: Database session
        current_user: Current authenticated approver

    Returns:
        Updated ICard information

    Raises:
        HTTPException: If card not found or not occupied
    """
    card = db.query(ICard).filter(ICard.id == card_id).first()

    if not card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Card with ID {card_id} not found"
        )

    if not card.occ_status:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Card '{card.card_name}' is not occupied"
        )

    # Update visitor's check_out_time
    visitor_id = card.occ_to
    if visitor_id:
        visitor = db.query(Visitor).filter(Visitor.id == visitor_id).first()
        if visitor:
            visitor.check_out_time = datetime.now(timezone.utc)

    # Release card
    card.occ_status = False
    card.occ_to = None

    db.commit()
    db.refresh(card)

    return ICardResponse.model_validate(card)


@router.delete("/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_icard(
    card_id: int,
    db: Session = Depends(get_db),
    current_user: Approver = Depends(get_current_approver)
):
    """
    Delete an ICard. Requires authentication.

    Args:
        card_id: ID of the card to delete
        db: Database session
        current_user: Current authenticated approver

    Raises:
        HTTPException: If card not found
    """
    card = db.query(ICard).filter(ICard.id == card_id).first()

    if not card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Card with ID {card_id} not found"
        )

    db.delete(card)
    db.commit()

    return None
