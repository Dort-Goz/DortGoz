from types import SimpleNamespace

import pytest

from dortgoz.agent import graph as graph_mod
from dortgoz.config import settings


class _Yakala:

    def __init__(self):
        self.kwargs = None

    async def __call__(self, client, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="tamam", tool_calls=None))])


@pytest.fixture
def yakala(monkeypatch):
    y = _Yakala()
    monkeypatch.setattr(graph_mod, "create_chat", y)
    monkeypatch.setattr(graph_mod, "main_client", lambda: object())
    return y


class _SessizManager:

    async def broadcast(self, *args, **kwargs) -> None:
        return None


async def _tek_tur(manager=None):
    return graph_mod._build_graph(manager or _SessizManager())


@pytest.mark.asyncio
async def test_varsayilan_uretim_davranisi_dusunmesiz_kalir(yakala, monkeypatch):
    monkeypatch.setattr(settings, "agent_effort", "")
    graf = await _tek_tur()
    await graf.ainvoke({"messages": [{"role": "user", "content": "merhaba"}],
                        "rounds": 0})
    kw = yakala.kwargs
    assert kw["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert kw["max_tokens"] == 700
    assert "reasoning_budget_tokens" not in kw["extra_body"]


@pytest.mark.asyncio
async def test_kademe_acikken_butce_gider_ve_tavan_yukselir(yakala, monkeypatch):
    monkeypatch.setattr(settings, "agent_effort", "medium")
    monkeypatch.setattr(settings, "agent_think_budget", 1200)
    graf = await _tek_tur()
    await graf.ainvoke({"messages": [{"role": "user", "content": "merhaba"}],
                        "rounds": 0})
    kw = yakala.kwargs
    assert kw["extra_body"]["chat_template_kwargs"] == {"reasoning_effort": "medium"}
    assert kw["extra_body"]["reasoning_budget_tokens"] == 1200
    assert kw["max_tokens"] > 1200


@pytest.mark.asyncio
async def test_agent_model_bos_ise_main_model_kullanilir(yakala, monkeypatch):
    monkeypatch.setattr(settings, "agent_model", "")
    monkeypatch.setattr(settings, "main_model", "qwen3.6-35b-a3b-vision")
    graf = await _tek_tur()
    await graf.ainvoke({"messages": [{"role": "user", "content": "m"}], "rounds": 0})
    assert yakala.kwargs["model"] == "qwen3.6-35b-a3b-vision"

    monkeypatch.setattr(settings, "agent_model", "qwen3.8-27b")
    graf = await _tek_tur()
    await graf.ainvoke({"messages": [{"role": "user", "content": "m"}], "rounds": 0})
    assert yakala.kwargs["model"] == "qwen3.8-27b"
