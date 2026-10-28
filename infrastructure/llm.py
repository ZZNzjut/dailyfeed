from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import urllib.error
import urllib.request

from ..config import Settings
from ..domain.models import Article, Section, Summary

SECTION_KEYWORDS: dict[Section, list[str]] = {
    Section.AI: ["大模型", "模型", "Agent", "智能体", "推理", "多模态", "RAG", "向量", "评测", "AI"],
    Section.ENGINEERING: ["架构", "部署", "数据库", "Redis", "Kafka", "并发", "重构", "工程", "性能", "Kubernetes", "检索"],
    Section.PRODUCT: ["产品", "发布", "上线", "用户", "体验", "功能", "设计", "对话"],
    Section.BUSINESS: ["融资", "营收", "市场", "收购", "合作", "商业化", "估值", "芯片"],
}

TAG_KEYWORDS = [
    "大模型",
    "Agent",
    "开源",
    "推理",
    "多模态",
    "RAG",
    "向量数据库",
    "融资",
    "芯片",
    "部署",
    "架构",
    "评测",
    "数据库",
    "Redis",
    "Kafka",
]

_CJK = re.compile(r"[\u4e00-\u9fff]")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _tokens(text: str) -> list[str]:
    lowered = (text or "").lower()
    tokens = re.findall(r"[a-z0-9]+", lowered)
    cjk = "".join(_CJK.findall(lowered))
    tokens.extend(cjk[i : i + 2] for i in range(len(cjk) - 1))
    return tokens


def _hash_embedding(text: str, dim: int = 256) -> list[float]:
    vector = [0.0] * dim
    for token in _tokens(text):
        index = int(hashlib.md5(token.encode("utf-8")).hexdigest()[:8], 16) % dim
        vector[index] += 1.0
    norm = math.sqrt(sum(value * value for value in vector))
    if norm:
        vector = [value / norm for value in vector]
    return vector


class MockLLM:
    """离线可运行的确定性实现：摘要走抽取式，路由走向关键词打分，向量走哈希词袋。"""

    async def summarize(self, article: Article) -> tuple[str, list[str]]:
        text = _clean(article.content) or _clean(article.title)
        sentences = [part.strip() for part in re.split(r"(?<=[。！？!?])", text) if part.strip()]
        summary = "".join(sentences[:2]) or text
        if len(summary) > 120:
            summary = summary[:120].rstrip() + "…"
        tags = [keyword for keyword in TAG_KEYWORDS if keyword in text or keyword in article.title]
        return summary, tags[:3] or ["资讯"]

    async def route(
        self, summary: Summary, topics: list[str], recent: list[str]
    ) -> tuple[Section, int, str]:
        text = f"{summary.article.title} {summary.summary}"
        scores = {
            section: sum(text.count(keyword) for keyword in keywords)
            for section, keywords in SECTION_KEYWORDS.items()
        }
        best = max(scores, key=lambda section: scores[section])
        hits = scores[best]
        if hits == 0:
            return Section.OTHER, 1, "未命中领域关键词"
        priority = max(1, min(5, hits))
        return best, priority, f"{best.value} 关键词命中 {hits} 次"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_hash_embedding(text) for text in texts]


def _parse_json(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"模型未返回 JSON：{text[:200]}")
    return json.loads(cleaned[start : end + 1])


class DashScopeLLM:
    """通过 DashScope 兼容 OpenAI 的接口调用 Qwen 与 Embedding 模型。"""

    def __init__(self, settings: Settings) -> None:
        if not settings.dashscope_api_key:
            raise ValueError("DAILYFEED_LLM=dashscope 需要设置 DASHSCOPE_API_KEY")
        self._settings = settings
        self._base_url = settings.dashscope_base_url.rstrip("/")

    def _post_sync(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._settings.dashscope_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"DashScope 请求失败 HTTP {exc.code}: {detail}") from exc

    async def _post(self, path: str, payload: dict) -> dict:
        return await asyncio.to_thread(self._post_sync, path, payload)

    async def _chat(self, system: str, user: str) -> str:
        data = await self._post(
            "/chat/completions",
            {
                "model": self._settings.chat_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
            },
        )
        return data["choices"][0]["message"]["content"]

    async def summarize(self, article: Article) -> tuple[str, list[str]]:
        content = _clean(article.content)[:2000] or article.title
        raw = await self._chat(
            "你是资讯编辑，擅长把长文压缩成信息密度高的一句话摘要。只输出 JSON。",
            (
                "请用中文把文章压缩成不超过 80 字的摘要，并给出 1-3 个标签。\n"
                '输出格式：{"summary": "...", "tags": ["..."]}\n'
                f"标题：{article.title}\n正文：{content}"
            ),
        )
        payload = _parse_json(raw)
        summary = str(payload.get("summary", "")).strip() or _clean(article.content)[:80]
        tags = [str(tag).strip() for tag in payload.get("tags", []) if str(tag).strip()]
        return summary, tags[:3]

    async def route(
        self, summary: Summary, topics: list[str], recent: list[str]
    ) -> tuple[Section, int, str]:
        recent_hint = "\n".join(f"- {item}" for item in recent[-5:]) or "（无）"
        raw = await self._chat(
            "你是资讯主编，负责给文章分栏并评估重要性。只输出 JSON。",
            (
                "把文章分到以下栏目之一：AI、工程、产品、商业、其他。\n"
                "给出 1-5 的优先级（5 最高），并说明理由。\n"
                '输出格式：{"section": "AI", "priority": 4, "reason": "..."}\n'
                f"关注方向：{', '.join(topics) or '不限'}\n"
                f"近期处理记录：\n{recent_hint}\n"
                f"标题：{summary.article.title}\n摘要：{summary.summary}\n标签：{', '.join(summary.tags)}"
            ),
        )
        payload = _parse_json(raw)
        section = Section.parse(str(payload.get("section", "其他")))
        priority = max(1, min(5, int(payload.get("priority", 3))))
        reason = str(payload.get("reason", "")).strip()
        return section, priority, reason

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        data = await self._post(
            "/embeddings",
            {
                "model": self._settings.embedding_model,
                "input": texts,
                "encoding_format": "float",
            },
        )
        items = sorted(data["data"], key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in items]


def build_llm(settings: Settings):
    if settings.llm == "dashscope":
        return DashScopeLLM(settings)
    return MockLLM()
