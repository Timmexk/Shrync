#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
# Shrync — Docker Hub build & push script
# ══════════════════════════════════════════════════════════════════════════════
# Gebruik: ./build.sh <dockerhub-gebruikersnaam> [versienummer]
# Voorbeeld: ./build.sh mijngebruikersnaam 0.01

set -e

USERNAME=${1:-"timmex91"}
VERSION=${2:-"0.05"}
IMAGE="${USERNAME}/shrync"

echo "┌─────────────────────────────────────────────────────┐"
echo "│  Shrync v${VERSION} — Docker Hub build & push              │"
echo "└─────────────────────────────────────────────────────┘"
echo ""
echo "  Image  : ${IMAGE}"
echo "  Tags   : latest, ${VERSION}"
echo ""

# Login controleren
if ! docker info 2>/dev/null | grep -q "Username"; then
    echo "  ✗ Niet ingelogd bij Docker Hub"
    echo "    Voer eerst uit: docker login"
    exit 1
fi

echo "  ▶ Image bouwen..."
docker build \
    --platform linux/amd64 \
    --build-arg SHRYNC_VERSION="${VERSION}" \
    -t "${IMAGE}:latest" \
    -t "${IMAGE}:${VERSION}" \
    .

echo ""
echo "  ▶ Pushen naar Docker Hub..."
docker push "${IMAGE}:latest"
docker push "${IMAGE}:${VERSION}"

echo ""
echo "  ✓ Klaar! Beschikbaar als:"
echo "    ${IMAGE}:latest   (meest recent)"
echo "    ${IMAGE}:${VERSION}     (versie)"
echo ""
echo "  Zowel CPU als Nvidia GPU worden ondersteund door deze image."
echo "  Gebruikers hoeven geen aparte image te kiezen."
