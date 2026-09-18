"""
API Layer: FastAPI application with rate limiting endpoints.

This module provides:
- POST /v1/limiter/check: Rate limit check endpoint
- GET /v1/limiter/usage/{client_id}: Usage statistics endpoint
- Lifespan handler for database initialization and cleanup task management
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException, status
from sqlmodel import Session

from backend.app.database import create_db_and_tables, get_session
from backend.app.models import CheckRequest, CheckResponse, UsageResponse
from backend.app.service import periodic_cleanup, rate_limiter_service

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global cleanup task reference
cleanup_task = None


@asynccontextmanager
async def lifespan_handler(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Lifespan context manager for application startup and shutdown.
    
    Startup:
        - Initialize database and create tables
        - Start periodic cleanup background task
    
    Shutdown:
        - Cancel periodic cleanup task
        - Close database connections
    """
    global cleanup_task
    
    # Startup
    logger.info("Starting Rate Limiter Service")
    
    # Initialize database and create tables
    try:
        create_db_and_tables()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise
    
    # Start periodic cleanup background task
    cleanup_task = asyncio.create_task(periodic_cleanup())
    logger.info("Periodic cleanup task started")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Rate Limiter Service")
    
    # Cancel cleanup task
    if cleanup_task:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            logger.info("Periodic cleanup task cancelled")


# Create FastAPI application instance
app = FastAPI(
    title="Rate Limiter Service",
    version="1.0.0",
    lifespan=lifespan_handler
)


@app.get("/health")
def health_check():
    """Health check endpoint for monitoring."""
    return {"status": "ok"}


@app.post(
    "/v1/limiter/check",
    response_model=CheckResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Rate limit check completed"},
        400: {"description": "Bad request - missing required fields"},
        422: {"description": "Unprocessable entity - invalid data types"},
        500: {"description": "Internal server error"}
    }
)
async def check_rate_limit(
    request: CheckRequest,
    session: Session = Depends(get_session)
) -> CheckResponse:
    """
    Check if a client request is allowed under rate limits.
    
    Args:
        request: CheckRequest containing client_id and tier
        session: Database session (injected via dependency)
    
    Returns:
        CheckResponse with allowed status, remaining quota, and reset time
    
    Raises:
        HTTPException: 400 for missing fields, 422 for invalid types, 500 for server errors
    
    Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 9.5
    """
    try:
        # Use global rate_limiter_service instance
        
        # Call service.check_limit
        allowed, remaining, reset_in_seconds = await rate_limiter_service.check_limit(
            client_id=request.client_id,
            tier=request.tier,
            session=session
        )
        
        # Map result to CheckResponse schema
        return CheckResponse(
            allowed=allowed,
            remaining=remaining,
            reset_in_seconds=reset_in_seconds
        )
    
    except ValueError as e:
        # Handle validation errors
        logger.warning(f"Validation error in check_rate_limit: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    
    except Exception as e:
        # Handle unexpected errors
        logger.error(f"Error in check_rate_limit: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@app.get(
    "/v1/limiter/usage/{client_id}",
    response_model=UsageResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Usage statistics retrieved successfully"},
        404: {"description": "Client not found"},
        500: {"description": "Internal server error"}
    }
)
async def get_usage(
    client_id: str,
    session: Session = Depends(get_session)
) -> UsageResponse:
    """
    Retrieve historical usage and current window status for a client.
    
    Args:
        client_id: Unique client identifier (path parameter)
        session: Database session (injected via dependency)
    
    Returns:
        UsageResponse with client_id, tier, total_requests, and current_window_usage_percent
    
    Raises:
        HTTPException: 404 if client not found, 500 for server errors
    
    Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 9.5
    """
    try:
        # Use global rate_limiter_service instance
        
        # Call service.get_usage
        usage_data = await rate_limiter_service.get_usage(
            client_id=client_id,
            session=session
        )
        
        # Map result dict to UsageResponse schema
        return UsageResponse(
            client_id=usage_data["client_id"],
            tier=usage_data["tier"],
            total_requests=usage_data["total_requests"],
            current_window_usage_percent=usage_data["current_window_usage_percent"]
        )
    
    except ValueError as e:
        # Handle client not found (ValueError raised by service.get_usage)
        logger.info(f"Client not found: {client_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Client not found: {client_id}"
        )
    
    except Exception as e:
        # Handle unexpected errors
        logger.error(f"Error in get_usage: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )
