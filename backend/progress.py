"""Real-time progress and Server-Sent Events (SSE) broadcaster."""
from __future__ import annotations

import asyncio
from typing import AsyncGenerator
from backend.schemas import JobProgressEvent


class ProgressBroadcaster:
    def __init__(self):
        # Maps job_id to a list of asyncio.Queue instances
        self._listeners: dict[str, list[asyncio.Queue[JobProgressEvent]]] = {}

    def subscribe(self, job_id: str) -> asyncio.Queue[JobProgressEvent]:
        q: asyncio.Queue[JobProgressEvent] = asyncio.Queue()
        if job_id not in self._listeners:
            self._listeners[job_id] = []
        self._listeners[job_id].append(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue[JobProgressEvent]) -> None:
        if job_id in self._listeners:
            if q in self._listeners[job_id]:
                self._listeners[job_id].remove(q)
            if not self._listeners[job_id]:
                del self._listeners[job_id]

    async def broadcast(self, event: JobProgressEvent) -> None:
        queues = self._listeners.get(event.job_id, [])
        for q in list(queues):
            await q.put(event)

    async def event_generator(self, job_id: str) -> AsyncGenerator[str, None]:
        q = self.subscribe(job_id)
        try:
            while True:
                # Wait for next event or timeout to send heartbeat comment
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"event: progress\ndata: {event.model_dump_json()}\n\n"
                    if event.progress >= 100 or "FAILED" in event.stage or "COMPLETE" in event.stage:
                        break
                except asyncio.TimeoutError:
                    # Heartbeat comment to keep SSE connection alive
                    yield ": ping\n\n"
        finally:
            self.unsubscribe(job_id, q)


progress_broadcaster = ProgressBroadcaster()
