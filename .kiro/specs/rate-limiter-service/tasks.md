# Implementation Plan: Rate Limiter Service

## Overview

This implementation plan breaks down the Rate Limiter Service into incremental coding tasks following the sliding window log algorithm with in-memory state management and SQLModel persistence. Tasks progress from data layer through service logic to API endpoints, with property-based tests integrated as optional sub-tasks to validate correctness properties defined in the design.

## Tasks

- [ ] 1. Set up database layer and core data models
  - [x] 1.1 Implement database engine initialization and session dependency in `backend/app/database.py`
    - Create SQLite engine with `check_same_thread=False` for async compatibility
    - Implement `create_db_and_tables()` function using `SQLModel.metadata.create_all()`
    - Implement `get_session()` dependency generator with proper transaction handling (commit/rollback/close)
    - _Requirements: 6.5, 6.6, 9.2_
  
  - [x] 1.2 Define SQLModel database tables in `backend/app/models.py`
    - Create `ClientMetadata` table with fields: client_id (primary key), tier, created_at, updated_at
    - Create `UsageLog` table with fields: id (primary key), client_id (indexed), timestamp (indexed), request_count
    - Add appropriate field constraints and indexes per design specifications
    - _Requirements: 6.1, 6.2, 9.3_
  
  - [x] 1.3 Define Pydantic request and response schemas in `backend/app/models.py`
    - Create `CheckRequest` schema with client_id (min_length=1) and tier validation
    - Create `CheckResponse` schema with allowed, remaining (ge=0), reset_in_seconds (ge=0)
    - Create `UsageResponse` schema with client_id, tier, total_requests (ge=0), current_window_usage_percent (0.0-100.0)
    - Add Config examples for each schema
    - _Requirements: 10.1, 10.2, 12.3, 12.4_

- [ ] 2. Implement core rate limiting service logic
  - [x] 2.1 Create `RateLimiterService` class skeleton in `backend/app/service.py`
    - Initialize state dictionaries: window_state (dict[str, deque[float]]), client_locks (dict[str, asyncio.Lock]), last_access (dict[str, float])
    - Define tier configuration constants: TIER_LIMITS = {"free": 10, "pro": 100}
    - Implement `_get_tier_limit(tier: str)` helper with case-insensitive normalization and default fallback
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 5.1, 5.2, 5.3_
  
  - [x] 2.2 Implement sliding window eviction logic in `RateLimiterService`
    - Implement `_evict_stale_timestamps(client_id: str, current_time: float)` method
    - Use deque.popleft() to remove timestamps older than 60 seconds
    - Ensure method modifies window_state in-place efficiently
    - _Requirements: 2.4, 7.1_
  
  - [ ]* 2.3 Write property test for stale timestamp eviction
    - **Property 4: Window State Timestamp Freshness**
    - **Validates: Requirements 2.1, 2.4, 7.1**
    - Generate random timestamp sequences and verify all timestamps in window_state are within 60 seconds after eviction
  
  - [x] 2.4 Implement atomic rate limit check in `RateLimiterService`
    - Implement `async check_limit(client_id: str, tier: str, session: Session)` method
    - Acquire per-client lock from client_locks (create if not exists)
    - Call _evict_stale_timestamps before counting
    - Count timestamps in window_state[client_id]
    - If under limit: append current timestamp, persist to DB (best-effort), return (True, remaining, reset_time)
    - If over limit: return (False, 0, reset_time)
    - Update last_access timestamp
    - _Requirements: 2.2, 2.3, 2.5, 3.3, 3.4, 3.5, 3.6, 8.1, 8.2_
  
  - [ ]* 2.5 Write property test for tier-based rate limiting
    - **Property 1: Tier-Based Rate Limiting**
    - **Validates: Requirements 1.1, 1.2, 3.3, 3.4**
    - Generate sequences of N+1 requests for each tier and verify Nth allowed, (N+1)th rejected
  
  - [ ]* 2.6 Write property test for unknown tier default behavior
    - **Property 2: Unknown Tier Default**
    - **Validates: Requirements 1.3**
    - Generate random invalid tier strings and verify they enforce free tier limits (10 requests)
  
  - [ ]* 2.7 Write property test for case-insensitive tier handling
    - **Property 3: Case-Insensitive Tier Handling**
    - **Validates: Requirements 1.4**
    - Generate case variations of "free" and "pro" and verify identical rate limiting behavior

- [ ] 3. Implement database persistence and usage tracking
  - [x] 3.1 Implement best-effort usage log persistence in `RateLimiterService`
    - Implement `async _persist_usage_log(client_id: str, tier: str, session: Session)` method
    - Create or retrieve ClientMetadata record for client_id
    - Insert UsageLog entry with current timestamp and request_count=1
    - Wrap in try/except to log errors without raising (graceful degradation)
    - _Requirements: 6.1, 6.2, 6.3, 11.4_
  
  - [x] 3.2 Implement usage query method in `RateLimiterService`
    - Implement `async get_usage(client_id: str, session: Session)` method
    - Query ClientMetadata table for client record (raise ValueError if not found)
    - Aggregate sum of request_count from UsageLog for client_id
    - Calculate current window usage percentage from window_state length
    - Return dict with client_id, tier, total_requests, current_window_usage_percent
    - _Requirements: 4.2, 4.3, 4.4, 6.4_
  
  - [ ]* 3.3 Write property test for remaining quota accuracy
    - **Property 7: Remaining Quota Accuracy**
    - **Validates: Requirements 3.5**
    - For random request counts, verify remaining = tier_limit - current_window_count
  
  - [ ]* 3.4 Write property test for usage percentage calculation
    - **Property 10: Usage Percentage Calculation**
    - **Validates: Requirements 4.3**
    - Verify current_window_usage_percent = (window_count / tier_limit) × 100

- [ ] 4. Implement memory management and cleanup
  - [x] 4.1 Implement inactive client cleanup in `RateLimiterService`
    - Implement `async cleanup_inactive_clients()` method
    - Iterate through last_access dict and identify clients with no activity for >300 seconds
    - Acquire client lock before removing from window_state, client_locks, last_access
    - Log cleanup operations for observability
    - _Requirements: 7.2, 7.3_
  
  - [x] 4.2 Implement periodic cleanup background task
    - Create `async periodic_cleanup()` coroutine that runs cleanup_inactive_clients every 60 seconds
    - Use asyncio.sleep for interval timing
    - Handle cancellation gracefully for shutdown
    - _Requirements: 7.2, 7.3_
  
  - [ ]* 4.3 Write property test for inactive client cleanup
    - **Property 13: Inactive Client Cleanup**
    - **Validates: Requirements 7.2**
    - Simulate time passage and verify clients with >300s inactivity are removed
  
  - [ ]* 4.4 Write property test for window state size invariant
    - **Property 14: Window State Size Invariant**
    - **Validates: Requirements 7.4**
    - For any client at any time, verify len(window_state[client_id]) ≤ 200

- [ ] 5. Checkpoint - Verify service layer implementation
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 6. Implement FastAPI endpoints and application setup
  - [x] 6.1 Create FastAPI application with lifespan handler in `backend/app/main.py`
    - Import FastAPI, asynccontextmanager, and all dependencies
    - Implement `async lifespan_handler(app: FastAPI)` context manager
    - On startup: call create_db_and_tables(), start periodic_cleanup() task
    - On shutdown: cancel cleanup task
    - Create FastAPI app instance with title, version, and lifespan
    - _Requirements: 6.6, 9.5_
  
  - [x] 6.2 Implement POST /v1/limiter/check endpoint in `backend/app/main.py`
    - Define route handler accepting CheckRequest and session dependency
    - Instantiate RateLimiterService
    - Call service.check_limit(request.client_id, request.tier, session)
    - Map result tuple to CheckResponse schema
    - Handle exceptions and return appropriate status codes (400, 422, 500)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 9.5_
  
  - [x] 6.3 Implement GET /v1/limiter/usage/{client_id} endpoint in `backend/app/main.py`
    - Define route handler with client_id path parameter and session dependency
    - Instantiate RateLimiterService
    - Call service.get_usage(client_id, session)
    - Map result dict to UsageResponse schema
    - Handle ValueError (client not found) with 404 response
    - Handle other exceptions with 500 response
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 9.5_
  
  - [ ]* 6.4 Write property test for check response schema completeness
    - **Property 16: Check Response Schema Completeness**
    - **Validates: Requirements 10.1**
    - Verify all successful check responses contain exactly three fields with correct types
  
  - [ ]* 6.5 Write property test for usage response schema completeness
    - **Property 17: Usage Response Schema Completeness**
    - **Validates: Requirements 10.2**
    - Verify all successful usage responses contain exactly four fields with correct types

- [ ] 7. Implement comprehensive test suite
  - [x] 7.1 Set up test fixtures in `backend/tests/conftest.py`
    - Create in-memory SQLite engine fixture ("sqlite:///:memory:")
    - Create test_session fixture that yields clean database session
    - Create test_client fixture using FastAPI TestClient with test database
    - Create clean_service fixture returning fresh RateLimiterService instance
    - _Requirements: 9.6, 9.7_
  
  - [x] 7.2 Write unit tests for endpoint routing in `backend/tests/test_main.py`
    - Test POST /v1/limiter/check returns 200 for valid request
    - Test GET /v1/limiter/usage/{client_id} returns 200 for existing client
    - Test POST /v1/limiter/check returns 400 for missing client_id field
    - Test POST /v1/limiter/check returns 422 for invalid data types
    - Test POST /v1/limiter/check returns 400 for malformed JSON
    - _Requirements: 3.1, 3.7, 4.1, 11.2, 11.3, 12.2_
  
  - [x] 7.3 Write unit tests for error scenarios in `backend/tests/test_main.py`
    - Test GET /v1/limiter/usage/{client_id} returns 404 for non-existent client
    - Test endpoints return 500 on database failures (mock session failure)
    - Test error responses include "detail" field with descriptive messages
    - _Requirements: 4.5, 10.3, 10.4, 11.1_
  
  - [x] 7.4 Write unit tests for rate limiting edge cases in `backend/tests/test_main.py`
    - Test first request for new client (empty window state)
    - Test request exactly at tier limit (boundary condition)
    - Test requests after all timestamps expired (window reset)
    - Test rapid consecutive requests within same second
    - _Requirements: 2.1, 2.2, 2.3, 5.3_
  
  - [ ]* 7.5 Write integration unit tests for state persistence in `backend/tests/test_main.py`
    - Test ClientMetadata created on first check request
    - Test UsageLog increments across multiple allowed requests
    - Test usage endpoint aggregates total_requests correctly
    - Test rate limiting continues if database unavailable (graceful degradation)
    - _Requirements: 5.4, 6.1, 6.3, 6.4, 11.4, 11.5_
  
  - [ ]* 7.6 Write property test for allowed request timestamp recording
    - **Property 5: Allowed Request Timestamp Recording**
    - **Validates: Requirements 2.3**
    - For any allowed request, verify timestamp appears in window_state immediately after
  
  - [ ]* 7.7 Write property test for request count equals window state size
    - **Property 6: Request Count Equals Window State Size**
    - **Validates: Requirements 2.5**
    - After N requests in 60s window, verify len(window_state) = N
  
  - [ ]* 7.8 Write property test for reset time calculation
    - **Property 8: Reset Time Calculation**
    - **Validates: Requirements 3.6**
    - Verify reset_in_seconds = 60 - (current_time - oldest_timestamp) when window non-empty
  
  - [ ]* 7.9 Write property test for concurrent request atomicity
    - **Property 9: Concurrent Request Atomicity**
    - **Validates: Requirements 3.8, 8.2**
    - Send N concurrent requests at limit and verify total allowed ≤ (tier_limit - initial_count)
  
  - [ ]* 7.10 Write property test for client metadata persistence
    - **Property 11: Client Metadata Persistence**
    - **Validates: Requirements 6.1**
    - Verify ClientMetadata record exists after first request
  
  - [ ]* 7.11 Write property test for usage log persistence and increment
    - **Property 12: Usage Log Persistence and Increment**
    - **Validates: Requirements 6.2, 6.3**
    - Verify sum of request_count increases by N for N allowed requests
  
  - [ ]* 7.12 Write property test for concurrent request confluence
    - **Property 15: Concurrent Request Confluence**
    - **Validates: Requirements 8.4**
    - Verify final window_state contains same timestamps regardless of processing order
  
  - [ ]* 7.13 Write property test for request serialization round-trip
    - **Property 18: Request Serialization Round-Trip**
    - **Validates: Requirements 12.5**
    - Verify CheckRequest → JSON → CheckRequest → JSON produces identical structures

- [ ] 8. Final checkpoint - Integration verification and testing
  - Run full test suite: `pytest backend/tests/ --cov=backend/app`
  - Verify all endpoints return correct status codes and response schemas
  - Verify concurrent request handling with multiple clients
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP delivery
- Property tests validate universal correctness properties across 100+ randomized iterations
- Unit tests focus on specific examples, edge cases, and integration scenarios
- Each task references specific requirements from the requirements document for traceability
- Database persistence is best-effort during rate limit checks (graceful degradation pattern)
- Per-client locks ensure atomic check-and-increment operations for concurrent requests
- Inactive client cleanup prevents unbounded memory growth in long-running deployments
- All code follows repository structure standards defined in `.kiro/steering/structure.md`
