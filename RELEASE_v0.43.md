# Shrync v0.43

## Bugfixes

### 🐛 Geconverteerd bestand groter dan origineel
In sommige gevallen — met name bij Bluray-rips met complexe beeldinhoud zoals
veel beweging, korrelstructuur of fijnmazige details — kon het geconverteerde
H.265 bestand groter zijn dan het origineel. Het bestand werd dan alsnog
opgeslagen en het kleinere origineel overschreven.

Dit deed zich voor bij hoge kwaliteitsinstellingen (bijv. NVENC Max, CQ 19)
waarbij de encoder zoveel bits reserveert om de gewenste kwaliteit te
garanderen dat de output groter uitvalt dan de bron.

**Fix:** Na elke conversie wordt de bestandsgrootte vergeleken. Is het
geconverteerde bestand even groot of groter dan het origineel, dan wordt het
weggegooid en het origineel behouden. De job verschijnt in de geschiedenis
met de status **Overgeslagen** (oranje badge) inclusief de reden, zodat je
altijd kunt zien wat er gebeurd is.

## Technisch

- Grootte-check toegevoegd in de conversiepipeline vóór `os.rename()`
- Nieuwe historystatus `skipped` met bijbehorende oranje badge in de UI
- Tijdelijk uitvoerbestand wordt netjes opgeruimd bij overschrijding
