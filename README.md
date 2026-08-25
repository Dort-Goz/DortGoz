# Dörtgöz

**Ekip: Dörtgöz — TEKNOFEST 2026 Yapay Zeka Dil Ajanları, Senaryo 3**

Dörtgöz, kamera videosunu olay odaklı bir çalışma listesine dönüştüren,
ajan tabanlı bir video analiz ve karar destek sistemidir. Sistem olayın ne
zaman başladığını, ne olduğunu, hangi kanıta dayandığını ve operatörün hangi
adımı değerlendirebileceğini Türkçe olarak sunar. Nihai karar operatörde kalır.

Sistem kişi kimliği, suçluluk ya da niyet hakkında hüküm vermez. Görüntüdeki
olay adaylarını zaman damgası, kanıt karesi, belirsizlik, risk gerekçesi ve
insan incelemesiyle birlikte sunar. Dış kuruma gönderim veya fiziksel işlem
kendi kendine yapılmaz.

## Uçtan uca akış

```text
video / canlı akış
  → güvenli ingest + hareket profili
  → 30 sn pencereler + SigLIP-2 semantik screening + D-FINE kişi kurtarma (yerel, CPU/ONNX)
  → VLM video yorumu (Türkçe, şema-zorlamalı JSON)
  → kanıt doğrulayıcı (kare kimliği + zaman + SHA-256, fail-closed)
  → olay defteri (Ledger) + olay-geneli ikinci geçiş
  → deterministik risk + kaynaklı prosedür önerisi
  → insan onayı → REST/WS operatör konsolu
```

Çıkarım, yarışmanın sağladığı EVREN servisinde (vLLM) veya `.env` ile seçilen
herhangi bir OpenAI-uyumlu yerel uçta çalışır. Ayrıntı ve diyagram:
[`docs/MIMARI.md`](docs/MIMARI.md).

## Hızlı başlangıç

GPU/model gerektirmeyen arayüz test akışı:

```bash
./scripts/dev.sh          # Linux/macOS
```

```powershell
.\scripts\dev.ps1         # Windows
```

Bu kip yalnız arayüz olay sözleşmesini oynatır ve video analizi yapmaz. Gerçek
analiz için `.env.example` dosyasını `.env` yapın, uç noktayı ayarlayın ve
`./scripts/dev.sh real` (veya `.\scripts\dev.ps1 -Real`) kullanın. Arayüz
`http://localhost:5173`, API `http://localhost:8000`, liveness `/health`,
readiness `/ready` yolundadır. Adım adım temiz kurulum:
[`docs/SETUP.md`](docs/SETUP.md).

## Doğrulama

```bash
cd backend && uv run pytest -q && uv run ruff check .
cd ../frontend && bun run build
cd .. && python scripts/verify_offline.py
```

## Ölçüm sonuçları

UCF-Crime resmî test bölmesi (290 klip, 10,30 saat) üzerinde tam üretim
koşusu: 140 anomali klibinin 121'i yakalandı, 150 normal klipte 22 eyleme konu
yanlış alarm, dört eşzamanlı işte 7,30× gerçek zaman hızı. Bu sayılar
geliştirme kıyasıdır; genelleme kanıtı olarak, geliştirmede hiç kullanılmamış
80 kliplik kör holdout'ta %90,0 [%76,9–%96,0] yakalama ölçülmüştür. Tam rapor,
sınırlar ve tekrar üretim komutları:
[`docs/BENCHMARK_REPORT.md`](docs/BENCHMARK_REPORT.md).

## Veri kümeleri

Ham video klipleri ve model ağırlıkları bu repoya girmez; herkese açık
kaynaklardan indirilir:

- **UCF-Crime** — resmî sayfa: <https://www.crcv.ucf.edu/projects/real-world/>.
  Geliştirmede kullanılan sabit örnek klip listesi ve indirme:
  `python scripts/fetch_ucf_samples.py`.
- **UCA (UCF-Crime Annotation)** — kaynak:
  <https://github.com/Xuange923/Surveillance-Video-Understanding>. İndirme ve
  SHA-256 doğrulama: `python scripts/fetch_uca.py`. Atıf:
  [`data/uca/CITATION.bib`](data/uca/CITATION.bib).

## Dokümantasyon

- [Mimari özet ve diyagram](docs/MIMARI.md)
- [Mimari karar kaydı](DORTGOZ_ARCHITECTURE_BASELINE.md)
- [GitHub'dan temiz kurulum](docs/SETUP.md)
- [Benchmark raporu](docs/BENCHMARK_REPORT.md)
- [Üçüncü taraf lisans bildirimleri](THIRD_PARTY_NOTICES.md)

## Lisans ve veri politikası

Kaynak kod Apache-2.0'dır. Bağımlılıklar için OSI onaylı açık kaynak lisanslar
kabul edilir; AGPL ve SSPL yasaktır ve release kapısı (`scripts/sbom.py
--check`) güçlü copyleft'i ayrıca engeller. Ham veri kümesi klipleri, model
ağırlıkları, benchmark medyası ve secret'lar repoya girmez.
