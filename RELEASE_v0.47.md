# Shrync v0.47 — Release notes & werkinstructies

## Werkinstructies — Docker Hub upload

### Vereisten
- Docker Desktop actief op je Windows machine
- Ingelogd bij Docker Hub: `docker login`

### Stappen

1. **ZIP uitpakken** en naar de map navigeren:
   ```
   cd C:\pad\naar\shrync
   ```

2. **Image bouwen:**
   ```
   docker build --no-cache --platform linux/amd64 -t timmex91/shrync:latest -t timmex91/shrync:0.47 .
   ```
   Dit duurt 5–10 minuten (apt upgrade + ffmpeg download).

3. **Pushen naar Docker Hub:**
   ```
   docker push timmex91/shrync:latest
   docker push timmex91/shrync:0.47
   ```

4. **Unraid container updaten:**
   - Docker → Shrync container → Edit
   - Repository: `timmex91/shrync:0.47` (of `:latest`)
   - Apply → container herstart automatisch

5. **GPU parameters controleren** (nodig voor je P4000):
   - Extra Parameters: `--runtime=nvidia`
   - Environment: `NVIDIA_VISIBLE_DEVICES=all`
   - Environment: `NVIDIA_DRIVER_CAPABILITIES=video,compute,utility`

---

## Changelog

### 🐛 Hotfix — Internal server error bij opstarten
Na de introductie van de FastAPI `lifespan` context manager in v0.46 startte
de container niet meer op. De oorzaak was een volgorde-probleem: de globale
variabele `_sub_dispatcher_running` werd door de lifespan functie gerefereerd
vóórdat hij als module-level variabele gedefinieerd was. In de oude
`@app.on_event` aanpak was dit geen probleem, maar de lifespan context manager
pakt initialisatie anders op.

**Fix:** `_sub_dispatcher_running` verplaatst naar de State sectie bovenaan
`main.py` waar alle andere globals (`worker_running`, `active_jobs`, etc.)
ook gedefinieerd staan. Lifespan vereenvoudigd: `global` declaratie alleen
nog in het shutdown-gedeelte waar de variabelen daadwerkelijk gewijzigd worden.

---

### 🔒 Security — CVE fixes in Python packages

Docker Scout rapporteerde drie kwetsbaarheden in pip-geïnstalleerde packages.
Opgelost door deze te pinnen op niet-kwetsbare versies in `requirements.txt`:

| Package | Was | Nu | CVEs |
|---------|-----|----|------|
| setuptools | 68.1.2 | via apt (python3-setuptools) | CVE-2025-47273 (7.7H), CVE-2024-6345 (7.5H) |
| wheel | 0.42.0 | via apt (python3-wheel) | CVE-2026-24049 (7.1H) |

> Debian installeert setuptools en wheel zonder RECORD file, waardoor pip
> ze niet kan overschrijven. Opgelost door ze via `apt install python3-setuptools
> python3-wheel` te installeren in plaats van via pip.

> **Opmerking over kernel CVEs:** Docker Scout toont ook kwetsbaarheden in
> `ubuntu/linux 6.8.0-106`. Dit zijn kernel-CVEs die niet fixable zijn via
> de Dockerfile — de kernel komt van de Unraid host, niet van de container.
> Dit zijn false positives voor containergebruik en kunnen genegeerd worden.

---

### 🔒 Security — apt-get upgrade in Dockerfile (overgenomen uit v0.46)
`apt-get upgrade -y` toegevoegd aan het build-stap zodat alle OS-pakketten
(glibc, nghttp2, ncurses, systemd, tar) bijgewerkt worden naar de laatste
security-patches bij elke nieuwe build.

---

### ✨ Nieuwe functie — Bulk acties in Ondertiteling → Geschiedenis
Checkboxen per rij in de Geschiedenis-tabel van de Ondertitelingspagina.
Zodra je een of meer items selecteert verschijnt een actiebalk met:

- **↺ Opnieuw verwerken** — verwijdert het bestaande SRT van schijf en
  zet het item opnieuw in de vertaalwachtrij met de huidige instellingen.
  Handig om eerder vertaalde bestanden te herverwerken na de timing-bugfix.
- **✕ SRT verwijderen** — verwijdert het SRT bestand van schijf én het
  history record volledig.
- **Alles selecteren** — checkbox in de tabelheader.
- **✕ Deselecteer** — wist de selectie zonder actie.

Nieuw API endpoint: `POST /api/subtitle/history/bulk`
Body: `{ "ids": ["id1", "id2"], "action": "requeue" | "delete_srt" }`

---

### 🐛 Bugfix — Ondertiteling timing (overgenomen uit v0.46)
Intermitterende timing-verschuiving bij vertaalde ondertitels opgelost.
De oude nummering (`1. tekst`, `2. tekst`) ging mis zodra vertaalde tekst
een punt bevatte zoals `Dr. Smith` of `"Nee."` — `split(". ", 1)` kapte dan
op de verkeerde plek waardoor tekst bij de verkeerde timecode belandde.

**Fix:** prompt en parser gebruiken nu `###1###`, `###2###` markers die
onmogelijk in gewone filmtekst voorkomen. Extra: als minder dan 50% van de
markers terugkomt in een respons wordt de hele batch als mislukt beschouwd
en behouden met originele tekst — timecodes blijven altijd correct.

---

### ♻️ Dependency updates (overgenomen uit v0.46)

| Package | Was | Nu |
|---------|-----|----|
| fastapi | 0.111.0 | 0.135.2 |
| uvicorn[standard] | 0.30.1 | 0.42.0 |
| pydantic | 2.7.3 | 2.12.5 |
| jinja2 | 3.1.4 | 3.1.6 |
| aiofiles | 23.2.1 | 24.1.0 |
| watchdog | 4.0.1 | 6.0.0 |
| setuptools | — | 80.1.0 |
| wheel | — | 0.45.1 |

---

### ♻️ Code-kwaliteit (overgenomen uit v0.46)
- FastAPI `@app.on_event` vervangen door moderne `lifespan` context manager
- `import glob` en `import re` verplaatst van function bodies naar top-level
- Dockerfile: Ubuntu 22.04 → 24.04 LTS (Python 3.12, support tot 2029)
- `pip3 install --break-system-packages` voor Ubuntu 24.04 compatibiliteit

---

### 🎨 UI verbeteringen (overgenomen uit v0.45)
- Stat cards: horizontale strip met gekleurde icons, thema-correcte achtergrond
- Progress bar: 7px met paarse gloed en shimmer-animatie
- Badges: compact, uppercase, scherpe hoeken — consistent op alle pagina's
- Sidebar: `border-left: 3px solid var(--blue)` accent op actief item
- Logo: SVG icon links naast de naam in de sidebar
- Scrollbar: 3px

---

## Bestanden in deze release

| Bestand | Omschrijving |
|---------|-------------|
| `app/main.py` | Applicatie backend |
| `templates/index.html` | Web UI |
| `Dockerfile` | Ubuntu 24.04, apt upgrade, pip fix |
| `requirements.txt` | Python dependencies incl. security fixes |
| `docker-compose.yml` | Compose configuratie voor Unraid |
| `build.sh` | Build & push script voor Docker Hub |
| `entrypoint.sh` | GPU auto-detectie bij opstarten |
| `shrync.xml` | Unraid Community Applications template |
| `DOCKER_HUB_UPLOAD.md` | Uitgebreide upload instructies |
