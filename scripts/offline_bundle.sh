#!/usr/bin/env bash
# Офлайн-комплект для сервера без интернета (docs/DEPLOY.md, вариант Б).
# Запускать на машине с интернетом и Docker:  bash scripts/offline_bundle.sh [папка]
# На сервере: docker load -i rechkontrol-images.tar, затем .env и docker compose up -d
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-dist/rechkontrol-offline}"
mkdir -p "$OUT"
BAKE_MODELS=1 docker compose build app
docker pull postgres:17
IMAGES=(rechkontrol:latest postgres:17)
if [ "${WITH_LLM:-0}" = "1" ]; then
    docker pull ollama/ollama:latest
    IMAGES+=(ollama/ollama:latest)
fi
docker save -o "$OUT/rechkontrol-images.tar" "${IMAGES[@]}"
cp docker-compose.yml .env.example "$OUT/"
cp docs/DEPLOY.md "$OUT/README-DEPLOY.md"
echo "Готово: $OUT ($(du -sh "$OUT" | cut -f1)). Образы: ${IMAGES[*]}"
