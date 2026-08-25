# GitHub'dan temiz kurulum

Bu rehber, başka bir bilgisayarda sıfırdan klonlanan depoyu önce **modelsiz arayüz
test akışında**, sonra istenirse gerçek video analiziyle çalıştırmak içindir.
Video, model ağırlığı, `.env`, çalışma sonuçları ve özel ağ adresleri Git'te
bulunmaz.

## 1. Ön koşullar

- Git
- `uv` (backend için Python 3.12 ortamını lock dosyasına göre yönetir)
- Bun
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

Bu adım `development` profilinde atlanabilir. Artifact yoksa sistem hareket
temelli bir temel modele düşer ve koşmaya devam eder.

### 3.2. Çıkarım ucu

1. `.env.example` dosyasını `.env` olarak kopyalayın.
2. `DORTGOZ_MOCK=0` yapın. `DORTGOZ_LLAMA_BASE_URL` ve `DORTGOZ_API_KEY`
   değerlerini kendi uç noktanıza göre doldurun. Uç OpenAI-uyumlu olmalıdır:
   yarışmanın sağladığı servis, yerel bir llama.cpp veya yerel bir vLLM örneği.
   Bulut servisi kullanılmaz.
3. Model takma adlarını ucunuzun `/v1/models` çıktısıyla eşleştirin. ⚠ Geçersiz
   bir ad hata vermez, sessizce varsayılana yönlenir.

Bu kip gerçek videoyu analiz eder. Dış aksiyonlar yine yalnız yerel taslak üretir.

### 3.3. Dağıtım profili

`DORTGOZ_DEPLOYMENT_PROFILE` iki değer alır.

- `development` — eksik yerel bileşen varsa uyarır ve düşerek devam eder.
- `competition-real` — D-FINE dağıtımını, SigLIP artifact'ini, prosedür
  manifestini ve uç kimliğini zorunlu sayar. Eksik bileşen varsa `GET /ready` 503
  döner ve analiz hiç başlamaz.

⚠ `competition-real`, D-FINE için model kaydından üretilmiş hash doğrulamalı bir
aktif dağıtım manifesti ister. Bu manifest `fetch_models.sh` ile gelmez. **Yeni
bir klonda `development` profilini kullanın.**

Hangi bileşenin hazır olmadığını `GET /ready` çıktısı bileşen bileşen söyler.

### 3.4. Başlatma

Aşağıdaki kontrol geçmeden gerçek modu açmayın:

```powershell
uv run --directory backend python ..\scripts\preflight.py --root .. --mode real --check-tools
.\scripts\dev.ps1 -Real
```

Linux/macOS'ta preflight komutunda `/` kullanın ve son komutu `./scripts/dev.sh real`
olarak çalıştırın. Preflight geçmediği sürece uygulama gerçek profili açmaz;
bu bilinçli bir güvenlik kapısıdır.

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
