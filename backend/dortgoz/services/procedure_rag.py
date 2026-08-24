from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field

from ..agent.llm import create_embedding, main_client
from ..config import Settings
from .procedure_index import LocalProcedureIndex

Transport = Callable[[str, str, dict[str, Any] | None, set[int]], Awaitable[dict[str, Any]]]


class ProcedureRagUnavailable(RuntimeError):
    pass


class ProcedureHit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1)
    section: str = Field(min_length=1)
    action: str = Field(min_length=1)
    version: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    score: float


class EvrenProcedureRag:
    def __init__(
        self,
        index: LocalProcedureIndex,
        settings: Settings,
        *,
        transport: Transport | None = None,
    ) -> None:
        self.index = index
        self.settings = settings
        self.transport = transport or self._http
        self._synced = False
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.qdrant_url
            and self.settings.qdrant_api_key
            and self.settings.embedding_model
        )

    async def sync(self) -> None:
        if self._synced:
            return
        if not self.configured:
            raise ProcedureRagUnavailable("EVREN Qdrant kimliği ayarlanmadı")
        async with self._lock:
            if self._synced:
                return
            rows = self._rows()
            if not rows:
                raise ProcedureRagUnavailable("onaylı ve geçerli prosedür bölümü yok")
            vectors = await self._embed([row[1] for row in rows])
            collection = quote(self.settings.qdrant_collection, safe="")
            await self.transport(
                "PUT",
                f"/collections/{collection}",
                {"vectors": {"size": 1024, "distance": "Cosine"}},
                {409},
            )
            await self.transport(
                "PUT",
                f"/collections/{collection}/points?wait=true",
                {
                    "points": [
                        {
                            "id": str(uuid5(NAMESPACE_URL, row[0])),
                            "vector": vector,
                            "payload": row[2],
                        }
                        for row, vector in zip(rows, vectors, strict=True)
                    ]
                },
                set(),
            )
            self._synced = True

    async def query(self, text: str, *, limit: int | None = None) -> list[ProcedureHit]:
        question = text.strip()
        if not question:
            return []
        await self.sync()
        vector = (await self._embed([question]))[0]
        collection = quote(self.settings.qdrant_collection, safe="")
        payload = await self.transport(
            "POST",
            f"/collections/{collection}/points/query",
            {
                "query": vector,
                "limit": limit or self.settings.procedure_rag_top_k,
                "with_payload": True,
            },
            set(),
        )
        result = payload.get("result", {})
        points = result.get("points", []) if isinstance(result, dict) else result
        allowed = {
            (row[2]["document_id"], row[2]["section"], row[2]["content_hash"])
            for row in self._rows()
        }
        hits = []
        for point in points if isinstance(points, list) else []:
            item = point.get("payload", {}) if isinstance(point, dict) else {}
            key = (item.get("document_id"), item.get("section"), item.get("content_hash"))
            if key not in allowed:
                continue
            hits.append(
                ProcedureHit(
                    document_id=item["document_id"],
                    section=item["section"],
                    action=item["action"],
                    version=item["version"],
                    content_hash=item["content_hash"],
                    score=float(point.get("score", 0.0)),
                )
            )
        return hits

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        response = await create_embedding(
            main_client(), model=self.settings.embedding_model, input=texts
        )
        vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
        if len(vectors) != len(texts) or any(len(vector) != 1024 for vector in vectors):
            raise ProcedureRagUnavailable("EVREN gömme çıktısı 1024 boyutunda değil")
        return vectors

    def _rows(self) -> list[tuple[str, str, dict[str, Any]]]:
        today = date.today()
        rows = []
        for document in self.index.manifest.documents:
            current = (
                (document.valid_from is None or document.valid_from <= today)
                and (document.valid_until is None or today <= document.valid_until)
            )
            if not document.approved_for_demo or not current:
                continue
            for section in document.sections:
                payload = {
                    "document_id": document.document_id,
                    "section": section.section,
                    "action": section.action,
                    "version": document.version,
                    "content_hash": document.content_hash,
                }
                key = f"{document.document_id}:{document.version}:{section.section}"
                rows.append((key, f"{section.section}\n{section.action}", payload))
        return rows

    async def _http(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        allowed_status: set[int],
    ) -> dict[str, Any]:
        base = self.settings.qdrant_url.rstrip("/")
        prefix = self.settings.qdrant_prefix.strip("/")
        if prefix and not base.endswith(f"/{prefix}"):
            base = f"{base}/{prefix}"
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            base + path,
            data=body,
            method=method,
            headers={
                "api-key": self.settings.qdrant_api_key,
                "Content-Type": "application/json",
            },
        )

        def send() -> dict[str, Any]:
            try:
                with urllib.request.urlopen(request, timeout=600) as response:
                    raw = response.read()
            except urllib.error.HTTPError as exc:
                if exc.code in allowed_status:
                    return {}
                raise ProcedureRagUnavailable(f"Qdrant HTTP {exc.code}") from exc
            except OSError as exc:
                raise ProcedureRagUnavailable("Qdrant erişilemedi") from exc
            return json.loads(raw) if raw else {}

        return await asyncio.to_thread(send)


__all__ = ["EvrenProcedureRag", "ProcedureHit", "ProcedureRagUnavailable"]
