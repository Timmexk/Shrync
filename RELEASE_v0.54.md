# Shrync v0.54 — Release notes & werkinstructies

## Werkinstructies — Docker Hub upload

1. `cd C:\pad\naar\shrync`
2. `docker build --no-cache --platform linux/amd64 -t timmex91/shrync:latest -t timmex91/shrync:0.54 .`
3. `docker push timmex91/shrync:latest && docker push timmex91/shrync:0.54`
4. Unraid: Docker → Shrync → Edit → Repository `timmex91/shrync:0.54` → Apply

---

## Bugfix — Alle audio en ondertitelsporen behouden na conversie

**Oorzaak:** zonder expliciete `-map 0` past ffmpeg zijn eigen stream
selectie toe. Dat betekent standaard: één videostream, één audiostream
(de "beste"), en één ondertitelstream — alleen de primaire taal.
Alle overige audio- en ondertiteltalen werden stilletjes weggegooid.

**Fix:** `-map 0` toegevoegd aan alle vier de ffmpeg builders (NVENC,
AMF, QSV, CPU), direct na `-i src`. Dit instrueert ffmpeg om letterlijk
alle streams uit de input te kopiëren: alle videosporen, alle audiotalen,
alle ondertiteltalen, alle bijlagen.

Gecombineerd met `-c:s copy` worden alle ingebakken ondertitelsporen
(Engels, Nederlands, Duits, Frans, ...) integraal bewaard.
