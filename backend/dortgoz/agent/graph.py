"""LangGraph ajan çekirdeği — iskelet.

Düğümler (tasarım: docs/interface_design.md + ön değerlendirme sunumu §5):

    triage ──▶ interpret ──▶ ledger ──▶ oversight ──▶ respond
                   ▲                        │
                   └── burst (tırmandırma) ◀┘  (tutarlılık eşiği altında)

Her düğüm giriş/çıkışta AgentStep olayı yayınlar; her araç çağrısı ToolCall
olarak akar — ajan konsolu bu izlerle canlanır.
"""

from __future__ import annotations

from ..events import AgentStep, ChatMessage, Event
from ..ws import ConnectionManager
from .llm import main_client
from ..config import settings

SYSTEM_TR = (
    "Sen Dörtgöz saha güvenliği operatör asistanısın. Video analiz hattının "
    "ürettiği olay defterine dayanarak Türkçe, kısa ve operasyonel yanıtlar ver. "
    "Belirsizlikte doğru soruyu sor; gerektiğinde inisiyatif al."
)


async def run_chat(text: str, manager: ConnectionManager) -> None:
    """Operatör sohbeti — v0: doğrudan ana modele, akış halinde.

    TODO(hafta 3): LangGraph grafiğine bağla (araç kullanımı: seek_video,
    highlight_incident, query_detections, lookup_procedure) ve olay defteri
    bağlamını sistem istemine ekle.
    """
    await manager.broadcast(Event.wrap(AgentStep(node="respond", status="start")))
    client = main_client()
    stream = await client.chat.completions.create(
        model=settings.main_model,
        messages=[{"role": "system", "content": SYSTEM_TR},
                  {"role": "user", "content": text}],
        stream=True,
        max_tokens=512,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        if delta:
            await manager.broadcast(Event.wrap(
                ChatMessage(role="agent", text=delta, streaming=True)))
    await manager.broadcast(Event.wrap(ChatMessage(role="agent", text="", streaming=False)))
    await manager.broadcast(Event.wrap(AgentStep(node="respond", status="end")))


# TODO(hafta 3): build_graph() — langgraph.StateGraph ile tam grafik:
#   - State: pencere raporları, olay defteri, tutarlılık puanı
#   - oversight düğümü: tutarlılık < eşik → burst düğümüne geri besleme
#   - normal-durum kuralları + prosedür RAG araçları
