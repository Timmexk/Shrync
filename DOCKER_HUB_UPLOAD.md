# Shrync v0.62 — Docker Hub upload instructie

## Vereisten

- Docker Desktop geïnstalleerd en actief (of Docker Engine op Linux)
- Docker Hub account met schrijfrechten op jouw repository
- Ingelogd via `docker login`

---

## Stap 1 — Inloggen bij Docker Hub

```bash
docker login
```

Voer je Docker Hub gebruikersnaam en wachtwoord (of access token) in.
Een access token aanmaken doe je via: https://hub.docker.com/settings/security

---

## Stap 2 — ZIP uitpakken

Pak de `shrync.zip` uit op je machine, navigeer naar de map:

```bash
unzip shrync.zip
cd shrync
```

---

## Stap 3 — Image bouwen en pushen

Gebruik het meegeleverde `build.sh` script:

```bash
chmod +x build.sh
./build.sh <jouw-dockerhub-gebruikersnaam> 0.62
```

**Voorbeeld:**
```bash
./build.sh tijmen 0.62
```

Dit doet automatisch:
1. Image bouwen voor `linux/amd64`
2. Taggen als `tijmen/shrync:latest` én `tijmen/shrync:0.62`
3. Beide tags pushen naar Docker Hub

---

## Stap 4 — Unraid container updaten

In Unraid:

1. Ga naar **Docker** → zoek de Shrync container
2. Klik op het icoontje → **Edit**
3. Verander het **Repository** veld naar:
   ```
   <jouw-gebruikersnaam>/shrync:0.62
   ```
   Of gebruik `:latest` om altijd de nieuwste versie te trekken:
   ```
   <jouw-gebruikersnaam>/shrync:latest
   ```
4. Klik **Apply**
5. Unraid trekt automatisch de nieuwe image en herstart de container

---

## Handmatig bouwen (zonder build.sh)

Als je het liever handmatig doet:

```bash
# Bouwen
docker build \
  --platform linux/amd64 \
  --build-arg SHRYNC_VERSION="0.62" \
  -t <gebruikersnaam>/shrync:latest \
  -t <gebruikersnaam>/shrync:0.62 \
  .

# Pushen
docker push <gebruikersnaam>/shrync:latest
docker push <gebruikersnaam>/shrync:0.62
```

---

## Verifiëren

Na het pushen kun je controleren of de image beschikbaar is:

```bash
docker pull <gebruikersnaam>/shrync:0.62
```

Of bekijk het op https://hub.docker.com/r/<gebruikersnaam>/shrync

---

## Oudere Nvidia GPU / driver — NVENC API version foutmelding

Krijg je bij het converteren een fout als *"does not support the required
nvenc API version"* of *"minimum required Nvidia driver for nvenc is X.XX"*?
Dan is de nieuwste ffmpeg-build (die de container standaard gebruikt) gebouwd
tegen een NVENC-SDK-versie die een nieuwere driver vereist dan jouw GPU
ondersteunt — dit komt vaak voor bij oudere kaarten (bijv. Pascal-generatie
Quadro's) die op een bepaalde driverversie geplafonneerd zitten.

Los dit op door een oudere ffmpeg-build te kiezen via `FFMPEG_TAG`:

```bash
docker build \
  --platform linux/amd64 \
  --build-arg SHRYNC_VERSION="0.62" \
  --build-arg FFMPEG_TAG="autobuild-2026-05-31-13-22" \
  -t <gebruikersnaam>/shrync:latest \
  -t <gebruikersnaam>/shrync:0.62 \
  .
```

`autobuild-2026-05-31-13-22` is geverifieerd als de laatste BtbN-build die
nog NVENC API 13.0 gebruikt (nodig voor Nvidia-driver's onder de 610.00,
bijv. bij een Pascal-generatie Quadro naast een nieuwere kaart in dezelfde
Nvidia-plugin). De exacte bestandsnaam van die release hoef je niet te
weten — de Dockerfile zoekt die zelf op via de GitHub API.

**Let op — tag-datums zijn onbetrouwbaar als indicator.** BtbN's build-datum
zegt niets over welke NVENC-SDK-versie erin zit: die wordt bepaald door een
los gepinde commit in hun eigen `scripts.d/50-ffnvcodec.sh`, die soms wéken
vóór de officiële SDK-releasetag al naar de nieuwe versie wijst. Wil je zelf
een andere/nieuwere tag proberen, controleer dan niet de releasedatum maar
de daadwerkelijke header:
```bash
git clone -q https://github.com/BtbN/FFmpeg-Builds.git && cd FFmpeg-Builds
git show <tag>:scripts.d/50-ffnvcodec.sh | grep SCRIPT_COMMIT=  # eerste regel = primaire pin
git clone -q https://github.com/FFmpeg/nv-codec-headers.git && cd nv-codec-headers
git show <die-commit>:include/ffnvcodec/nvEncodeAPI.h | grep NVENCAPI_MINOR_VERSION
```
`MINOR_VERSION 0` → veilig voor driver's onder 610. `MINOR_VERSION 1` → vereist driver 610+.

Nadeel van een gepinde tag: die ffmpeg-build blijft dan bevroren op dat
moment (ook de CPU-encoders libx265/libx264), tot je 'm handmatig weer op
`latest` zet zodra je driver geüpdatet is.

---

## Let op — GPU support

De image werkt automatisch voor zowel CPU als Nvidia GPU.
Bij Unraid met je P4000: zorg dat in de container-instellingen
`Extra Parameters` de volgende waarde heeft:

```
--runtime=nvidia
```

En onder **Environment Variables**:
```
NVIDIA_VISIBLE_DEVICES=all
NVIDIA_DRIVER_CAPABILITIES=video,compute,utility
```

De container detecteert de GPU automatisch bij opstarten.
