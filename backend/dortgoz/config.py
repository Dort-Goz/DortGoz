"""Merkezî yapılandırma — tüm dış uçlar ve modlar tek yerden."""

# Bulut telemetrisi kapalı. `langsmith` langgraph/langchain zinciriyle geliyor;
# ortamda LANGSMITH_TRACING veya LANGCHAIN_TRACING_V2 açıksa kütüphane izleri
# bulut uç noktasına gönderir ve yarışmanın "bulut API yok" kuralı çiğnenir.
# Ortamı KASITLI olarak eziyoruz (setdefault değil) — dışarıdan gelen bir
# "true" değeri de kapatılmalıdır. Blok, langchain/langgraph içe aktarılmadan
# ÖNCE çalışsın diye dosyanın en üstündedir.
import os

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Model uçları (OpenAI-uyumlu)
    llama_base_url: str = "http://127.0.0.1:8080/v1"  # model sunucusu (ana VLM + ajan; eski proxy emekli 2026-08-03)
    vllm_base_url: str = "http://127.0.0.1:8001/v1"  # RTX 4060 vLLM (MiniCPM-V ön eleme)
    api_key: str = "local"

    main_model: str = "qwen3.6-35b-a3b-vision"
    triage_model: str = "minicpm-v-4.6"

    # Çalışma modu
    mock: bool = False  # DORTGOZ_MOCK=1 → model/GPU olmadan mock olay akışı
    mock_speed: float = 1.0  # mock yeniden oynatma hız çarpanı
    # development: eksik opsiyonel algı bileşenlerini açıkça raporlar, fakat
    # yerel geliştirmeyi engellemez. competition-real: hiçbir üretim bileşeni
    # eksikken analiz başlatmaz.
    deployment_profile: Literal["development", "competition-real"] = "development"

    # İşleme hattı
    base_fps: float = 1.0  # hareket profili tarama hızı
    window_seconds: float = 30.0  # dinamik modda ÜST sınır
    # Pencereler sabit ızgara yerine ETKİNLİĞE hizalansın (ölü bölge atlanır,
    # pencere olayın başladığı yerde açılır). ⚠ VARSAYILAN KAPALI — 2026-08-05
    # ölçümü: kazanç yok, hassasiyet düşüyor (bkz. project-state günlüğü).
    # Mekanizma doğru; darboğaz onu süren ETKİNLİK SİNYALİ (dedektör/SigLIP
    # kapısı geldiğinde yeniden ölç). DORTGOZ_DYNAMIC_WINDOWS=1 ile aç.
    dynamic_windows: bool = False
    window_min_seconds: float = 8.0  # tek saniyelik kıpırtı da bağlamıyla okunsun
    window_preroll: float = 3.0  # olayın açılışı kırpılmasın
    window_quiet_tail: float = 6.0  # bu kadar sessizlik pencereyi bitirir
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
    motion_gate: float = 0.004  # uyarlanabilir mod kapalıysa sabit eşik / taban
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
    # Terfi servisi bu manifesti atomik olarak yazar. Dosya yoksa sabit
    # DORTGOZ_DFINE_ONNX yolu kullanılır. Dosya varsa hash doğrulaması zorunludur.
    dfine_active_manifest: Path = (
        Path(__file__).resolve().parents[2] / "models" / "dfine" / "local" / "active_manifest.json"
    )
    dfine_workspace_root: Path = Path(__file__).resolve().parents[2]
    detector_conf: float = 0.40
    # Kurtarma kararının eşiği (yalnız-geri-çağırma OR kuralı) — meta sayıları
    # detector_conf'ta kalır. 2026-08-07 ölçümü: uzak plan/320×240 kaynakta
    # 0,40 gerçek kalabalıkları (13-29 kişi) tümüyle kaçırıyor, 0,15 hepsini
    # buluyor; kurtarma ucuz-yanlış-pozitife dayanıklı (maliyet yalnız fazladan
    # derin okuma), o yüzden düşük eşik güvenli.
    detector_rescue_conf: float = 0.25
    detector_samples: int = 4  # pencere başına örneklenen kare

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
    training_frame_width: int = 640
    incident_pre_capture_seconds: float = 8.0
    incident_post_capture_seconds: float = 8.0
    incident_clip_timeout_seconds: float = 90.0
    # Ağırlık nöbetçisi sayfa-düşürme hedefleri (":" ayraçlı GGUF yolları).
    # Boş bırakılırsa iyileşme yalnız /unload yapar (bkz. services/weight_guard).
    gguf_paths: str = ""
    # Eşzamanlı koşu sınırı: şartname senaryosu 24 kamera; +1 pay 5×5 canlı
    # ızgarayı (25 akış) karşılar. Prova ölçümü (2026-08-14): 24 akış 0,85×.
    max_feeds: int = 25
    # Canlı CCTV kipi (services/live_cctv): akış listesi JSON'u ve segmentleme.
    live_feeds_path: Path = Path(__file__).resolve().parents[2] / "config" / "live_feeds.json"
    live_segment_seconds: int = 30  # segment süresi = anlık görüntü tazeliği
    live_max_backlog: int = 2  # işlenmemiş segment sınırı — fazlası ATILIR (canlıya yetişme)
    # ge=1: 0 verilirse budama mantığı ters çalışır (son N dilimi boş kalır).
    live_keep_segments: int = Field(default=3, ge=1)  # saklanan son N işlenmiş segment
    live_keep_runs: int = Field(default=20, ge=1)  # akış başına saklanan son N koşu kaydı
    candidate_cache_dir: Path = Path(__file__).resolve().parents[2] / "cache" / "candidate"
    candidate_manifest_path: Path = (
        Path(__file__).resolve().parents[2] / "models" / "candidate" / "manifest.json"
    )
    # Varsayılan bellek adapter'ı test/mock sadeliği için süreç içidir. Docker/
    # offline dağıtım bu yolu ayarlayarak restart sonrası SQLite kalıcılığı açar.
    event_store_path: Path | None = None

    @property
    def runtime_profile(self) -> Literal["mock", "development", "competition-real"]:
        """Tek etkili profil; mock bayrağı gerçek çalışmayı her zaman bastırır."""

        return "mock" if self.mock else self.deployment_profile

    @field_validator("vlm_manifest_path", "event_store_path", mode="before")
    @classmethod
    def blank_path_is_unset(cls, value: object) -> object:
        """Compose'taki boş opsiyonel env değeri sahte ``Path('.')`` olmasın."""

        return None if value is None or value == "" else value

    @field_validator(
        "candidate_manifest_path",
        "dfine_active_manifest",
        "dfine_workspace_root",
        mode="after",
    )
    @classmethod
    def resolve_repository_path(cls, value: Path) -> Path:
        """Env'den gelen göreli repository yolunu çalışma köküne sabitler."""

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
