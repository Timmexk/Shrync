# Shrync v0.63 — Release notes & werkinstructies

## ⚠️ Kritieke fix — mogelijk dataverlies door verkeerde streamselectie

**Dit is de belangrijkste release tot nu toe.** v0.62 introduceerde een regressie
die er in bepaalde gevallen toe kon leiden dat het **originele bestand werd
vervangen door een kapotte, vrijwel lege conversie** — zonder dat Shrync dit
als fout herkende.

### Wat ging er mis

De whitelist-streamselectie uit v0.62 gebruikte `-map 0:v:0?` (kleine letter
`v`) om de eerste videostream te selecteren. In ffmpeg's stream-specifier
syntax telt een **ingebed albumhoesje / cover-art-plaatje** (bijv. een
losse MJPEG-afbeelding die veel videobestanden als metadata bevatten) ook
mee als "videostream". Als zo'n plaatje vóór de echte film in het bestand
stond, selecteerde `0:v:0?` het plaatje in plaats van de film. Het resultaat
was een geconverteerd bestand van ~1 seconde, terwijl ffmpeg gewoon
returncode 0 (succes) teruggaf — Shrync herkende dit dus niet als mislukte
conversie en verving het origineel met deze kapotte uitvoer.

**Impact:** voor bestanden die hierdoor zijn geraakt, is het originele
bestand hoogstwaarschijnlijk **niet meer terug te halen via Shrync** — er is
geen backup-mechanisme in de app zelf. Herstel is alleen mogelijk als er op
filesystem- of storage-niveau een aparte back-up/snapshot bestaat (bijv.
ZFS/BTRFS snapshot, Unraid recycle-bin plugin, of een losse kopie).

### De twee fixes in deze release

**1. Hoofdletter `V` in plaats van kleine letter `v`**

```
-map 0:V:0?   # hoofdletter V = video-sporen ZONDER attached pictures/cover art
-map 0:a?     # alle audiosporen, optioneel
-map 0:s?     # alle ondertitelsporen, optioneel
```

Ffmpeg's eigen documentatie maakt dit onderscheid expliciet: kleine `v`
omvat *alle* videotype-sporen inclusief attached pictures; hoofdletter `V`
sluit die uit. Dit lost het specifieke cover-art-probleem op.

**2. Structurele veiligheidscontrole (voorkomt deze hele klasse van fouten)**

Belangrijker dan fix 1: er is nu een duur-controle toegevoegd die **voorkomt
dat dit ooit weer tot dataverlies leidt**, ongeacht de exacte oorzaak. Na
elke conversie wordt de duur van het geconverteerde bestand vergeleken met
de duur van het origineel. Wijkt die meer dan 10% af (bij bronbestanden
langer dan 5 seconden), dan wordt de conversie als **mislukt** gemarkeerd:

- het kapotte tijdelijke bestand wordt verwijderd
- het **origineel blijft onaangeroerd**
- er verschijnt een duidelijke foutmelding in de geschiedenis

Dit beschermt niet alleen tegen de cover-art-bug, maar tegen elke toekomstige
situatie waarin ffmpeg "succesvol" afsluit terwijl de uitvoer feitelijk kapot
is — precies het soort bug-klasse dat het lastigst met losse fixes op te
lossen is.

Getest met een gesimuleerde ffmpeg-run (mocked `subprocess`) die zowel het
kapotte-uitvoer-scenario (1,2s output op een bron van 5400s → conversie
correct geweigerd, origineel intact) als het normale succesvolle scenario
(5399,5s output op een bron van 5400s → gewoon geaccepteerd) doorloopt.

---

## Werkinstructies — Docker Hub upload

1. `cd C:\pad\naar\shrync`
2. `docker build --no-cache --platform linux/amd64 --build-arg SHRYNC_VERSION=0.63 -t timmex91/shrync:latest -t timmex91/shrync:0.63 .`
3. `docker push timmex91/shrync:latest && docker push timmex91/shrync:0.63`
4. Unraid: Docker → Shrync → Edit → Repository `timmex91/shrync:0.63` → Apply

Gebruik je een gepinde `FFMPEG_TAG` vanwege een oudere GPU-driver (bijv. de
Quadro P2000)? Geef die dan opnieuw mee bij het bouwen:

```bash
docker build --no-cache --platform linux/amd64 \
  --build-arg SHRYNC_VERSION=0.63 \
  --build-arg FFMPEG_TAG=autobuild-2026-05-31-13-22 \
  -t timmex91/shrync:latest -t timmex91/shrync:0.63 .
```
