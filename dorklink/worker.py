from __future__ import annotations

import asyncio
import threading
import traceback

from PySide6.QtCore import QThread, Signal

from .crawler import DorkLinkCrawler
from .models import ScanConfig
from .storage import ScanStore


class ScanWorker(QThread):
    progress = Signal(dict)
    completed = Signal(int, list, int)
    failed = Signal(str)

    def __init__(self, config: ScanConfig, database_path: str):
        super().__init__()
        self.config = config
        self.database_path = database_path
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            store = ScanStore(self.database_path)
            crawler = DorkLinkCrawler(self.config, store, self.progress.emit, self._cancel.is_set)
            scan_id, items, errors = asyncio.run(crawler.run())
            self.completed.emit(scan_id, [item.to_dict() for item in items], errors)
        except Exception as exc:
            self.failed.emit(f"{exc}\n\n{traceback.format_exc()}")
