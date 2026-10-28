from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from pathlib import Path

from ..config import Settings
from ..domain.models import Digest

SECTION_ORDER = ["AI", "工程", "产品", "商业", "其他"]


def render_markdown(digest: Digest) -> str:
    ordered = [(name, digest.sections[name]) for name in SECTION_ORDER if name in digest.sections]
    ordered += [(name, items) for name, items in digest.sections.items() if name not in SECTION_ORDER]
    lines = [
        "# 每日资讯日报",
        "",
        f"- 任务：`{digest.task_id}`",
        f"- 生成时间：{digest.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 条目：{digest.total} 篇（跨期去重 {digest.deduped} 篇）",
        "",
    ]
    if digest.total == 0:
        lines += ["今天没有新的内容，全部条目都在历史记忆中被判定为已推送。", ""]
    for name, items in ordered:
        if not items:
            continue
        lines.append(f"## {name}（{len(items)}）")
        lines.append("")
        for item in items:
            article = item.summary.article
            lines.append(f"### [{article.title}]({article.url})")
            lines.append(f"- 来源：{article.source} · 优先级 P{item.priority}")
            lines.append(f"- 摘要：{item.summary.summary}")
            if item.summary.tags:
                lines.append(f"- 标签：{' / '.join(item.summary.tags)}")
            if item.reason:
                lines.append(f"- 路由依据：{item.reason}")
            lines.append("")
    return "\n".join(lines)


class FileNotifier:
    def __init__(self, output_dir: str) -> None:
        self._dir = Path(output_dir)

    async def send(self, digest: Digest) -> str:
        self._dir.mkdir(parents=True, exist_ok=True)
        name = f"digest-{digest.created_at.strftime('%Y%m%d-%H%M%S')}-{digest.task_id}.md"
        path = self._dir / name
        path.write_text(render_markdown(digest), encoding="utf-8")
        return str(path)


class EmailNotifier:
    def __init__(self, file_notifier: FileNotifier, settings: Settings) -> None:
        self._file_notifier = file_notifier
        self._settings = settings

    async def send(self, digest: Digest) -> str:
        path = await self._file_notifier.send(digest)
        await asyncio.to_thread(self._send_sync, digest)
        return path

    def _send_sync(self, digest: Digest) -> None:
        settings = self._settings
        message = EmailMessage()
        message["Subject"] = f"每日资讯日报 {digest.created_at.strftime('%Y-%m-%d')}"
        message["From"] = settings.smtp_from or settings.smtp_user
        message["To"] = settings.smtp_to
        message.set_content(render_markdown(digest))
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port) as server:
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(message)


def build_notifier(settings: Settings):
    file_notifier = FileNotifier(settings.output_dir)
    if settings.smtp_host and settings.smtp_to:
        return EmailNotifier(file_notifier, settings)
    return file_notifier
