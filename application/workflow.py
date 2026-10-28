from __future__ import annotations

import asyncio
import time

from ..config import Settings
from ..domain.models import Event, EventType
from ..domain.ports import ArticleSource, EventBus, LLM, Notifier, Repository
from .agents import CollectorAgent, ComposerAgent, NotifierAgent, RouterAgent, SummarizerAgent
from .memory import ThreeTierMemory


class Workflow:
    """事件驱动编排：每个事件由一个 Agent 处理，处理完再发布下一个事件。"""

    def __init__(
        self,
        settings: Settings,
        source: ArticleSource,
        llm: LLM,
        bus: EventBus,
        store: Repository,
        memory: ThreeTierMemory,
        notifier: Notifier,
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._store = store
        self._done = asyncio.Event()
        self._result: dict | None = None
        self._error: Exception | None = None

        self._collector = CollectorAgent(source, bus, store, memory, settings.max_articles)
        self._summarizer = SummarizerAgent(llm, bus, store, memory)
        self._router = RouterAgent(llm, bus, store, memory, settings.dedupe_threshold)
        self._composer = ComposerAgent(bus)
        self._notifier = NotifierAgent(notifier, bus, store, memory)

    def _handlers(self) -> dict:
        return {
            EventType.TASK_CREATED: self._guard(self._collector.handle),
            EventType.ARTICLES_COLLECTED: self._guard(self._summarizer.handle),
            EventType.ARTICLES_SUMMARIZED: self._guard(self._router.handle),
            EventType.ARTICLES_ROUTED: self._guard(self._composer.handle),
            EventType.DIGEST_READY: self._guard(self._notifier.handle),
            EventType.DIGEST_SENT: self._guard(self._on_sent),
        }

    def _guard(self, handler):
        async def wrapped(event: Event) -> None:
            try:
                await handler(event)
            except Exception as exc:
                await self._fail(event.task_id, exc)

        return wrapped

    async def _on_sent(self, event: Event) -> None:
        self._result = event.payload
        self._done.set()

    async def _fail(self, task_id: str, exc: Exception) -> None:
        self._error = exc
        self._store.set_task_status(task_id, "failed", str(exc))
        self._store.record_metric("task.failed", 1, task_id)
        self._done.set()

    async def run_once(self, topics: list[str]) -> dict:
        task = self._store.create_task(topics)
        self._store.set_task_status(task.id, "running")
        self._store.record_metric("task.started", 1, task.id)

        self._done = asyncio.Event()
        self._result = None
        self._error = None

        bus_task = asyncio.create_task(self._bus.run(self._handlers()))
        done_task = asyncio.create_task(self._done.wait())
        started = time.perf_counter()
        try:
            await self._bus.publish(Event(EventType.TASK_CREATED, task.id, {"topics": topics}))
            await asyncio.wait(
                {done_task, bus_task},
                timeout=self._settings.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not self._done.is_set():
                if bus_task.done() and not bus_task.cancelled():
                    exc = bus_task.exception()
                    await self._fail(task.id, exc or RuntimeError("事件总线异常退出"))
                else:
                    await self._fail(task.id, TimeoutError("工作流超时"))
        except Exception as exc:
            if not self._done.is_set():
                await self._fail(task.id, exc)
        finally:
            await self._bus.stop()
            done_task.cancel()
            try:
                await asyncio.wait_for(bus_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                bus_task.cancel()
            except Exception:
                pass

        duration_ms = (time.perf_counter() - started) * 1000
        self._store.record_metric("task.duration_ms", duration_ms, task.id)

        if self._error is not None:
            return {
                "task_id": task.id,
                "ok": False,
                "error": str(self._error),
                "duration_ms": duration_ms,
            }

        self._store.set_task_status(task.id, "done")
        self._store.record_metric("task.succeeded", 1, task.id)
        result = dict(self._result or {})
        result.update({"task_id": task.id, "ok": True, "duration_ms": duration_ms})
        return result
