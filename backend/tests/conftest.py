import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from backend.app.main import app
from backend.app.database import get_session
from backend.app.service import RateLimiterService


# In-memory SQLite engine for testing
@pytest.fixture(name="engine")
def engine_fixture():
    """
    Create in-memory SQLite engine for testing.
    
    Uses StaticPool to ensure the same in-memory database is used
    across all connections within a test.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture(name="test_session")
def session_fixture(engine):
    """
    Create test database session that yields clean database session.
    
    This fixture creates a new session for each test and cleans up afterwards.
    """
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(test_session: Session):
    """
    Create test client using FastAPI TestClient with test database.
    
    Overrides the get_session dependency to use the test database session.
    """
    def get_session_override():
        return test_session
    
    app.dependency_overrides[get_session] = get_session_override
    
    with TestClient(app) as test_client:
        yield test_client
    
    # Clean up dependency override
    app.dependency_overrides.clear()


@pytest.fixture(name="clean_service")
def clean_service_fixture():
    """
    Create clean RateLimiterService instance with empty state.
    
    Returns a fresh service instance with no clients in window_state.
    """
    return RateLimiterService()
