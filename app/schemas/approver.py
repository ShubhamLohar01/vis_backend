from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional
from datetime import datetime


class ApproverBase(BaseModel):
    """Base schema for Approver with common fields"""
    username: str = Field(..., min_length=3, max_length=50, description="Unique username for the approver")
    email: EmailStr = Field(..., description="Email address of the approver")
    name: str = Field(..., min_length=1, max_length=255, description="Full name of the approver")


class ApproverCreate(ApproverBase):
    """Schema for creating a new approver"""
    ph_no: Optional[str] = Field(None, max_length=20, description="Phone number of the approver")
    warehouse: Optional[str] = Field(None, max_length=255, description="Warehouse location of the approver")
    password: str = Field(..., min_length=8, description="Password for the approver (plain text, will be hashed)")
    superuser: bool = Field(default=False, description="Whether the approver has superuser privileges")
    admin: bool = Field(default=False, description="Whether the approver has admin privileges")


class ApproverUpdate(BaseModel):
    """Schema for updating an existing approver"""
    username: Optional[str] = Field(None, min_length=3, max_length=50)
    email: Optional[EmailStr] = None
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    ph_no: Optional[str] = Field(None, max_length=20, description="Phone number of the approver")
    warehouse: Optional[str] = Field(None, max_length=255, description="Warehouse location of the approver")
    password: Optional[str] = Field(None, min_length=8, description="New password (plain text, will be hashed)")
    superuser: Optional[bool] = None
    admin: Optional[bool] = None
    is_active: Optional[bool] = None


class ApproverResponse(ApproverBase):
    """Schema for approver response"""
    id: int
    ph_no: Optional[str] = None
    warehouse: Optional[str] = None
    superuser: bool
    admin: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApproverLogin(BaseModel):
    """Schema for approver login request"""
    username: str = Field(..., description="Username for authentication")
    password: str = Field(..., description="Password for authentication (plain text)")


class Token(BaseModel):
    """Schema for JWT token response"""
    access_token: str
    token_type: str = "bearer"


class ApproverLoginResponse(BaseModel):
    """Schema for login response with token and user info"""
    access_token: str
    token_type: str = "bearer"
    approver: ApproverResponse


class TokenData(BaseModel):
    """Schema for token payload data"""
    username: Optional[str] = None
    approver_id: Optional[int] = None


class ApproverSimple(BaseModel):
    """Simplified schema for approver list (for dropdowns/selection)"""
    id: int
    username: str = Field(..., description="Username of the approver")
    name: str = Field(..., description="Full name of the approver")
    email: EmailStr = Field(..., description="Email address")
    ph_no: Optional[str] = Field(None, description="Phone number")
    warehouse: Optional[str] = Field(None, description="Warehouse location")
    is_active: bool = Field(..., description="Whether the approver is active")

    model_config = ConfigDict(from_attributes=True)


class ForgotPasswordRequest(BaseModel):
    """Schema for forgot password request"""
    username: str = Field(..., description="Username or email address of the approver")
    new_password: str = Field(..., min_length=6, description="New password (plain text, will be hashed)")


class ForgotPasswordResponse(BaseModel):
    """Schema for forgot password response"""
    message: str
    username: str
