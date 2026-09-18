"""
Data Models: SQLModel database tables and Pydantic schemas for Rate Limiter Service.

This module defines:
- ClientMetadata: Persistent client configuration and tier information (SQLModel)
- UsageLog: Historical log of client requests for analytics and auditing (SQLModel)
- CheckRequest: Request schema for POST /v1/limiter/check (Pydantic)
- CheckResponse: Response schema for POST /v1/limiter/check (Pydantic)
- UsageResponse: Response schema for GET /v1/limiter/usage/{client_id} (Pydantic)
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Field, SQLModel


class ClientMetadata(SQLModel, table=True):
    """
    Persistent client configuration and tier information.
    
    Table: client_metadata
    
    Fields:
        client_id: Unique client identifier (primary key, indexed)
        tier: Subscription level (free, pro) - defaults to "free"
        created_at: Timestamp when client record was created
        updated_at: Timestamp when client record was last updated
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
    
    Fields:
        id: Auto-incrementing primary key
        client_id: Client identifier (indexed for fast lookups)
        timestamp: When the request occurred (indexed for time-range queries)
        request_count: Number of requests (typically 1 per log entry)
    """
    __tablename__ = "usage_log"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: str = Field(index=True, max_length=255)
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)
    request_count: int = Field(default=1)


# ============================================================================
# API Request/Response Schemas (Pydantic)
# ============================================================================


class CheckRequest(BaseModel):
    """
    Request schema for POST /v1/limiter/check
    
    Validation:
        - client_id: non-empty string (min_length=1)
        - tier: string (will be normalized to lowercase in service layer)
    
    Fields:
        client_id: Unique client identifier
        tier: Client tier (free, pro)
    """
    client_id: str = PydanticField(..., min_length=1, description="Unique client identifier")
    tier: str = PydanticField(..., description="Client tier (free, pro)")
    
    class Config:
        json_schema_extra = {
            "example": {
                "client_id": "client-abc-123",
                "tier": "pro"
            }
        }


class CheckResponse(BaseModel):
    """
    Response schema for POST /v1/limiter/check
    
    Fields:
        - allowed: Whether the request is permitted
        - remaining: Requests remaining in current window (non-negative)
        - reset_in_seconds: Time until oldest timestamp expires (non-negative)
    """
    allowed: bool
    remaining: int = PydanticField(ge=0)
    reset_in_seconds: int = PydanticField(ge=0)
    
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
        - total_requests: Historical request count (all time, non-negative)
        - current_window_usage_percent: Percentage of tier limit used in current window (0.0-100.0)
    """
    client_id: str
    tier: str
    total_requests: int = PydanticField(ge=0)
    current_window_usage_percent: float = PydanticField(ge=0.0, le=100.0)
    
    class Config:
        json_schema_extra = {
            "example": {
                "client_id": "client-abc-123",
                "tier": "pro",
                "total_requests": 15420,
                "current_window_usage_percent": 73.5
            }
        }
