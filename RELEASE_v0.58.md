# Shrync v0.58 — Release notes & werkinstructies

## Werkinstructies — Docker Hub upload

1. `cd C:\pad\naar\shrync`
2. `docker build --no-cache --platform linux/amd64 --build-arg SHRYNC_VERSION=0.58 -t timmex91/shrync:latest -t timmex91/shrync:0.58 .`
3. `docker push timmex91/shrync:latest && docker push timmex91/shrync:0.58`
4. Unraid: Docker → Shrync → Edit → Repository `timmex91/shrync:0.58` → Apply

---

## Onderhoud — volledige review en dependency-update

Deze release bevat geen nieuwe functies, maar een volledige doorlichting van
de codebase: dependencies bijgewerkt, een paar losstaande bugs opgelost en
verweesde bestanden opgeruimd.

### Dependencies bijgewerkt

`requirements.txt` stond op versies uit begin 2025. Bijgewerkt naar de
actuele stabiele releases (compatibiliteit geverifieerd door een schone
installatie in een virtualenv):

| Package | Was | Nu |
|---|---|---|
| fastapi | 0.115.12 | 0.140.0 |
| uvicorn[standard] | 0.32.1 | 0.51.0 |
| pydantic | 2.10.6 | 2.13.4 |
| aiofiles | 24.1.0 | 25.1.0 |
| starlette | 0.46.2 | 1.3.1 |
| jinja2 / watchdog | — | ongewijzigd, al actueel |

### Bugfix — `--build-arg SHRYNC_VERSION` deed niets

`build.sh` en `DOCKER_HUB_UPLOAD.md` geven al sinds vroege versies
`--build-arg SHRYNC_VERSION=X.Y` mee aan `docker build`, maar de Dockerfile
had geen `ARG SHRYNC_VERSION` declaratie. Docker negeert een build-arg
zonder bijbehorend `ARG` stilzwijgend, dus de versie in het image kwam
altijd overeen met wat er hardcoded in de Dockerfile stond op het moment
van builden — nooit met de waarde die werd meegegeven. Opgelost door
`ARG SHRYNC_VERSION=0.58` toe te voegen vóór de `LABEL`/`ENV` regels die
hem gebruiken.

### Bugfix — handmatige queue-endpoints accepteerden elk pad

`/api/queue/add` en `/api/subtitle/queue/add` namen een `file_path` van de
client aan met alleen een `os.path.exists()` check. In tegenstelling tot de
bibliotheekscan en de file watcher — die alleen bestanden binnen een
geconfigureerde bibliotheek oppikken — konden deze twee endpoints elk pad
in de container in de conversie-/vertaalwachtrij zetten, inclusief
bestanden buiten `/media`. Omdat een conversietaak het bronbestand na
afloop verwijdert en vervangt, kon dit gebruikt worden om willekeurige
voor de container leesbare bestanden te laten overschrijven. Beide
endpoints valideren nu dat het pad binnen een geconfigureerde bibliotheek
valt én een ondersteunde video-extensie heeft.

### Opgeruimd

- `Dockerfile.nvidia` verwijderd — verweesd bestand uit vóór de overstap
  naar één universele CPU/GPU-image (verouderde CUDA-base, geen
  GPU-detectie-entrypoint, nergens meer naar verwezen).
- `templates/index.html.bak` verwijderd — losse back-up die niet door de
  applicatie gebruikt wordt.
- `.gitignore` toegevoegd (ontbrak volledig) zodat `__pycache__/`, lokale
  virtualenvs en databestanden niet per ongeluk meegecommit worden.
- Versienummer overal consistent gemaakt: `README.md` verwees nog naar
  `v0.01` terwijl de rest van het project al op v0.57 stond.
- Placeholder-waarden in Dockerfile-labels (`JOUWGITHUBUSERNAME`) vervangen
  door de daadwerkelijke GitHub-organisatie.
