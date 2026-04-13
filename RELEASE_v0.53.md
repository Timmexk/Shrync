# Shrync v0.53 — Release notes & werkinstructies

## Werkinstructies — Docker Hub upload

1. `cd C:\pad\naar\shrync`
2. `docker build --no-cache --platform linux/amd64 -t timmex91/shrync:latest -t timmex91/shrync:0.53 .`
3. `docker push timmex91/shrync:latest && docker push timmex91/shrync:0.53`
4. Unraid: Docker → Shrync → Edit → Repository `timmex91/shrync:0.53` → Apply

---

## Bugfixes

### ✓ Alle ondertitelsporen bewaard na conversie
Alle ffmpeg commands gebruiken `-c:s copy` zonder enige `-map` restrictie.
Alle subtitle streams (alle talen, alle codecs) worden integraal meegenomen.

Als je toch verlies ziet: controleer eerst met ffprobe of de streams er
werkelijk niet in zitten, of dat de mediaspeler ze simpelweg niet toont.
Commando om te controleren:
  ffprobe -v quiet -print_format json -show_streams bestand.mkv

### 🐛 Verstreken tijd toonde altijd "—"
`+00:00` timezone suffix veroorzaakte ongeldige datum parsing in de browser.
Fix: `+00:00` wordt vervangen door `Z` voor `new Date()` parsing.

### 🐛 Recent verwerkt — activity feed samengevoegd
Conversies én ondertitelingen gecombineerd in één tabel, gesorteerd op
datum. Type badge (`✓ Conv`, `✓ Sub`) per rij.

### 🐛 Scan loopt vast — sqlite3.Row `.get()` fout
`lib.get("exclude_patterns")` faalde op sqlite3.Row objecten.
Fix: directe bracket-toegang met try/except.
