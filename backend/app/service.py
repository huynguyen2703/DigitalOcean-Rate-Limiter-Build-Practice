"""
Service Layer: RateLimiterService class implementing sliding window log algorithm.

This module provides:
- RateLimiterService: Core rate limiting logic with in-memory state management
- Sliding window eviction and timestamp tracking
- Atomic rate limit checks with per-client locking
- Best-effort database persistence with graceful degradation
- Inactive client cleanup for memory management
"""

import asyncio
import logging
import time
from collections import deque
from datetime import datetime
from typing import Dict, Tuple

from sqlmodel import Session, select

from backend.app.models import ClientMetadata, UsageLog

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RateLimiterService:
    """
    Manages in-memory rate limiting state and coordinates database persistence.
    
    State:
        - window_state: dict[str, deque[float]] - Timestamp logs per client
        - client_locks: dict[str, asyncio.Lock] - Per-client concurrency locks
        - last_access: dict[str, float] - Last request time for eviction tracking
    
    Configuration:
        - window_size: 60 seconds (sliding window duration)
        - inactive_threshold: 300 seconds (cleanup threshold)
        - TIER_LIMITS: {"free": 10, "pro": 100}
    """
    
    # Tier configuration constants
    TIER_LIMITS = {
        "free": 10,
        "pro": 100
    }
    
    def __init__(self):
        """Initialize RateLimiterService with empty state dictionaries."""
        self.window_state: Dict[str, deque[float]] = {}
        self.client_locks: Dict[str, asyncio.Lock] = {}
        self.last_access: Dict[str, float] = {}
        self.window_size: int = 60  # seconds
        self.inactive_threshold: int = 300  # seconds
        logger.info("RateLimiterService initialized")
    
    def _get_tier_limit(self, tier: str) -> int:
        """
        Get request limit for tier (case-insensitive, defaults to 'free').
        
        Args:
            tier: Tier name
        
        Returns:
            Request limit per 60-second window
        
        Requirements: 1.1, 1.2, 1.3, 1.4, 5.1, 5.2, 5.3
        """
        normalized = tier.lower().strip()
        return self.TIER_LIMITS.get(normalized, self.TIER_LIMITS["free"])
    
    def _evict_stale_timestamps(self, client_id: str, current_time: float) -> None:
        """
        Remove timestamps older than window_size from client's deque.
        
        Args:
            client_id: Client to evict timestamps for
            current_time: Current Unix timestamp
        
        Side Effects: Modifies self.window_state[client_id] in-place
        
        Requirements: 2.4, 7.1
        """
        if client_id not in self.window_state:
            return
        
        window = self.window_state[client_id]
        cutoff_time = current_time - self.window_size
        
        # Remove all timestamps older than cutoff_time
        while window and window[0] < cutoff_time:
            window.popleft()
    
    async def check_limit(
        self,
        client_id: str,
        tier: str,
        session: Session
    ) -> Tuple[bool, int, int]:
        """
        Check if request is allowed and update state.
        
        Args:
            client_id: Unique client identifier
            tier: Client tier (free/pro, case-insensitive)
            session: Database session for persistence
        
        Returns:
            (allowed, remaining, reset_in_seconds)
        
        Thread Safety: Acquires per-client lock for atomic check-and-increment
        
        Requirements: 2.2, 2.3, 2.5, 3.3, 3.4, 3.5, 3.6, 8.1, 8.2
        """
        current_time = time.time()
        tier_limit = self._get_tier_limit(tier)
        
        # Ensure client has a lock (create if not exists)
        if client_id not in self.client_locks:
            self.client_locks[client_id] = asyncio.Lock()
        
        # Acquire per-client lock for atomic operation
        async with self.client_locks[client_id]:
            # Initialize window state if this is the first request
            if client_id not in self.window_state:
                self.window_state[client_id] = deque()
            
            # Evict stale timestamps before counting
            self._evict_stale_timestamps(client_id, current_time)
            
            # Count current requests in window
            current_count = len(self.window_state[client_id])
            
            # Determine if request is allowed
            if current_count < tier_limit:
                # Request allowed - append timestamp
                self.window_state[client_id].append(current_time)
                
                # Persist to database (best-effort)
                await self._persist_usage_log(client_id, tier, session)
                
                # Update last access time
                self.last_access[client_id] = current_time
                
                # Calculate remaining and reset time
                remaining = tier_limit - (current_count + 1)
                reset_in_seconds = self._calculate_reset_time(client_id, current_time)
                
                return (True, remaining, reset_in_seconds)
            else:
                # Request rejected - over limit
                self.last_access[client_id] = current_time
                reset_in_seconds = self._calculate_reset_time(client_id, current_time)
                
                return (False, 0, reset_in_seconds)
    
    def _calculate_reset_time(self, client_id: str, current_time: float) -> int:
        """
        Calculate time until oldest timestamp expires from window.
        
        Args:
            client_id: Client identifier
            current_time: Current Unix timestamp
        
        Returns:
            Seconds until oldest timestamp expires (0 if window empty)
        """
        if client_id not in self.window_state or not self.window_state[client_id]:
            return 0
        
        oldest_timestamp = self.window_state[client_id][0]
        time_since_oldest = current_time - oldest_timestamp
        reset_time = max(0, int(self.window_size - time_since_oldest))
        
        return reset_time
    
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
        
        Requirements: 6.1, 6.2, 6.3, 11.4
        """
        try:
            # Create or retrieve ClientMetadata record
            statement = select(ClientMetadata).where(ClientMetadata.client_id == client_id)
            client_metadata = session.exec(statement).first()
            
            if not client_metadata:
                # Create new client metadata
                client_metadata = ClientMetadata(
                    client_id=client_id,
                    tier=tier.lower().strip()
                )
                session.add(client_metadata)
                logger.info(f"Created ClientMetadata for client_id={client_id}, tier={tier}")
            else:
                # Update existing client metadata timestamp
                client_metadata.updated_at = datetime.utcnow()
                session.add(client_metadata)
            
            # Insert UsageLog entry
            usage_log = UsageLog(
                client_id=client_id,
                timestamp=datetime.utcnow(),
                request_count=1
            )
            session.add(usage_log)
            session.commit()
            
            logger.debug(f"Persisted usage log for client_id={client_id}")
        
        except Exception as e:
            # Graceful degradation - log error but don't raise
            logger.warning(f"Failed to persist usage log for client_id={client_id}: {e}")
            session.rollback()
    
    async def get_usage(
        self,
        client_id: str,
        session: Session
    ) -> Dict[str, any]:
        """
        Retrieve usage statistics for a client.
        
        Args:
            client_id: Unique client identifier
            session: Database session
        
        Returns:
            dict with keys: client_id, tier, total_requests, current_window_usage_percent
        
        Raises:
            ValueError: If client_id not found in database
        
        Requirements: 4.2, 4.3, 4.4, 6.4
        """
        try:
            # Query ClientMetadata table for client record
            statement = select(ClientMetadata).where(ClientMetadata.client_id == client_id)
            client_metadata = session.exec(statement).first()
            
            if not client_metadata:
                raise ValueError(f"Client not found: {client_id}")
            
            # Aggregate sum of request_count from UsageLog
            usage_logs = session.exec(
                select(UsageLog).where(UsageLog.client_id == client_id)
            ).all()
            
            total_requests = sum(log.request_count for log in usage_logs)
            
            # Calculate current window usage percentage from window_state
            current_window_count = 0
            if client_id in self.window_state:
                current_time = time.time()
                self._evict_stale_timestamps(client_id, current_time)
                current_window_count = len(self.window_state[client_id])
            
            tier_limit = self._get_tier_limit(client_metadata.tier)
            current_window_usage_percent = (current_window_count / tier_limit) * 100.0
            
            return {
                "client_id": client_id,
                "tier": client_metadata.tier,
                "total_requests": total_requests,
                "current_window_usage_percent": current_window_usage_percent
            }
        
        except ValueError:
            # Re-raise ValueError for 404 handling
            raise
        except Exception as e:
            logger.error(f"Failed to retrieve usage for client_id={client_id}: {e}")
            raise
    
    async def cleanup_inactive_clients(self) -> None:
        """
        Remove clients with no activity for > inactive_threshold seconds.
        
        Side Effects: Removes entries from window_state, client_locks, last_access
        
        Requirements: 7.2, 7.3
        """
        current_time = time.time()
        inactive_clients = []
        
        # Identify inactive clients
        for client_id, last_time in list(self.last_access.items()):
            if current_time - last_time > self.inactive_threshold:
                inactive_clients.append(client_id)
        
        # Remove inactive clients (acquire lock first for safety)
        for client_id in inactive_clients:
            # Acquire client lock before removing
            if client_id in self.client_locks:
                async with self.client_locks[client_id]:
                    # Remove from all state dictionaries
                    if client_id in self.window_state:
                        del self.window_state[client_id]
                    if client_id in self.last_access:
                        del self.last_access[client_id]
                
                # Remove the lock itself (after releasing)
                del self.client_locks[client_id]
                
                logger.info(f"Cleaned up inactive client: {client_id}")
        
        if inactive_clients:
            logger.info(f"Cleanup completed: removed {len(inactive_clients)} inactive clients")


# Global service instance
rate_limiter_service = RateLimiterService()


async def periodic_cleanup() -> None:
    """
    Periodic cleanup background task that runs cleanup_inactive_clients every 60 seconds.
    
    This coroutine runs indefinitely until cancelled, typically during application shutdown.
    Uses asyncio.sleep for interval timing and handles cancellation gracefully.
    
    Requirements: 7.2, 7.3
    """
    logger.info("Starting periodic cleanup background task")
    
    try:
        while True:
            await asyncio.sleep(60)  # Wait 60 seconds
            await rate_limiter_service.cleanup_inactive_clients()
    
    except asyncio.CancelledError:
        # Graceful shutdown
        logger.info("Periodic cleanup task cancelled, shutting down gracefully")
        raise
    except Exception as e:
        logger.error(f"Error in periodic cleanup task: {e}")
        raise
