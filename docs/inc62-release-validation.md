# INC-62: причины отказа и проверка последовательных исправлений

## Подтверждённая причина

Fix #35 прошёл Java-тесты, но оба запроса деплоя были отвергнуты runner с HTTP 409; переключения image этими запросами не было (`recovery_required: false`). API сохранял только `Runner HTTP 409`, а карточка ошибочно сообщала о непроверенном завершении, поэтому конкретная причина оставалась невидимой.

На момент исследования `active.json` указывал на проверенный image `sha256:f6d471300ecaa3d912106f22a0a202186b77738ced65dbf0c42f8130bf69835d` (patch job-22), но Docker запускал baseline `sha256:25518146daa4a0f58c0284b6146b28dc1298bd086115d908dd880b33c84584a7`. Кроме того, runner запрещал следующий release с тем же source HEAD, пока предыдущий patch не интегрирован вручную. Новый workspace создавался от чистого HEAD без уже выпущенных обработчиков ошибок. Поэтому даже успешные unit-тесты не давали рабочего последовательного агентного цикла.

## Что изменено

- Workspace наследует текущий проверенный patch; diff/hash/tests показывают накопленные изменения целиком. Исходный checkout не меняется. Конфликтующие правки не перезаписываются; внешний image drift не принимается за доверенный release.
- Уже выпущенный patch без новых правок не считается решением новой задачи. Тесты сбрасываются после наследования и повторяются для точного нового hash.
- Модели доступны HTML/CSS application resources, чтобы исправлять шаблоны и оформлять страницу в стиле проекта. Java/MVC regression tests остаются частью задачи. Секреты, shell хоста, build policy, конфигурации и миграции не открыты модели.
- Известные безопасные причины отказа runner сохраняются; неизвестные HTTP bodies скрываются. В карточке исправления показан последний результат деплоя. Известный 4xx-отказ больше не выдаётся за неизвестное завершение.
- Первый проход live-проверки выявил отдельный дефект: успех тестов после чтения сохранённого diff завершал агента до окончания всей задачи. После tool-правок теперь требуется явное завершение модели и matching trusted tests; исчерпание бюджета не делает частичный passing patch готовым к деплою. Добавлены проверки нескольких файлов и сценария retained patch → чтение → дополнительные правки → итог.
- Второй live-прогон выявил тупик JSON fallback: после двух шагов без изменений repair навсегда терял tool-вызовы и повторял неверные exact replacements. Теперь JSON применяется только для начальной генерации, с возвратом к tools после неудачи; после внесения task-правок read/search/edit остаются доступны независимо от количества шагов исследования.
- Компактация контекста больше не вытесняет все запрошенные исходники большим последним сообщением с Maven log: короткая текущая диагностика закреплена отдельно от истории tools. Регрессионный тест при реальном бюджете 18k проверяет одновременное сохранение compiler error, содержимого `file_read` и целостности tool-call/result.
- UI показывает отдельно бюджет модели и tools и не предлагает `/continue`, если все 60 tool calls израсходованы, даже когда вызовы модели ещё остались.

## Операторское восстановление

До live-продолжения сохранены копии active/deployment journal и сведения о запущенном image в игнорируемом `.runtime/agent-release-backup-20261017T055809Z/`. По явному запросу пользователя на развёртывание восстановлен прежний одобренный image; health, site smoke и Docker health успешны. Baseline-проверка `GET /market?page=doctor-validation-invalid-number` вернула HTTP 500, `text/plain;charset=UTF-8` и прежнее безопасное текстовое сообщение — это ещё не подтверждение новой HTML-страницы.

## Независимое review

Отдельный subagent нашёл три P2: регрессию офлайн-подготовки, зацикливание UI на устаревшем parent workspace и неполный список безопасных 409. Все исправлены и покрыты тестами. Повторное review не обнаружило блокирующих замечаний. Изменение gate завершения агента также отдельно проверено subagent без блокирующих замечаний.

## Автоматические проверки

- Backend: **194 passed**, одно существующее предупреждение deprecation Starlette TestClient.
- Frontend: **17 passed**, production build успешен.
- `git diff --check`: без ошибок.

Unit/API tests используют mocked provider/operations и не выдаются за проверку настоящего Docker/DeepSeek pipeline.
В том числе проверен неудачный последовательный release: rollback восстанавливает предыдущий patched image и active journal, а не исходный baseline. В браузере проверены вход в обновлённую консоль и мобильный рендеринг без JavaScript page errors.

## Live-проверка

Первое продолжение Fix #35 сохранило прежний бюджет (4 использованных вызова до продолжения, 5 после) и выпустило накопленный patch через **Deployment #3: SUCCEEDED**. На live URL получен HTTP 500, `text/html;charset=UTF-8`, безопасное сообщение, без Tomcat error page и stack trace. Но это была прежняя простая HTML-страница; полный многофайловый сценарий нельзя было считать завершённым. Этот результат сохранён отдельно в `.runtime/inc62-validation.json`, история не переписана.

Дополнительная задача **#36** от проверенного активного release остановилась на **30 вызовах**, сохранив workspace и ошибки тестов; READY/деплой не созданы, рабочий image не переключался. Бюджет этой задачи не сбрасывался. Её данные сохранены в `.runtime/inc62-followup-validation.json`. По этим событиям исправлен описанный выше JSON-тупик; остановка не скрыта и не выдана за успешный полный цикл.

Отдельные validation jobs после изменения кода: **#37** (`deepseek-flash`) и **#38** (`deepseek-v4-pro`, модель проверена через `/models`) остановились при попытке превысить лимит **60 tool calls**: использовано соответственно **58/59 tools и по 21 model calls**; очередной batch целиком не помещался в остаток. Ни один не прошедший тесты patch не выпущен. Их счётчики не сбрасывались; данные сохранены в `.runtime/inc62-final-validation.json` и `.runtime/inc62-pro-validation.json`. Это новые validation runs от рабочего image, а не успешное продолжение #36 в прежнем бюджете. Полностью автономное решение данной многофайловой задачи не доказано — это существенное ограничение, а не замалчиваемый успех.

## Операторское завершение страницы

После остановки модельных попыток оператор явно завершил patch в сохранённом `job-38` без новых LLM-вызовов и без сброса счётчиков. До вмешательства сохранён исходный предложенный diff `.runtime/inc62-pro-before-operator.patch`. После trusted runner tests job операторски переведена в READY, добавлено событие `operator_completion`, в summary явно указано ручное завершение. Это не разрешение модели обходить лимит и не автоматическое преобразование BUDGET_EXHAUSTED в READY.

Общий `ErrorHtmlRenderer` отдаёт самостоятельный HTML в розово-лавандовой палитре существующего `static/css/color.css`, с брендом, безопасным сообщением и ссылкой домой. Ни Thymeleaf, ни БД, ни внешние шрифты/скрипты не нужны. Единственный MVC advice — `GlobalExceptionHandler`; servlet `/error` использует тот же HTML и сохраняет 404/500. Legacy `SafeErrorHandler` остаётся совместимым helper, но больше не регистрирует конкурирующий advice. Коды `ResponseStatusException` и `@ResponseStatus` сохраняются.

Новый regression test выявил проблему уже открытого writer с ISO-8859-1. Исправление сбрасывает незавершённый ответ, сохраняет security/cookie/tracing headers, удаляет старые representation headers и пишет UTF-8 bytes; committed response не перезаписывается.

**Настоящие Maven/Java проверки: 17 tests, 0 failures, 0 errors** (`DoctorDemoControllerTest`, `ErrorPageMvcRegressionTest`, `GlobalExceptionHandlerTest`, `SafeErrorControllerTest`, `SafeErrorHandlerTest`). Проверены MVC исключение, servlet 404/500, кириллица, отсутствие деталей, tracing/security headers, старые Content-Length/Content-Encoding и committed response. Дополнительное review нашло два P2: обход общего writer на `/error` и потерю штатных MVC 400/405. Оба исправлены; тесты проверяют missing/invalid parameter 400 и unsupported method 405 с `Allow`. Повторное review подтвердило закрытие замечаний. Это standalone MVC и unit suite без БД, не полная legacy integration suite.

## Итоговый release и браузер

**Deployment #4: SUCCEEDED**, точный patch SHA-256:
`815824b5dfcf8152a5db94b4d19eda3b115ef4353ac3eb93a2a1bb5e3c2499b7`.

Image: `sha256:931b98505c5b64e0b914d629768756105816c0719bde1ca4b3618266a332a2db`.

- Trusted runner проверил exact tests/hash/base/image, собрал image и переключил только `spring`. Health, site smoke, controlled demo и Docker health успешны. Исходные exception/fingerprint в проверяемых логах не обнаружены.
- Реальный `GET /market?page=doctor-validation-invalid-number`: **500**, `text/html;charset=UTF-8`, оформленная страница AurelSpa с точным безопасным сообщением; внутренних исключений и Tomcat error page в ответе нет.
- Реальный `GET /cart/promo`: **405**, `Allow: POST`, та же HTML-страница. Анонимный `/error` остаётся защищённым (**302**), security policy не менялась. Servlet 404 проверен в MockMvc, не выдаётся за анонимный live 404.
- Playwright: результат deployment виден в карточке INC-62, страница ошибки проверена на desktop и 390px mobile, без горизонтального переполнения и JS page errors. Скриншоты: `.runtime/inc62-error-desktop.png`, `inc62-error-mobile.png`, `inc62-dashboard.png`, `inc62-dashboard-mobile.png`.
- Все четыре Compose-сервиса healthy; `/health` на 8050, 3050, 8085 — **200**. Host runner/collector работают с обновлённым кодом.

Проверенный полный application diff сохранён в [patches/inc62-safe-error-page.patch](patches/inc62-safe-error-page.patch); его SHA совпадает с release. Он включён в Git AI Doctor как воспроизводимый артефакт. Исходный checkout и Git remote aurelSpa не менялись. INC-62 не помечен автоматически RESOLVED: исходный POST-сценарий инцидента неизвестен, а показ безопасной страницы не доказывает устранение первичной бизнес-ошибки.
