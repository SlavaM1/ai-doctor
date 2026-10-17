# Проверка инцидентов и локальной почты — 17 октября 2026

Проверено на работающем Docker-стенде, без подстановки событий в API/SQLite.

## Не только MailSendException

- `GET /market?page=doctor-repro` → HTTP 500 → **INC-106**, `java.lang.NumberFormatException`.
- `GET /market?size=0` → HTTP 500 → **INC-107**, `java.lang.IllegalArgumentException` (`Page size must not be less than one!`).
- Свежая сессия, `GET /registration` и `GET /login` → ошибки Thymeleaf/Servlet → **INC-108–111**. Причина в стеке: `Cannot create a session after the response has been committed`. Это отдельный дефект приложения, не ошибка SMTP; он не исправлялся и не исключался.

Collector сам прочитал Docker logs, API сохранил разные инциденты, `/incidents` вернул их одновременно. Нет ограничения «только один тип ошибок».
Playwright также проверил настоящий frontend: в очереди одновременно видны **INC-105–111**, снимок сохранён в `.runtime/runtime-incidents-mail-validated.png`.

Почему прежних записей не было: в долговечном кольце остались события `NumberFormatException` (`doctor-log-check`, 07:46 UTC), `rawPassword cannot be null` и ошибки authentication. Для них есть `log_receipts`, но уже нет связанных `incident_occurrences`/инцидентов. Сохранённый ранее снимок `.runtime/log-visibility-check.png` показывает уже существовавший INC-99 с NumberFormatException. Это соответствует удалению инцидентов, а не пропуску collector. Удаление сохраняет receipts: старые строки при повторном чтении не воскресают, но новое возникновение снова создаёт инцидент. Это поведение отдельно закреплено API regression test.

## Исправление почты

Причина: `localhost:1025` указывал на сам контейнер Spring без SMTP-сервера. `compose.mail.yaml` добавляет Mailpit и направляет Spring на `mailpit:1025`, отключает AUTH/STARTTLS только в локальном стенде, задаёт таймауты. На хост публикуется только UI **http://127.0.0.1:8025**; SMTP не опубликован. Relay не настроен: наружу письма не отправляются.

Применено с сохранением **того же** работающего image Spring: `sha256:8466d179224c68f0a05e9fb06e2b8228ecb3678f81505ad8b235fc31909ccf25`. Не пересобирались Java, API или frontend, не менялись source checkout, MySQL или uploads. Текущий image зафиксирован в игнорируемом `.runtime/mail-current-image.json` только для операторского применения настроек:

```bash
docker compose -f compose.yaml -f .runtime/mail-current-image.json up -d --no-build --wait mailpit spring
```

Этот файл — снимок именно данной проверки, не замена deployment journal и не команда запуска после будущего release. Уже существовавшее расхождение running image и одобренного journal не переобъявлялось одобренным release.

Проверка реальными формами с CSRF, на специально созданном адресе `doctor-smtp-…@example.test`:

1. Создана HTTP-сессия до рендеринга формы (обычный отказ 403 на POST с неверным CSRF), затем получен настоящий CSRF из `/login`. Это позволяет проверить SMTP независимо от отдельного дефекта позднего создания сессии.
2. `POST /registration` → **302 /login**.
3. `POST /user/recovery` → **302 /login**.
4. `/api/v1/messages` Mailpit содержит **2 письма**: «Активация email» и «Восстановление пароля».
5. В Docker logs нового контейнера Spring нет `MailSendException`.
6. После успешной проверки **INC-105 переведён в RESOLVED** через API, история не удалена.

`DEPLOYMENT_COMPOSE_FILE` в `.env` указывает на полный `ai-doctor/compose.yaml`, host runner перезапущен: будущие release/rollback сохраняют настройки SMTP. Allowlist сервиса, immutable image, `--no-deps`, `--no-build` и запрет обновления других сервисов остаются прежними.

## Автоматические проверки

Regression tests проверяют три независимых типа Servlet-ошибок, переходы между batch, повторную доставку, удаление/новое возникновение и сохранение полного compose при release/rollback. Проверки Docker/SMTP выше — реальные, не mocked provider/tests.

`.venv/bin/python -m pytest -q` → **197 passed**. API, Spring и Mailpit health endpoints → **200**, контейнеры healthy; браузер открыл UI Mailpit.
