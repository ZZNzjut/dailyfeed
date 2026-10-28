from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from ..bootstrap import build_runtime
from ..config import Settings
from ..infrastructure.notify import SECTION_ORDER
from ..infrastructure.store import SqliteStore


def _reset_db(path: str) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def _build_runtime(settings: Settings, source_override: str | None = None):
    try:
        return build_runtime(settings, source_override)
    except Exception as exc:
        print(f"启动失败：{exc}")
        return None, None


def _print_result(label: str, result: dict) -> None:
    if not result.get("ok"):
        print(f"{label}：失败 - {result.get('error')}")
        return
    sections = result.get("sections") or {}
    ordered = [name for name in SECTION_ORDER if name in sections]
    ordered += [name for name in sections if name not in SECTION_ORDER]
    distribution = " / ".join(f"{name} {sections[name]}" for name in ordered) or "无"
    print(
        f"{label}：成功，{result.get('total', 0)} 条，"
        f"跨期去重 {result.get('deduped', 0)} 条，耗时 {result.get('duration_ms', 0):.1f} ms"
    )
    print(f"  板块分布：{distribution}")
    print(f"  日报文件：{result.get('path')}")


def _format_stats(stats: dict[str, float]) -> str:
    return "\n".join(
        [
            (
                f"任务：启动 {int(stats['tasks_started'])} / 成功 {int(stats['tasks_succeeded'])} / "
                f"失败 {int(stats['tasks_failed'])}（成功率 {stats['success_rate'] * 100:.1f}%）"
            ),
            f"采集：{int(stats['articles_collected'])} 篇，跨期去重 {int(stats['articles_deduped'])} 篇",
            (
                f"路由：{int(stats['articles_routed'])} 篇，命中 {int(stats['route_hit'])} 篇"
                f"（命中率 {stats['route_hit_rate'] * 100:.1f}%）"
            ),
            f"平均任务耗时：{stats['avg_duration_ms']:.1f} ms",
        ]
    )


async def _run_demo(workflow, store: SqliteStore) -> None:
    print("第 1 次运行：AI / Agent")
    first = await workflow.run_once(["AI", "Agent"])
    _print_result("  结果", first)
    print()
    print("第 2 次运行：开源 / 工程（验证跨期向量去重）")
    second = await workflow.run_once(["开源", "工程"])
    _print_result("  结果", second)
    print()
    print("指标汇总")
    print(_format_stats(store.stats()))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(prog="dailyfeed", description="多 Agent 协作的日报推送助手")
    subparsers = parser.add_subparsers(dest="command")

    demo = subparsers.add_parser("demo", help="离线演示：连续跑两次，验证跨期去重")
    demo.add_argument("--db", default="data/demo.db", help="演示数据库路径（会先清空）")

    run = subparsers.add_parser("run", help="执行一次日报任务")
    run.add_argument("--topics", default="AI,Agent,开源", help="关注方向，英文逗号分隔")
    run.add_argument("--source", choices=["mock", "rss", "search"], help="覆盖数据源")

    stats = subparsers.add_parser("stats", help="查看指标")
    stats.add_argument("--db", help="数据库路径")

    args = parser.parse_args(argv)

    if args.command == "stats":
        settings = Settings.from_env()
        store = SqliteStore(args.db or settings.db_path)
        print(_format_stats(store.stats()))
        store.close()
        return 0

    if args.command == "demo":
        settings = Settings.from_env()
        settings.llm = "mock"
        settings.bus = "memory"
        settings.source = "mock"
        settings.db_path = args.db
        _reset_db(settings.db_path)
        workflow, store = _build_runtime(settings)
        if workflow is None:
            return 1
        asyncio.run(_run_demo(workflow, store))
        store.close()
        return 0

    if args.command == "run":
        settings = Settings.from_env()
        topics = [item.strip() for item in args.topics.split(",") if item.strip()]
        workflow, store = _build_runtime(settings, source_override=args.source)
        if workflow is None:
            return 1
        result = asyncio.run(workflow.run_once(topics))
        _print_result("结果", result)
        print()
        print(_format_stats(store.stats()))
        store.close()
        return 0 if result.get("ok") else 1

    parser.print_help()
    return 0
