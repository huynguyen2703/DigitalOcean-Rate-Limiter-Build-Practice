"""
Database Layer: Engine initialization and session dependency for SQLModel.

This module provides:
- SQLite engine with async-compatible configuration
- Database table creation function
- Session dependency generator for FastAPI endpoints
"""

from typing import Generator
from sqlmodel import Session, SQLModel, create_engine

# SQLite database URL - file-based storage
DATABASE_URL = "sqlite:///./rate_limiter.db"

# Create engine with SQLite-specific configuration
# check_same_thread=False allows FastAPI async workers to share the engine
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False  # Set to True for SQL query logging during development
)


def create_db_and_tables() -> None:
    """
    Create all SQLModel tables if they don't exist.
    
    This function should be called during application startup in the lifespan handler.
    It uses SQLModel.metadata.create_all() to create tables for all defined SQLModel
    entities with table=True.
    """
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    """
    FastAPI dependency that provides database session with transaction management.
    
    Usage:
        @app.post("/endpoint")
        async def endpoint(session: Session = Depends(get_session)):
            # Use session for database operations
            ...
    
    Lifecycle:
        - Creates new session for each request
        - Automatically commits on success
        - Rolls back on exception
        - Closes session in finally block
    
    Yields:
        Session: SQLModel database session
    """
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
