"""LangGraph ajan çekirdeği — araç kullanan operatör asistanı.

Grafik (ReAct döngüsü):

    agent ──(araç çağrısı var)──▶ tools ──▶ agent
      │
      └──(nihai yanıt)──▶ END

Her düğüm geçişi AgentStep, her araç çağrısı ToolCall olayı olarak akar —
ajan konsolu bu izlerle canlanır (jüri: karar zinciri görünür). Nihai yanıt
ChatMessage(streaming=True) parçalarıyla verilir.

Araç turlarında akış kapalıdır (llama.cpp tool_calls ayrıştırması gövde
tamamlanınca güvenilir); "akış hissi" nihai metnin parça parça yayınıyla
verilir. `parallel_tool_calls: false` — 2026-08-03 kural seti.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from ..config import settings
from ..events import AgentStep, ChatMessage, Event
from ..ws import ConnectionManager
from . import tools
from .llm import create_chat, main_client
from ..pipeline.thinking import thinking_extra, thinking_on

SYSTEM_TR = (
    "Sen Dörtgöz saha güvenliği operatör asistanısın. Video analiz hattının "
    "ürettiği olay defterine dayanarak Türkçe, kısa ve operasyonel yanıtlar ver. "
    "Belirsizlikte doğru soruyu sor; gerektiğinde inisiyatif al.\n\n"
    "ARAÇLARIN VAR ve arayüzü yönetebilirsin: bir olaydan söz ederken "
    "`videoya_git` + `olayi_vurgula` ile operatöre GÖSTER; defterde olmayan "
    "ayrıntı sorulursa uydurmak yerine `pencere_sorgula` ile ham raporu getir; "
    "rapor yetersizse `yeniden_incele` ile kaydı derinlemesine yeniden oku; "
    "kanıt istenirse `kanit_klibi_olustur`. Aksiyon gerekiyorsa "
    "`aktuator_calistir` öner — kritik olanlar operatör onayına düşer. "
    "Araçları gerçekten katkı sağlayacaklarında kullan; her yanıt araç "
    "gerektirmez."
)

CONTEXT_RULES = (
    "\n\nAşağıda AZ ÖNCE çözümlediğin kaydın tam bağlamı var. Operatör bu analizin "
    "devamı olarak konuşuyor: 'ne oldu', 'neden bu risk', 'şu saniyede ne vardı' "
    "gibi sorular bu kayda aittir.\n"
    "Kurallar:\n"
    "- Yalnız bu bağlamdaki gözlemlere dayan; bağlamda olmayan ayrıntıyı UYDURMA.\n"
    "- Bağlam yetmiyorsa 'kayıtta bu bilgi yok' de ya da `pencere_sorgula`/"
    "`yeniden_incele` ile kaynağa in.\n"
    "- Zamanları dakika:saniye biçiminde ver ki operatör videoda bulabilsin.\n"
    "- `dusuk` işaretli gözlemler olağan hareketliliktir, alarm değildir.\n"
    "- Belirsiz olarak işaretlenmiş noktaları belirsiz olarak aktar.\n\n"
)

NO_RUN_HINT = (
    "\n\nHenüz çözümlenmiş bir kayıt yok. Operatör bir olaydan söz ederse, önce "
    "üst çubuktan bir klip seçip analizi başlatması gerektiğini kısaca hatırlat."
)

MAX_TOOL_ROUNDS = 5      # emniyet: araç döngüsü sınırsız dönmesin
HISTORY_LIMIT = 12       # sohbet hafızası (operatör+ajan nihai mesajları)

# Çok turlu sohbet hafızası — araç trafiği DEĞİL, yalnız nihai mesajlar tutulur
# (bağlam şişmesin; araç sonuçları zaten yanıtın içine işlenmiş olur).
_history: list[dict[str, str]] = []


def reset_history() -> None:
    """Yeni koşu = yeni bağlam; eski sohbet yeni kayda taşınmaz."""
    _history.clear()


def build_system_prompt() -> str:
    """Sistem istemi = rol + (varsa) koşu bağlam(lar)ı.

    Sohbetin analizden SONRA da anlamlı olmasının yolu bu: koşu bitince
    `session` bağlamı yaşamaya devam eder, buraya gömülür. Çoklu-akış (demo)
    kipinde TÜM kameraların brifingi başlıklarla art arda verilir — operatör
    "3. kamerada ne oldu" diye sorabilir. 256K'lık modelde yer sorunu yok.
    """
    from .. import session
    ctxs = session.all_contexts()
    if not ctxs:
        return SYSTEM_TR + NO_RUN_HINT
    if len(ctxs) == 1:
        return SYSTEM_TR + CONTEXT_RULES + ctxs[0].briefing()
    parts = [SYSTEM_TR + CONTEXT_RULES,
             f"AYNI ANDA {len(ctxs)} KAMERA ÇÖZÜMLENDİ. Operatör kamera adıyla "
             "sorabilir; hangi kameradan söz ettiği belirsizse sor.\n"]
    for c in ctxs:
        parts.append(f"\n════ KAMERA {c.feed or 'ana'} ════\n{c.briefing()}")
    return "\n".join(parts)


class ChatState(TypedDict):
    messages: list[dict[str, Any]]
    rounds: int


def _build_graph(manager: ConnectionManager):
    client = main_client()

    async def agent_node(state: ChatState) -> dict:
        rounds = state["rounds"]
        await _step(manager, "respond", "start",
                    f"tur {rounds + 1}" if rounds else "")
        # Tur sınırı aşıldıysa araçsız çağrı — model yanıtını vermek ZORUNDA
        kwargs: dict[str, Any] = {}
        if rounds < MAX_TOOL_ROUNDS:
            kwargs = {"tools": tools.TOOLS, "parallel_tool_calls": False}
        # Düşünme burada bütçesiz açıldığında 700 token'ın tamamı
        # reasoning_content'e gidiyordu ve operatör BOŞ yanıt görüyordu. Arıza
        # kademede değil bütçesizlikteydi: bütçe `</think>`i zorlar, kalan
        # token içeriğe yeter. Tavan da düşünen turda yükseltilir.
        dusunur = thinking_on(think=False, effort=settings.agent_effort)
        resp = await create_chat(client,
            model=settings.agent_model or settings.main_model,
            messages=state["messages"],
            max_tokens=2200 if dusunur else 700,
            temperature=0.3,
            extra_body=thinking_extra(think=False, effort=settings.agent_effort,
                                      budget=settings.agent_think_budget),
            **kwargs,
        )
        msg = resp.choices[0].message
        entry: dict[str, Any] = {"role": "assistant",
                                 "content": msg.content or ""}
        if msg.tool_calls:
            entry["tool_calls"] = [tc.model_dump() for tc in msg.tool_calls]
        return {"messages": state["messages"] + [entry], "rounds": rounds + 1}

    async def tools_node(state: ChatState) -> dict:
        calls = state["messages"][-1].get("tool_calls", [])
        out = list(state["messages"])
        for tc in calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args = tools.parse_args(fn.get("arguments", ""))
            await _step(manager, "tools", "start", name)
            result = await tools.execute(name, args, manager)
            out.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                        "content": result})
            await _step(manager, "tools", "end", name)
        return {"messages": out, "rounds": state["rounds"]}

    def route(state: ChatState) -> str:
        return "tools" if state["messages"][-1].get("tool_calls") else END

    g = StateGraph(ChatState)
    g.add_node("agent", agent_node)
    g.add_node("tools", tools_node)
    g.set_entry_point("agent")
    g.add_conditional_edges("agent", route)
    g.add_edge("tools", "agent")
    return g.compile()


async def _step(manager: ConnectionManager, node: str,
                status: str, detail: str = "") -> None:
    await manager.broadcast(Event.wrap(
        AgentStep(node=node, status=status, detail=detail)))  # type: ignore[arg-type]


async def _stream_text(manager: ConnectionManager, text: str) -> None:
    """Nihai yanıtı parça parça yayınlar (arayüz akış sözleşmesi korunur)."""
    for i in range(0, len(text), 48):
        await manager.broadcast(Event.wrap(
            ChatMessage(role="agent", text=text[i:i + 48], streaming=True)))
    await manager.broadcast(Event.wrap(
        ChatMessage(role="agent", text="", streaming=False)))


async def run_chat(text: str, manager: ConnectionManager) -> None:
    """Operatör sohbeti — LangGraph araç döngüsü + çok turlu hafıza."""
    from .. import session
    ctx = session.current()
    await _step(manager, "respond", "start",
                f"bağlam: {ctx.video} · {len(ctx.incidents)} olay" if ctx else "bağlamsız")

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt()},
        *_history,
        {"role": "user", "content": text},
    ]
    graph = _build_graph(manager)
    try:
        final = await graph.ainvoke({"messages": messages, "rounds": 0})
        answer = (final["messages"][-1].get("content") or "").strip()
        if not answer:
            answer = "Yanıt üretemedim — soruyu farklı ifade eder misin?"
    except Exception as exc:
        answer = f"Ajan hatası: {str(exc)[:200]}"
        await _step(manager, "respond", "error", answer)

    _history.append({"role": "user", "content": text})
    _history.append({"role": "assistant", "content": answer})
    del _history[:-HISTORY_LIMIT]

    await _stream_text(manager, answer)
    await _step(manager, "respond", "end")
