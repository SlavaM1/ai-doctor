# AI Doctor

Локальный инженерный пульт для **реальных runtime-ошибок aurelSpa**: Docker logs → правила → SQLite incident → DeepSeek → изолированный Git fix → diff + проверки → явное подтверждение → release и rollback.

Никаких моделей для фильтрации обычных логов. Никакого Docker socket внутри API. Никаких автоматических push или deploy со стороны LLM.

## Запуск в WSL

Требуются Docker Engine/Compose v2+, Git, Python 3.12 и доступ пользователя к Docker. Репозитории — рядом:

```text
~/ai/ai-doctor                # dev/day35
~/ai/aurelSpa                # существующий checkout: не изменяется
~/ai/aurelSpa-day35          # integration worktree, dev/day35-ai-doctor
~/ai/ai-doctor-workspaces    # отдельные detached worktrees исправлений
```

Создайте integration worktree от подготовленной ветки (не поверх существующих изменений):

```bash
git -C ../aurelSpa fetch origin
git -C ../aurelSpa worktree add ../aurelSpa-day35 origin/dev/day35-ai-doctor
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock
cp .env.example .env
chmod 600 .env
```

Если системный Python не имеет `venv`, можно использовать `uv venv --python 3.12` и `uv pip install -r requirements.txt`.

В `.env` задайте `DEEPSEEK_API_KEY`, случайный `RUNNER_TOKEN`, абсолютные пути и параметры. Пустые model IDs автоматически выбираются **из ответа DeepSeek `/models`**, а не выдумываются. Для target worktree настройте его собственный `.env` по `docs/day35-doctor-demo.md`; используйте отдельные случайные MySQL-пароли. `DEMO_TOKEN` AI Doctor должен совпасть с `DOCTOR_DEMO_TOKEN` target worktree. Ключ API никогда не передаётся frontend или в build context.

```bash
bash scripts/start.sh
```

Скрипт запускает два доверенных host-процесса и `docker compose up -d --build`. Compose включает существующий integration compose: **mysql + spring + api + frontend**, без второй БД и без изменения day33-сервисов.

- Dashboard: **http://127.0.0.1:3050**
- API/OpenAPI: http://127.0.0.1:8050/docs
- aurelSpa: **http://127.0.0.1:8085**
- Target health: http://127.0.0.1:8085/health

Для последующих запусков также используйте `bash scripts/start.sh`: он сохраняет уже одобренный image из deployment journal и не заменяет его исходным demo release. `docker compose up -d --build api frontend` пересобирает только AI Doctor. Прямой общий `docker compose up -d --build` предназначен для первоначального baseline и требует `SOURCE_REVISION` текущего clean Git HEAD; после release он может заменить приложение baseline-образом — не используйте его для обычного перезапуска. Host runner и collector должны работать; их логи/PID находятся в игнорируемой `.runtime/`. Не используйте `down -v`: SQLite, MySQL и uploads сохраняются в named volumes.

## Мониторинг

Collector выбирает контейнеры **только по двум Docker Compose labels**: разрешённый project + service. Он читает stdout/stderr с Docker timestamps; следит за сменой container ID, читает завершившиеся контейнеры и переподключается. Checkpoint сохраняется на диск только после подтверждения API. Двухсекундное перекрытие и durable pending batch позволяют повторить доставку; SQLite receipts подавляют повторно полученные строки. Multiline buffer также хранится в SQLite.

ERROR/FATAL и необработанные Java exceptions собираются вместе со stack trace, `Caused by`, logger, application frames, файлом/строкой и версией image. INFO/DEBUG/TRACE не вызывают AI. Слово `retry` **не исключает ERROR**. WARN connection failures, exhausted pool, deadlocks, retry exhaustion, timeout и health/external failures проходят только после порога **3 события за 60 секунд**; пороги меняются в Settings.

Fingerprint использует service/version, тип исключения, нормализованные message/root cause и значимые application frames. UUID, timestamps и динамические числа нормализуются. Повторы увеличивают `occurrences`, но не вызывают новую диагностику. Новый контекст/версия может создать новый incident. Повтор resolved fingerprint переоткрывает incident; недавний диагноз защищён persistent cooldown (по умолчанию 900 секунд), после которого ставится одна повторная диагностика. Ручной Re-analyze может явно обойти cooldown.

Сырые логи бесконечно не хранятся: trace ≤32 KiB, контекст — несколько строк, occurrence retention по умолчанию 30 дней, receipts — 3 дня. Суммарные occurrences и история diagnoses/fixes/deployments сохраняются. SSE служит уведомлением; при открытии UI всё загружается из SQLite.

Во вкладке **«Данные и исходный код → Лог сессии»** показано последнее повторение ошибки и до **5 соседних событий до / 5 после** (не строк stack trace). Последующие события дополняют окно при поступлении, в том числе из другого batch и после перезапуска API. Если заголовок содержит `traceId=<id>` (также распознаётся поле JSON `"traceId":"<id>"`), выбираются только события того же traceId **и контейнера**; без traceId — соседние события контейнера. Допустимый ID: 1–128 символов `[A-Za-z0-9._:-]`. Заголовки Java-событий должны начинаться с даты/времени, stack trace продолжается отдельными строками; целиком JSON-события не заменяют текущий Java parser. Секреты редактируются до записи. Временное кольцо — максимум 5000 событий по 8 KiB, до 3 дней; долговечное окно — максимум 11 событий на инцидент. Исторический контекст до обновления не восстанавливается.

Окно дополняется последующими событиями в течение 3 дней после ошибки, даже если сама ошибка уже вытеснена из временного кольца. После этого сохраняется собранный ограниченный контекст; UI сообщает о завершении периода сбора.

Статус инцидента можно менять вручную. **«Не требует решения»** отключает автоанализ и отменяет ожидающие работы; повтор того же fingerprint обновляет счётчик/контекст, но не переоткрывает инцидент. Для нового анализа/исправления сначала измените этот статус. Удаление требует отдельного подтверждения и удаляет связанные записи, но не файлы/ветки на хосте. Активная диагностика, чат, fix/Git/deploy и непроверенное состояние deploy блокируют удаление; ожидающие работы удаляются вместе с инцидентом. Новый fingerprint (включая новую версию приложения) — отдельный инцидент.

## Diagnosis и бюджет

Автоматическая диагностика — обычно **один** реальный DeepSeek-вызов Flash. Контекст ищется по stack frame, точному filename/class/line и ограниченным фрагментам, а не по всему репозиторию. API сохраняет наблюдения отдельно от гипотез, рекомендации, confidence, реальные usage и latency. Если провайдер не вернул usage, числа помечаются approximate.

Flash и Pro обнаруживаются через API, модель выбирается явно в UI. По умолчанию один одновременный LLM-вызов, ограниченные context/output, ограниченная очередь диагностики. Ошибка API сохраняется как ошибка — никакого mock fallback. Fix Agent ограничен `MAX_AGENT_ITERATIONS` (по умолчанию 5), количеством tools и размером результатов. Исследование занимает максимум два вызова; затем отдельный non-tool JSON-вызов генерирует реальные replacements/new Java files, которые проверяет runner. Это не заранее записанный patch. Исчерпание бюджета останавливает job и сохраняет промежуточный diff. **Continue remaining budget** — только явное действие, только для последней job и без сброса уже потраченных вызовов. Исторические superseded jobs продолжать нельзя.

## Fix и deploy

1. Откройте incident и диагноз; при необходимости задайте вопрос в мини-чате.
2. **Prepare fix** создаёт detached Git worktree от конкретного SHA.
3. Модель исследует код через узкие file/search tools, меняет только разрешённые Java-файлы и запускает allowlisted проверки в ограниченном Docker-контейнере.
4. UI показывает реальные tool events, changed files, Git diff, test output, модель и токены.
5. Только **Approve & Deploy** с подтверждением **конкретного hash diff** разрешает release. Даже просьба в чате «исправь и перезапусти» не является таким подтверждением.
6. Trusted runner повторно валидирует workspace, test result, base version и hash, собирает image, заменяет только `spring`, проверяет health/site и controlled regression probe.
7. При неудаче возвращает прежний image и проверяет его здоровье. MySQL/volumes не удаляются, обратные SQL-миграции не выполняются. Изменения build policy/миграций не допускаются через fix tools.

### Ручное сохранение в GitHub

В проверенном исправлении раскройте **«Сохранить исправление в Git»**. Создание ветки, коммит и push — три независимых подтверждаемых действия, не LLM tools. Ветка начинается строго от **текущего HEAD** настроенного `SOURCE_REPO`, который должен совпадать с base исправления и быть чистым. Checkout пользователя не переключается и не меняется. Коммит содержит только точный проверенный diff; push отправляет только этот SHA в именованную ветку `origin`, без force и без запуска hooks. После коммита отдельно доступен прежний deploy flow.

Новые переменные окружения для Git не нужны. **Host runner должен работать под локальным пользователем с настроенными `user.name`/`user.email` и доступом к origin через его существующий credential helper либо SSH agent.** Identity/credentials настраивайте локально, не в API/frontend или репозитории; токены не вводите в URL remote, сообщение коммита, чат или `.env`, доступный sandbox. Для push разрешён только GitHub remote вида `https://github.com/owner/repo.git`, `git@github.com:owner/repo.git` либо `ssh://git@github.com/owner/repo.git`, без встроенного пароля/токена. Runner не возвращает вывод Git push/commit и секреты в UI; ошибки Git намеренно обобщены. Новые/изменённые файлы с распознанными секретами/PII блокируются; это не заменяет пользовательскую проверку diff и истории Git.

При обновлении API автоматически создаст две новые SQLite-таблицы `session_log_events` и `incident_log_windows` (`create_all`, без удаления данных и ручной SQL-миграции). Для применения нужны пересборка API/frontend и согласованный перезапуск host runner при отсутствии активных работ; collector не требует новой конфигурации.

Незавершённый deploy со статусом `INTERRUPTED` также блокирует удаление/смену статуса: рестарт API не доказывает остановку host runner. Ручной Git до отправки runner сохраняет допуск `RUNNING` в SQLite `State` (`manual-git:<incident_id>`); только полученный успешный ответ завершает допуск. Timeout, disconnect/отмена и рестарт API оставляют `UNKNOWN` и блокируют повторное Git-действие, удаление и смену статуса этого инцидента. Эти состояния не снимаются автоматически: HTTP-действия сброса нет, нужна доверенная операторская сверка результата и отсутствия продолжающейся/ожидающей host-работы. Обычная успешная последовательность branch → commit → push блокировку не оставляет. Новых таблиц или env для этой защиты не требуется.

Подробнее о реальной демонстрации и её границах: [docs/demo.md](docs/demo.md). Результаты конкретного запуска: [summary.md](summary.md).

Если host runner аварийно остановился во время переключения image, сначала проверьте его durable journal (раздел deployment в `/status`). Не запускайте новый deploy вслепую. Явное операторское восстановление прежнего release: `.venv/bin/python -c "from doctor.runner import recover_deployment; print(recover_deployment(confirmed=True)['status'])"`. Это не LLM tool и не HTTP endpoint. После обновления Python-кода host-процессов используйте `.venv/bin/python scripts/restart-host.py` только когда нет активного deploy/test job.

## Граница доверия

API/frontend опубликованы только на loopback. Mutations требуют специального заголовка и проверяют browser Origin; не публикуйте эти порты через reverse proxy без дополнительной аутентификации. Host runner слушает **Unix socket**, все операции требуют отдельный секрет. API получает только read-only socket directory: ни Docker socket, ни host filesystem, ни workspace mount в API нет.

LLM не имеет shell, deployment tool, доступа к `.env`, приватным ключам, Git push, policy или посторонним контейнерам. Файловые операции проверяют traversal/symlinks и рабочую директорию. Миграции, Dockerfile, pom.xml и pipeline policy не редактируются моделью. Секреты/authorization/cookies/пароли/ненужные персональные данные редактируются перед записью и отправкой в LLM. Логи, код и tool output рассматриваются как недоверенные данные, а не инструкции.

**Важно:** Docker group даёт host runner привилегии Docker host. Это осознанно доверенный локальный компонент, не sandbox для чужих репозиториев. Generated Java выполняется в тестовом контейнере с ограничениями, но проект предназначен для доверенного локального использования, не multi-tenant сервиса.

## Проверки

```bash
.venv/bin/python -m pytest -q
docker compose ps
curl --fail http://127.0.0.1:8050/health
curl --fail http://127.0.0.1:8085/health
```

Unit/integration tests используют mocked provider, временные Git repositories и SQLite. Live API вызывается отдельно и ограниченно. Не выдавайте passing mocked test за подтверждение Docker/LLM pipeline.
