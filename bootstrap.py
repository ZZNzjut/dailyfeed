from __future__ import annotations

from .application.memory import ThreeTierMemory
from .application.workflow import Workflow
from .config import Settings
from .infrastructure.bus import build_bus
from .infrastructure.llm import build_llm
from .infrastructure.notify import build_notifier
from .infrastructure.sources import build_source
from .infrastructure.store import SqliteStore


def build_runtime(settings: Settings, source_override: str | None = None) -> tuple[Workflow, SqliteStore]:
    store = SqliteStore(settings.db_path)
    memory = ThreeTierMemory(store, settings.stm_size)
    workflow = Workflow(
        settings=settings,
        source=build_source(settings, source_override),
        llm=build_llm(settings),
        bus=build_bus(settings),
        store=store,
        memory=memory,
        notifier=build_notifier(settings),
    )
    return workflow, store
