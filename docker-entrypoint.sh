#!/bin/sh
# Если модели вшиты в образ (сборка с BAKE_MODELS=1), подключаем их в data/models ссылками:
# том appdata остаётся маленьким, а сервер без интернета ничего не скачивает.
set -e
mkdir -p /app/data/models
if [ -d /opt/models ]; then
    for m in /opt/models/*; do
        [ -e "$m" ] || continue
        name=$(basename "$m")
        if [ ! -e "/app/data/models/$name" ]; then
            ln -s "$m" "/app/data/models/$name"
        fi
    done
fi
exec "$@"
