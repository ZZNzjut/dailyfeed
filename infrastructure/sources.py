from __future__ import annotations

import asyncio
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

from ..config import Settings
from ..domain.models import Article

MOCK_ARTICLES = [
    {
        "title": "开源 Agent 框架发布 0.4 版本，新增多智能体编排能力",
        "url": "https://example.com/news/agent-framework-04",
        "source": "开源前线",
        "content": "该框架在 0.4 版本中引入 Agent Swarm 编排与工具调用协议，支持多个智能体按角色分工协作。官方称新版本将任务成功率提升了 12 个百分点，并提供可观测的事件追踪能力。",
        "tags": ["AI", "Agent", "开源"],
    },
    {
        "title": "大模型推理成本再降 40%，长上下文场景开始规模化落地",
        "url": "https://example.com/news/inference-cost",
        "source": "算力观察",
        "content": "某云厂商公布新一代推理集群，单位 token 成本较上一代下降约 40%。在长上下文检索与多轮对话场景中，延迟和显存占用均有明显改善。",
        "tags": ["AI", "推理"],
    },
    {
        "title": "RAG 系统召回率优化实践：四路召回与融合排序",
        "url": "https://example.com/news/rag-recall",
        "source": "工程实践",
        "content": "团队在知识库问答中引入向量、BM25、实体关系与文档位置四路召回，并通过加权融合排序提升效果。离线评测显示 Top-5 命中率相比单一向量召回有明显提升。",
        "tags": ["AI", "RAG", "工程"],
    },
    {
        "title": "向量数据库选型对比：pgvector、Milvus 与 Qdrant",
        "url": "https://example.com/news/vector-db",
        "source": "数据栈",
        "content": "文章从部署成本、检索延迟、过滤能力和生态兼容性四个维度对比主流向量数据库。结论是十万级文档优先 pgvector，亿级场景再考虑专用向量库。",
        "tags": ["工程", "数据库", "AI"],
    },
    {
        "title": "某初创公司完成 2 亿元 B 轮融资，主攻企业知识库 Agent",
        "url": "https://example.com/news/funding-agent",
        "source": "创投日报",
        "content": "该公司计划把资金投入企业知识库与智能体产品研发，客户主要集中在制造与金融行业。投资方认为私有化部署和权限隔离是落地关键。",
        "tags": ["商业", "融资", "Agent"],
    },
    {
        "title": "从单体到事件驱动：一次订单系统的重构复盘",
        "url": "https://example.com/news/event-driven",
        "source": "架构笔记",
        "content": "团队把同步调用改为事件驱动架构，用消息队列解耦下单、库存与结算服务。重构后峰值吞吐提升，故障隔离能力也更强。",
        "tags": ["工程", "架构"],
    },
    {
        "title": "产品经理如何设计 AI 助手的首次对话体验",
        "url": "https://example.com/news/ai-onboarding",
        "source": "产品沉思录",
        "content": "文章建议在首次对话中明确能力边界，用示例引导用户提问，并给出可点击的推荐问题。良好的首轮体验能显著提升用户留存。",
        "tags": ["产品", "体验", "AI"],
    },
    {
        "title": "Kubernetes 上部署大模型服务的三种显存优化方案",
        "url": "https://example.com/news/llm-gpu",
        "source": "云原生周刊",
        "content": "方案分别从量化、KV Cache 复用和动态批处理三个方向降低显存占用。实测在同等硬件下可支撑更高的并发请求。",
        "tags": ["工程", "部署", "大模型"],
    },
    {
        "title": "多模态模型在电商图文检索中的落地效果",
        "url": "https://example.com/news/multimodal-search",
        "source": "算法实战",
        "content": "团队用图文双塔模型替换原有标签检索，商品召回准确率提升，长尾类目收益更明显。模型推理部署在 GPU 集群上。",
        "tags": ["AI", "多模态", "产品"],
    },
    {
        "title": "开源社区发布轻量级向量检索库，单机支持千万级",
        "url": "https://example.com/news/vector-lib",
        "source": "开源前线",
        "content": "该项目基于倒排索引与量化压缩，在单机环境下可支撑千万级向量的近似检索。作者强调低内存占用和零外部依赖。",
        "tags": ["开源", "向量数据库", "工程"],
    },
    {
        "title": "AI 编程助手用户调研：七成开发者愿意为补全付费",
        "url": "https://example.com/news/coding-assistant",
        "source": "用户研究",
        "content": "调研覆盖两千名开发者，结果显示代码补全和单元测试生成是最受欢迎的功能。价格敏感度低于预期，团队协作能力成为采购关键。",
        "tags": ["产品", "商业", "AI"],
    },
    {
        "title": "Redis Stream 与 Kafka 在异步任务中的取舍",
        "url": "https://example.com/news/redis-vs-kafka",
        "source": "架构笔记",
        "content": "文章对比两者的吞吐、持久化、消费组语义和运维成本。结论是中小规模任务优先 Redis Stream，跨机房与高吞吐场景再上 Kafka。",
        "tags": ["工程", "架构", "Redis"],
    },
    {
        "title": "模型评测体系搭建：从离线指标到线上反馈闭环",
        "url": "https://example.com/news/eval-system",
        "source": "评测实验室",
        "content": "文章介绍如何用 RAGAS 等工具做离线评测，并把线上用户反馈回流到测试集。强调指标口径、样本量和人工标注的一致性。",
        "tags": ["AI", "工程", "评测"],
    },
    {
        "title": "芯片厂商发布新一代推理加速卡，主打低功耗",
        "url": "https://example.com/news/inference-chip",
        "source": "硬件观察",
        "content": "新卡面向边缘推理场景，功耗较上一代下降，支持主流大模型量化格式。厂商同时开放了配套的推理框架。",
        "tags": ["商业", "芯片", "硬件"],
    },
]


def _matches(text: str, topics: list[str]) -> bool:
    if not topics:
        return True
    haystack = text.lower()
    return any(topic.lower() in haystack for topic in topics)


class MockSource:
    """离线数据源：返回固定的一批资讯，用于演示与测试。"""

    def __init__(self) -> None:
        self._items = MOCK_ARTICLES

    async def fetch(self, topics: list[str], limit: int) -> list[Article]:
        selected = []
        for item in self._items:
            haystack = f"{item['title']} {item['content']} {' '.join(item['tags'])}"
            if _matches(haystack, topics):
                selected.append(
                    Article(
                        title=item["title"],
                        url=item["url"],
                        source=item["source"],
                        content=item["content"],
                    )
                )
        return selected[:limit]


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        return parsed
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class RssSource:
    """RSS / Atom 数据源：使用标准库抓取与解析。"""

    def __init__(self, feeds: list[str]) -> None:
        self._feeds = feeds

    def _fetch_sync(self, url: str, limit: int) -> list[Article]:
        request = urllib.request.Request(url, headers={"User-Agent": "DailyFeed/0.1"})
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
        root = ET.fromstring(raw)
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall(".//item") or root.findall(".//atom:entry", namespace)
        articles = []
        for entry in entries[:limit]:
            title = entry.findtext("title") or entry.findtext("atom:title", default="", namespaces=namespace)
            link = entry.findtext("link")
            if not link:
                atom_link = entry.find("atom:link", namespace)
                link = atom_link.get("href", "") if atom_link is not None else ""
            content = (
                entry.findtext("description")
                or entry.findtext("summary", default="", namespaces=namespace)
                or entry.findtext("content", default="", namespaces=namespace)
                or ""
            )
            published = _parse_date(entry.findtext("pubDate") or entry.findtext("updated", default="", namespaces=namespace))
            if title and link:
                articles.append(
                    Article(
                        title=_strip_html(title),
                        url=link.strip(),
                        source=url,
                        content=_strip_html(content),
                        published_at=published,
                    )
                )
        return articles

    async def fetch(self, topics: list[str], limit: int) -> list[Article]:
        collected: list[Article] = []
        per_feed = max(3, limit // max(1, len(self._feeds)))
        for feed in self._feeds:
            try:
                articles = await asyncio.to_thread(self._fetch_sync, feed, per_feed)
            except Exception:
                continue
            collected.extend(articles)
        filtered = [
            article
            for article in collected
            if _matches(f"{article.title} {article.content}", topics)
        ]
        unique: dict[str, Article] = {}
        for article in filtered:
            unique.setdefault(article.id, article)
        return list(unique.values())[:limit]


def build_source(settings: Settings, override: str | None = None):
    name = (override or settings.source).lower()
    if name == "rss":
        if not settings.rss_feeds:
            raise ValueError("RSS 模式需要配置 DAILYFEED_RSS_FEEDS，多个地址用英文逗号分隔")
        return RssSource(settings.rss_feeds)
    return MockSource()
