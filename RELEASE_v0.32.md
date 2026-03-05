# Shrync v0.32

## Nieuw

**Ondertitelwachtrij leegmaken**
Op de ondertitelingpagina staat nu een "Wachtrij leegmaken" knop in de header van de wachtrij tabel. Alle `pending` items worden verwijderd. Lopende vertalingen worden niet onderbroken. Er zit een bevestigingsdialoog op.

**Betere extern ondertitelbestand detectie**
`has_dutch_subtitle` controleert nu drie lagen:
1. Gelabeld NL bestand (`.nl.srt`, `.nld.srt`, `.dut.srt` etc.)
2. Elk extern ondertitelbestand met dezelfde naam ongeacht taalcode — `Film.srt`, `Film.en.srt`, `.ass`, `.ssa`, `.vtt`, `.sub`
3. NL ondertitelstream in het MKV zelf

## Bugfixes

**Ondertitel jobs blijven hangen na herstart**
Bij het opstarten worden onderbroken `processing` subtitle jobs nu automatisch teruggezet naar `pending`. Fout-items worden direct verwijderd uit de wachtrij en verplaatst naar de geschiedenis.

**Fouten blijven plakken in de wachtrij**
Een mislukte vertaling werd eerder opgeslagen als `error` in de wachtrij en bleef daar zichtbaar. Nu worden fouten direct naar de geschiedenis verplaatst en uit de wachtrij verwijderd. Opnieuw proberen kan via de ↺ knop in de geschiedenis.

**Mobiel hamburger menu maakt scherm wazig**
De sidebar had geen `isolation: isolate` waardoor de `backdrop-filter` blur doorsijpelde naar de content. De sidebar heeft nu `z-index: 200` met volledige blur-isolatie. De overlay heeft `z-index: 199`.
