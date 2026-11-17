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

# Внутри контейнера слушаем все интерфейсы контейнера; наружу порт публикуется
# только на 127.0.0.1 хоста (docker-compose.yml).
ENV APP_HOST=0.0.0.0 \
    APP_PORT=8080 \
    ALLOW_REMOTE=true

RUN useradd --create-home --uid 1000 rk && mkdir -p /app/data && chown rk:rk /app/data
USER rk
EXPOSE 8080
CMD ["python", "run.py"]
