# ══════════════════════════════════════════════════════════════════════════════
# Shrync v0.04 — Universele image (CPU + Nvidia GPU auto-detectie)
# ══════════════════════════════════════════════════════════════════════════════
#
# ffmpeg met NVENC ondersteuning — compatibel met Nvidia driver 570+
# Presets: slow/medium/fast — werkt op alle GPU generaties (Pascal t/m Lovelace)
# Eén image voor iedereen. GPU wordt automatisch gedetecteerd bij opstarten.

FROM ubuntu:22.04

LABEL org.opencontainers.image.title="Shrync"
LABEL org.opencontainers.image.description="Zelf-gehoste H.265 media converter — automatische GPU-detectie"
LABEL org.opencontainers.image.version="0.04"
LABEL org.opencontainers.image.authors="timmexk"
LABEL org.opencontainers.image.source="https://github.com/timmexk/Shrync"

# Build-time variabelen — niet zichtbaar als configuratie in Unraid CA
ARG SHRYNC_VERSION=0.04
ENV SHRYNC_VERSION=${SHRYNC_VERSION}
ENV PYTHONUNBUFFERED=1
# GPU_MODE en CACHE_DIR worden ingesteld via de Unraid template / docker-compose
ENV GPU_MODE=
ENV CACHE_DIR=

# ── Systeem dependencies ──────────────────────────────────────────────────────
RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y --no-install-recommends \
    xz-utils \
    curl \
    ca-certificates \
    python3 \
    python3-pip \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# ── ffmpeg statische build (GPL, met NVENC + libx265 + libx264) ───────────────
# Vereist Nvidia driver 570+ voor NVENC gebruik.
# Zonder GPU of oudere driver: automatische CPU fallback via entrypoint.
RUN curl -fsSL \
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz" \
    -o /tmp/ffmpeg.tar.xz \
    && tar -xf /tmp/ffmpeg.tar.xz -C /tmp \
    && mv /tmp/ffmpeg-master-latest-linux64-gpl/bin/ffmpeg /usr/local/bin/ffmpeg \
    && mv /tmp/ffmpeg-master-latest-linux64-gpl/bin/ffprobe /usr/local/bin/ffprobe \
    && chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe \
    && rm -rf /tmp/ffmpeg* \
    && ffmpeg -version | head -1

# ── Python dependencies ───────────────────────────────────────────────────────
WORKDIR /shrync

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# ── Applicatie ────────────────────────────────────────────────────────────────
COPY app/ ./app/
COPY templates/ ./templates/
COPY static/ ./static/
COPY entrypoint.sh .

RUN mkdir -p /config && chmod +x /shrync/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/shrync/entrypoint.sh"]
