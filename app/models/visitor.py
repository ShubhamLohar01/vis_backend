from sqlalchemy import Column, Integer, BigInteger, String, DateTime, Enum as SQLEnum
from sqlalchemy.sql import func
from app.core.database import Base
import enum


class VisitorStatus(str, enum.Enum):
    """Enum for visitor check-in status"""
    WAITING = "WAITING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class Visitor(Base):
    """
    Visitor model for check-in management.
    Stores visitor information and check-in details.
    """
    __tablename__ = "vis_visitors"

    id = Column(BigInteger, primary_key=True, index=True)
    visitor_name = Column(String(255), nullable=False, index=True)
    mobile_number = Column(String(20), nullable=False)
    email_address = Column(String(255), nullable=True)
    company = Column(String(255), nullable=True)
    person_to_meet = Column(String(255), nullable=False)
    reason_to_visit = Column(String(500), nullable=False)
    warehouse = Column(String(255), nullable=True)
    health_declaration = Column(String, nullable=True)  # JSON string containing health & safety declaration
    status = Column(SQLEnum(VisitorStatus), default=VisitorStatus.WAITING, nullable=False)
    img_url = Column(String(500), nullable=True)
    rejection_reason = Column(String(500), nullable=True)  # Reason for rejection (if rejected)

    # Timestamps
    check_in_time = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    check_out_time = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<Visitor(id={self.id}, name='{self.visitor_name}', company='{self.company}', status='{self.status}')>"
