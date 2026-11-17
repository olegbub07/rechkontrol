# HTTP API

Базовый URL: `http://127.0.0.1:8080/api`.

Все методы, кроме `POST /auth/login` и `GET /health`, требуют заголовок:

```
Authorization: Bearer <token>
```

Токен выдаётся на 12 часов (`JWT_TTL_HOURS`). Ответы API не кэшируются (`Cache-Control: no-store`). Документация OpenAPI (`/docs`) включена только при `APP_DEBUG=true`.

## Служебные

| Метод | Путь | Описание |
|---|---|---|
| GET | `/health` | статус, офлайн-модель, длина очереди |

## Авторизация

| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| POST | `/auth/login` | `{ "username", "password" }` | `{ token, user }` |
| POST | `/auth/logout` | — | отзыв текущего токена |
| GET | `/auth/me` | — | текущий пользователь, поле `must_change_password` |

После 8 неудачных попыток с одного IP вход блокируется на 15 минут.

## Звонки и очередь

| Метод | Путь | Описание |
|---|---|---|
| GET | `/calls` | список: `status`, `operator_id`, `q` (файл и текст, до 120 символов), `flagged`, `page`, `page_size` |
| GET | `/calls/{id}` | досье: сегменты, чек-лист, рекомендации, заметка |
| PATCH | `/calls/{id}` | `{ "review_note", "flagged" }` |
| POST | `/calls/upload` | `multipart`: `files`, `operator_id`, `operator_name`, `call_at`, `operator_first` |
| GET | `/calls/{id}/audio` | расшифрованный поток исходного файла, только звуковой MIME |
| POST | `/calls/{id}/reprocess` | снова в очередь |
| POST | `/calls/retry-failed` | все ошибки с аудио снова в очередь, ответ `{ "retried" }` |
| DELETE | `/calls/{id}` | удаление записи и файла |
| GET | `/queue` | queued / processing / failed |

## Операторы и скрипт

| Метод | Путь | Описание |
|---|---|---|
| GET | `/operators` | список со средним качеством |
| POST | `/operators` | `{ "name", "external_id" }` |
| GET | `/settings` | модель, лимиты, фразы скрипта |
| PUT | `/settings` | `{ "company_phrases", "extra_script_phrases" }` |
| POST | `/analyze-text` | оценка текста без аудио |

## Дашборд и экспорт

| Метод | Путь | Описание |
|---|---|---|
| GET | `/dashboard?period=7d\|30d\|all` | KPI, тренд, тональность, операторы, список `attention` |
| GET | `/export/csv` | фильтры `operator_id`, `date_from`, `date_to` |
| GET | `/export/json` | те же фильтры; `full=true` — с расшифровками |

Выгрузка отдаёт не больше 5000 строк.

Колонки CSV (UTF-8 BOM):

Идентификатор звонка, Дата и время, Идентификатор оператора, Оператор, Файл, Длительность, Итоговое качество, Вежливость, Полнота ответа, Соблюдение скрипта, Решение проблемы, Тональность, Среднее время ответа, Процент нарушений, Статус.

## Датасет

| Метод | Путь | Описание |
|---|---|---|
| GET | `/dataset` | статус локальной копии MInDS-14 |
| POST | `/dataset/download` | `{ "limit": 24 }` |
| POST | `/dataset/evaluate?limit=12` | WER / CER |
| POST | `/dataset/import?limit=12` | клипы в очередь как обычные звонки |

## Пример входа

```powershell
curl http://127.0.0.1:8080/api/auth/login -H "Content-Type: application/json" -d "{\"username\":\"admin\",\"password\":\"CentrAdmin2026!\"}"
```
