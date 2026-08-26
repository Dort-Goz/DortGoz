import asyncio
import json
from types import SimpleNamespace

import pytest

from dortgoz import main, session
from dortgoz.agent import graph
from dortgoz.agent.conversation import store
from dortgoz.agent.memory import Incident
from dortgoz.events import OperatorMessage, WindowEvent, WindowReport


class CaptureManager:
    def __init__(self) -> None:
        self.events = []

    async def broadcast(self, event) -> None:
        self.events.append(event)


class FakeLlm:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    async def __call__(self, _client, **kwargs):
        self.calls.append(kwargs["messages"])
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=f"yanıt-{len(self.calls)}",
                        tool_calls=None,
                    )
                )
            ]
        )


class ToolCallingLlm:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    async def __call__(self, _client, **kwargs):
        self.calls.append(kwargs["messages"])
        if len(self.calls) == 1:
            tool_call = SimpleNamespace(
                model_dump=lambda: {
                    "id": "tool-1",
                    "type": "function",
                    "function": {
                        "name": "pencere_sorgula",
                        "arguments": json.dumps({"t": 12, "gerekce": "olayın ayrıntısını doğrula"}),
                    },
                }
            )
            message = SimpleNamespace(content="", tool_calls=[tool_call])
        else:
            message = SimpleNamespace(
                content="12. saniyede koşan kişi görüldü.",
                tool_calls=None,
            )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture(autouse=True)
def clean_agent_state():
    session.clear()
    graph.reset_history()
    yield
    session.clear()
    graph.reset_history()


def context(feed: str, incident_id: str) -> session.RunContext:
    ctx = session.start(f"run-{feed}", f"{feed}.mp4", feed=feed)
    ctx.duration = 90
    ctx.finished = True
    ctx.ledger.incidents[incident_id] = Incident(
        incident_id=incident_id,
        title=f"{feed} olayı",
        first_seen=10,
        last_seen=20,
        risk="orta",
    )
    return ctx


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLlm()
    monkeypatch.setattr(graph, "create_chat", fake)
    monkeypatch.setattr(graph, "main_client", lambda: object())
    return fake


@pytest.mark.asyncio
async def test_multiple_cameras_require_clarification_before_model_call(monkeypatch):
    context("KAM-1", "inc-1")
    context("KAM-2", "inc-2")

    async def forbidden(*args, **kwargs):
        raise AssertionError("belirsiz bağlam modele gönderilmemeli")

    monkeypatch.setattr(graph, "create_chat", forbidden)
    monkeypatch.setattr(graph, "main_client", lambda: object())
    manager = CaptureManager()

    answer = await graph.run_chat("Orada ne oldu?", manager, dialogue_id="dialogue-a")

    assert "Hangi kamerayı" in answer
    assert "KAM-1" in answer and "KAM-2" in answer
    assert all(
        event.payload.dialogue_id == "dialogue-a"
        for event in manager.events
        if hasattr(event.payload, "dialogue_id")
    )


def test_feed_name_match_does_not_confuse_prefixes():
    context("KAM-1", "inc-1")
    expected = context("KAM-10", "inc-10")

    resolution = graph.resolve_context(
        "KAM-10 kamerasında ne oldu?",
        store.get("dialogue-a"),
    )

    assert resolution.context is expected
    assert resolution.feed == "KAM-10"


def test_explicit_empty_feed_selects_main_camera():
    expected = context("", "inc-main")
    context("KAM-2", "inc-2")

    resolution = graph.resolve_context(
        "Burada ne oldu?",
        store.get("dialogue-a"),
        feed="",
    )

    assert resolution.context is expected
    assert resolution.feed == ""


def test_system_prompt_routes_case_questions_to_targeted_review():
    context("KAM-1", "inc-1")

    prompt = graph.build_system_prompt(feed="KAM-1", referenced_event_id="inc-1")

    assert "`olayi_aydinlat`" in prompt
    assert "gözlenen, çıkarılan ve belirlenemeyen" in prompt


def test_two_mentioned_cameras_override_remembered_context_with_clarification():
    context("KAM-1", "inc-1")
    context("KAM-2", "inc-2")
    store.remember_context("dialogue-a", feed="KAM-1", referenced_event_id="inc-1")

    resolution = graph.resolve_context(
        "KAM-1 ile KAM-2 arasında karşılaştırma yap",
        store.get("dialogue-a"),
    )

    assert resolution.context is None
    assert "Tek bir kamera seç" in resolution.clarification


def test_duplicate_event_id_uses_remembered_feed_but_keeps_selected_event():
    context("KAM-1", "shared")
    context("KAM-2", "shared")
    store.remember_context("dialogue-a", feed="KAM-2", referenced_event_id="old-event")

    resolution = graph.resolve_context(
        "Bu olayı açıkla",
        store.get("dialogue-a"),
        referenced_event_id="shared",
    )

    assert resolution.feed == "KAM-2"
    assert resolution.referenced_event_id == "shared"


@pytest.mark.asyncio
async def test_websocket_preserves_explicit_main_feed(monkeypatch):
    captured = []

    async def fake_run_chat(*args, **kwargs):
        captured.append(kwargs["feed"])
        return "tamam"

    monkeypatch.setattr(graph, "run_chat", fake_run_chat)
    monkeypatch.setattr(main, "manager", CaptureManager())
    monkeypatch.setattr(main.settings, "mock", False)

    await main.handle_operator_message(
        OperatorMessage.model_validate({"kind": "chat", "text": "ana", "feed": ""})
    )
    await main.handle_operator_message(OperatorMessage(kind="chat", text="eski istemci"))

    assert captured == ["", None]


@pytest.mark.asyncio
async def test_dialogue_histories_are_isolated(fake_llm):
    context("KAM-1", "inc-1")
    manager = CaptureManager()

    await graph.run_chat("birinci soru", manager, dialogue_id="dialogue-a", feed="KAM-1")
    await graph.run_chat("başka kullanıcı", manager, dialogue_id="dialogue-b", feed="KAM-1")
    await graph.run_chat("devam sorusu", manager, dialogue_id="dialogue-a", feed="KAM-1")

    third = fake_llm.calls[2]
    assert any(item.get("content") == "birinci soru" for item in third)
    assert any(item.get("content") == "yanıt-1" for item in third)
    assert not any(item.get("content") == "başka kullanıcı" for item in third)
    assert len(store.get("dialogue-a").history) == 4
    assert len(store.get("dialogue-b").history) == 2


@pytest.mark.asyncio
async def test_context_switch_uses_selected_feed_and_event(fake_llm):
    context("KAM-1", "inc-1")
    context("KAM-2", "inc-2")
    manager = CaptureManager()

    await graph.run_chat(
        "Bu olayı açıkla",
        manager,
        dialogue_id="dialogue-a",
        feed="KAM-1",
        referenced_event_id="inc-1",
    )
    await graph.run_chat(
        "Şimdi buna bak",
        manager,
        dialogue_id="dialogue-a",
        feed="KAM-2",
        referenced_event_id="inc-2",
    )

    first_system = fake_llm.calls[0][0]["content"]
    second_system = fake_llm.calls[1][0]["content"]
    assert "AKTİF KAMERA: KAM-1" in first_system
    assert "AKTİF OLAY: [inc-1]" in first_system
    assert "KAM-2.mp4" not in first_system
    assert "AKTİF KAMERA: KAM-2" in second_system
    assert "AKTİF OLAY: [inc-2]" in second_system
    assert "KAM-1.mp4" not in second_system
    assert any(item.get("content") == "Bu olayı açıkla" for item in fake_llm.calls[1])


@pytest.mark.asyncio
async def test_agent_executes_tool_then_uses_observation(monkeypatch):
    ctx = context("KAM-1", "inc-1")
    ctx.reports.append(
        WindowReport(
            window_start=0,
            window_end=30,
            anomaly_type="bilinmeyen",
            summary="Bir kişi hızla hareket ediyor.",
            events=[
                WindowEvent(
                    t=12,
                    desc="Koşan kişi görüldü.",
                    severity_hint="orta",
                )
            ],
        )
    )
    fake = ToolCallingLlm()
    monkeypatch.setattr(graph, "create_chat", fake)
    monkeypatch.setattr(graph, "main_client", lambda: object())
    manager = CaptureManager()

    answer = await graph.run_chat(
        "12. saniyede ne oldu?",
        manager,
        dialogue_id="dialogue-tool",
        feed="KAM-1",
    )

    assert answer == "12. saniyede koşan kişi görüldü."
    assert len(fake.calls) == 2
    tool_messages = [item for item in fake.calls[1] if item["role"] == "tool"]
    assert len(tool_messages) == 1
    assert "Koşan kişi görüldü" in tool_messages[0]["content"]
    tool_events = [event for event in manager.events if event.payload.type == "tool_call"]
    assert len(tool_events) == 1
    assert tool_events[0].feed == "KAM-1"
    assert tool_events[0].payload.dialogue_id == "dialogue-tool"


@pytest.mark.asyncio
async def test_internal_error_is_not_exposed_to_operator(monkeypatch):
    context("KAM-1", "inc-1")

    async def fail(*args, **kwargs):
        raise RuntimeError("C:/secret/model/path token=123")

    monkeypatch.setattr(graph, "create_chat", fail)
    monkeypatch.setattr(graph, "main_client", lambda: object())
    manager = CaptureManager()

    answer = await graph.run_chat(
        "ne oldu",
        manager,
        dialogue_id="dialogue-a",
        feed="KAM-1",
    )

    assert "secret" not in answer and "token=123" not in answer
    error_steps = [
        event.payload
        for event in manager.events
        if event.payload.type == "agent_step" and event.payload.status == "error"
    ]
    assert error_steps and all("secret" not in step.detail for step in error_steps)


@pytest.mark.asyncio
async def test_model_timeout_returns_recoverable_message(monkeypatch):
    context("KAM-1", "inc-1")

    async def slow(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(graph, "create_chat", slow)
    monkeypatch.setattr(graph, "main_client", lambda: object())
    monkeypatch.setattr(graph.settings, "agent_timeout_seconds", 0.01)
    manager = CaptureManager()

    answer = await graph.run_chat(
        "ne oldu",
        manager,
        dialogue_id="dialogue-timeout",
        feed="KAM-1",
    )

    assert "zamanında yanıt vermedi" in answer
    assert any(
        event.payload.type == "agent_step"
        and event.payload.status == "error"
        and event.payload.detail == "yerel model zaman aşımı"
        for event in manager.events
    )
