# GitHub'dan temiz kurulum

Bu rehber, başka bir bilgisayarda sıfırdan klonlanan depoyu önce **modelsiz arayüz
test akışında**, sonra istenirse gerçek yerel VLM ile çalıştırmak içindir. Video, model
ağırlığı, `.env`, çalışma sonuçları ve özel ağ adresleri Git'te bulunmaz.

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

## 3. Gerçek yerel VLM

1. `.env.example` dosyasını `.env` olarak kopyalayın.
2. `.env` içindeki `DORTGOZ_MOCK=0`, model endpoint'i ve model kimliğini kendi
   bilgisayarınıza göre ayarlayın. Endpoint bulut servisi olamaz.
   Bu kip gerçek videoyu analiz eder. Dış aksiyonlar yine yalnız yerel taslak üretir.
3. Aşağıdaki kontrol geçmeden gerçek modu açmayın:

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
- `models/vlm/manifest.local.json`, `.env`, ağırlıklar, benchmark çıktıları ve
  `runs/` Git'e eklenmez.
- Kendi makinenizde geliştirmeden önce `backend` içinde `uv run pytest -q`,
  `uv run ruff check .`; `frontend` içinde `bun run build` çalıştırın.
- Air-gap makine veya Docker ile dağıtım için kökteki `docker-compose.yml`
  kullanılır.
