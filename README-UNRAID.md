# Shrync — Unraid installatie

## Vereisten
- Unraid 6.10+
- Nvidia GPU met NVENC support (Quadro P4000, RTX, etc.)
- Nvidia driver plugin geinstalleerd in Unraid
- Docker Compose plugin geinstalleerd in Unraid

## Installatie in 3 stappen

### 1. Bestanden klaarzetten
Maak een map aan op je server en kopieer alle bestanden daarheen:
```bash
mkdir -p /mnt/user/appdata/shrync
cp -r . /mnt/user/appdata/shrync/
cd /mnt/user/appdata/shrync
```

### 2. Controleer docker-compose.yml
Open docker-compose.yml en controleer de volumes:
```yaml
volumes:
  - /mnt/user/appdata/shrync/config:/config   # database & config
  - /mnt/user/Transcode:/transcode            # cache map
  - /mnt/user/Films:/media/films              # jouw films
  - /mnt/user/Series:/media/series            # jouw series
  - "/mnt/user/Kinder Films:/media/kinder-films"
```
Pas de paden aan naar jouw situatie als ze anders zijn.

### 3. Bouwen en starten
```bash
cd /mnt/user/appdata/shrync
docker-compose build --no-cache
docker-compose up -d
```

Of gebruik het meegeleverde script:
```bash
bash build.sh
```

## Toegang
Open je browser en ga naar: `http://UNRAID-IP:8988`

## GPU werkt niet?
Controleer of de Nvidia driver plugin actief is in Unraid.
Test GPU toegang:
```bash
docker exec shrync nvidia-smi
```

## Bestanden bijwerken (nieuwe versie)
```bash
cd /mnt/user/appdata/shrync
docker-compose down
# kopieer nieuwe bestanden
docker-compose build --no-cache
docker-compose up -d
```

## Logs bekijken
```bash
docker-compose logs -f shrync
```
