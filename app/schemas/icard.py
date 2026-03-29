from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from datetime import datetime


class ICardBase(BaseModel):
    """Base schema for ICard with common fields"""
    card_name: str = Field(..., min_length=1, max_length=255, description="Unique card name/identifier")
    occ_status: bool = Field(default=False, description="Card occupation status (True if occupied)")
    occ_to: Optional[int] = Field(None, description="Visitor ID the card is assigned to")


class ICardCreate(ICardBase):
    """Schema for creating a new ICard"""
    pass


class ICardUpdate(BaseModel):
    """Schema for updating ICard information"""
    card_name: Optional[str] = Field(None, min_length=1, max_length=255)
    occ_status: Optional[bool] = None
    occ_to: Optional[int] = None


class ICardAssign(BaseModel):
    """Schema for assigning a card to a visitor"""
    visitor_id: int = Field(..., description="Visitor ID to assign the card to")


class ICardRelease(BaseModel):
    """Schema for releasing a card from a visitor"""
    pass


class ICardResponse(ICardBase):
    """Schema for ICard response"""
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ICardListResponse(BaseModel):
    """Schema for paginated ICard list"""
    total: int
    cards: list[ICardResponse]
    page: int
    page_size: int


class ICardStatsResponse(BaseModel):
    """Schema for ICard statistics"""
    total_cards: int
    available_cards: int
    occupied_cards: int


class VisitorCardResponse(BaseModel):
    """Schema for visitor's assigned card"""
    visitor_id: int = Field(..., description="Visitor ID (YYYYMMDDHHMMSS format)")
    card_name: Optional[str] = Field(None, description="Name of the assigned card, null if no card assigned")
    card_id: Optional[int] = Field(None, description="ID of the assigned card, null if no card assigned")
