import asyncio

from .errors import StreamClosed, StreamOverflow


class Subscription:
    """Bounded async iterator. Overflow is explicit, never silently loses ticks."""

    def __init__(self, close_callback, size=256):
        self._queue = asyncio.Queue(maxsize=size)
        self._close_callback = close_callback
        self.closed = False
        self.dropped = 0

    def put(self, item):
        if self.closed:
            return
        if self._queue.full():
            self.dropped += 1
            while not self._queue.empty():
                self._queue.get_nowait()
            self._queue.put_nowait(StreamOverflow("Consumer queue overflow; resync required"))
            self.closed = True
        else:
            self._queue.put_nowait(item)

    def finish(self):
        self.closed = True
        if self._queue.full():
            self._queue.get_nowait()
        self._queue.put_nowait(StreamClosed("Subscription closed"))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.closed and self._queue.empty():
            raise StopAsyncIteration
        value = await self._queue.get()
        if isinstance(value, StreamClosed):
            raise StopAsyncIteration
        if isinstance(value, Exception):
            raise value
        return value

    async def aclose(self):
        await self._close_callback(self)
        self.finish()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()
