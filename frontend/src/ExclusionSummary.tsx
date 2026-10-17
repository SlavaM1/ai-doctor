import { number } from './components'
import type { Status } from './types'

export const exclusionStatuses = ['EXCLUDED', 'NO_ACTION']

export default function ExclusionSummary({ stats }: { stats?: Status['stats'] }) {
  return <section className="telemetry" aria-label="Статистика исключений">
    <div className="metric"><span>Инцидентов в исключениях</span><strong>{number(stats?.excluded_incidents)}</strong><small>Включая «Не требует решения»</small></div>
    <div className="metric"><span>Возникновений всего</span><strong>{number(stats?.excluded_occurrences)}</strong><small>Первое событие и все повторы</small></div>
    <div className="metric"><span>За последний час</span><strong>{number(stats?.excluded_errors_hour)}</strong><small>Возникновений исключённых ошибок</small></div>
    <div className="metric"><span>За последние 24 часа</span><strong>{number(stats?.excluded_errors_day)}</strong><small>Без анализа и исправлений</small></div>
  </section>
}
