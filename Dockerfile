# Образ РечьКонтроль для docker compose (см. docs/RUN.md, раздел PostgreSQL).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app
COPY run.py ./
COPY scripts ./scripts
COPY samples ./samples

# Модели внутрь образа (для сервера без интернета): docker compose build --build-arg BAKE_MODELS=1
# или BAKE_MODELS=1 в .env. Whisper small и e5 вместе добавляют к образу около 1 ГБ.
ARG BAKE_MODELS=0
RUN if [ "$BAKE_MODELS" = "1" ]; then python scripts/download_models.py --dir /opt/models && chmod -R a+rX /opt/models; fi
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Внутри контейнера слушаем все интерфейсы контейнера; наружу порт публикуется
# только на 127.0.0.1 хоста (docker-compose.yml).
ENV APP_HOST=0.0.0.0 \
    APP_PORT=8080 \
    ALLOW_REMOTE=true

RUN useradd --create-home --uid 1000 rk && mkdir -p /app/data /app/keys && chown rk:rk /app/data /app/keys && chmod 700 /app/keys
USER rk
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "run.py"]
