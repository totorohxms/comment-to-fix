"""Minimal statsd client: UDP fire-and-forget, never raises, never blocks.

No dependency needed — the statsd wire format is one line per metric:
    <prefix>.<name>:<value>|c      counter
    <prefix>.<name>:<ms>|ms        timing
    <prefix>.<name>:<value>|g      gauge

Config: STATSD_* in .env / environment (see backend/config.py). UDP to a
non-listening port is harmless, so this is safe to leave on in dev; point it
at a real statsd/Datadog agent in prod.
"""

import socket

from backend.config import settings

class StatsdClient:
    def __init__(self, host: str, port: int, prefix: str, enabled: bool = True):
        self.prefix = prefix
        self.enabled = enabled
        self._addr = (host, port)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)

    def _send(self, payload: str) -> None:
        if not self.enabled:
            return
        try:
            self._sock.sendto(payload.encode("ascii", "replace"), self._addr)
        except OSError:
            pass  # metrics must never take the service down

    def incr(self, name: str, value: int = 1) -> None:
        self._send(f"{self.prefix}.{name}:{value}|c")

    def timing(self, name: str, ms: float) -> None:
        self._send(f"{self.prefix}.{name}:{ms:.2f}|ms")

    def gauge(self, name: str, value: float) -> None:
        self._send(f"{self.prefix}.{name}:{value}|g")

metrics = StatsdClient(
    host=settings.statsd_host,
    port=settings.statsd_port,
    prefix=settings.statsd_prefix,
    enabled=settings.statsd_enabled,
)
