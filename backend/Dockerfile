# syntax=docker/dockerfile:1
# Images are built before air-gapping, then saved by scripts/offline_bundle.py.
FROM python:3.12-slim

ARG UV_VERSION=0.12.1
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_MANAGED_PYTHON=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /usr/sbin/nologin app

WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock ./
RUN python -m pip install --no-cache-dir "uv==${UV_VERSION}" \
    && uv sync --frozen --no-dev --no-install-project

COPY backend/dortgoz ./dortgoz
RUN uv sync --frozen --no-dev

COPY configs /app/configs
# services/live_cctv canlı akış listesini /app/config altından okur; bu dizin
# olmadan /api/live/start konteynerde FileNotFoundError verir. Gerçek liste
# çalışma anında compose bind mount'u ile üzerine bindirilir.
COPY config /app/config
COPY data/procedures /app/data/procedures
COPY models /app/models
RUN mkdir -p /app/media /app/runs /app/cache/candidate \
    && chown -R app:app /app/media /app/runs /app/cache

USER app

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["uv", "run", "--no-sync", "uvicorn", "dortgoz.main:app", "--host", "0.0.0.0", "--port", "8000"]
