import asyncio
import math
import time


class RequestPacer:
    """Per-client request spacing; does not impose an undocumented venue quota."""

    def __init__(self, rate):
        if rate is not None and (not math.isfinite(rate) or rate <= 0):
            raise ValueError("Request rate must be finite and positive, or None")
        self.interval = 1 / rate if rate else 0
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self):
        if not self.interval:
            return
        async with self._lock:
            delay = self._last + self.interval - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()
