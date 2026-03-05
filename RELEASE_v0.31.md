# Shrync v0.31

## Nieuw

**Ondertitelwachtrij leegmaken**
Op de ondertitelingpagina staat nu een "Wachtrij leegmaken" knop in de header van de wachtrij tabel. Alle `pending` items worden verwijderd. Lopende vertalingen worden niet onderbroken. Er zit een bevestigingsdialoog op om per ongeluk leegmaken te voorkomen.

**Betere extern ondertitelbestand detectie**
`has_dutch_subtitle` controleert nu drie lagen in volgorde:
1. Gelabeld NL bestand naast het mediabestand (`.nl.srt`, `.nld.srt`, `.dut.srt` etc.)
2. Elk extern ondertitelbestand met dezelfde bestandsnaam, ongeacht taalcode — `Film.srt`, `Film.en.srt`, `Film.eng.srt`, `.ass`, `.ssa`, `.vtt`, `.sub`. Als er al iets naast het mediabestand staat wordt het overgeslagen.
3. NL ondertitelstream in het MKV zelf

Dit voorkomt dat bestanden met een bestaand extern ondertitelbestand onnodig worden toegevoegd aan de vertaalwachtrij.
