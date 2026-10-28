from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Section(str, Enum):
    AI = "AI"
    ENGINEERING = "工程"
    PRODUCT = "产品"
    BUSINESS = "商业"
    OTHER = "其他"

    @classmethod
    def parse(cls, value: str) -> "Section":
        text = (value or "").strip()
        aliases = {
            "ai": cls.AI,
            "人工智能": cls.AI,
            "大模型": cls.AI,
            "工程": cls.ENGINEERING,
            "engineering": cls.ENGINEERING,
            "技术": cls.ENGINEERING,
            "产品": cls.PRODUCT,
            "product": cls.PRODUCT,
            "商业": cls.BUSINESS,
            "business": cls.BUSINESS,
            "融资": cls.BUSINESS,
        }
        return aliases.get(text.lower(), cls.OTHER)


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    source: str
    content: str
    published_at: datetime | None = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            digest = hashlib.sha1(self.url.encode("utf-8")).hexdigest()
            object.__setattr__(self, "id", digest[:16])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "content": self.content,
            "published_at": self.published_at.isoformat() if self.published_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Article":
        published = data.get("published_at")
        return cls(
            title=data["title"],
            url=data["url"],
            source=data["source"],
            content=data.get("content", ""),
            published_at=datetime.fromisoformat(published) if published else None,
            id=data.get("id", ""),
        )


@dataclass
class Summary:
    article: Article
    summary: str
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "article": self.article.to_dict(),
            "summary": self.summary,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Summary":
        return cls(
            article=Article.from_dict(data["article"]),
            summary=data["summary"],
            tags=list(data.get("tags", [])),
        )


@dataclass
class RoutedArticle:
    summary: Summary
    section: Section
    priority: int
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary.to_dict(),
            "section": self.section.value,
            "priority": self.priority,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoutedArticle":
        return cls(
            summary=Summary.from_dict(data["summary"]),
            section=Section.parse(data["section"]),
            priority=int(data.get("priority", 1)),
            reason=data.get("reason", ""),
        )


@dataclass
class Digest:
    task_id: str
    created_at: datetime
    sections: dict[str, list[RoutedArticle]]
    total: int
    deduped: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "created_at": self.created_at.isoformat(),
            "total": self.total,
            "deduped": self.deduped,
            "sections": {
                name: [item.to_dict() for item in items] for name, items in self.sections.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Digest":
        sections = {
            name: [RoutedArticle.from_dict(item) for item in items]
            for name, items in data.get("sections", {}).items()
        }
        return cls(
            task_id=data["task_id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            sections=sections,
            total=int(data.get("total", 0)),
            deduped=int(data.get("deduped", 0)),
        )


@dataclass
class Task:
    id: str
    topics: list[str]
    created_at: datetime
    status: str = "pending"


class EventType:
    TASK_CREATED = "task.created"
    ARTICLES_COLLECTED = "articles.collected"
    ARTICLES_SUMMARIZED = "articles.summarized"
    ARTICLES_ROUTED = "articles.routed"
    DIGEST_READY = "digest.ready"
    DIGEST_SENT = "digest.sent"


@dataclass
class Event:
    type: str
    task_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=datetime.now)
