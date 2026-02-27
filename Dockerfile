# ══════════════════════════════════════════════════════════════════════════════
# Shrync v0.03 — Universele image (CPU + Nvidia GPU auto-detectie)
# ══════════════════════════════════════════════════════════════════════════════
#
# ffmpeg 6.1 (CUDA 11.8) — werkt met Nvidia driver 450+ op Unraid
# Eén image voor iedereen. GPU wordt automatisch gedetecteerd bij opstarten.

FROM ubuntu:22.04

LABEL org.opencontainers.image.title="Shrync"
LABEL org.opencontainers.image.description="Zelf-gehoste H.265 media converter — automatische GPU-detectie"
LABEL org.opencontainers.image.version="0.03"
LABEL org.opencontainers.image.authors="timmexk"
LABEL org.opencontainers.image.source="https://github.com/timmexk/Shrync"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV GPU_MODE=
ENV CACHE_DIR=
ENV SHRYNC_VERSION=0.03

# ── Systeem dependencies ──────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    xz-utils \
    curl \
    ca-certificates \
    python3 \
    python3-pip \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# ── ffmpeg 6.1 statische build (CUDA 11.8 — driver 450+) ─────────────────────
# ffmpeg 6.1 is gecompileerd tegen CUDA 11.8 en werkt met Nvidia driver 450+.
# De 'latest' BtbN build vereist driver 570+ — te nieuw voor veel Unraid setups.
# Bevat: hevc_nvenc, h264_nvenc, libx265, libx264
RUN curl -fsSL \
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2024-01-31-12-55/ffmpeg-n6.1.1-26-g3e8b2b4-linux64-gpl.tar.xz" \
    -o /tmp/ffmpeg.tar.xz \
    && tar -xf /tmp/ffmpeg.tar.xz -C /tmp \
    && mv /tmp/ffmpeg-n6.1.1-26-g3e8b2b4-linux64-gpl/bin/ffmpeg /usr/local/bin/ffmpeg \
    && mv /tmp/ffmpeg-n6.1.1-26-g3e8b2b4-linux64-gpl/bin/ffprobe /usr/local/bin/ffprobe \
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
