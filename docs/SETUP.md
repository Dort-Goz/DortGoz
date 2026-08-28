# GitHub'dan temiz kurulum

Bu rehber, başka bir bilgisayarda sıfırdan klonlanan depoyu önce **modelsiz arayüz
test akışında**, sonra istenirse gerçek video analiziyle çalıştırmak içindir.
Video, model ağırlığı, `.env`, çalışma sonuçları ve özel ağ adresleri Git'te
bulunmaz.

## 1. Ön koşullar

- Git
- `uv` (backend için Python 3.12 ortamını lock dosyasına göre yönetir)
- Bun
- Python 3 ve `curl` (örnek klip ve model indirme betikleri için)
- Gerçek video analizi için ayrıca lisansı onaylanmış `ffmpeg` ve `ffprobe`

Arayüz test akışı model/GPU/FFmpeg istemez ve video analizi yapmaz. İlk kez bağımlılık indirmek için internet gerekir;
air-gap/Docker dağıtımı için kökteki `docker-compose.yml` kullanılabilir.

## 2. Arayüz test akışı ile ilk açılış

```powershell
git clone https://github.com/Dort-Goz/DortGoz.git
cd DortGoz
uv run --directory backend python ..\scripts\preflight.py --root .. --mode mock --check-tools
.\scripts\dev.ps1
```

Linux/macOS:

```bash
git clone https://github.com/Dort-Goz/DortGoz.git
cd DortGoz
uv run --directory backend python ../scripts/preflight.py --root .. --mode mock --check-tools
./scripts/dev.sh
```

Başlatıcı kilitli bağımlılıkları kurar ve arayüz test akışında API ile konsolu açar. Konsol
`http://localhost:5173`, API `http://localhost:8000/health`, readiness ise
`http://localhost:8000/ready` adresindedir. Uygulamayı kapatmak için terminalde
`Ctrl+C` kullanın.

## 3. Gerçek video analizi

### 3.1. Örnek video ve yerel algı modelleri

Depo video klibi ve model ağırlığı taşımaz. Gerçek analizden önce ikisini de
indirin.

```bash
python scripts/fetch_ucf_samples.py    # örnek klipler -> media/
./scripts/fetch_models.sh              # D-FINE dedektörü (41 MB)
```

SigLIP-2 screening artifact'i (~355 MB) tek seferlik bir aktarım ister. Aktarım
`torch` ve `transformers` gerektirir; bu paketler backend bağımlılıklarında
değildir. Ayrı bir sanal ortam kullanın:

```bash
python -m venv /tmp/siglip && /tmp/siglip/bin/pip install torch transformers onnxruntime numpy
/tmp/siglip/bin/python scripts/export_siglip.py
```

Aktarım `models/semantic/local/` altına yazar ve depodaki
`models/semantic/semantic-v1.json` ile `manifest.json` dosyalarını yeni
hash'lerle günceller; bu iki dosyanın değişmiş görünmesi beklenen durumdur.

Bu adım `development` profilinde atlanabilir. Artifact yoksa sistem hareket
temelli bir temel modele düşer ve koşmaya devam eder.

### 3.2. Çıkarım ucu

1. `.env.example` dosyasını `.env` olarak kopyalayın.
2. `DORTGOZ_MOCK=0` yapın. `DORTGOZ_LLAMA_BASE_URL` ve `DORTGOZ_API_KEY`
   değerlerini kendi uç noktanıza göre doldurun. Uç OpenAI-uyumlu olmalıdır:
   yarışmanın sağladığı servis, yerel bir llama.cpp veya yerel bir vLLM örneği.
   Bulut servisi kullanılmaz. `DORTGOZ_API_KEY` boş bırakılamaz; anahtar
   istemeyen yerel bir uç için herhangi bir değer yazın. Yerel uçlar
   (`http://127.0.0.1`) yalnız `development` profilinde kabul edilir (§3.3).
3. Model takma adlarını ucunuzun `/v1/models` çıktısıyla eşleştirin. ⚠ Geçersiz
   bir ad hata vermez, sessizce varsayılana yönlenir.

Bu kip gerçek videoyu analiz eder. Dış aksiyonlar yine yalnız yerel taslak üretir.

### 3.3. Dağıtım profili

`DORTGOZ_DEPLOYMENT_PROFILE` iki değer alır.

- `development` — eksik yerel bileşen varsa uyarır ve düşerek devam eder. Her
  OpenAI-uyumlu uçla (yerel dahil) çalışır; Qdrant değişkenleri boş kalabilir.
- `competition-real` — D-FINE dağıtımını, SigLIP artifact'ini, prosedür
  manifestini, EVREN uç kimliğini ve EVREN Qdrant kimliğini (`DORTGOZ_QDRANT_URL`,
  `_PREFIX`, `_API_KEY`) zorunlu sayar. Uç yerel olmayan bir `https://` adres
  olmalı; model takma adları tam olarak `llm-fast`, `vlm`, `llm-large`, `router`,
  `guard`, `bge-m3-embed` olmalı ve hepsi `/v1/models` listesinde bulunmalıdır.
  Eksik bileşen varsa `GET /ready` 503 döner ve analiz hiç başlamaz.

⚠ `competition-real`, D-FINE için model kaydından üretilmiş hash doğrulamalı bir
aktif dağıtım manifesti ister. Bu manifest `fetch_models.sh` ile gelmez. **Yeni
bir klonda `development` profilini kullanın.**

Hangi bileşenin hazır olmadığını `GET /ready` çıktısı bileşen bileşen söyler.

### 3.4. Yerel algı donanımı (isteğe bağlı GPU)

SigLIP-2 ve D-FINE varsayılan olarak CPU'da çalışır. Bu her makinede çalışır ve
hiçbir ayar istemez. GPU'nuz varsa açabilirsiniz.

#### Ayar değişkenleri

| Değişken | Varsayılan | Anlam |
|---|---|---|
| `DORTGOZ_ONNX_DEVICE` | `cpu` | `cpu`, `auto` veya `gpu`/`cuda`. `auto` GPU yoksa sessizce CPU'ya düşer; `gpu` aynı şekilde düşer ama uyarı basar |
| `DORTGOZ_ONNX_PROVIDERS` | boş | Sağlayıcı listesini elle verir, `DORTGOZ_ONNX_DEVICE` değerini ezer. Örnek: `CUDAExecutionProvider` |
| `DORTGOZ_MIGRAPHX_DIR` | boş | Derlenmiş MIGraphX artifact dizini. Boşsa AMD GPU yolu kapalıdır |
| `DORTGOZ_ONNX_INTRA_THREADS` | `4` | CPU'da kalan iş için iş parçacığı sayısı |
| `DORTGOZ_LOCAL_INFERENCE_LIMIT` | `2` | Eşzamanlı yerel çıkarım sayısı (1-8) |

İki GPU yolu ayrıdır ve birbirini gerektirmez.

#### NVIDIA (CUDA)

ONNX Runtime'ın GPU sürümü ve CUDA kitaplıkları gerekir:

```bash
cd backend
uv pip install onnxruntime-gpu nvidia-cudnn-cu12 nvidia-cublas-cu12 \
  nvidia-cufft-cu12 nvidia-curand-cu12
```

`.env` içine ekleyin:

```ini
DORTGOZ_ONNX_DEVICE=auto
```

⚠ **`uv sync --locked` bu kurulumu geri alır.** Kilit dosyası CPU sürümünü
tutar; `onnxruntime-gpu` yerel bir geçersiz kılmadır. `./scripts/dev.sh real`
bağımlılıkları senkronladığı için sonrasında tekrar kurun. İki paket çakıştığı ve
AMD makineleri bozduğu için `pyproject.toml`'a konmamıştır.

Bu yolun uçtan uca kazancı %7-10'dur.

#### AMD (ROCm / MIGraphX)

ROCm ve `/opt/rocm/bin/migraphx-driver` kurulu olmalıdır. Modeller bir kez
derlenir:

```bash
./scripts/build_migraphx.sh
```

Betik sabit şekilli ONNX kopyaları üretir, D-FINE'ı fp32 ve SigLIP'i fp16 olarak
derler, `.mxr` dosyalarını ve bir manifest yazar. Derleme süresi D-FINE için
~5,6 dakika, SigLIP için ~1,5 dakikadır. Çıktı varsayılan olarak
`~/.cache/dortgoz/migraphx/` altına gider (94-416 MB, repo dışı).

Betik biterken eklenecek satırı ekrana basar:

```ini
DORTGOZ_MIGRAPHX_DIR=~/.cache/dortgoz/migraphx
```

Batch boyutları derleme anında sabitlenir. Değiştirmek isterseniz betiği
`DORTGOZ_DFINE_BATCH` (varsayılan 4) ve `DORTGOZ_SIGLIP_BATCH` (varsayılan 16)
ile çalıştırın. Sürücü yolu `MIGRAPHX_DRIVER` ile değiştirilebilir.

Bu yolun kazancı tam test bölmesinde ölçüldü: yerel ayak **3.497 sn → 389 sn**
(SigLIP 27,4×, D-FINE 3,0×).

#### Doğrulama

Backend günlüğünde şu satırları arayın:

- `MIGraphX siglip etkin: siglip.mxr batch=16` — GPU yolu açık.
- `MIGraphX dfine kullanılmıyor, CPU sürüyor: ...` — GPU yolu kapalı; satır
  sebebi yazar.
- `onnx sağlayıcı yok, atlandı: CUDAExecutionProvider` — CUDA kurulumu eksik.

⚠ **Bütünlük kapısı.** MIGraphX manifesti kaynak ONNX dosyasının SHA-256 değerini
tutar. Kaynak model değişirse derlenmiş artifact reddedilir ve sistem CPU'ya
döner. Model güncellendiğinde `build_migraphx.sh` yeniden çalıştırılmalıdır.

GPU yolu her iki durumda da **kaliteyi değiştirmez**: SigLIP kosinüs eşliği
0,999988, D-FINE 0,40 eşiğinde tespitler birebir aynıdır. D-FINE fp16 bilinçli
olarak kullanılmaz çünkü aynı eşikte tespit sayısını değiştirir.

### 3.5. Başlatma

`development` profilinde backend ve konsolu iki terminalde elle açın; `.env`
backend tarafından okunur (Windows PowerShell'de `&&` yerine komutları ayrı
satırda çalıştırın):

```bash
cd backend && uv run uvicorn dortgoz.main:app --port 8000
cd frontend && bun run dev
```

`competition-real` profilinde aşağıdaki kontrol geçmeden gerçek modu açmayın:

```powershell
uv run --directory backend python ..\scripts\preflight.py --root .. --mode real --check-tools
.\scripts\dev.ps1 -Real
```

Linux/macOS'ta preflight komutunda `/` kullanın ve son komutu `./scripts/dev.sh real`
olarak çalıştırın. Preflight `.env` içinde `competition-real` profilini ve §3.3'teki
her koşulu ister; geçmediği sürece uygulama gerçek profili açmaz. Bu bilinçli bir
güvenlik kapısıdır. `dev.sh real` ve `dev.ps1 -Real` profili her zaman
`competition-real` olarak zorlar.

`dev.sh` ve `dev.ps1` yalnız `127.0.0.1` adresine bağlanır. Başka bir
bilgisayardan (örneğin özel ağ/VPN üzerinden) erişmek için
`.\scripts\dev.ps1 -Real -Remote` kullanın. `-Remote` tüm ağ arabirimlerini açar
ve kimlik doğrulama YOKTUR; yalnız güvenilen bir ağda kullanın.

⚠ `DORTGOZ_VLM_MANIFEST_PATH` artık kodda okunmuyor. Eski `.env` dosyalarında
kalmışsa etkisizdir.

## 4. Paylaşım kuralları

- `media/` altına koyduğunuz videolar yalnız o bilgisayarda kalır.
- `.env`, model ağırlıkları, benchmark çıktıları ve `runs/` Git'e eklenmez.
- Kendi makinenizde geliştirmeden önce `backend` içinde `uv run pytest -q`,
  `uv run ruff check .`; `frontend` içinde `bun run build` çalıştırın.
- Air-gap makine veya Docker ile dağıtım için kökteki `docker-compose.yml`
  kullanılır.
