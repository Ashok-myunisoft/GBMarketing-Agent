"""Process-wide pacing for direct requests to Google.

Google's automated-traffic detection weighs request velocity from a single
network heavily - concurrent enrichment workers each searching Google
independently reproduces exactly the burst pattern that trips it. This
serializes every Google navigation across threads behind one lock with a
randomized minimum gap, so N callers still land like one careful caller
instead of N simultaneous ones.
"""

import random
import threading
import time

from core.config import settings

_lock = threading.Lock()
_last_request_at = 0.0


def wait_turn() -> None:
    """Blocks the calling thread until it's safe to send the next Google request."""

    global _last_request_at

    with _lock:
        min_gap = settings.GOOGLE_SEARCH_MIN_INTERVAL_SECONDS + random.uniform(
            0, settings.GOOGLE_SEARCH_JITTER_SECONDS
        )
        now = time.monotonic()
        remaining = _last_request_at + min_gap - now
        if remaining > 0:
            time.sleep(remaining)
        _last_request_at = time.monotonic()
