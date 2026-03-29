import bcrypt
from jose import jwt, JWTError
from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.approver import Approver
from app.schemas.approver import TokenData


# Security scheme for bearer token
security = HTTPBearer()


class AuthUtils:
    """Utility class for authentication operations"""

    @staticmethod
    def hash_password(password: str) -> str:
        """
        Hash a plain text password using bcrypt.

        Args:
            password: Plain text password

        Returns:
            Hashed password string
        """
        salt = bcrypt.gensalt(rounds=settings.bcrypt_rounds)
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
        return hashed.decode('utf-8')

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """
        Verify a plain text password against a hashed password.

        Args:
            plain_password: Plain text password to verify
            hashed_password: Hashed password from database

        Returns:
            True if password matches, False otherwise
        """
        return bcrypt.checkpw(
            plain_password.encode('utf-8'),
            hashed_password.encode('utf-8')
        )

    @staticmethod
    def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """
        Create a JWT access token.

        Args:
            data: Dictionary of data to encode in the token
            expires_delta: Optional expiration time delta

        Returns:
            Encoded JWT token string
        """
        to_encode = data.copy()

        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(hours=settings.JWT_EXPIRATION_HOURS)

        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(
            to_encode,
            settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM
        )
        return encoded_jwt

    @staticmethod
    def decode_token(token: str) -> TokenData:
        """
        Decode and validate a JWT token.

        Args:
            token: JWT token string

        Returns:
            TokenData object with username and approver_id

        Raises:
            HTTPException: If token is invalid or expired
        """
        credentials_exception = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET,
                algorithms=[settings.JWT_ALGORITHM]
            )
            username: str = payload.get("sub")
            approver_id: int = payload.get("approver_id")

            if username is None or approver_id is None:
                raise credentials_exception

            return TokenData(username=username, approver_id=approver_id)
        except JWTError:
            raise credentials_exception
        except Exception:
            raise credentials_exception


def get_current_approver(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> Approver:
    """
    Dependency to get the current authenticated approver.
    Checks both vis_approvers and vis_admin tables.

    Args:
        credentials: HTTP bearer token credentials
        db: Database session

    Returns:
        Approver object of the authenticated user (or Admin converted to Approver format)

    Raises:
        HTTPException: If authentication fails
    """
    from app.models.admin import Admin
    
    token = credentials.credentials
    token_data = AuthUtils.decode_token(token)

    # First, try to find in vis_approvers table
    approver = db.query(Approver).filter(
        Approver.username == token_data.username,
        Approver.id == token_data.approver_id
    ).first()

    # If not found, check vis_admin table
    if approver is None:
        admin = db.query(Admin).filter(
            Admin.username == token_data.username,
            Admin.id == token_data.approver_id
        ).first()
        
        if admin:
            # Convert Admin to Approver-like object for compatibility
            # Create a new Approver instance (not bound to session, just for auth checks)
            approver = Approver()
            approver.id = admin.id
            approver.username = admin.username
            approver.email = admin.email
            approver.name = admin.name
            approver.ph_no = None
            approver.warehouse = admin.warehouse
            approver.hashed_password = admin.hashed_password
            approver.superuser = False
            approver.admin = True
            approver.is_active = admin.is_active
            approver.created_at = admin.created_at
            approver.updated_at = admin.updated_at
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )

    if not approver.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive account"
        )

    return approver


def get_current_superuser(
    current_approver: Approver = Depends(get_current_approver)
) -> Approver:
    """
    Dependency to verify current user is a superuser.

    Args:
        current_approver: Current authenticated approver

    Returns:
        Approver object if user is superuser

    Raises:
        HTTPException: If user is not a superuser
    """
    if not current_approver.superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions. Superuser access required."
        )

    return current_approver


def get_current_admin(
    current_approver: Approver = Depends(get_current_approver)
) -> Approver:
    """
    Dependency to verify current user is an admin or superuser.
    Admins and superusers can access the admin page.

    Args:
        current_approver: Current authenticated approver

    Returns:
        Approver object if user is admin or superuser

    Raises:
        HTTPException: If user is not an admin or superuser
    """
    if not current_approver.admin and not current_approver.superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions. Admin or superuser access required."
        )

    return current_approver
