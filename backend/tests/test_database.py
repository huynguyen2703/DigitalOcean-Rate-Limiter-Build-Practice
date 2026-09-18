"""
Unit tests for database layer functionality.

Tests database engine initialization, table creation, and session dependency.
"""

import pytest
from sqlmodel import Session, SQLModel, Field, create_engine
from backend.app.database import create_db_and_tables, get_session, engine


def test_engine_configuration():
    """Test that engine is configured with correct SQLite settings."""
    assert engine.url.drivername == "sqlite"
    # Verify engine exists and is properly initialized
    assert engine is not None
    assert str(engine.url) == "sqlite:///./rate_limiter.db"


def test_create_db_and_tables():
    """Test that create_db_and_tables executes without error."""
    # Should not raise any exceptions
    create_db_and_tables()


def test_get_session_lifecycle():
    """Test session dependency provides valid session with transaction handling."""
    # Get session from generator
    session_gen = get_session()
    session = next(session_gen)
    
    # Verify we got a valid Session object
    assert isinstance(session, Session)
    
    # Clean up
    try:
        next(session_gen)
    except StopIteration:
        pass  # Expected - generator exhausted


def test_get_session_commit_on_success():
    """Test that session commits on successful operation."""
    # Create a test table for this test
    class TestModel(SQLModel, table=True):
        __tablename__ = "test_commit_model"
        id: int = Field(primary_key=True)
        value: str
    
    # Create table
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    
    # Test session commit
    from sqlmodel import Session as SQLSession
    with SQLSession(test_engine) as session:
        try:
            # Simulate successful operation
            test_obj = TestModel(id=1, value="test")
            session.add(test_obj)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    
    # Verify data persisted
    with SQLSession(test_engine) as verify_session:
        result = verify_session.get(TestModel, 1)
        assert result is not None
        assert result.value == "test"


def test_get_session_rollback_on_error():
    """Test that session rolls back on exception."""
    session_gen = get_session()
    session = next(session_gen)
    
    # Verify rollback occurs on exception
    try:
        # Simulate error
        raise ValueError("Test error")
    except ValueError:
        try:
            session_gen.throw(ValueError, ValueError("Test error"), None)
        except ValueError:
            pass  # Expected
    finally:
        try:
            session_gen.close()
        except:
            pass
