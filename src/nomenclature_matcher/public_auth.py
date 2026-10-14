"""HTTP Basic authentication and a small process-local request limit."""
import base64
from collections import deque
from secrets import compare_digest
from threading import Lock
from time import monotonic

def valid_basic_auth(header: str | None, *, username: str, password: str) -> bool:
    if not header or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
        got_user, sep, got_password = decoded.partition(":")
        return bool(sep) and compare_digest(got_user, username) and compare_digest(got_password, password)
    except (ValueError, UnicodeDecodeError):
        return False

class RequestLimiter:
    """One global process-local ceiling. Set multiple-worker limits at proxy."""
    def __init__(self):
        self.lock = Lock()
        self.history = deque()

    def allow(self, rpm: int) -> bool:
        if rpm <= 0:
            return False
        now = monotonic()
        with self.lock:
            while self.history and self.history[0] <= now - 60:
                self.history.popleft()
            if len(self.history) >= rpm:
                return False
            self.history.append(now)
            return True
