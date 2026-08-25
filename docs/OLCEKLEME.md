# Ölçekleme noktasında gerekli ihtiyaçlar

Bu belge, Dörtgöz'ün ölçülmüş kapasitesini ve daha büyük kuruluma geçerken
karşılaşılacak ihtiyaçları kaydeder. Rakamlar ölçüm sonuçlarıdır. Ölçüm düzeneği
ve tam tablolar [`BENCHMARK_REPORT.md`](BENCHMARK_REPORT.md) içindedir.

---

## 1. Bugünkü ölçülmüş kapasite

### 1.1. Kayıtlı video analizi

UCF-Crime resmî test bölmesi, 290 klip, 10,30 saat video, 1.380 pencere.

⚠ Aşağıda **iki farklı yapılandırma** vardır. Benimsenen üretim yolu r3'tür:
giriş genişliği 540 ve yerel algı GPU'da (§4.2).

| Ölçüt | r1 (720, algı CPU'da) | **r3 (540, algı GPU'da) — benimsenen** |
|---|---:|---:|
| Toplam iş süresi | 18.699 sn | **13.246 sn** |
| Uzak model (EVREN) | 15.016 sn | **12.452 sn** |
| Yerel algı (SigLIP + D-FINE) | 3.497 sn (%18,7) | **389 sn (%2,9)** |
| Tek akış eşdeğeri | 1,98× | **2,80× gerçek zaman** |
| Yakalama | 121/140 | 119/140 |
| Yanlış alarm | 22/150 | **18/150** |

r1'den r3'e toplam iş süresi **%29 azaldı.** İki bağımsız kazanç birleşti:
giriş genişliği 720→540 (uzak modelde %17,9) ve yerel algının GPU'ya taşınması
(yerel ayakta 3.497→389 sn).

**Dört eşzamanlı işte geçen gerçek süre.** r1 için ölçüldü: 84,7 dakika, yani
toplam akış hızı 7,30× gerçek zaman. r3 için aynı eşzamanlılık verimi varsayılırsa
**yaklaşık 60 dakika ve ~10,3×** beklenir. ⚠ Bu ikinci değer **türetilmiştir,
ölçülmemiştir**: r3 dört eşzamanlı iş altında ayrıca zamanlanmadı.

| Ölçüt (r1'de ölçüldü) | Değer |
|---|---:|
| Ortanca klip işleme | 36,4 sn |
| p95 klip işleme | 185,1 sn |
| Toplam model çağrısı | 1.818 |

### 1.2. Canlı akış

| Kamera sayısı | Düşürülen video | Gecikme (ortanca / en yüksek) |
|---:|---:|---|
| 25 | 900 sn | 152 sn / 330 sn |
| **9** | **0** | **≤ 18 sn** |

Dokuz kamera üretim listesidir. **Az kamera daha çok işlenmiş video saati
verir.** Kapasitenin üstüne çıkmak toplam verimi düşürür, çünkü düşürülen
segmentler hiç analiz edilmez.

⚠ Kamera listesi `config/live_feeds.json` dosyasındadır ve Git'e girmez. Dosya
yoksa sistem `config/live_feeds.example.json` örneğine düşer. **Örnek liste 25
kamera içerir** ve yukarıdaki düşürme davranışını yeniden üretir. Kendi
listenizi kapasitenize göre kısaltın.

### 1.3. Zaman nereye gidiyor

Benimsenen yapılandırmada (r3: 540, yerel algı GPU'da) 290 klip için toplam iş
süresi 13.246 saniyedir.

| Bileşen | Süre | Pay |
|---|---:|---:|
| Uzak model çağrıları (EVREN) | 12.452 sn | **%94,0** |
| D-FINE dedektör (yerel, GPU) | 294 sn | %2,2 |
| SigLIP-2 screening (yerel, GPU) | 95 sn | %0,7 |
| Diğerleri (klip kodlama, hareket profili, ısı) | ~405 sn | %3,1 |

**Yerel algı artık ölçülebilir bir maliyet değildir.** GPU taşımasından önce
yerel ayak toplamın %18,7'siydi; şimdi **%2,9**'dur.

**Sonuç: darboğaz artık tamamen uzak modeldir.** Yerel taraftan alınabilecek en
büyük kazanç toplamın %3'üdür. Bundan sonraki her hız işi EVREN çağrılarını
azaltmak veya kısaltmak zorundadır.

CPU döneminde (r1) `primary:vlm` tek başına toplamın %63,3'üydü; GPU taşımasından
sonra uzak çağrıların toplam payı %94'e çıktı.

Rol bazında ortalama çağrı süresi:

| Rol | Çağrı | Ortalama |
|---|---:|---:|
| `vlm` birincil | 1.329 | 8,90 sn |
| `llm-large` olay-geneli denetim | 164 | 10,16 sn |
| `llm-fast` sınıf hakemi | 120 | 5,29 sn |
| `llm-large` tırmandırma | 91 | 4,76 sn |
| `llm-large` ikinci görüş | 114 | 4,04 sn |

### 1.4. Token yükü

| Ölçüt | Değer |
|---|---:|
| Prompt token (toplam) | 17.616.342 |
| Tamamlama token (toplam) | 429.845 |
| Ortalama prompt | 11.976 token/çağrı |
| Ortalama tamamlama | 292 token/çağrı |
| Etkin tamamlama hızı | ~32 token/sn |

Prompt token sayısı tamamlama token sayısının kırk katıdır. Maliyet modeli
kurarken bu oran belirleyicidir. Etkin hız video kodlama ve paylaşımlı kuyruk
süresini içerir; saf üretim hızı değildir.

---

## 2. Ölçeklemeyi engelleyen üç sınır

### 2.1. Uzak çıkarım kuyruğu paylaşımlıdır

`max_inflight` değeri 4'ten 8'e çıkarıldığında koşu **%20 yavaşladı**. Sebep
istemci tarafında değildir: uç, tüm takımlar arasında paylaşımlı bir FIFO kuyruk
kullanır. Daha çok eşzamanlı istek kuyrukta daha uzun beklemek demektir.

**Sonuç.** Eşzamanlılığı artırmak bu mimaride kapasite artırmaz. Kapasite
artışı ya ayrılmış (dedicated) uç ya da daha az çağrı ister.

**İhtiyaç.** Çok kuruluma geçilecekse her kurulum kendi çıkarım kotasına sahip
olmalıdır. Ortak kuyruk üstünde kurulum sayısı doğrusal ölçeklenmez.

### 2.2. Pencere başına maliyet olay yoğunluğuna bağlıdır

Olaysız pencere kısa çıktı üretir ve ucuzdur. Olaylı pencere uzun çıktı üretir.
İki uç ölçüldü:

| Kayıt tipi | Maliyet | Gerçek zaman katı |
|---|---:|---:|
| UCF-Crime (%100 olaylı, en kötü durum) | 27 GPU-sn/dk | 2,2× |
| Gerçekçi gözetim kaydı (%84 ölü görüntü) | 4,8 GPU-sn/dk | 12,2× |

**Sonuç.** Kapasite planı sahnenin olay yoğunluğuna göre yapılmalıdır. Sakin bir
sahada bir sunucu çok daha fazla kamera taşır.

### 2.3. Yerel algı tek makinede yarışır

SigLIP ve D-FINE aynı makinede çalışır. Eşzamanlı yerel çıkarım
`DORTGOZ_LOCAL_INFERENCE_LIMIT` ile sınırlıdır (varsayılan 2). Bu sınır
kaldırılırsa CPU doygunluğu uzak çağrıların hazırlanmasını da geciktirir.

---

## 3. Depolama

| Kalem | Ölçü |
|---|---|
| Canlı segment (ortanca) | ~1,7 MB |
| Dokuz kamera segment üretimi | ~480 segment/saat |
| Kaba segment akışı | ~0,8 GB/saat |

Segmentler saklanmaz. Tampon yalnız üç segment (yaklaşık 90 saniye) tutar. Olay
açılınca kanıt klibi ayrıca kesilir ve saklanır. Bu karar bilinçlidir: tüm
segmentleri saklamak saatler içinde gigabaytlara çıkar, kanıt klibi ise yalnız
olay süresi kadardır.

**İhtiyaç.** Uzun süreli saklama isteniyorsa ayrı bir nesne deposu ve saklama
politikası gerekir. Bugünkü tasarım kanıt saklar, ham akış saklamaz.

Olay deposu SQLite'tır. Tek yazıcı süreç varsayımıyla çalışır. Çok sunuculu
kuruluma geçilirse bu bileşen değişmelidir.

---

## 4. Donanım notları

### 4.1. Yerel model yolu (geçmiş mimari)

Yerel VLM artık üretim yolu değildir. Aşağıdaki değerler yerel bir kuruluma
dönülürse geçerlidir.

- 16 GB kartta gerçek sınır GTT taşma uçurumudur: ~16.050-16.100 MiB toplam.
- Aşımda çökme olmaz, prompt işleme %79 düşer.
- Pratik bütçe ~15.400 MiB'dir.

### 4.2. Algı katmanı GPU'ya taşındı (2026-08-25)

Yerel algı katmanı MIGraphX 2.15 (ROCm 7.2.3, gfx1201, MIT lisans) ile GPU'ya
taşındı. ONNX Runtime'da ROCm sağlayıcı wheel'i yoktur; çözüm `libmigraphx_c.so`
üzerine bir ctypes sarıcıdır (`pipeline/migraphx_ep.py`). EVREN çağrıları
değişmedi.

| Aşama | CPU | GPU | Kazanç | Sayısal denklik |
|---|---:|---:|---:|---|
| SigLIP fp16, batch 16 | 1.936 ms | **9,7 ms** | **202×** | kosinüs 0,999988 |
| D-FINE fp32, batch 4 | 172 ms/kare | **12,3 ms/kare** | **14×** | 0,40 eşiğinde tespitler birebir |

**Boru hattı içinde ölçülen gerçek kazanç** (31 klip × 3 tekrar):

| Bileşen | CPU | GPU | Kazanç |
|---|---:|---:|---:|
| SigLIP | 142,0 sn | **16,1 sn** | **8,8×** |
| D-FINE | 69,3 sn | **21,2 sn** | **3,3×** |

Tam bölmede (290 klip) kazanç daha da büyüktür, çünkü batch-16 fp16 daha uzun
korpusta daha iyi amorti olur:

| Bileşen | r1 (CPU) | r3 (GPU) | Kazanç |
|---|---:|---:|---:|
| SigLIP | 2.613,5 sn | **95,2 sn** | **27,4×** |
| D-FINE | 883,5 sn | **293,9 sn** | **3,0×** |
| Yerel toplam | 3.497,0 sn | **389,1 sn** | **9,0×** |

**Bu değerler benimsenen üretim yapılandırmasının içindedir.** §1'deki r3
sütunu GPU yolunu zaten kullanır.

**⚠ Yalıtılmış çekirdek hızından boru hattı kazancı çıkarmayın.** 202× mikro
ölçüm boru hattında 8,8× olur, çünkü sayacın içinde ffmpeg kare çıkarma da vardır.
**Bu taşımadan sonra yerel algının tabanı ffmpeg'dir, model değildir.**

**D-FINE fp16 reddedildi.** Aynı karede 0,40 eşiğinde 11 yerine 12 tespit üretti.

**Kalite bandı.** CPU üç tekrar 22/22/22 yakalama. GPU üç tekrar 21/22/22. Her iki
kolda 0/5 yanlış alarm ve aynı sabit kaçırma çekirdeği. Fark, belgelenmiş EVREN
örnekleme varyansı (±3 klip) içindedir. Bantlar örtüşür; özdeşlik iddia edilmez.

**Kurulum.** Derleme tek seferliktir (D-FINE ~5,6 dk, SigLIP ~1,5 dk) ve `.mxr`
olarak diske yazılır. `scripts/build_migraphx.sh` yeniden üretir. Artifact'lar
`~/.cache/dortgoz/migraphx/` altındadır (94-416 MB, repo dışı).

**Bütünlük.** Manifest kaynak ONNX'in SHA-256 değerini tutar. Kaynak değişirse GPU
yolu kapanır ve sistem CPU'ya döner. `DORTGOZ_MIGRAPHX_DIR` boşsa GPU yolu
kapalıdır, böylece GPU'suz makineler etkilenmez.

**NVIDIA tarafı (ayrı yol).** Dizüstünde `DORTGOZ_ONNX_PROVIDERS=CUDAExecutionProvider`
kullanılabilir. Bu yolun uçtan uca kazancı yalnız %7-10'dur ve MIGraphX yolunun
yerini tutmaz. GPU sağlayıcısı açıkken backend yaklaşık 2,3 GB VRAM tutar.

### 4.3. İstemci tarafı

Algı katmanı CPU/ONNX üzerinde çalışır. Operatör konsolunu çalıştıran makinede
GPU gerekmez. Bu, ekip üyelerinin GPU'suz dizüstülerde tam sistemi koşabilmesini
sağlar.

---

## 5. Ölçeklemek için gerekenler

Aşağıdaki liste, bugünkü tek sunuculu kurulumdan daha büyük bir kuruluma
geçmek için gereken işleri önem sırasıyla verir.

1. **Ayrılmış çıkarım kapasitesi.** Paylaşımlı kuyruk üstünde eşzamanlılık
   artırmak ters teper. Her kurulum kendi kotasına sahip olmalıdır.
2. **Olay deposunun değişmesi.** SQLite tek yazıcı varsayar. Çok sunuculu
   kurulumda PostgreSQL veya eşdeğeri gerekir. Depo katmanı protokol arkasında
   olduğu için değişim yalıtılmıştır.
3. **Nöbet kuyruğunun kalıcı olması.** Bugün kuyruk bellektedir. Backend yeniden
   başlayınca karar verilmemiş kayıtlar silinir. Karara bağlanmış kayıtlar
   canonical deftere yazıldığı için korunur.
4. **Kamera başına kapasite planı.** Sahnenin olay yoğunluğu maliyeti dört kattan
   fazla değiştirir. Kamera sayısı ölçülerek belirlenmelidir, varsayılarak değil.
5. **Saklama politikası.** Kanıt klibi saklanır, ham segment saklanmaz. Daha uzun
   saklama isteniyorsa ayrı depo ve silme politikası gerekir.
6. **Kamera başına kalibrasyon verisi.** Güven kalibrasyonu için kamera başına
   yaklaşık 20 etiket gerekir. Bu veri bugün yalnız toplam düzeyde vardır.

---

## 6. Ölçülmemiş alanlar

Dürüstlük için: aşağıdaki konular ölçülmemiştir ve rakam verilemez.

- Yüzden fazla kameralı kurulum hiç denenmedi.
- Çok sunuculu koordinasyon hiç denenmedi.
- Uzun süreli (günlerce) kesintisiz canlı koşu hiç denenmedi. En uzun ölçülen
  canlı koşu saatler mertebesindedir.
- Ağ kesintisi altında uzak uç davranışı ölçülmedi.
