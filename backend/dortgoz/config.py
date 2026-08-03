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

    # Yollar
    media_dir: Path = Path(__file__).resolve().parents[2] / "media"
    runs_dir: Path = Path(__file__).resolve().parents[2] / "runs"

    model_config = {
        "env_prefix": "DORTGOZ_",
        "env_file": str(Path(__file__).resolve().parents[2] / ".env"),
        "extra": "ignore",
    }


settings = Settings()
