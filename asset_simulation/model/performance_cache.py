"""Bounded caches for deterministic read-only model projections.

The asset simulation intentionally rebuilds many downstream views from a stable
``GlobalMacroRun``.  That keeps ownership boundaries explicit, but it also means
multiple consumers can request the exact same expensive projection repeatedly.

This module provides a small cache decorator keyed by the upstream run identity
plus ordinary positional/keyword arguments.  It deliberately keeps no knowledge
of any specific model layer.

Important:
    Cached return values are shared objects.  Decorated projections must therefore
    be treated as immutable/read-only by their callers.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from functools import wraps
import threading
from typing import Any, Callable, TypeVar


_RESULT = TypeVar("_RESULT")
_MISSING = object()


def _freeze_cache_value(value: Any) -> Any:
    """Convert common argument containers into a stable hashable cache key."""

    if isinstance(value, Mapping):
        return tuple(
            sorted(
                ((str(key), _freeze_cache_value(item)) for key, item in value.items()),
                key=lambda pair: pair[0],
            )
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_cache_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze_cache_value(item) for item in value), key=repr))
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def deterministic_projection_cache(
    *,
    max_entries: int,
) -> Callable[[Callable[..., _RESULT]], Callable[..., _RESULT]]:
    """Memoize a projection whose first argument is a ``GlobalMacroRun``.

    Cache misses are intentionally serialized per decorated function.  The local
    HTTP service is CPU-bound and can issue the same oil-market request from
    multiple threads at once; serializing a miss prevents a cache stampede while
    leaving cache hits very cheap.
    """

    if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries < 1:
        raise ValueError("projection cache max_entries must be a positive integer")

    def decorate(func: Callable[..., _RESULT]) -> Callable[..., _RESULT]:
        cache: OrderedDict[tuple[Any, ...], _RESULT] = OrderedDict()
        lock = threading.RLock()
        hits = 0
        misses = 0

        @wraps(func)
        def wrapper(global_run: Any, *args: Any, **kwargs: Any) -> _RESULT:
            nonlocal hits, misses

            try:
                upstream_identity = str(global_run.identity["identity_hash"])
            except (AttributeError, KeyError, TypeError) as exc:
                raise ValueError(
                    f"{func.__name__} cache requires global_run.identity['identity_hash']"
                ) from exc

            key = (
                upstream_identity,
                _freeze_cache_value(args),
                _freeze_cache_value(kwargs),
            )
            with lock:
                cached = cache.get(key, _MISSING)
                if cached is not _MISSING:
                    hits += 1
                    cache.move_to_end(key)
                    return cached  # type: ignore[return-value]

                misses += 1
                result = func(global_run, *args, **kwargs)
                cache[key] = result
                cache.move_to_end(key)
                while len(cache) > max_entries:
                    cache.popitem(last=False)
                return result

        def cache_clear() -> None:
            nonlocal hits, misses
            with lock:
                cache.clear()
                hits = 0
                misses = 0

        def cache_info() -> dict[str, int]:
            with lock:
                return {
                    "hits": hits,
                    "misses": misses,
                    "currentEntries": len(cache),
                    "maximumEntries": max_entries,
                }

        wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]
        wrapper.cache_info = cache_info  # type: ignore[attr-defined]
        return wrapper

    return decorate
