# ══════════════════════════════════════════════════════════════════════════════
# Shrync v0.09 — Multi-stage build op python:3.12-slim (Debian Bookworm)
# ══════════════════════════════════════════════════════════════════════════════
#
# Waarom python:3.12-slim?
#   - Kleiner dan Ubuntu (~45MB vs ~80MB base)
#   - Python correct ingebouwd — geen handmatige installatie nodig
#   - Officieel Docker-image, regelmatig gepatcht door Python-team
#   - Zelfde platform in builder én runtime → dependency-kopie werkt feilloos
#   - Geen onnodige system-pakketten zoals Ubuntu meebrengt

# ── Stage 1: Python dependencies bouwen ──────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

ENV PIP_NO_CACHE_DIR=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN pip install --upgrade pip setuptools wheel

COPY requirements.txt .
RUN pip install --target=/build/deps -r requirements.txt

# ── Stage 2: Runtime image ────────────────────────────────────────────────────
FROM python:3.12-slim

LABEL org.opencontainers.image.title="Shrync"
LABEL org.opencontainers.image.description="Zelf-gehoste H.265 media converter — automatische GPU-detectie"
LABEL org.opencontainers.image.version="0.09"
LABEL org.opencontainers.image.authors="timmexk"
LABEL org.opencontainers.image.source="https://github.com/timmexk/Shrync"

ARG SHRYNC_VERSION=0.09
ENV SHRYNC_VERSION=${SHRYNC_VERSION}
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app/deps
ENV PATH="/app/deps/bin:${PATH}"
ENV GPU_MODE=
ENV CACHE_DIR=

WORKDIR /app

# Minimale runtime-pakketten — alleen wat ffmpeg en nvidia-smi nodig hebben
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

# ── ffmpeg statische build (GPL, NVENC + libx265 + libx264) ──────────────────
# Statische binary — geen extra libraries nodig, werkt op elke Linux distro.
# Vereist Nvidia driver 570+ voor NVENC. Zonder GPU: automatische CPU fallback.
RUN curl -fsSL \
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz" \
    -o /tmp/ffmpeg.tar.xz \
    && tar -xf /tmp/ffmpeg.tar.xz -C /tmp \
    && mv /tmp/ffmpeg-master-latest-linux64-gpl/bin/ffmpeg /usr/local/bin/ffmpeg \
    && mv /tmp/ffmpeg-master-latest-linux64-gpl/bin/ffprobe /usr/local/bin/ffprobe \
    && chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe \
    && rm -rf /tmp/ffmpeg* \
    && ffmpeg -version | head -1

# ── Python dependencies vanuit builder ───────────────────────────────────────
COPY --from=builder /build/deps ./deps

# ── Applicatie ────────────────────────────────────────────────────────────────
COPY app/ ./app/
COPY templates/ ./templates/
COPY static/ ./static/
COPY entrypoint.sh ./entrypoint.sh

# ── Non-root gebruiker ────────────────────────────────────────────────────────
RUN useradd -m -u 1000 -s /bin/bash shrync \
    && mkdir -p /config /cache /media \
    && chmod 777 /config /cache /media \
    && chmod +x /app/entrypoint.sh

USER shrync

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
