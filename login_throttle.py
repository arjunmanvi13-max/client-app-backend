"""In-process login throttling for /auth/login.

Blocks online brute force against a domain-restricted, guessable-email login.
State is per-process: with a single Railway worker that is the whole service; if
the deployment is ever scaled to multiple replicas the effective limit becomes
`limit x replicas`, at which point this should move to Redis or Mongo.
"""
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from fastapi import HTTPException

WINDOW_SECONDS = 15 * 60
MAX_FAILURES_PER_EMAIL = 8
MAX_FAILURES_PER_IP = 30
LOCKOUT_SECONDS = 15 * 60

_email_failures: Dict[str, Deque[float]] = defaultdict(deque)
_ip_failures: Dict[str, Deque[float]] = defaultdict(deque)
_lockouts: Dict[str, float] = {}


def _prune(bucket: Deque[float], now: float) -> None:
    while bucket and now - bucket[0] > WINDOW_SECONDS:
        bucket.popleft()


def _sweep(now: float) -> None:
    for key, until in list(_lockouts.items()):
        if until <= now:
            _lockouts.pop(key, None)
    for store in (_email_failures, _ip_failures):
        for key, bucket in list(store.items()):
            _prune(bucket, now)
            if not bucket:
                store.pop(key, None)


def check_login_allowed(email: str, ip: str) -> None:
    now = time.monotonic()
    _sweep(now)
    for key in (f"email:{email}", f"ip:{ip}"):
        until = _lockouts.get(key)
        if until and until > now:
            raise HTTPException(
                429,
                "Too many failed sign-in attempts. Try again in "
                f"{int((until - now) // 60) + 1} minute(s).",
            )


def record_login_failure(email: str, ip: str) -> None:
    now = time.monotonic()
    for key, bucket, limit in (
        (f"email:{email}", _email_failures[email], MAX_FAILURES_PER_EMAIL),
        (f"ip:{ip}", _ip_failures[ip], MAX_FAILURES_PER_IP),
    ):
        bucket.append(now)
        _prune(bucket, now)
        if len(bucket) >= limit:
            _lockouts[key] = now + LOCKOUT_SECONDS
            bucket.clear()


def reset_login_failures(email: str, ip: str) -> None:
    _email_failures.pop(email, None)
    _ip_failures.pop(ip, None)
    _lockouts.pop(f"email:{email}", None)
    _lockouts.pop(f"ip:{ip}", None)


def reset_all() -> None:
    """Test hook."""
    _email_failures.clear()
    _ip_failures.clear()
    _lockouts.clear()


def current_state() -> Tuple[int, int, int]:
    return len(_email_failures), len(_ip_failures), len(_lockouts)
