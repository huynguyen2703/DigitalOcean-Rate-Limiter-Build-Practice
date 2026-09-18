# Rate Limiter Service

A multi-tenant API rate limiting and metering service for DigitalOcean App Platform. Implements sliding window log algorithm for accurate burst traffic handling with tier-based quotas.

## Features

- **Multi-Tenant Rate Limiting**: Tier-based quotas (free: 10 req/min, pro: 100 req/min)
- **Sliding Window Algorithm**: Accurate burst detection without fixed-window aliasing
- **High Performance**: Sub-50ms rate limit checks using in-memory state
- **Concurrent-Safe**: Per-client locks prevent race conditions
- **Graceful Degradation**: Rate limiting continues if database unavailable
- **Historical Analytics**: Persistent usage logs and client metadata
- **Automatic Cleanup**: Memory-bounded with inactive client eviction

## Architecture

```
FastAPI Layer (main.py)
    ↓ Depends(get_session)
Service Layer (service.py)
    ↓ In-memory state + asyncio.Lock
Data Layer (models.py, database.py)
    ↓ SQLModel + SQLite
```

**Key Components:**
- **In-memory state**: `dict[str, deque[float]]` for timestamp tracking
- **Concurrency control**: Per-client `asyncio.Lock` for atomic operations
- **Persistence**: SQLite with ClientMetadata and UsageLog tables
- **Cleanup**: Background task removes inactive clients (>300s idle)

## API Endpoints

### Check Rate Limit
```http
POST /v1/limiter/check
Content-Type: application/json

{
  "client_id": "client-abc-123",
  "tier": "pro"
}
```

**Response (200 OK):**
```json
{
  "allowed": true,
  "remaining": 7,
  "reset_in_seconds": 45
}
```

**Status Codes:**
- `200` - Request processed (check `allowed` field)
- `422` - Invalid request (missing/empty client_id, wrong data types)
- `500` - Internal server error

### Get Usage Statistics
```http
GET /v1/limiter/usage/{client_id}
```

**Response (200 OK):**
```json
{
  "client_id": "client-abc-123",
  "tier": "pro",
  "total_requests": 15420,
  "current_window_usage_percent": 73.5
}
```

**Status Codes:**
- `200` - Usage data retrieved
- `404` - Client not found
- `500` - Internal server error

## Installation

### Prerequisites
- Python 3.10+
- pip

### Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Run the service
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

### Docker Deployment
```bash
# Build image
docker build -t rate-limiter-service .

# Run container
docker run -p 8000:8000 rate-limiter-service
```

## Configuration

### Tier Limits
Configure in `backend/app/service.py`:
```python
TIER_LIMITS = {
    "free": 10,    # requests per 60 seconds
    "pro": 100     # requests per 60 seconds
}
```

### Database
Default: SQLite at `./rate_limiter.db`

Configure in `backend/app/database.py`:
```python
DATABASE_URL = "sqlite:///./rate_limiter.db"
```

### Cleanup Settings
Configure in `backend/app/service.py`:
```python
window_size = 60          # seconds (sliding window duration)
inactive_threshold = 300  # seconds (cleanup threshold)
```

## Testing

### Run All Tests
```bash
pytest backend/tests/
```

### Run with Coverage
```bash
pytest --cov=backend/app backend/tests/
```

### Test Categories
- **Endpoint routing**: 200, 404, 422 responses
- **Rate limiting**: Free/pro tier enforcement
- **Edge cases**: Boundary conditions, window resets, rapid requests
- **Error handling**: Database failures, validation errors
- **Concurrency**: Atomic operations, race condition prevention

**Total: 24 tests**

## Project Structure

```
backend/
├── app/
│   ├── __init__.py
│   ├── database.py      # Engine, session dependency
│   ├── models.py        # SQLModel tables, Pydantic schemas
│   ├── service.py       # RateLimiterService, sliding window logic
│   └── main.py          # FastAPI endpoints, lifespan handler
└── tests/
    ├── __init__.py
    ├── conftest.py      # Test fixtures
    └── test_main.py     # Integration tests
```

## How It Works

### Sliding Window Algorithm

1. **Request arrives** → Acquire per-client lock
2. **Evict stale timestamps** → Remove entries >60s old from deque
3. **Count requests** → Check deque length against tier limit
4. **If under limit**:
   - Append current timestamp to deque
   - Persist to database (best-effort)
   - Return `allowed=true`
5. **If over limit**:
   - Return `allowed=false`
6. **Calculate metrics** → remaining quota, reset time
7. **Release lock**

### Concurrency Handling

- **Per-client locks**: Each `client_id` gets unique `asyncio.Lock`
- **Atomic operations**: Lock held during eviction + count + append
- **Parallel processing**: Different clients processed concurrently
- **Race condition prevention**: Same client requests serialized

### Memory Management

**Automatic Eviction:**
- **Stale timestamps**: Removed on every rate limit check (>60s old)
- **Inactive clients**: Removed every 60s by background task (>300s idle)

**Memory bounds:**
- Max timestamps per client: 200 (tier limit + buffer)
- Inactive clients cleaned up automatically
- No unbounded growth in long-running deployments

### Graceful Degradation

**Database unavailable?**
- ✅ Rate limiting **continues** using in-memory state
- ⚠️ Usage logs **not persisted** (logged as warning)
- ✅ Full functionality **resumes** when database available

## Performance

- **Rate limit checks**: <50ms for 95% of requests
- **Eviction complexity**: O(k) where k = expired timestamps
- **Lock contention**: Minimal (per-client, <1ms hold time)
- **Memory usage**: Bounded by (active_clients × tier_limit × 8 bytes)

## API Validation

### Request Validation
- `client_id`: Non-empty string (min_length=1, max 255 chars)
- `tier`: String (case-insensitive, defaults to "free" if unknown)

### Response Validation
- `allowed`: Boolean
- `remaining`: Non-negative integer
- `reset_in_seconds`: Non-negative integer (0-60 range)
- `current_window_usage_percent`: Float (0.0-100.0 range)

### Unknown Tier Behavior
Unknown or invalid tiers automatically default to "free" tier limits (10 requests/60s).

```bash
# These are equivalent:
{"tier": "free"} → 10 requests/60s
{"tier": "unknown"} → 10 requests/60s
{"tier": "invalid-tier-xyz"} → 10 requests/60s
```

## Error Handling

### Client Errors (4xx)
- **422 Unprocessable Entity**: Invalid data types, empty client_id, validation failures
- **404 Not Found**: Client does not exist (usage endpoint only)

### Server Errors (5xx)
- **500 Internal Server Error**: Database failures, unexpected exceptions

### Error Response Format
```json
{
  "detail": "Descriptive error message"
}
```

## Monitoring

### Health Check
```http
GET /health
```

**Response:**
```json
{
  "status": "ok"
}
```

### Logs
- **INFO**: Successful operations, service lifecycle
- **WARNING**: Database persistence failures
- **ERROR**: Unexpected exceptions, database connection failures
- **DEBUG**: Lock acquisition, eviction operations, cleanup runs

## Database Schema

### ClientMetadata Table
```sql
CREATE TABLE client_metadata (
    client_id VARCHAR(255) PRIMARY KEY,
    tier VARCHAR(50) DEFAULT 'free',
    created_at DATETIME,
    updated_at DATETIME
);
CREATE INDEX ix_client_metadata_client_id ON client_metadata (client_id);
```

### UsageLog Table
```sql
CREATE TABLE usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id VARCHAR(255),
    timestamp DATETIME,
    request_count INTEGER DEFAULT 1
);
CREATE INDEX ix_usage_log_client_id ON usage_log (client_id);
CREATE INDEX ix_usage_log_timestamp ON usage_log (timestamp);
```

## Development

### Local Development
```bash
# Install dev dependencies
pip install -r requirements.txt

# Run with auto-reload
uvicorn backend.app.main:app --reload

# Run tests
pytest backend/tests/ -v
```

### Adding New Tiers
1. Update `TIER_LIMITS` in `backend/app/service.py`
2. Add tests for new tier in `backend/tests/test_main.py`
3. Update documentation

## Production Considerations

### Scalability
- **Current**: Single-instance with in-memory state
- **Scale-out**: Requires distributed state (Redis) or consistent hashing
- **Database**: Consider PostgreSQL for production workloads

### Monitoring
- Track rate limit rejections per client
- Monitor database persistence failure rates
- Alert on cleanup task failures

### Security
- Add authentication/authorization for endpoints
- Rate limit the rate limiter (prevent abuse)
- Validate client_id format to prevent injection

## License

[Your License Here]

## Support

For issues or questions, please contact [Your Contact Info].
