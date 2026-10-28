from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass
class Settings:
    llm: str = "mock"
    bus: str = "memory"
    source: str = "mock"
    db_path: str = "data/dailyfeed.db"
    output_dir: str = "data/digests"
    max_articles: int = 20
    stm_size: int = 8
    dedupe_threshold: float = 0.92
    timeout_seconds: float = 120.0

    redis_url: str = "redis://localhost:6379/0"
    redis_stream: str = "dailyfeed:events"
    redis_group: str = "dailyfeed"

    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    chat_model: str = "qwen-plus"
    embedding_model: str = "text-embedding-v3"

    rss_feeds: list[str] = field(default_factory=list)

    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        feeds = [item.strip() for item in _env("DAILYFEED_RSS_FEEDS", "").split(",") if item.strip()]
        return cls(
            llm=_env("DAILYFEED_LLM", "mock").lower(),
            bus=_env("DAILYFEED_BUS", "memory").lower(),
            source=_env("DAILYFEED_SOURCE", "mock").lower(),
            db_path=_env("DAILYFEED_DB", "data/dailyfeed.db"),
            output_dir=_env("DAILYFEED_OUTPUT_DIR", "data/digests"),
            max_articles=int(_env("DAILYFEED_MAX_ARTICLES", "20")),
            stm_size=int(_env("DAILYFEED_STM_SIZE", "8")),
            dedupe_threshold=float(_env("DAILYFEED_DEDUPE_THRESHOLD", "0.92")),
            timeout_seconds=float(_env("DAILYFEED_TIMEOUT", "120")),
            redis_url=_env("REDIS_URL", "redis://localhost:6379/0"),
            redis_stream=_env("DAILYFEED_REDIS_STREAM", "dailyfeed:events"),
            redis_group=_env("DAILYFEED_REDIS_GROUP", "dailyfeed"),
            dashscope_api_key=_env("DASHSCOPE_API_KEY", ""),
            dashscope_base_url=_env(
                "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
            chat_model=_env("DASHSCOPE_CHAT_MODEL", "qwen-plus"),
            embedding_model=_env("DASHSCOPE_EMBEDDING_MODEL", "text-embedding-v3"),
            rss_feeds=feeds,
            smtp_host=_env("SMTP_HOST", ""),
            smtp_port=int(_env("SMTP_PORT", "465")),
            smtp_user=_env("SMTP_USER", ""),
            smtp_password=_env("SMTP_PASSWORD", ""),
            smtp_from=_env("SMTP_FROM", ""),
            smtp_to=_env("SMTP_TO", ""),
        )
