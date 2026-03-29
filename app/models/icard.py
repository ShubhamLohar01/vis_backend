from sqlalchemy import Column, Integer, BigInteger, String, Boolean, DateTime
from sqlalchemy.sql import func
from app.core.database import Base


class ICard(Base):
    """
    ICard model for visitor card management.
    Stores card information and assignment status.
    """
    __tablename__ = "icards"

    id = Column(Integer, primary_key=True, index=True)
    card_name = Column(String(255), nullable=False, unique=True, index=True)
    occ_status = Column(Boolean, default=False, nullable=False)
    occ_to = Column(BigInteger, nullable=True)  # Visitor ID that the card is assigned to

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<ICard(id={self.id}, card_name='{self.card_name}', occupied={self.occ_status}, assigned_to={self.occ_to})>"
