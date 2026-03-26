# Shrync v0.46 — Docker Hub upload instructie

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
./build.sh <jouw-dockerhub-gebruikersnaam> 0.46
```

**Voorbeeld:**
```bash
./build.sh tijmen 0.46
```

Dit doet automatisch:
1. Image bouwen voor `linux/amd64`
2. Taggen als `tijmen/shrync:latest` én `tijmen/shrync:0.46`
3. Beide tags pushen naar Docker Hub

---

## Stap 4 — Unraid container updaten

In Unraid:

1. Ga naar **Docker** → zoek de Shrync container
2. Klik op het icoontje → **Edit**
3. Verander het **Repository** veld naar:
   ```
   <jouw-gebruikersnaam>/shrync:0.46
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
  --build-arg SHRYNC_VERSION="0.46" \
  -t <gebruikersnaam>/shrync:latest \
  -t <gebruikersnaam>/shrync:0.46 \
  .

# Pushen
docker push <gebruikersnaam>/shrync:latest
docker push <gebruikersnaam>/shrync:0.46
```

---

## Verifiëren

Na het pushen kun je controleren of de image beschikbaar is:

```bash
docker pull <gebruikersnaam>/shrync:0.46
```

Of bekijk het op https://hub.docker.com/r/<gebruikersnaam>/shrync

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
