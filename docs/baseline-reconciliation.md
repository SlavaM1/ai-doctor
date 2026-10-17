# Согласование runtime и исходников для исправлений с кнопки

## Причина HTTP 409

Задача #71 остановилась до LLM: `active.json` описывал image `931b9850…` из job-38, а Spring работал на `8466d179…`. Более того, запущенный JAR содержал изменения, которых не было в `SOURCE_REPO`:

- `WebSecurityConfig`: разрешение ERROR-dispatch без редиректа на вход;
- `TomcatErrorReportConfig`: стандартный ответ Tomcat без подробностей исключения;
- `application.properties`: отключён Spring `ErrorMvcAutoConfiguration`.

Просто стереть журнал или принять image по одному OCI revision было бы неверно: модель готовила бы исправления от других исходников и могла бы потерять уже работающее поведение.

## Безопасное восстановление

Создан отдельный source worktree `/home/slava/ai/aurelSpa-runtime-baseline`, локальная ветка `operator/runtime-baseline-20261017`. Commit `367c7e14fdc87eb0b57e4cff181f40ad22bd6415` сохраняет **существовавшие** runtime-изменения и тестовую настройку ERROR-dispatch. Исходный `/home/slava/ai/aurelSpa-day35` и изменённая старая job-38 не переключаются и не перезаписываются; Git push не выполняется. Ошибки пагинации и гостевого checkout этим commit не исправляются.

`scripts/reconcile_baseline.py` — только доверенная host-операция, не HTTP endpoint и не tool агента. Она требует точный image ID и явное `--confirmed`:

1. Под runner lock проверяет чистоту исходников, допустимый terminal journal, отсутствие активных/непроверенных API-работ, здоровый контейнер и точный ожидаемый image.
2. Собирает clean source с тестами из Dockerfile, без `.env` и host-repository mount.
3. Сравнивает SHA-256 **каждой записи JAR**, включая application classes, ресурсы, manifest и dependencies; не учитывает только ZIP metadata. При любом отличии оставляет trusted state неизменным.
4. Проверяет health, сайт и demo probe; повторно сверяет source HEAD, image и container ID после сборки.
5. Сохраняет прежние `active.json` и `deployment.json` целиком в durable `baseline-adoption-*.json` внутри `.runner-state`.
6. Только с отдельным `--replace-equivalent-image` переключает Spring на bytecode-эквивалентный image с правильным source revision. Проходит health/site/demo; при неудаче восстанавливает прежний image и состояние. MySQL, Mailpit, frontend, API и volumes операция не пересоздаёт.
7. Помечает согласованный baseline без AI patch; последующие исправления снова используют обычные проверки image, HEAD, hash, tests, approval и rollback.

В `.env` `SOURCE_REPO`/`SOURCE_REVISION` и Compose build context должны указывать на одну согласованную базу. Host runner перечитывает их после контролируемого перезапуска. Старый job-71 остаётся сохранённым, но относится к прежнему HEAD: нужен свежий инцидент и новая задача, а не перенос старого workspace на другую базу. Runner теперь отказывает такому workspace до расходования модельного бюджета.

## Ограничения проверки

Успех reconciliation не означает исправление runtime-дефектов. Их нужно воспроизвести заново на текущей версии и исправлять отдельными задачами через UI. Проверка допуска `/workspace` без HTTP 409 подтверждает только снятие инфраструктурной блокировки, а не успешный LLM patch/test/deploy.

Тесты покрывают отказ без подтверждения, неверный image/revision, dirty source, незавершённый deploy, BUSY API, несовпадающий JAR, изменение исходников во время сборки, архивирование журналов, эквивалентную смену image с rollback и ранний отказ устаревшему workspace.

## Результат применения

- Все **637 записей JAR** исходного runtime и сборки commit `367c7e14…` совпали; первая попытка от старого HEAD корректно отклонена до изменения trusted state.
- Новый baseline image: `sha256:aadd3b761f287c9705ab245147e4214beaed311c27da894461a6161d5c2141ae`.
- Архив: `.runner-state/baseline-adoption-6bbaa449f2e145f3bdff6296a0ed23d0.json` содержит оба прежних журнала целиком.
- Health/site/demo прошли, Docker healthy. Host runner/collector перезапущены; runner revision и Compose build context указывают на тот же новый source worktree.
- Повтор сценариев создал **INC-126 / NumberFormatException** и **INC-127 / NullPointerException** на согласованной версии.
- Playwright открыл обе карточки и проверил доступную кнопку «Подготовить исправление» после ввода цели. Запрос исправления при этой проверке не отправлялся; снимок `.runtime/baseline-buttons-ready.png`.
- `.venv/bin/python -m pytest -q`: **214 passed**.
- Через настоящий Unix-socket API runner создана временная рабочая область от текущего HEAD: `/workspace` → **HTTP 200**, без stale patch. Через `/tool/run_allowed_tests` выполнены **5 Java-тестов, BUILD SUCCESS**. Временный probe workspace удалён только после проверки отсутствия изменений; реальные рабочие области сохранены. Evidence: `.runtime/baseline-button-readiness.json`. Ни LLM fix, ни деплой исправления двух учебных ошибок при этой проверке не запускались.
