# File: database.py
# Path: backend/app/core/database.py

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

from app.core.config import settings

# Shared declarative base for all models
Base = declarative_base()

# Updated engine configuration for better threading support
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,            # Smaller pool for Lambda
    max_overflow=10,        # Reduced overflow
    pool_recycle=300,       # Recycle connections every 5 minutes (Lambda reuse)
    pool_timeout=20,        # Reduced timeout
    connect_args={
        # Add connection options for better stability
        "options": "-c timezone=utc",
        "connect_timeout": 5,  # Faster connection timeout
        "application_name": "CandorFoodsBackend"
    } if "postgresql" in settings.DATABASE_URL else {},
    echo=settings.database_echo  # Use debug setting from config
)

# Updated SessionLocal with threading fixes
SessionLocal = sessionmaker(
    bind=engine, 
    autocommit=False, 
    autoflush=False,
    expire_on_commit=False  # IMPORTANT: Prevents threading issues
)

def get_db():
    """
    Dependency function for FastAPI endpoints.
    Creates a new database session for each request.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception as e:
        db.rollback()
        raise
    finally:
        db.close()

# Additional utility function for thread-safe database access
def get_thread_db():
    """
    Create a thread-safe database session for background tasks.
    Use this in print queue processing and other threaded operations.
    """
    return SessionLocal()

def test_database_connection():
    """
    Test database connection health.
    Returns True if connection is successful, False otherwise.
    """
    import logging
    from sqlalchemy import text
    logger = logging.getLogger(__name__)
    
    try:
        db = SessionLocal()
        # Simple query to test connection
        result = db.execute(text("SELECT 1"))
        db.close()
        logger.info("Database connection test successful")
        return True
    except Exception as e:
        logger.error(f"Database connection test failed: {str(e)}")
        return False