#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
# Shrync v0.10 — entrypoint met automatische GPU-detectie
# ══════════════════════════════════════════════════════════════════════════════

# Geen set -e — we willen zelf foutafhandeling doen, niet vroegtijdig afbreken

echo "┌─────────────────────────────────────────────┐"
echo "│  Shrync v${SHRYNC_VERSION}  —  H.265 Media Converter  │"
echo "└─────────────────────────────────────────────┘"

# ── Executables uit deps/bin beschikbaar maken ────────────────────────────────
# pip install --target plaatst scripts in /app/deps/bin (uvicorn, etc.)
export PATH="/app/deps/bin:${PATH}"

# ── Stap 1: GPU detecteren ────────────────────────────────────────────────────
GPU_DETECTED=false
GPU_NAME="geen"

if command -v nvidia-smi >/dev/null 2>&1; then
    if nvidia-smi --query-gpu=name --format=csv,noheader >/dev/null 2>&1; then
        GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
        DRIVER_VER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 || echo "onbekend")
        echo "  GPU gedetecteerd   : ${GPU_NAME}"
        echo "  Nvidia driver      : ${DRIVER_VER}"
        GPU_DETECTED=true
    fi
fi

if [ "$GPU_DETECTED" = false ] && [ -e /dev/nvidia0 ]; then
    echo "  GPU gedetecteerd via /dev/nvidia0"
    GPU_DETECTED=true
fi

if [ "$GPU_DETECTED" = false ] && \
   [ -n "${NVIDIA_VISIBLE_DEVICES}" ] && \
   [ "${NVIDIA_VISIBLE_DEVICES}" != "void" ]; then
    echo "  GPU gedetecteerd via NVIDIA_VISIBLE_DEVICES=${NVIDIA_VISIBLE_DEVICES}"
    GPU_DETECTED=true
fi

# ── Stap 2: GPU_MODE bepalen ──────────────────────────────────────────────────
if [ "$GPU_DETECTED" = true ]; then
    if [ "${GPU_MODE}" = "cpu" ]; then
        echo "  GPU beschikbaar maar GPU_MODE=cpu geforceerd — CPU encoding actief"
        export GPU_MODE=cpu
    else
        export GPU_MODE=nvidia
        echo "  GPU_MODE automatisch ingesteld: nvidia"
    fi
else
    if [ "${GPU_MODE}" = "nvidia" ]; then
        echo "  WAARSCHUWING: GPU_MODE=nvidia maar geen GPU gevonden — terugvallen op CPU"
    fi
    export GPU_MODE=cpu
    echo "  Geen GPU gevonden — CPU encoding actief"
fi

# ── Stap 3: NVENC valideren ───────────────────────────────────────────────────
if [ "${GPU_MODE}" = "nvidia" ]; then
    echo "  ffmpeg NVENC check..."

    if ! ffmpeg -hide_banner -encoders 2>/dev/null | grep -q "hevc_nvenc"; then
        echo "  WAARSCHUWING: hevc_nvenc niet beschikbaar — terugvallen op CPU"
        export GPU_MODE=cpu
    else
        NVENC_TEST=$(ffmpeg -hide_banner \
            -f lavfi -i color=c=black:s=128x128:r=25:d=2 \
            -vf format=yuv420p \
            -c:v hevc_nvenc -preset fast -rc vbr -cq 28 -b:v 0 \
            -f null /dev/null 2>&1 || true)

        if echo "$NVENC_TEST" | grep -qE "No capable devices|Cannot load|not supported|Operation not permitted|no decoder|No such"; then
            echo "  WAARSCHUWING: NVENC niet beschikbaar"
            echo "  Fout: $(echo "$NVENC_TEST" | grep -Ei 'error|cannot|no capable' | head -1)"
            echo "  → Terugvallen op CPU encoding"
            export GPU_MODE=cpu
        else
            echo "  NVENC (hevc_nvenc) gevalideerd ✓"
        fi
    fi
fi

# ── Stap 4: Config map aanmaken en schrijfrechten zekerstellen ───────────────
mkdir -p /config 2>/dev/null || true
chmod 755 /config 2>/dev/null || true
if [ -n "${CACHE_DIR}" ]; then
    mkdir -p "${CACHE_DIR}" 2>/dev/null || true
fi

# ── Stap 5: Samenvatting ──────────────────────────────────────────────────────
echo ""
echo "  Modus      : ${GPU_MODE}"
echo "  GPU        : ${GPU_NAME}"
echo "  Cache      : ${CACHE_DIR:-(naast bronbestand)}"
echo "  Config     : /config"
echo "  uvicorn    : $(which uvicorn 2>/dev/null || echo 'niet gevonden!')"
echo ""
echo "  Shrync v${SHRYNC_VERSION} is klaar"
echo "───────────────────────────────────────────────"
echo ""

# ── Stap 6: Applicatie starten ────────────────────────────────────────────────
cd /app
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --log-level info
