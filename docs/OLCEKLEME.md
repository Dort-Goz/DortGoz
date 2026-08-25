# Ölçekleme noktasında gerekli ihtiyaçlar

Bu belge, Dörtgöz'ün ölçülmüş kapasitesini ve daha büyük kuruluma geçerken
karşılaşılacak ihtiyaçları kaydeder. Rakamlar ölçüm sonuçlarıdır. Ölçüm düzeneği
ve tam tablolar [`BENCHMARK_REPORT.md`](BENCHMARK_REPORT.md) içindedir.

---

## 1. Bugünkü ölçülmüş kapasite

### 1.1. Kayıtlı video analizi

UCF-Crime resmî test bölmesi, 290 klip, 10,30 saat video, 1.380 pencere:

| Ölçüt | Değer |
|---|---:|
| Duvar süresi (dört eşzamanlı iş) | 84,7 dk |
| Toplam akış hızı | **7,30× gerçek zaman** |
| Tek akış eşdeğeri | 1,98× gerçek zaman |
| Ortanca klip işleme | 36,4 sn |
| p95 klip işleme | 185,1 sn |
| Toplam model çağrısı | 1.818 |

Bir sunucu, dört eşzamanlı iş ile **saatte yaklaşık 7,3 saat video** işler.

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

Üretim koşusunda toplam model süresi 15.016 saniyedir.

| Bileşen | Süre | Pay |
|---|---:|---:|
| Uzak model çağrıları (EVREN) | 15.016 sn | %81 |
| SigLIP-2 screening (yerel, CPU) | 2.614 sn | %14 |
| D-FINE dedektör (yerel, CPU) | 884 sn | %5 |

**Darboğaz uzak modeldir.** Yerel algı toplamın yalnız beşte biridir. Bu, ölçekleme
yatırımının nereye gideceğini belirler: yerel CPU eklemek toplam süreyi en fazla
%19 iyileştirebilir.

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

### 4.2. Algı katmanı GPU hızlandırması

ONNX yürütme sağlayıcısı `DORTGOZ_ONNX_PROVIDERS` ile seçilir. Varsayılan CPU'dur.

**⚠ Yalıtılmış çekirdek hızından boru hattı kazancı çıkarmayın.** Yalıtılmış
ölçüm SigLIP için 13,65×, D-FINE için 6,77× gösterdi. Ancak boru hattı sayacı
ffmpeg çözümleme ve ön işlemeyi de içerir ve asıl maliyet oradadır. Uçtan uca
gerçek kazanç **%7-10**'dur.

GPU sağlayıcısı açıkken backend yaklaşık 2,3 GB VRAM tutar. Kalite değişmez:
aynı kliplerde tarama tepe puanları CPU ile GPU arasında en fazla 6e-04 ayrılır.

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
