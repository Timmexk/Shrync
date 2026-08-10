# Shrync v0.65 — Release notes & werkinstructies

## Teruggedraaid — "ondertitels forceren" (v0.64)

De v0.64-feature om ondertiteling te forceren ondanks een al aanwezige
Nederlandse ondertitel is op verzoek teruggedraaid. De extra knop
("Ondertitels forceren") en de `force`-optie op
`/api/libraries/{id}/scan-subtitles` en `/api/subtitle/queue/add` zijn
verwijderd. Shrync gedraagt zich op dit punt weer zoals in v0.63: een
bestand met een al gedetecteerde Nederlandse ondertitel (embedded spoor of
extern bestand) wordt overgeslagen voor automatische vertaling.

Geen andere functionele wijzigingen — dit is puur een revert.

---

## Werkinstructies — Docker Hub upload

1. `cd C:\pad\naar\shrync`
2. `docker build --no-cache --platform linux/amd64 --build-arg SHRYNC_VERSION=0.65 -t timmex91/shrync:latest -t timmex91/shrync:0.65 .`
3. `docker push timmex91/shrync:latest && docker push timmex91/shrync:0.65`
4. Unraid: Docker → Shrync → Edit → Repository `timmex91/shrync:0.65` → Apply

Gebruik je een gepinde `FFMPEG_TAG` vanwege een oudere GPU-driver (bijv. de
Quadro P2000)? Geef die dan opnieuw mee bij het bouwen:

```bash
docker build --no-cache --platform linux/amd64 \
  --build-arg SHRYNC_VERSION=0.65 \
  --build-arg FFMPEG_TAG=autobuild-2026-05-31-13-22 \
  -t timmex91/shrync:latest -t timmex91/shrync:0.65 .
```
