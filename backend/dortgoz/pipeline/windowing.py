"""[2] AKILLI KARE SEÇİMİ — puan-kapılı örnekleme + 30 sn pencereler.

Tasarım dayanağı (Holmes-VAD): tekdüze örnekleme yerine puan-kapılı seçim
= +23 AP ve 7,7 kat hız. Kare seçimi hattın en belirleyici halkasıdır.

TODO(hafta 2): select_keyframes(window, scores, k=4..8)
               — tetik çevresinde yoğun, sakin bölgede seyrek; phash tekilleştirme
TODO(hafta 3): burst(window, t, fps=5..10) — tırmandırma döngüsü için yoğun örnekleme
"""
