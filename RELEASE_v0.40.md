# Shrync v0.40

## Nieuwe features

### 🌙 Dark / Light mode
Shrync heeft nu een volledig themasysteem. Wissel tussen donker en licht thema via de knop onderaan de sidebar. De voorkeur wordt opgeslagen en hersteld bij de volgende sessie. Alle kleuren, achtergronden, invoervelden en grafieken passen zich automatisch aan.

### 🔍 Zoekbalk in geschiedenis
De conversiegeschiedenis heeft een zoekbalk gekregen. Zoek direct op bestandsnaam of bibliotheek. Resultaten worden live gefilterd zonder paginering te verliezen.

### ↕ Sorteerbare kolommen in geschiedenis
Klik op een kolomkop in de geschiedenis om te sorteren — op bestandsnaam, bibliotheek, bespaard, status of datum. Een tweede klik wisselt de richting. De actieve sortering is zichtbaar via een ↑ of ↓ indicator.

### ☑ Bulk acties in geschiedenis
Selecteer meerdere items tegelijk via checkboxes en verwijder ze in één klik. De bulkactiebalk verschijnt automatisch zodra je iets selecteert. Er is ook een "alles selecteren" checkbox in de kolomkop.

### ⏱ Geschatte eindtijd wachtrij
Het dashboard toont nu een schatting van de resterende wachtrij tijd op basis van het gemiddelde van de laatste 20 voltooide conversies en het aantal actieve workers.

### 📈 Besparing grafiek over tijd
De statistiekenpagina heeft een lijngrafiek gekregen die de wekelijkse besparing in GB over de afgelopen 8 weken visualiseert. De grafiek past zich aan het actieve thema aan (donker/licht).

### 🌐 Instelbare bron- en doeltaal ondertiteling
In de instellingen voor AI ondertiteling zijn nu twee dropdowns beschikbaar: brontaal en doeltaal. Ondersteunde talen: Engels, Nederlands, Duits, Frans, Spaans, Italiaans, Portugees, Russisch, Japans, Chinees, Koreaans, Pools, Zweeds, Noors, Deens, Fins en Turks. De Ollama vertaalprompt en output bestandsnaam (`.nl.srt`, `.de.srt` etc.) passen zich automatisch aan.

### 🧪 Vertaalkwaliteit testen
Voer een testzin in en klik op Testen om direct de vertaalkwaliteit van het ingestelde Ollama model te beoordelen. De test gebruikt de geconfigureerde bron- en doeltaal. Handig om te controleren of het model geschikt is voor jouw taalcombinatie voordat je de volledige bibliotheek verwerkt.

### 📚 Handmatige ondertiteling scan per bibliotheek
Op elke bibliotheekkaart verschijnt een "Ondertitels scannen" knop zodra AI ondertiteling is ingeschakeld. Hiermee start je handmatig een volledige scan van die bibliotheek op bestanden zonder ondertitel, zonder de container te hoeven herstarten.

### ⊗ Uitsluitingen beheer
Nieuwe pagina "Uitsluitingen" in de navigatie. Bestanden die je permanent wil overslaan bij conversie en/of ondertiteling kun je hier beheren. Uitsluitingen worden gecontroleerd tijdens elke scan en bij het automatisch in de wachtrij zetten. De pagina heeft een zoekbalk, checkboxes voor bulk verwijdering en afzonderlijke kolommen voor conversie- en ondertitelingsuitsluiting.

## Technisch

- `exclusions` tabel toegevoegd aan de database
- `/api/exclusions` GET/POST/DELETE endpoints
- `/api/savings/chart` endpoint met wekelijkse aggregatie
- `/api/history` uitgebreid met `search`, `sort` en `dir` queryparameters
- `/api/history/{id}` DELETE endpoint voor individuele items
- `/api/libraries/{id}/scan-subtitles` POST endpoint
- `/api/subtitle/test-translation` POST endpoint
- `/api/stats` geeft nu `eta_seconds` terug
- Ondertitellogica volledig taal-agnostisch via `pick_best_source_stream()`
- Exclusiebeheer geïntegreerd in scan loop en subtitle dispatcher
- `theme` opgeslagen in settings tabel
