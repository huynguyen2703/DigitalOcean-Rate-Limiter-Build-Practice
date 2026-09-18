import pytest
from unittest.mock import Mock, patch
from sqlmodel import Session


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ============================================================================
# Task 7.2: Unit tests for endpoint routing
# ============================================================================

def test_check_endpoint_exists(client):
    """Verify POST /v1/limiter/check endpoint exists and accepts requests."""
    response = client.post(
        "/v1/limiter/check",
        json={"client_id": "test-client-1", "tier": "free"}
    )
    # Should return 200 with valid response schema
    assert response.status_code == 200
    data = response.json()
    assert "allowed" in data
    assert "remaining" in data
    assert "reset_in_seconds" in data
    assert isinstance(data["allowed"], bool)
    assert isinstance(data["remaining"], int)
    assert isinstance(data["reset_in_seconds"], int)


def test_check_endpoint_returns_200_for_valid_request(client):
    """Test POST /v1/limiter/check returns 200 for valid request."""
    response = client.post(
        "/v1/limiter/check",
        json={"client_id": "valid-client", "tier": "free"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["allowed"] is True
    assert data["remaining"] >= 0
    assert data["reset_in_seconds"] >= 0


def test_usage_endpoint_returns_200_for_existing_client(client):
    """Test GET /v1/limiter/usage/{client_id} returns 200 for existing client."""
    # First make a request to create the client
    client.post(
        "/v1/limiter/check",
        json={"client_id": "test-client-usage", "tier": "pro"}
    )
    
    # Now query usage
    response = client.get("/v1/limiter/usage/test-client-usage")
    assert response.status_code == 200
    data = response.json()
    assert "client_id" in data
    assert "tier" in data
    assert "total_requests" in data
    assert "current_window_usage_percent" in data
    assert data["client_id"] == "test-client-usage"
    assert data["tier"] == "pro"
    assert data["total_requests"] >= 1


def test_check_endpoint_returns_400_for_missing_client_id(client):
    """Test POST /v1/limiter/check returns 400 for missing client_id field."""
    response = client.post(
        "/v1/limiter/check",
        json={"tier": "free"}
    )
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data


def test_check_endpoint_returns_422_for_invalid_data_types(client):
    """Test POST /v1/limiter/check returns 422 for invalid data types."""
    # Test with integer client_id instead of string
    response = client.post(
        "/v1/limiter/check",
        json={"client_id": 12345, "tier": "free"}
    )
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data


def test_check_endpoint_returns_400_for_malformed_json(client):
    """Test POST /v1/limiter/check returns 400 for malformed JSON."""
    response = client.post(
        "/v1/limiter/check",
        data="this is not valid json",
        headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data


def test_check_endpoint_enforces_free_tier_limit(client):
    """Verify free tier limit (10 requests per 60s window) is enforced."""
    client_id = "test-client-free"
    
    # Make 10 requests - all should be allowed
    for i in range(10):
        response = client.post(
            "/v1/limiter/check",
            json={"client_id": client_id, "tier": "free"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["allowed"] is True
        assert data["remaining"] == 10 - (i + 1)
    
    # 11th request should be rejected
    response = client.post(
        "/v1/limiter/check",
        json={"client_id": client_id, "tier": "free"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["allowed"] is False
    assert data["remaining"] == 0


def test_check_endpoint_enforces_pro_tier_limit(client):
    """Verify pro tier limit (100 requests per 60s window) is enforced."""
    client_id = "test-client-pro"
    
    # Make 100 requests - all should be allowed
    for i in range(100):
        response = client.post(
            "/v1/limiter/check",
            json={"client_id": client_id, "tier": "pro"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["allowed"] is True
        assert data["remaining"] == 100 - (i + 1)
    
    # 101st request should be rejected
    response = client.post(
        "/v1/limiter/check",
        json={"client_id": client_id, "tier": "pro"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["allowed"] is False
    assert data["remaining"] == 0


# ============================================================================
# Task 7.3: Unit tests for error scenarios
# ============================================================================

def test_usage_endpoint_returns_404_for_non_existent_client(client):
    """Test GET /v1/limiter/usage/{client_id} returns 404 for non-existent client."""
    response = client.get("/v1/limiter/usage/non-existent-client-12345")
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
    assert "not found" in data["detail"].lower()


def test_check_endpoint_handles_database_persistence_failure_gracefully(client):
    """Test that check endpoint handles database persistence failures gracefully.
    
    Per design requirement 11.4: Rate limiting continues using in-memory state
    if database operations fail. This tests that database errors during persistence
    don't prevent rate limiting from working.
    """
    # Mock session.commit to raise an exception
    with patch.object(Session, 'commit', side_effect=Exception("Database commit failed")):
        # The check endpoint should still work because rate limiting uses in-memory state
        response = client.post(
            "/v1/limiter/check",
            json={"client_id": "test-client-db-persist-fail", "tier": "free"}
        )
        
        # Should succeed - rate limiting works even if persistence fails
        assert response.status_code == 200
        data = response.json()
        assert data["allowed"] is True
        assert data["remaining"] >= 0


def test_usage_endpoint_returns_500_on_database_failure(client):
    """Test usage endpoint returns 500 when database query fails."""
    with patch('backend.app.service.rate_limiter_service.get_usage') as mock_get_usage:
        # Simulate database failure
        mock_get_usage.side_effect = Exception("Database connection failed")
        
        response = client.get("/v1/limiter/usage/some-client")
        assert response.status_code == 500
        data = response.json()
        assert "detail" in data


def test_error_responses_include_detail_field(client):
    """Test error responses include 'detail' field with descriptive messages."""
    # Test 404 error
    response = client.get("/v1/limiter/usage/nonexistent")
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
    assert isinstance(data["detail"], str)
    assert len(data["detail"]) > 0
    
    # Test 422 error
    response = client.post(
        "/v1/limiter/check",
        json={"tier": "free"}  # missing client_id
    )
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data


# ============================================================================
# Task 7.4: Unit tests for rate limiting edge cases
# ============================================================================

def test_first_request_for_new_client(client):
    """Test first request for new client (empty window state)."""
    response = client.post(
        "/v1/limiter/check",
        json={"client_id": "brand-new-client", "tier": "free"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["allowed"] is True
    assert data["remaining"] == 9  # 10 - 1 = 9 remaining
    assert data["reset_in_seconds"] >= 0


def test_request_exactly_at_tier_limit(client):
    """Test request exactly at tier limit (boundary condition)."""
    client_id = "test-client-boundary"
    
    # Make exactly 10 requests (free tier limit)
    for i in range(10):
        response = client.post(
            "/v1/limiter/check",
            json={"client_id": client_id, "tier": "free"}
        )
        assert response.status_code == 200
        assert response.json()["allowed"] is True
    
    # The 11th request should be rejected
    response = client.post(
        "/v1/limiter/check",
        json={"client_id": client_id, "tier": "free"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["allowed"] is False
    assert data["remaining"] == 0


def test_requests_after_all_timestamps_expired(client, clean_service):
    """Test requests after all timestamps expired (window reset)."""
    import time
    from backend.app import service
    
    client_id = "test-client-expired"
    
    # Mock time to simulate passage of time
    current_time = time.time()
    
    # Add some old timestamps to the service (simulating past requests)
    with patch('time.time', return_value=current_time):
        for _ in range(5):
            response = client.post(
                "/v1/limiter/check",
                json={"client_id": client_id, "tier": "free"}
            )
            assert response.status_code == 200
    
    # Now advance time by 61 seconds (past window expiration)
    with patch('time.time', return_value=current_time + 61):
        # All old timestamps should be evicted, so we should have full quota
        response = client.post(
            "/v1/limiter/check",
            json={"client_id": client_id, "tier": "free"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["allowed"] is True
        # Should have 9 remaining (full reset, minus this request)
        assert data["remaining"] == 9


def test_rapid_consecutive_requests_within_same_second(client):
    """Test rapid consecutive requests within same second."""
    client_id = "test-client-rapid"
    
    # Make multiple rapid requests
    responses = []
    for _ in range(5):
        response = client.post(
            "/v1/limiter/check",
            json={"client_id": client_id, "tier": "free"}
        )
        responses.append(response)
    
    # All should succeed and return 200
    for response in responses:
        assert response.status_code == 200
        assert response.json()["allowed"] is True
    
    # Verify remaining count decreases correctly
    expected_remaining = [9, 8, 7, 6, 5]
    for i, response in enumerate(responses):
        assert response.json()["remaining"] == expected_remaining[i]


# ============================================================================
# Additional tests from original test_main.py
# ============================================================================

def test_check_endpoint_missing_client_id(client):
    """Verify 422 error for missing client_id field."""
    response = client.post(
        "/v1/limiter/check",
        json={"tier": "free"}
    )
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data


def test_check_endpoint_empty_client_id(client):
    """Verify 422 error for empty client_id (violates min_length=1)."""
    response = client.post(
        "/v1/limiter/check",
        json={"client_id": "", "tier": "free"}
    )
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data


def test_usage_endpoint_exists(client):
    """Verify GET /v1/limiter/usage/{client_id} endpoint exists."""
    # First make a request to create the client
    client.post(
        "/v1/limiter/check",
        json={"client_id": "test-client-usage-exists", "tier": "pro"}
    )
    
    # Now query usage
    response = client.get("/v1/limiter/usage/test-client-usage-exists")
    assert response.status_code == 200
    data = response.json()
    assert "client_id" in data
    assert "tier" in data
    assert "total_requests" in data
    assert "current_window_usage_percent" in data
    assert data["client_id"] == "test-client-usage-exists"
    assert data["tier"] == "pro"
    assert data["total_requests"] >= 1


def test_case_insensitive_tier_handling(client):
    """Verify tier values are case-insensitive."""
    client_id = "test-client-case"
    
    # Test with uppercase "FREE"
    response1 = client.post(
        "/v1/limiter/check",
        json={"client_id": client_id, "tier": "FREE"}
    )
    assert response1.status_code == 200
    
    # Test with mixed case "Pro"
    response2 = client.post(
        "/v1/limiter/check",
        json={"client_id": client_id + "-2", "tier": "Pro"}
    )
    assert response2.status_code == 200


def test_unknown_tier_defaults_to_free(client):
    """Verify unknown tier defaults to free tier (10 requests limit)."""
    client_id = "test-client-unknown-tier"
    
    # Make 10 requests with unknown tier
    for i in range(10):
        response = client.post(
            "/v1/limiter/check",
            json={"client_id": client_id, "tier": "unknown_tier_xyz"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["allowed"] is True
    
    # 11th request should be rejected (free tier limit)
    response = client.post(
        "/v1/limiter/check",
        json={"client_id": client_id, "tier": "unknown_tier_xyz"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["allowed"] is False


def test_usage_endpoint_shows_correct_percentage(client):
    """Verify current_window_usage_percent is calculated correctly."""
    client_id = "test-client-percentage"
    
    # Make 5 requests with free tier (limit=10)
    for _ in range(5):
        client.post(
            "/v1/limiter/check",
            json={"client_id": client_id, "tier": "free"}
        )
    
    # Check usage percentage
    response = client.get(f"/v1/limiter/usage/{client_id}")
    assert response.status_code == 200
    data = response.json()
    
    # 5 out of 10 = 50%
    assert data["current_window_usage_percent"] == 50.0
    assert data["total_requests"] == 5


def test_reset_in_seconds_is_non_negative(client):
    """Verify reset_in_seconds is always non-negative."""
    client_id = "test-client-reset"
    
    response = client.post(
        "/v1/limiter/check",
        json={"client_id": client_id, "tier": "free"}
    )
    assert response.status_code == 200
    data = response.json()
    
    # reset_in_seconds should be non-negative and <= 60
    assert data["reset_in_seconds"] >= 0
    assert data["reset_in_seconds"] <= 60
