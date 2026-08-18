"""Merkezî yapılandırma — tüm dış uçlar ve modlar tek yerden."""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Model uçları (OpenAI-uyumlu)
    llama_base_url: str = "http://127.0.0.1:8080/v1"   # model sunucusu (ana VLM + ajan; eski proxy emekli 2026-08-03)
    vllm_base_url: str = "http://127.0.0.1:8001/v1"    # RTX 4060 vLLM (MiniCPM-V ön eleme)
    api_key: str = "local"

    main_model: str = "qwen3.6-35b-a3b-vision"
    triage_model: str = "minicpm-v-4.6"

    # Çalışma modu
    mock: bool = False           # DORTGOZ_MOCK=1 → model/GPU olmadan mock olay akışı
    mock_speed: float = 1.0      # mock yeniden oynatma hız çarpanı

    # İşleme hattı
    base_fps: float = 1.0        # hareket profili tarama hızı
    window_seconds: float = 30.0        # dinamik modda ÜST sınır
    # Pencereler sabit ızgara yerine ETKİNLİĞE hizalansın (ölü bölge atlanır,
    # pencere olayın başladığı yerde açılır). ⚠ VARSAYILAN KAPALI — 2026-08-05
    # ölçümü: kazanç yok, hassasiyet düşüyor (bkz. project-state günlüğü).
    # Mekanizma doğru; darboğaz onu süren ETKİNLİK SİNYALİ (dedektör/SigLIP
    # kapısı geldiğinde yeniden ölç). DORTGOZ_DYNAMIC_WINDOWS=1 ile aç.
    dynamic_windows: bool = False
    window_min_seconds: float = 8.0     # tek saniyelik kıpırtı da bağlamıyla okunsun
    window_preroll: float = 3.0         # olayın açılışı kırpılmasın
    window_quiet_tail: float = 6.0      # bu kadar sessizlik pencereyi bitirir
    keyframes_per_window: int = 6
    # Kare tekilleştirme: seçilen karelerin 64×48 gri profilde DEĞİŞEN-PİKSEL
    # ORANI bu eşiğin altındaysa kopya sayılıp gönderilmez (0 = kapalı).
    # Kodlama seri ve 1 numaralı ölçek darboğazı — durağan pencere 6 yerine
    # 2 kare gönderir. Ölçülen bant (2026-08-07): gerçekten durağan sahne
    # 0,001-0,005 · aynı sahnede olay ≥0,010 · canlı sokak 0,12+ (elenmez).
    keyframe_dedup: float = 0.008
    # Eleme eşiği: uyarlanabilir mod kameranın kendi gürültü tabanından türetir
    # (sabit küresel eşik farklı kameralara hizmet edemiyordu — 2026-08-03 ölçümü)
    motion_gate_adaptive: bool = True
    motion_gate: float = 0.004   # uyarlanabilir mod kapalıysa sabit eşik / taban
    # 2026-08-05: 700 → 1400. Olay yoğun pencerede 700 JSON'ı ortadan kesiyordu
    # (bir koşu 19. dakikada bu yüzden düştü). İki kademeli üretimde olağan
    # pencere zaten erken duruyor, yani yüksek tavanın maliyeti YALNIZ gerçek
    # olaylı pencerelerde ödenir — kesilme riskini almaya değmez.
    interpret_max_tokens: int = 1400
    # İki kademeli üretim (Cerberus deseni): olağan pencere tek cümle üretir,
    # tam rapor yalnız `dikkat` dalında. DORTGOZ_TWO_TIER=0 → eski tek şema (ablation)
    two_tier: bool = True
    # Süregelen olayın bağlamını sonraki pencereye taşı (olay bölünmesini önler)
    carry_context: bool = True
    # Olay kapanınca tüm aralığı tek bağlamda yeniden oku (bütünlüklü anlatı)
    incident_review: bool = True
    # Olayı kapatmadan önce tolere edilen sessiz pencere sayısı (0 = eski davranış)
    incident_grace_windows: int = 1
    # Sınırda pencere tırmandırması: verdikt `olagan` ama dal token'ın ham
    # P(dikkat) kütlesi (gramer maskesi ÖNCESİ model inancı) eşiği aşarsa BİR
    # düşünmeli yeniden sorgu yapılır. 0 = kapalı (ablation). 2026-08-06 ölçümü
    # (71 klip alt kümesi): eşik 0,10'da pencerelerin ~%4'ü tırmanıyor,
    # klip yakalama +1 / yanlış alarm +0; kurtarılanlar GT-ilişkiliydi.
    escalate_p: float = 0.10
    # İkinci görüş (çapraz model, 2026-08-15 A/B): birincil model olaysız
    # bıraktı VE pencere hareketi eşiği aştıysa pencere BİR kez de ikinci
    # modele okutulur. İki modelin kör noktaları tamamlayıcı ölçüldü (31 klip:
    # 27B, 35B'nin olaysız 8 penceresinde orta+ buldu; tersi 2) — motion>=0,30
    # kapısıyla klip yakalama 13/26 → 19/26, FA +0. Model değişimi model sunucusu
    # yeniden yüklemesidir; canlı düzende kuyrukla amorti edilir. "" = KAPALI.
    # Ölçüm: bench/results/ab_qwen38_rol_analizi_20260815.md
    second_opinion_model: str = ""   # ör. "qwen3.8-27b-vision-dg"
    # 0,30 → 0,40 (2026-08-18, 3 replika üzerinde kural taraması): yakalama bandı
    # AYNI (105-108/140), yanlış alarm 16-19 → 15-18, tırmandırılan pencere %24 az.
    # Kesin iyileşme. 0,50 maliyeti %38 azaltıyor ama bandın altı 104'e düşüyor.
    second_opinion_motion: float = 0.40
    # Düşünme kademesi (Qwen3.8 ailesi `reasoning_effort` bekler; Qwen3.6
    # ikili `enable_thinking` konuşur — çeviri pipeline/thinking.py'de).
    # "" = aile varsayılanı (üretim davranışı, düşünme kapalı okuma),
    # "kapali" = açıkça kapalı, ya da low|medium|high|xhigh.
    # ⚠ Kademe yükseldikçe düşünme token tavanını yeme riski artar; bütçe
    # (interpret_think_budget) bu yüzden kademeli modda da uygulanır.
    interpret_effort: str = ""
    second_opinion_effort: str = ""
    # Ajan/diyalog katmanı ayrı model kullanabilir. "" = main_model (A7 kuralı:
    # tek yüklü örnek — ayrı profil model sunucusu YENİDEN YÜKLEMESİ demektir ve
    # canlı koşuda görü modelini tahliye eder; yalnız çevrimdışı/demo dışı
    # kullanım için doldurulmalı).
    agent_model: str = ""
    # Ajan düşünme kademesi. Düşünme burada TARİHSEL olarak kapalıydı: bütçesiz
    # düşünme 700 token tavanının tamamını reasoning_content'e harcayıp content'i
    # BOŞ bırakıyordu (operatör boş yanıt görüyordu). Sebep kademe değil BÜTÇESİZLİK
    # — aynı arıza ev tarafında MMLU-Pro @xhigh koşusunda da ölçüldü. Bütçe +
    # yükseltilmiş tavanla düşünme güvenle açılabilir. "" = kapalı (üretim).
    agent_effort: str = ""
    # Ajanın düşünme bütçesi — yanıt için token BIRAKMALI (tavan 2200).
    agent_think_budget: int = 1200
    # Düşünme token bütçesi — 2026-08-07 soak ölçümüyle 2500 (bkz. thinking.py)
    interpret_think_budget: int = 2500
    # Düşünen okumada örnekleme sıcaklığı. Yorumlama yolu üretimde AÇGÖZLÜ
    # (temperature=0) koşar; şema-kısıtlı kısa rapor için doğrudur. Düşünme
    # açıkken açgözlü kod çözme Qwen akıl yürütme kiplerinde tekrar/döngü
    # riski taşır (üretici kendi kartında thinking için temp>0 + top_p 0,95
    # öneriyor). 0 = değiştirme (üretim davranışı); >0 YALNIZ düşünen çağrıda
    # uygulanır, düşünmeyen okuma açgözlü kalır.
    interpret_think_temp: float = 0.0
    # Çift okuma (2026-08-11 birleşim analizi): olagan kalan pencere bir kez de
    # 12 motion-ranked kareyle okunur; İKİ okumadan biri olay görürse alarm.
    # k6∪k12 birleşimi 99→112/140 yakalama vaat ediyor (FA 12→~21, maliyet ~2×)
    # — max-recall kipi; varsayılan KAPALI, tam-bölme ölçümüyle karar verilir.
    dual_read: bool = False
    # Son tarama (2026-08-12 ölçümü): hiç olay açılmamış koşuya TEK 16-karelik
    # tam-video bakışı. 30 sn pencerelerin yapısal göremediği "alıp-götürme"
    # yayını yakalar (28 kaçırmadan 4 açıldı; temiz normallerde yeni FA yok).
    # Maliyet yalnız olaysız koşuda 1 çağrı (~%3-4).
    final_sweep: bool = False
    # Süreç genelinde aynı anda en çok bu kadar VLM çağrısı uçuşta olur —
    # 24 akış sunucuya 24 istek yığınca model sunucusu 429 döndürüyor ve pencereler
    # atlanıyordu (2026-08-06 canlı). Kendi kendini kısıtlamak + 429'da geri
    # çekilip YENİDEN denemek doğru davranış: sistem yavaşlar, görüntü atlamaz.
    max_inflight: int = 8
    llm_retries: int = 6

    # Algı katmanı — D-FINE (ONNX, CPU). Ağırlık repo dışında; yol makineye
    # göre değişir (scripts/fetch_models.sh indirir). detector_enabled=0 →
    # dedektörsüz eski davranış (ablation / ağırlıksız kurulum).
    detector_enabled: bool = True
    dfine_onnx: str = "~/.cache/dortgoz/dfine/model.onnx"
    detector_conf: float = 0.40
    # Kurtarma kararının eşiği (yalnız-geri-çağırma OR kuralı) — meta sayıları
    # detector_conf'ta kalır. 2026-08-07 ölçümü: uzak plan/320×240 kaynakta
    # 0,40 gerçek kalabalıkları (13-29 kişi) tümüyle kaçırıyor, 0,15 hepsini
    # buluyor; kurtarma ucuz-yanlış-pozitife dayanıklı (maliyet yalnız fazladan
    # derin okuma), o yüzden düşük eşik güvenli.
    detector_rescue_conf: float = 0.25
    detector_samples: int = 4        # pencere başına örneklenen kare

    # Yeni kontrollü agent dikeyinin güvenlik sınırları. Bunlar policy içinde
    # sabit yazılmaz; DORTGOZ_* ortam değişkenleriyle kontrollü değiştirilebilir.
    video_max_bytes: int = 2 * 1024 * 1024 * 1024
    max_agent_steps: int = 14
    max_vlm_attempts: int = 2
    max_context_expansions: int = 1
    max_dense_analyses: int = 1
    quality_min: float = 0.35
    medium_candidate_score: float = 0.45
    high_candidate_score: float = 0.70
    critical_candidate_score: float = 0.85
    cv_only_confidence: float = 0.92
    vlm_confirm_confidence: float = 0.80
    vlm_reject_confidence: float = 0.80

    # Görev 07 candidate interval baseline. Learned model geldiğinde aynı
    # threshold sözleşmesi validation/calibration çıktısından beslenir.
    # HİBRİT (2026-08-07): Bengisu'nun aday-aralık screening'i ana koşucuda
    # ön-kapı olarak çalışır — aday KAPSAMAYAN pencere derin okunmaz; dedektör
    # kurtarması (rescue_persons) hareket-görünmez sınıf için emniyet ağı kalır.
    # Ölçülen taban (5 soak feed): varsayılan eşikte GT recall 19/19, kapsama
    # %67,9 ⇒ ~%32 VLM tasarrufu. 0 = kapalı (eski davranış).
    candidate_screening: bool = True
    # Screening skorlayıcısı: boş = MotionBaselineModel (el-ayarı; ölçüldü:
    # soak GT 19/19 @ %67,9 kapsama, held-out UCF aralık olay-recall'u 0,846).
    # Eğitilmiş temporal CNN DENENDİ ve dürüst kapı REDDETTİ (2026-08-07):
    # düzeltilmiş eğiticiyle soak feed'lerinde 19/19 @ %47 görünse de held-out
    # kliplerde aralık olay-recall'u 0,077 — model olayı değil YOĞUNLUĞU
    # öğreniyor; feed sonucu olayların hareketli kesitlere gömülü olmasının
    # artefaktı. Kapıyı (0.95 aralık-recall) geçen bir model gelirse manifest
    # yolu buraya verilir; yükleme hash-doğrulamalı (load_candidate_scorer).
    candidate_model_manifest: str = ""
    candidate_start_threshold: float = 0.65
    candidate_continue_threshold: float = 0.40
    candidate_end_patience: int = 3
    # Per-kamera nedensel eşik adaptasyonu (doygunluk politikası): geçmişte
    # skor doyuran kamera yeni aralık barını yükseltir. Semantic scorer'la
    # ölçüldü (2026-08-08, iki alanda tam recall); taban scorer'da doğrulanmadı
    # — varsayılan kapalı, semantic ile birlikte açılması önerilir.
    candidate_adaptive_threshold: bool = False
    candidate_adaptive_saturation: float = 0.95
    candidate_adaptive_raised: float = 0.85
    candidate_merge_gap_seconds: float = 2.0
    candidate_min_duration_seconds: float = 0.5
    candidate_threshold_version: str = "candidate-thresholds-v1"

    # Görev 08: gerçek VLM yalnız explicit, hash-doğrulanmış local manifest ile
    # açılır. Mock modda bu profil isteği reddedilir.
    vlm_manifest_path: Path | None = None
    vlm_timeout_seconds: float = 90.0
    vlm_context_clip_timeout_seconds: float = 90.0
    vlm_context_before_seconds: float = 8.0
    vlm_context_after_seconds: float = 8.0

    # Yollar
    media_dir: Path = Path(__file__).resolve().parents[2] / "media"
    runs_dir: Path = Path(__file__).resolve().parents[2] / "runs"
    # Ağırlık nöbetçisi sayfa-düşürme hedefleri (":" ayraçlı GGUF yolları).
    # Boş bırakılırsa iyileşme yalnız /unload yapar (bkz. services/weight_guard).
    gguf_paths: str = ""
    # Eşzamanlı koşu sınırı: şartname senaryosu 24 kamera; +1 pay 5×5 canlı
    # ızgarayı (25 akış) karşılar. Prova ölçümü (2026-08-14): 24 akış 0,85×.
    max_feeds: int = 25
    # Canlı CCTV kipi (services/live_cctv): akış listesi JSON'u ve segmentleme.
    live_feeds_path: Path = Path(__file__).resolve().parents[2] / "config" / "live_feeds.json"
    live_segment_seconds: int = 30    # segment süresi = anlık görüntü tazeliği
    live_max_backlog: int = 2         # işlenmemiş segment sınırı — fazlası ATILIR (canlıya yetişme)
    live_keep_segments: int = 3       # işlenmiş segmentten saklanan son N (hata ayıklama)
    live_keep_runs: int = 20          # akış başına saklanan son N segment koşu kaydı
    candidate_cache_dir: Path = Path(__file__).resolve().parents[2] / "cache" / "candidate"
    candidate_manifest_path: Path = (
        Path(__file__).resolve().parents[2] / "models" / "candidate" / "manifest.json"
    )
    # Varsayılan bellek adapter'ı test/mock sadeliği için süreç içidir. Docker/
    # offline dağıtım bu yolu ayarlayarak restart sonrası SQLite kalıcılığı açar.
    event_store_path: Path | None = None

    @field_validator("vlm_manifest_path", "event_store_path", mode="before")
    @classmethod
    def blank_path_is_unset(cls, value: object) -> object:
        """Compose'taki boş opsiyonel env değeri sahte ``Path('.')`` olmasın."""

        return None if value is None or value == "" else value

    @field_validator("candidate_manifest_path", mode="after")
    @classmethod
    def resolve_candidate_manifest_path(cls, value: Path) -> Path:
        """Env'den gelen göreli manifest yolunu repository köküne sabitler."""

        if value.is_absolute():
            return value.resolve()
        return (Path(__file__).resolve().parents[2] / value).resolve()

    @field_validator("candidate_model_manifest", mode="after")
    @classmethod
    def resolve_candidate_model_manifest(cls, value: str) -> str:
        """Göreli scorer-manifest yolu CWD'ye değil repo köküne göre çözülür.

        (2026-08-08 soak bulgusu: backend/ CWD'sinden başlatılan süreçte
        .env'deki göreli yol yüklenemiyor, koşu sessizce tabana düşüyordu.)
        """

        if not value or Path(value).is_absolute():
            return value
        return str((Path(__file__).resolve().parents[2] / value).resolve())

    @field_validator("vlm_manifest_path", mode="after")
    @classmethod
    def resolve_vlm_manifest_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if value.is_absolute():
            return value.resolve()
        return (Path(__file__).resolve().parents[2] / value).resolve()

    @field_validator("event_store_path", mode="after")
    @classmethod
    def resolve_event_store_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if value.is_absolute():
            return value.resolve()
        return (Path(__file__).resolve().parents[2] / value).resolve()

    model_config = {
        "env_prefix": "DORTGOZ_",
        "env_file": str(Path(__file__).resolve().parents[2] / ".env"),
        "extra": "ignore",
    }


settings = Settings()
