from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime

from ..config import Settings
from ..domain.models import Event
from ..domain.ports import EventHandler


class MemoryBus:
    """进程内事件总线：用于本地演示与测试。"""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._handlers: dict[str, EventHandler] = {}
        self.history: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.history.append(event)
        await self._queue.put(event)

    async def run(self, handlers: dict[str, EventHandler]) -> None:
        self._handlers = handlers
        while True:
            event = await self._queue.get()
            if event is None:
                break
            handler = self._handlers.get(event.type)
            if handler is not None:
                await handler(event)

    async def stop(self) -> None:
        await self._queue.put(None)


class RedisStreamBus:
    """Redis Stream 事件总线：XADD 发布，消费组 XREADGROUP 消费。"""

    def __init__(self, settings: Settings) -> None:
        self._url = settings.redis_url
        self._stream = settings.redis_stream
        self._group = settings.redis_group
        self._client = None
        self._stopped = asyncio.Event()

    async def _ensure_client(self):
        if self._client is None:
            try:
                import redis.asyncio as redis
            except ImportError as exc:
                raise RuntimeError("Redis 模式需要先安装依赖：pip install redis") from exc
            self._client = redis.from_url(self._url, decode_responses=True)
            try:
                await self._client.xgroup_create(self._stream, self._group, id="0", mkstream=True)
            except Exception as exc:
                if "BUSYGROUP" not in str(exc):
                    raise
        return self._client

    async def publish(self, event: Event) -> None:
        client = await self._ensure_client()
        await client.xadd(
            self._stream,
            {
                "type": event.type,
                "task_id": event.task_id,
                "payload": json.dumps(event.payload, ensure_ascii=False),
                "occurred_at": event.occurred_at.isoformat(),
            },
        )

    async def run(self, handlers: dict[str, EventHandler]) -> None:
        self._stopped.clear()
        client = await self._ensure_client()
        consumer = f"worker-{uuid.uuid4().hex[:6]}"
        try:
            while not self._stopped.is_set():
                response = await client.xreadgroup(
                    self._group, consumer, {self._stream: ">"}, count=10, block=1000
                )
                if not response:
                    continue
                for _, entries in response:
                    for entry_id, fields in entries:
                        event = Event(
                            type=fields["type"],
                            task_id=fields["task_id"],
                            payload=json.loads(fields["payload"]),
                            occurred_at=datetime.fromisoformat(fields["occurred_at"]),
                        )
                        handler = handlers.get(event.type)
                        if handler is not None:
                            await handler(event)
                        await client.xack(self._stream, self._group, entry_id)
        finally:
            if self._client is not None:
                await self._client.aclose()
                self._client = None

    async def stop(self) -> None:
        self._stopped.set()


def build_bus(settings: Settings):
    if settings.bus == "redis":
        try:
            import redis  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("Redis 模式需要先安装依赖：pip install redis") from exc
        return RedisStreamBus(settings)
    return MemoryBus()
