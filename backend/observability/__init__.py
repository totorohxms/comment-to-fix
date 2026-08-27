"""Observability toolkit: structured logging, statsd metrics, request
middleware, and the @track decorator for non-HTTP code paths."""

import functools
import inspect
import time

from backend.observability.logging import current_request_id, setup_logging
from backend.observability.middleware import RequestContextMiddleware
from backend.observability.statsd import metrics

__all__ = ["current_request_id", "metrics", "setup_logging",
           "RequestContextMiddleware", "track"]

def track(name: str):
    """Instrument a function (sync or async) with zero code inside it:
    <name>.ok / <name>.error counters and a <name> timing. HTTP routes are
    covered by the middleware already — use this for agent/queue internals.
    """
    def decorate(fn):
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                start = time.perf_counter()
                try:
                    result = await fn(*args, **kwargs)
                    metrics.incr(f"{name}.ok")
                    return result
                except Exception:
                    metrics.incr(f"{name}.error")
                    raise
                finally:
                    metrics.timing(name, (time.perf_counter() - start) * 1000)
            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
                metrics.incr(f"{name}.ok")
                return result
            except Exception:
                metrics.incr(f"{name}.error")
                raise
            finally:
                metrics.timing(name, (time.perf_counter() - start) * 1000)
        return sync_wrapper
    return decorate
