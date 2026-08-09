# Shrync v0.61 — Release notes & werkinstructies

## Werkinstructies — Docker Hub upload

1. `cd C:\pad\naar\shrync`
2. `docker build --no-cache --platform linux/amd64 --build-arg SHRYNC_VERSION=0.61 -t timmex91/shrync:latest -t timmex91/shrync:0.61 .`
3. `docker push timmex91/shrync:latest && docker push timmex91/shrync:0.61`
4. Unraid: Docker → Shrync → Edit → Repository `timmex91/shrync:0.61` → Apply

Gebruik je een gepinde `FFMPEG_TAG` vanwege een oudere GPU-driver? Geef die
dan opnieuw mee bij het bouwen — zie DOCKER_HUB_UPLOAD.md.

---

## Bugfix — conversie mislukte bij MP4-bronnen met ingebedde ondertitels

**Probleem:** conversie van een `.mp4`/`.mov`-bestand met een ingebedde
ondertitelspoor mislukte volledig, met de foutmelding:
`Subtitle codec mov_text (94213) is not supported`, gevolgd door
`Nothing was written into output file`.

**Oorzaak:** Shrync zet elk bestand om naar een Matroska-container (`.mkv`),
en kopieerde ondertitelsporen altijd blind (`-c:s copy`). `mov_text` — het
standaard ondertitelformaat in MP4/MOV — is echter geen geldige codec voor
Matroska, waardoor ffmpeg weigerde de uitvoer te schrijven en de hele
conversie faalde, ook al ging het alleen om de ondertitels.

**Fix:** vóór de conversie wordt nu via `ffprobe` gecontroleerd welke
ondertitelcodecs aanwezig zijn. Alleen `mov_text` (en een paar vergelijkbare
tekstformaten) wordt omgezet naar `srt`; alle andere ondertitelformaten
(inclusief beeld-gebaseerde zoals PGS/VobSub uit Blu-ray-rips) worden zoals
voorheen gewoon gekopieerd. Bestanden zonder ondertitels of waarbij de
detectie faalt, gedragen zich exact als voorheen.
