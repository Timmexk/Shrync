# Shrync v0.50 — Release notes & werkinstructies

## Werkinstructies — Docker Hub upload

1. ZIP uitpakken en navigeren:
   ```
   cd C:\pad\naar\shrync
   ```
2. Image bouwen:
   ```
   docker build --no-cache --platform linux/amd64 -t timmex91/shrync:latest -t timmex91/shrync:0.50 .
   ```
3. Pushen:
   ```
   docker push timmex91/shrync:latest
   docker push timmex91/shrync:0.50
   ```
4. Unraid: Docker → Shrync → Edit → Repository: `timmex91/shrync:0.50` → Apply

---

## Nieuwe functies

### 📋 Dashboard — Live activity feed
Onderaan het dashboard staat nu een **Activiteit** tijdlijn die de laatste
20 acties toont, gecombineerd uit conversie-geschiedenis en ondertiteling-
geschiedenis. Per item is zichtbaar:
- Bestandsnaam
- Type actie (conversie / ondertiteling) en resultaat (besparing, regels)
- Tijdstip

De feed wordt automatisch bijgewerkt met elke dashboard-refresh (elke 5s).
Items zijn kleurgecodeerd: blauw = conversie, paars = ondertiteling, rood = fout,
groen = geslaagd.

---

### 🖥 Actieve conversie — detailkaart
De kaart voor actieve conversies is uitgebreid met een visuele detailstrook:
- **% voortgang** groot en prominent rechtsboven met ETA eronder
- **FPS** — live frames per seconde van het encoderproces
- **Origineel** — bestandsgrootte van de bron in MB
- **Verstreken** — hoe lang de conversie al bezig is (live bijgewerkt)
- **Encoder** — welk profiel/encoder actief is (bijv. `nvenc_max`)

De progressbalk behoudt de shimmer-animatie. De verstreken tijd wordt
berekend op basis van `started_at` in de database.

---

### 🚫 Uitsluitingsregels per bibliotheek
In de bibliotheek-instellingen (toevoegen én bewerken) kun je nu
**uitsluitingspatronen** opgeven — één per regel, regex toegestaan.

Bestanden waarvan de naam overeenkomt met een patroon worden volledig
overgeslagen tijdens het scannen. Ze verschijnen niet in de wachtrij.

**Voorbeelden:**
```
Remux
2160p
\.sample\.
HEVC-D3G
```

De patronen zijn case-insensitief en worden vergeleken met alleen de
bestandsnaam (niet het volledige pad). Regex-syntaxis wordt ondersteund
via Python `re.search()`.

---

### 🎯 Vertaalkwaliteit per bibliotheek
Bij elke bibliotheek kun je nu instellen hoe grondig de ondertitel-
vertaling moet zijn:

| Modus | Batch | Context | Gebruik |
|-------|-------|---------|---------|
| **Snel** | 5 | 4096 | Dagelijkse series, veel afleveringen |
| **Normaal** | 10 | 8192 | Standaard (was de enige optie) |
| **Grondig** | 6 | 16384 | Films, hoge kwaliteit prioriteit |

"Grondig" gebruikt kleinere batches maar geeft het model meer context per
batch, wat de coherentie van de vertaling ten goede komt voor langere,
complexere dialogen zoals in films.

De kwaliteitsinstelling is zichtbaar als drie klikbare opties in de
bibliotheek-modal. Bestaande bibliotheken krijgen automatisch "Normaal".

---

## Technische wijzigingen

- `libraries` tabel: kolommen `exclude_patterns TEXT` en
  `subtitle_quality TEXT` toegevoegd met automatische migratie voor
  bestaande databases (geen data verlies)
- `queue` tabel: kolom `profile_id TEXT` toegevoegd — wordt gevuld bij
  start van elke conversie zodat de detailkaart de encoder kan tonen
- `translate_blocks_ollama()` accepteert nu een `quality` parameter die
  `BATCH_SIZE` en `num_ctx` dynamisch instelt
- `scan_library()` past uitsluitingspatronen toe via `re.search()` vóór
  het toevoegen aan de wachtrij
- `LibraryCreate` / `LibraryUpdate` Pydantic modellen uitgebreid met
  `exclude_patterns` en `subtitle_quality`
- Nieuwe JS functie `loadActivityFeed()` combineert history en subtitle
  history in één gesorteerde tijdlijn
- Nieuwe JS functie `formatSaved()` toont MB-besparing met percentage

---

## Tijdzone fix (v0.49, in deze release meegenomen)

Alle timestamps in de database worden nu opgeslagen met expliciete UTC-
aanduiding (`+00:00`). De `formatDate()` functie in de frontend behandelt
ook bestaande records zonder timezone-aanduiding correct als UTC, waardoor
de weergegeven tijden aansluiten bij de lokale tijdzone van de browser.

