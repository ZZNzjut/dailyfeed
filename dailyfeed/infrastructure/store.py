from __future__ import annotations

import json
import math
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from ..domain.models import Article, Digest, Task

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  topics TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  finished_at TEXT,
  error TEXT
);
CREATE TABLE IF NOT EXISTS articles (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  source TEXT NOT NULL,
  content TEXT NOT NULL,
  published_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_task ON articles(task_id);
CREATE TABLE IF NOT EXISTS digests (
  task_id TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  total INTEGER NOT NULL,
  deduped INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS global_memory (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  text TEXT NOT NULL,
  vector TEXT NOT NULL,
  meta TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS short_memory (
  task_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (task_id, seq)
);
CREATE TABLE IF NOT EXISTS task_state (
  task_id TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY (task_id, key)
);
CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  value REAL NOT NULL,
  task_id TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(name);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)


class SqliteStore:
    """SQLite 实现：任务/文章/日报仓储 + 三层记忆 + 指标。"""

    def __init__(self, path: str) -> None:
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---------- Repository ----------

    def create_task(self, topics: list[str]) -> Task:
        task_id = uuid.uuid4().hex[:12]
        created = datetime.now()
        self._conn.execute(
            "INSERT INTO tasks (id, topics, status, created_at) VALUES (?, ?, ?, ?)",
            (task_id, json.dumps(topics, ensure_ascii=False), "pending", created.isoformat(timespec="seconds")),
        )
        self._conn.commit()
        return Task(id=task_id, topics=topics, created_at=created)

    def set_task_status(self, task_id: str, status: str, error: str | None = None) -> None:
        finished = _now() if status in {"done", "failed"} else None
        self._conn.execute(
            "UPDATE tasks SET status = ?, error = ?, finished_at = COALESCE(?, finished_at) WHERE id = ?",
            (status, error, finished, task_id),
        )
        self._conn.commit()

    def save_articles(self, task_id: str, articles: list[Article]) -> None:
        if not articles:
            return
        self._conn.executemany(
            """
            INSERT OR REPLACE INTO articles
              (id, task_id, title, url, source, content, published_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    article.id,
                    task_id,
                    article.title,
                    article.url,
                    article.source,
                    article.content,
                    article.published_at.isoformat() if article.published_at else None,
                )
                for article in articles
            ],
        )
        self._conn.commit()

    def save_digest(self, digest: Digest, path: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO digests (task_id, path, total, deduped, created_at) VALUES (?, ?, ?, ?, ?)",
            (digest.task_id, path, digest.total, digest.deduped, digest.created_at.isoformat()),
        )
        self._conn.commit()

    def record_metric(self, name: str, value: float = 1.0, task_id: str | None = None) -> None:
        self._conn.execute(
            "INSERT INTO metrics (name, value, task_id, created_at) VALUES (?, ?, ?, ?)",
            (name, value, task_id, _now()),
        )
        self._conn.commit()

    # ---------- MemoryStore ----------

    def add_global(self, task_id: str, text: str, vector: list[float], meta: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO global_memory (task_id, text, vector, meta, created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, text, json.dumps(vector), json.dumps(meta, ensure_ascii=False), _now()),
        )
        self._conn.commit()

    def search_global(
        self, vector: list[float], k: int, exclude_task_id: str | None = None
    ) -> list[tuple[float, dict[str, Any]]]:
        if exclude_task_id:
            rows = self._conn.execute(
                "SELECT task_id, text, vector, meta FROM global_memory WHERE task_id != ?",
                (exclude_task_id,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT task_id, text, vector, meta FROM global_memory").fetchall()
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            score = _cosine(vector, json.loads(row["vector"]))
            if score <= 0:
                continue
            meta = json.loads(row["meta"])
            meta.update({"text": row["text"], "task_id": row["task_id"], "score": score})
            scored.append((score, meta))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[:k]

    def push_short(self, task_id: str, text: str, maxlen: int) -> None:
        row = self._conn.execute(
            "SELECT MAX(seq) AS seq FROM short_memory WHERE task_id = ?", (task_id,)
        ).fetchone()
        seq = int(row["seq"] or 0) + 1
        self._conn.execute(
            "INSERT INTO short_memory (task_id, seq, text, created_at) VALUES (?, ?, ?, ?)",
            (task_id, seq, text, _now()),
        )
        if maxlen > 0:
            self._conn.execute(
                "DELETE FROM short_memory WHERE task_id = ? AND seq <= ?", (task_id, seq - maxlen)
            )
        self._conn.commit()

    def recent_short(self, task_id: str, n: int) -> list[str]:
        rows = self._conn.execute(
            "SELECT text FROM short_memory WHERE task_id = ? ORDER BY seq DESC LIMIT ?",
            (task_id, n),
        ).fetchall()
        return [row["text"] for row in reversed(rows)]

    def get_task_state(self, task_id: str, key: str, default: Any = None) -> Any:
        row = self._conn.execute(
            "SELECT value FROM task_state WHERE task_id = ? AND key = ?", (task_id, key)
        ).fetchone()
        if row is None:
            return default
        return json.loads(row["value"])

    def set_task_state(self, task_id: str, key: str, value: Any) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO task_state (task_id, key, value) VALUES (?, ?, ?)",
            (task_id, key, json.dumps(value, ensure_ascii=False)),
        )
        self._conn.commit()

    # ---------- Stats ----------

    def stats(self) -> dict[str, float]:
        def count(name: str) -> int:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM metrics WHERE name = ?", (name,)).fetchone()
            return int(row["n"])

        def total(name: str) -> float:
            row = self._conn.execute("SELECT COALESCE(SUM(value), 0) AS v FROM metrics WHERE name = ?", (name,)).fetchone()
            return float(row["v"])

        def average(name: str) -> float:
            row = self._conn.execute("SELECT COALESCE(AVG(value), 0) AS v FROM metrics WHERE name = ?", (name,)).fetchone()
            return float(row["v"])

        started = count("task.started")
        succeeded = count("task.succeeded")
        failed = count("task.failed")
        routed = count("articles.routed")
        hits = count("route.hit")
        return {
            "tasks_started": started,
            "tasks_succeeded": succeeded,
            "tasks_failed": failed,
            "success_rate": succeeded / started if started else 0.0,
            "articles_collected": total("articles.collected"),
            "articles_deduped": total("articles.deduped"),
            "articles_routed": routed,
            "route_hit": hits,
            "route_hit_rate": hits / routed if routed else 0.0,
            "avg_duration_ms": average("task.duration_ms"),
        }
