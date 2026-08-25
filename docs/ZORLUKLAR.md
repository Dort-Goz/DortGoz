# Karşılaşılan zorluklar ve getirilen çözümler

Bu belge, Dörtgöz geliştirilirken karşılaşılan gerçek sorunları kaydeder. Her
madde aynı biçimi kullanır: sorun, kök neden, çözüm ve kanıt. Kayıt yalnız
ölçülmüş sonuçları içerir. Denenip reddedilen fikirler de buradadır, çünkü bir
fikri neden bırakmak gerektiği de sonuçtur.

Ayrıntılı ölçüm tabloları [`BENCHMARK_REPORT.md`](BENCHMARK_REPORT.md) içindedir.
Mimari kararların gerekçesi [`../DORTGOZ_ARCHITECTURE_BASELINE.md`](../DORTGOZ_ARCHITECTURE_BASELINE.md)
içindedir.

---

## 1. En pahalı ders: sessiz bozulma

Projede zaman kaybettiren hataların çoğu çökme değildi. Sistem yeşil görünürken
bir bileşen sessizce devre dışı kalıyordu. Bu sınıf o kadar sık tekrarladı ki
ayrı bir kural haline geldi.

**Kural: bir bileşen kapanabiliyorsa, kapandığını SESLİ söylemelidir.**

Ölçülen örnekler:

| Bileşen | Sessiz davranış | Sonuç |
|---|---|---|
| D-FINE dedektörü | ONNX dosyası yoksa ize bir satır yazıp devam eder | İlk canlı koşunun tamamı dedektörsüz geçti |
| Prosedür RAG | Süresi dolan belgeyi filtreler, uyarı vermez | Prosedür getirme boş dönerdi |
| MigraphX sağlayıcısı | Yüklenemezse CPU'ya döner | Hızlanma sessizce kaybolur |
| Model adı | Uçta olmayan ad varsayılana yönlenir | Yanlış model ölçülür |
| Vite proxy | `localhost` IPv6'ya çözülür | Tüm arayüz boş kalır |
| Olay klibi kodlayıcısı | Tarayıcının çözemediği codec üretir | Oynatıcı `0:00` ve ölü açılır |

Her biri aşağıda ayrı ayrı açıklanmıştır. Ortak çözüm deseni şudur: hazırlık
denetimi (`/ready`) bileşeni zorunlu sayar, koşu kaydı bileşenin gerçekten
çalıştığını sayı ile kanıtlar, ve arayüz eksikliği operatöre gösterir.

---

## 2. Algı katmanı

### 2.1. Hareket kapısı yönsüzdü

**Sorun.** Kapı 40 pencerenin sıfırını eledi. Daha kötüsü, gerçek olaylı
pencereler 0,0060'a kadar iniyor, olaysız pencereler 0,4461'e çıkıyordu.
Dağılımlar uçlarda ters dönmüştü.

**Kök neden.** Ölçüt ortalama mutlak farktı. Bu ölçüt sensör gürültüsünü tüm
piksellere yayarak toplar. Gürültülü ölü kayıt 0,0042 verdi. En sakin gerçek
pencere 0,0061 verdi. Pay yalnız 1,5× kaldı. Eşik gürültü tabanına sıkışmıştı.

**Çözüm.** Tek ölçüt yerine iki sinyal kullanılır.

1. `changed` — piksel başına eşiği (τ=18) aşan piksellerin oranı. Gürültülü ölü
   kayıtta 0,0000 verir. Pay 3,3×'e çıkar.
2. `fg` — koşan arka plan modeline göre ön plan oranı. Bu sinyal "sahnede bir şey
   VAR mı" sorusunu sorar. Kare farkı yalnız "DEĞİŞTİ mi" sorusunu sorar.

İkinci sinyal kritiktir. **Yerde hareketsiz duran kişi hedef olay türlerimizden
biridir ve kare farkı için görünmezdir.** `BG_ALPHA=0.02` ile duran nesne yaklaşık
50 saniye ön planda kalır. Nihai ölçüt `activity = max(changed, fg)` olur.

**Yapılan ikinci hata.** Eşiği yüzdelikle ölçekleyen ilk "uyarlanabilir" sürüm,
baştan sona hareketli kliplerde gürültü tabanını sinyal sandı. Eşiği 1,0'ın
üstüne çıkardı ve **12 gerçek olaylı pencereyi eledi**. Düzeltme bir tavandır
(`ceiling=0.010`). Ders: **eşik sahne yoğunluğuyla ölçeklenmemelidir.**

**Kanıt.** 31 klipte 40/40 pencere korunur. Sıfır gerçek olay kaybı. Üç sentetik
ölü kayıt (temiz, gürültülü, saat damgalı) elenir. Regresyon testi ölü görüntüyü
ffmpeg ile üretir.

**Mimari sonuç.** Hiçbir hareket ölçütü "duran kişi" ile "boş oda"yı ayıramaz.
Pencere kabul kararı dedektöre ve semantik screening'e devredilmiştir. Hareket
yalnız dedektörün hangi karelerde koşacağını seçer.

### 2.2. Dedektör sessizce kapandı

**Sorun.** İlk canlı koşunun tamamı D-FINE dedektörü kapalı halde çalıştı. Koşu
yeşil göründü.

**Kök neden.** `detector_enabled` varsayılanı `True` idi. Ancak `dfine_onnx`
varsayılanı bir makineye özgü mutlak yoldu. Dosya başka bilgisayarda yoktu. Hat
hata vermek yerine ize bir satır yazıp küçük düşürülmüş halde devam etti.

**Çözüm.** Üç katman eklendi.

1. Model indirme betiği: `./scripts/fetch_models.sh` (41 MB).
2. `competition-real` hazırlık denetimi D-FINE artifact hash'ini zorunlu sayar.
   Artifact yoksa `/ready` 503 döner ve analiz hiç başlamaz.
3. Koşu kaydındaki `run_metrics` içinde `dfine_calls` sayacı bulunur. Sayı sıfırsa
   dedektör koşmamıştır.

**Doğrulama yöntemi.** Yeni makinede `dfine_calls > 0` olduğunu bir kez denetleyin.

**Ölçüm.** Dizüstü CPU'sunda çağrı başına yaklaşık 697 ms. Dört kare örnekle
kare başına yaklaşık 174 ms. Bu değer, belgelenen 160 ms/kare ile tutarlıdır.

### 2.3. Screening artifact'i yoksa hat düşer, durmaz

**Sorun.** SigLIP-2 ONNX dosyası 355 MB'dir ve depoya girmez. Dosya yoksa ne olur?

**Çözüm.** Davranış bilinçli olarak iki katmanlıdır.

- `development` profili: scorer yüklenemezse ize `anlamsal screening düştü,
  baseline'a dönüldü` satırı yazılır ve hareket temelli baseline devreye girer.
  Koşu tamamlanır, kalite düşer.
- `competition-real` profili: hazırlık denetimi artifact hash'ini zorunlu sayar.
  Eksikse analiz hiç başlamaz.

Bu ayrım, geliştiricinin modelsiz çalışabilmesini sağlar ve yarışma koşusunun
sessizce küçük düşürülmesini engeller.

---

## 3. Model çıktısı ve şema

### 3.1. Kesilmiş JSON koşuyu 19. dakikada öldürüyordu

**Sorun.** Uzun koşular ortada ölüyordu.

**Kök neden.** GBNF dilbilgisi geçerli bir **önek** garanti eder. Çıktının
**bitmiş** olmasını garanti etmez. Olay yoğun bir pencere `max_tokens` sınırını
aşınca çıktı ortadan kesilir. Kesilen metin şema doğrulamasından geçmez.

**Çözüm.**

1. `repair_truncated_json()` çıktıyı son TAM öğeye kadar kurtarır. Virgül yalnız
   öğe sınırında güvenli kesim noktasıdır.
2. Kesilme olayın `uncertainties` alanına yazılır. Operatör bilgi kaybını görür.
3. `max_tokens` 700'den 1400'e çıkarıldı. İki kademeli üretimde bu maliyet yalnız
   olaylı pencerede ödenir.

### 3.2. Düşünme kipi kazanç vermedi

**Sorun.** Model kalitesini artırmak için düşünme kipi (`enable_thinking`)
denendi.

**Ölçüm.** Yakalama %70,7'den %76,4'e çıktı. Ancak yanlış alarm 14'ten 18/150'ye
yükseldi ve ek maliyet 1.423 saniye oldu. Bu, dakika başına 2,3 GPU saniyesi
demektir.

**Karar.** Üretimde kapalıdır. Yalnız tırmandırma çağrısı düşünme kullanır.
Belgelerin "düşünme zararlı" genellemesi bizim yapımız için doğru değildir;
ölçülen sonuç maliyet-fayda dengesidir.

### 3.3. Model adı doğrulanmalıdır

**Sorun.** Uçta bulunmayan bir model adı verildiğinde istek reddedilmez. Ad
sessizce varsayılana yönlenir.

**Çözüm.** Kullanılan adlar `/v1/models` çıktısına karşı doğrulanır. Hazırlık
denetimi model takma adlarını sayar ve raporlar.

---

## 4. Olay bütünlüğü

### 4.1. Sabit pencere uzun olayı ikiye bölüyordu

**Sorun.** 270 saniyelik bir saldırı olayı defterde iki ayrı olay olarak
göründü. Ortadaki bağlamsız pencere "normal" dedi. Defter olayı kapatıp yenisini
açtı. Ayrıca hiçbir çağrı olayın bütününü görmediği için öncesi-zirve-sonrası
anlatısı doldurulamıyordu.

**Çözüm — üç katman.**

1. **Süreklilik ipucu.** Açık olayın durumu bir sonraki pencerenin istemine
   taşınır. Ham önceki özet değil, defter durumu taşınır. Böylece bir pencere
   gecikme olur ve eşzamanlılık bozulmaz. ⚠ Çapa etkisine karşı "bittiyse açıkça
   yaz, olay uydurma" talimatı zorunludur.
2. **Olay-geneli ikinci geçiş.** Olay kapanınca sınırlar bilinir. Tüm aralık 16
   kareyle tek çağrıda yeniden okunur. Kart yerinde düzeltilir. Yalnız
   çok-pencereli olaylarda çalışır.
3. **Defter toleransı** (`incident_grace_windows=1`). Tek sessiz pencere olayı
   kapatmaz.

Yalnız birinci katman yeterli olmadı. Bir koşuda olay birleşti, diğerinde yine
bölündü. Yapısal çözüm üçüncü katmandır.

**Sonuç.** Saldırı tek olay olur. Yakalama ve yanlış alarm değişmez.

### 4.2. Olay bitişi video süresini aşabiliyordu

**Sorun.** Operatör nöbet kuyruğunda hiçbir kararı kaydedemiyordu. Arayüz
"bağlantıyı denetleyin" diyordu. Bağlantı sağlamdı.

**Kök neden.** Defter, kanıt zaman damgasına 1 saniye dolgu ekler. Klibin son
saniyesindeki bir kanıt, video süresinin ötesinde bir bitiş üretir. Örnek: 31,6
saniyelik klipte bitiş 32,0 saniye. Sunucu bunu doğru biçimde reddeder. Ancak
form varsayılanı bu geçersiz değeri taşıdığı için kayıt her denemede
başarısız olur.

**Çözüm.** Defter video süresini bilir ve bitişi süreye kırpar. Arayüz ayrıca
sunucunun gerçek hata mesajını gösterir, genel bir bağlantı mesajı değil.

---

## 5. Dayanıklılık

### 5.1. Tek yavaş istemci tüm analizi donduruyordu

**Sorun.** Analiz duruyordu. Port dinliyordu. Yirmi bağlantı kuyrukta bekliyordu.
Otuz sekiz sızıntı bağlantı vardı.

**Kök neden.** WebSocket yayını her istemciye sırayla ve zaman aşımsız
gönderiyordu. Tamponu dolan tek istemci — uyuyan bir dizüstü veya temiz
kapanmayan bir sekme — tüm yayını kilitliyordu.

**Çözüm.** Eşzamanlı gönderim, 5 saniye zaman aşımı ve yanıtsız istemciyi
düşürme.

**Not.** Bu hata jüri demosunda ölümcül olurdu.

### 5.2. Tek pencere hatası koşuyu öldürüyordu

**Çözüm.** Pencere yalıtımı eklendi. Bir pencerenin hatası kaydedilir, pencere
atlanır ve koşu devam eder.

### 5.3. Eşzamanlılık sınırı hata gibi görünür

**Sorun.** 25 klip başlatıldığında altı besleme "HATA" raporladı.

**Kök neden.** Gerçek sebep `akış sınırı: aynı anda en çok 25 koşu` idi. Klipler
hiç başlamamıştı.

**Ders.** Hata saydığınız şeyin detayını okuyun. Sınır aşımı ile çalışma zamanı
hatası aynı rozetle gösterilmemelidir.

### 5.4. Test paketi gerçek olay deposuna yazıyordu

**Sorun.** On dört test kırmızıydı.

**Kök neden — iki ayrı hata.**

1. İki test dosyası her videoya aynı `file_hash_sha256` değerini veriyordu.
   `videos` tablosunda kasıtlı bir içerik tekilleştirme indeksi vardır. İkinci
   video UNIQUE ihlaline düşüyordu.
2. `.env` dosyası `DORTGOZ_EVENT_STORE_PATH` verdiği için modül seviyesindeki
   çalışma zamanı nesnesi **import anında** gerçek SQLite dosyasını açıyordu.
   Test paketi üretim verisini kirletiyordu.

**Çözüm.** Hash artık video kimliğinden türetilir. Test paketi olay deposunu
izole eder. Sonuç: 690 yeşil test.

### 5.5. Zaman bombası: prosedür belgesinin süresi doluyordu

**Sorun.** `data/procedures/manifest.json` içindeki `valid_until` değeri
2026-08-26 idi. Final ve ödül töreni bu tarihten sonradır.

**Kök neden.** `procedure_index.find()` süresi dolan belgeyi sessizce filtreler.
Daha kötüsü, hazırlık denetimi yalnız onay bayrağına bakıyor, geçerlilik
penceresine hiç bakmıyordu. Hazırlık yeşil görünürken RAG ölü olacaktı.

**Çözüm.** Geçerlilik 2026-10-31'e alındı. `usable_documents()` eklendi. Hem
arama hem hazırlık aynı kuralı kullanır, böylece kural tek yerdedir. Hazırlık
artık "onaylı N belgenin hiçbiri bugün geçerli değil" diye sesli düşer.

---

## 6. Arayüz

### 6.1. Vite proxy `localhost` kullanamaz

Node 18 ve sonrası `localhost` adını önce IPv6'ya çözer. Uvicorn yalnız IPv4
dinlerse tüm arayüz sessizce boş kalır: video listesi, deney paneli ve WebSocket
gelmez. Proxy `127.0.0.1` kullanmalıdır.

### 6.2. Olay klibi tarayıcıda oynamıyordu

**Sorun.** İnceleme kartındaki oynatıcı `0:00` süre ve kapalı denetimlerle
açılıyordu.

**Kök neden.** Klip yazıcısı sabit `-c:v mpeg4` kullanıyordu. Bu MPEG-4 Part 2
demektir ve hiçbir tarayıcı bu codec'i çözmez.

**Çözüm.** Kodlayıcı koşu başına bir kez sorulur: `libx264`, sonra
`libopenh264`, sonra `mpeg4`. Sonuç önbelleğe alınır.

### 6.3. Kayıtsız klipler için operatör karar veremiyordu

**Sorun.** Yalnız API'den yüklenen videolar için karar kaydı çalışıyordu. Diskteki
`media/` klipleri için `/api/triage/decide` 409 dönüyordu.

**Kök neden.** Çalışma zamanı projeksiyonu videoyu canonical defterde bulamazsa
analiz ve `event_id` üretmez. Dosya tabanlı analiz yolu kaynağı hiç kaydetmiyordu.
Canlı akış yolu bunu zaten yapıyordu.

**Çözüm.** Dosya analizi de koşu öncesi `register_runtime_source` çağırır.

### 6.4. İzleme akışı okunamıyordu

**Sorun.** Ajan izleme onlarca satır üretiyordu ve akış izlenemiyordu.

**Çözüm — iki kipli izleme.** Varsayılan kip başlangıç ve bitiş çiftlerini tek
satıra indirir ve ardışık olaysız pencereleri katlar. Bu kip bilgi attığı için
panelde bir **detay** düğmesi vardır. Detay kipi ham akışı gösterir ve hiçbir
satırı gizlemez.

Ayrıca izleme satırları zenginleştirildi. Satır artık pencere sınırını, süreyi,
etkinlik-eşik karşılaştırmasını, süregelen olay bağlamının verilip verilmediğini,
yorum sonucunu ve defterin kararını taşır.

---

## 7. Ölçüm disiplini

### 7.1. Tek koşudan sonuç çıkarılamaz

**Ölçüm.** Sıcaklık sıfır olmasına rağmen 71 kırılgan klipte 11 karar koşudan
koşuya değişti. Eşzamanlı kopyalarda karar birebir aynıdır. Varyansın kaynağı
zaman içindeki sunucu durumudur.

**Kural.** Rapora giren her sayı en az iki, tercihen üç tekrar ile ve yayılım
bandıyla verilir. Tek sayı değil, aralık verilir.

**Sonuç.** Resmî test bölmesi üç tekrarla ölçüldü. Klip yakalama %66-70 bandında,
yanlış alarm 13-15/150 bandındadır.

### 7.2. Geliştirme kıyası genelleme kanıtı değildir

Eşikler ve istemler test bölmesi üzerinde ayarlandı. Bu yüzden aynı bölmedeki
sonuç iyimserdir. Geliştirmede hiç kullanılmamış 80 kliplik kör bir holdout
ayrıldı. Kör holdout yakalaması %90,0 [%76,9-%96,0] ölçüldü.

### 7.3. Kendi ölçütünüz yanıltabilir

İlk değerlendirme ölçütü "herhangi bir olay üretildi mi" idi. Bu ölçüt normal
kliplerde 14 olay saydı ve sistemi başarısız gösterdi. İnceleme, bu 14 olayın
tamamının `dusuk` şiddetli olduğunu gösterdi: park eden araç, yürüyen insanlar.
Model istendiği gibi betimleme yapıyordu.

**Düzeltme.** Ayırt edici olan **şiddet**tir, olay varlığı değil. `orta` ve
üstü eşikle aynı veri 20/26 yakalama ve 0/5 yanlış alarm verir. Defter `dusuk`
olayları anlatı sayar ve alarm üretmez.

---

## 8. Ölçülüp reddedilen fikirler

Aşağıdaki fikirler denenmiş, ölçülmüş ve kullanılmamıştır. Her biri gerçek
geliştirme zamanı harcadı.

| Fikir | Ölçüm | Neden reddedildi |
|---|---|---|
| İki kademeli VLM kaskadı (ucuz ön eleme + derin okuma) | Birim maliyet oranı yalnız 3,5× | Kaskad deseni ~20× fark varsayar. Eşit yakalamada kol B %27 daha pahalı çıktı |
| Ucuz VLM bakışı (forced-choice) | Olaylı pencere ortanca P=0,108, olaysız 0,010 | Dağılımlar örtüşüyor. Olaylı min < olaysız maks |
| Kategori istem kuralları | 31 klipte net zararlı | Doğruluk düştü, kök neden bulunamadı |
| Her boş pencerede ikinci görüş | 41 klipte 266 ek çağrı | Hiçbir kaçan anomali kurtarılmadı |
| Sekiz saniyelik yakınlaştırma | 19 kaçan klipte 1 alarm | O tek alarm da yanlış sınıflandı |
| Sıkı olay-geneli denetim | Yanlış alarm 22→11 | Yakalama 121→108 düştü. Recall kaybı kabul edilemez |
| `max_inflight` 4→8 | p8, p4'ten %20 yavaş | Uç tüm takımlar arasında paylaşımlı FIFO kuyruk kullanır |
| Etkinliğe hizalı dinamik pencereleme | Gerçek kayıtta %6 kazanç | Ölü görüntüde daha kötü. Arka plan modelinin ~50 sn toparlanma gecikmesi var |
| Dedektör sayı öznitelikleri | val-AUROC 0,774, ölçekte düştü | Küçük örneklem yanılsaması |
| Hücre temelli konsantrasyon | AUROC 0,456 | Yerellik hipotezi doğrulanmadı |

**Ders.** Bir optimizasyonun teorik gerekçesi, o gerekçenin öncülleri ölçülmeden
kabul edilmemelidir. Kaskad fikri literatürde doğrudur; bizim modelimiz MoE
olduğu için öncülü geçersizdi.

---

## 9. Donanım ve servisleme

### 9.1. VRAM taşma uçurumu

Eski "15,0 GB tavan" kuralı geçersizdir. Gerçek sınır GTT taşma uçurumudur ve
16 GB kartta yaklaşık 16.050-16.100 MiB toplam kullanımdadır. Aşımda çökme
olmaz. Ciddi yavaşlama olur: prompt işleme %79 düşer. Bu yüzden pratik bütçe
yaklaşık 15.400 MiB'dir.

### 9.2. FP8 sessizce FP32'ye açılıyordu

RDNA4 üzerinde vLLM denendi. GGUF yükleyici hedef mimariyi reddetti. FP8
safetensors ise sessizce FP32'ye açıldı ve %29 hız kaybı ölçüldü. Bu yüzden
büyük model için Vulkan yolu korundu.

### 9.3. Çoklu görüntü ve spekülatif kod çözme birlikte çöküyordu

Görüntü projektörü ile spekülatif kod çözme birlikte kullanılırsa süreç çöker.
İstek başına `speculative.n_max: 0` verilmesi zorunludur.

### 9.4. Windows'ta satır sonu hash kapısını kırıyordu

Prosedür manifesti dosyanın SHA-256 değerini tutar. Git blob'u LF ile bu değeri
üretir. Windows çalışma ağacı CRLF ile farklı bir değer üretir. Hash eşleşmeyince
prosedür RAG yüklenmez. Çözüm `.gitattributes` ile prosedür dosyasının satır
sonunu sabitlemektir.

---

## 10. Canlı akış

### 10.1. Yirmi beş kamera tek GPU'yu aşıyordu

**Ölçüm.** 25 kamerada 900 saniye video düşürüldü. Gecikme ortancası 152
saniye, en yüksek 330 saniye oldu.

**Ölçüm.** 9 kamerada düşürme sıfır, gecikme en fazla 18 saniyedir.

**Sonuç.** Az kamera daha çok işlenmiş video saati verir. Üretim varsayılanı
dokuz kameradır.

### 10.2. Canlı kanıt yok oluyordu

**Sorun.** Canlı segment tamponu yalnız üç segment (yaklaşık 90 saniye) tutar.
Operatör bir olayı incelemeye geldiğinde segment silinmiş oluyordu.

**Çözüm.** Olay açılınca kanıt klibi ayrıca kesilir ve saklanır.

**Neden segment saklamak yerine klip kesmek.** Segment ortancası yaklaşık
1,7 MB'dir. Dokuz kamera saatte 480 segment üretir. Saatlerce saklamak
gigabaytlara çıkar. Kanıt klibi yalnız olay süresi kadardır.

---

## 11. Tarihsel not: yerel modelden EVREN'e geçiş

Proje ilk üç haftada tek 16 GB GPU üzerinde tamamen yerel çalışacak biçimde
tasarlandı. Model seçimi, bağlam profili, VRAM bütçesi ve kaskad kararları bu
kısıta göre alındı.

Yarışma sahibi 8×H200 üzerinde vLLM ile servis edilen EVREN model kadrosunu
sağlayınca üretim yolu değişti. Yerel modeller üretimden çıktı. Rol dağıtımı
yeniden yapıldı.

Bu geçiş, önceki ölçümlerin bir kısmını geçersiz kıldı. Karar kaydı eski
kararları silmez, üzerini çizip gerekçesini bırakır. Böylece bir kararın neden
verildiği ve neden geçersizleştiği birlikte okunabilir.
