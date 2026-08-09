# Shrync v0.62 — Release notes & werkinstructies

## Werkinstructies — Docker Hub upload

1. `cd C:\pad\naar\shrync`
2. `docker build --no-cache --platform linux/amd64 --build-arg SHRYNC_VERSION=0.62 -t timmex91/shrync:latest -t timmex91/shrync:0.62 .`
3. `docker push timmex91/shrync:latest && docker push timmex91/shrync:0.62`
4. Unraid: Docker → Shrync → Edit → Repository `timmex91/shrync:0.62` → Apply

Gebruik je een gepinde `FFMPEG_TAG` vanwege een oudere GPU-driver? Geef die
dan opnieuw mee bij het bouwen — zie DOCKER_HUB_UPLOAD.md.

---

## Structurele fix — streamselectie omgedraaid naar een whitelist (geïnspireerd op Unmanic)

**Aanleiding:** na de mov_text-fix in v0.61 liep conversie op hetzelfde
bronbestand alsnog vast, nu met *"Only audio, video, and subtitles are
supported for Matroska"*. Reden: Shrync mapte met `-map 0` altijd **alles**
uit het bronbestand, en sloot pas ná een bugreport het specifieke
kapotte spoortype uit. Dat is een fundamenteel fragiel patroon — elk nieuw
soort onverwacht spoor (data-sporen, bijlagen, GoPro-telemetrie, embedded
lettertypen, cover-art als losse videostream) breekt de conversie opnieuw,
één voor één ontdekt via falende conversies.

**Aanpak:** de broncode van [Unmanic](https://github.com/Unmanic/unmanic)
bekeken (een vergelijkbare, gevestigde library-transcoder) om te zien hoe
zij dit oplossen. Unmanic mapt nooit blind alles — het bouwt de
`-map`-argumenten expliciet op per spoor, uitsluitend voor sporen die het
herkent als video/audio/ondertiteling.

Shrync's streamselectie is nu op diezelfde manier omgedraaid van een
**blacklist** (alles pakken, bekende probleemgevallen uitsluiten) naar een
**whitelist** (alleen bekend-veilige soorten pakken):

```
-map 0:v:0?   # alleen de eerste/primaire videostream
              # (voorkomt dat een los cover-art-plaatje als 2e "video" wordt meegecodeerd)
-map 0:a?     # alle audiosporen, optioneel
-map 0:s?     # alle ondertitelsporen, optioneel
```

Dit lost niet alleen de twee nu bekende fouten (data-sporen, bijlagen) op,
maar voorkomt ook de hele klasse van toekomstige varianten daarvan —
zonder dat er per geval een nieuwe uitsluiting bij hoeft.

Getest door de volledige ffmpeg-commando's te genereren met een
gesimuleerde `ffprobe`-uitvoer en te controleren dat de argumenten exact
kloppen (geen ffmpeg beschikbaar in de ontwikkelomgeving om écht te
coderen).
