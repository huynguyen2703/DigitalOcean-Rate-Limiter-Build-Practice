"""
Integration tests for database layer with SQLModel tables.

Tests the complete flow: engine -> session -> table creation -> CRUD operations.
"""

import pytest
from sqlmodel import Session, SQLModel, Field, select
from backend.app.database import create_db_and_tables, get_session, engine


# Define a test model to verify full integration
class TestClientModel(SQLModel, table=True):
    """Test model for integration testing."""
    __tablename__ = "test_clients"
    
    client_id: str = Field(primary_key=True)
    tier: str = Field(default="free")


def test_full_database_integration():
    """Test complete database flow from engine to CRUD operations."""
    # Step 1: Create tables
    SQLModel.metadata.create_all(engine)
    
    # Step 2: Use session dependency to perform CRUD
    session_gen = get_session()
    session = next(session_gen)
    
    try:
        # Create
        test_client = TestClientModel(client_id="test-123", tier="pro")
        session.add(test_client)
        session.commit()
        
        # Read
        statement = select(TestClientModel).where(TestClientModel.client_id == "test-123")
        result = session.exec(statement).first()
        
        assert result is not None
        assert result.client_id == "test-123"
        assert result.tier == "pro"
        
        # Update
        result.tier = "free"
        session.add(result)
        session.commit()
        
        # Verify update
        updated = session.exec(statement).first()
        assert updated.tier == "free"
        
        # Delete
        session.delete(updated)
        session.commit()
        
        # Verify deletion
        deleted = session.exec(statement).first()
        assert deleted is None
        
    finally:
        # Clean up session
        try:
            next(session_gen)
        except StopIteration:
            pass
        
        # Clean up table
        TestClientModel.__table__.drop(engine, checkfirst=True)


def test_session_dependency_with_context_manager():
    """Test session dependency works correctly with context manager pattern."""
    # Create a test table
    SQLModel.metadata.create_all(engine)
    
    # Simulate FastAPI dependency injection pattern
    def use_session_dependency():
        session_gen = get_session()
        session = next(session_gen)
        return session
    
    session = use_session_dependency()
    
    # Verify session is usable
    assert isinstance(session, Session)
    assert session.is_active
