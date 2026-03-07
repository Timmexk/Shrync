# Shrync v0.41

## Wijzigingen

### Verwijderd — Uitsluitingen
De uitsluitingen functionaliteit is verwijderd. Dit betreft:
- Navigatie item "Uitsluitingen"
- De volledige uitsluitingen beheerpagina
- `/api/exclusions` API endpoints
- `exclusions` database tabel (wordt niet meer aangemaakt)
- Exclusie checks in de scan loop en subtitle dispatcher

Bestaande databases met een `exclusions` tabel ondervinden geen problemen — de tabel wordt simpelweg niet meer gebruikt.
