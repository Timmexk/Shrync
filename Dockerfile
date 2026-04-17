# ══════════════════════════════════════════════════════════════════════════════
# Shrync v0.54 — Universele image (één image voor CPU én Nvidia GPU)
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
#
# Python packages worden geïnstalleerd in een virtualenv (/app/deps) zodat
# pip volledig geïsoleerd is van het systeem-Python. Dit vermijdt het PEP 668
# conflict op Ubuntu 24.04 én laat toe om setuptools/wheel te updaten naar
# niet-kwetsbare versies zonder RECORD-bestand problemen.

FROM ubuntu:24.04

LABEL org.opencontainers.image.title="Shrync"
LABEL org.opencontainers.image.description="Zelf-gehoste H.265 media converter — automatische GPU-detectie"
LABEL org.opencontainers.image.version="0.54"
LABEL org.opencontainers.image.authors="JOUWGITHUBUSERNAME"
LABEL org.opencontainers.image.source="https://github.com/JOUWGITHUBUSERNAME/shrync"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# GPU_MODE: leeg = auto-detectie (aanbevolen voor alle gebruikers)
ENV GPU_MODE=

# CACHE_DIR: tijdelijk bestand tijdens conversie
ENV CACHE_DIR=

ENV SHRYNC_VERSION=0.54

# Virtualenv pad — Python en alle deps leven hier, volledig geïsoleerd van systeem-Python
ENV PYTHONPATH=/app/deps
ENV PATH=/app/deps/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# ── Systeem dependencies ──────────────────────────────────────────────────────
# apt-get upgrade: security patches voor glibc, nghttp2, ncurses, systemd, tar
# python3-venv: nodig om de virtualenv aan te maken
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends \
        xz-utils \
        curl \
        ca-certificates \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

# ── ffmpeg statische build (met NVENC + libx265 + libx264) ───────────────────
RUN curl -fsSL \
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz" \
    -o /tmp/ffmpeg.tar.xz \
    && tar -xf /tmp/ffmpeg.tar.xz -C /tmp \
    && mv /tmp/ffmpeg-master-latest-linux64-gpl/bin/ffmpeg /usr/local/bin/ffmpeg \
    && mv /tmp/ffmpeg-master-latest-linux64-gpl/bin/ffprobe /usr/local/bin/ffprobe \
    && chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe \
    && rm -rf /tmp/ffmpeg* \
    && ffmpeg -version | head -1

# ── Python virtualenv + dependencies ─────────────────────────────────────────
# Maak een virtualenv in /app/deps — volledig geïsoleerd van systeem-Python.
# Pip kan hier zonder restricties installeren: geen PEP 668, geen RECORD-conflict.
# setuptools en wheel worden als eerste geüpdatet naar niet-kwetsbare versies.
WORKDIR /app

COPY requirements.txt .
RUN python3 -m venv /app/deps \
    && /app/deps/bin/pip install --no-cache-dir --upgrade pip setuptools==80.1.0 wheel==0.45.1 \
    && /app/deps/bin/pip install --no-cache-dir -r requirements.txt

# ── Applicatie ────────────────────────────────────────────────────────────────
COPY app/ ./app/
COPY templates/ ./templates/
COPY static/ ./static/
COPY entrypoint.sh .

RUN mkdir -p /config /cache /media && chmod +x /app/entrypoint.sh

EXPOSE 8000

# entrypoint.sh: GPU detecteren → GPU_MODE instellen → uvicorn starten
ENTRYPOINT ["/app/entrypoint.sh"]
