"""Asynchronous Redis Client and In-Memory Emulator.

Implements Data Store D6 (Asynchronous Ingestion Queue) interface with support for
Upstash Serverless Redis / Redis cluster and a zero-dependency in-memory async Redis emulator
for testing and offline local execution.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import redis.asyncio as aioredis
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import RedisError, TimeoutError as RedisTimeoutError
except ImportError:  # pragma: no cover
    aioredis = None
    RedisConnectionError = Exception
    RedisTimeoutError = Exception
    RedisError = Exception

from app.core.config import get_settings

logger = logging.getLogger("app.core.redis")


class InMemoryRedisClient:
    """High-fidelity in-memory asynchronous Redis emulator.

    Simulates key-value operations, TTL expirations, and list primitives (FIFO queues
    with blocking BLPOP and LPOP) matching redis-py asyncio API.
    """

    def __init__(self, decode_responses: bool = True) -> None:
        self.decode_responses = decode_responses
        self._data: Dict[str, Any] = {}
        self._lists: Dict[str, List[str]] = {}
        self._expires: Dict[str, float] = {}
        self._waiters: Dict[str, List[asyncio.Future]] = {}
        self._closed: bool = False

    def _is_expired(self, key: str) -> bool:
        """Check if a key has expired based on current timestamp and clean up if expired."""
        if key in self._expires:
            if time.time() >= self._expires[key]:
                self._expires.pop(key, None)
                self._data.pop(key, None)
                self._lists.pop(key, None)
                return True
        return False

    async def ping(self) -> bool:
        """Ping the server / mock instance."""
        if self._closed:
            raise RuntimeError("Redis connection is closed.")
        return True

    async def set(
        self,
        name: str,
        value: Any,
        ex: Optional[Union[int, float]] = None,
        px: Optional[Union[int, float]] = None,
        nx: bool = False,
        xx: bool = False,
    ) -> Optional[bool]:
        """Set key to hold string value with optional TTL and conditions.

        Args:
            name: Key identifier.
            value: Value to store.
            ex: Expiration time in seconds.
            px: Expiration time in milliseconds.
            nx: Only set the key if it does not already exist.
            xx: Only set the key if it already exists.

        Returns:
            True if key was set, None/False if condition (nx/xx) was not met.
        """
        if self._closed:
            raise RuntimeError("Redis connection is closed.")

        exists = not self._is_expired(name) and (name in self._data or name in self._lists)

        if nx and exists:
            return None
        if xx and not exists:
            return None

        val_str = str(value) if not isinstance(value, (str, bytes)) else value
        if self.decode_responses and isinstance(val_str, bytes):
            val_str = val_str.decode("utf-8")

        self._data[name] = val_str
        self._lists.pop(name, None)

        if ex is not None:
            self._expires[name] = time.time() + float(ex)
        elif px is not None:
            self._expires[name] = time.time() + (float(px) / 1000.0)
        else:
            self._expires.pop(name, None)

        return True

    async def setnx(self, name: str, value: Any) -> bool:
        """Set key only if it does not exist. Returns True if set, False otherwise."""
        result = await self.set(name, value, nx=True)
        return bool(result)

    async def get(self, name: str) -> Optional[str]:
        """Get the value of key. Returns None if key does not exist or expired."""
        if self._closed:
            raise RuntimeError("Redis connection is closed.")
        if self._is_expired(name):
            return None
        val = self._data.get(name)
        if val is not None and self.decode_responses and isinstance(val, bytes):
            return val.decode("utf-8")
        return val

    async def rpush(self, name: str, *values: Any) -> int:
        """Append one or multiple elements to a list from the right.

        Wakes up any coroutines waiting on blpop for this key.
        """
        if self._closed:
            raise RuntimeError("Redis connection is closed.")
        self._is_expired(name)
        self._data.pop(name, None)

        queue = self._lists.setdefault(name, [])
        for v in values:
            item_str = str(v) if not isinstance(v, (str, bytes)) else v
            if self.decode_responses and isinstance(item_str, bytes):
                item_str = item_str.decode("utf-8")
            queue.append(item_str)

        # Notify any waiters waiting on this queue via blpop
        waiters = self._waiters.get(name, [])
        while waiters and queue:
            fut = waiters.pop(0)
            if not fut.done() and not fut.cancelled():
                item = queue.pop(0)
                fut.set_result((name, item))
                break

        return len(queue)

    async def lpush(self, name: str, *values: Any) -> int:
        """Prepend one or multiple elements to a list from the left."""
        if self._closed:
            raise RuntimeError("Redis connection is closed.")
        self._is_expired(name)
        self._data.pop(name, None)

        queue = self._lists.setdefault(name, [])
        for v in values:
            item_str = str(v) if not isinstance(v, (str, bytes)) else v
            if self.decode_responses and isinstance(item_str, bytes):
                item_str = item_str.decode("utf-8")
            queue.insert(0, item_str)

        waiters = self._waiters.get(name, [])
        while waiters and queue:
            fut = waiters.pop(0)
            if not fut.done() and not fut.cancelled():
                item = queue.pop(0)
                fut.set_result((name, item))
                break

        return len(queue)

    async def lpop(self, name: str, count: Optional[int] = None) -> Optional[Union[str, List[str]]]:
        """Remove and get the first elements in a list from the left."""
        if self._closed:
            raise RuntimeError("Redis connection is closed.")
        if self._is_expired(name):
            return None

        queue = self._lists.get(name)
        if not queue:
            return None

        if count is None:
            return queue.pop(0)

        result = []
        for _ in range(count):
            if queue:
                result.append(queue.pop(0))
            else:
                break
        return result

    async def blpop(
        self,
        keys: Union[str, List[str]],
        timeout: Union[int, float] = 0,
    ) -> Optional[Tuple[str, str]]:
        """Remove and get the first element in a list, or block until one is available.

        Args:
            keys: Key or list of keys to pop from.
            timeout: Maximum seconds to block (0 blocks indefinitely until cancelled).

        Returns:
            Tuple of (key, element) or None on timeout.
        """
        if self._closed:
            raise RuntimeError("Redis connection is closed.")

        key_list = [keys] if isinstance(keys, str) else list(keys)

        # Check existing list items first (strict FIFO order)
        for k in key_list:
            if not self._is_expired(k):
                queue = self._lists.get(k)
                if queue:
                    return (k, queue.pop(0))

        # If nothing available and non-blocking timeout is zero (or negative), return None immediately
        if timeout is not None and timeout < 0:
            return None

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        for k in key_list:
            self._waiters.setdefault(k, []).append(fut)

        try:
            if timeout and timeout > 0:
                return await asyncio.wait_for(asyncio.shield(fut), timeout=float(timeout))
            else:
                return await fut
        except (asyncio.TimeoutError, TimeoutError):
            return None
        finally:
            for k in key_list:
                waiter_list = self._waiters.get(k, [])
                if fut in waiter_list:
                    waiter_list.remove(fut)

    async def llen(self, name: str) -> int:
        """Get the length of a list."""
        if self._closed:
            raise RuntimeError("Redis connection is closed.")
        if self._is_expired(name):
            return 0
        return len(self._lists.get(name, []))

    async def lrange(self, name: str, start: int, end: int) -> List[str]:
        """Get a range of elements from a list (end index inclusive)."""
        if self._closed:
            raise RuntimeError("Redis connection is closed.")
        if self._is_expired(name):
            return []
        queue = self._lists.get(name, [])
        if end == -1:
            return queue[start:]
        return queue[start : end + 1]

    async def exists(self, *names: str) -> int:
        """Returns the number of keys that exist and have not expired."""
        if self._closed:
            raise RuntimeError("Redis connection is closed.")
        count = 0
        for name in names:
            if not self._is_expired(name):
                if name in self._data or name in self._lists:
                    count += 1
        return count

    async def delete(self, *names: str) -> int:
        """Delete one or more keys."""
        if self._closed:
            raise RuntimeError("Redis connection is closed.")
        deleted = 0
        for name in names:
            self._is_expired(name)
            had_key = (name in self._data) or (name in self._lists) or (name in self._expires)
            self._data.pop(name, None)
            self._lists.pop(name, None)
            self._expires.pop(name, None)
            if had_key:
                deleted += 1
        return deleted

    async def expire(self, name: str, time_sec: Union[int, float]) -> bool:
        """Set a key's time to live in seconds."""
        if self._closed:
            raise RuntimeError("Redis connection is closed.")
        if self._is_expired(name):
            return False
        if name in self._data or name in self._lists:
            self._expires[name] = time.time() + float(time_sec)
            return True
        return False

    async def ttl(self, name: str) -> int:
        """Get the time to live for a key in seconds.

        Returns:
            -2 if key does not exist or expired.
            -1 if key exists with no expiration.
            Remaining seconds >= 0 if key has expiration.
        """
        if self._closed:
            raise RuntimeError("Redis connection is closed.")
        if self._is_expired(name):
            return -2
        if name not in self._data and name not in self._lists:
            return -2
        if name in self._expires:
            rem = int(self._expires[name] - time.time())
            return max(0, rem)
        return -1

    async def flushdb(self) -> bool:
        """Delete all keys in current mock database."""
        self._data.clear()
        self._lists.clear()
        self._expires.clear()
        for waiters in self._waiters.values():
            for fut in waiters:
                if not fut.done():
                    fut.cancel()
        self._waiters.clear()
        return True

    async def flushall(self) -> bool:
        """Delete all keys across mock database."""
        return await self.flushdb()

    async def close(self) -> None:
        """Close mock connection and cancel pending waiters."""
        self._closed = True
        for waiters in self._waiters.values():
            for fut in waiters:
                if not fut.done():
                    fut.cancel()
        self._waiters.clear()

    async def aclose(self) -> None:
        """Asynchronous close matching redis-py 5.x+ aclose API."""
        await self.close()

    async def __aenter__(self) -> "InMemoryRedisClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()


# Shared in-memory instance
_SHARED_IN_MEMORY_REDIS: Optional[InMemoryRedisClient] = None
_GLOBAL_REDIS_CLIENT: Optional[Any] = None


def get_in_memory_redis_client() -> InMemoryRedisClient:
    """Return the shared in-memory Redis client instance."""
    global _SHARED_IN_MEMORY_REDIS
    if _SHARED_IN_MEMORY_REDIS is None or _SHARED_IN_MEMORY_REDIS._closed:
        _SHARED_IN_MEMORY_REDIS = InMemoryRedisClient(decode_responses=True)
    return _SHARED_IN_MEMORY_REDIS


async def reset_in_memory_redis() -> None:
    """Reset all state in the shared in-memory Redis client."""
    client = get_in_memory_redis_client()
    await client.flushdb()


async def get_redis_client(
    redis_url: Optional[str] = None,
    force_in_memory: bool = False,
) -> Union[Any, InMemoryRedisClient]:
    """Retrieve an active async Redis client with transparent fallback to in-memory emulator.

    Args:
        redis_url: Optional connection URL (defaults to Settings.REDIS_URL).
        force_in_memory: If True, bypasses live connection and returns in-memory emulator.

    Returns:
        Async Redis client instance conforming to standard redis-py interface.
    """
    global _GLOBAL_REDIS_CLIENT

    if force_in_memory:
        return get_in_memory_redis_client()

    settings = get_settings()
    url = redis_url or settings.REDIS_URL

    if aioredis is None:
        logger.warning("redis-py package is not installed; using in-memory Redis emulator.")
        return get_in_memory_redis_client()

    try:
        client = aioredis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=0.3,
            socket_timeout=0.3,
        )
        await client.ping()
        logger.info("Connected to live Redis server at %s", url)
        _GLOBAL_REDIS_CLIENT = client
        return client
    except Exception as exc:
        logger.warning(
            "Live Redis server unreachable at %s (%s). Falling back to in-memory Redis emulator.",
            url,
            exc,
        )
        return get_in_memory_redis_client()


async def close_redis_client() -> None:
    """Close active global redis client connection if active."""
    global _GLOBAL_REDIS_CLIENT, _SHARED_IN_MEMORY_REDIS
    if _GLOBAL_REDIS_CLIENT is not None:
        try:
            if hasattr(_GLOBAL_REDIS_CLIENT, "aclose"):
                await _GLOBAL_REDIS_CLIENT.aclose()
            elif hasattr(_GLOBAL_REDIS_CLIENT, "close"):
                await _GLOBAL_REDIS_CLIENT.close()
        except Exception:
            pass
        _GLOBAL_REDIS_CLIENT = None

    if _SHARED_IN_MEMORY_REDIS is not None:
        await _SHARED_IN_MEMORY_REDIS.close()
        _SHARED_IN_MEMORY_REDIS = None
