"""Düşünme (reasoning) anahtarlarının model ailesine göre çevirisi.

İki aile iki ayrı lehçe konuşuyor:

- **Qwen3.6** (üretim yorumlayıcısı): düşünme AÇIK/KAPALI ikili anahtardır —
  ``chat_template_kwargs.enable_thinking``.
- **Qwen3.8** (aday yorumlayıcı): düşünme KADEMELİDİR —
  ``chat_template_kwargs.reasoning_effort`` ∈ {low, medium, high, xhigh}.
  İki anahtar birlikte gönderilmez; ``enable_thinking`` kademeyi ezer.

Bu modül tek karar noktası olur, çağrı yerleri lehçe bilmez.

⚠ **Düşünme bütçesi kademeli modda da ZORUNLU.** Ölçülmüş iki bağımsız kanıt:
(1) bu depoda 24-akış soak (2026-08-07) — bütçesiz düşünme 4.000 token tavanını
yiyip JSON'u yarıda bıraktı, tırmandırmaların %45'i şema hatasıyla tüm maliyeti
çöpe attı; (2) ev tarafında MMLU-Pro @xhigh (2026-08-17) — 140 sorunun 6'sında
düşünme 24.000 token tavanının TAMAMINI yedi ve içerik hiç başlamadı, 2'sinde
düşünme bitti ama içerik yine boş geldi (toplam %5,7 boş yanıt). Kademe ne kadar
yüksekse tavanı yeme riski o kadar büyük; şema-geçerli rapor üretmesi gereken bir
hatta bütçesiz düşünme çalıştırılmaz.
"""

from typing import Any

# Qwen3.8'in GERÇEK kademeleri — şablondan okundu (2026-08-17, GGUF chat_template):
#   reasoning_effort|default('xhigh')      → varsayılan EN PAHALI kademedir
#   'high' == 'xhigh'                      → şablon 'high'i xhigh'e çevirir
#   desteklenmeyen değer                   → raise_exception (istek 500 döner)
# ⚠ Varsayılanın xhigh olması bir TUZAK: model sunucusu görü profillerinde
# (`qwen3.8-27b-vision`, `-vision-dg`) --chat-template-kwargs YOK, yani düşünme
# açılan her istek kademe verilmezse xhigh koşar. Kademe hep AÇIKÇA verilmeli.
EFFORT_LADDER = ("low", "medium", "xhigh")
# Şablonun kendi takma adı; ölçüm kolunu ikiye bölmemek için sınırda çevrilir
# (aksi hâlde 'high' ve 'xhigh' AYNI koldur ama iki ayrı dosyaya yazılır).
EFFORT_ALIASES = {"high": "xhigh"}
# Kademeli modu KAPATAN özel değer — "" (aile varsayılanı) ile karıştırılmasın
EFFORT_OFF = "kapali"
VALID_EFFORTS = ("", EFFORT_OFF, *EFFORT_LADDER)


def validate_effort(effort: str) -> str:
    """Kademeyi doğrular ve takma adı çözer.

    Geçersiz kademe SESSİZCE varsayılana düşmez, hata verir (şablon da öyle
    yapıyor: desteklenmeyen değerde raise_exception).
    """
    effort = EFFORT_ALIASES.get(effort, effort)
    if effort not in VALID_EFFORTS:
        raise ValueError(
            f"geçersiz düşünme kademesi {effort!r} — geçerli değerler: "
            f"{', '.join(repr(v) for v in VALID_EFFORTS)}")
    return effort


def thinking_on(*, think: bool, effort: str = "") -> bool:
    """Bu çağrıda düşünme üretilecek mi? (token tavanını çağıran buna göre seçer)"""
    effort = validate_effort(effort)
    if effort == EFFORT_OFF:
        return False
    if effort in EFFORT_LADDER:
        return True
    return think


def thinking_extra(*, think: bool, effort: str = "", budget: int = 2500) -> dict[str, Any]:
    """``extra_body``'ye eklenecek düşünme anahtarlarını üretir.

    ``effort``:
      - ``""``      → aile varsayılanı: ``enable_thinking`` ikili anahtarı (mevcut davranış)
      - ``"kapali"`` → düşünme açıkça kapatılır (kademeli model dahil)
      - kademe adı  → ``reasoning_effort`` gönderilir, ``enable_thinking`` GÖNDERİLMEZ
    """
    effort = validate_effort(effort)
    if effort in EFFORT_LADDER:
        return {"chat_template_kwargs": {"reasoning_effort": effort},
                "reasoning_budget_tokens": budget}
    if effort == EFFORT_OFF:
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return {"chat_template_kwargs": {"enable_thinking": think},
            **({"reasoning_budget_tokens": budget} if think else {})}
