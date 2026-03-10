# Shrync v0.45

## UI — Visuele vernieuwing

Deze release bevat een volledige visuele opfrisbeurt van de interface, doorgevoerd
in alle pagina's van de applicatie. De functionaliteit is ongewijzigd; het gaat
puur om een cohesievere, modernere look.

### 🎨 Stat cards — horizontale strip met gekleurde icons
De statistiekkaarten op het Dashboard en de Ondertitelingspagina zijn omgebouwd
van losse kaarten naar één aaneengesloten horizontale strip. Elke metric heeft nu
een eigen gekleurde icon-pill (blauw, groen, cyaan, rood, paars) zodat je in één
oogopslag ziet wat je bekijkt. De nummers zijn iets compacter gezet waardoor de
strip op alle schermformaten prettig leest.

### ✨ Progress bar — glow en shimmer sweep
De voortgangsbalk bij actieve conversies is verhoogd van 5px naar 7px en heeft
nu een paarse gloed (`box-shadow`) die meebeweegt met de vulling. Over de balk
loopt continu een transparante lichtreflectie (shimmer) zodat het er actief en
levend uitziet, ook als de voortgang langzaam gaat.

### 🏷 Badge redesign — compact, uppercase, scherp
Alle statusbadges in de applicatie (wachtrij, geschiedenis, ondertitelingen) zijn
opnieuw vormgegeven. Ze zijn kleiner, uppercase en hebben vierkante hoeken in
plaats van een pilvorm. Dit geeft een scherpere, meer developer-tool-achtige look
die consistent is over alle pagina's.

Gewijzigde badges:
- `✓ OK` — groen, scherp
- `⚡ Bezig X%` → `● X%` — paars, compacter label
- `◌ Wacht` — neutraal grijs
- `⊘ Skip` — oranje (was "Overgeslagen")
- `✕ Fout` — rood
- `● Live` — paars, animated pulse

### 🔷 Logo — SVG icon naast de naam
Het Shrync-logo in de sidebar heeft nu een klein SVG-icoontje gekregen (blauwe
afgeronde tegel met een gestileerde bliksem/conversiepijl) links naast de naam.
Dit geeft de sidebar een herkenbaar ankerpunt en maakt de app direct
identificeerbaar in een tabblad of bij gedeeld scherm.

### 📜 Scrollbar — slanker
De scrollbar is teruggebracht van 4px naar 3px breed met licht afgeronde eindjes.
Subtiele verbetering die de interface iets strakker maakt.

## Bugfixes (overgenomen uit v0.42 en v0.43)

### 🐛 Ondertiteling — `target_lang` not defined
`source_lang` en `target_lang` werden niet opgehaald in `run_subtitle_translation`,
waardoor vertaaljobs crashten met een `NameError`. Beide variabelen worden nu
direct aan het begin van de functie opgehaald via `get_subtitle_setting()`.

### 🐛 Ondertiteling — brontaal werd genegeerd
De vertaalfunctie gebruikte nog `pick_best_english_stream()` in plaats van de
configureerbare `pick_best_source_stream(streams, source_lang)`. De ingestelde
brontaal werd daardoor volledig genegeerd.

### 🐛 Geconverteerd bestand groter dan origineel
Bij bepaalde bronbestanden (bijv. Bluray-rips met veel beweging of detail) kon
het H.265 resultaat groter uitvallen dan het origineel. Het origineel werd dan
overschreven door een groter bestand. Er is nu een grootte-check toegevoegd
vóór `os.rename()`: is het resultaat even groot of groter, dan wordt het
weggegooid en het origineel behouden. De job verschijnt in de geschiedenis als
`⊘ Skip` met de reden erbij.

## Technisch

- `stat-card` omgebouwd naar flex-layout met `.stat-icon` en `.stat-text` slots
- `.stats-grid` gebruikt nu `gap:1px` met `background:var(--border)` voor naadloze strip
- `.progress-bar` verhoogd naar 7px, `overflow:visible`, `.progress-fill::after` shimmer animatie
- `.badge` border-radius van `20px` naar `5px`, font-size van `10px` naar `9px`, uppercase
- SVG logo inline in `.sidebar-top` — geen externe assets nodig
- Scrollbar `::-webkit-scrollbar` breedte van 4px naar 3px
- Alle badge inline-styles vervangen door CSS klassen
- Versie `0.43` → `0.45`
