# Shrync v0.48 — Release notes & werkinstructies

## Werkinstructies — Docker Hub upload

1. ZIP uitpakken en naar de map navigeren:
   ```
   cd C:\pad\naar\shrync
   ```

2. Image bouwen:
   ```
   docker build --no-cache --platform linux/amd64 -t timmex91/shrync:latest -t timmex91/shrync:0.48 .
   ```

3. Pushen naar Docker Hub:
   ```
   docker push timmex91/shrync:latest
   docker push timmex91/shrync:0.48
   ```

4. Unraid container updaten:
   - Docker → Shrync → Edit → Repository: `timmex91/shrync:0.48` → Apply

5. GPU parameters (P4000):
   - Extra Parameters: `--runtime=nvidia`
   - Environment: `NVIDIA_VISIBLE_DEVICES=all`
   - Environment: `NVIDIA_DRIVER_CAPABILITIES=video,compute,utility`

---

## Changelog

### 🐛 Fix — Ondertiteling uit sync (onvertaalde zinnen midden in bestand)

**Symptoom:** ondertiteling loopt af en toe voor, gevolgd door 1-2 onvertaalde
zinnen, daarna weer normaal.

**Oorzaak:** het Ollama model stopte halverwege een batch omdat de output-limiet
(`num_predict`) bereikt was. De resterende blokken in die batch vielen terug op
de originele tekst — correct gesynchroniseerd maar onvertaald. De volgende batch
begon dan normaal, waardoor het patroon zich herhaalde.

**Drie fixes tegelijk:**

**1. Batch van 20 → 10**
Kleinere batches zijn betrouwbaarder voor lokale modellen zoals Gemma 4B. Minder
kans op context-verlies en weggelaten markers.

**2. `num_predict` van 2048 → 4096**
Dit was de directe oorzaak. De output-limiet was te krap voor een volledige batch
bij langere dialoogscènes. Met 4096 tokens is er ruim genoeg ruimte.

**3. `num_ctx: 8192` expliciet meegegeven**
Gemma 4B (en andere kleine modellen) gebruiken standaard soms slechts 2048 tokens
als context window als dit niet expliciet opgegeven wordt. Door `num_ctx: 8192`
mee te sturen wordt gegarandeerd dat het volledige gesprek in context past.

**4. Per-blok retry bij ontbrekende markers**
Als er na een batch toch nog markers ontbreken, worden die blokken nu individueel
opnieuw verstuurd in plaats van terugvallen op het onvertaalde origineel. Zo wordt
elk blok altijd vertaald, ook als het model een marker overslaat.

---

### 🐛 Fix — Internal server error bij openen webinterface

**Oorzaak:** FastAPI 0.135.2 trok automatisch Starlette 1.0.0 binnen als
dependency. Starlette 1.0.0 heeft de `TemplateResponse` API gewijzigd — de oude
signatuur `TemplateResponse(name, {"request": request})` is verwijderd en geeft
een `TypeError: unhashable type: 'dict'`.

**Fix:** twee maatregelen:
- `starlette==0.46.2` en `fastapi==0.115.12` gepind in requirements.txt zodat
  pip nooit automatisch naar Starlette 1.0 springt
- `TemplateResponse` bijgewerkt naar de nieuwe signatuur
  `TemplateResponse(request, "index.html")` voor toekomstbestendige compatibiliteit

---

### 🐛 Fix — Container startte niet op (lifespan variabele volgorde)

`_sub_dispatcher_running` werd door de lifespan functie gebruikt voordat de
variabele als module-level global gedefinieerd was. Verplaatst naar de State
sectie bovenaan `main.py` bij de andere globals. `worker_running = True`
hersteld in het startup-gedeelte van de lifespan.

---

### 🔧 Dockerfile — Virtualenv voor Python packages

Python packages worden nu geïnstalleerd in een virtualenv (`/app/deps`) in plaats
van system-wide. Oplossing voor drie terugkerende buildfouten:
- Geen `externally-managed-environment` fout meer (PEP 668 / Ubuntu 24.04)
- Geen `RECORD file not found` fout meer (Debian pip/wheel/setuptools conflict)
- `setuptools==80.1.0` en `wheel==0.45.1` kunnen nu wel bijgewerkt worden
  (CVE-2025-47273, CVE-2024-6345, CVE-2026-24049)

```dockerfile
RUN python3 -m venv /app/deps \
    && /app/deps/bin/pip install --upgrade pip setuptools==80.1.0 wheel==0.45.1 \
    && /app/deps/bin/pip install -r requirements.txt
```

---

### 🔒 Security — OS CVE fixes

`apt-get upgrade -y` in de Dockerfile zorgt dat alle OS-pakketten bijgewerkt
worden bij elke nieuwe build. Dekt: glibc (CVE-2026-0861, CVE-2026-0915,
CVE-2025-15281), nghttp2 (CVE-2026-27135), ncurses (CVE-2025-69720),
systemd (CVE-2026-29111), tar (CVE-2025-45582).

> **Kernel CVEs** (`ubuntu/linux 6.8.0-xxx`) zijn niet fixable via de Dockerfile
> — de kernel komt van de Unraid host. Deze Docker Scout meldingen kunnen
> genegeerd worden.

---

### ✨ Nieuwe functie — Bulk acties in Ondertiteling → Geschiedenis

Checkboxen per rij in de Geschiedenis-tabel. Actiebalk bij selectie:
- **↺ Opnieuw verwerken** — verwijdert bestaande SRT en zet terug in wachtrij
- **✕ SRT verwijderen** — verwijdert SRT van schijf én history record
- **Alles selecteren** checkbox in tabelheader

API: `POST /api/subtitle/history/bulk` met `{"ids": [...], "action": "requeue"|"delete_srt"}`

---

### 🎨 UI (eerder doorgevoerd)

- Stat cards: horizontale strip met gekleurde icons
- Progress bar: 7px, paarse gloed, shimmer-animatie
- Badges: compact, uppercase, scherpe hoeken
- Sidebar: `border-left: 3px solid var(--blue)` accent op actief item
- Logo: SVG icon naast naam in sidebar

---

### ♻️ Dependency overzicht

| Package | Versie |
|---------|--------|
| fastapi | 0.115.12 |
| starlette | 0.46.2 (expliciet gepind) |
| uvicorn[standard] | 0.32.1 |
| pydantic | 2.10.6 |
| jinja2 | 3.1.6 |
| aiofiles | 24.1.0 |
| watchdog | 6.0.0 |
| setuptools | 80.1.0 (via venv) |
| wheel | 0.45.1 (via venv) |

Base image: `ubuntu:24.04` met `apt-get upgrade` bij elke build.
