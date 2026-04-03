# Shrync v0.51 — Release notes & werkinstructies

## Werkinstructies — Docker Hub upload

1. ZIP uitpakken: `cd C:\pad\naar\shrync`
2. Bouwen: `docker build --no-cache --platform linux/amd64 -t timmex91/shrync:latest -t timmex91/shrync:0.51 .`
3. Pushen: `docker push timmex91/shrync:latest && docker push timmex91/shrync:0.51`
4. Unraid: Docker → Shrync → Edit → Repository `timmex91/shrync:0.51` → Apply

---

## Bugfixes — stabiliteit

### 🐛 Scannen loopt vast bij grote bibliotheken

**Oorzaak 1 — Sequentieel scannen blokkeerde workers:**
Bij het opstarten werden alle bibliotheken na elkaar gescand in de
hoofdthread van `initial_startup()`. Pas nadat elke scan volledig klaar was
startten de workers. Bij 3 bibliotheken van 600+ bestanden betekende dat
potentieel 30+ minuten wachten voor de eerste conversie kon beginnen.

**Fix:** Workers starten nu als eerste bij opstarten. Scans lopen parallel
in eigen threads per bibliotheek, zodat items direct verwerkt worden zodra
ze de wachtrij ingaan.

**Oorzaak 2 — ffprobe timeout blokkeerde scan:**
`needs_conversion()` riep ffprobe aan met `timeout=8` maar ving
`TimeoutExpired` niet apart op. Bij een bestand dat lang duurde om te
openen (bijv. beschadigd of op trage NFS mount) bleef de scan hangen of
ging door met onbetrouwbare resultaten.

**Fix:** `TimeoutExpired` wordt nu apart afgevangen. Een timeout betekent
dat het bestand overgeslagen wordt (niet toegevoegd aan de wachtrij) zodat
de scan gewoon doorgaat met de rest.

---

### 🐛 Conversies mislukken zonder duidelijke reden

**Oorzaak 1 — ETA berekening had foutieve `* 25` factor:**
De resterende tijd werd berekend als `(resterende_seconden / fps) * 25`.
Die `* 25` was een overblijfsel van een oude framerate-aanname en had er
nooit in mogen zitten. Dit gaf verkeerde ETA-waarden en kon in edge cases
de progressberekening verstoren.

**Fix:** ETA is nu correct: `resterende_seconden / fps`.

**Oorzaak 2 — `stderr_thread.join(timeout=5)` was te kort:**
Na een lange conversie had de stderr-leesthread soms meer dan 5 seconden
nodig om alle output te verwerken. Hierdoor werd de foutmelding afgekapt
en verscheen een incomplete of lege error in de geschiedenis.

**Fix:** Timeout verhoogd naar 30 seconden.

**Oorzaak 3 — ffmpeg loglevel vervuilde de progress pipe:**
Zonder expliciete loglevel schreef ffmpeg ook info/verbose output naar
stderr, wat soms de progress-parsing op stdout beïnvloedde.

**Fix:** `-loglevel warning` toegevoegd aan alle ffmpeg commands (NVENC,
AMF, QSV, CPU). Alleen echte waarschuwingen en fouten worden gelogd.

---

## Nieuwe functies (v0.50, in deze release meegenomen)

- **Activity feed** op dashboard — tijdlijn van de laatste 20 acties
- **Actieve conversie detailkaart** — FPS, origineel, verstreken, encoder
- **Uitsluitingspatronen per bibliotheek** — regex, één per regel
- **Vertaalkwaliteit per bibliotheek** — dropdown in Instellingen →
  AI Ondertiteling (Snel / Normaal / Grondig)

