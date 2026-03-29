from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional
from datetime import datetime
from app.models.visitor import VisitorStatus


class VisitorBase(BaseModel):
    """Base schema for Visitor with common fields"""
    visitor_name: str = Field(..., min_length=1, max_length=255, description="Name of the visitor")
    mobile_number: str = Field(..., min_length=10, max_length=20, description="Mobile number of the visitor")
    email_address: EmailStr = Field(..., description="Email address of the visitor")
    company: str = Field(..., min_length=1, max_length=255, description="Company name of the visitor")
    person_to_meet: str = Field(..., min_length=1, max_length=255, description="Person the visitor wants to meet")
    reason_to_visit: str = Field(..., min_length=1, max_length=500, description="Reason for the visit")
    warehouse: Optional[str] = Field(None, max_length=255, description="Warehouse location")
    health_declaration: Optional[str] = Field(None, description="Health & safety declaration as JSON string")
    date_of_visit: Optional[str] = Field(None, description="Scheduled date of visit (YYYY-MM-DD format)")
    time_slot: Optional[str] = Field(None, max_length=50, description="Scheduled time slot for the visit")


class VisitorCheckIn(VisitorBase):
    """Schema for visitor check-in"""
    pass


class VisitorUpdate(BaseModel):
    """Schema for updating visitor information"""
    visitor_name: Optional[str] = Field(None, min_length=1, max_length=255)
    mobile_number: Optional[str] = Field(None, min_length=10, max_length=20)
    email_address: Optional[EmailStr] = None
    company: Optional[str] = Field(None, max_length=255)
    person_to_meet: Optional[str] = Field(None, min_length=1, max_length=255)
    reason_to_visit: Optional[str] = Field(None, min_length=1, max_length=500)
    warehouse: Optional[str] = Field(None, max_length=255)
    health_declaration: Optional[str] = Field(None, description="Health & safety declaration as JSON string")
    status: Optional[VisitorStatus] = None
    check_out_time: Optional[datetime] = None


class VisitorStatusUpdate(BaseModel):
    """Schema for updating only visitor status"""
    status: VisitorStatus = Field(..., description="New status for the visitor")


class VisitorResponse(VisitorBase):
    """Schema for visitor response"""
    id: int
    status: VisitorStatus
    check_in_time: datetime
    check_out_time: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    person_to_meet_contact: Optional[str] = Field(None, description="Contact number of the person to meet")
    img_url: Optional[str] = Field(None, description="URL of the visitor image in S3")
    health_declaration: Optional[str] = Field(None, description="Health & safety declaration as JSON string")
    rejection_reason: Optional[str] = Field(None, description="Reason for rejection (if rejected)")
    date_of_visit: Optional[str] = Field(None, description="Scheduled date of visit")
    time_slot: Optional[str] = Field(None, description="Scheduled time slot")

    model_config = ConfigDict(from_attributes=True)


class VisitorCheckInResponse(BaseModel):
    """Schema for check-in response with success message"""
    message: str
    visitor: VisitorResponse


class VisitorListResponse(BaseModel):
    """Schema for paginated visitor list"""
    total: int
    visitors: list[VisitorResponse]
    page: int
    page_size: int


class VisitorStatsResponse(BaseModel):
    """Schema for visitor statistics"""
    total_visitors: int
    waiting: int
    approved: int
    rejected: int


class GoogleFormSubmission(BaseModel):
    """Schema for Google Form submission data"""
    source: Optional[str] = Field(None, description="Source of submission")
    submitted_at: Optional[str] = Field(None, description="Submission timestamp")
    visitor_name: str = Field(..., description="Full Name of Visitor")
    mobile: str = Field(..., description="Mobile Number")
    email: str = Field(..., description="Email Id")
    company: str = Field(..., description="Company / Organization Name")
    host_name: str = Field(..., description="Enter Person Name You Want to Meet")
    purpose: str = Field(..., description="Purpose of Visit")
    preferred_time_slot: Optional[str] = Field(None, description="Preferred Time Slot")
    carrying_items: Optional[str] = Field(None, description="Are you carrying any items inside the premises?")
    additional_remarks: Optional[str] = Field(None, description="Additional Remarks(if any)")
    sheet_name: Optional[str] = Field(None, description="Google Sheet name")
    row_number: Optional[int] = Field(None, description="Row number in sheet")
