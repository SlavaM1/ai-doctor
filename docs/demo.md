# Реальная демонстрация

Сначала запустите `bash scripts/start.sh` из `ai-doctor`. Откройте http://127.0.0.1:3050. Для реальной диагностики нужен действующий `DEEPSEEK_API_KEY`.

## A. Docker ERROR → incident → DeepSeek

В отдельном integration worktree:

```bash
cd ../aurelSpa-day35
python3 scripts/smoke_doctor_demo.py --trigger
```

До исправления тестовый запрос возвращает 500 и Java пишет настоящий multiline ERROR в Docker logs. Endpoint существует только в `doctor-demo`, требует локальный секретный header; скрипт читает его из игнорируемой `.env`, не печатает значение. Без токена ответ 403.

В Dashboard появляется incident `NullPointerException`, service `spring`, файл `DemoNameNormalizer.java`, строка 10. Дождитесь реального диагноза Flash. Проверьте наблюдения, гипотезы, source context, calls и actual token usage. Если API недоступен, UI должен показать failure, а не выдуманный результат.

## B. Дедупликация

Повторите trigger несколько раз. У одного fingerprint растут occurrences; число diagnosis calls остаётся прежним. Docker replay после перезапуска collector также не должен увеличивать occurrences старых записей.

## C. Настоящий Fix Agent

В incident нажмите **Prepare fix**, задав цель: «Исправь нормализацию отсутствующего имени, сохрани корректное поведение и добавь regression test». Pro исследует реальные Java-файлы, меняет detached worktree, запускает проверки. Результат — Git diff, список файлов и тестовый вывод, а не заранее подготовленный patch.

Проверьте diff: production checkout и запущенный image пока не изменились. Если лимит достигнут, результат остаётся частичным; автоматического бесконечного продолжения нет.

## D. Подтверждение и release

Прочитайте конкретный diff и тесты. Только затем нажмите **Approve & Deploy** и подтвердите. Runner повторно проверит hash, соберёт image и заменит разрешённый `spring`. Health, публичная страница и повтор тестового запроса должны пройти; исходная ошибка не должна повториться. Только достаточная проверка позволяет отметить incident resolved.

История Deployments показывает release/image, health и результат rollback. Если исходный trigger всё ещё даёт 500, исправление не подтверждено.

## E. Rollback

Unit/integration suite проверяет отказ новой версии и возврат предыдущего image. Результат отдельной реальной безопасной rollback-проверки, если выполнена, указан в `summary.md`. Не ломайте БД/production профиль ради демонстрации. Не удаляйте volumes; миграции не откатываются SQL-командами.

После успешного fix исходный дефект больше не воспроизводится. Для повторения A–D нужен новый изолированный demo release из начального SHA ветки, с отдельным approval; не откатывайте пользовательский checkout или БД.
