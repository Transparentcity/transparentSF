"""
Redis-based session management for TransparentSF Platform.

This module provides persistent session storage for AI agents using Redis,
with fallback to in-memory storage for development environments.

Originally inspired by transparentcity-platform design, adapted for TransparentSF's
langchain agent architecture.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Dict, Any, Optional, List
from dataclasses import dataclass, asdict, field
from dataclasses_json import dataclass_json

try:
    import redis
    from redis.exceptions import ConnectionError, RedisError
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None  # type: ignore
    ConnectionError = Exception  # type: ignore
    RedisError = Exception  # type: ignore

logger = logging.getLogger(__name__)


@dataclass_json
@dataclass
class SessionData:
    """Session data structure for agent memory."""
    
    session_id: str
    user_id: Optional[str] = None
    agent_type: str = "langchain_explainer"
    model_key: str = "gpt-5"
    tool_groups: List[str] = field(default_factory=lambda: ["core"])
    created_at: str = ""
    last_accessed: str = ""
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    context_data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    # LangChain-specific fields
    intermediate_responses: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    total_execution_time_ms: int = 0
    total_tokens_used: int = 0
    
    def __post_init__(self):
        """Initialize default values."""
        if not self.created_at:
            self.created_at = datetime.utcnow().isoformat()
        if not self.last_accessed:
            self.last_accessed = datetime.utcnow().isoformat()


class SessionError(Exception):
    """Raised when session operations fail."""
    pass


class SessionStore(ABC):
    """Abstract base class for session storage."""
    
    @abstractmethod
    async def get_session(self, session_id: str) -> Optional[SessionData]:
        """Retrieve session data."""
        pass
    
    @abstractmethod
    async def save_session(self, session_data: SessionData, ttl_seconds: int = 3600) -> bool:
        """Store session data."""
        pass
    
    @abstractmethod
    async def delete_session(self, session_id: str) -> bool:
        """Remove session."""
        pass
    
    @abstractmethod
    async def list_sessions(self, user_id: Optional[str] = None) -> List[str]:
        """List session IDs."""
        pass
    
    @abstractmethod
    async def cleanup_expired_sessions(self) -> int:
        """Remove expired sessions."""
        pass
    
    # Synchronous versions for compatibility with existing code
    def get_session_sync(self, session_id: str) -> Optional[SessionData]:
        """Synchronous version of get_session."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(self.get_session(session_id))
    
    def save_session_sync(self, session_data: SessionData, ttl_seconds: int = 3600) -> bool:
        """Synchronous version of save_session."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(self.save_session(session_data, ttl_seconds))
    
    def delete_session_sync(self, session_id: str) -> bool:
        """Synchronous version of delete_session."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(self.delete_session(session_id))


class RedisSessionStore(SessionStore):
    """
    Redis-based session storage with connection pooling and error handling.
    
    Provides persistent session storage for production environments with
    automatic expiration and efficient memory usage.
    """
    
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        key_prefix: str = "transparentsf:session:",
        max_connections: int = 50,
        socket_timeout: int = 5,
        socket_connect_timeout: int = 5,
    ):
        """
        Initialize Redis session storage.
        
        Args:
            redis_url: Redis connection URL
            key_prefix: Prefix for Redis keys
            max_connections: Maximum connections in pool
            socket_timeout: Socket timeout in seconds
            socket_connect_timeout: Connection timeout in seconds
        """
        if not REDIS_AVAILABLE:
            raise SessionError("Redis library not installed. Install with: pip install redis")
        
        self.redis_url = redis_url
        self.key_prefix = key_prefix
        self.logger = logging.getLogger(__name__)
        
        # Initialize Redis connection pool
        self._redis_pool: Optional[redis.ConnectionPool] = None
        self._redis_client: Optional[redis.Redis] = None
        
        try:
            self._initialize_redis(
                max_connections=max_connections,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_connect_timeout
            )
            self.logger.info("Redis session storage initialized successfully")
        except Exception as e:
            self.logger.error(f"Failed to initialize Redis: {e}")
            raise SessionError(f"Redis initialization failed: {e}") from e
    
    def _initialize_redis(
        self,
        max_connections: int,
        socket_timeout: int,
        socket_connect_timeout: int,
    ) -> None:
        """Initialize Redis connection with connection pooling."""
        try:
            # Create connection pool
            self._redis_pool = redis.ConnectionPool.from_url(
                self.redis_url,
                max_connections=max_connections,
                socket_keepalive=True,
                socket_keepalive_options={},
                decode_responses=True,
            )
            
            # Create Redis client
            self._redis_client = redis.Redis(
                connection_pool=self._redis_pool,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_connect_timeout,
                retry_on_timeout=True,
            )
            
            # Test connection
            self._redis_client.ping()
            self.logger.info("Redis connection test successful")
            
        except ConnectionError as e:
            self.logger.error(f"Redis connection failed: {e}")
            raise SessionError(f"Redis connection failed: {e}") from e
        except Exception as e:
            self.logger.error(f"Unexpected Redis error: {e}")
            raise SessionError(f"Redis initialization failed: {e}") from e
    
    def _ensure_redis_client(self) -> redis.Redis:
        """Ensure Redis client is available."""
        if self._redis_client is None:
            raise SessionError("Redis client not initialized")
        return self._redis_client
    
    def _get_redis_key(self, session_id: str) -> str:
        """Generate Redis key for session."""
        return f"{self.key_prefix}{session_id}"
    
    async def get_session(self, session_id: str) -> Optional[SessionData]:
        """Retrieve session data from Redis."""
        try:
            redis_client = self._ensure_redis_client()
            key = self._get_redis_key(session_id)
            
            # Get session data
            data = redis_client.get(key)
            if not data:
                self.logger.debug(f"Session {session_id} not found")
                return None
            
            # Parse JSON data
            session_dict = json.loads(data)
            session_data = SessionData.from_dict(session_dict)
            
            # Update last accessed time
            session_data.last_accessed = datetime.utcnow().isoformat()
            await self.save_session(session_data)
            
            self.logger.debug(f"Retrieved session {session_id}")
            return session_data
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse session data for {session_id}: {e}")
            return None
        except RedisError as e:
            self.logger.error(f"Redis error retrieving session {session_id}: {e}")
            raise SessionError(f"Failed to retrieve session: {e}") from e
        except Exception as e:
            self.logger.error(f"Unexpected error retrieving session {session_id}: {e}")
            raise SessionError(f"Session retrieval failed: {e}") from e
    
    async def save_session(self, session_data: SessionData, ttl_seconds: int = 3600) -> bool:
        """Store session data in Redis."""
        try:
            redis_client = self._ensure_redis_client()
            key = self._get_redis_key(session_data.session_id)
            
            # Update last accessed time
            session_data.last_accessed = datetime.utcnow().isoformat()
            
            # Serialize session data
            data = session_data.to_json()
            
            # Store with TTL
            redis_client.setex(key, ttl_seconds, data)
            
            self.logger.debug(f"Saved session {session_data.session_id} with TTL {ttl_seconds}s")
            return True
            
        except RedisError as e:
            self.logger.error(f"Redis error saving session {session_data.session_id}: {e}")
            raise SessionError(f"Failed to save session: {e}") from e
        except Exception as e:
            self.logger.error(f"Unexpected error saving session {session_data.session_id}: {e}")
            raise SessionError(f"Session save failed: {e}") from e
    
    async def delete_session(self, session_id: str) -> bool:
        """Remove session from Redis."""
        try:
            redis_client = self._ensure_redis_client()
            key = self._get_redis_key(session_id)
            
            result = redis_client.delete(key)
            success = result > 0
            
            if success:
                self.logger.debug(f"Deleted session {session_id}")
            else:
                self.logger.debug(f"Session {session_id} not found for deletion")
            
            return success
            
        except RedisError as e:
            self.logger.error(f"Redis error deleting session {session_id}: {e}")
            raise SessionError(f"Failed to delete session: {e}") from e
        except Exception as e:
            self.logger.error(f"Unexpected error deleting session {session_id}: {e}")
            raise SessionError(f"Session deletion failed: {e}") from e
    
    async def list_sessions(self, user_id: Optional[str] = None) -> List[str]:
        """List session IDs, optionally filtered by user."""
        try:
            redis_client = self._ensure_redis_client()
            pattern = f"{self.key_prefix}*"
            
            # Get all session keys
            keys = redis_client.keys(pattern)
            session_ids = []
            
            for key in keys:
                session_id = key.replace(self.key_prefix, "")
                
                # Filter by user if specified
                if user_id:
                    try:
                        data = redis_client.get(key)
                        if data:
                            session_dict = json.loads(data)
                            if session_dict.get("user_id") == user_id:
                                session_ids.append(session_id)
                    except (json.JSONDecodeError, KeyError):
                        continue
                else:
                    session_ids.append(session_id)
            
            self.logger.debug(f"Listed {len(session_ids)} sessions")
            return session_ids
            
        except RedisError as e:
            self.logger.error(f"Redis error listing sessions: {e}")
            raise SessionError(f"Failed to list sessions: {e}") from e
        except Exception as e:
            self.logger.error(f"Unexpected error listing sessions: {e}")
            raise SessionError(f"Session listing failed: {e}") from e
    
    async def cleanup_expired_sessions(self) -> int:
        """Remove expired sessions (Redis handles TTL automatically)."""
        # Redis automatically removes expired keys, so this is mainly for logging
        try:
            redis_client = self._ensure_redis_client()
            
            # Get Redis info about expired keys
            info = redis_client.info("stats")
            expired_keys = info.get("expired_keys", 0)
            
            self.logger.debug(f"Redis has expired {expired_keys} keys")
            return expired_keys
            
        except RedisError as e:
            self.logger.error(f"Redis error during cleanup: {e}")
            return 0
        except Exception as e:
            self.logger.error(f"Unexpected error during cleanup: {e}")
            return 0


class InMemorySessionStore(SessionStore):
    """
    In-memory session storage for development and testing.
    
    Provides session storage without external dependencies,
    with automatic cleanup and memory management.
    """
    
    def __init__(self, max_sessions: int = 100, default_ttl: int = 3600):
        """
        Initialize in-memory session storage.
        
        Args:
            max_sessions: Maximum number of sessions to store
            default_ttl: Default TTL in seconds
        """
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.max_sessions = max_sessions
        self.default_ttl = default_ttl
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"In-memory session storage initialized (max: {max_sessions})")
    
    async def get_session(self, session_id: str) -> Optional[SessionData]:
        """Retrieve session data from memory."""
        try:
            if session_id not in self.sessions:
                self.logger.debug(f"Session {session_id} not found")
                return None
            
            session_dict = self.sessions[session_id]
            
            # Check if session is expired
            if self._is_expired(session_dict):
                del self.sessions[session_id]
                self.logger.debug(f"Session {session_id} expired and removed")
                return None
            
            # Update last accessed time
            session_dict["last_accessed"] = datetime.utcnow().isoformat()
            
            # Convert to SessionData
            session_data = SessionData.from_dict(session_dict)
            
            self.logger.debug(f"Retrieved session {session_id}")
            return session_data
            
        except Exception as e:
            self.logger.error(f"Error retrieving session {session_id}: {e}")
            return None
    
    async def save_session(self, session_data: SessionData, ttl_seconds: int = 3600) -> bool:
        """Store session data in memory."""
        try:
            # Convert to dict and add TTL info
            session_dict = session_data.to_dict()
            session_dict["_ttl_seconds"] = ttl_seconds
            session_dict["_created_at"] = datetime.utcnow().isoformat()
            
            # Check if we need to evict old sessions
            if len(self.sessions) >= self.max_sessions:
                await self._evict_oldest_session()
            
            # Store session
            self.sessions[session_data.session_id] = session_dict
            
            self.logger.debug(f"Saved session {session_data.session_id} in memory")
            return True
            
        except Exception as e:
            self.logger.error(f"Error saving session {session_data.session_id}: {e}")
            return False
    
    async def delete_session(self, session_id: str) -> bool:
        """Remove session from memory."""
        try:
            if session_id in self.sessions:
                del self.sessions[session_id]
                self.logger.debug(f"Deleted session {session_id}")
                return True
            else:
                self.logger.debug(f"Session {session_id} not found for deletion")
                return False
                
        except Exception as e:
            self.logger.error(f"Error deleting session {session_id}: {e}")
            return False
    
    async def list_sessions(self, user_id: Optional[str] = None) -> List[str]:
        """List session IDs, optionally filtered by user."""
        try:
            session_ids = []
            
            for session_id, session_dict in self.sessions.items():
                # Skip expired sessions
                if self._is_expired(session_dict):
                    continue
                
                # Filter by user if specified
                if user_id and session_dict.get("user_id") != user_id:
                    continue
                
                session_ids.append(session_id)
            
            self.logger.debug(f"Listed {len(session_ids)} sessions")
            return session_ids
            
        except Exception as e:
            self.logger.error(f"Error listing sessions: {e}")
            return []
    
    async def cleanup_expired_sessions(self) -> int:
        """Remove expired sessions from memory."""
        try:
            expired_count = 0
            expired_sessions = []
            
            for session_id, session_dict in self.sessions.items():
                if self._is_expired(session_dict):
                    expired_sessions.append(session_id)
            
            for session_id in expired_sessions:
                del self.sessions[session_id]
                expired_count += 1
            
            if expired_count > 0:
                self.logger.debug(f"Cleaned up {expired_count} expired sessions")
            
            return expired_count
            
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
            return 0
    
    def _is_expired(self, session_dict: Dict[str, Any]) -> bool:
        """Check if session is expired."""
        try:
            created_at_str = session_dict.get("_created_at")
            ttl_seconds = session_dict.get("_ttl_seconds", self.default_ttl)
            
            if not created_at_str:
                return True
            
            created_at = datetime.fromisoformat(created_at_str)
            expiry_time = created_at + timedelta(seconds=ttl_seconds)
            
            return datetime.utcnow() > expiry_time
            
        except Exception:
            return True
    
    async def _evict_oldest_session(self) -> None:
        """Remove the oldest session to make room."""
        try:
            if not self.sessions:
                return
            
            # Find oldest session by last_accessed time
            oldest_session_id = None
            oldest_time = None
            
            for session_id, session_dict in self.sessions.items():
                last_accessed = session_dict.get("last_accessed")
                if last_accessed:
                    try:
                        accessed_time = datetime.fromisoformat(last_accessed)
                        if oldest_time is None or accessed_time < oldest_time:
                            oldest_time = accessed_time
                            oldest_session_id = session_id
                    except ValueError:
                        continue
            
            if oldest_session_id:
                del self.sessions[oldest_session_id]
                self.logger.debug(f"Evicted oldest session {oldest_session_id}")
                
        except Exception as e:
            self.logger.error(f"Error evicting oldest session: {e}")


def create_session_store(
    redis_url: Optional[str] = None,
    use_redis: bool = True,
    fallback_to_memory: bool = True,
    **kwargs
) -> SessionStore:
    """
    Create session store based on configuration.
    
    Args:
        redis_url: Redis connection URL
        use_redis: Whether to use Redis (if available)
        fallback_to_memory: Whether to fallback to in-memory if Redis fails
        **kwargs: Additional arguments for session store
    
    Returns:
        Configured session store instance
    """
    if use_redis and redis_url and REDIS_AVAILABLE:
        try:
            store = RedisSessionStore(redis_url=redis_url, **kwargs)
            logger.info("Created Redis session store")
            return store
        except SessionError as e:
            logger.warning(f"Redis session store failed: {e}")
            if fallback_to_memory:
                logger.info("Falling back to in-memory session store")
                return InMemorySessionStore(**kwargs)
            else:
                raise
    elif use_redis and not REDIS_AVAILABLE:
        logger.warning("Redis requested but redis library not installed, using in-memory store")
    
    # Use in-memory store
    logger.info("Using in-memory session store")
    return InMemorySessionStore(**kwargs)


def generate_session_id() -> str:
    """Generate a unique session ID."""
    return f"session_{uuid.uuid4().hex[:16]}"


# Global session store instance
_session_store: Optional[SessionStore] = None


def get_session_store() -> SessionStore:
    """Get the global session store instance."""
    global _session_store
    if _session_store is None:
        # Initialize with environment variables
        redis_url = os.getenv("REDIS_URL")
        use_redis = os.getenv("USE_REDIS", "true").lower() == "true"
        
        _session_store = create_session_store(
            redis_url=redis_url,
            use_redis=use_redis,
            fallback_to_memory=True
        )
    
    return _session_store


def set_session_store(session_store: SessionStore) -> None:
    """Set the global session store instance."""
    global _session_store
    _session_store = session_store

