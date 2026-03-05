# Shrync v0.30 — AI Ondertiteling

## Nieuwe functie: Automatische ondertitelvertaling via Ollama

Shrync kan nu automatisch Engelse ondertitels vertalen naar Nederlands
met behulp van een lokaal Ollama model. De vertaling wordt als los
`.nl.srt` bestand naast het mediabestand opgeslagen zodat
Jellyfin/Plex/Bazarr het direct oppikt.

### Hoe het werkt

1. Stel het Ollama adres en model in via Instellingen → AI Ondertiteling
2. Schakel "Automatisch vertalen" in
3. Na elke succesvolle H.265 conversie controleert Shrync automatisch:
   - Is er al een `.nl.srt` naast het bestand? → overslaan
   - Zit er een Engelse ondertitelstream in het MKV? → toevoegen aan wachtrij
4. De ondertitelwachtrij start zodra de conversiewachtrij leeg is
5. Ollama vertaalt de ondertitels in batches van 20 regels

### Opstartscan

Bij het opstarten scant Shrync alle bibliotheken op bestanden die:
- Nog geen Nederlandse ondertitel hebben
- Wel een Engelse ondertitelstream bevatten
- Nog niet eerder vertaald zijn

Deze worden automatisch toegevoegd aan de ondertitelwachtrij
(alleen als ondertiteling ingeschakeld is).

### Slimme stream selectie

Shrync kiest automatisch het beste Engelse ondertitelspoor:
- Engels (normaal) heeft voorkeur boven Engels SDH/CC
- SDH bevat geluidsomschrijvingen die de vertaling verstoren

### Bestandsnaming

Output: `Bestandsnaam.nl.srt` naast het MKV bestand.
Compatibel met Jellyfin, Plex, Bazarr en Kodi.

### Instellingen

- **Ollama adres**: het HTTP adres van je Ollama instantie
- **Model**: keuze uit alle gedownloade modellen via dropdown
- **Automatisch vertalen**: aan/uit schakelaar (optioneel — raakt conversie niet)

### Aanbevolen modellen

| Model | Kwaliteit | VRAM |
|-------|-----------|------|
| mistral:7b | ★★★★☆ | ~5 GB |
| gemma2:9b | ★★★★★ | ~6 GB |
| llama3.1:8b | ★★★★☆ | ~5 GB |
| gemma2:2b | ★★★☆☆ | ~2 GB |

### Dashboard

- Nieuwe statustegel op het dashboard toont wachtrij, actieve vertaling en dagelijkse teller
- Nieuwe pagina "Ondertiteling" met wachtrij, voortgang en geschiedenis
- Mislukte vertalingen kunnen opnieuw worden geprobeerd via de ↺ knop

### Technische details

- Timecodes worden nooit aangepast — alleen de tekst wordt vertaald
- Vertaling via Ollama `/api/generate` met `temperature: 0.3`
- Maximaal 1 vertaling tegelijk (Ollama is single-threaded)
- Fouten worden gelogd en opgeslagen in de geschiedenis

## Bugfixes (v0.25 → v0.30)

- Scan bleef hangen op bestand 1 door tuple mismatch in `profile_to_ffmpeg` (3 vs 4 waarden)
- Conversie startte niet door `NameError: profile_id` in `run_conversion`
- GPU monitor layout aangepast naar stat-card stijl
- Datum kolom in geschiedenis niet uitgelijnd door `display:flex` op `<td>`
- Overgeslagen bestanden modal toonde nu correcte bestanden uit database
- nvidia-smi `encoder.stats.sessionCount` vervangen door `utilization.encoder`
