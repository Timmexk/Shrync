# ══════════════════════════════════════════════════════════════════════════════
# Shrync v0.01 — Universele image (één image voor CPU én Nvidia GPU)
# ══════════════════════════════════════════════════════════════════════════════
#
# Werking:
#   - CPU-gebruikers: gewoon starten, geen extra opties nodig
#   - GPU-gebruikers: --runtime=nvidia toevoegen, de rest gaat automatisch
#
# De entrypoint detecteert bij elke start of een GPU aanwezig is en stelt
# GPU_MODE automatisch in. Gebruiker hoeft nooit van image te wisselen.
#
# ffmpeg wordt geïnstalleerd als statische build van BtbN (met NVENC-support).
# Op CPU-systemen gebruikt ffmpeg gewoon libx265/libx264 zonder GPU.

FROM ubuntu:22.04

LABEL org.opencontainers.image.title="Shrync"
LABEL org.opencontainers.image.description="Zelf-gehoste H.265 media converter — automatische GPU-detectie"
LABEL org.opencontainers.image.version="0.01"
LABEL org.opencontainers.image.authors="JOUWGITHUBUSERNAME"
LABEL org.opencontainers.image.source="https://github.com/JOUWGITHUBUSERNAME/shrync"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# GPU_MODE: leeg = auto-detectie (aanbevolen voor alle gebruikers)
#   leeg   → entrypoint detecteert en stelt 'nvidia' of 'cpu' in
#   'cpu'  → forceer CPU, ook als GPU aanwezig is
# Stel dit NOOIT handmatig op 'nvidia' in — laat de auto-detectie het doen.
ENV GPU_MODE=

# CACHE_DIR: tijdelijk bestand tijdens conversie
# Leeg = naast het bronbestand. Stel in op /cache als je een snel SSD-pad wilt.
ENV CACHE_DIR=

ENV SHRYNC_VERSION=0.01

# ── Systeem dependencies ──────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # ffmpeg dependencies en tools
    xz-utils \
    curl \
    ca-certificates \
    # Python
    python3 \
    python3-pip \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# ── ffmpeg statische build (met NVENC + libx265 + libx264) ───────────────────
# BtbN statische build bevat: hevc_nvenc, h264_nvenc, libx265, libx264
# Werkt op GPU én CPU — de NVENC encoders worden genegeerd zonder GPU-runtime.
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

# entrypoint.sh: GPU detecteren → GPU_MODE instellen → uvicorn starten
ENTRYPOINT ["/shrync/entrypoint.sh"]
