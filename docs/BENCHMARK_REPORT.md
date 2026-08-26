# EVREN üretim kalite ve hız raporu

**Tarih:** 2026-08-24 (güncelleme 2026-08-25)  
**Durum:** Üç tam bölme koşusu tamamlandı (r1, r2, r3).

> ## ⚠ BU BİR GELİŞTİRME KIYASIDIR, FİNAL DOĞRULUK KANITI DEĞİLDİR
>
> Buradaki tüm sayılar UCF-Crime **resmî test bölmesinde** ölçüldü. Aynı bölme
> model seçimi, eşik ayarı ve varyant eleştirisi için de tekrar tekrar
> kullanıldı. `DORTGOZ_ARCHITECTURE_BASELINE.md` bu bölmeyi geliştirme
> açısından **tamamen kontamine** sayar. Bu yüzden:
>
> - Bu tabloları “geliştirme kıyası” diye etiketleyin, genelleme kanıtı diye değil.
> - **Genelleme kanıtı için bölüm 10.4'teki KÖR HOLDOUT sonucunu kullanın.**
>   Geliştirmede hiç kullanılmamış 80 klipte yakalama %90,0 [%76,9-%96,0]
>   ölçüldü; kontamine bölmeden **ölçülebilir farkı yok** (z=0,76).
> - **Tek koşu başlığı vermeyin, bant verin** (bkz. bölüm 10).
> - Tek akış hızı ile toplam kapasite hızını aynı cümlede kullanmayın.

## 1. Amaç

Bu çalışma, EVREN video yolunu eski yerel kare-model sonuçlarından ayrı ölçer.
Çalışma şu sorulara cevap verir:

1. Hangi EVREN modeli birincil video okuyucu olmalıdır?
2. İkinci görüş ve olay-geneli denetim yakalamayı artırır mı?
3. Denetim yanlış alarmı azaltır mı?
4. Kanıt sözleşmesi geçerli kalır mı?
5. Sistem gerçek zamana göre ne kadar hızlıdır?

## 2. Düzenek

- Veri: UCF-Crime resmî test bölmesi.
- Bölme parmak izi: `afcb2a6bffe7286607e08c864425964d8784cbd5b1f263e03b76025e11548de7`.
- Klip: 290. Anomali: 140. Normal: 150.
- Video süresi: 10,30 saat.
- Pencere: 30 saniye. Toplam pencere: 1.380.
- Eşzamanlı iş: 4.
- Screening: SigLIP-2 + D-FINE kurtarma.
- Screening eşikleri: başlangıç `0,80`, devam `0,48`.
- Candidate manifest SHA-256: `a16aea548000ed1fe7f679b1eb703c3db7d8ee1959f919b9c739792ebe6f7b90`.
- Çıkarım: EVREN, BF16, vLLM.
- Tam bölme kodu: `c734887b40a6311a790fe8c9400e1655bf655ba9` (r1-r3),
  `e56b44ca2286c1611908ffb058c7c5ba80b3cfcd` (r4).
- r4 tekrar üretimi: `bench/evren_quality.py --arm production --split test
  --parallel 4 --ucf <UCF kökü> --out bench/results/evren_testsplit_production_r4_gpu.jsonl`
- Sistem istemi SHA-256: `62474711ca079a3af4c33cc8dc655f74a409f1e019310e0714662de7c840b78c`.
- Görev istemi SHA-256: `65694521936b2f73ad4618b2dc56ce7059919cf13f67462eec1470b2a4d81684`.

Her olayın final sınıfı `normal` değilse ve risk seviyesi `dusuk` değilse olay
eyleme konu sayılır. Olay-geneli denetimin `normal` yaptığı kayıt yanlış alarm sayılmaz.
Ham pencere alarmı ayrıca zamansal değerlendirmede raporlanır.

## 3. Pilot model seçimi

31 kliplik pilotta 26 anomali ve 5 normal klip vardır.

| Birincil yol | Yakalama | Yanlış alarm | Doğru kategori |
|---|---:|---:|---:|
| `vlm` | **23/26** | 0/5 | 9 |
| `llm-fast` | 20/26 | 0/5 | **10** |
| `llm-large` | 16/26 | 0/5 | 7 |
| Üretim kaskadı | 22/26 | 0/5 | 9 |

Üç tekrarlı finalist pilotunda iki yol da her tekrarda 22/26 yakalama ve 0/5 yanlış
alarm verdi. `vlm` doğru kategori sayısı 8, 9 ve 8 oldu. Üretim kaskadı her tekrarda
9 doğru kategori verdi. Bu sonuç, `llm-fast` ve `llm-large` modellerini birincil
okuyucu olmaktan çıkardı.

## 4. Tam bölme sonucu

| Ölçüt | Yalnız `vlm` | Üretim kaskadı | Fark |
|---|---:|---:|---:|
| Anomali klip yakalama | 120/140 | **121/140** | +1 |
| Eyleme konu yanlış alarm | 39/150 | **22/150** | −17 |
| Doğru kategori | 61 | **74** | +13 |
| Teknik kanıt geçerliliği | **%97,35** | %97,06 | −0,29 puan |
| Otomatik geçerli kanıt | %86,00 | **%87,44** | +1,44 puan |
| Terminal hata | 0 | 0 | aynı |

Üretim kaskadı iki anomali klibini kurtardı: `RoadAccidents017` ve
`RoadAccidents131`. `Shoplifting010` klibini final denetimde normale indirdi. Net
kazanç bir kliptir.

Üretim kaskadı 18 `vlm` yanlış alarmını bastırdı. Bir yeni yanlış alarm ekledi.
Net azalma 17 kliptir. Olay-geneli denetim toplam 15 kaydı `normal` yaptı.

## 5. Zamansal sonuç

Her iki finalist de 156 GT olay aralığının 132'sini yakaladı: **%85**.

| Ölçüt | Yalnız `vlm` | Üretim kaskadı |
|---|---:|---:|
| GT aralığı | 132/156 | 132/156 |
| Pencere duyarlılığı | 0,86 | 0,86 |
| Pencere kesinliği | 0,35 | **0,37** |
| Normal görüntüde ham alarm | 21,6 pencere/saat | **20,3 pencere/saat** |
| Ortanca tespit gecikmesi | 0 sn | 0 sn |
| En yüksek tespit gecikmesi | 105 sn | 105 sn |

Zayıf sınıflar şunlardır:

- Shoplifting: 12/25 GT aralığı.
- RoadAccidents: 19/23 GT aralığı.
- Shooting: 21/25 GT aralığı.
- Robbery: 4/5 GT aralığı.

Beş saniyeden kısa olaylarda üretim kaskadı 35/47 yakalama verdi. Bu grup ana kalite
darboğazıdır.

## 6. Hız ve kapasite

### 6.1. Benimsenen yapılandırma — r4, ölçüldü (2026-08-26)

Bu koşu bugünkü üretim yolunu taşır: giriş genişliği 540, yerel algı MIGraphX
ile GPU'da, `parallel 4`. Kod sürümü `e56b44ca`.

| Ölçüt | Değer |
|---|---:|
| **Dört eşzamanlı işte gerçek duvar süresi** | **54,8 dk (3.285 sn)** |
| **Toplam akış hızı** | **11,29× gerçek zaman** |
| Toplam iş süresi (bileşen toplamı) | 12.099 sn |
| Tek-akış eşdeğeri | 3,07× gerçek zaman |
| Ortanca klip işleme | 24,0 sn |
| p95 klip işleme | 130,5 sn |
| En uzun klip | 870,7 sn |
| Terminal hata | **0/290** |

Zaman dökümü (toplam 12.077 sn):

| Bileşen | Süre | Pay |
|---|---:|---:|
| EVREN model çağrıları | 11.385 sn | **%94,3** |
| D-FINE dedektör (GPU) | 219 sn | %1,8 |
| SigLIP-2 screening (GPU) | 85 sn | %0,7 |
| Diğerleri (klip kodlama, hareket profili) | ~388 sn | %3,2 |

`vlm` çağrısı başına ortalama **6,25 sn** (1.821 çağrı). Olay-geneli ikinci geçiş
302 çağrı ve 1.683 saniyedir.

Süzgeç hunisi:

| Adım | Değer |
|---|---:|
| Görülen pencere | 1.380 |
| VLM öncesi elenen | 50 |
| D-FINE kurtarması | 549 |
| Seçilen anahtar kare | 8.255 |
| Açılan olay | 170 |
| Olay güncellemesi | 733 |

Kanıt doğrulama: teknik geçerlilik **%98,4**, otomatik geçerlilik **%88,0**
(1.926 doğrulama, 31 geçersiz, 0 belirsiz).

### 6.2. Önceki yapılandırma — r1 (genişlik 720, algı CPU'da)

| Ölçüt | Yalnız `vlm` | Üretim kaskadı |
|---|---:|---:|
| Dört eşzamanlı işte duvar süresi | 70,5 dk | 84,7 dk |
| Toplam akış hızı | **8,76×** gerçek zaman | **7,30×** gerçek zaman |
| Tek-akış eşdeğeri | 2,43× | 1,98× |
| Ortanca klip işleme | 27,2 sn | 36,4 sn |
| p95 klip işleme | 171,9 sn | 185,1 sn |
| Toplam model çağrısı | 1.329 | 1.818 |

r1'den r4'e duvar süresi **84,7 dk → 54,8 dk** (−%35), akış hızı **7,30× → 11,29×**
oldu. İki bağımsız kazanç birleşti: giriş genişliği 720→540 ve yerel algının
GPU'ya taşınması (yerel ayak 3.497 → 304 sn).

Üretim kaskadında birincil `vlm` çağrısının ortalaması 8,90 saniyedir.

| Rol | Çağrı | Ortalama süre |
|---|---:|---:|
| `vlm` birincil | 1.329 | 8,90 sn |
| `llm-large` olay-geneli denetim | 164 | 10,16 sn |
| `llm-fast` sınıf hakemi | 120 | 5,29 sn |
| `llm-large` tırmandırma | 91 | 4,76 sn |
| `llm-large` ikinci görüş | 114 | 4,04 sn |

Toplam üretim model süresi 15.016 saniyedir. SigLIP süresi 2.614 saniyedir. D-FINE
süresi 884 saniyedir. Screening yalnız 51/1.380 pencereyi VLM öncesinde eledi.
D-FINE 536 pencereyi geri açtı.

## 7. Token kaydı

Üretim koşusunda token bilgisi taşıyan 1.471 çağrı vardır.

- Prompt token: 17.616.342.
- Tamamlama token: 429.845.
- Ortalama prompt: 11.976 token/çağrı.
- Ortalama tamamlama: 292 token/çağrı.
- Etkin tamamlama hızı: yaklaşık 32 token/saniye.

Bu hız video kodlama ve EVREN ortak kuyruk süresini içerir. EVREN yanıtı ayrı TTFT veya
saf üretim TPS değeri vermez. İkinci görüş ve sınıf hakemi çağrılarında süre rol bazında
kayıtlıdır. Bu çağrıların token toplamı henüz run metriğine eklenmez.

## 8. Kalite varyantları

### Her boş pencerede ikinci görüş — reddedildi

41 zor klipte 266 ek ikinci görüş çağrısı yapıldı. Hiçbir kaçan anomali doğru biçimde
kurtarılmadı. Yanlış alarm sayısı 21 kaldı. Bu varyant kullanılmaz.

### Sekiz saniyelik yakınlaştırma — reddedildi

19 kaçan anomali klibinde yalnız `Shooting021` alarm üretti. Model olayı
`hirsizlik` olarak yanlış sınıfladı. Diğer 18 klip kaçtı. Bu varyant kullanılmaz.

### Sıkı olay-geneli denetim — reddedildi

Sıkı denetim, 22 bilinen yanlış alarmın 11'ini daha bastırdı. Ancak 140 anomali
klibinde yakalamayı 121'den **108'e** düşürdü. 21 anomali kaydını `normal` yaptı.
Recall kaybı kabul edilemez. Bu varyant kullanılmaz.

## 9. Üretim kararı

Üretim kaskadı seçildi:

1. `vlm` her kabul edilen pencereyi yerli video olarak okur.
2. `llm-large` yalnız eşik tırmandırması, ikinci görüş ve olay-geneli denetim yapar.
3. `llm-fast` kapanmış karışabilir olayların sınıf hakemidir.
4. SigLIP eşikleri `0,80/0,48` olur.
5. Kategori istem kuralları kapalı kalır.
6. Sıkı denetim, sürekli ikinci görüş ve yakınlaştırma kapalı kalır.

Seçim sırası güvenlik-önceliklidir: önce yakalama, sonra yanlış alarm, sonra kategori,
sonra maliyet. Üretim kaskadı `vlm` yoluna göre daha yüksek yakalama, daha düşük yanlış
alarm ve daha yüksek kategori doğruluğu verir.

## 10. Sınırlamalar

### 10.1. Üç tam tekrarın bandı (2026-08-25)

| koşu | genişlik | algı | yakalama | yanlış alarm | kategori |
|---|---|---|---|---|---|
| r1 | 720 | CPU | 121/140 | 22/150 | 74 |
| r2 | 720 | GPU | 120/140 | 28/150 | 72 |
| r3 | **540** | GPU | 119/140 | **18/150** | 74 |
| r4 | **540** | GPU | **123/140** | 25/150 | **75** |

**Rapora ve sunuma bu bantlar girer:** yakalama **%85,0-87,9**, eyleme konu
yanlış alarm **%12,0-18,7**, kategori **72-75**. Tek koşu başlığı (ör. “121/140”)
tek başına kullanılmaz. r3 ve r4 diğer ikisinden farklı bir giriş genişliği
kullanır; aralık bu yüzden hem tekrar oynaklığını hem genişlik etkisini içerir.

r3 ve r4 **aynı yapılandırmadır** ve yalnız tekrar oynaklığıyla ayrılır: yakalama
119 vs 123, yanlış alarm 18 vs 25. Bu, §10.2'deki kararlılık uyarısının doğrudan
kanıtıdır — **aynı kod ve aynı veriyle iki koşu bu kadar ayrılabilir.**

### 10.2. Yanlış alarm kimliği KARARSIZ

Üç koşunun yanlış alarm kümeleri büyük ölçüde örtüşmüyor: `r1∩r2∩r3` yalnız
**9 klip**. r3'te 8 yepyeni yanlış alarm çıktı, r1∩r2'nin 11'i kayboldu.
Yanlış alarm sayısı tekrarlanabilir, **yanlış alarm listesi değildir**. Tek bir
koşunun klip listesine dayanan hata analizi yanıltıcıdır.

### 10.3. Dal bazında kesinlik eşit DEĞİL

r3 ölçümü (klip düzeyi, eyleme konu olaylar):

| dal | duyarlılık | kesinlik | taban oran |
|---|---|---|---|
| patlama | %47 | %93 | %10 |
| arac_kazasi | %65 | %88 | %8 |
| yangin | %23 | %88 | %10 |
| hirsizlik | %66 | **%56** | %15 |
| silahli_olay | %39 | **%50** | %10 |
| kavga | %60 | **%47** | %5 |

Görünür fiziksel olaylar (patlama, kaza, yangın) ~%90 kesinlik verir. **Niyet**
gerektiren sınıflar (hırsızlık, silahlı olay, kavga) ~%50'de kalır. Yanlış
alarmların çoğunluğu `hirsizlik` dalından gelir ve bu dal bastırılamaz:
kapatılırsa hırsızlık-ailesi yakalama %75'ten %9'a düşer. Ayrıntı ve gerekçe iç
karar günlüğünde kayıtlıdır ("Yanlış alarm çekirdeği incelemesi", 2026-08-25).

### 10.4. KÖR HOLDOUT — kontaminasyon itirazına cevap (2026-08-25)

Tüm yukarıdaki sayılar kontamine bölmededir. Bu itirazı kapatmak için
**geliştirmede HİÇ kullanılmamış** 80 klipten oluşan kör bir set ölçüldü.

- Kaynak: UCF-Crime **eğitim** bölmesi (test bölmesi değil).
- Seçim: `bench/kor_holdout_sec.py`, tohum `20260825`, tekrar üretilebilir.
  "Kullanılmamış" tanımı `data/annotations/`, `media/` ve **tüm önceki
  `bench/results/*.jsonl`** kliplerinin dışında kalmaktır (1.610 eğitim
  klibinin 1.585'i dokunulmamış).
- Kompozisyon: 40 anomali + 40 normal, sınıf oranları test bölmesini yansıtır.
- Süre filtresi: 10-300 sn (medyan 75 sn), toplam 2,0 saat.
- Kod `6ee3d36`, genişlik 540, p4, üretim kolu. Ham veri:
  `bench/results/evren_holdout_train80.jsonl`.

| ölçüt | **kör holdout** | test bölmesi r3 (10-300 sn, eşleştirilmiş) |
|---|---|---|
| yakalama | **36/40 = %90,0** [%76,9-%96,0] | 110/129 = %85,3 [%78,1-%90,4] |
| eyleme konu yanlış alarm | 5/40 = %12,5 [%5,5-%26,1] | 13/137 = %9,5 [%5,6-%15,6] |
| kategori doğruluğu | 20/36 = %56 | 69/110 = %63 |
| **kritik alt küme** | **20/20 = %100** [%83,9-%100] | 58/65 = %89,2 [%79,4-%94,7] |
| tek akış hızı | 2,51× | 2,80× |
| terminal hata | 0 | 0 |

Aralıklar Wilson %95'tir. Kritik alt küme = Explosion, Shooting, Arson,
Assault, Fighting, Robbery, Abuse.

**Sonuç — dikkatli okuyun:**

1. **Genelleme tutuyor.** Kör sette yakalama düşmüyor, hatta 4,7 puan yüksek.
   Bu fark **istatistiksel olarak anlamlı DEĞİLDİR** (z=0,76). Doğru ifade
   "kör sette daha iyiyiz" DEĞİL, **"kör set ile kontamine set arasında
   ölçülebilir bir fark yok"**tır. Yani test bölmesindeki geliştirme,
   yakalama sayısını şişirmemiş.
2. **n=40'a dikkat.** %90,0 bir nokta değeridir; güven aralığı %76,9-%96,0'dır.
   "Kör sette %90 yakalıyoruz" cümlesi aralık verilmeden kullanılmamalıdır.
3. **Kategori doğruluğu kör sette bir miktar düşüyor** (%56 vs %63).
   Kontaminasyonun gerçekten yardım etmiş olabileceği tek ölçüt budur;
   sınıf ayrımı test bölmesine bakarak ayarlanmıştı.
4. **Yanlış alarm kalıbı bağımsız veride tekrarlandı.** 5 yanlış alarmın
   tamamı `orta` risktir; sınıfları `hirsizlik` (3) ve `arac_kazasi` (2).
   Metinler yine olağan davranış: "kişi araçtan valiz çıkarıyor", "kadın
   çantasını koltuğa bırakıyor". Bu, `possible_theft@orta` teşhisini
   **bağımsız veride doğrular**.
5. Kaçırmalar bilinen zayıf noktalarda toplanıyor: RoadAccidents 5/7,
   Shoplifting 4/6. Diğer 11 sınıfın hepsi tam yakalandı.
6. İki klipte (`Shooting029`, `Shooting050`) 2. geçiş kanıt kapısından
   geçmedi ve mevcut olay korundu. Bu **fail-closed doğru davranıştır**,
   hata değildir; yakalama zaten gerçekleşmiştir.

### 10.5. Diğer sınırlar

- **Tek akış hızı koşuya ve giriş genişliğine göre 1,98× ile 2,80× arasındadır.**
  r1 (720) 1,98×, r2 (720) 2,31×, r3 (**540, benimsenen**) **2,80×**, kör
  holdout (540) **2,51×**. Benimsenen yapılandırmada **%2,5× hedefi karşılanır**.
  ⚠ Eski raporlardaki tek başına "1,98×" değeri yalnız r1'e aittir ve
  güncel değildir. Aynı genişlikteki r1 ile r2 arasında %17 fark vardır;
  EVREN paylaşımlı kuyruk yükü tek akış hızını ciddi şekilde değiştirir,
  bu yüzden tek değer değil **bant** verilmelidir.
- "4 iş toplam" değerleri (7,93× / 9,22× / 11,20× / 10,04×) **kapasitedir**,
  tek akış hızı DEĞİLDİR. İkisi aynı cümlede kullanılmaz.
- `evidence_automatic_valid_rate` bir **doğruluk** ölçüsü değildir. Kanıt
  doğrulayıcısı yalnız frame_id, timestamp, path ve hash denetler; iddia metnini
  kareyle karşılaştırmaz. Bu bir **atıf hijyeni** ölçüsüdür.
- UCF-Crime normal klipleri tam saha normal dağılımını temsil etmez.
- Ham pencere FP metriği, anomali klibindeki GT dışı gerçek olayları da FP sayabilir.
- EVREN ortak kuyruk yükü gecikmeyi değiştirir.
- Kanıt zaman çizelgesi 1 saniye hassasiyetindedir.
- Shoplifting ve çok kısa olaylar için 2 fps video örneklemesi sınırlayıcı olabilir.
- 2026-08-25 öncesi koşu artıfaktlarında `config_hash` giriş genişliğini
  içermez; r2 (720) ile r3 (540) aynı hash'i taşır. Bu alan sonradan eklendi.

## 11. Yerel algı GPU hızlandırması (2026-08-25)

Yerel algı katmanı MIGraphX (ROCm 7.2.3, gfx1201, MIT lisans) ile GPU'ya taşındı.
EVREN çağrıları değişmedi.

| Aşama | CPU | GPU | Kazanç | Sayısal denklik |
|---|---:|---:|---:|---|
| SigLIP fp16, batch 16 | 1.936 ms | **9,7 ms** | 202× | kosinüs 0,999988 |
| D-FINE fp32, batch 4 | 172 ms/kare | **12,3 ms/kare** | 14× | 0,40 eşiğinde tespitler birebir |

Boru hattı içinde ölçülen gerçek kazanç daha düşüktür, çünkü sayaçlar ffmpeg kare
çıkarmayı da kapsar: 31 klip × 3 tekrarda SigLIP 142,0 → 16,1 sn (8,8×), D-FINE
69,3 → 21,2 sn (3,3×). Bundan sonra taban ffmpeg'dir.

**D-FINE fp16 reddedildi.** Aynı karede 0,40 eşiğinde 11 yerine 12 tespit üretti.

**Kalite bandı.** CPU üç tekrar 22/22/22 yakalama; GPU üç tekrar 21/22/22. Her iki
kolda 0/5 yanlış alarm ve aynı sabit kaçırma çekirdeği. Tek koşudaki fazladan
kaçırma, belgelenmiş EVREN örnekleme varyansı (±3 klip) içindedir. Bantlar örtüşür;
özdeşlik iddia edilmez.

Kurulum `scripts/build_migraphx.sh` ile yeniden üretilir. `DORTGOZ_MIGRAPHX_DIR`
boşsa GPU yolu kapalıdır ve sistem CPU ile çalışır. Manifest kaynak ONNX SHA-256'sını
tutar; kaynak değişirse GPU yolu sessizce CPU'ya döner.

## 12. Artifact'lar

- `bench/results/evren_testsplit_vlm.jsonl`
- `bench/results/evren_testsplit_vlm.summary.json`
- `bench/results/evren_testsplit_production_r4_gpu.jsonl`
- `bench/results/evren_testsplit_production_r4_gpu.summary.json`
- `bench/results/evren_testsplit_production.jsonl`
- `bench/results/evren_testsplit_production.summary.json`
- `bench/results/evren_pilot_vlm_r3.jsonl`
- `bench/results/evren_pilot_production_r3.jsonl`
- `bench/results/evren_hard_rescue_all.jsonl`
- `bench/results/evren_false_alarm_strict.jsonl`
- `bench/results/evren_missed_zoom.jsonl`
- `bench/results/evren_anomaly_strict.jsonl`
