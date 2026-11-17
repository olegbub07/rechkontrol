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
| POST | `/auth/password` | `{ "old_password", "new_password" }` | новый токен; старые сессии закрыты |

После 8 неудачных попыток входа с одного IP вход блокируется на 15 минут. Отключённая учётка отвечает тем же текстом, что и неверный пароль. После 8 неверных старых паролей смена пароля блокируется на 15 минут.

Пока `must_change_password` истинно, открыты только `/auth/me`, `/auth/password` и `/auth/logout`. Остальное — 403 «Смените стартовый пароль».

Чужой `Origin` или `Sec-Fetch-Site: cross-site` на `POST`/`PUT`/`PATCH`/`DELETE` даёт 403. Тело обычного запроса больше 1 МБ — 413, даже если заголовок `Content-Length` не прислан. Ошибка полей — 422 «Проверьте поля запроса», без эха пароля.

Роли: `operator` видит и слушает только звонки своей карточки (`operator_id` в `/auth/me`). Чужой звонок — 404. `analyst` читает все звонки. `admin` плюс удаление, полный экспорт, настройки скрипта, датасет и `/admin`.

## Звонки и очередь

| Метод | Путь | Описание |
|---|---|---|
| GET | `/calls` | список: `status`, `operator_id`, `q` (файл и текст, до 120 символов), `flagged`, `page`, `page_size` |
| GET | `/calls/{id}` | досье. `?unmasked=true` снимает маску ПДн, только администратор, событие `call.unmask` |
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
| GET | `/checklist` | чек-лист качества, значения по умолчанию, лимиты (admin, analyst) |
| PUT | `/checklist` | `{ "criteria": [{ "id", "enabled", "title", "weight", "phrases" }], "category_weights" }`, только admin, 422 при ошибке проверки |
| POST | `/checklist/reset` | вернуть встроенный чек-лист (admin) |
| POST | `/checklist/rescore` | пересчитать оценки готовых звонков по сохранённой расшифровке (admin) |
| GET | `/operators/compare?period=7d\|30d\|90d\|all` | рейтинг, тренд по неделям, частые ошибки; оператор получает только себя, `team` = null |

## Дашборд и экспорт

| Метод | Путь | Описание |
|---|---|---|
| GET | `/dashboard?period=7d\|30d\|all` | KPI, тренд, тональность, операторы, список `attention` |
| GET | `/export/csv` | фильтры `operator_id`, `date_from`, `date_to` |
| GET | `/export/json` | те же фильтры; `full=true` — с расшифровками (только admin); `unmasked=true` вместе с `full` снимает маску и пишется в журнал |

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
curl http://127.0.0.1:8080/api/auth/login -H "Content-Type: application/json" -d "{\"username\":\"admin\",\"password\":\"<пароль из data/initial_credentials.txt>\"}"
```
