# Shrync v0.55 — Release notes & werkinstructies

## Werkinstructies — Docker Hub upload

1. `cd C:\pad\naar\shrync`
2. `docker build --no-cache --platform linux/amd64 -t timmex91/shrync:latest -t timmex91/shrync:0.55 .`
3. `docker push timmex91/shrync:latest && docker push timmex91/shrync:0.55`
4. Unraid: Docker → Shrync → Edit → Repository `timmex91/shrync:0.55` → Apply

---

## Bugfix — Bestanden komen steeds terug in de wachtrij ("Al optimaal")

**Symptoom:** bestanden worden geconverteerd, verschijnen als "Al optimaal"
in Recent verwerkt, maar komen bij de volgende scan opnieuw in de wachtrij.

**Oorzaak:** wanneer het geconverteerde bestand groter is dan het origineel
(bijv. een al goed gecomprimeerde H.264 serie) bewaart Shrync het origineel
en schrijft status `skipped` in de history. De scan-logica controleerde
echter alleen op `status='success'` in de history — `skipped` werd niet
herkend als "al verwerkt", waardoor het bestand bij elke scan opnieuw werd
toegevoegd aan de wachtrij.

**Fix:** de history check in de scanner kijkt nu op
`status IN ('success', 'skipped')`. Bestanden die al geprobeerd zijn maar
waarbij het origineel kleiner bleek, worden niet meer opnieuw toegevoegd.

---

## Eerder in deze release (v0.54)

### ✓ Alle streams bewaard — `-map 0` toegevoegd
Zonder expliciete `-map 0` past ffmpeg eigen stream selectie toe: één
videostream, één audiostream, één ondertitelstream (de primaire taal).
Met `-map 0` worden alle streams uit de input meegenomen: alle audiotalen,
alle ondertiteltalen, alle bijlagen. Toegevoegd aan NVENC, AMF, QSV en CPU.
