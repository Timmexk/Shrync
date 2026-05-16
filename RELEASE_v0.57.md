# Shrync v0.57 — Release notes & werkinstructies

## Werkinstructies — Docker Hub upload

1. `cd C:\pad\naar\shrync`
2. `docker build --no-cache --platform linux/amd64 -t timmex91/shrync:latest -t timmex91/shrync:0.57 .`
3. `docker push timmex91/shrync:latest && docker push timmex91/shrync:0.57`
4. Unraid: Docker → Shrync → Edit → Repository `timmex91/shrync:0.57` → Apply

---

## Verbetering — Natuurlijkere ondertitelvertaling

**Probleem:** vertalingen kwamen er erg letterlijk uit. Een zin als
*"I don't have any idea what you're trying to say"* werd vertaald als
*"Ik heb er geen idee van wat je probeert te zeggen"* in plaats van
het veel natuurlijkere *"Ik snap niet wat je bedoelt."*

**Oorzaak:** de system prompt gaf het model te weinig richting. Kleine
lokale modellen zoals Gemma 4B vallen bij gebrek aan concrete instructies
terug op woord-voor-woord vertaling.

**Aanpak — drie verbeteringen in de prompt:**

**1. Duidelijk doel:** het model krijgt nu expliciet mee dat het doel
een professionele bioscoopvertaling is, niet een letterlijke vertaling.
*"Prioritize how native Dutch speakers actually speak"* geeft het model
de vrijheid om zinnen te herstructureren.

**2. Concrete regels:**
- Idioom aanpassen aan Nederlandse equivalenten
- Zinsstructuur omgooien voor natuurlijke flow
- Register bewaren: informeel blijft informeel, grappig blijft grappig
- Schuttingtaal en slang niet censureren
- Voorkeur voor omgangstaal boven formeel taalgebruik
- Korte krachtige zinnen blijven kort

**3. Few-shot voorbeelden:** kleine modellen reageren veel beter op
concrete voor/na-voorbeelden dan op abstracte instructies:

| Engels | Slecht (letterlijk) | Goed (natuurlijk) |
|--------|--------------------|--------------------|
| I have no idea what you mean | Ik heb er geen idee van wat je bedoelt | Ik snap niet wat je bedoelt |
| That's not something I'd want to do | Dat is niet iets wat ik zou willen doen | Dat doe ik liever niet |
| You did good, man | Je hebt het goed gedaan, man | Goed gedaan, man |
