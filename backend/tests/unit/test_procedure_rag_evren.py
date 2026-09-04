from pathlib import Path
from types import SimpleNamespace

import pytest

from dortgoz.config import Settings
from dortgoz.services import procedure_rag
from dortgoz.services.procedure_index import LocalProcedureIndex
from dortgoz.services.procedure_rag import EvrenProcedureRag


@pytest.fixture
def index() -> LocalProcedureIndex:
    root = Path(__file__).resolve().parents[3] / "data" / "procedures"
    return LocalProcedureIndex.load(root, root / "manifest.json")


@pytest.fixture
def rag_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        qdrant_url="https://qdrant.invalid",
        qdrant_prefix="team-test",
        qdrant_api_key="fixture-key",
        qdrant_collection="procedures-test",
        embedding_model="bge-m3-embed",
        media_dir=tmp_path / "media",
        runs_dir=tmp_path / "runs",
    )


@pytest.mark.asyncio
async def test_dense_sync_and_query_keep_verified_citations(
    index: LocalProcedureIndex,
    rag_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    points = []
    calls = []

    async def fake_embedding(_client, *, model, input):
        assert model == "bge-m3-embed"
        return SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=[float(i + 1)] * 1024)
                  for i in range(len(input))]
        )

    async def transport(method, path, payload, allowed):
        calls.append((method, path, allowed))
        if path.endswith("/points?wait=true"):
            points.extend(payload["points"])
        if path.endswith("/points/query"):
            return {"result": {"points": [
                {"score": 0.91, "payload": points[0]["payload"]}
            ]}}
        return {}

    monkeypatch.setattr(procedure_rag, "create_embedding", fake_embedding)
    monkeypatch.setattr(procedure_rag, "main_client", lambda: None)
    service = EvrenProcedureRag(index, rag_settings, transport=transport)

    hits = await service.query("Kritik olayda ne yapmalıyım?")

    assert hits[0].document_id == "dortgoz-demo-operator-v1"
    assert hits[0].content_hash == index.manifest.documents[0].content_hash
    assert hits[0].score == pytest.approx(0.91)
    assert len(points) == 3
    assert [call[0] for call in calls] == ["PUT", "PUT", "POST"]


@pytest.mark.asyncio
async def test_query_drops_unverified_qdrant_payload(
    index: LocalProcedureIndex,
    rag_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_embedding(_client, *, model, input):
        return SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=[1.0] * 1024)
                  for i in range(len(input))]
        )

    async def transport(_method, path, _payload, _allowed):
        if path.endswith("/points/query"):
            return {"result": {"points": [{
                "score": 1.0,
                "payload": {
                    "document_id": "tampered",
                    "section": "1",
                    "action": "Güvenilmez işlem",
                    "version": "1",
                    "content_hash": "0" * 64,
                },
            }]}}
        return {}

    monkeypatch.setattr(procedure_rag, "create_embedding", fake_embedding)
    monkeypatch.setattr(procedure_rag, "main_client", lambda: None)
    service = EvrenProcedureRag(index, rag_settings, transport=transport)

    assert await service.query("soru") == []
