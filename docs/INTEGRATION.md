# Интеграции: папка входящих, REST API, вебхуки

Всё работает внутри сети банка. Сервис сам никуда не ходит, кроме адресов вебхуков, а они по умолчанию разрешены только внутри сети (частные IP и localhost). Для интеграций не нужен интернет и не нужны новые зависимости.

| Способ | Когда нужен | Включение |
|---|---|---|
| Папка входящих | АТС или скрипт складывает записи в каталог | `INBOX_DIR` |
| REST API | CRM, BI или своя система сама отправляет записи и забирает результат | API-ключ из админки или CLI |
| Вебхуки | внешней системе нужно сразу узнать, что звонок оценён | `WEBHOOK_URLS` и `WEBHOOK_SECRET` |

## 1. Папка входящих

```env
INBOX_DIR=/mnt/pbx-records       # пусто: выключено
INBOX_POLL_SEC=5                 # как часто смотреть в папку
INBOX_SETTLE_SEC=3               # файл берётся, если его размер не менялся столько секунд
INBOX_KEEP_ORIGINALS=true        # false: исходник удаляется после шифрования в хранилище
INBOX_OPERATOR_FIRST=true        # для моно-записей: первым говорит оператор
```

Как это работает:

1. В папку кладутся файлы WAV, MP3, OGG, FLAC или M4A. Скрытые файлы и файлы с окончанием `.part`, `.tmp`, `.filepart`, `.crdownload`, `.partial`, `.lock` пропускаются.
2. Файл берётся только тогда, когда его размер и время изменения не менялись `INBOX_SETTLE_SEC` секунд, поэтому недописанную запись сервис не тронет. Надёжнее всего, если АТС пишет файл как `имя.wav.part` и после записи переименовывает его в `имя.wav`.
3. Сервис переименовывает файл в `processing/` (это атомарно: второй экземпляр сервиса этот файл уже не увидит), шифрует его в хранилище и ставит в очередь. Источник звонка `inbox`.
4. Исходник переезжает в `done/` (или удаляется при `INBOX_KEEP_ORIGINALS=false`). Если файл не аудио или больше `MAX_UPLOAD_MB`, он попадает в `failed/`, а рядом лежит `имя.error.txt` с причиной.
5. Повторно положенный тот же файл (совпадает SHA-256) новый звонок не создаёт, а сразу уходит в `done/`. После аварийного останова файлы, оставшиеся в `processing/`, разбираются при следующем запуске.

Дата и оператор берутся из имени файла так же, как при загрузке в кабинете: `2026-11-07_OP-104_....wav` даёт дату 07.11.2026 и оператора с табельным кодом OP-104.

Внутри `done/` лежат открытые записи. Если папка доступна многим, ставьте `INBOX_KEEP_ORIGINALS=false` или чистите `done/` по расписанию.

Один проход вручную (например, для проверки прав на папку):

```bash
python -m app.cli inbox scan
```

### Сетевая папка, SFTP или SMB

Сервис читает обычный каталог, поэтому сетевое хранилище подключается средствами ОС, а не кодом сервиса.

SMB (Windows-шара АТС) на Linux:

```bash
sudo mkdir -p /mnt/pbx-records
sudo mount -t cifs //pbx01/records /mnt/pbx-records \
  -o credentials=/etc/rechkontrol-smb.cred,uid=1000,gid=1000,file_mode=0660,dir_mode=0770
```

Для постоянного подключения та же строка в `/etc/fstab`. На Windows папку можно указать как UNC-путь: `INBOX_DIR=\\pbx01\records`.

SFTP через sshfs:

```bash
sudo apt install sshfs
sshfs rk@sftp01:/records /mnt/pbx-records -o IdentityFile=/etc/rechkontrol/sftp_key,reconnect,uid=1000,gid=1000
```

В Docker папка пробрасывается томом в `docker-compose.override.yml`:

```yaml
services:
  app:
    environment:
      INBOX_DIR: /inbox
    volumes:
      - /mnt/pbx-records:/inbox
```

У пользователя сервиса (в Docker uid 1000) должны быть права на чтение, запись и переименование в этой папке: туда создаются `processing/`, `done/` и `failed/`.

## 2. REST API для внешних систем

Все методы лежат под `/api/v1/`, доступ по API-ключу в заголовке `X-API-Key`. Сессия кабинета для них не подходит, а ключ не открывает кабинет.

### Ключи

В кабинете: «Админка», раздел «Интеграции», «Создать ключ». Ключ показывается один раз. В базе хранится только его SHA-256 и короткий префикс, по которому ключ видно в списке. Там же ключ отзывается, отзыв действует сразу.

Из командной строки на сервере:

```bash
python -m app.cli apikey create --name CRM --scopes calls:read,calls:write
python -m app.cli apikey list
python -m app.cli apikey revoke 3
```

В Docker: `docker compose exec app python -m app.cli apikey create --name CRM`.

Права (scope):

| Право | Что разрешает |
|---|---|
| `calls:write` | загрузка записей |
| `calls:read` | статус, результат и список звонков |

Создание и отзыв ключей, загрузка через API и каждое чтение результата пишутся в журнал аудита (кто: `api:<название ключа>`). Действует общий лимит запросов с одного адреса (480 в минуту).

### Методы

| Метод | Путь | Право | Что делает |
|---|---|---|---|
| POST | `/api/v1/calls` | `calls:write` | загрузить запись, ответ 202 с номером звонка |
| GET | `/api/v1/calls/{id}` | `calls:read` | статус: queued, processing, done, error |
| GET | `/api/v1/calls/{id}/result` | `calls:read` | оценки, нарушения, проверки, рекомендации, расшифровка |
| GET | `/api/v1/calls` | `calls:read` | список: `status`, `since_id`, `operator_external_id`, `limit` |

Полное описание с типами полей: страница `/docs` и файл `/openapi.json`. Они включаются `API_DOCS=true` (или `APP_DEBUG=true`). Страница `/docs` собрана из файлов самого сервиса, без Swagger UI с внешних CDN, поэтому работает без интернета. `/openapi.json` можно импортировать в Postman или Insomnia.

### Примеры curl

```bash
export RK=http://127.0.0.1:8080
export KEY=rk_1a2b3c4d_...

# загрузка (поля кроме file необязательны)
curl -s -X POST "$RK/api/v1/calls" -H "X-API-Key: $KEY" \
  -F "file=@2026-11-07_OP-104_call.wav" \
  -F "operator_external_id=OP-104" \
  -F "call_at=2026-11-07T10:15:00" \
  -F "operator_first=true"
# {"id":42,"status":"queued","progress":0,"source":"api",...,"duplicate":false}

# статус
curl -s "$RK/api/v1/calls/42" -H "X-API-Key: $KEY"

# результат (когда status=done, иначе 409)
curl -s "$RK/api/v1/calls/42/result" -H "X-API-Key: $KEY"

# всё новое после звонка 40, по порядку
curl -s "$RK/api/v1/calls?since_id=40&status=done&limit=100" -H "X-API-Key: $KEY"
```

Повторная отправка того же файла (совпадает SHA-256) не создаёт второй звонок: ответ 200 с `"duplicate": true` и номером уже существующего звонка. Ошибки: 400 (не аудио), 401 (нет ключа или он отозван), 403 (нет права), 404, 409 (результата ещё нет), 413 (файл больше `MAX_UPLOAD_MB`), 429 (лимит запросов).

Если сервис слушает не только 127.0.0.1 (`ALLOW_REMOTE=true`, см. [SECURITY.md](SECURITY.md)), ставьте перед ним reverse-proxy с TLS, чтобы ключ и записи не шли по сети открыто.

Фрагмент ответа `/result`:

```json
{
  "call": {"id": 42, "status": "done", "source": "api", "filename": "2026-11-07_OP-104_call.wav",
           "operator": {"id": 3, "external_id": "OP-104", "name": "Иванова А."}, "overall": 64.5},
  "scores": {"overall": 64.5, "politeness": 50.0, "completeness": 70.0, "script_compliance": 60.0,
             "resolution": 80.0, "tonality": "neutral", "violation_pct": 10.0, "...": "..."},
  "violations": [{"id": "no_rudeness", "title": "Отсутствие грубости", "category": "politeness", "detail": "..."}],
  "checks": [{"id": "greeting", "passed": true, "...": "..."}],
  "recommendations": ["..."],
  "summary": "...",
  "transcript": [{"start": 0.0, "end": 2.1, "role": "operator", "text": "Здравствуйте, Центр-инвест..."}]
}
```

Расшифровка и цитаты отдаются с маскированием ПДн (карты, телефоны, ФИО), если `PII_MASKING=true`, как и в кабинете.

## 3. Вебхуки

```env
WEBHOOK_URLS=http://crm.bank.local/hooks/rechkontrol,http://10.0.5.20:9000/rk
WEBHOOK_SECRET=длинная_случайная_строка    # без секрета вебхуки не отправляются
WEBHOOK_ONLY_VIOLATIONS=false              # true: только звонки с нарушениями
WEBHOOK_VIOLATION_IDS=                     # сузить, например no_rudeness,no_tutoyage
WEBHOOK_MAX_ATTEMPTS=6
WEBHOOK_BACKOFF_SEC=30                     # пауза перед повтором: 30, 60, 120... но не больше часа
WEBHOOK_TIMEOUT_SEC=10
WEBHOOK_ALLOW_PUBLIC=false                 # true разрешает адреса вне внутренней сети
```

Секрет можно сгенерировать так: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

Событие `call.processed` уходит, когда звонок обработан: и при успехе (`status=done`), и при ошибке (`status=error`). Демо-звонки не отправляются. Запрос: `POST`, тело JSON, заголовки:

| Заголовок | Значение |
|---|---|
| `X-RK-Event` | `call.processed` |
| `X-RK-Delivery` | номер доставки (повтор приходит с тем же номером, по нему отсекаются дубли) |
| `X-RK-Signature` | `t=<unix-время>,v1=<hex HMAC-SHA256(секрет, "<t>.<тело>")>` |

Тело:

```json
{
  "event": "call.processed",
  "call": {"id": 42, "status": "done", "source": "inbox", "filename": "2026-11-07_OP-104_call.wav",
           "call_at": "2026-11-07T10:15:00", "duration_sec": 184.2,
           "operator": {"id": 3, "external_id": "OP-104", "name": "Иванова А."}, "error": null},
  "quality": {"overall": 64.5, "politeness": 50.0, "completeness": 70.0, "script_compliance": 60.0,
              "resolution": 80.0, "tonality": "neutral", "violation_pct": 10.0,
              "violations": [{"id": "no_rudeness", "title": "Отсутствие грубости", "category": "politeness"}]},
  "result_url": "/api/v1/calls/42/result"
}
```

Расшифровки в теле нет, только оценки. Текст забирается по `result_url` с API-ключом.

Доставка считается успешной при ответе 2xx. Иначе (код ошибки, таймаут, сеть недоступна) будет повтор с паузой `WEBHOOK_BACKOFF_SEC * 2^n`, но не больше часа. После `WEBHOOK_MAX_ATTEMPTS` попыток доставка помечается `failed`. Переадресации (3xx) не выполняются, системный прокси не используется. Журнал доставки виден в «Админке», раздел «Интеграции»: время, адрес, статус, число попыток, код ответа. Там же неудачную доставку можно отправить ещё раз. Тела в журнале хранятся зашифрованными, записи старше 30 дней удаляются. Отправить накопленное вручную: `python -m app.cli webhook send`.

### Проверка подписи (Python)

```python
import hashlib
import hmac
import time

SECRET = b"длинная_случайная_строка"


def verify(body: bytes, header: str, tolerance_sec: int = 300) -> bool:
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = int(parts["t"])
    except (KeyError, ValueError):
        return False
    if abs(time.time() - ts) > tolerance_sec:  # защита от повтора старого запроса
        return False
    expected = hmac.new(SECRET, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts.get("v1", ""))


# Flask:
# @app.post("/hooks/rechkontrol")
# def hook():
#     if not verify(request.get_data(), request.headers.get("X-RK-Signature", "")):
#         abort(401)
#     event = request.get_json()
#     ...
#     return "", 204
```

Подпись считается от сырого тела запроса. Если сначала разобрать JSON и собрать его заново, байты изменятся и подпись не сойдётся.
