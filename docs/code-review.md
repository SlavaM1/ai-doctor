# Независимый Code Review

Review выполнен отдельным агентом, без Docker/LLM вызовов и без изменения runtime DB.

## Подтверждённые High и исправления

1. **Tracked credential в sandbox.** В исходном `aurelSpa/application.properties` находился старый mail credential. Generated tests могли прочитать ресурс до approval и использовать исходящую сеть. Credential удалён из текущей integration ветки: конфигурация использует environment placeholders. Snapshot ограничен `pom.xml`, Dockerfile/dockerignore и `src/**`; обнаруженные plaintext password/API-key/private-key значения блокируют sandbox. `.env`, Git metadata, host credentials и Compose-конфигурация в него не попадают. Добавлен regression test.
2. **Непривязанный running baseline.** Первый deploy мог одобрить маленький diff поверх другого source HEAD, чем текущий image. Теперь baseline имеет OCI source revision; runner проверяет SHA, immutable image ID на момент preparation и incident version. Непроверенный/сменившийся release отвергается. Следующий release требует интеграции предыдущего patch в clean source HEAD. Добавлен regression test.

3. **Budget attribution при продолжении старой job.** Финальный review выявил, что continuation старой job после создания следующей мог обходить time-window подсчёта вызовов. Продолжение superseded jobs запрещено; UI не показывает для них оставшийся бюджет. Добавлен regression test A → B → continue A. Последняя job использует сохранённый original call count, без сброса.

Повторный независимый review подтвердил устранение первых двух High; третья исправлена с отдельной regression-проверкой.

**Остаётся оператору:** отозвать/заменить исторический mail credential, если он ещё действителен. Новая ветка не очищает историю основного репозитория и не отзывает credential. Docker labels — доверенная локальная provenance, не криптографическая аттестация.

## Ошибки, найденные интеграционными проверками

- Test runner приведён к реальному Java 17; вместо legacy Spring context tests без DB запускается controller/security suite и все изменённые Java tests.
- Для Git file manifest введён отдельный bounded limit 512 KiB: реальный manifest aurelSpa больше обычного tool-output cap 24 KiB.
- Source lookup восстановлен и закреплён за source revision; старый неизвестный image не выдаётся за подтверждённый исходник.
- Контекст Fix Agent упакован в компактные incident/diagnosis/source snippets; первая неудачная job сохранена с причиной context-budget stop, а не замаскирована как успешное исправление.
- Host-процессы запускаются в отдельной process session; checkpoint/journal сохраняются при reconnect/restart.
- Idle multiline buffers финализируются из SQLite даже после удаления исходного контейнера.
- Launcher сохраняет уже одобренный image из journal вместо скрытого возврата на исходный demo image.

Реальные результаты pipeline, Fix Agent и rollback приведены в `summary.md`. Mocked provider/deployment tests не считаются доказательством live интеграции.
