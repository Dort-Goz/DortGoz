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

## Kurulum ve ilk çalıştırma

### Ön koşullar

| Araç | Niçin gerekir |
|---|---|
| Git | depoyu klonlamak için |
| [`uv`](https://docs.astral.sh/uv/) | backend Python 3.12 ortamını lock dosyasına göre kurar |
| [Bun](https://bun.sh/) | konsolu derler ve çalıştırır |
| `ffmpeg` + `ffprobe` | **yalnız gerçek video analizi için** |

Arayüz test akışı GPU, model ağırlığı ve ffmpeg istemez.

### Adım 1 — arayüz test akışı (kurulum doğru mu?)

```bash
git clone https://github.com/Dort-Goz/DortGoz.git
cd DortGoz
./scripts/dev.sh                       # Windows: .\scripts\dev.ps1
```

Konsol `http://localhost:5173` adresinde açılır. “Başlat” düğmesi kayıtlı bir
örnek olay akışını oynatır. **Bu kip video analizi yapmaz**; yalnız arayüz olay
sözleşmesini gösterir. Burası çalışıyorsa kurulum sağlandır.

Durdurmak için terminalde `Ctrl+C` kullanın.

### Adım 2 — örnek video indirin

Depoda video klibi yoktur. Gerçek analiz için `media/` klasörü dolu olmalıdır.

```bash
python scripts/fetch_ucf_samples.py    # sabit örnek liste, herkese açık kaynak
```

Kendi videolarınızı da `media/` altına kopyalayabilir veya konsoldaki
“Video yükle” düğmesini kullanabilirsiniz.

### Adım 3 — yerel algı modellerini indirin

Model ağırlıkları depoya girmez. İki yerel bileşen vardır. İkisi de CPU'da
çalışır; isteğe bağlı GPU yolu için [`docs/OLCEKLEME.md`](docs/OLCEKLEME.md)
§4.2'ye bakın.

```bash
./scripts/fetch_models.sh              # D-FINE dedektörü (41 MB)
```

Betik dosyayı `~/.cache/dortgoz/dfine/` altına koyar. Backend bu yolu kendisi
bulur; ek ayar gerekmez.

SigLIP-2 screening artifact'i (~355 MB) tek seferlik bir aktarım ister ve
`torch` + `transformers` gerektirir. Bu paketler backend bağımlılıklarında
yoktur; aktarımı ayrı bir sanal ortamda yapın:

```bash
python -m venv /tmp/siglip && /tmp/siglip/bin/pip install torch transformers onnxruntime numpy
/tmp/siglip/bin/python scripts/export_siglip.py
```

**Bu adım atlanabilir.** `development` profilinde screening artifact'i yoksa sistem
hareket temelli bir temel modele düşer, izleme paneline `anlamsal screening
düştü` satırını yazar ve koşmaya devam eder. Yalnız kalite düşer.

#### İsteğe bağlı: yerel algıyı GPU'da çalıştırın

SigLIP ve D-FINE varsayılan olarak CPU'da çalışır. GPU'nuz varsa açın — tam test
bölmesinde yerel ayak **3.497 sn'den 389 sn'ye** indi.

**NVIDIA (CUDA).** Önce GPU çalışma zamanını kurun:

```bash
cd backend && uv pip install onnxruntime-gpu nvidia-cudnn-cu12 nvidia-cublas-cu12 \
  nvidia-cufft-cu12 nvidia-curand-cu12
```

Sonra `.env` içine yazın:

```ini
DORTGOZ_ONNX_DEVICE=auto     # varsa GPU, yoksa sessizce CPU
# DORTGOZ_ONNX_DEVICE=gpu    # GPU iste; yoksa uyarı basıp CPU'ya döner
```

⚠ `uv sync --locked` bu kurulumu **geri alır** (kilit dosyasında CPU sürümü
vardır). `./scripts/dev.sh real` sonrası tekrar kurun.

**AMD (ROCm / MIGraphX).** ROCm ve `migraphx-driver` kurulu olmalıdır. Derleme tek
seferliktir (D-FINE ~5,6 dk, SigLIP ~1,5 dk):

```bash
./scripts/build_migraphx.sh
```

Betik `~/.cache/dortgoz/migraphx/` altına `.mxr` dosyalarını ve bir manifest yazar,
sonra `.env` satırını ekrana basar:

```ini
DORTGOZ_MIGRAPHX_DIR=~/.cache/dortgoz/migraphx
```

**Doğrulama.** Backend günlüğünde `MIGraphX siglip etkin` ve `MIGraphX dfine
etkin` satırlarını arayın. `MIGraphX ... kullanılmıyor, CPU sürüyor` görürseniz
GPU yolu kapalıdır; satır sebebi yazar.

Seçenekler ve ölçümler: [`docs/SETUP.md`](docs/SETUP.md) §3.4 ve
[`docs/OLCEKLEME.md`](docs/OLCEKLEME.md) §4.2.

### Adım 4 — çıkarım ucunu tanımlayın

```bash
cp .env.example .env
```

`.env` içinde en az şunları doldurun:

```ini
DORTGOZ_MOCK=0
DORTGOZ_LLAMA_BASE_URL=https://<openai-uyumlu-ucunuz>/v1
DORTGOZ_API_KEY=<anahtarınız>
```

Model takma adları (`DORTGOZ_VIDEO_MODEL`, `DORTGOZ_MAIN_MODEL`, …) ucunuzun
`/v1/models` çıktısındaki adlarla eşleşmelidir. **Uyarı:** geçersiz bir ad hata
vermez, sessizce varsayılana yönlenir.

Uç herhangi bir OpenAI-uyumlu servis olabilir: yarışmanın sağladığı EVREN, yerel
bir llama.cpp veya vLLM örneği. Bulut servisi kullanılmaz.

### Adım 5 — gerçek analizi başlatın

```bash
cd backend && uv run python ../scripts/preflight.py --root .. --mode real --check-tools && cd ..
./scripts/dev.sh real                  # Windows: .\scripts\dev.ps1 -Real
```

Preflight geçmeden uygulama gerçek profili açmaz. Bu bilinçli bir güvenlik
kapısıdır.

Konsolda üst çubuktan bir kaynak seçin ve “Başlat”ı tıklayın. API
`http://localhost:8000`, liveness `/health`, readiness `/ready` yolundadır.

### İki dağıtım profili

| Profil | Ne yapar | Ne zaman kullanılır |
|---|---|---|
| `development` (varsayılan) | Eksik yerel bileşen varsa uyarıp düşer, koşmaya devam eder | Geliştirme, deneme, **yeni klon** |
| `competition-real` | D-FINE dağıtımını, SigLIP artifact'ini, prosedür manifestini ve uç kimliğini **zorunlu** sayar; eksikse analiz hiç başlamaz | Değerlendirme koşusu |

Profil `.env` içinde `DORTGOZ_DEPLOYMENT_PROFILE` ile seçilir. Hangi bileşenin
hazır olmadığını `GET /ready` çıktısı bileşen bileşen söyler.

⚠ `competition-real`, D-FINE için yalnız ONNX dosyasını değil, model kaydından
üretilmiş **hash doğrulamalı bir aktif dağıtım manifesti** ister
(`models/dfine/local/active_manifest.json`). Bu manifest `fetch_models.sh` ile
gelmez; dağıtım hattı (`scripts/dfine_feedback_training.py`) tarafından bir kez
üretilir. **Yeni bir klonda `development` profilini kullanın.**

Adım adım temiz kurulum ve uzaktan erişim: [`docs/SETUP.md`](docs/SETUP.md).

### Sık karşılaşılan durumlar

| Belirti | Sebep ve çözüm |
|---|---|
| Kaynak listesi boş | `media/` boş. Adım 2'yi çalıştırın |
| `competition-real analiz kapısı kapalı` | Zorunlu bir yerel bileşen eksik. `GET /ready` hangisi olduğunu söyler |
| İzlemede `dedektör kapatıldı` | D-FINE ONNX bulunamadı. Adım 3'ü çalıştırın veya `DORTGOZ_DFINE_ONNX` yolunu düzeltin |
| İzlemede `anlamsal screening düştü` | SigLIP artifact'i yok. Sistem temel modele düştü, koşu geçerlidir |
| Konsol tamamen boş | Vite proxy `127.0.0.1` kullanmalıdır; `localhost` bazı Node sürümlerinde IPv6'ya çözülür |

## Doğrulama

```bash
cd backend && uv run pytest -q && uv run ruff check .
cd ../frontend && bun run build
cd .. && python scripts/verify_offline.py
```

## Ölçüm sonuçları

UCF-Crime resmî test bölmesi (290 klip, 10,30 saat) üzerinde dört tam üretim
koşusu yapıldı. **Tek bir koşunun başlığı tek başına kullanılmaz; bant verilir:**
yakalama **%85,0-87,9** (119-123/140), eyleme konu yanlış alarm **%12,0-18,7**
(18-28/150), kategori 72-75.

Benimsenen yapılandırmada (giriş genişliği 540, yerel algı GPU'da) ölçülen hız:
10,30 saatlik video dört eşzamanlı işte **54,8 dakikada** işlendi, yani
**11,29× gerçek zaman**. 290 klibin hiçbirinde terminal hata olmadı.

Bu sayılar geliştirme kıyasıdır; genelleme kanıtı olarak, geliştirmede hiç
kullanılmamış 80 kliplik kör holdout'ta %90,0 [%76,9–%96,0] yakalama
ölçülmüştür. Tam rapor, sınırlar ve tekrar üretim komutları:
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

## Şartname dışı eklenen yetenekler

Şartnamenin istediği çekirdek — video girdisi, olay tespiti, Türkçe özet, JSON
çıktı ve ajan araçları — dışında aşağıdaki yetenekler eklendi. Her biri ölçüm
veya operatör ihtiyacı sonucunda doğdu.

**Operatör iş akışı**

- **Olay inceleme merkezi.** İnsan incelemesi isteyen olaylar öncelik puanıyla
  sıralanır. Operatör doğru sınıfı, riski ve olay sınırlarını düzeltir. Karar
  kalıcı deftere yazılır.
- **Çok kamera duvarı.** Birden çok akış eşzamanlı çözümlenir; şerit her akışın
  hızını, ilerlemesini ve en kötü riskini gösterir.
- **Canlı kamera modu.** RTSP/HLS akışları segmentlenir ve aynı hattan geçer.
- **Analiz paketi dışa/içe aktarımı.** Bir koşu akış, özet, video ve kanıt
  kareleriyle tek `.zip` olur. Paket başka bir kuruluma aktarılır ve sohbet ajanı
  paket üzerinde tam yetenekle çalışır.

**Güvenlik ve doğrulanabilirlik**

- **Kanıt doğrulayıcı.** Model her iddiasını bir kare kimliğine bağlar. Kimlik,
  zaman ve SHA-256 doğrulanmazsa iddia düşer (fail-closed).
- **İnsan kapılı dış aksiyon taslakları.** Ajan emniyet veya sağlık bildirimi
  hazırlayabilir. Taslak yalnız operatör onayıyla üretilir ve **hiçbir zaman dış
  kuruma gönderilmez**.
- **Kaynaklı prosedür getirme.** Öneri metni belge kimliği, bölüm, sürüm ve
  içerik hash'i ile birlikte gelir.
- **Hazırlık denetimi.** `GET /ready` her zorunlu bileşeni tek tek raporlar.
  Yarışma profilinde eksik bileşen analizi başlatmaz.

**Kalite ve öğrenme**

- **İki aşamalı yerel eleme.** SigLIP-2 semantik screening aday aralıklarını
  seçer; D-FINE kişi tespiti eleme dışı kalan pencereleri geri açar.
- **Olay-geneli ikinci geçiş.** Olay kapanınca tüm aralık tek çağrıda yeniden
  okunur ve anlatı bütünlenir.
- **Öğrenme merkezi.** İnsan onaylı geliştirme rotaları, öncelik kuyruğu ve
  gölge kayma gözcüsü. Otomatik eğitim ve canlı modele otomatik terfi kapalıdır.
- **Güven kalibrasyonu.** Platt ölçeklemesi saf Python ile yazıldı. Ölçüm: Brier
  0,1372 → 0,0853.
- **Deney paneli.** Model ve istem koşu başına değiştirilir; etkin yapılandırma
  koşu kaydına yazılır, böylece hangi istemin hangi çıktıyı ürettiği izlenir.

## Dokümantasyon

Şartname §6 “Teslim edilmesi gerekenler” karşılıkları:

| İstenen | Nerede |
|---|---|
| Sistem mimarisi özeti ve diyagramı | [`docs/MIMARI.md`](docs/MIMARI.md) |
| Kullanılan agentic framework ve LLM'ler | [`docs/MIMARI.md`](docs/MIMARI.md) §Model rolleri |
| İmplemente edilen senaryolar ve mock fonksiyonlar | [`docs/MIMARI.md`](docs/MIMARI.md) §Ajan bileşenleri |
| Adım adım kurulum ve çalıştırma | Bu dosyada §Kurulum, [`docs/SETUP.md`](docs/SETUP.md) |
| Karşılaşılan zorluklar ve çözümler | [`docs/ZORLUKLAR.md`](docs/ZORLUKLAR.md) |
| Eklenen ek özellikler | Bu dosyada §Şartname dışı eklenen yetenekler |
| Ölçümleme sonuçları | [`docs/BENCHMARK_REPORT.md`](docs/BENCHMARK_REPORT.md) |
| Ölçekleme noktasında gerekli ihtiyaçlar | [`docs/OLCEKLEME.md`](docs/OLCEKLEME.md) |

Diğer belgeler:

- [Mimari karar kaydı](DORTGOZ_ARCHITECTURE_BASELINE.md)
- [Üçüncü taraf lisans bildirimleri](THIRD_PARTY_NOTICES.md)

## Lisans ve veri politikası

Kaynak kod Apache-2.0'dır. Bağımlılıklar için OSI onaylı açık kaynak lisanslar
kabul edilir; AGPL ve SSPL yasaktır ve release kapısı (`scripts/sbom.py
--check`) güçlü copyleft'i ayrıca engeller. Ham veri kümesi klipleri, model
ağırlıkları, benchmark medyası ve secret'lar repoya girmez.
