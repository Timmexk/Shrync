# Shrync v0.59 — Release notes & werkinstructies

## Werkinstructies — Docker Hub upload

1. `cd C:\pad\naar\shrync`
2. `docker build --no-cache --platform linux/amd64 --build-arg SHRYNC_VERSION=0.59 -t timmex91/shrync:latest -t timmex91/shrync:0.59 .`
3. `docker push timmex91/shrync:latest && docker push timmex91/shrync:0.59`
4. Unraid: Docker → Shrync → Edit → Repository `timmex91/shrync:0.59` → Apply

---

## Visuele vernieuwing — professioneler icoonsysteem en kleurgebruik

**Probleem:** de interface gebruikte zo'n dertig verschillende emoji/Unicode-
tekens als iconen (⏳⚡✓⚠️💾▦ enz.). Die renderen inconsistent per platform —
soms als kleurrijke emoji, soms als platte tekens — en dat gaf de UI een
onafgewerkte, "AI-gegenereerde" uitstraling. Daarnaast gebruikten de
statistiekkaarten zes verschillende accentkleuren, waarvan er maar twee
(rood voor fouten, groen voor succes) echt betekenis hadden.

**Aanpak:**
- Eén consistente inline SVG-icoonset (dunne lijnstijl, kleurt automatisch
  mee met de tekstkleur) ter vervanging van alle emoji/Unicode-iconen in
  navigatie, statistiekkaarten, badges, lege-status-schermen, thema-
  schakelaar, zoekbalk en diagnostiek.
- Statistiekkaarten teruggebracht naar drie doelgerichte kleuren: blauw
  (neutrale info), groen (positief), rood (fouten) — in plaats van zes
  willekeurige tinten. Vendor-specifieke badges (NVIDIA/AMD/Intel) en de
  Conv/Sub-badge-onderscheiding zijn ongemoeid gelaten, want die kleuren
  hebben wél een functionele betekenis.
- Ongebruikt geworden CSS opgeruimd (`--cyan`, `.stat-icon.cyan/.purple/
  .orange`, `.c-cyan`).

Puur visueel — geen wijzigingen aan functionaliteit. Getest met een
Playwright-doorloop van elke pagina in beide thema's, geen consolefouten.

---

## Docker-image — integriteit en betrouwbaarheid

- **Checksum-verificatie van de ffmpeg-download**: de build haalt nu ook
  BtbN's `checksums.sha256` op en verifieert het ffmpeg-archief daarmee
  vóór het uitpakken. Een corrupte of gemanipuleerde download breekt de
  build nu af in plaats van stilzwijgend door te gaan.
- **HEALTHCHECK** toegevoegd op `/api/config`, zodat Docker/Unraid een
  vastgelopen container (proces leeft, maar reageert niet) kan detecteren.
- **`.dockerignore`** toegevoegd — ontbrak volledig.
