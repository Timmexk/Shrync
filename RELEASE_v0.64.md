# Shrync v0.64 — Release notes & werkinstructies

## Nieuw — ondertiteling forceren, ook als er al Nederlandse ondertiteling is

Shrync sloeg bestanden altijd over voor automatische ondertitelvertaling
zodra er al een Nederlandse ondertitel gedetecteerd werd (embedded stream,
of een extern `.nl.srt`/`.srt` bestand). Dat is meestal terecht, maar niet
als die bestaande Nederlandse ondertiteling zelf niet goed is — bijvoorbeeld
een embedded NL-spoor met verkeerde timing.

Er was voorheen geen manier om dat te omzeilen: de "Ondertitels scannen"
knop per bibliotheek sloeg zulke bestanden altijd over, en de handmatige
`/api/subtitle/queue/add` endpoint gaf een harde 400-fout.

**Nu:** elke bibliotheek met ondertiteling ingeschakeld heeft een tweede
knop, **"Ondertitels forceren"**, naast de bestaande scanknop. Die scant
dezelfde map, maar negeert de "NL al aanwezig"-check — inclusief bestanden
die al eerder succesvol (of mislukt) verwerkt zijn. Er verschijnt eerst een
bevestigingsdialoog die uitlegt wat er gebeurt:

- Er wordt een **nieuw** ondertitelbestand gegenereerd (vertaald vanaf het
  brontaal-spoor, bijv. Engels), met correcte naam/taalcode
  (`Film.nl.srt`).
- De **bestaande** Nederlandse ondertiteling (embedded stream of los
  bestand) wordt **niet aangeraakt of verwijderd** — het nieuwe bestand komt
  er gewoon naast te staan. De mediaspeler kiest meestal automatisch het
  externe `.srt` bestand boven een embedded spoor.

Technisch: `has_dutch_subtitle()` blijft ongewijzigd (die functie bepaalt
nog steeds correct of er al NL-ondertiteling is), maar zowel
`/api/libraries/{id}/scan-subtitles` als `/api/subtitle/queue/add` accepteren
nu een `force: true` veld in de request body om die check bewust te
negeren. Bij het forceren van een bibliotheekscan wordt bovendien oude
geschiedenis (success/error) voor een geraakt bestand opgeruimd, zodat er
geen dubbele/verouderde entries ontstaan na herverwerking.

---

## Werkinstructies — Docker Hub upload

1. `cd C:\pad\naar\shrync`
2. `docker build --no-cache --platform linux/amd64 --build-arg SHRYNC_VERSION=0.64 -t timmex91/shrync:latest -t timmex91/shrync:0.64 .`
3. `docker push timmex91/shrync:latest && docker push timmex91/shrync:0.64`
4. Unraid: Docker → Shrync → Edit → Repository `timmex91/shrync:0.64` → Apply

Gebruik je een gepinde `FFMPEG_TAG` vanwege een oudere GPU-driver (bijv. de
Quadro P2000)? Geef die dan opnieuw mee bij het bouwen:

```bash
docker build --no-cache --platform linux/amd64 \
  --build-arg SHRYNC_VERSION=0.64 \
  --build-arg FFMPEG_TAG=autobuild-2026-05-31-13-22 \
  -t timmex91/shrync:latest -t timmex91/shrync:0.64 .
```
