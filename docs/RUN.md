# Запуск, настройка и разработка

## Требования

- Windows 10/11 или Linux, Python 3.11+
- Для реального STT: ~150 МБ на модель Whisper `base` и несколько ГБ RAM
- GPU не обязателен (`WHISPER_DEVICE=cpu`)

## Установка

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
python run.py
```

Либо одной командой: `.\run.ps1`.

Откройте http://127.0.0.1:8080.

Если порт занят предыдущим запуском:

```powershell
python run.py          # сообщит, что сервер уже работает
python run.py --replace
python run.py --stop
```

WinError 10048 — это занятый порт, не ошибка кода.

## Переменные `.env`

| Переменная | Смысл | По умолчанию |
|---|---|---|
| `APP_HOST` / `APP_PORT` | адрес | `127.0.0.1` / `8080`. Локальный запуск также открывает `localhost` |
| `APP_DEBUG` | OpenAPI `/docs` | `false` |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | первая учётка администратора, если её ещё нет | `admin` / пусто: пароль генерируется при первом запуске и пишется в `data/initial_credentials.txt` |
| `TRUSTED_PROXIES` | адреса reverse-proxy, от которых принимается `X-Forwarded-For` | пусто |
| `WHISPER_MODEL` | `tiny`, `base`, `small`, … | `base` |
| `WHISPER_DEVICE` | `cpu` или `cuda` | `cpu` |
| `WHISPER_COMPUTE_TYPE` | `int8` на CPU | `int8` |
| `MAX_PARALLEL_JOBS` | слоты очереди | `1` |
| `MAX_UPLOAD_MB` | размер файла | `80` |
| `MAX_DURATION_SEC` | лимит длины | `900` (15 мин) |
| `ALLOW_REMOTE` | слушать не только localhost | `false` |
| `WHISPER_BEAM_SIZE` | луч декодера, 1–5 | `5` |
| `WHISPER_BATCH_SIZE` | пакет для записей от 90 с | `8` |
| `WHISPER_CPU_THREADS` | потоки CPU, `0` — само | `0` |

`APP_HOST`, отличный от `127.0.0.1` / `localhost`, без `ALLOW_REMOTE=true` не запускается. Для сети перед приложением нужен TLS-прокси. Подробности — в [SECURITY.md](SECURITY.md) и [PERFORMANCE.md](PERFORMANCE.md).

Ключи `data/master.key` и `data/jwt.secret` создаются при первом старте.

## Модель Whisper

Ищется в `data/models/faster-whisper-<WHISPER_MODEL>/model.bin` (например `faster-whisper-small`). Если файла нет, модель один раз скачивается с Hugging Face в эту папку (нужна сеть). После этого контур офлайн. Скорость и точность на своей машине: `python scripts/benchmark.py` (см. [PERFORMANCE.md](PERFORMANCE.md)).

## Датасет

```powershell
python -m app.dataset
```

Скачивает 24 клипа PolyAI MInDS-14 (ru-RU) в `data/datasets/minds14_ru/`. Дальше — вкладка «Датасет» в кабинете.

## Тесты

```powershell
.\.venv\Scripts\pytest
```

Юнит-тесты не требуют Whisper: качество, диаризация, безопасность путей, поля экспорта, статус датасета.

## Структура репозитория

```
README.md
docs/                 документация
run.py                запуск uvicorn
run.ps1               venv + зависимости + запуск
requirements.txt
app/                  код и статический кабинет
tests/
data/                 локальные данные, не в git
```

## Типичные ситуации

**Сервер не открывается.** Проверьте, что процесс слушает 8080, и что в браузере именно `127.0.0.1`, не другой хост.

**«Запись слишком короткая».** Файл короче 0,4 с или без речи.

**«Не удалось декодировать».** Это не WAV/MP3/OGG или файл повреждён. Сигнатура должна совпадать с расширением.

**Пустая расшифровка.** Шум, музыка, слишком тихая запись. Демо-звонки в журнале показывают оценку без аудио.

**Повторный `run.py` падает.** Используйте текущий процесс на :8080 или `python run.py --replace`.
