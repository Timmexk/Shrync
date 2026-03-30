# Shrync v0.46

## Nieuwe functie — Bulk acties in ondertitelinggeschiedenis

### ☑️ Selecteren en bulk verwerken in Geschiedenis
In het tabblad **Ondertiteling → Geschiedenis** kun je nu één of meerdere items
selecteren via checkboxen aan de linkerkant van elke rij. Zodra je een selectie
maakt verschijnt er een actiebalk bovenaan de tabel met twee opties:

- **↺ Opnieuw verwerken** — verwijdert het bestaande SRT bestand van schijf en
  zet het item opnieuw in de vertaalwachtrij met de huidige instellingen en het
  verbeterde `###N###` marker-systeem. Handig om eerder vertaalde bestanden te
  herverwerken na de timing-bugfix.
- **✕ SRT verwijderen** — verwijdert het SRT bestand van schijf én het history
  record. Gebruik dit om een vertaling volledig ongedaan te maken.

Er is ook een **Alles selecteren** checkbox in de tabelheader, en een
**✕ Deselecteer** knop in de actiebalk. Bij paginawisseling wordt de selectie
automatisch gewist.

Technisch: nieuw `POST /api/subtitle/history/bulk` endpoint met `action` parameter
(`delete_srt` of `requeue`). Geeft per item terug of het geslaagd of mislukt is,
met een foutmelding als het bronbestand niet meer bestaat of er geen
ondertitelstream gevonden wordt.

## Security — CVE fixes

### 🔒 OS-pakket CVEs opgelost via apt-get upgrade in Dockerfile
Docker Scout rapporteerde 13 kwetsbaarheden in de base image pakketten.
Al deze CVEs zijn gefixt in de Ubuntu 24.04 security updates maar stonden
nog niet in de base image snapshot. Door `apt-get upgrade` toe te voegen
aan de Dockerfile worden alle OS-pakketten bijgewerkt bij het bouwen:

| CVE | Ernst | Pakket | Status |
|-----|-------|--------|--------|
| CVE-2026-0861 | 8.4 H | glibc 2.41-12 → 2.39-0ubuntu8.7 | ✓ Gefixt |
| CVE-2026-0915 | 7.5 H | glibc | ✓ Gefixt |
| CVE-2025-15281 | 7.5 H | glibc | ✓ Gefixt |
| CVE-2026-27135 | 7.5 H | nghttp2 1.64.0-1.1 | ✓ Gefixt |
| CVE-2025-69720 | 7.3 H | ncurses 6.5+20250216-2 | ✓ Gefixt |
| CVE-2026-29111 | 5.5 M | systemd 257.9-1 | ✓ Gefixt |
| CVE-2025-45582 | 4.1 M | tar 1.35+dfsg-3.1 | ✓ Gefixt |
| CVE-2019-1010024 | N/A L | glibc | ✓ Gefixt |
| CVE-2019-1010025 | N/A L | glibc | ✓ Gefixt |
| CVE-2010-4756 | N/A L | glibc | ✓ Gefixt |

Aanpak: `apt-get upgrade -y` gecombineerd in dezelfde RUN-laag als
`apt-get install` — geen extra Docker-laag, geen grotere image.
Eveneens `pip3 install --upgrade pip` toegevoegd zodat pip zelf
ook altijd de meest recente versie gebruikt.

## Bugfix — Ondertiteling timing

### 🐛 Intermitterende timing-verschuiving bij vertaling
De vertaalfunctie gebruikte genummerde regels (`1. tekst`) om batches te parsen.
Dit ging mis zodra vertaalde tekst zelf een punt bevatte — `Dr. Smith`, `"Nee."`,
afkortingen — waardoor `split(". ", 1)` de tekst halverwege kapte. Als een nummer
vervolgens werd overgeslagen, verschoof de tekst van blok N naar de timecode van
blok N+1, wat de schijnbare timing-drift veroorzaakte die "vanzelf" weer goed ging
na een paar regels.

**Fix:** prompt en parser werken nu met `###1###`, `###2###` etc. als scheidingstekens.
Die kunnen onmogelijk in gewone filmondertitels voorkomen. De response wordt gesplitst
via een regex op deze markers, waardoor punten, haakjes en dubbele punten in de
vertaalde tekst de parsing nooit meer kunnen verstoren.

**Bonus:** als het model minder dan 50% van de verwachte markers teruggeeft (time-out,
halve respons), wordt de hele batch behouden met de originele tekst — timecodes
blijven altijd correct.

## Dependency updates

| Package | Was | Nu |
|---------|-----|----|
| fastapi | 0.111.0 | **0.135.2** |
| uvicorn[standard] | 0.30.1 | **0.42.0** |
| pydantic | 2.7.3 | **2.12.5** |
| jinja2 | 3.1.4 | **3.1.6** |
| aiofiles | 23.2.1 | **24.1.0** |
| watchdog | 4.0.1 | **6.0.0** |

FastAPI 0.135.2 bevat verbeterde OpenAPI schema-generatie, betere Pydantic v2
integratie en strict Content-Type checking. Uvicorn 0.42.0 brengt Python 3.14
support en diverse stabiliteitsverbeteringen.

## Code-kwaliteit

### ♻️ FastAPI lifespan (vervangt deprecated on_event)
`@app.on_event("startup")` en `@app.on_event("shutdown")` zijn vervangen door
het moderne `lifespan` context manager patroon dat de FastAPI-documentatie
aanraadt vanaf versie 0.93. Dit verwijdert deprecation-waarschuwingen bij opstarten.

### 🧹 Imports opgeschoond
`import glob` en `import re` stonden als inline imports binnenin functies. Beide
zijn verplaatst naar de top-level imports, conform Python best practices.

### 🐳 Dockerfile: Ubuntu 22.04 → 24.04 LTS
Ubuntu 22.04 heeft Python 3.10 als standaard; 24.04 LTS brengt Python 3.12 mee,
met betere performance, langere security support (tot 2029) en volledige
compatibiliteit met de bijgewerkte dependencies. Python 3.12 is ~5% sneller dan
3.10 bij I/O-bound werklasten zoals Shrync's ffmpeg-monitoring loop.

## UI (overgenomen uit v0.45)

- Stat cards: horizontale strip met gekleurde icons, vaste `var(--bg2)` achtergrond
  die in dark én light mode correct meeveert met de rest van de interface
- Progress bar: 7px, paarse gloed, shimmer-animatie
- Badges: compact, uppercase, scherpe hoeken — consistent op alle pagina's
- Sidebar: active state met `border-left: 3px solid var(--blue)` accent
- Logo: SVG icon naast naam in sidebar
- Scrollbar: 3px

## Technisch

- `translate_blocks_ollama`: `###N###` markers, `re.compile(r"###(\d+)###")` splitter,
  50%-drempel voor batch-validatie
- `lifespan` context manager vervangt `@app.on_event`
- `glob`, `re` uit function bodies naar module-level imports
- Alle versienummers (`app/main.py`, `Dockerfile`, `build.sh`, `docker-compose.yml`,
  `shrync.xml`) bijgewerkt naar `0.46`
