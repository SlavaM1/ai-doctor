import { useState } from 'react'
import { mutate, useApi } from './api'
import { canDeploy, testsPassed } from './deployment'
import { Badge, ConfirmDialog, DataValue, date, Empty, ErrorNotice, Icon, ModelSelect, number, statusTone, text } from './components'
import { label } from './labels'
import type { Diagnosis, FixJob, IncidentDetail, JsonObject, Model, Settings } from './types'
import { IncidentControls, ManualGit, SessionLog } from './IncidentControls'

function DiagnosisResult({ diagnosis }: { diagnosis: Diagnosis }) {
  const result = diagnosis.result || {}
  return <div className="diagnosis-result">
    <div className="section-meta"><span>{diagnosis.model || 'Модель не указана'}</span><time>{date(diagnosis.created_at)}</time></div>
    {diagnosis.error && <ErrorNotice>{diagnosis.error}</ErrorNotice>}
    {Object.keys(result).length === 0 && !diagnosis.error && <p className="muted">Результат диагностики не получен.</p>}
    {result.root_cause != null && <div className="root-cause"><span className="field-label">Первопричина</span><DataValue value={result.root_cause} /></div>}
    {result.confidence != null && <div className="confidence">Уверенность <strong>{text(result.confidence)}</strong></div>}
    {['observations', 'hypotheses', 'recommendations', 'locations'].map(key => result[key] != null && <details key={key} className="result-details" open={key === 'recommendations'}><summary>{label(key)}</summary><DataValue value={result[key]} /></details>)}
    {Object.keys(result).filter(key => !['root_cause', 'confidence', 'observations', 'hypotheses', 'recommendations', 'locations'].includes(key)).length > 0 && <details className="result-details"><summary>Дополнительные поля результата</summary><DataValue value={Object.fromEntries(Object.entries(result).filter(([key]) => !['root_cause', 'confidence', 'observations', 'hypotheses', 'recommendations', 'locations'].includes(key)))} /></details>}
  </div>
}

function ChatEntry({ entry }: { entry: unknown }) {
  if (entry && typeof entry === 'object') {
    const message = entry as JsonObject
    const content = message.content ?? message.message ?? message.response ?? message.answer
    const timestamp = message.created_at ?? message.at
    return <div className={`chat-entry ${message.role === 'user' ? 'user' : ''}`}>
      <span className="field-label">{typeof message.role === 'string' ? label(message.role) : 'Ответ сервера'}{timestamp ? ` · ${date(String(timestamp))}` : ''}</span>
      <DataValue value={content ?? entry} />
    </div>
  }
  return <div className="chat-entry"><DataValue value={entry} /></div>
}

function SourcePreview({ value }: { value: unknown }) {
  if (value && typeof value === 'object' && 'files' in value && Array.isArray(value.files)) {
    if (!value.files.length) return <p className="muted">Для зафиксированных кадров стека не найдены подходящие исходные файлы.</p>
    return <div className="source-files">{value.files.map((file: unknown, index: number) => {
      if (!file || typeof file !== 'object') return <DataValue key={index} value={file} />
      const source = file as JsonObject
      return <div key={index}><div className="source-file-heading"><code>{text(source.path) || 'Путь не указан'}</code>{source.line != null && <span>Строка {text(source.line)}</span>}</div>{typeof source.content === 'string' ? <pre className="code-block">{source.content}</pre> : <DataValue value={source} />}</div>
    })}</div>
  }
  return <DataValue value={value} />
}

function TestResults({ tests }: { tests: JsonObject }) {
  const metadata = Object.fromEntries(Object.entries(tests).filter(([key]) => key !== 'output'))
  return <div className="test-results"><DataValue value={metadata} />{tests.output != null && <details className="result-details"><summary>Вывод тестов</summary><pre className="code-block">{text(tests.output)}</pre></details>}</div>
}

function FixBudget({ job, refresh, onError }: { job: FixJob; refresh: () => void; onError: (error: string) => void }) {
  const [busy, setBusy] = useState(false)
  const canContinue = ['FAILED', 'BUDGET_EXHAUSTED', 'INTERRUPTED'].includes(job.status) && (job.remaining_calls ?? 0) > 0
  const resume = async () => {
    setBusy(true)
    try { await mutate(`/fixes/${job.id}/continue`, {}); refresh() }
    catch (error) { onError(error instanceof Error ? error.message : String(error)) }
    finally { setBusy(false) }
  }
  return <div className="test-results">
    {job.usage && <p className="small muted">Использовано вызовов: {number(job.calls_used)}; осталось: {number(job.remaining_calls)}. Токенов на входе: {number(job.usage.input_tokens)} / на выходе: {number(job.usage.output_tokens)}{job.usage.approximate ? ' (приблизительно)' : ''}. {number(job.usage.latency_ms)} мс.</p>}
    {canContinue && <button className="button" disabled={busy} onClick={resume}>{busy ? 'Отправка запроса…' : 'Продолжить в пределах лимита'}</button>}
    {canContinue && <p className="small muted">Явное продолжение сохраняет рабочую область и исходный счётчик вызовов. Оно не сбрасывает лимит и не одобряет развёртывание.</p>}
  </div>
}

function FixReview({ job, refresh, onError, stale }: { job: FixJob; refresh: () => void; onError: (error: string) => void; stale: boolean }) {
  const [approval, setApproval] = useState<{ id: number; sha: string } | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [deploying, setDeploying] = useState(false)
  const [deployError, setDeployError] = useState('')
  const [result, setResult] = useState<unknown>()
  const passed = testsPassed(job.tests || {}, job.patch_sha)
  const eligible = canDeploy(job) && !stale && result === undefined
  const unchanged = !!approval && approval.id === job.id && approval.sha === job.patch_sha && eligible
  const deploy = async () => {
    if (!approval || !acknowledged || !unchanged || deploying) return
    setDeploying(true)
    setDeployError('')
    onError('')
    try {
      const response = await mutate<unknown>(`/fixes/${approval.id}/deploy`, { confirmed: true, patch_sha: approval.sha })
      setResult(response)
      setApproval(null)
      refresh()
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      onError(message)
      setDeployError(message)
    }
    finally { setDeploying(false) }
  }
  return <div className="fix-review">
    <div className="fix-heading"><div><strong>Исправление #{job.id}</strong><span className="muted">{date(job.created_at)}</span></div><Badge tone={statusTone(job.status)}>{label(job.status)}</Badge></div>
    {job.goal && <p className="fix-goal">{job.goal}</p>}
    {job.summary && <p className="prose">{job.summary}</p>}
    {job.error && <ErrorNotice>{job.error}</ErrorNotice>}
    <FixBudget job={job} refresh={refresh} onError={onError} />
    <dl className="compact-facts"><div><dt>Модель</dt><dd>{job.model || 'Нет данных'}</dd></div>{job.base_commit && <div><dt>Базовый коммит</dt><dd className="mono wrap">{job.base_commit}</dd></div>}</dl>
    {job.changed_files?.length > 0 && <details className="result-details"><summary>Изменённые файлы ({job.changed_files.length})</summary><DataValue value={job.changed_files} /></details>}
    <div className="code-heading"><span><Icon name="source" size={15} /> Предлагаемые изменения</span><span>Секреты скрыты сервером</span></div>
    {job.patch ? <pre className="code-block diff">{job.patch.split('\n').map((line, index) => <span key={index} className={line.startsWith('+') ? 'diff-add' : line.startsWith('-') ? 'diff-remove' : line.startsWith('@@') ? 'diff-position' : ''}>{line}{'\n'}</span>)}</pre> : <div className="inline-empty">{['QUEUED', 'RUNNING'].includes(job.status) ? 'Изменения появятся, когда агент их подготовит.' : 'Изменения не получены.'}</div>}
    {job.patch_sha && <div className="patch-sha"><span>SHA изменений</span><code>{job.patch_sha}</code></div>}
    <div className="test-heading"><h4>Результаты тестов</h4><Badge tone={passed ? 'cyan' : ''}>{passed ? 'Пройдены' : Object.keys(job.tests || {}).length ? 'Не пройдены' : 'Нет данных'}</Badge></div>
    {Object.keys(job.tests || {}).length > 0 ? <TestResults tests={job.tests} /> : <p className="muted small">Развёртывание заблокировано, пока сервер не сообщит об успешном прохождении тестов.</p>}
    <details className="result-details"><summary>События агента ({job.events?.length || 0})</summary>{job.events?.length ? <ol className="agent-events">{job.events.map((event, index) => <li key={index}><DataValue value={event} /></li>)}</ol> : <p className="muted">События агента пока не получены.</p>}</details>
    <div className="deploy-bar"><div><Icon name="shield" size={17} /><span>{eligible ? 'Перед одобрением проверьте изменения и тесты.' : 'Нужны готовое исправление, хеш изменений и успешные тесты.'}</span></div><button className="button primary" disabled={!eligible || deploying} onClick={() => { setAcknowledged(false); setDeployError(''); setApproval({ id: job.id, sha: job.patch_sha }) }}><Icon name="deployments" size={16} />Одобрить и развернуть</button></div>
    {result !== undefined && <div className="action-result" role="status"><strong>Запрос на развёртывание принят</strong><DataValue value={result} /></div>}
    <ManualGit job={job} stale={stale} />
    {approval && <ConfirmDialog title="Одобрить и развернуть эти изменения?" onClose={() => { if (!deploying) setApproval(null) }}>
      <p>Проверенные изменения будут отправлены исполнителю развёртывания. Это изменит целевое приложение; сама подготовка исправления его не меняет.</p>
      <dl className="approval-facts"><div><dt>Исправление</dt><dd>#{approval.id}</dd></div><div><dt>Тесты</dt><dd>{passed ? 'Пройдены, по данным API' : 'Больше не проходят'}</dd></div><div><dt>Точный SHA изменений</dt><dd><code>{approval.sha}</code></dd></div></dl>
      {!unchanged && <ErrorNotice>Исправление изменилось или больше не готово к развёртыванию. Закройте диалог и проверьте актуальные изменения.</ErrorNotice>}
      {deployError && <ErrorNotice>{deployError}</ErrorNotice>}
      <label className="acknowledgement"><input type="checkbox" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} />Я проверил изменения, их точный хеш и успешные результаты тестов.</label>
      <div className="dialog-actions"><button className="button" onClick={() => setApproval(null)} disabled={deploying}>Отмена</button><button className="button primary" onClick={deploy} disabled={!acknowledged || !unchanged || deploying}>{deploying ? 'Запрос развёртывания…' : 'Одобрить и развернуть'}</button></div>
    </ConfirmDialog>}
  </div>
}

export default function IncidentPanel({ id, revision, refresh, models, settings }: { id: number; revision: number; refresh: () => void; models: Model[]; settings?: Settings }) {
  const detail = useApi<IncidentDetail>(`/incidents/${id}`, revision)
  const [sourceOpen, setSourceOpen] = useState(false)
  const [evidenceTab, setEvidenceTab] = useState<'source' | 'log'>('source')
  const source = useApi<unknown>(sourceOpen ? `/incidents/${id}/source` : null, revision)
  const [diagnosisModel, setDiagnosisModel] = useState('')
  const [fixModel, setFixModel] = useState('')
  const [goal, setGoal] = useState('')
  const [intent, setIntent] = useState<'explain' | 'fix'>('explain')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [feedback, setFeedback] = useState('')
  const [lastAction, setLastAction] = useState('')
  const [chatResponse, setChatResponse] = useState<unknown>()
  const [chosenFix, setChosenFix] = useState<number | null>(null)
  const fixes = [...(detail.data?.fix_jobs || [])].sort((a, b) => b.id - a.id)
  const fixId = chosenFix ?? fixes[0]?.id
  const fix = useApi<FixJob>(fixId != null ? `/fixes/${fixId}` : null, revision)
  const currentFix = fix.data || fixes.find(job => job.id === fixId)

  const action = async (kind: 'analyze' | 'explain' | 'fix') => {
    if (busy || (kind !== 'analyze' && !goal.trim())) return
    setBusy(kind); setError(''); setFeedback('')
    setLastAction(kind)
    try {
      const response = await mutate<unknown>(`/incidents/${id}/${kind === 'explain' ? 'chat' : kind}`, kind === 'analyze' ? { model: diagnosisModel } : kind === 'fix' ? { goal: goal.trim(), model: fixModel } : { message: goal.trim(), model: diagnosisModel })
      if (kind === 'explain') { setChatResponse(response); setFeedback('Ответ получен.') }
      if (kind === 'analyze') setFeedback('Анализ запрошен. Результаты появятся, когда сервер их передаст.')
      if (kind === 'fix') {
        setFeedback('Подготовка исправления запрошена. Без одобрения ничего не будет развёрнуто.')
        if (response && typeof response === 'object') {
          const data = response as JsonObject
          const job = data.fix_job && typeof data.fix_job === 'object' ? data.fix_job as JsonObject : data
          if (typeof job.id === 'number') setChosenFix(job.id)
        }
      }
      if (kind !== 'analyze') setGoal('')
      refresh()
    } catch (error) { setError(error instanceof Error ? error.message : String(error)) }
    finally { setBusy('') }
  }

  if (!detail.data) return <div className="incident-detail">{detail.error ? <ErrorNotice>{detail.error}</ErrorNotice> : <div className="loading" role="status">Загрузка инцидента #{id}…</div>}</div>
  const { incident, diagnoses = [], agent_runs = [], chat = [] } = detail.data
  const sortedDiagnoses = [...diagnoses].sort((a, b) => b.id - a.id)
  const latestDiagnosis = sortedDiagnoses[0]
  const event = incident.event || {}
  const excluded = ['EXCLUDED', 'NO_ACTION'].includes(incident.status)
  const trace = event.stack_trace ?? event.stacktrace ?? event.traceback ?? event.stack
  const timeline = [
    { at: incident.first_seen, title: 'Первое обнаружение', detail: `${incident.service}${incident.version ? ` / ${incident.version}` : ''}` },
    ...diagnoses.map(entry => ({ at: entry.created_at, title: entry.error ? 'Сбой диагностики' : 'Диагностика записана', detail: entry.model })),
    ...fixes.map(entry => ({ at: entry.created_at, title: `Создано исправление #${entry.id}`, detail: label(entry.status) })),
    ...(incident.last_seen !== incident.first_seen ? [{ at: incident.last_seen, title: 'Последнее повторение', detail: `Всего возникновений: ${number(incident.occurrences)}` }] : []),
  ].sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime())

  return <article className="incident-detail" aria-label={`Инцидент ${id}`}>
    <IncidentControls key={`${id}-${incident.status}`} id={id} status={incident.status} refresh={refresh} />
    {detail.error && <ErrorNotice>Не удалось обновить данные; показан последний загруженный инцидент. {detail.error}</ErrorNotice>}
    <header className="incident-heading"><div className="incident-kicker"><span className="mono">INC-{incident.id}</span><Badge tone={statusTone(incident.severity)}>{label(incident.severity)}</Badge><Badge tone={statusTone(incident.status)}>{label(incident.status)}</Badge></div><h2>{incident.exception_type || 'Инцидент приложения'}</h2><p>{incident.message || 'Сообщение об инциденте отсутствует.'}</p><div className="incident-meta"><span><Icon name="source" size={14} />{incident.service || 'Сервис не указан'}</span>{incident.version && <span className="mono">{incident.version}</span>}<span><Icon name="clock" size={14} />{date(incident.last_seen)}</span><span title="Первое событие и все повторы">Возникновений: {number(incident.occurrences)}</span></div></header>

    <section className="work-section evidence-section"><div className="section-heading"><h3><Icon name="source" />Данные и исходный код</h3></div>
      <div className="intent-switch evidence-tabs" role="group" aria-label="Просмотр данных"><button type="button" aria-pressed={evidenceTab === 'source'} className={evidenceTab === 'source' ? 'selected' : ''} onClick={() => setEvidenceTab('source')}>Ошибка и исходный код</button><button type="button" aria-pressed={evidenceTab === 'log'} className={evidenceTab === 'log' ? 'selected' : ''} onClick={() => setEvidenceTab('log')}>Лог сессии</button></div>
      {evidenceTab === 'log' ? <SessionLog id={id} revision={revision} /> : <>
      {trace ? <pre className="code-block stack-trace">{text(trace)}</pre> : <p className="inline-empty">В этом инциденте нет трассировки стека.</p>}
      <details className="result-details"><summary>Зафиксированное событие</summary><DataValue value={event} /></details>
      <details className="result-details" open={sourceOpen} onToggle={event => setSourceOpen(event.currentTarget.open)}><summary>Просмотр исходного кода <span className="muted">Только для чтения</span></summary>{source.error ? <ErrorNotice>{source.error}</ErrorNotice> : source.loading && !source.data ? <p className="muted">Загрузка контекста исходного кода…</p> : source.data != null ? <SourcePreview value={source.data} /> : <p className="muted">Контекст исходного кода не получен.</p>}</details>
      </>}
    </section>

    <section className="work-section"><div className="section-heading"><h3><Icon name="pulse" />Диагностика</h3><Badge tone={statusTone(incident.diagnosis_status)}>{label(incident.diagnosis_status)}</Badge></div>
      {excluded && <p className="inline-empty">Диагностика отключена. {incident.status === 'EXCLUDED' ? 'Чтобы продолжить работу, нажмите «Вернуть в инциденты».' : 'Чтобы продолжить работу, измените статус инцидента.'}</p>}
      <div className="analysis-controls"><ModelSelect value={diagnosisModel} onChange={setDiagnosisModel} models={models} configured={settings?.default_diagnosis_model} /><button className="button" disabled={!!busy || excluded} onClick={() => action('analyze')}><Icon name="pulse" size={16} />{busy === 'analyze' ? 'Отправка запроса…' : latestDiagnosis ? 'Повторить анализ' : 'Анализировать инцидент'}</button></div>
      {lastAction === 'analyze' && error && <ErrorNotice>{error}</ErrorNotice>}{lastAction === 'analyze' && feedback && <p className="feedback" role="status">{feedback}</p>}
      {latestDiagnosis ? <DiagnosisResult diagnosis={latestDiagnosis} /> : excluded ? <p className="inline-empty">Новые повторения сохраняются без анализа.</p> : incident.root_cause ? <div className="root-cause"><span className="field-label">Зафиксированная первопричина</span><p>{incident.root_cause}</p></div> : <p className="inline-empty">Диагностика ещё не выполнена. Выберите модель или используйте модель по умолчанию для анализа собранных данных.</p>}
      {sortedDiagnoses.length > 1 && <details className="result-details"><summary>Предыдущие диагностики ({sortedDiagnoses.length - 1})</summary>{sortedDiagnoses.slice(1).map(diagnosis => <DiagnosisResult key={diagnosis.id} diagnosis={diagnosis} />)}</details>}
    </section>

    {!excluded && <section className="work-section fix-section"><div className="section-heading"><h3><Icon name="chat" />Исследование и исправление</h3><span className="small muted">Нужно одобрение человека</span></div>
      <div className="chat-log">{chat.map((entry, index) => <ChatEntry key={index} entry={entry} />)}{chatResponse !== undefined && <div className="latest-response"><span className="field-label">Ответ на последнее действие</span><ChatEntry entry={chatResponse} /></div>}</div>
      <form className="composer" onSubmit={event => { event.preventDefault(); action(intent) }}><div className="intent-switch" role="group" aria-label="Тип действия"><button type="button" aria-pressed={intent === 'explain'} className={intent === 'explain' ? 'selected' : ''} onClick={() => setIntent('explain')}>Спросить / объяснить</button><button type="button" aria-pressed={intent === 'fix'} className={intent === 'fix' ? 'selected' : ''} onClick={() => setIntent('fix')}>Подготовить исправление</button></div><label className="sr-only" htmlFor={`goal-${id}`}>{intent === 'fix' ? 'Цель исправления' : 'Вопрос об инциденте'}</label><textarea id={`goal-${id}`} value={goal} onChange={event => setGoal(event.target.value)} placeholder={intent === 'fix' ? 'Опишите изменения, которые должен подготовить агент…' : 'Спросите о данных, диагностике или более безопасном подходе…'} rows={3} required maxLength={2000} /><div className="composer-footer"><ModelSelect value={intent === 'fix' ? fixModel : diagnosisModel} onChange={intent === 'fix' ? setFixModel : setDiagnosisModel} models={models} configured={intent === 'fix' ? settings?.default_fix_model : settings?.default_diagnosis_model} /><button className="button primary" type="submit" disabled={!!busy || !goal.trim()}>{busy === 'explain' || busy === 'fix' ? 'Отправка запроса…' : intent === 'fix' ? 'Подготовить исправление' : 'Отправить вопрос'}<Icon name="arrow" size={16} /></button></div></form>
      <p className="composer-note">{intent === 'fix' ? 'Создаёт изолированный вариант изменений. Перед развёртыванием проверьте изменения и тесты.' : 'Отправляет вопрос на сервер. Полученная диагностика или результат действия появятся здесь.'}</p>
      {lastAction !== 'analyze' && error && <ErrorNotice>{error}</ErrorNotice>}{lastAction !== 'analyze' && feedback && <p className="feedback" role="status">{feedback}</p>}
      {fixes.length > 1 && <label className="fix-selector">История исправлений<select value={fixId} onChange={event => setChosenFix(Number(event.target.value))}>{fixes.map(job => <option key={job.id} value={job.id}>#{job.id} — {label(job.status)} — {date(job.created_at)}</option>)}</select></label>}
      {fix.error && <ErrorNotice>{fix.error}</ErrorNotice>}
      {currentFix ? <FixReview key={currentFix.id} job={currentFix} refresh={refresh} onError={message => { setLastAction('deploy'); setError(message) }} stale={!!fix.error || !!detail.error} /> : <div className="inline-empty">Для этого инцидента ещё не подготовлено исправление.</div>}
    </section>}

    <section className="work-section timeline-section"><div className="section-heading"><h3><Icon name="clock" />Хронология инцидента</h3><span className="small muted">Только записанные события</span></div><ol className="timeline">{timeline.map((entry, index) => <li key={`${entry.title}-${entry.at}-${index}`}><span className="timeline-marker" /><div><strong>{entry.title}</strong><span>{entry.detail}</span></div><time>{date(entry.at)}</time></li>)}</ol>
      <details className="result-details"><summary>Использование моделей (запусков: {agent_runs.length})</summary>{agent_runs.length ? <div className="runs">{agent_runs.map(run => <div key={run.id}><div><strong>{label(run.kind)}</strong><span className="muted">{run.model || 'Модель не указана'}</span><time>{date(run.created_at)}</time></div><p>Токенов: {number(run.total_tokens)}{run.approximate ? ' (приблизительно)' : ''} · Вызовов: {number(run.calls)} · {number(run.latency_ms)} мс</p>{run.error && <ErrorNotice>{run.error}</ErrorNotice>}</div>)}</div> : <Empty title="Запусков моделей пока нет" />}</details>
    </section>
  </article>
}
