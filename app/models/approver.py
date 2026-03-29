from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from app.core.database import Base


class Approver(Base):
    """
    Approver model for authentication and authorization.
    Stores user credentials and role information.
    """
    __tablename__ = "vis_approvers"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=False)
    ph_no = Column(String(20), nullable=True)
    warehouse = Column(String(255), nullable=True)
    hashed_password = Column(String(255), nullable=False)
    superuser = Column(Boolean, default=False, nullable=False)
    admin = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<Approver(id={self.id}, username='{self.username}', email='{self.email}', superuser={self.superuser})>"
