"""
Appointment Model
Stores appointment booking data from Google Forms
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, BigInteger, ForeignKey
from sqlalchemy.sql import func
from app.core.database import Base


class Appointment(Base):
    __tablename__ = "vis_appointment"

    id = Column(BigInteger, primary_key=True, index=True)
    
    # Visitor Information
    visitor_name = Column(String(255), nullable=False)
    mobile_number = Column(String(20), nullable=False)
    email_address = Column(String(255), nullable=False)
    company = Column(String(255), nullable=False)
    
    # Appointment Details
    person_to_meet = Column(String(255), nullable=False)
    purpose_of_visit = Column(String(500), nullable=False)
    preferred_time_slot = Column(String(100), nullable=True)
    
    # Additional Information
    carrying_items = Column(Text, nullable=True)
    additional_remarks = Column(Text, nullable=True)
    
    # Metadata
    source = Column(String(50), default='google_form')
    submitted_at = Column(DateTime(timezone=True), server_default=func.now())
    sheet_name = Column(String(255), nullable=True)
    row_number = Column(Integer, nullable=True)
    
    # Status and Tracking
    status = Column(String(50), default='PENDING')  # PENDING, CONFIRMED, CANCELLED, COMPLETED
    visitor_id = Column(BigInteger, ForeignKey('vis_visitors.id'), nullable=True)  # Reference to visitor
    
    # QR Code
    qr_code = Column(String(500), nullable=True)  # Unique QR code identifier
    qr_code_sent = Column(String(10), default='NO')  # YES/NO - whether QR was sent via email
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

