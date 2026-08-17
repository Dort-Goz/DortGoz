"""Düşünme lehçesi: iki model ailesinin anahtarları doğru çevriliyor mu?"""

import pytest

from dortgoz.pipeline.thinking import (
    EFFORT_ALIASES,
    EFFORT_LADDER,
    EFFORT_OFF,
    thinking_extra,
    thinking_on,
    validate_effort,
)


def test_aile_varsayilani_ikili_anahtar_kalir():
    """Kademe verilmezse üretim davranışı DEĞİŞMEZ (Qwen3.6 yolu)."""
    assert thinking_extra(think=False) == {
        "chat_template_kwargs": {"enable_thinking": False}}
    assert thinking_extra(think=True, budget=2500) == {
        "chat_template_kwargs": {"enable_thinking": True},
        "reasoning_budget_tokens": 2500}


@pytest.mark.parametrize("kademe", EFFORT_LADDER)
def test_kademe_reasoning_effort_gonderir_ve_enable_thinking_gondermez(kademe):
    """İki anahtar birlikte gitmez — enable_thinking kademeyi ezerdi."""
    extra = thinking_extra(think=False, effort=kademe, budget=1800)
    assert extra["chat_template_kwargs"] == {"reasoning_effort": kademe}
    assert "enable_thinking" not in extra["chat_template_kwargs"]
    # Bütçe kademeli modda da uygulanır (bkz. thinking.py gerekçesi)
    assert extra["reasoning_budget_tokens"] == 1800


def test_kapali_kademe_dusunmeyi_acikca_kapatir():
    extra = thinking_extra(think=True, effort=EFFORT_OFF)
    assert extra == {"chat_template_kwargs": {"enable_thinking": False}}
    assert thinking_on(think=True, effort=EFFORT_OFF) is False


@pytest.mark.parametrize("kademe,beklenen", [
    ("", False), (EFFORT_OFF, False), ("low", True), ("medium", True), ("xhigh", True),
])
def test_thinking_on_token_tavanini_belirler(kademe, beklenen):
    assert thinking_on(think=False, effort=kademe) is beklenen


def test_think_bayragi_aile_varsayilaninda_hala_gecerli():
    assert thinking_on(think=True, effort="") is True


def test_gecersiz_kademe_sessizce_dusmez_hata_verir():
    """Yanlış yapılandırma sessiz taban davranışına düşmemeli (depo kuralı)."""
    with pytest.raises(ValueError, match="geçersiz düşünme kademesi"):
        validate_effort("ultra")
    with pytest.raises(ValueError):
        thinking_extra(think=False, effort="medium-high")


def test_high_takma_adi_xhigh_e_cozulur():
    """Şablon 'high'i xhigh'e çeviriyor — kolu ikiye bölmemek için biz de çeviririz."""
    assert EFFORT_ALIASES["high"] == "xhigh"
    assert validate_effort("high") == "xhigh"
    assert (thinking_extra(think=False, effort="high")["chat_template_kwargs"]
            == {"reasoning_effort": "xhigh"})
    assert "high" not in EFFORT_LADDER          # kolun kimliği tek addır
