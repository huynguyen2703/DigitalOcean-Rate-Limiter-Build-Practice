# Design Document: Rate Limiter Service

## Overview

The Rate Limiter Service is a FastAPI-based multi-tenant rate limiting system that enforces tier-based quotas using a sliding window log algorithm. The service maintains in-memory state for fast rate limit checks while persisting client metadata and usage logs to a SQLite database via SQLModel.

### Key Design Decisions

1. **Sliding Window Log Algorithm**: We use a deque-based timestamp log per client to track requests within a 60-second rolling window, providing accurate burst detection without fixed-window aliasing.

2. **Hybrid State Management**: In-memory deques provide sub-50ms rate limit checks while SQLModel persistence ensures data survives restarts and enables historical analytics.

3. **Async-Safe Concurrency**: Per-client asyncio.Lock instances serialize concurrent requests for the same client, preventing race conditions while allowing parallel processing across different clients.

4. **Graceful Degradation**: Rate limiting continues using in-memory state if database operations fail, with background retry logic for persistence.

5. **Automatic Eviction**: Lazy eviction removes stale timestamps during checks; periodic cleanup task removes inactive client state entries (>300s idle).

## Architecture

### Component Layers

```
┌─────────────────────────────────────────────────────────────┐
│                      FastAPI Layer                           │
│  (main.py: /v1/limiter/check, /v1/limiter/usage/{id})      │
└────────────────────┬────────────────────────────────────────┘
                     │ Depends(get_session)
┌────────────────────▼────────────────────────────────────────┐
│                   Service Layer                              │
│  (service.py: RateLimiterService class)                     │
│  - In-memory Window_State (dict[str, deque[float]])        │
│  - Per-client locks (dict[str, asyncio.Lock])              │
│  - Sliding window logic & eviction                          │
└────────────────────┬────────────────────────────────────────┘
                     │ Session injection
┌────────────────────▼────────────────────────────────────────┐
│                 Data Access Layer                            │
│  (models.py: ClientMetadata, UsageLog SQLModel tables)      │
│  (database.py: engine, get_session dependency)              │
└─────────────────────────────────────────────────────────────┘
```

### Request Flow

**Rate Limit Check (/v1/limiter/check)**:
1. FastAPI validates request schema (CheckRequest)
2. Service acquires per-client lock
3. Service evicts timestamps older than 60s
4. Service counts remaining timestamps in window
5. If count < tier limit:
   - Add current timestamp to deque
   - Persist usage log entry (best-effort)
   - Return allowed=true
6. Else:
   - Return allowed=false
7. Calculate remaining quota and reset time
8. Release lock

**Usage Query (/v1/limiter/usage/{client_id})**:
1. FastAPI extracts client_id from path
2. Service queries ClientMetadata table
3. If not found, return 404
4. Service aggregates total_requests from UsageLog
5. Service calculates current window percentage from in-memory state
6. Return usage response

## Components and Interfaces

### 1. API Layer (backend/app/main.py)

**FastAPI Application**:
```python
app = FastAPI(
    title="Rate Limiter Service",
    version="1.0.0",
    lifespan=lifespan_handler
)
```

**Endpoints**:

```python
@app.post("/v1/limiter/check", response_model=CheckResponse, status_code=200)
async def check_rate_limit(
    request: CheckRequest,
    session: Session = Depends(get_session)
) -> CheckResponse:
    """
    Check if a client request is allowed under rate limits.
    
    Returns:
        - 200: CheckResponse with allowed status
        - 400: Missing required fields
        - 422: Invalid field types
        - 500: Internal server error
    """
```

```python
@app.get("/v1/limiter/usage/{client_id}", response_model=UsageResponse, status_code=200)
async def get_usage(
    client_id: str,
    session: Session = Depends(get_session)
) -> UsageResponse:
    """
    Retrieve historical usage and current window status for a client.
    
    Returns:
        - 200: UsageResponse with usage data
        - 404: Client not found
        - 500: Internal server error
    """
```

**Lifespan Handler**:
```python
@asynccontextmanager
async def lifespan_handler(app: FastAPI):
    """
    Startup: Initialize database, create tables, start cleanup task
    Shutdown: Cancel cleanup task, close connections
    """
    # Startup
    create_db_and_tables()
    cleanup_task = asyncio.create_task(periodic_cleanup())
    yield
    # Shutdown
    cleanup_task.cancel()
```

**HTTP Status Code Mappings**:
- 200: Successful operation
- 400: Bad request (missing required fields, malformed JSON)
- 404: Resource not found (client_id does not exist)
- 422: Unprocessable entity (invalid data types, validation errors)
- 500: Internal server error (database failures, unexpected exceptions)

### 2. Service Layer (backend/app/service.py)

**RateLimiterService Class**:

```python
class RateLimiterService:
    """
    Manages in-memory rate limiting state and coordinates database persistence.
    
    State:
        - window_state: dict[str, deque[float]] - Timestamp logs per client
        - client_locks: dict[str, asyncio.Lock] - Per-client concurrency locks
        - last_access: dict[str, float] - Last request time for eviction tracking
    """
    
    def __init__(self):
        self.window_state: dict[str, deque[float]] = {}
        self.client_locks: dict[str, asyncio.Lock] = {}
        self.last_access: dict[str, float] = {}
        self.window_size: int = 60  # seconds
        self.inactive_threshold: int = 300  # seconds
```

**Core Methods**:

```python
async def check_limit(
    self,
    client_id: str,
    tier: str,
    session: Session
) -> tuple[bool, int, int]:
    """
    Check if request is allowed and update state.
    
    Args:
        client_id: Unique client identifier
        tier: Client tier (free/pro, case-insensitive)
        session: Database session for persistence
    
    Returns:
        (allowed, remaining, reset_in_seconds)
    
    Thread Safety: Acquires per-client lock for atomic check-and-increment
    """
    
async def get_usage(
    self,
    client_id: str,
    session: Session
) -> dict[str, any]:
    """
    Retrieve usage statistics for a client.
    
    Args:
        client_id: Unique client identifier
        session: Database session
    
    Returns:
        dict with keys: client_id, tier, total_requests, current_window_usage_percent
    
    Raises:
        ValueError: If client_id not found in database
    """

def _evict_stale_timestamps(self, client_id: str, current_time: float) -> None:
    """
    Remove timestamps older than window_size from client's deque.
    
    Args:
        client_id: Client to evict timestamps for
        current_time: Current Unix timestamp
    
    Side Effects: Modifies self.window_state[client_id] in-place
    """

async def _persist_usage_log(
    self,
    client_id: str,
    tier: str,
    session: Session
) -> None:
    """
    Best-effort persistence of usage log entry.
    
    Args:
        client_id: Client identifier
        tier: Client tier
        session: Database session
    
    Error Handling: Logs failures but does not raise exceptions
    """

async def cleanup_inactive_clients(self) -> None:
    """
    Remove clients with no activity for > inactive_threshold seconds.
    
    Side Effects: Removes entries from window_state, client_locks, last_access
    """
```

**Tier Configuration**:
```python
TIER_LIMITS = {
    "free": 10,
    "pro": 100
}

def _get_tier_limit(tier: str) -> int:
    """
    Get request limit for tier (case-insensitive, defaults to 'free').
    
    Args:
        tier: Tier name
    
    Returns:
        Request limit per 60-second window
    """
    normalized = tier.lower().strip()
    return TIER_LIMITS.get(normalized, TIER_LIMITS["free"])
```

**Concurrency Strategy**:
- **Per-Client Locks**: Each client_id gets a unique asyncio.Lock stored in `client_locks` dict
- **Lock Acquisition**: Before checking/updating window_state, acquire lock for that client_id
- **Lock Scope**: Lock held only during: eviction + count + timestamp append (< 1ms typically)
- **Parallel Processing**: Different clients can be processed concurrently; same client serialized
- **Lock Lifecycle**: Locks created on-demand, removed during cleanup_inactive_clients

**Eviction Logic**:

*Stale Timestamp Eviction*:
- **Trigger**: Every check_limit call before counting
- **Logic**: `while deque and deque[0] < (current_time - window_size): deque.popleft()`
- **Complexity**: O(k) where k = number of expired timestamps

*Inactive Client Eviction*:
- **Trigger**: Background task every 60 seconds via periodic_cleanup()
- **Logic**: Remove client from all dicts if `current_time - last_access[client] > inactive_threshold`
- **Safety**: Acquires client lock before removal to prevent mid-check eviction

**Database Fallback Handling**:
- **Philosophy**: Rate limiting is the primary function; persistence is secondary
- **Check Limit Path**: Wrap `_persist_usage_log` in try/except; log error but return successful response
- **Usage Query Path**: Database required; return 500 if query fails
- **Startup**: If table creation fails, log error and exit (cannot operate without schema)

### 3. Data Models (backend/app/models.py)

**Database Tables (SQLModel)**:

```python
class ClientMetadata(SQLModel, table=True):
    """
    Persistent client configuration and tier information.
    
    Table: client_metadata
    """
    __tablename__ = "client_metadata"
    
    client_id: str = Field(primary_key=True, index=True, max_length=255)
    tier: str = Field(default="free", max_length=50)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class UsageLog(SQLModel, table=True):
    """
    Historical log of client requests for analytics and auditing.
    
    Table: usage_log
    """
    __tablename__ = "usage_log"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = Field(index=True, max_length=255)
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)
    request_count: int = Field(default=1)
```

**API Request Schemas (Pydantic)**:

```python
class CheckRequest(BaseModel):
    """
    Request schema for POST /v1/limiter/check
    
    Validation:
        - client_id: non-empty string (min_length=1)
        - tier: string (will be normalized to lowercase)
    """
    client_id: str = Field(..., min_length=1, description="Unique client identifier")
    tier: str = Field(..., description="Client tier (free, pro)")
    
    class Config:
        json_schema_extra = {
            "example": {
                "client_id": "client-abc-123",
                "tier": "pro"
            }
        }
```

**API Response Schemas (Pydantic)**:

```python
class CheckResponse(BaseModel):
    """
    Response schema for POST /v1/limiter/check
    
    Fields:
        - allowed: Whether the request is permitted
        - remaining: Requests remaining in current window
        - reset_in_seconds: Time until oldest timestamp expires
    """
    allowed: bool
    remaining: int = Field(ge=0)
    reset_in_seconds: int = Field(ge=0)
    
    class Config:
        json_schema_extra = {
            "example": {
                "allowed": True,
                "remaining": 7,
                "reset_in_seconds": 45
            }
        }

class UsageResponse(BaseModel):
    """
    Response schema for GET /v1/limiter/usage/{client_id}
    
    Fields:
        - client_id: Client identifier
        - tier: Current tier
        - total_requests: Historical request count (all time)
        - current_window_usage_percent: Percentage of tier limit used in current window
    """
    client_id: str
    tier: str
    total_requests: int = Field(ge=0)
    current_window_usage_percent: float = Field(ge=0.0, le=100.0)
    
    class Config:
        json_schema_extra = {
            "example": {
                "client_id": "client-abc-123",
                "tier": "pro",
                "total_requests": 15420,
                "current_window_usage_percent": 73.5
            }
        }
```

**Field Validation Rules**:
- **client_id**: Non-empty string (min_length=1), max 255 chars for database compatibility
- **tier**: String, case-insensitive normalization in service layer
- **remaining**: Non-negative integer (ge=0)
- **reset_in_seconds**: Non-negative integer (ge=0)
- **total_requests**: Non-negative integer (ge=0)
- **current_window_usage_percent**: Float between 0.0 and 100.0 (ge=0.0, le=100.0)

### 4. Database Layer (backend/app/database.py)

**Engine Initialization**:

```python
# SQLite engine with connection pooling
DATABASE_URL = "sqlite:///./rate_limiter.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # Required for SQLite
    echo=False  # Set to True for SQL query logging
)

def create_db_and_tables():
    """
    Create all SQLModel tables if they don't exist.
    
    Called during application startup in lifespan handler.
    """
    SQLModel.metadata.create_all(engine)
```

**Session Dependency**:

```python
def get_session() -> Generator[Session, None, None]:
    """
    FastAPI dependency that provides database session.
    
    Usage:
        @app.post("/endpoint")
        async def endpoint(session: Session = Depends(get_session)):
            ...
    
    Lifecycle:
        - Creates new session for each request
        - Automatically commits on success
        - Rolls back on exception
        - Closes session in finally block
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
```

**Connection Management**:
- **Pool Size**: Default SQLite pooling (5 connections)
- **Thread Safety**: `check_same_thread=False` allows FastAPI async workers to share engine
- **Transaction Isolation**: Read committed (SQLite default)

## Data Models

See **Components and Interfaces** section above for complete data model specifications including:
- SQLModel entities: ClientMetadata, UsageLog
- Pydantic request schemas: CheckRequest
- Pydantic response schemas: CheckResponse, UsageResponse
- Field validation rules and constraints


## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Tier-Based Rate Limiting

*For any* client with a given tier (free or pro), when making a sequence of requests within a 60-second window, the Nth request SHALL be allowed and the (N+1)th request SHALL be rejected, where N equals the tier's request limit (10 for free, 100 for pro).

**Validates: Requirements 1.1, 1.2, 3.3, 3.4**

### Property 2: Unknown Tier Default

*For any* tier value that is not "free" or "pro" (case-insensitive), the system SHALL enforce the same rate limit as tier "free" (10 requests per 60-second window).

**Validates: Requirements 1.3**

### Property 3: Case-Insensitive Tier Handling

*For any* valid tier name (free, pro), all case variations (FREE, Free, free, PRO, Pro, pro, etc.) SHALL produce identical rate limiting behavior.

**Validates: Requirements 1.4**

### Property 4: Window State Timestamp Freshness

*For any* client's window state at any point during execution, all timestamps in the deque SHALL be within 60 seconds of the current time (after eviction has been applied).

**Validates: Requirements 2.1, 2.4, 7.1**

### Property 5: Allowed Request Timestamp Recording

*For any* request that returns `allowed=true`, the current timestamp SHALL appear in that client's window_state deque immediately after the operation completes.

**Validates: Requirements 2.3**

### Property 6: Request Count Equals Window State Size

*For any* client after making N requests within a 60-second window, the length of that client's window_state deque SHALL equal N (assuming no timestamps have aged beyond 60 seconds).

**Validates: Requirements 2.5**

### Property 7: Remaining Quota Accuracy

*For any* rate limit check response, the `remaining` field SHALL equal (tier_limit - current_window_count), where current_window_count is the number of timestamps in the client's window_state within the last 60 seconds.

**Validates: Requirements 3.5**

### Property 8: Reset Time Calculation

*For any* rate limit check response where the window_state is non-empty, the `reset_in_seconds` field SHALL equal the time until the oldest timestamp in the window expires (60 - (current_time - oldest_timestamp)).

**Validates: Requirements 3.6**

### Property 9: Concurrent Request Atomicity

*For any* client at or near their rate limit, when N concurrent requests arrive simultaneously, the total number of requests with `allowed=true` SHALL NOT exceed (tier_limit - initial_window_count).

**Validates: Requirements 3.8, 8.2**

### Property 10: Usage Percentage Calculation

*For any* client, the `current_window_usage_percent` returned by the usage endpoint SHALL equal (current_window_count / tier_limit) × 100, where current_window_count is the number of timestamps in the client's window_state.

**Validates: Requirements 4.3**

### Property 11: Client Metadata Persistence

*For any* client making their first request, a ClientMetadata record with matching client_id and tier SHALL exist in the database after the request completes.

**Validates: Requirements 6.1**

### Property 12: Usage Log Persistence and Increment

*For any* client making N allowed requests, the sum of request_count values in the UsageLog table for that client_id SHALL increase by N.

**Validates: Requirements 6.2, 6.3**

### Property 13: Inactive Client Cleanup

*For any* client with no requests in the last 300 seconds, after the cleanup task runs, that client's entry SHALL be removed from window_state, client_locks, and last_access dictionaries.

**Validates: Requirements 7.2**

### Property 14: Window State Size Invariant

*For any* client's window_state at any point during execution, the number of timestamps SHALL NOT exceed 200 (max tier limit of 100 + buffer of 100).

**Validates: Requirements 7.4**

### Property 15: Concurrent Request Confluence

*For any* set of concurrent requests for the same client, the final window_state SHALL contain the same timestamps regardless of the order in which the requests were processed (order-independence property).

**Validates: Requirements 8.4**

### Property 16: Check Response Schema Completeness

*For any* successful rate limit check (status 200), the response SHALL contain exactly three fields: `allowed` (boolean), `remaining` (non-negative integer), and `reset_in_seconds` (non-negative integer).

**Validates: Requirements 10.1**

### Property 17: Usage Response Schema Completeness

*For any* successful usage query (status 200), the response SHALL contain exactly four fields: `client_id` (string), `tier` (string), `total_requests` (non-negative integer), and `current_window_usage_percent` (float between 0.0 and 100.0).

**Validates: Requirements 10.2**

### Property 18: Request Serialization Round-Trip

*For any* valid CheckRequest object, the following transformation SHALL produce an equivalent JSON structure: CheckRequest → JSON (serialize) → CheckRequest (parse) → JSON (serialize), where the first and final JSON are structurally identical.

**Validates: Requirements 12.5**

## Error Handling

### Error Categories and Responses

**Validation Errors (4xx)**:
- **400 Bad Request**: Missing required fields, malformed JSON
  - Example: Request body missing `client_id` field
  - Response: `{"detail": "Field required: client_id"}`
  
- **404 Not Found**: Requested resource does not exist
  - Example: Usage query for non-existent client_id
  - Response: `{"detail": "Client not found: unknown-client-123"}`
  
- **422 Unprocessable Entity**: Invalid data types or constraint violations
  - Example: `client_id` is empty string or `tier` is not a string
  - Response: `{"detail": [{"loc": ["body", "client_id"], "msg": "ensure this value has at least 1 characters", "type": "value_error.any_str.min_length"}]}`

**Server Errors (5xx)**:
- **500 Internal Server Error**: Database failures, unexpected exceptions
  - Example: Database connection lost during query
  - Response: `{"detail": "Internal server error"}`
  - Logging: Full exception stack trace logged for debugging

### Error Handling Strategy

**Database Failures**:
- **During rate limit check**: Log error, continue with in-memory rate limiting, return successful response (graceful degradation)
- **During usage query**: Return 500 status (database required for this operation)
- **During startup**: Log fatal error and exit (cannot create schema)

**Concurrency Errors**:
- **Lock acquisition timeout**: Should not occur (locks are async and non-blocking)
- **Race condition detection**: Prevented by per-client locks

**Input Validation**:
- **Pydantic validation**: Automatic by FastAPI, returns 422 with detailed field errors
- **Custom validation**: Service layer normalizes tier to lowercase, defaults unknown tiers to "free"

**Logging Strategy**:
- **Info**: Successful operations (rate limit checks, usage queries)
- **Warning**: Database persistence failures (best-effort logging)
- **Error**: Unexpected exceptions, database connection failures
- **Debug**: Lock acquisition, eviction operations, cleanup task runs

### Resilience Patterns

**Graceful Degradation**:
- Rate limiting continues with in-memory state if database unavailable
- Usage logs persisted on best-effort basis during database issues

**Retry Logic**:
- No automatic retries for database operations (fail-fast for observability)
- Client responsible for retrying 500 errors

**Circuit Breaking**:
- Not implemented (database failures handled per-request)
- Consider adding if frequent transient database errors observed

## Testing Strategy

### Dual Testing Approach

The testing strategy employs both unit tests and property-based tests to ensure comprehensive coverage:

- **Unit tests**: Verify specific examples, edge cases, integration points, and error conditions
- **Property-based tests**: Verify universal properties hold across randomized inputs (100+ iterations per property)

### Property-Based Testing Configuration

**Library**: `hypothesis` for Python (pytest integration)

**Test Configuration**:
```python
from hypothesis import given, settings, strategies as st

@settings(max_examples=100)  # Minimum 100 iterations per property
@given(...)
def test_property_name(...):
    """
    Feature: rate-limiter-service, Property N: [property text]
    """
```

**Tag Format**: Each property test MUST include a docstring comment:
```
Feature: rate-limiter-service, Property {number}: {property_text}
```

**Generator Strategies**:
- `client_id`: Non-empty strings, alphanumeric with hyphens
- `tier`: Random strings (valid and invalid), case variations
- `timestamps`: Float values representing Unix time
- `request_sequences`: Lists of requests with controlled timing
- `concurrent_requests`: Lists of requests executed in parallel

### Test Coverage by Category

**Unit Tests (backend/tests/test_main.py)**:
1. **Endpoint existence and routing** (Requirements 3.1, 4.1)
   - POST /v1/limiter/check accepts requests
   - GET /v1/limiter/usage/{client_id} accepts requests

2. **Specific error scenarios** (Requirements 3.7, 4.5, 11.1, 11.2, 11.3, 12.2, 12.4)
   - Missing client_id field → 400
   - Non-existent client_id → 404
   - Malformed JSON → 400
   - Invalid data types → 422
   - Database failure → 500

3. **Edge cases**:
   - Empty window state (first request)
   - Exactly at limit (boundary condition)
   - All timestamps expired (empty window after eviction)

4. **Integration scenarios** (Requirements 5.4, 6.4, 11.4, 11.5)
   - State persistence across multiple checks
   - Database retrieval and aggregation
   - Rate limiting with database unavailable
   - Recovery after database outage

**Property-Based Tests (backend/tests/test_properties.py)**:

Each test corresponds to one correctness property (see Correctness Properties section):

1. `test_tier_based_rate_limiting` - Property 1
2. `test_unknown_tier_default` - Property 2
3. `test_case_insensitive_tier` - Property 3
4. `test_window_state_freshness` - Property 4
5. `test_allowed_request_recording` - Property 5
6. `test_request_count_equals_state` - Property 6
7. `test_remaining_quota_accuracy` - Property 7
8. `test_reset_time_calculation` - Property 8
9. `test_concurrent_atomicity` - Property 9
10. `test_usage_percentage_calculation` - Property 10
11. `test_client_metadata_persistence` - Property 11
12. `test_usage_log_increment` - Property 12
13. `test_inactive_client_cleanup` - Property 13
14. `test_window_state_size_bound` - Property 14
15. `test_concurrent_confluence` - Property 15
16. `test_check_response_schema` - Property 16
17. `test_usage_response_schema` - Property 17
18. `test_request_round_trip` - Property 18

**Test Fixtures (backend/tests/conftest.py)**:
- `test_client`: FastAPI TestClient with in-memory database
- `test_session`: SQLModel Session with in-memory SQLite
- `clean_service`: Fresh RateLimiterService instance with empty state
- `mock_time`: Controllable time for testing timestamp logic

### Test Execution

**Running Tests**:
```bash
# All tests
pytest backend/tests/

# Unit tests only
pytest backend/tests/test_main.py

# Property tests only
pytest backend/tests/test_properties.py

# With coverage
pytest --cov=backend/app backend/tests/
```

**Continuous Integration**:
- Run full test suite on every commit
- Minimum 90% code coverage required
- Property tests run with 100 iterations each

### Testing Anti-Patterns to Avoid

**Don't**: Write excessive unit tests for scenarios covered by properties
- Property tests already generate hundreds of input combinations
- Focus unit tests on specific examples and integration points

**Don't**: Mock the sliding window logic itself
- Test the actual implementation to catch subtle bugs
- Use controllable time (mock `time.time()`) instead of mocking logic

**Don't**: Skip property tests due to execution time
- 100 iterations × 18 properties = ~2000 test cases
- Typical execution time: 5-10 seconds (acceptable for CI)

**Do**: Use in-memory SQLite for fast test execution
**Do**: Test concurrency with actual async execution (not mocked)
**Do**: Verify both success and failure paths in properties

