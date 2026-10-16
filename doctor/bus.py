import asyncio
import json


class EventBus:
    def __init__(self):
        self.subscribers = set()

    async def publish(self, kind: str, data: dict | None = None):
        message = json.dumps({'type': kind, 'data': data or {}})
        for queue in tuple(self.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(message)

    async def stream(self):
        queue = asyncio.Queue(maxsize=100)
        self.subscribers.add(queue)
        try:
            yield 'event: ready\ndata: {}\n\n'
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f'data: {message}\n\n'
                except TimeoutError:
                    yield ': heartbeat\n\n'
        finally:
            self.subscribers.discard(queue)


bus = EventBus()
