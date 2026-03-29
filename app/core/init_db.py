"""
Database initialization script.
Creates all tables and optionally seeds initial data.
"""

from sqlalchemy import inspect
from app.core.database import engine, Base, SessionLocal
from app.core.auth import AuthUtils
from app.models.approver import Approver
from app.models.visitor import Visitor
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def init_db():
    """
    Initialize the database by creating all tables.
    """
    logger.info("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created successfully!")


def seed_initial_data():
    """
    Seed the database with initial data (default superuser).
    """
    db = SessionLocal()

    try:
        # Check if any approvers exist
        existing_approvers = db.query(Approver).count()

        if existing_approvers == 0:
            logger.info("No approvers found. Creating default superuser...")

            # Create default superuser
            default_superuser = Approver(
                username="admin",
                email="admin@example.com",
                name="System Administrator",
                hashed_password=AuthUtils.hash_password("admin123"),
                superuser=True,
                is_active=True
            )

            db.add(default_superuser)
            db.commit()

            logger.info("Default superuser created successfully!")
            logger.info("Username: admin")
            logger.info("Password: admin123")
            logger.info("IMPORTANT: Please change the default password after first login!")
        else:
            logger.info(f"Database already has {existing_approvers} approver(s). Skipping seed data.")

    except Exception as e:
        logger.error(f"Error seeding initial data: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def check_tables():
    """
    Check which tables exist in the database.
    """
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    logger.info("Existing tables in database:")
    for table in tables:
        logger.info(f"  - {table}")

    return tables


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("Database Initialization Script")
    logger.info("=" * 50)

    # Check existing tables
    check_tables()

    # Initialize database
    init_db()

    # Seed initial data
    seed_initial_data()

    # Check tables again
    check_tables()

    logger.info("=" * 50)
    logger.info("Database initialization complete!")
    logger.info("=" * 50)
