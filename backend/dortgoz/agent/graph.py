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

CONTEXT_RULES = (
    "\n\nAşağıda AZ ÖNCE çözümlediğin kaydın tam bağlamı var. Operatör bu analizin "
    "devamı olarak konuşuyor: 'ne oldu', 'neden bu risk', 'şu saniyede ne vardı' "
    "gibi sorular bu kayda aittir.\n"
    "Kurallar:\n"
    "- Yalnız bu bağlamdaki gözlemlere dayan; bağlamda olmayan ayrıntıyı UYDURMA.\n"
    "- Bağlam yetmiyorsa 'kayıtta bu bilgi yok' de, tahmin yürütme.\n"
    "- Zamanları dakika:saniye biçiminde ver ki operatör videoda bulabilsin.\n"
    "- `dusuk` işaretli gözlemler olağan hareketliliktir, alarm değildir.\n"
    "- Belirsiz olarak işaretlenmiş noktaları belirsiz olarak aktar.\n\n"
)

NO_RUN_HINT = (
    "\n\nHenüz çözümlenmiş bir kayıt yok. Operatör bir olaydan söz ederse, önce "
    "üst çubuktan bir klip seçip analizi başlatması gerektiğini kısaca hatırlat."
)


def build_system_prompt() -> str:
    """Sistem istemi = rol + (varsa) koşu bağlamı.

    Sohbetin analizden SONRA da anlamlı olmasının yolu bu: koşu bitince
    `session` bağlamı yaşamaya devam eder, buraya gömülür.
    """
    from .. import session
    ctx = session.current()
    if ctx is None:
        return SYSTEM_TR + NO_RUN_HINT
    return SYSTEM_TR + CONTEXT_RULES + ctx.briefing()


async def run_chat(text: str, manager: ConnectionManager) -> None:
    """Operatör sohbeti — v0: doğrudan ana modele, akış halinde.

    TODO(hafta 3): LangGraph grafiğine bağla (araç kullanımı: seek_video,
    highlight_incident, query_detections, lookup_procedure) ve olay defteri
    bağlamını sistem istemine ekle.
    """
    from .. import session
    ctx = session.current()
    await manager.broadcast(Event.wrap(AgentStep(
        node="respond", status="start",
        detail=f"bağlam: {ctx.video} · {len(ctx.incidents)} olay" if ctx else "bağlamsız",
    )))
    client = main_client()
    stream = await client.chat.completions.create(
        model=settings.main_model,
        messages=[{"role": "system", "content": build_system_prompt()},
                  {"role": "user", "content": text}],
        stream=True,
        max_tokens=512,
        # Düşünme modu açıkken bütçenin tamamı reasoning_content'e gidip
        # delta.content boş kalabiliyordu → operatör boş yanıt görüyordu.
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
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
