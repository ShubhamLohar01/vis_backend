"""
Visitor Management System API
Main application file
"""

from fastapi import FastAPI, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import asyncio
import logging
import os
import uvicorn
import requests

from app.core.config import settings
from app.core.database import engine, Base, get_db
from app.routers import approver, visitor, icard, appointment, whatsapp_webhook
# from app.models import Approver, Visitor

# ============================================================================
# Logging Configuration
# ============================================================================

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# Periodic Health Ping (Render keep-alive)
# ============================================================================

HEALTH_PING_URL = "https://vis-backend-fa1q.onrender.com/health"
HEALTH_PING_INTERVAL_SECONDS = 5 * 60  # 5 minutes
HEALTH_PING_TIMEOUT_SECONDS = 10

_health_ping_stop_event: asyncio.Event | None = None
_health_ping_task: asyncio.Task | None = None


async def _ping_health_endpoint() -> None:
    """Send a single health ping without blocking the event loop."""
    def _do_request() -> tuple[int | None, str | None]:
        try:
            resp = requests.get(HEALTH_PING_URL, timeout=HEALTH_PING_TIMEOUT_SECONDS)
            return resp.status_code, resp.text[:200] if resp.text else ""
        except Exception as e:
            return None, str(e)

    status, info = await asyncio.to_thread(_do_request)
    if status is None:
        logger.warning(f"Health ping failed: {info}")
    else:
        logger.info(f"Health ping ok: {status}")


async def _health_ping_loop() -> None:
    """Background task: ping health URL every 5 minutes until stopped."""
    assert _health_ping_stop_event is not None

    # Small initial delay so startup can finish cleanly
    try:
        await asyncio.wait_for(_health_ping_stop_event.wait(), timeout=5)
        return
    except asyncio.TimeoutError:
        pass

    while not _health_ping_stop_event.is_set():
        await _ping_health_endpoint()
        try:
            await asyncio.wait_for(
                _health_ping_stop_event.wait(),
                timeout=HEALTH_PING_INTERVAL_SECONDS,
            )
        except asyncio.TimeoutError:
            continue


# ============================================================================
# FastAPI Application
# ============================================================================

# Disable docs in production for Lambda compatibility
docs_url = "/docs" if settings.ENVIRONMENT != "production" else None
redoc_url = "/redoc" if settings.ENVIRONMENT != "production" else None

app = FastAPI(
    title="Visitor Management System API",
    version="1.0.0",
    description="A comprehensive visitor management system with approver authentication and visitor check-in tracking",
    contact={
        "name": "API Support",
        "email": "support@example.com",
    },
    license_info={
        "name": "Proprietary",
    },
    docs_url=docs_url,
    redoc_url=redoc_url,
)

# ============================================================================
# CORS Configuration
# ============================================================================

if settings.API_CORS_ORIGINS and settings.API_CORS_ORIGINS.strip() == "*":
    # Allow all origins (credentials must be False)
    origins = ["*"]
    allow_credentials = False
else:
    # Specific origins (credentials can be True)
    origins = [
        "http://localhost:3000",
        "http://localhost:4000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:4000",
        "http://127.0.0.1:4001",
        "https://x5xqkl8w-4000.inc1.devtunnels.ms",
        "https://q80bvqq1-3000.inc1.devtunnels.ms",
        # Add your tunnel URLs here
    ]

    if settings.API_CORS_ORIGINS:
        additional_origins = [o.strip() for o in settings.API_CORS_ORIGINS.split(",") if o.strip()]
        origins.extend(additional_origins)

    allow_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],  # Allow all methods including OPTIONS for preflight
    allow_headers=["*"],  # Allow all headers
    expose_headers=["*"],  # Expose all headers in response
)

# ============================================================================
# Root & Health Endpoints
# ============================================================================

@app.get("/", tags=["Root"])
def root():
    """Root endpoint - API information"""
    return {
        "name": "Visitor Management System API",
        "version": "1.0.0",
        "status": "running",
        "documentation": "/docs",
        "endpoints": {
            "health": "/health",
            "approvers": "/api/approvers",
            "visitors": "/api/visitors",
            "icards": "/api/icards"
        }
    }

@app.get("/health", tags=["Health"])
def health_check():
    """Health check endpoint for API monitoring"""
    try:
        return {
            "status": "ok",
            "message": "Visitor Management System API is running",
            "version": "1.0.0",
            "environment": getattr(settings, 'ENVIRONMENT', 'unknown'),
            "timestamp": str(__import__('datetime').datetime.utcnow())
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Health check failed: {str(e)}",
            "version": "1.0.0"
        }

@app.get("/api/health", tags=["Health"])
def api_health():
    """Health check endpoint (alternative path)"""
    try:
        return {
            "status": "ok",
            "message": "Visitor Management System API is running",
            "version": "1.0.0",
            "environment": getattr(settings, 'ENVIRONMENT', 'unknown')
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"API health check failed: {str(e)}",
            "version": "1.0.0"
        }

# ============================================================================
# Event Handlers
# ============================================================================

@app.on_event("startup")
async def startup_event():
    """Actions to perform on application startup"""
    logger.info("=" * 60)
    logger.info("Starting Visitor Management System API")
    logger.info("=" * 60)
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Database: {settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")
    logger.info(f"CORS Origins: {settings.API_CORS_ORIGINS or 'Default'}")
    logger.info(f"JWT Expiration: {settings.JWT_EXPIRATION_HOURS} hours")
    logger.info("=" * 60)

    # Create database tables if they don't exist
    try:
        logger.info("Ensuring database tables exist...")
        Base.metadata.create_all(bind=engine)
        logger.info("✓ Database tables ready")
    except Exception as e:
        logger.error(f"✗ Database initialization failed: {e}")
        logger.warning("Application will continue, but database operations may fail")

    # Start periodic health pings (keep-alive) in the background
    global _health_ping_stop_event, _health_ping_task
    _health_ping_stop_event = asyncio.Event()
    _health_ping_task = asyncio.create_task(_health_ping_loop())

@app.on_event("shutdown")
async def shutdown_event():
    """Actions to perform on application shutdown"""
    logger.info("=" * 60)
    logger.info("Shutting down Visitor Management System API")
    logger.info("=" * 60)

    # Stop periodic health pings
    global _health_ping_stop_event, _health_ping_task
    if _health_ping_stop_event is not None:
        _health_ping_stop_event.set()
    if _health_ping_task is not None:
        try:
            await asyncio.wait_for(_health_ping_task, timeout=5)
        except asyncio.TimeoutError:
            _health_ping_task.cancel()
        except Exception:
            # Don't block shutdown on ping loop issues
            pass

# ============================================================================
# Router Registration
# ============================================================================

logger.info("Registering API routers...")
app.include_router(approver.router)  # Approver authentication and management
app.include_router(visitor.router)  # Visitor check-in and management
app.include_router(icard.router)  # ICard management
app.include_router(appointment.router)  # Appointment management
app.include_router(whatsapp_webhook.router)  # WhatsApp webhook for button replies

# Add Google Form endpoint at root level to match Apps Script URL
from app.routers.visitor import google_form_submission, GoogleFormSubmission

@app.post("/api/google-form", status_code=201)
def google_form_root_endpoint(
    form_data: GoogleFormSubmission,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Root level Google Form endpoint to match Apps Script URL"""
    return google_form_submission(form_data, background_tasks, db)

logger.info("All routers registered successfully")

if __name__ == "__main__":
    is_dev = settings.ENVIRONMENT != "production"
    port = int(os.getenv("PORT", "8000"))
    # When running via "python main.py", pass app object directly (not import string)
    # Import string "main:app" only works when uvicorn is invoked from outside the module
    uvicorn.run(
        app,  # Pass app object directly when running from main.py
        host="0.0.0.0",
        port=port,
        reload=is_dev,
        log_level=settings.LOG_LEVEL.lower()
    )
