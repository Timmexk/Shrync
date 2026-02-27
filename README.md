# Shrync v0.03 — H.265 Media Converter

Zelf-gehoste H.265/HEVC media converter met automatische bibliotheekbewaking.
**Één image voor CPU én Nvidia GPU** — de container detecteert automatisch wat beschikbaar is.

---

## Snelstart

### CPU (geen GPU, geen extra instellingen)

```bash
docker run -d \
  --name shrync \
  --restart unless-stopped \
  -p 8988:8000 \
  -v /pad/naar/config:/config \
  -v /pad/naar/films:/media/films \
  timmex91/shrync:latest
```

### Nvidia GPU (voeg alleen de runtime toe — de rest gaat automatisch)

```bash
docker run -d \
  --name shrync \
  --restart unless-stopped \
  --runtime=nvidia \
  -e NVIDIA_VISIBLE_DEVICES=all \
  -p 8988:8000 \
  -v /pad/naar/config:/config \
  -v /pad/naar/films:/media/films \
  timmex91/shrync:latest
```

De container detecteert de GPU automatisch en activeert NVENC. Zelfde image, geen tag-wissel nodig.

---

## Hoe werkt de automatische GPU-detectie?

Bij elke containerstart voert de entrypoint drie detectiemethoden uit:

1. `nvidia-smi` — vraagt de GPU-naam op via de driver
2. `/dev/nvidia0` — controleert of het GPU-apparaat zichtbaar is
3. `NVIDIA_VISIBLE_DEVICES` — leest de container-runtime variabele

Is een GPU gevonden én ondersteunt ffmpeg `hevc_nvenc`? Dan wordt `GPU_MODE=nvidia` automatisch ingesteld. Anders valt de container stil terug op CPU — zonder foutmelding, zonder handmatige aanpassing.

---

## docker-compose.yml

```yaml
services:
  shrync:
    image: timmex91/shrync:latest
    container_name: shrync
    restart: unless-stopped

    # GPU — verwijder commentaar voor Nvidia GPU:
    # runtime: nvidia
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: 1
    #           capabilities: [gpu, video, compute]

    environment:
      # Leeg laten = auto-detectie (aanbevolen)
      # 'cpu' = forceer CPU, ook als GPU aanwezig
      - GPU_MODE=
      # Leeg = naast bronbestand. Of: /cache voor aparte schijf
      - CACHE_DIR=

    ports:
      - "8988:8000"

    volumes:
      - /pad/naar/config:/config
      # Cache (optioneel, SSD aanbevolen):
      # - /pad/naar/cache:/cache
      # Mediamappen:
      - /pad/naar/films:/media/films
      - /pad/naar/series:/media/series
```

---

## Omgevingsvariabelen

| Variabele | Standaard | Beschrijving |
|---|---|---|
| `GPU_MODE` | *(leeg — auto)* | Leeg = automatische detectie. `cpu` = forceer CPU. Nooit handmatig `nvidia` invullen. |
| `CACHE_DIR` | *(leeg)* | Tijdelijk bestand tijdens conversie. Leeg = naast bronbestand. |

---

## Volumes

| Pad | Verplicht | Beschrijving |
|---|---|---|
| `/config` | Ja | Database en configuratie |
| `/cache` | Nee | Tijdelijk bestand (stel CACHE_DIR=/cache in) |
| `/media/*` | Ja | Mediamappen — voeg zoveel toe als nodig |

---

## Image tags

| Tag | Beschrijving |
|---|---|
| `latest` | Meest recente versie — altijd CPU + automatische GPU-detectie |
| `0.03` | Bugfix NVENC encoder parameters (aanbevolen) |
| `0.01` | Eerste release |

Er is **geen aparte GPU-tag**. Dezelfde image werkt overal.

---

## ffmpeg

Shrync gebruikt een statische ffmpeg-build van [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) met GPL-licentie. Deze build bevat:

- `hevc_nvenc` — Nvidia H.265 GPU encoding
- `h264_nvenc` — Nvidia H.264 GPU encoding  
- `libx265` — CPU H.265 encoding
- `libx264` — CPU H.264 encoding

Op systemen zonder GPU worden de NVENC-encoders automatisch overgeslagen.

---

## Functies

- Automatische GPU-detectie bij elke start
- H.265 encoding via CPU (libx265) of Nvidia GPU (NVENC)
- Automatische bibliotheekbewaking — nieuwe bestanden direct in wachtrij
- Meerdere conversieprofiel-presets
- Live scanvoortgang per bibliotheek
- Geschiedenis met foutmelding per bestand
- Statistieken: ruimtebesparing per bibliotheek
- Nederlands / Engels

---

## Unraid

Voeg de template toe via **Apps → ⚙️ → Template repositories**:
```
https://raw.githubusercontent.com/timmexk/Shrync/main/shrync.xml
```
