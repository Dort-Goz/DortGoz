"""Merkezî yapılandırma — tüm dış uçlar ve modlar tek yerden."""

from pathlib import Path

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
    window_seconds: float = 30.0
    keyframes_per_window: int = 6
    # Eleme eşiği: uyarlanabilir mod kameranın kendi gürültü tabanından türetir
    # (sabit küresel eşik farklı kameralara hizmet edemiyordu — 2026-08-03 ölçümü)
    motion_gate_adaptive: bool = True
    motion_gate: float = 0.004   # uyarlanabilir mod kapalıysa sabit eşik / taban
    interpret_max_tokens: int = 700
    # İki kademeli üretim (Cerberus deseni): olağan pencere tek cümle üretir,
    # tam rapor yalnız `dikkat` dalında. DORTGOZ_TWO_TIER=0 → eski tek şema (ablation)
    two_tier: bool = True

    # Yollar
    media_dir: Path = Path(__file__).resolve().parents[2] / "media"
    runs_dir: Path = Path(__file__).resolve().parents[2] / "runs"

    model_config = {
        "env_prefix": "DORTGOZ_",
        "env_file": str(Path(__file__).resolve().parents[2] / ".env"),
        "extra": "ignore",
    }


settings = Settings()
