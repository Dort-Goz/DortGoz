from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

HISTORY_LIMIT = 12
CONVERSATION_LIMIT = 256


@dataclass
class ConversationMemory:
    history: list[dict[str, Any]] = field(default_factory=list)
    feed: str | None = None
    referenced_event_id: str = ""


class ConversationStore:
    """İstemci oturumlarını birbirinden ayıran sınırlı çalışma belleği."""

    def __init__(self, max_conversations: int = CONVERSATION_LIMIT) -> None:
        self.max_conversations = max(1, max_conversations)
        self._items: OrderedDict[str, ConversationMemory] = OrderedDict()
        self._locks: dict[tuple[int, str], asyncio.Lock] = {}

    def get(self, dialogue_id: str) -> ConversationMemory:
        key = dialogue_id.strip() or "legacy"
        memory = self._items.get(key)
        if memory is None:
            memory = ConversationMemory()
            self._items[key] = memory
            self._prune()
        else:
            self._items.move_to_end(key)
        return memory

    def lock(self, dialogue_id: str) -> asyncio.Lock:
        loop_key = id(asyncio.get_running_loop())
        key = (loop_key, dialogue_id.strip() or "legacy")
        return self._locks.setdefault(key, asyncio.Lock())

    def remember_context(
        self,
        dialogue_id: str,
        *,
        feed: str | None,
        referenced_event_id: str,
    ) -> ConversationMemory:
        memory = self.get(dialogue_id)
        memory.feed = feed
        memory.referenced_event_id = referenced_event_id
        return memory

    def append_exchange(self, dialogue_id: str, user: str, assistant: str) -> None:
        memory = self.get(dialogue_id)
        memory.history.extend(
            [
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant},
            ]
        )
        del memory.history[:-HISTORY_LIMIT]

    def reset(self, dialogue_id: str | None = None, *, feed: str | None = None) -> None:
        if dialogue_id is not None:
            key = dialogue_id.strip() or "legacy"
            self._items.pop(key, None)
            self._locks = {item: lock for item, lock in self._locks.items() if item[1] != key}
            return
        if feed is not None:
            removed = {key for key, memory in self._items.items() if memory.feed == feed}
            for key in removed:
                self._items.pop(key, None)
            self._locks = {
                item: lock for item, lock in self._locks.items() if item[1] not in removed
            }
            return
        self._items.clear()
        self._locks.clear()

    def _prune(self) -> None:
        while len(self._items) > self.max_conversations:
            key, _ = self._items.popitem(last=False)
            self._locks = {item: lock for item, lock in self._locks.items() if item[1] != key}


store = ConversationStore()
