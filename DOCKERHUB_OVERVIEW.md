# Shrync — Self-hosted H.265 Media Converter

Shrync is a self-hosted Docker container that automatically converts your media library to H.265/HEVC. It watches your folders for new files, queues them, and encodes them using your GPU or CPU — all managed through a clean web UI.

**One image. Works on CPU and Nvidia GPU automatically.**

---

## Features

- 🎬 **H.265 encoding** via Nvidia NVENC, AMD AMF, Intel QSV, or CPU (libx265)
- 📁 **Library watching** — new files are detected and queued automatically
- 🤖 **AI subtitle translation** — translates subtitles between any supported languages via a local Ollama model
- 🌙 **Dark / Light mode** — full theme system, preference saved across sessions
- 🔍 **Search & sort** — filter history by filename or library, sort by any column
- ☑ **Bulk actions** — select and delete multiple history items at once
- ⏱ **Estimated queue time** — live ETA based on recent conversion speeds
- 📈 **Savings chart** — weekly storage savings graph over the past 8 weeks
- ⊗ **Exclusions** — permanently skip files from conversion and/or subtitling
- 🌐 **Configurable subtitle languages** — choose any source and target language (17 supported)
- 📊 **Statistics** — tracks storage saved per library
- 🖥️ **Unraid Community Applications** template available
- 🌐 **English / Dutch UI**

---

## Quick start

### CPU (no GPU required)

```bash
docker run -d \
  --name shrync \
  --restart unless-stopped \
  -p 8988:8000 \
  -v /path/to/config:/config \
  -v /path/to/movies:/media/movies \
  timmex91/shrync:latest
```

### Nvidia GPU

```bash
docker run -d \
  --name shrync \
  --restart unless-stopped \
  --runtime=nvidia \
  -e NVIDIA_VISIBLE_DEVICES=all \
  -p 8988:8000 \
  -v /path/to/config:/config \
  -v /path/to/movies:/media/movies \
  timmex91/shrync:latest
```

The container detects the GPU automatically on every start and enables NVENC encoding. No tag switching, no manual configuration needed.

---

## docker-compose.yml

```yaml
services:
  shrync:
    image: timmex91/shrync:latest
    container_name: shrync
    restart: unless-stopped

    # GPU — uncomment for Nvidia:
    # runtime: nvidia
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: 1
    #           capabilities: [gpu, video, compute]

    environment:
      - GPU_MODE=        # leave empty for auto-detect (recommended)
      - CACHE_DIR=       # leave empty to use source folder

    ports:
      - "8988:8000"

    volumes:
      - /path/to/config:/config
      - /path/to/movies:/media/movies
      - /path/to/series:/media/series
```

Open `http://your-server-ip:8988` after starting.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `GPU_MODE` | *(empty — auto)* | Empty = auto-detect. `cpu` = force CPU encoding. Never set manually to `nvidia`. |
| `CACHE_DIR` | *(empty)* | Temp file location during encoding. Empty = next to source file. |

---

## Volumes

| Path | Required | Description |
|---|---|---|
| `/config` | Yes | Database and configuration |
| `/cache` | No | Temp encoding location (set `CACHE_DIR=/cache`) |
| `/media/*` | Yes | Your media folders — add as many as needed |

---

## AI Subtitle Translation

Shrync can automatically translate subtitles using a locally running [Ollama](https://ollama.com) model after each conversion. Configure the Ollama host, model, source language, and target language in Settings → AI Subtitles.

Supported languages: English, Dutch, German, French, Spanish, Italian, Portuguese, Russian, Japanese, Chinese, Korean, Polish, Swedish, Norwegian, Danish, Finnish, Turkish.

Use the built-in **translation quality test** to verify your model before processing your full library.

Recommended models: `mistral:7b`, `gemma2:9b`, `llama3.1:8b`

---

## GPU auto-detection

On every container start, Shrync runs three detection methods:

1. `nvidia-smi` — queries the GPU name via the driver
2. `/dev/nvidia0` — checks if the GPU device is visible
3. `NVIDIA_VISIBLE_DEVICES` — reads the container runtime variable

If a GPU is found and ffmpeg supports `hevc_nvenc`, encoding is set to GPU automatically. Otherwise it silently falls back to CPU.

---

## Conversion profiles

| Profile | Encoder | Description |
|---|---|---|
| NVENC Max | Nvidia GPU | Highest quality NVENC encode |
| NVENC Balanced | Nvidia GPU | Good quality, fast encode |
| AMF Max | AMD GPU | Highest quality AMD encode |
| QSV Balanced | Intel GPU | Good quality Intel Quick Sync |
| CPU Max | libx265 | Best quality, slow |
| CPU Balanced | libx265 | Good quality/speed balance |

---

## Unraid

Add the template via **Apps → ⚙️ → Template repositories**:

```
https://raw.githubusercontent.com/timmexk/Shrync/main/shrync.xml
```

---

## ffmpeg

Shrync uses a static ffmpeg build from [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) (GPL). Included encoders: `hevc_nvenc`, `h264_nvenc`, `hevc_amf`, `hevc_qsv`, `libx265`, `libx264`.
