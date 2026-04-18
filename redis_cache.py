import redis
import json
import logging
from typing import Any, Optional
from functools import wraps
import time

logger = logging.getLogger(__name__)

class RedisCache:
    """Redis-based caching layer for improved performance"""

    def __init__(self, host: str = 'localhost', port: int = 6379, db: int = 0, password: str = None):
        try:
            self.redis = redis.Redis(
                host=host,
                port=port,
                db=db,
                password=password,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5
            )
            # Test connection
            self.redis.ping()
            self.enabled = True
            logger.info("Redis cache initialized successfully")
        except redis.ConnectionError:
            logger.warning("Redis not available, falling back to in-memory cache")
            self.enabled = False
            self.memory_cache = {}

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        if not self.enabled:
            return self.memory_cache.get(key)

        try:
            value = self.redis.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"Redis get error: {e}")
            return None

    def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """Set value in cache with TTL"""
        if not self.enabled:
            self.memory_cache[key] = value
            return True

        try:
            return self.redis.setex(key, ttl, json.dumps(value))
        except Exception as e:
            logger.error(f"Redis set error: {e}")
            return False

    def delete(self, key: str) -> bool:
        """Delete key from cache"""
        if not self.enabled:
            self.memory_cache.pop(key, None)
            return True

        try:
            return bool(self.redis.delete(key))
        except Exception as e:
            logger.error(f"Redis delete error: {e}")
            return False

    def clear_pattern(self, pattern: str) -> int:
        """Clear all keys matching pattern"""
        if not self.enabled:
            # Clear memory cache keys containing pattern
            keys_to_delete = [k for k in self.memory_cache.keys() if pattern in k]
            for key in keys_to_delete:
                del self.memory_cache[key]
            return len(keys_to_delete)

        try:
            keys = self.redis.keys(pattern)
            if keys:
                return self.redis.delete(*keys)
            return 0
        except Exception as e:
            logger.error(f"Redis clear pattern error: {e}")
            return 0

    def get_user_cache_key(self, chat_id: int) -> str:
        """Generate cache key for user data"""
        return f"user:{chat_id}"

    def get_stats_cache_key(self, chat_id: int) -> str:
        """Generate cache key for user stats"""
        return f"stats:{chat_id}"

    def invalidate_user_cache(self, chat_id: int):
        """Invalidate all user-related cache"""
        patterns = [
            self.get_user_cache_key(chat_id),
            self.get_stats_cache_key(chat_id),
            f"analytics:*"  # Clear analytics cache
        ]
        for pattern in patterns:
            self.clear_pattern(pattern)

# Global cache instance
cache = RedisCache()

def cached(ttl: int = 300):
    """Decorator for caching function results"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Create cache key from function name and arguments
            key = f"{func.__name__}:{str(args)}:{str(kwargs)}"

            # Try to get from cache first
            cached_result = cache.get(key)
            if cached_result is not None:
                return cached_result

            # Execute function and cache result
            result = func(*args, **kwargs)
            cache.set(key, result, ttl)
            return result
        return wrapper
    return decorator

def invalidate_user_cache(chat_id: int):
    """Invalidate user cache after updates"""
    cache.invalidate_user_cache(chat_id)

def get_cached_user(chat_id: int):
    """Get user data from cache"""
    key = cache.get_user_cache_key(chat_id)
    return cache.get(key)

def set_cached_user(chat_id: int, user_data: dict, ttl: int = 300):
    """Cache user data"""
    key = cache.get_user_cache_key(chat_id)
    cache.set(key, user_data, ttl)
