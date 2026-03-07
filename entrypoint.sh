#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
# Shrync v0.40 — entrypoint met automatische GPU-detectie (Nvidia / AMD / Intel)
# ══════════════════════════════════════════════════════════════════════════════

echo "┌─────────────────────────────────────────────┐"
echo "│  Shrync v${SHRYNC_VERSION}  —  H.265 Media Converter  │"
echo "└─────────────────────────────────────────────┘"

export PATH="/app/deps/bin:${PATH}"

# ── Stap 1: GPU detecteren ────────────────────────────────────────────────────
GPU_DETECTED=false
GPU_NAME="geen"
DETECTED_TYPE="cpu"

# Nvidia
if command -v nvidia-smi >/dev/null 2>&1; then
    if nvidia-smi --query-gpu=name --format=csv,noheader >/dev/null 2>&1; then
        GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
        DRIVER_VER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 || echo "onbekend")
        echo "  GPU gedetecteerd   : ${GPU_NAME} (Nvidia)"
        echo "  Nvidia driver      : ${DRIVER_VER}"
        GPU_DETECTED=true
        DETECTED_TYPE="nvidia"
    fi
fi

if [ "$GPU_DETECTED" = false ] && [ -e /dev/nvidia0 ]; then
    echo "  GPU gedetecteerd via /dev/nvidia0 (Nvidia)"
    GPU_DETECTED=true
    DETECTED_TYPE="nvidia"
fi

if [ "$GPU_DETECTED" = false ] && \
   [ -n "${NVIDIA_VISIBLE_DEVICES}" ] && \
   [ "${NVIDIA_VISIBLE_DEVICES}" != "void" ]; then
    echo "  GPU gedetecteerd via NVIDIA_VISIBLE_DEVICES=${NVIDIA_VISIBLE_DEVICES}"
    GPU_DETECTED=true
    DETECTED_TYPE="nvidia"
fi

# AMD — via /dev/dri en product_name
if [ "$GPU_DETECTED" = false ] && ls /dev/dri/renderD* >/dev/null 2>&1; then
    AMD_NAME=$(cat /sys/class/drm/card0/device/product_name 2>/dev/null || echo "")
    if echo "$AMD_NAME" | grep -qi "AMD\|Radeon\|RX\|Vega\|RDNA"; then
        echo "  GPU gedetecteerd   : ${AMD_NAME} (AMD)"
        GPU_DETECTED=true
        DETECTED_TYPE="amd"
        GPU_NAME="$AMD_NAME"
    fi
fi

# Intel — via /dev/dri en vendor
if [ "$GPU_DETECTED" = false ] && ls /dev/dri/renderD* >/dev/null 2>&1; then
    INTEL_VENDOR=$(cat /sys/class/drm/card0/device/vendor 2>/dev/null || echo "")
    if [ "$INTEL_VENDOR" = "0x8086" ]; then
        INTEL_NAME=$(cat /sys/class/drm/card0/device/product_name 2>/dev/null || echo "Intel GPU")
        echo "  GPU gedetecteerd   : ${INTEL_NAME} (Intel)"
        GPU_DETECTED=true
        DETECTED_TYPE="intel"
        GPU_NAME="$INTEL_NAME"
    fi
fi

# ── Stap 2: GPU_MODE bepalen ──────────────────────────────────────────────────
if [ "$GPU_DETECTED" = true ]; then
    if [ "${GPU_MODE}" = "cpu" ]; then
        echo "  GPU beschikbaar maar GPU_MODE=cpu geforceerd — CPU encoding actief"
        export GPU_MODE=cpu
    elif [ -n "${GPU_MODE}" ] && [ "${GPU_MODE}" != "$DETECTED_TYPE" ]; then
        echo "  WAARSCHUWING: GPU_MODE=${GPU_MODE} maar gedetecteerd type is ${DETECTED_TYPE}"
        export GPU_MODE="${DETECTED_TYPE}"
    else
        export GPU_MODE="${DETECTED_TYPE}"
        echo "  GPU_MODE automatisch ingesteld: ${GPU_MODE}"
    fi
else
    if [ "${GPU_MODE}" = "nvidia" ] || [ "${GPU_MODE}" = "amd" ] || [ "${GPU_MODE}" = "intel" ]; then
        echo "  WAARSCHUWING: GPU_MODE=${GPU_MODE} maar geen GPU gevonden — terugvallen op CPU"
    fi
    export GPU_MODE=cpu
    echo "  Geen GPU gevonden — CPU encoding actief"
fi

# ── Stap 3: Encoder valideren ─────────────────────────────────────────────────
if [ "${GPU_MODE}" = "nvidia" ]; then
    echo "  ffmpeg NVENC check..."
    if ! ffmpeg -hide_banner -encoders 2>/dev/null | grep -q "hevc_nvenc"; then
        echo "  WAARSCHUWING: hevc_nvenc niet beschikbaar — terugvallen op CPU"
        export GPU_MODE=cpu
    else
        NVENC_TEST=$(ffmpeg -hide_banner \
            -f lavfi -i color=c=black:s=128x128:r=25:d=2 \
            -vf format=yuv420p \
            -c:v hevc_nvenc -preset p4 -rc constqp -qp 28 -bf 0 -pix_fmt yuv420p \
            -f null /dev/null 2>&1 || true)
        if echo "$NVENC_TEST" | grep -qE "No capable devices|Cannot load|not supported|Operation not permitted|no decoder|No such"; then
            echo "  WAARSCHUWING: NVENC niet beschikbaar — terugvallen op CPU"
            export GPU_MODE=cpu
        else
            echo "  NVENC (hevc_nvenc) gevalideerd ✓"
        fi
    fi
fi

if [ "${GPU_MODE}" = "amd" ]; then
    echo "  ffmpeg AMF check..."
    if ! ffmpeg -hide_banner -encoders 2>/dev/null | grep -q "hevc_amf"; then
        echo "  WAARSCHUWING: hevc_amf niet beschikbaar — terugvallen op CPU"
        export GPU_MODE=cpu
    else
        echo "  AMF (hevc_amf) beschikbaar ✓"
    fi
fi

if [ "${GPU_MODE}" = "intel" ]; then
    echo "  ffmpeg QSV check..."
    if ! ffmpeg -hide_banner -encoders 2>/dev/null | grep -q "hevc_qsv"; then
        echo "  WAARSCHUWING: hevc_qsv niet beschikbaar — terugvallen op CPU"
        export GPU_MODE=cpu
    else
        echo "  QSV (hevc_qsv) beschikbaar ✓"
    fi
fi

# ── Stap 4: Config map aanmaken ───────────────────────────────────────────────
mkdir -p /config 2>/dev/null || true
chmod 755 /config 2>/dev/null || true

# ── Stap 5: Samenvatting ──────────────────────────────────────────────────────
echo ""
echo "  Modus      : ${GPU_MODE}"
echo "  GPU        : ${GPU_NAME}"
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
