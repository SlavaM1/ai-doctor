import { number } from './components'
import type { Status } from './types'

export const exclusionStatuses = ['EXCLUDED', 'NO_ACTION']

export function ExcludedNotice({ stats, onOpen }: { stats?: Status['stats']; onOpen: () => void }) {
  if (!stats?.excluded_incidents) return null
  return <div className="model-warning" role="status">
    <p>Ошибки в исключениях: {number(stats.excluded_incidents)}. Они собираются и учитываются в счётчиках, но исключённые типы скрыты из основной очереди. За последний час: {number(stats.excluded_errors_hour)} повторений.</p>
    <button className="button" onClick={onOpen}>Показать исключённые ошибки</button>
  </div>
}

export default function ExclusionSummary({ stats }: { stats?: Status['stats'] }) {
  return <section className="telemetry" aria-label="Статистика исключений">
    <div className="metric"><span>Инцидентов в исключениях</span><strong>{number(stats?.excluded_incidents)}</strong><small>Включая «Не требует решения»</small></div>
    <div className="metric"><span>Возникновений всего</span><strong>{number(stats?.excluded_occurrences)}</strong><small>Первое событие и все повторы</small></div>
    <div className="metric"><span>За последний час</span><strong>{number(stats?.excluded_errors_hour)}</strong><small>Возникновений исключённых ошибок</small></div>
    <div className="metric"><span>За последние 24 часа</span><strong>{number(stats?.excluded_errors_day)}</strong><small>Без анализа и исправлений</small></div>
  </section>
}
