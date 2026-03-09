# Shrync v0.42

## Bugfixes

### Ondertiteling — `target_lang` not defined
Bij het vertalen van ondertitels crashte de vertaaljob met de foutmelding
`name 'target_lang' is not defined`. Dit had twee oorzaken:

**Oorzaak 1:** In de functie `run_subtitle_translation` werden `source_lang` en
`target_lang` nooit opgehaald uit de instellingen. Ze waren wel aanwezig in de
onderliggende `translate_blocks_ollama` functie, maar de aanroepende functie had
ze ook nodig — voor de output bestandsnaam (`.nl.srt`) en voor het opslaan in de
geschiedenis.

**Oorzaak 2:** Dezelfde functie gebruikte nog `pick_best_english_stream()` — een
hardcoded Engelse streamkiezer — in plaats van de configureerbare
`pick_best_source_stream(streams, source_lang)`. Hierdoor werd de ingestelde
brontaal volledig genegeerd.

**Fix:** `source_lang` en `target_lang` worden nu direct opgehaald aan het begin
van `run_subtitle_translation`, en de streamkiezer gebruikt de ingestelde brontaal.
