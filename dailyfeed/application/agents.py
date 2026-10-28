from __future__ import annotations

from datetime import datetime

from ..domain.models import (
    Article,
    Digest,
    Event,
    EventType,
    RoutedArticle,
    Section,
    Summary,
)
from ..domain.ports import ArticleSource, EventBus, LLM, Notifier, Repository
from .memory import ThreeTierMemory


class CollectorAgent:
    """采集 Agent：多源抓取 + 任务内 URL 去重。"""

    def __init__(
        self,
        source: ArticleSource,
        bus: EventBus,
        store: Repository,
        memory: ThreeTierMemory,
        max_articles: int,
    ) -> None:
        self._source = source
        self._bus = bus
        self._store = store
        self._memory = memory
        self._max_articles = max_articles

    async def handle(self, event: Event) -> None:
        task_id = event.task_id
        topics = event.payload.get("topics", [])
        articles = await self._source.fetch(topics, self._max_articles)

        seen = set(self._memory.task_get(task_id, "seen_urls", []))
        fresh = [article for article in articles if article.id not in seen]
        skipped = len(articles) - len(fresh)

        self._memory.task_set(task_id, "seen_urls", list(seen | {article.id for article in fresh}))
        self._memory.task_set(task_id, "topics", topics)
        self._store.save_articles(task_id, fresh)
        self._store.record_metric("articles.collected", len(fresh), task_id)
        if skipped:
            self._store.record_metric("articles.skipped", skipped, task_id)
        self._memory.note(task_id, f"采集完成：新增 {len(fresh)} 篇，任务内去重 {skipped} 篇")

        await self._bus.publish(
            Event(
                EventType.ARTICLES_COLLECTED,
                task_id,
                {"articles": [article.to_dict() for article in fresh], "skipped": skipped},
            )
        )


class SummarizerAgent:
    """摘要 Agent：压缩正文、抽取标签，并把摘要写入全局向量记忆。"""

    def __init__(self, llm: LLM, bus: EventBus, store: Repository, memory: ThreeTierMemory) -> None:
        self._llm = llm
        self._bus = bus
        self._store = store
        self._memory = memory

    async def handle(self, event: Event) -> None:
        task_id = event.task_id
        articles = [Article.from_dict(item) for item in event.payload.get("articles", [])]
        if not articles:
            self._memory.note(task_id, "没有新增文章，跳过摘要")
            await self._bus.publish(Event(EventType.ARTICLES_SUMMARIZED, task_id, {"items": []}))
            return

        summaries: list[Summary] = []
        for article in articles:
            text, tags = await self._llm.summarize(article)
            summaries.append(Summary(article=article, summary=text, tags=tags))

        vectors = await self._llm.embed([item.summary for item in summaries])
        if len(vectors) != len(summaries):
            raise RuntimeError("向量数量与摘要数量不一致")

        for item, vector in zip(summaries, vectors):
            self._memory.remember(
                task_id,
                item.summary,
                vector,
                {"article_id": item.article.id, "title": item.article.title},
            )

        self._store.record_metric("articles.summarized", len(summaries), task_id)
        self._memory.note(task_id, f"摘要完成 {len(summaries)} 篇")

        await self._bus.publish(
            Event(
                EventType.ARTICLES_SUMMARIZED,
                task_id,
                {"items": [item.to_dict() for item in summaries]},
            )
        )


class RouterAgent:
    """路由 Agent：跨期向量去重 + 栏目/优先级判定。"""

    def __init__(
        self,
        llm: LLM,
        bus: EventBus,
        store: Repository,
        memory: ThreeTierMemory,
        dedupe_threshold: float,
    ) -> None:
        self._llm = llm
        self._bus = bus
        self._store = store
        self._memory = memory
        self._dedupe_threshold = dedupe_threshold

    async def handle(self, event: Event) -> None:
        task_id = event.task_id
        items = [Summary.from_dict(item) for item in event.payload.get("items", [])]
        topics = self._memory.task_get(task_id, "topics", [])
        recent = self._memory.recent(task_id, 5)

        routed: list[RoutedArticle] = []
        deduped = 0

        if items:
            vectors = await self._llm.embed([item.summary for item in items])
            if len(vectors) != len(items):
                raise RuntimeError("向量数量与摘要数量不一致")
        else:
            vectors = []

        for item, vector in zip(items, vectors):
            matches = self._memory.similar(vector, k=1, exclude_task_id=task_id)
            if matches and matches[0][0] >= self._dedupe_threshold:
                deduped += 1
                self._store.record_metric("articles.deduped", 1, task_id)
                self._memory.note(task_id, f"跨期去重：{item.article.title[:20]}")
                continue

            section, priority, reason = await self._llm.route(item, topics, recent)
            routed.append(
                RoutedArticle(summary=item, section=section, priority=priority, reason=reason)
            )
            self._store.record_metric("articles.routed", 1, task_id)
            self._store.record_metric(
                "route.hit" if section != Section.OTHER else "route.miss", 1, task_id
            )
            self._memory.note(task_id, f"路由：{section.value} P{priority} · {item.article.title[:16]}")

        await self._bus.publish(
            Event(
                EventType.ARTICLES_ROUTED,
                task_id,
                {"items": [item.to_dict() for item in routed], "deduped": deduped},
            )
        )


class ComposerAgent:
    """日报合成 Agent：按栏目分组、按优先级排序。"""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    async def handle(self, event: Event) -> None:
        items = [RoutedArticle.from_dict(item) for item in event.payload.get("items", [])]
        sections: dict[str, list[RoutedArticle]] = {}
        for item in sorted(items, key=lambda entry: (-entry.priority, entry.summary.article.title)):
            sections.setdefault(item.section.value, []).append(item)

        digest = Digest(
            task_id=event.task_id,
            created_at=datetime.now(),
            sections=sections,
            total=len(items),
            deduped=int(event.payload.get("deduped", 0)),
        )
        await self._bus.publish(
            Event(EventType.DIGEST_READY, event.task_id, {"digest": digest.to_dict()})
        )


class NotifierAgent:
    """推送 Agent：落盘 / 发邮件，并记录日报。"""

    def __init__(
        self, notifier: Notifier, bus: EventBus, store: Repository, memory: ThreeTierMemory
    ) -> None:
        self._notifier = notifier
        self._bus = bus
        self._store = store
        self._memory = memory

    async def handle(self, event: Event) -> None:
        digest = Digest.from_dict(event.payload["digest"])
        path = await self._notifier.send(digest)
        self._store.save_digest(digest, path)
        self._memory.note(event.task_id, f"日报已生成：{path}")
        await self._bus.publish(
            Event(
                EventType.DIGEST_SENT,
                event.task_id,
                {
                    "path": path,
                    "total": digest.total,
                    "deduped": digest.deduped,
                    "sections": {name: len(items) for name, items in digest.sections.items()},
                },
            )
        )
