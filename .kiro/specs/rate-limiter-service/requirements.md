# Requirements Document

## Introduction

The Rate Limiter Service is a multi-tenant API rate limiting and metering system designed for the DigitalOcean App Platform. The service enforces tier-based request quotas using a sliding window log algorithm, provides real-time limit checking, exposes historical usage metrics, and maintains client metadata with persistence. The service handles concurrent requests, unknown client tiers, and stale window eviction gracefully.

## Glossary

- **Rate_Limiter_Service**: The system responsible for enforcing rate limits and tracking usage
- **Client**: An API consumer identified by a unique client_id
- **Tier**: A subscription level that determines rate limits (free, pro)
- **Sliding_Window**: A time-based algorithm that tracks requests within a rolling time period
- **Request_Window**: A 60-second period used to calculate rate limits
- **Check_Endpoint**: The POST /v1/limiter/check API endpoint
- **Usage_Endpoint**: The GET /v1/limiter/usage/{client_id} API endpoint
- **Window_State**: In-memory data structure tracking timestamps of recent requests
- **Client_Metadata**: Persistent storage of client tier and configuration
- **Usage_Log**: Historical record of client request counts
- **Session_Dependency**: FastAPI dependency injection pattern for database access
- **Backend_Package**: The backend/app/ directory containing runtime logic
- **Test_Package**: The backend/tests/ directory containing test cases

## Requirements

### Requirement 1: Multi-Tenant Rate Limiting

**User Story:** As a platform operator, I want to enforce different rate limits based on client subscription tiers, so that free and pro users have appropriate resource allocations.

#### Acceptance Criteria

1. WHEN a client with tier "free" makes a request, THE Rate_Limiter_Service SHALL enforce a limit of 10 requests per Request_Window
2. WHEN a client with tier "pro" makes a request, THE Rate_Limiter_Service SHALL enforce a limit of 100 requests per Request_Window
3. WHEN a client provides an unknown tier value, THE Rate_Limiter_Service SHALL default to tier "free" with 10 requests per Request_Window
4. THE Rate_Limiter_Service SHALL treat tier values in a case-insensitive manner

### Requirement 2: Sliding Window Algorithm Implementation

**User Story:** As a platform operator, I want to use sliding window log rate limiting, so that burst traffic is handled accurately without aliasing effects.

#### Acceptance Criteria

1. THE Rate_Limiter_Service SHALL maintain a Window_State containing timestamps of all requests within the current Request_Window
2. WHEN evaluating a rate limit check, THE Rate_Limiter_Service SHALL count only requests with timestamps within the last 60 seconds from the current time
3. WHEN a request is allowed, THE Rate_Limiter_Service SHALL add the current timestamp to the Window_State for that Client
4. THE Rate_Limiter_Service SHALL evict timestamps older than 60 seconds from the Window_State
5. FOR ALL valid Request_Window periods, the count of timestamps in Window_State SHALL equal the count of requests made during that period (round-trip property)

### Requirement 3: Rate Limit Check Endpoint

**User Story:** As an API consumer, I want to check if my request is allowed under rate limits, so that I can avoid failed requests and understand my quota status.

#### Acceptance Criteria

1. THE Check_Endpoint SHALL accept POST requests at path "/v1/limiter/check"
2. WHEN the Check_Endpoint receives a request, THE Rate_Limiter_Service SHALL validate the presence of required fields "client_id" and "tier"
3. WHEN the Check_Endpoint receives a valid request and the Client has not exceeded their rate limit, THE Rate_Limiter_Service SHALL return status 200 with "allowed" set to true
4. WHEN the Check_Endpoint receives a valid request and the Client has exceeded their rate limit, THE Rate_Limiter_Service SHALL return status 200 with "allowed" set to false
5. THE Check_Endpoint SHALL return "remaining" as an integer representing requests remaining in the current Request_Window
6. THE Check_Endpoint SHALL return "reset_in_seconds" as an integer representing seconds until the oldest request timestamp expires from the Window_State
7. WHEN the Check_Endpoint receives a request with missing required fields, THE Rate_Limiter_Service SHALL return status 400 with a descriptive error message
8. WHEN multiple requests for the same Client arrive concurrently, THE Rate_Limiter_Service SHALL process them atomically to prevent race conditions

### Requirement 4: Usage Reporting Endpoint

**User Story:** As an API consumer, I want to view my historical usage and current window status, so that I can monitor my consumption patterns and tier status.

#### Acceptance Criteria

1. THE Usage_Endpoint SHALL accept GET requests at path "/v1/limiter/usage/{client_id}"
2. WHEN the Usage_Endpoint receives a request, THE Rate_Limiter_Service SHALL return the total historical request count for that Client
3. THE Usage_Endpoint SHALL return the current Request_Window usage as a percentage of the tier limit
4. THE Usage_Endpoint SHALL return the Client tier
5. WHEN the Usage_Endpoint receives a request for a non-existent Client, THE Rate_Limiter_Service SHALL return status 404 with a descriptive error message
6. THE Usage_Endpoint SHALL return status 200 with usage data when the Client exists

### Requirement 5: In-Memory State Management

**User Story:** As a platform operator, I want request window state maintained in memory, so that rate limit checks are fast and responsive.

#### Acceptance Criteria

1. THE Rate_Limiter_Service SHALL store Window_State in memory using a data structure that supports fast timestamp insertion and range queries
2. WHEN the Rate_Limiter_Service starts, THE Rate_Limiter_Service SHALL initialize an empty Window_State for each Client
3. WHEN a Client makes their first request, THE Rate_Limiter_Service SHALL create a new Window_State entry for that Client
4. THE Rate_Limiter_Service SHALL maintain Window_State entries in memory for the duration of the service runtime
5. WHEN the Rate_Limiter_Service evaluates rate limits, THE Rate_Limiter_Service SHALL complete the evaluation within 50 milliseconds for 95% of requests

### Requirement 6: Persistent Client Metadata and Usage Logs

**User Story:** As a platform operator, I want client metadata and usage logs persisted to a database, so that data survives service restarts and supports auditing.

#### Acceptance Criteria

1. THE Rate_Limiter_Service SHALL store Client_Metadata in a SQLModel database table including client_id and tier
2. THE Rate_Limiter_Service SHALL store Usage_Log entries in a SQLModel database table including client_id, timestamp, and request count
3. WHEN the Check_Endpoint processes an allowed request, THE Rate_Limiter_Service SHALL increment the historical request count in the database
4. WHEN the Usage_Endpoint is called, THE Rate_Limiter_Service SHALL retrieve Client_Metadata and aggregate Usage_Log entries from the database
5. THE Rate_Limiter_Service SHALL use Session_Dependency pattern for database access in all endpoints
6. WHEN the Rate_Limiter_Service starts up, THE Rate_Limiter_Service SHALL initialize the database engine and create tables if they do not exist

### Requirement 7: Stale Window Eviction

**User Story:** As a platform operator, I want stale window data automatically evicted, so that memory usage remains bounded and calculations stay accurate.

#### Acceptance Criteria

1. WHEN the Rate_Limiter_Service evaluates a rate limit check, THE Rate_Limiter_Service SHALL remove all timestamps older than 60 seconds from the Window_State
2. WHEN a Client has no requests in the last 300 seconds, THE Rate_Limiter_Service SHALL remove the Window_State entry for that Client from memory
3. THE Rate_Limiter_Service SHALL perform stale entry eviction without blocking concurrent rate limit checks
4. FOR ALL Window_State entries, the number of timestamps SHALL NOT exceed the maximum tier limit plus 100 (invariant property)

### Requirement 8: Concurrent Request Handling

**User Story:** As a platform operator, I want concurrent requests from the same client handled correctly, so that race conditions do not allow quota violations.

#### Acceptance Criteria

1. WHEN multiple requests for the same Client arrive simultaneously, THE Rate_Limiter_Service SHALL serialize access to that Client's Window_State
2. WHEN processing concurrent requests, THE Rate_Limiter_Service SHALL ensure that the sum of "allowed" responses does not exceed the Client's tier limit
3. THE Rate_Limiter_Service SHALL use thread-safe or async-safe data structures for Window_State management
4. FOR ALL concurrent request sequences, the final Window_State SHALL be equivalent to processing requests in any serial order (confluence property)

### Requirement 9: Repository Structure Compliance

**User Story:** As a developer, I want the codebase to follow the established repository structure, so that the service integrates with existing deployment and testing infrastructure.

#### Acceptance Criteria

1. THE Rate_Limiter_Service SHALL implement all runtime logic in the Backend_Package
2. THE Rate_Limiter_Service SHALL define SQLModel database engine and Session_Dependency in backend/app/database.py
3. THE Rate_Limiter_Service SHALL define SQLModel tables and Pydantic request/response schemas in backend/app/models.py
4. THE Rate_Limiter_Service SHALL implement domain logic and Window_State management in backend/app/service.py
5. THE Rate_Limiter_Service SHALL implement FastAPI endpoints in backend/app/main.py using dependency injection
6. THE Rate_Limiter_Service SHALL implement all test cases in the Test_Package
7. THE Rate_Limiter_Service SHALL provide test fixtures in backend/tests/conftest.py for TestClient and in-memory database context

### Requirement 10: API Response Schema Compliance

**User Story:** As an API consumer, I want consistent and well-defined response schemas, so that I can reliably parse responses and handle errors.

#### Acceptance Criteria

1. WHEN the Check_Endpoint returns a successful response, THE Rate_Limiter_Service SHALL include exactly three fields: "allowed" (boolean), "remaining" (integer), and "reset_in_seconds" (integer)
2. WHEN the Usage_Endpoint returns a successful response, THE Rate_Limiter_Service SHALL include exactly four fields: "client_id" (string), "tier" (string), "total_requests" (integer), and "current_window_usage_percent" (float)
3. WHEN any endpoint returns an error response, THE Rate_Limiter_Service SHALL include a "detail" field with a descriptive error message
4. THE Rate_Limiter_Service SHALL return responses with appropriate HTTP status codes (200 for success, 400 for bad request, 404 for not found, 500 for server error)
5. THE Rate_Limiter_Service SHALL return responses with Content-Type "application/json"

### Requirement 11: Error Handling and Resilience

**User Story:** As a platform operator, I want the service to handle errors gracefully, so that transient failures do not cause service outages.

#### Acceptance Criteria

1. WHEN a database operation fails, THE Rate_Limiter_Service SHALL log the error and return status 500 with a generic error message
2. WHEN the Check_Endpoint receives malformed JSON, THE Rate_Limiter_Service SHALL return status 400 with a descriptive error message
3. WHEN the Check_Endpoint receives a request with invalid data types, THE Rate_Limiter_Service SHALL return status 422 with field-level validation errors
4. IF the database is unavailable, THEN THE Rate_Limiter_Service SHALL continue serving rate limit checks using in-memory Window_State
5. WHEN the database becomes available after an outage, THE Rate_Limiter_Service SHALL resume persisting Usage_Log entries

### Requirement 12: Request Validation Parser

**User Story:** As a developer, I want request validation to parse and validate incoming JSON payloads, so that invalid requests are rejected before processing.

#### Acceptance Criteria

1. WHEN the Check_Endpoint receives a request, THE Request_Parser SHALL parse the JSON body into a validated request object
2. WHEN the Request_Parser encounters invalid JSON syntax, THE Rate_Limiter_Service SHALL return a descriptive error indicating the syntax error location
3. THE Request_Parser SHALL validate that "client_id" is a non-empty string
4. THE Request_Parser SHALL validate that "tier" is a string
5. FOR ALL valid request objects, serializing then parsing then serializing SHALL produce an equivalent JSON structure (round-trip property)

