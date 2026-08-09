# Shrync v0.60 — Release notes & werkinstructies

## Werkinstructies — Docker Hub upload

1. `cd C:\pad\naar\shrync`
2. `docker build --no-cache --platform linux/amd64 --build-arg SHRYNC_VERSION=0.60 -t timmex91/shrync:latest -t timmex91/shrync:0.60 .`
3. `docker push timmex91/shrync:latest && docker push timmex91/shrync:0.60`
4. Unraid: Docker → Shrync → Edit → Repository `timmex91/shrync:0.60` → Apply

Gebruik je een gepinde `FFMPEG_TAG` vanwege een oudere GPU-driver? Geef die
dan opnieuw mee bij het bouwen — zie DOCKER_HUB_UPLOAD.md.

---

## Visuele vernieuwing — zachte pastel dashboardstijl

Volledige restyling van het kleuren- en vormsysteem, geïnspireerd op een
zacht pastel dashboard-ontwerp, in zowel light als dark mode.

**Wat er is veranderd:**
- Nieuw kleurenpalet: gedempt lavendel/leisteen-canvas met opake, ronder
  afgeronde panelen in plaats van de donkerblauwe glow/glass-stijl.
- Statistiekkaarten zijn nu losse, volledig pastel-getinte kaarten (mint/
  sky/rose/amber/slate/rood) in plaats van icoontje-only tinting — elke
  kleur is gekoppeld aan een echte categorie (rood = fout, mint = positief,
  sky = actief, slate = neutraal), geen decoratieve regenboog.
- Badges zijn nu volledig ronde pastel-pillen i.p.v. dunne outline-tags.
- Navigatie: actieve pagina krijgt een effen donkere ronde pil-highlight
  in plaats van een linker accentrand.
- Achtergrond-glow-orbs en rastertextuur verwijderd voor een rustiger,
  vlakker canvas.

**Bewuste keuze — geen nepgrafiekjes:** het referentieontwerp toont kleine
staafdiagrammetjes in elke statistiekkaart. Omdat Shrync's queue-tellers
(in wachtrij/bezig/vandaag klaar) geen onderliggende trendreeks hebben om
zo'n grafiekje op te baseren, zijn die bewust weggelaten — een decoratief
maar nep grafiekje in een monitoring-tool zou misleidend zijn.

**Bijgevangen bugs tijdens het herstijlen:** een aantal UI-elementen was
hardcoded voor het donkere thema en onleesbaar in het lichte thema —
o.a. het "Shrync"-wordmerk (witte gradient-tekst op een wit paneel), de
modal-achtergrond (altijd donkerblauw, ongeacht thema), en verschillende
knop-/badge-tekstkleuren (Pause/Danger/Accent-knoppen, encoder-badges)
die te weinig contrast hadden op een licht paneel. Allemaal gefixt door
consistent gebruik van thema-bewuste kleurtokens.

Getest met een volledige Playwright-doorloop van elke pagina in beide
thema's, mét en zonder voorbeelddata (queue/history/library-rijen),
inclusief modal- en tab-interacties — geen consolefouten.
