"""Current-selection state, independent of GUI and COM."""

from __future__ import annotations

from collections import OrderedDict
import threading

from .models import Analysis, MailSnapshot


class SelectionState:
    def __init__(self, cache_size: int = 12):
        self.current: MailSnapshot | None = None
        self.analysis: Analysis | None = None
        self.reply_draft = ""
        self.generation = 0
        self.cancel = threading.Event()
        self.reply_cancel = threading.Event()
        self._cache_size = cache_size
        self._cache: OrderedDict[str, Analysis] = OrderedDict()
        self._reply_cache: OrderedDict[str, str] = OrderedDict()
        self._reply_received = False

    @property
    def reply_received(self) -> bool:
        """An empty draft is still a completed, cacheable reply result."""
        return self._reply_received

    def cancel_pending(self) -> None:
        """Cancel captured worker tokens while preserving current results and edits."""
        self.cancel.set()
        self.reply_cancel.set()
        self.cancel = threading.Event()
        self.reply_cancel = threading.Event()

    def select(self, mail: MailSnapshot | None) -> int:
        self.cancel.set()
        self.reply_cancel.set()
        self.cancel = threading.Event()
        self.reply_cancel = threading.Event()
        self.generation += 1
        self.current = mail
        self.analysis = self._cache.get(mail.analysis_key) if mail else None
        self.reply_draft = self._reply_cache.get(mail.analysis_key, "") if mail else ""
        self._reply_received = bool(mail and mail.analysis_key in self._reply_cache)
        if mail and self.analysis:
            self._cache.move_to_end(mail.analysis_key)
        if mail and self._reply_received:
            self._reply_cache.move_to_end(mail.analysis_key)
        return self.generation

    def accept(self, generation: int, analysis: Analysis) -> bool:
        if generation != self.generation or self.current is None or self.cancel.is_set():
            return False
        self.analysis = analysis
        self._cache[self.current.analysis_key] = analysis
        self._cache.move_to_end(self.current.analysis_key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        if analysis.reply_draft and not self._reply_received and not self.reply_cancel.is_set():
            self._store_reply(analysis.reply_draft)
        return True

    def _store_reply(self, draft: str) -> None:
        self.reply_draft = draft
        self._reply_received = True
        self._reply_cache[self.current.analysis_key] = draft
        self._reply_cache.move_to_end(self.current.analysis_key)
        while len(self._reply_cache) > self._cache_size:
            self._reply_cache.popitem(last=False)

    def accept_reply(self, generation: int, draft: str) -> bool:
        if generation != self.generation or self.current is None or self.reply_cancel.is_set():
            return False
        if not isinstance(draft, str) or len(draft) > 16000:
            return False
        self._store_reply(draft)
        return True

    def restart_summary(self) -> threading.Event:
        self.cancel.set()
        self.cancel = threading.Event()
        self.forget_current()
        return self.cancel

    def restart_reply(self) -> threading.Event:
        self.reply_cancel.set()
        self.reply_cancel = threading.Event()
        if self.current:
            self._reply_cache.pop(self.current.analysis_key, None)
        self.reply_draft = ""
        self._reply_received = False
        return self.reply_cancel

    def forget_current(self) -> None:
        if self.current:
            self._cache.pop(self.current.analysis_key, None)
        self.analysis = None

    def clear_results(self) -> None:
        """Invalidate all provider-dependent results while keeping the selected mail."""
        self._cache.clear()
        self._reply_cache.clear()
        self.select(self.current)
