# Развёртывание на сервере банка

Сервер банка обычно без интернета и часто на Windows. Ниже три варианта, от простого к запасному.

## Вариант А. docker compose, одна команда (есть интернет при установке)

- `start.bat` (Windows) или `docker compose up -d --build`: приложение + PostgreSQL 17, с `-Llm` ещё Ollama.
- Модели Whisper и e5 можно сразу вшить в образ: `BAKE_MODELS=1` в `.env`. Тогда при первом звонке ничего не скачивается.
- У контейнера приложения есть healthcheck по `/api/health`, `docker compose ps` показывает `healthy`.
- Плюсы: уже работает, одна команда, обновление = `git pull` + та же команда.
- Минусы: нужен Docker и интернет на время сборки. На Windows Server Docker Desktop официально не поддерживается, нужен Linux-сервер, WSL2 или Docker Engine.

## Вариант Б. Офлайн-комплект образов (рекомендуем)

На машине с интернетом:

```
bash scripts/offline_bundle.sh              # приложение с моделями + postgres:17
WITH_LLM=1 bash scripts/offline_bundle.sh   # плюс образ Ollama
```

Получается папка `dist/rechkontrol-offline/` с `rechkontrol-images.tar`, `docker-compose.yml`, `.env.example`. На сервере:

```
docker load -i rechkontrol-images.tar
copy .env.example .env      # задать POSTGRES_PASSWORD (буквы и цифры)
docker compose up -d
```

- Модель LLM (qwen2.5:7b-instruct, около 4,7 ГБ) в образ Ollama не входит: на машине с интернетом `ollama pull`, затем перенести каталог моделей Ollama (`~/.ollama/models`) в том `ollama` на сервере.
- Плюсы: на сервере ни pip, ни интернета, всё проверено заранее, одинаково на любом сервере с Docker.
- Минусы: архив несколько ГБ (образ приложения с моделями около 3,2 ГБ в распакованном виде), всё ещё нужен Docker.

## Вариант В. Портативная папка для Windows без Docker (запасной)

Если в банке Docker запрещён: папка с встроенным Python (python-3.12 embeddable), уже установленными пакетами, моделями в `data\models` и `start.bat`, который запускает `python run.py`. База SQLite или PostgreSQL банка (`DATABASE_URL`).

- Плюсы: не нужен Docker и права администратора, копируется флешкой.
- Минусы: собирать отдельно под Windows x64, PostgreSQL и Ollama ставить отдельно, обновлять вручную. Пока не сделано, сейчас есть `start.bat -NoDocker` (нужен Python и интернет для pip).

## Модели без интернета

`python scripts/download_models.py --dir ПАПКА` скачивает Whisper (`WHISPER_MODEL`) и e5 в одну папку. Её содержимое кладётся в `data\models` на сервере.


Пошаговая инструкция по сборке с моделями и переносу на сервер без интернета: [MODELS_OFFLINE.md](MODELS_OFFLINE.md).
