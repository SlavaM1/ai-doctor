# Day 35 — AI Doctor

**Результат: готово.** Проверен реальный pipeline на изолированном Docker aurelSpa, а не на mocked logs. Последнее подтверждение: 16 октября 2026.

## Что реализовано

- FastAPI/SQLAlchemy/SQLite API, React/TypeScript/Vite dashboard, persistent Docker volume, SSE.
- Trusted host collector и runner через authenticated Unix socket. Ни API, ни LLM не получают Docker socket или shell.
- Java multiline parser, root cause/application frames, ERROR/FATAL, необработанные exceptions, crash/OOM/health и пороговые WARN. WARN: connection/pool/deadlock/retry exhaustion/timeout/external/health; обычно 3 за 60 секунд. Access-log HTTP 5xx также пороговые.
- Stable fingerprint, нормализация UUID/чисел/timestamps, receipts/checkpoints/replay dedup, persistent multiline buffers, retention. Повтор не вызывает новый AI-запрос. Reopen имеет persistent cooldown; manual Re-analyze явный.
- Verified DeepSeek model discovery, Flash diagnosis, Pro Fix Agent, compact source snippets, bounded calls/context/output, реальные usage/latency.
- Detached Git worktrees; узкие source tools, модельный JSON patch, Java 17 проверки в ограниченном контейнере, Git diff и SHA approval.
- Image build, замена только spring, container/DB/HTTP health, smoke/regression probe, проверка логов, image rollback и durable journal.

## Реальные результаты

| Проверка | Результат |
|---|---|
| aurelSpa / MySQL | Java 17, Spring Boot 2.6.3, Flyway V1–V5; оба healthy |
| Monitoring | Incident **#2**, настоящий `NullPointerException`, `DemoNameNormalizer.java:10`, 10 occurrences |
| Flash | Первая автоматическая диагностика: 1 вызов, **1270 input + 506 output = 1776 tokens** |
| Source-enriched manual analysis | 1 явный дополнительный вызов, **2039 tokens**; подтверждены строки 10 и 46, confidence 0.93 |
| Dedup | Повторы увеличили occurrences без повторных автоматических diagnosis calls |
| Fix #3 | `deepseek-v4-pro`, **4 вызова**, **9667 input + 704 output = 10371 tokens**; бюджет не сброшен при explicit continuation |
| Изменение | `name == null ? "" : name.trim()` + три новых JUnit regression tests |
| Проверки Java | `DoctorDemoControllerTest` + `DemoNameNormalizerTest` passed (5 + 3 tests) |
| Approval | Пользователь явно подтвердил конкретный diff; затем проверены SHA и UI Approve & Deploy |
| Deployment #1 | **SUCCEEDED**, исходный запрос 500 → 200/empty name; container, DB health и сайт 200; исходный exception/fingerprint не найден |
| Incident state | **RESOLVED**, не просто отсутствие строки в логах: есть воспроизведение до и после |
| Rollback | **ROLLED_BACK** на отдельном реальном Docker стенде 8086; точный предыдущий image восстановлен, Docker/HTTP healthy |
| Финальный запуск | Approved image сохранён через `scripts/start.sh`; ещё 3 исходных запроса вернули 200, occurrences не увеличились |
| UI | Реальный браузер: SSE refresh без navigation, сохранённая история/успешный deploy, 0 JS errors, mobile без horizontal overflow |

**Approved patch SHA-256:** `329b09c6f3d4f3aa99fe2e5dea76756833b8a8593cef69e537a43868020c99e7`.

**Рабочий image:** `sha256:1511edd4a82e3af476697ee2a7fb870203b0c3736e0ff30ff5ea225eb4249936`.

**Previous/rollback image:** `sha256:f6d34822fd60029bbcefdcacf3bf06bd1279a177a6a77ec99c2d9c45beb469be`.

В rollback-тесте намеренно инъецировалось только построение неработающего image. Activation, health verification и возврат image выполнял настоящий production runner; Docker operations не mocked. Отдельная MySQL/volumes, без удаления данных и без воздействия на основной 8085.

## Тесты и review

- Backend: **93 passed**, 0 failed; одна предупреждающая deprecation зависимости Starlette TestClient.
- Frontend: **6 passed**, TypeScript/Vite build passed.
- Mocked provider/deployment tests отдельно от перечисленных live Docker/DeepSeek checks.
- Независимый review обнаружил **3 High**: tracked credential в sandbox, непроверенный running baseline, continuation superseded job за пределами attribution window. Все исправлены, покрыты regression tests и повторно проверены. Подробно: `docs/code-review.md`.
- Дополнительно исправлены реальные integration bugs: Java version/legacy test selection, manifest >24 KiB, packing source context, лишнее исследование Pro, supervision host processes, idle multiline recovery, безопасный relaunch approved image.

**Без скрытого успеха:** Fix #1 остановлен без правок по context budget (2 вызова); Fix #2 — по iteration budget (5 вызовов). Они сохранены как неуспешные. В последнем job третий вызов провайдера вернул уже недоступный read tool — guard не исполнил его. После исправления протокола через UI явно продолжена та же job с одним оставшимся вызовом: JSON writing завершил fix, всего **4**, без сброса бюджета. В финальном протоколе свежая job исследует код максимум два шага, затем пишет patch отдельным non-tool completion.

**Все live-вызовы разработки суммарно:** **14 calls / 34624 tokens**, включая неудачные попытки и ручной source-enriched анализ; 3 Flash + 11 Pro. Все числа получены от провайдера, не approximate. Это не стоимость одного штатного diagnosis.

## Сервисы и проверка

- `day35-aurelspa-api-1`, `day35-aurelspa-frontend-1`, `day35-aurelspa-spring-1`, `day35-aurelspa-mysql-1`: healthy.
- Host runner/collector: работают отдельно, checkpoints/journal сохраняются.
- Dashboard: **http://127.0.0.1:3050**; API: **http://127.0.0.1:8050**; aurelSpa: **http://127.0.0.1:8085**.
- Запуск: `bash scripts/start.sh`; инструкция: `README.md`, пошаговая демонстрация: **`docs/demo.md`**.

## Git

- `ai-doctor`: **`origin/dev/day35`**. Основной implementation commit `fac4fd7`; финальная проверенная версия кода, launcher, cooldown и provenance guard — **`263aa2d7bff21386ead1d838ca3c0fdfa056892b`**. Следующий documentation-only commit добавляет этот summary. Актуальный remote tip проверяется `git ls-remote origin refs/heads/dev/day35`.
- `aurelSpa`: **`origin/dev/day35-ai-doctor`**, **`4b1663348e339f2a4158614c2592a3454ebff8c6`**. Включены fixture, удаление текущего embedded credential, provenance и точный approved AI fix. Merge в main не выполнялся.
- Исходный checkout aurelSpa остался чистым на `dev/day33-ai`; старые сервисы и пользовательские volumes не изменены.
- `.env`, API keys, runtime DB/logs, screenshots и workspaces не включены в commits.

## Ограничения

- Локальный учебный сервис, не multi-tenant/production security platform. Порты только loopback; для публикации нужна дополнительная аутентификация/TLS.
- Docker group у host runner привилегирован. Tests используют исходящую сеть для Maven dependencies; host secrets и Git metadata не монтируются. Snapshot credential detection — дополнительный guard, не универсальный secret scanner.
- Автоправки только Java source/tests; build policy, конфигурации и миграции запрещены. Deploy требует проверенного baseline; аварийное восстановление без известного рабочего release — операторское.
- Host processes после перезапуска WSL нужно поднять `scripts/start.sh`; незавершённый deployment journal требует явной recovery, не слепого retry.
- Старые versioned incidents не закрываются автоматически без основания; incident #1 относится к первоначальному снятому image. Текущий исправленный incident #2 resolved.
- Исторический mail credential исходного aurelSpa требуется **отозвать/заменить, если ещё действителен**. Текущий файл очищен, но старые Git commits не переписаны и credential не отозван автоматически.
