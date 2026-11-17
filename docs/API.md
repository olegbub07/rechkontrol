# HTTP API

Базовый URL: `http://127.0.0.1:8080/api`.

Все методы, кроме `POST /auth/login` и `GET /health`, требуют заголовок:

```
Authorization: Bearer <token>
```

Токен выдаётся на 12 часов (`JWT_TTL_HOURS`). Без запросов 15 минут сессия закрывается: ответ 401 «Сессия закрыта из-за простоя», токены учётки гасятся. Пока идут запросы, отметка активности обновляется не чаще раза в минуту. Ответы API не кэшируются (`Cache-Control: no-store`). Документация OpenAPI (`/docs`) включена только при `APP_DEBUG=true`.

## Служебные

| Метод | Путь | Описание |
|---|---|---|
| GET | `/health` | только `{"status":"ok"}`. Очередь и модель — в кабинете после входа |

## Авторизация

| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| POST | `/auth/login` | `{ "username", "password" }` | `{ token, user }` |
| POST | `/auth/logout` | — | отзыв текущего токена |
| GET | `/auth/me` | — | текущий пользователь, поле `must_change_password` |
| POST | `/auth/password` | `{ "old_password", "new_password" }` | новый токен; старые сессии закрыты |

После 8 неудачных попыток входа логин блокируется на 15 минут. На петлевом адресе адрес в этот счётчик не входит. С внешнего адреса дополнительно блокируется адрес. Отключённая учётка отвечает тем же текстом, что и неверный пароль. После 8 неверных старых паролей смена пароля блокируется на 15 минут.

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
| POST | `/operators` | `{ "name", "external_id" }`, только admin (аналитику 403), пишется в журнал `operator.create` |
| GET | `/script` | памятка: шаги разговора и дополнительные фразы. Доступна оператору. Модели и лимитов в ответе нет |
| GET | `/settings` | модель, лимиты, фразы скрипта. Оператору закрыто |
| PUT | `/settings` | `{ "company_phrases", "extra_script_phrases" }`, только администратор |
| POST | `/analyze-text` | оценка текста без аудио |
| GET | `/checklist` | чек-лист качества, значения по умолчанию, лимиты (admin, analyst) |
| PUT | `/checklist` | `{ "criteria": [{ "id", "enabled", "title", "weight", "phrases" }], "category_weights" }`, только admin, 422 с русским текстом при ошибке проверки (пустой вес, буквы, вне диапазона, вес критерия точнее 0,1, доля метрики точнее 0,001). Ответ: `checklist`, `changed`, `normalized`, `message` |
| POST | `/checklist/reset` | вернуть встроенный чек-лист (admin) |
| POST | `/checklist/rescore` | пересчитать оценки готовых звонков по сохранённой расшифровке (admin) |
| GET | `/operators/compare?period=7d\|30d\|90d\|all` | рейтинг, тренд по неделям, частые ошибки; оператор получает только себя, `team` = null. Ещё `unassigned` (готовые звонки без оператора за период), `data_from` / `data_to` (даты первого и последнего звонка), `delta` только когда в обеих неделях не меньше 2 звонков |

## Дашборд и экспорт

| Метод | Путь | Описание |
|---|---|---|
| GET | `/dashboard?period=7d\|30d\|all` | KPI, тренд, тональность, операторы, список `attention` |
| GET | `/export/xlsx` | Excel-файл с теми же колонками, числа и даты записаны как числа и даты (не зависит от языка системы), фильтры как у CSV |
| GET | `/export/csv` | фильтры `operator_id`, `date_from`, `date_to`; `style=ru` (по умолчанию: «;», запятая в дробях) или `style=standard` («,», точка в дробях, файл `..._standard.csv`) |
| POST | `/calls/upload` → `warning` | у каждого созданного звонка поле `warning`, например «оператор OP-999 не найден, звонок загружен без оператора». Файл, который не является аудио, отклоняется с 400 «это не аудиофайл…» |
| GET | `/export/json` | те же фильтры; `full=true` — с расшифровками (только admin); `unmasked=true` вместе с `full` снимает маску и пишется в журнал |

| GET | `/reports/operator/{id}.pdf?date_from=ГГГГ-ММ-ДД&date_to=ГГГГ-ММ-ДД` | PDF-отчёт по оператору (admin, analyst; по умолчанию последние 30 дней, период до 366 дней), пишется в журнал `report.operator` |

Выгрузка отдаёт не больше 5000 строк.

Колонки CSV (UTF-8 BOM, разделитель `;`, дробная часть через запятую - открывается в русском Excel без мастера импорта):

Идентификатор звонка, Дата и время, Идентификатор оператора, Оператор, Файл, Длительность (сек, до десятых), Итоговое качество, Вежливость, Полнота ответа, Соблюдение скрипта, Решение проблемы, Тональность (позитивная / нейтральная / негативная), Среднее время ответа, сек, Процент нарушений, Статус (по-русски).

В JSON ключи прежние, добавлены `tonality_ru` и `status_ru`. Имена файлов разные: `kpi_calls_ГГГГ-ММ-ДД.csv`, `kpi_calls_ГГГГ-ММ-ДД.json`, `calls_full_ГГГГ-ММ-ДД.json`, `calls_full_unmasked_ГГГГ-ММ-ДД.json`.

## Датасет

| Метод | Путь | Описание |
|---|---|---|
| GET | `/dataset` | статус локальной копии MInDS-14 |
| POST | `/dataset/download` | `{ "limit": 24 }` |
| POST | `/dataset/evaluate?limit=12` | WER / CER |
| POST | `/dataset/import?limit=12` | клипы в очередь как обычные звонки |

## Админка

Только `admin`.

| Метод | Путь | Описание |
|---|---|---|
| GET | `/admin/overview` | счётчики людей, звонков, ошибок, аудита и `login_locks` |
| GET | `/admin/posture` | контур, стартовый пароль, состояние базы |
| GET | `/admin/users` | люди; у каждой записи `password_attempts` и `password_locked` |
| POST | `/admin/users` | `{ username, password, role, operator_id? }` |
| PATCH | `/admin/users/{id}` | `{ role?, is_active?, operator_id? }`. `operator_id: null` снимает карточку, кроме роли оператора |
| POST | `/admin/users/{id}/password` | новый пароль. Если это вы сами, в ответе есть свежий `token`, эта сессия жива |
| POST | `/admin/users/{id}/sessions` | закрыть сессии. Для себя в ответе новый `token` |
| POST | `/admin/users/{id}/unlock` | снять блокировку смены пароля. Себе — 400 |
| POST | `/admin/locks/login` | снять блокировки входа, ответ `{ "cleared" }` |
| GET | `/admin/audit` | `page`, `page_size` |

## Пример входа

```powershell
curl http://127.0.0.1:8080/api/auth/login -H "Content-Type: application/json" -d "{\"username\":\"admin\",\"password\":\"<пароль из data/initial_credentials.txt>\"}"
```
