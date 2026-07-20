# Lightweight Railway image — no Playwright browsers, single-process web.
FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MALLOC_ARENA_MAX=2

# ffmpeg: thumbnail + brand-scan visual (skip with --build-arg INSTALL_FFMPEG=0 for ultra-light)
ARG INSTALL_FFMPEG=1
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates \
  && if [ "$INSTALL_FFMPEG" = "1" ]; then \
       apt-get install -y --no-install-recommends ffmpeg; \
     fi \
  && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
  && find /usr/local/lib/python3.12 -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true \
  && find /usr/local/lib/python3.12 -type f -name '*.pyc' -delete 2>/dev/null || true

COPY . .

RUN mkdir -p data/cookies data/downloads \
  && chmod +x start.sh \
  && find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true \
  && find . -type f -name '*.pyc' -delete 2>/dev/null || true

EXPOSE 8080

# Single worker, low idle overhead (see start.sh)
CMD ["./start.sh"]
