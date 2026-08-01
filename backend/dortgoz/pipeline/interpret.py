"""[3]-[4] ÖN ELEME + YORUMLAMA — iki kademeli VLM kaskadı.

Ön eleme (RTX 4060, vLLM, MiniCPM-V 4.6): "bu pencerede dikkat gerektiren
bir durum var mı?" — Cerberus deseni (151,79x hızlanma, %97,2 doğruluk korunumu).

Yorumlama (RX 9070 XT, llama.cpp): çoklu-görüntü istemi (kareler + zaman
damgaları + dedektör metaverisi + hareket bölgesi görsel işaretleri) →
şema-garantili WindowReport JSON (GBNF).

TODO(hafta 1): interpret_window(frames, meta) → WindowReport  (tek çağrı iskeleti)
TODO(hafta 3): triage_window(frames) → bool                   (vLLM ön eleme)
TODO(hafta 3): visual_prompt(frame, motion_regions)           (görsel işaretleme)
TODO(hafta 1): json_schema tanımı — events.WindowReport.model_json_schema()
               response_format ile llama.cpp'ye verilir.
"""
