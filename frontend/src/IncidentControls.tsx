import { useState } from 'react'
import { mutate, useApi } from './api'
import { ConfirmDialog, DataValue, ErrorNotice, date } from './components'
import { label } from './labels'
import type { FixJob } from './types'
import { testsPassed } from './deployment'

export const incidentStatuses = ['NEW', 'OPEN', 'DIAGNOSED', 'FIXING', 'RESOLVED', 'NO_ACTION']

export function IncidentControls({ id, status, refresh }: { id: number; status: string; refresh: () => void }) {
  const [selected, setSelected] = useState(status)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const act = async (remove = false) => {
    setBusy(true); setError('')
    try {
      await mutate(remove ? `/incidents/${id}` : `/incidents/${id}/status`, remove ? { confirmed: true } : { status: selected }, remove ? 'DELETE' : 'PATCH')
      setConfirm(false); refresh()
    } catch (error) { setError(error instanceof Error ? error.message : String(error)) }
    finally { setBusy(false) }
  }
  return <div className="incident-controls">
    <label>Статус<select aria-label="Статус инцидента" value={selected} onChange={event => setSelected(event.target.value)}>{incidentStatuses.map(value => <option key={value} value={value}>{label(value)}</option>)}</select></label>
    <button className="button" disabled={busy || selected === status} onClick={() => act()}>Изменить статус</button>
    <button className="button" disabled={busy} onClick={() => setConfirm(true)}>Удалить инцидент</button>
    {status === 'NO_ACTION' && <p className="small muted">Автоматический анализ отключён. Повторения не переоткроют инцидент.</p>}
    {error && <ErrorNotice>{error}</ErrorNotice>}
    {confirm && <ConfirmDialog title={`Удалить инцидент #${id}?`} onClose={() => { if (!busy) setConfirm(false) }}>
      <p>Инцидент, диагностики, чат, логи сессии и история исправлений и развёртываний будут удалены без возможности восстановления. Файлы и ветки Git останутся на хосте. При активной работе удаление заблокировано. Новая ошибка может создать новый инцидент.</p>
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <div className="dialog-actions"><button className="button" disabled={busy} onClick={() => setConfirm(false)}>Отмена</button><button className="button primary" disabled={busy} onClick={() => act(true)}>Удалить инцидент и историю</button></div>
    </ConfirmDialog>}
  </div>
}

type LogEntry = { timestamp: string; text: string; trace_id?: string }
type SessionWindow = { trace_id: string; before: LogEntry[]; error: LogEntry; after: LogEntry[]; unavailable?: boolean; collecting?: boolean }

export function SessionLog({ id, revision }: { id: number; revision: number }) {
  const logs = useApi<SessionWindow>(`/incidents/${id}/session-log`, revision)
  if (!logs.data) return logs.error ? <ErrorNotice>{logs.error}</ErrorNotice> : <p className="muted">Загрузка лога сессии…</p>
  const data = logs.data
  return <div className="session-log">
    {logs.error && <ErrorNotice>{logs.error}</ErrorNotice>}
    <p className="small muted">{data.trace_id ? `Группа traceId: ${data.trace_id}` : 'traceId отсутствует: соседние события этого контейнера.'} Последнее повторение: до 5 событий с каждой стороны ошибки; новые события появятся автоматически. Секреты скрыты.</p>
    {data.unavailable && <p className="muted">Контекст для старого события не сохранён. Он появится при новом повторении.</p>}
    {data.before.map((entry, index) => <LogLine key={`before-${index}`} entry={entry} title="До ошибки" />)}
    <LogLine entry={data.error} title="Ошибка" error />
    {data.after.map((entry, index) => <LogLine key={`after-${index}`} entry={entry} title="После ошибки" />)}
    {data.after.length < 5 && !data.unavailable && <p className="small muted">После ошибки получено {data.after.length} из 5 событий. {data.collecting === false ? 'Период сбора завершён; показан сохранённый контекст.' : 'Ожидание следующих событий этой группы (до 3 дней).'}</p>}
  </div>
}

function LogLine({ entry, title, error }: { entry: LogEntry; title: string; error?: boolean }) {
  return <div className={`session-log-entry ${error ? 'session-log-error' : ''}`}><div className="section-meta"><strong>{title}</strong><time>{date(entry.timestamp)}</time></div><pre className="code-block">{entry.text}</pre></div>
}

const gitLabels = { branch: 'Создать ветку от текущей', commit: 'Создать коммит', push: 'Отправить в GitHub' }

export function ManualGit({ job, stale }: { job: FixJob; stale: boolean }) {
  const [branch, setBranch] = useState(`doctor/fix-${job.id}`)
  const [message, setMessage] = useState(`Исправление инцидента #${job.incident_id}`)
  const [action, setAction] = useState<keyof typeof gitLabels | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<unknown>()
  const [approval, setApproval] = useState('')
  const enabled = !stale && ['READY', 'DEPLOYED'].includes(job.status) && /^[a-f0-9]{64}$/.test(job.patch_sha || '') && testsPassed(job.tests || {}, job.patch_sha) && (job.tests?.base_commit == null || job.tests.base_commit === job.base_commit)
  const execute = async () => {
    if (!action || busy || !enabled || approval !== job.patch_sha) return
    setBusy(true); setError('')
    try {
      setResult(await mutate(`/fixes/${job.id}/git`, { confirmed: true, patch_sha: approval, action, branch, message }))
      setAction(null)
    } catch (error) { setError(error instanceof Error ? error.message : String(error)) }
    finally { setBusy(false) }
  }
  return <details className="result-details manual-git"><summary>Сохранить исправление в Git</summary>
    <p className="small muted">Три отдельных ручных действия. Ветка создаётся от текущей ветки локального проекта, без переключения checkout и без развёртывания. Push — только в origin на GitHub, без force. Агенту эти действия недоступны.</p>
    <label>Новая ветка<input value={branch} maxLength={100} onChange={event => setBranch(event.target.value)} /></label>
    <label>Сообщение коммита<input value={message} maxLength={200} onChange={event => setMessage(event.target.value)} /></label>
    <div className="incident-controls">{(Object.keys(gitLabels) as (keyof typeof gitLabels)[]).map(kind => <button key={kind} className="button" disabled={!enabled || busy || !branch.trim()} onClick={() => { setAction(kind); setApproval(job.patch_sha); setError('') }}>{gitLabels[kind]}</button>)}</div>
    {result !== undefined && <div role="status"><DataValue value={result} /></div>}
    {error && <ErrorNotice>{error}</ErrorNotice>}
    {action && <ConfirmDialog title={`${gitLabels[action]}?`} onClose={() => { if (!busy) setAction(null) }}>
      <p>Ветка: <code>{branch}</code>. {action === 'push' ? 'Подготовленный коммит будет опубликован в GitHub с локальными credentials хоста.' : action === 'commit' ? `Будет создан коммит: ${message}` : 'Новая ветка начнётся строго от текущего HEAD локального проекта.'}</p>
      <p className="small wrap">Подтверждаемый SHA исправления: <code>{approval}</code></p>
      {(!enabled || approval !== job.patch_sha) && <ErrorNotice>Исправление изменилось. Закройте диалог и проверьте новый diff.</ErrorNotice>}
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <div className="dialog-actions"><button className="button" disabled={busy} onClick={() => setAction(null)}>Отмена</button><button className="button primary" disabled={busy || !enabled || approval !== job.patch_sha} onClick={execute}>{busy ? 'Выполнение…' : gitLabels[action]}</button></div>
    </ConfirmDialog>}
  </details>
}
