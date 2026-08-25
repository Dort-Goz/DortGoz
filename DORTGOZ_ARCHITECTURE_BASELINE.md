# Dörtgöz Mimari Baseline

Bu belge üretim mimarisinin kısa karar kaydıdır. Bir makale taslağı değildir.
Bir bileşen yalnız yayımlanmış bir ilke, doğrudan proje ihtiyacı, ölçülmüş bir
sorun veya kritik güvenilirlik gereği varsa üretim yolunda kalır.

## 1. Proje amacı

Dörtgöz, güvenlik kamerası videosunu yerel algı katmanı ve yarışmanın
sağladığı EVREN çıkarım servisi (vLLM) ile analiz eder; tamamen yerel
OpenAI-uyumlu bir uç da kullanılabilir. Sistem,
operatöre zaman ve kare kanıtına bağlı olaylar sunar. Sistem, olay sürekliliğini
korur ve gerektiğinde aynı olayı daha geniş zaman bağlamında tekrar inceler.
Sistem bulut API, ücretli servis ve lisansı uygun olmayan bileşen kullanmaz.

UCF-Crime, problem çerçevesi ve hata analizi için referanstır. Veri kümesi nihai
ve temiz başarı iddiası için uygun değildir. Mevcut geliştirme geçmişi bu sınırı
değiştirmez.

## 2. Canonical production pipeline

Üretim için tek yürütme yolu şudur:

`Frontend Başlat → WebSocket start_run → CanonicalAnalysisJobService → run_video`

`run_video` içindeki canonical sıra şudur:

`video → candidate screening → D-FINE perception/context → keyframe selection → Qwen VLM → frame_id/timestamp grounding → EvidenceValidator → fail-closed policy → Ledger → evidence-gated incident second pass → WebSocket Timeline → LangGraph`

REST ve WebSocket aynı `CanonicalAnalysisJobService` örneğini kullanır. Aynı
video, feed ve etkin yapılandırma için tek görev oluşur. `analysis_id` ile
`run_id` aynıdır. Frontend üretim deneyiminde tek analiz eylemi `Başlat`tır.
`Durdur`, aynı servisteki etkin görevleri iptal eder.

## 3. Bileşen → yayımlanmış veya doğrulanmış referans gerekçesi

| Bileşen | Dayanak | Karar |
|---|---|---|
| Güvenlik kamerası anomali problemi | UCF-Crime, Sultani ve diğerleri, CVPR 2018; uzun ve kesilmemiş gözetim videosunda zamansal olay problemi | `KEEP` |
| SigLIP-2 anlamsal screening | SigLIP, ICCV 2023; Open-Vocabulary VAD, CVPR 2024; Dörtgöz screening kampanyası | `KEEP_AND_MEASURE` |
| D-FINE algı ve kişi kurtarması | D-FINE, ICLR 2025; hızlı nesne tespiti ve konumlama | `KEEP` |
| Puanlı sparse keyframe seçimi | Holmes-VAU ve Adaptive Keyframe Sampling, CVPR 2025; anomali ve kapsama odaklı örnekleme | `KEEP` |
| Yerel Qwen video yorumu | LAVAD, CVPR 2024; uzun video için VLM tabanlı betimleme ve zamansal birleştirme ilkesi | `KEEP` |
| Açık `frame_id` ve timestamp | NumPro ve Seq2Time, CVPR 2025; açık kare kimliği ve zaman temsilinin temporal grounding değeri | `KEEP` |
| GBNF ve Pydantic çıktı sözleşmesi | Grammar-Constrained Decoding, EMNLP 2023; PICARD, EMNLP 2021 | `KEEP` |
| Ledger ve olay-geneli ikinci geçiş | VideoMindPalace ve ReVisionLLM, CVPR 2025; uzun video belleği ve coarse-to-fine tekrar inceleme ilkesi | `KEEP` |
| LangGraph araç döngüsü | ReAct, ICLR 2023; akıl yürütme ile araç eylemini ardışık bir döngüde birleştirme | `KEEP` |
| EvidenceValidator ve fail-closed kabul | Ex-VAD, ICML 2025 ve active evidence-seeking çalışmaları görsel kanıt ilkesini destekler. Exact validator kuralları Dörtgöz güvenilirlik mühendisliğidir. | `KEEP` |
| Canonical job ve single-flight | Doğrudan duplicate GPU işi, feed çakışması ve görev yaşam döngüsü risklerini kapatır. Akademik iddia değildir. | `KEEP` |
| Yerel prosedür erişimi | RAG, NeurIPS 2020 yalnız kaynaklı retrieval ilkesini destekler. Yetkili corpus olmadan ürün değeri yoktur. | `DEFER` |

Referanslar exact Dörtgöz eşiklerini, istemlerini veya politika kurallarını
kanıtlamaz. Bu ayarlar proje içi mühendislik kararlarıdır.

## 4. Bileşen → Dörtgöz gereksinimi

| Bileşen | Dörtgöz gereksinimi | Mevcut durum |
|---|---|---|
| Candidate screening | Pahalı VLM çağrılarını azaltırken kritik olayları kaçırmamak | Motion fallback vardır. Hash doğrulamalı SigLIP-2 opt-in çalışır. Gerçek VLM atlama oranı ayrıca ölçülür. |
| D-FINE | Low-motion insan olaylarında screening körlüğünü azaltmak ve VLM'ye sayısal bağlam vermek | CPU/ONNX yolunda kişi-temelli rescue ve context sağlar. |
| Keyframe seçimi | Sınırlı kare bütçesinde olay ve zaman kapsamasını korumak | Puanlı, zamana yayılmış ve dedup uygulanmış sparse kareler kullanılır. |
| Qwen + GBNF | Türkçe yorum ve şema-geçerli `WindowReport` üretmek | OpenAI-uyumlu uç kullanılır; üretim çıkarımı yarışma altyapısındaki EVREN (vLLM) servisindedir. Kısa normal dal korunur. |
| Frame/time grounding | Her iddiayı modele gerçekten gösterilen kare ve video zamanına bağlamak | Deterministik `f_000...` kimlikleri, üç ondalıklı timestamp ve whitelist guard vardır. |
| Transient evidence | Validator için gerçek JPEG ve SHA sağlamak, kanıt dosyasını kalıcı gereksinime çevirmemek | UUID namespace, atomik yazma, SHA kontrolü ve her çıkışta cleanup vardır. Operasyon hatası `UNDETERMINED` olur. |
| EvidenceValidator + policy | Teknik olarak geçersiz kanıtın incident durumunu değiştirmesini engellemek | `schema_valid`, `timestamps_valid` ve `evidence_valid` zorunludur. `HUMAN_REVIEW` tek başına kabul sağlamaz. |
| Ledger | Pencere parçalarını tek olay sürekliliğinde tutmak | Fail-closed provisional admission, quiet grace ve sticky `needs_review` uygulanır. |
| İkinci geçiş | Kapanan olayı daha geniş bağlamda bütünlemek | Aynı VLM yolu kullanılır. Geçersiz kanıt `apply_review` çağrısını engeller. VLM risk ipucu final risk olmaz. |
| CanonicalAnalysisJobService | REST ve WebSocket için tek görev sahibi, exact-once başlatma ve güvenli iptal | Single-flight, feed conflict, terminal durum ve fatal task politikası uygulanır. |
| Frontend + WS | Operatöre tek ve anlaşılır analiz deneyimi sunmak | Tek `Başlat` ve `Durdur` vardır. Timeline, IncidentUpdate, AgentTrace, video ve demo akışı korunur. |
| LangGraph | Operatörün canlı Ledger bağlamında arama, vurgulama, yeniden inceleme ve dış aksiyon taslağı araçlarını kullanması | WS chat üzerinden ReAct araç döngüsü çalışır. Dış aksiyonlar kanıt kapısından ve insan onayından sonra yalnız yerel taslak üretir. |

## 5. Bilinen ölçülmüş hatalar

- Screening örnek kapsamı, VLM çağrı tasarrufu değildir. Semantic-on soak
  çalışmasında D-FINE rescue, olası tasarrufun büyük kısmını geri açtı. Net
  gerçek VLM pencere atlaması yaklaşık yüzde 2 kaldı.
- Temporal CNN held-out olay aralıklarını öğrenemedi. Düzeltilmiş eğitimde
  interval recall 0,077 oldu. Temporal CNN üretim bileşeni değildir.
- Uyarlanabilir eşik mevcut ölçümlerde yakalamayı düşürdü. Varsayılan kapalıdır.
- Shoplifting/Stealing zayıftır. Önceki temporal değerlendirmede Shoplifting
  aralıkları 0/25 yakalandı. Sorun çözülmüş sayılmaz.
- Motion-only hard gate, hareketsiz veya düşük hareketli insan olaylarını
  kaçırabilir. D-FINE kişi rescue bu nedenle kalır.
- Çok-slot VLM çalıştırması sınır olaylarda karar varyansı üretti. Üretim profili
  belirleyicilik için tek slotta kalır.
- Prosedür corpus'u dar bir demo setidir (`data/procedures/`). Sistem corpus
  dışı prosedür aksiyonu uyduramaz.
- Bu bilgisayarda gerçek GPU/VLM entegrasyon testi yapılmaz.

## 6. Ertelenen ve production dışı bileşenler

- `TemporalCnnCandidateModel` eğitim ve karşılaştırma kodudur. Production
  manifesti olarak seçilmez.
- Uyarlanabilir candidate threshold varsayılan kapalıdır.
- `MockVerticalAnalysisService`, `EventOrchestrator` ve `LocalVlmAgentTools`
  canonical production start yolunda çalışmaz. Test ve uyumluluk amacıyla kalır.
- `DORTGOZ_MOCK=1` yalnız arayüz test akışını çalıştırır. Bu kip video analizi veya
  performans kanıtı değildir. Gerçek analiz `DORTGOZ_MOCK=0` ile çalışır. Dış
  aksiyonlar iki kipte de dış sisteme gönderim yapmaz.
- Legacy REST sonuç uçları silindi (2026-08-20): `/api/events/*`, `/api/reports/*`,
  `/api/system/metrics`, `/api/analyses/{id}/events`, `/api/analyses/{id}/query`.
  Bunları hiçbir frontend, test veya belge çağırmıyordu. `EventMemoryService` ve
  `repositories/protocols.py` bu uçlarla birlikte kalktı. Router'daki legacy
  `_run_analysis` yardımcısı da silindi: canonical yol `CanonicalAnalysisJobService`.
- Kalan REST yüzeyi: `POST /api/videos` (yükleme, frontend kullanır) ve analiz
  yaşam döngüsü (`/analyze`, `/status`, `/cancel`). Yaşam döngüsü uçlarını üretim
  frontend'i çağırmaz; bölüm 2'deki "REST ve WS aynı görev sahibini kullanır"
  özelliğini kanıtladıkları için korunur.
- SQLite/repository, kalıcı canonical event kaynağı olarak henüz gerekli değildir.
  JSONL runner kaydı ve canlı Ledger mevcut üretim ihtiyacını karşılar.
  `InMemoryEventRepository` video kaydı için canlı yolda kalır.
- `agent/policy.py` (P00–P47 kural kataloğu) ve `agent/orchestrator.py` production
  yolunda çalışmaz ama silinmez: P13/P20 gibi fail-closed kuralların tek
  uygulamasıdır ve bölüm 4 bunlara atıf yapar. `services/runtime_policy.py`
  canonical yolun kendi kararını verir ve numaralı kuralları içermez.
- Runtime risk adapterı yalnız provisional/review/undetermined koruması sağlar.
  Yeni risk kalibrasyonu ölçülmüş ihtiyaç oluşana kadar ertelenir.
- Procedure RAG demo corpus ile çalışır; corpus genişletme ertelenir.
- Grounding ablation, anotasyon metodolojisi, sample-size planı, final holdout
  ve yayın provenance çalışmaları production tesliminin blocker'ı değildir.

## 7. Geliştirme kuralları

- Üretim kararı referans, doğrudan gereksinim, ölçülmüş hata veya kritik
  güvenilirlik gerekçesi taşır.
- Test, integration, functional, latency ve GPU memory çalışmaları proje
  doğrulamasıdır. Ablation yalnız gerçek bir mimari karar gerekiyorsa yapılır.
- Bir değişiklik için en çok bir tasarım incelemesi, implementation testleri ve
  bir post-implementation audit yapılır. HIGH veya BLOCKER yoksa iş kapanır.
- Canonical taxonomy, frame/time grounding, EvidenceValidator, transient
  evidence, fail-closed Ledger, `HUMAN_REVIEW` kapısı, ikinci geçiş kanıt kapısı,
  canonical job servisi ve single-flight korunur.
- WS wire contract değişirse backend ve frontend aynaları birlikte değişir.
- Bağımlılıklar OSI onaylı açık kaynak lisans taşır; AGPL/SSPL yasaktır ve
  release kapısı güçlü copyleft'i ayrıca engeller. Bulut API ve ücretli servis
  kullanılmaz. Veri kümesi videosu repoya eklenmez.
- `OFFICIAL_TEST_STATUS = FULLY_CONTAMINATED_FOR_DORTGOZ_DEVELOPMENT` ve
  `TEAM_DECLARATION_STATUS = DEFERRED_MANUAL_REVIEW` korunur. Bu durum yalnız
  temiz final accuracy iddiasını engeller.
- Bu makinede kod, unit, integration, dry-run, manifest ve config kontrolü yapılır.
  Gerçek inference GPU makinesinde yapılır.
- Commit küçük, testli ve geri alınabilir olur. Push için insan onayı gerekir.

## 8. Güncel sonraki adımlar

1. Gerçek GPU ortamında competition-real preflight ve kısa smoke çalıştır.
2. Gerçek SigLIP/motion screening, D-FINE ve Qwen zincirini tek videoda uçtan uca doğrula.
3. Uçtan uca latency, throughput, GPU memory ve gerçek VLM invocation sayısını kaydet.
4. Shoplifting/Stealing kaçışlarını kare, screening, D-FINE ve VLM aşamalarına ayır.
5. Yalnız ölçülen darboğaz varsa screening/keyframe ayarı yap. Persistence, yeni risk kuralı veya prosedür retrieval işini gerçek ihtiyaç oluşana kadar açma.

Patch C tamamlanmıştır. Final frontend/backend entegrasyon denetimi geçmiştir.
`RUNTIME_CONSOLIDATION = DONE`.
