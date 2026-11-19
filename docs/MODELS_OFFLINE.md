# Как вшить модели и развернуть РечьКонтроль на сервере без интернета

Пошаговая инструкция. Делается в два места:

- **Машина А** - любой компьютер с интернетом и Docker (например, свой ноутбук).
- **Сервер** - машина банка, где интернета может не быть, но есть Docker.

Какие модели нужны:

- **Whisper small** - распознавание речи. Вшивается в образ.
- **multilingual-e5-small** - поиск фраз по смыслу. Вшивается в образ.
- **Qwen2.5-7B** (около 4,7 ГБ) - языковая модель, необязательна. Переносится отдельно, через Ollama.

---

## Часть 1. Машина А: собрать образ с моделями

1. Скачать проект:
   ```
   git clone https://github.com/olegbub07/rechkontrol.git
   cd rechkontrol
   ```
2. Создать файл настроек из примера:
   ```
   cp .env.example .env
   ```
   На Windows в PowerShell: `copy .env.example .env`
3. Открыть `.env` и заполнить:
   - `POSTGRES_PASSWORD=` - пароль базы, только буквы и цифры, например `Rk2026dbPass77`.
   - `BAKE_MODELS=1` - добавить эту строку, она и означает «вшить модели».
   - `SEMANTIC_MATCH=auto` - включить поиск по смыслу.
4. Собрать образ (первый раз займёт 10-20 минут, модели скачаются внутрь):
   ```
   docker compose build --build-arg BAKE_MODELS=1 app
   ```
5. Проверить, что образ появился:
   ```
   docker images rechkontrol
   ```
   Должна быть строка `rechkontrol  latest`, размер около 3 ГБ.
6. (Необязательно) Проверить, что всё работает без интернета:
   ```
   docker run --rm --network none rechkontrol:latest python -c "print('ok')"
   ```

## Часть 2. Машина А: упаковать всё в один архив

Вариант через скрипт (Linux, macOS или Git Bash на Windows):
```
bash scripts/offline_bundle.sh
```
С языковой моделью: `WITH_LLM=1 bash scripts/offline_bundle.sh`

Тот же результат вручную (подходит и для PowerShell):
```
docker pull postgres:17
docker save -o rechkontrol-images.tar rechkontrol:latest postgres:17
```

На выходе файл `rechkontrol-images.tar` (несколько ГБ). Рядом с ним положить:

- `docker-compose.yml`
- `.env` (уже заполненный в части 1)

### Если нужна языковая модель Qwen

1. Скачать образ Ollama и добавить его в архив:
   ```
   docker pull ollama/ollama:latest
   docker save -o ollama-image.tar ollama/ollama:latest
   ```
2. Запустить Ollama и скачать модель:
   ```
   docker compose --profile llm up -d ollama
   docker compose exec ollama ollama pull qwen2.5:7b-instruct
   ```
3. Узнать имя тома с моделями (оканчивается на `_ollama`):
   ```
   docker volume ls
   ```
4. Выгрузить модели в архив (вместо `ИМЯ_ТОМА` подставить имя из шага 3):
   ```
   docker run --rm -v ИМЯ_ТОМА:/v -v "${PWD}:/b" alpine tar czf /b/ollama-models.tgz -C /v .
   ```

## Часть 3. Перенести на сервер

Скопировать в одну папку на сервере (флешка, сетевой диск):

- `rechkontrol-images.tar`
- `docker-compose.yml`
- `.env`
- при необходимости `ollama-image.tar` и `ollama-models.tgz`

## Часть 4. Сервер: запустить

1. Перейти в папку с файлами и загрузить образы:
   ```
   docker load -i rechkontrol-images.tar
   ```
2. Запустить (ничего не скачивается, `build` не нужен):
   ```
   docker compose up -d --no-build
   ```
3. Проверить статус, у `app` через минуту должно быть `healthy`:
   ```
   docker compose ps
   ```
4. Открыть в браузере на сервере: `http://127.0.0.1:8080`
   Другой порт задаётся строкой `APP_PORT=8081` в `.env`.

### Языковая модель на сервере (если переносили)

1. Загрузить образ Ollama и запустить его, чтобы создался том:
   ```
   docker load -i ollama-image.tar
   docker compose --profile llm up -d --no-build ollama
   ```
2. Узнать имя тома (`docker volume ls`, оканчивается на `_ollama`) и распаковать модели в него:
   ```
   docker run --rm -v ИМЯ_ТОМА:/v -v "${PWD}:/b" alpine tar xzf /b/ollama-models.tgz -C /v
   ```
   Если образа `alpine` на сервере нет, перенесите его так же: `docker save -o alpine.tar alpine` на машине А и `docker load -i alpine.tar` на сервере.
3. В `.env` поставить `LLM_PROVIDER=ollama` и перезапустить:
   ```
   docker compose --profile llm up -d --no-build
   ```

## Обновление версии

Повторить части 1-4. Данные и ключ шифрования лежат в отдельных томах (`pgdata`, `appdata`, `appkeys`) и при обновлении не теряются.

**Важно:** без тома `appkeys` (ключ шифрования) старые расшифровки не открыть. Делайте его резервную копию отдельно от данных.

## Если что-то не так

- `Задайте POSTGRES_PASSWORD в .env` - не заполнен пароль в `.env`.
- Статус `unhealthy` - посмотрите журнал: `docker compose logs app --tail 50`.
- `pull access denied` на сервере - образ не загружен, повторите `docker load`, а запуск делайте только с `--no-build`.
- Поиск по смыслу не работает - проверьте, что образ собирали с `BAKE_MODELS=1` и в `.env` стоит `SEMANTIC_MATCH=auto`.

Подробнее о вариантах развёртывания: `docs/DEPLOY.md`.
