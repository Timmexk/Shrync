#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
# Shrync v0.01 — entrypoint met automatische GPU-detectie
# ══════════════════════════════════════════════════════════════════════════════
# Draait elke keer als de container start. Detecteert GPU, stelt GPU_MODE in,
# valideert ffmpeg NVENC beschikbaarheid, dan start de app.

set -e

echo "┌─────────────────────────────────────────────┐"
echo "│  Shrync v${SHRYNC_VERSION}  —  H.265 Media Converter  │"
echo "└─────────────────────────────────────────────┘"

# ── Stap 1: GPU aanwezigheid detecteren ───────────────────────────────────────
GPU_DETECTED=false
GPU_NAME="geen"

# Methode A: nvidia-smi aanwezig en reageert (meest betrouwbaar)
if command -v nvidia-smi &>/dev/null 2>&1; then
    if nvidia-smi --query-gpu=name --format=csv,noheader &>/dev/null 2>&1; then
        GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
        echo "  GPU gedetecteerd via nvidia-smi: ${GPU_NAME}"
        GPU_DETECTED=true
    fi
fi

# Methode B: GPU device nodes aanwezig (werkt als nvidia-smi niet in image zit)
if [ "$GPU_DETECTED" = false ] && [ -e /dev/nvidia0 ]; then
    echo "  GPU gedetecteerd via /dev/nvidia0"
    GPU_DETECTED=true
fi

# Methode C: CUDA_VISIBLE_DEVICES of NVIDIA_VISIBLE_DEVICES is ingesteld
if [ "$GPU_DETECTED" = false ] && \
   [ -n "${NVIDIA_VISIBLE_DEVICES}" ] && \
   [ "${NVIDIA_VISIBLE_DEVICES}" != "void" ]; then
    echo "  GPU gedetecteerd via NVIDIA_VISIBLE_DEVICES=${NVIDIA_VISIBLE_DEVICES}"
    GPU_DETECTED=true
fi

# ── Stap 2: GPU_MODE bepalen ──────────────────────────────────────────────────
if [ "$GPU_DETECTED" = true ]; then
    if [ "${GPU_MODE}" = "cpu" ]; then
        # Gebruiker heeft CPU expliciet geforceerd
        echo "  GPU beschikbaar maar GPU_MODE=cpu geforceerd — CPU encoding actief"
        export GPU_MODE=cpu
    else
        # Auto: GPU gevonden → nvidia mode
        export GPU_MODE=nvidia
        echo "  GPU_MODE automatisch ingesteld: nvidia"
    fi
else
    # Geen GPU → altijd CPU, ook als gebruiker per ongeluk 'nvidia' had ingevuld
    if [ "${GPU_MODE}" = "nvidia" ]; then
        echo "  WAARSCHUWING: GPU_MODE=nvidia ingesteld maar geen GPU gevonden"
        echo "  → Terugvallen op CPU encoding"
    fi
    export GPU_MODE=cpu
    echo "  Geen GPU — CPU encoding actief"
fi

# ── Stap 3: NVENC beschikbaarheid valideren ───────────────────────────────────
if [ "${GPU_MODE}" = "nvidia" ]; then
    echo "  ffmpeg NVENC check..."
    if ffmpeg -hide_banner -encoders 2>/dev/null | grep -q "hevc_nvenc"; then
        echo "  NVENC (hevc_nvenc) beschikbaar ✓"
    else
        echo "  WAARSCHUWING: hevc_nvenc niet beschikbaar in ffmpeg"
        echo "  → Terugvallen op CPU encoding"
        export GPU_MODE=cpu
    fi
fi

# ── Stap 4: Samenvatting ──────────────────────────────────────────────────────
echo ""
echo "  Modus      : ${GPU_MODE}"
echo "  GPU        : ${GPU_NAME}"
echo "  Cache      : ${CACHE_DIR:-(naast bronbestand)}"
echo "  Config     : /config"
echo ""
echo "  Shrync v${SHRYNC_VERSION} is klaar"
echo "───────────────────────────────────────────────"
echo ""

# ── Stap 5: Applicatie starten ────────────────────────────────────────────────
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --log-level info
